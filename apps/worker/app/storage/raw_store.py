"""Raw object store for uploaded deal documents.

The interface is intentionally small — ``put`` returns an opaque key
that any future ``get`` resolves. Layout under each backend includes
``content_hash`` so two different filenames with identical bytes
collapse onto the same object.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config import Settings

logger = logging.getLogger(__name__)


class StorageError(RuntimeError):
    """Raised on any object-store IO or config failure."""


class RawStore(ABC):
    """Common interface every backend implements."""

    @abstractmethod
    async def put(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        content_hash: str,
        filename: str,
        bytes_: bytes,
    ) -> str:
        """Persist the bytes; return the storage key (opaque URI)."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """Read back what was stored at ``key``."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Cheap presence check."""


# ──────────────────────────── local ────────────────────────────


class LocalRawStore(RawStore):
    """File-system backend. Writes under ``root``.

    Layout: ``{root}/{tenant_id}/{deal_id}/{content_hash}-{filename}``
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(
        self, tenant_id: str, deal_id: str, content_hash: str, filename: str
    ) -> Path:
        safe_name = Path(filename).name or "upload.bin"
        return self.root / tenant_id / deal_id / f"{content_hash}-{safe_name}"

    async def put(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        content_hash: str,
        filename: str,
        bytes_: bytes,
    ) -> str:
        target = self._path_for(tenant_id, deal_id, content_hash, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, bytes_)
        return target.resolve().as_uri()  # file://...

    async def get(self, key: str) -> bytes:
        path = _file_uri_to_path(key)
        if not path.exists():
            raise StorageError(f"local store: missing key {key}")
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        try:
            return _file_uri_to_path(key).exists()
        except StorageError:
            return False


def _file_uri_to_path(uri: str) -> Path:
    """Coerce a ``file://`` URI (or bare path) to a ``Path``."""
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme not in ("file", ""):
        raise StorageError(
            f"LocalRawStore cannot resolve non-file URI: {uri}"
        )
    return Path(parsed.path) if parsed.scheme == "file" else Path(uri)


# ──────────────────────────── s3 ────────────────────────────


class S3RawStore(RawStore):
    """S3 backend.

    When ``kms_key_id`` is provided we set SSE-KMS on every PUT;
    without one we use SSE-S3 (AES256). The bucket should additionally
    enforce default encryption at the policy layer — that's infra.
    """

    def __init__(
        self,
        *,
        bucket: str,
        region: str | None = None,
        kms_key_id: str | None = None,
        prefix: str = "fondok",
    ) -> None:
        self.bucket = bucket
        self.region = region
        self.kms_key_id = kms_key_id
        self.prefix = prefix.strip("/")

    def _key(
        self,
        tenant_id: str,
        deal_id: str,
        content_hash: str,
        filename: str,
    ) -> str:
        safe_name = Path(filename).name or "upload.bin"
        return (
            f"{self.prefix}/raw/{tenant_id}/{deal_id}/"
            f"{content_hash}-{safe_name}"
        )

    def _client(self) -> Any:
        try:
            import boto3  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover
            raise StorageError(
                "boto3 not installed; install fondok-worker with S3 extras"
            ) from exc
        return boto3.client("s3", region_name=self.region) if self.region else boto3.client(
            "s3"
        )

    async def put(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        content_hash: str,
        filename: str,
        bytes_: bytes,
    ) -> str:
        key = self._key(tenant_id, deal_id, content_hash, filename)
        extra: dict[str, Any] = {
            "Metadata": {
                "tenant_id": tenant_id,
                "deal_id": deal_id,
                "content_hash": content_hash,
                "filename": Path(filename).name,
            },
        }
        if self.kms_key_id:
            extra["ServerSideEncryption"] = "aws:kms"
            extra["SSEKMSKeyId"] = self.kms_key_id
        else:
            extra["ServerSideEncryption"] = "AES256"

        def _put() -> None:
            client = self._client()
            client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=bytes_,
                **extra,
            )

        await asyncio.to_thread(_put)
        return f"s3://{self.bucket}/{key}"

    async def get(self, key: str) -> bytes:
        bucket, object_key = _parse_s3_uri(key)
        if bucket != self.bucket:
            raise StorageError(
                f"refusing to read from foreign bucket: {bucket}"
            )

        def _get() -> bytes:
            client = self._client()
            response = client.get_object(Bucket=bucket, Key=object_key)
            return response["Body"].read()

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        try:
            bucket, object_key = _parse_s3_uri(key)
        except StorageError:
            return False

        def _head() -> bool:
            client = self._client()
            try:
                client.head_object(Bucket=bucket, Key=object_key)
                return True
            except Exception:  # noqa: BLE001 — boto raises ClientError on 404
                return False

        return await asyncio.to_thread(_head)


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    from urllib.parse import urlparse

    parsed = urlparse(uri)
    if parsed.scheme != "s3":
        raise StorageError(f"not an s3 URI: {uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        raise StorageError(f"malformed s3 URI: {uri}")
    return bucket, key


# ──────────────────────────── factory ────────────────────────────


_RAW_STORE: RawStore | None = None


def get_raw_store(settings: Settings | None = None) -> RawStore:
    """Process-singleton accessor.

    Tests that swap settings should also call ``reset_raw_store_cache``
    so the next call picks up the change.
    """
    global _RAW_STORE
    if _RAW_STORE is not None:
        return _RAW_STORE

    if settings is None:
        from ..config import get_settings as _get_settings

        settings = _get_settings()

    if settings.OBJECT_STORE_BACKEND == "s3":
        if not settings.S3_BUCKET:
            raise StorageError("OBJECT_STORE_BACKEND=s3 requires S3_BUCKET")
        logger.info(
            "raw store: s3 bucket=%s region=%s kms=%s prefix=%s",
            settings.S3_BUCKET,
            settings.S3_REGION,
            (settings.S3_KMS_KEY_ID[:8] + "…") if settings.S3_KMS_KEY_ID else "off",
            settings.S3_PREFIX,
        )
        _RAW_STORE = S3RawStore(
            bucket=settings.S3_BUCKET,
            region=settings.S3_REGION,
            kms_key_id=settings.S3_KMS_KEY_ID,
            prefix=settings.S3_PREFIX,
        )
    else:
        logger.info("raw store: local root=%s", settings.DOCUMENT_STORAGE_ROOT)
        _RAW_STORE = LocalRawStore(Path(settings.DOCUMENT_STORAGE_ROOT))
    return _RAW_STORE


def reset_raw_store_cache() -> None:
    """Test utility — drop the cached store so a settings change wins."""
    global _RAW_STORE
    _RAW_STORE = None


__all__ = [
    "LocalRawStore",
    "RawStore",
    "S3RawStore",
    "StorageError",
    "get_raw_store",
    "reset_raw_store_cache",
]
