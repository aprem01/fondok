"""Signed document links — ``/download-url`` + ``/download/signed`` (FON-41 / R-040).

"Open document in new tab" navigates a fresh tab to the worker, and a
top-level navigation carries no ``Authorization`` header — so once the
no-JWT tenant path closed (2026-10-06) every such tab 401'd and the tester
saw a raw "document not found on deal". The fix: the web app asks the
authenticated ``GET /deals/{deal}/documents/{doc}/download-url`` for a
short-lived link and sends the *tab* there. On S3 that is a presigned GET;
on the local store it is ``/download/signed?token=…&exp=…`` where ``token``
is HMAC-SHA256 over ``deal_id|doc_id|exp``.

These tests drive the local store end to end and stub boto3 for the S3
presign branch. The last two flip ``ALLOW_TENANT_HEADER_WITHOUT_JWT`` off
(the production posture) to prove ``/download-url`` still demands a JWT
while the signed route — whose credential is the signature — does not.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import pytest

_TMP_DB = Path(tempfile.mkdtemp()) / "download_url.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

PDF_BYTES = b"%PDF-1.4\n% fondok signed-link test fixture\n%%EOF\n"
FILENAME = "Miami Beach Offering Memorandum.pdf"


# ─────────────────────────── helpers ───────────────────────────


async def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _tenant_headers() -> dict[str, str]:
    from app.config import get_settings

    return {"X-Tenant-Id": get_settings().DEFAULT_TENANT_ID}


def _flip_header_path(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Same pattern as ``test_auth_header_path_locked``: set the env var
    explicitly (a dotenv value cannot be ``delenv``'d) and clear the cache
    so the dependency re-reads it."""
    from app.config import get_settings

    monkeypatch.setenv("ALLOW_TENANT_HEADER_WITHOUT_JWT", value)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _restore_settings_cache():
    from app.config import get_settings

    yield
    get_settings.cache_clear()


async def _insert_deal(session, *, tenant_id: str, name: str) -> str:
    from sqlalchemy import text

    deal_id = str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
            VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)
            """
        ),
        {"id": deal_id, "tenant": tenant_id, "name": name, "ts": "2026-10-07 00:00:00"},
    )
    return deal_id


async def _insert_document(
    session, *, deal_id: str, tenant_id: str, filename: str, storage_key: str
) -> str:
    from sqlalchemy import text

    doc_id = str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO documents (
                id, deal_id, tenant_id, filename, doc_type, status,
                uploaded_at, content_hash, storage_key, size_bytes, page_count, parser
            ) VALUES (
                :id, :deal_id, :tenant_id, :filename, 'OM', 'EXTRACTED',
                :uploaded_at, :content_hash, :storage_key, :size_bytes, 1, 'pymupdf'
            )
            """
        ),
        {
            "id": doc_id,
            "deal_id": deal_id,
            "tenant_id": tenant_id,
            "filename": filename,
            "uploaded_at": "2026-10-07 00:00:00",
            "content_hash": uuid4().hex,
            "storage_key": storage_key,
            "size_bytes": len(PDF_BYTES),
        },
    )
    return doc_id


@pytest.fixture
async def seeded() -> dict[str, str]:
    """Two deals on the default tenant; one document (real bytes in the
    local raw store) on deal A. Returns ids as strings."""
    from app.config import get_settings
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.storage import LocalRawStore, get_raw_store

    await run_startup_migrations()
    settings = get_settings()
    tenant_id = settings.DEFAULT_TENANT_ID

    store = get_raw_store(settings)
    assert isinstance(store, LocalRawStore), "suite must run on the local raw store"

    factory = get_session_factory()
    async with factory() as session:
        deal_a = await _insert_deal(session, tenant_id=tenant_id, name="Deal A")
        deal_b = await _insert_deal(session, tenant_id=tenant_id, name="Deal B")
        storage_key = await store.put(
            tenant_id=tenant_id,
            deal_id=deal_a,
            content_hash=uuid4().hex,
            filename=FILENAME,
            bytes_=PDF_BYTES,
        )
        doc_a = await _insert_document(
            session,
            deal_id=deal_a,
            tenant_id=tenant_id,
            filename=FILENAME,
            storage_key=storage_key,
        )
        await session.commit()
    return {"tenant": tenant_id, "deal_a": deal_a, "deal_b": deal_b, "doc_a": doc_a}


async def _mint(client, seeded: dict[str, str]) -> dict:
    r = await client.get(
        f"/deals/{seeded['deal_a']}/documents/{seeded['doc_a']}/download-url",
        headers=_tenant_headers(),
    )
    assert r.status_code == 200, r.text
    return r.json()


# ─────────────────────────── /download-url ───────────────────────────


async def test_download_url_404_for_document_on_another_deal(seeded) -> None:
    """Same ``id AND deal_id AND tenant_id`` lookup as ``/download``: the doc
    exists, but not on deal B."""
    async with await _client() as c:
        r = await c.get(
            f"/deals/{seeded['deal_b']}/documents/{seeded['doc_a']}/download-url",
            headers=_tenant_headers(),
        )
    assert r.status_code == 404, r.text


async def test_download_url_404_for_unknown_document(seeded) -> None:
    async with await _client() as c:
        r = await c.get(
            f"/deals/{seeded['deal_a']}/documents/{uuid4()}/download-url",
            headers=_tenant_headers(),
        )
    assert r.status_code == 404, r.text


async def test_download_url_local_store_returns_signed_path(seeded) -> None:
    async with await _client() as c:
        body = await _mint(c, seeded)

    assert body["kind"] == "signed_path"
    assert body["expires_in"] == 300
    assert body["filename"] == FILENAME
    assert body["content_type"] == "application/pdf"

    url = body["url"]
    assert url.startswith(
        f"/deals/{seeded['deal_a']}/documents/{seeded['doc_a']}/download/signed?"
    ), url
    qs = parse_qs(urlsplit(url).query)
    assert set(qs) == {"token", "exp"}
    assert len(qs["token"][0]) == 64  # hex SHA-256
    exp = int(qs["exp"][0])
    assert time.time() + 250 < exp <= time.time() + 300 + 5


async def test_download_url_s3_store_returns_presigned_get(seeded, monkeypatch) -> None:
    """With the S3 store (what production runs) the link is a presigned GET
    carrying inline disposition + the original filename + content type."""
    from sqlalchemy import text

    from app.api import documents as documents_api
    from app.database import get_session_factory
    from app.storage import S3RawStore

    s3_key = f"s3://fondok-raw-test/fondok/raw/{seeded['tenant']}/{seeded['deal_a']}/abc-{FILENAME}"
    factory = get_session_factory()
    async with factory() as session:
        doc_s3 = await _insert_document(
            session,
            deal_id=seeded["deal_a"],
            tenant_id=seeded["tenant"],
            filename=FILENAME,
            storage_key=s3_key,
        )
        await session.commit()

    calls: list[dict] = []

    class _FakeS3Client:
        def generate_presigned_url(self, op, Params, ExpiresIn):  # noqa: N803 — boto3 signature
            calls.append({"op": op, "Params": Params, "ExpiresIn": ExpiresIn})
            return (
                f"https://fondok-raw-test.s3.amazonaws.com/{Params['Key']}"
                "?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Signature=deadbeef"
            )

    store = S3RawStore(bucket="fondok-raw-test", region="us-east-1")
    monkeypatch.setattr(store, "_client", lambda: _FakeS3Client())
    monkeypatch.setattr(documents_api, "get_raw_store", lambda settings=None: store)

    async with await _client() as c:
        r = await c.get(
            f"/deals/{seeded['deal_a']}/documents/{doc_s3}/download-url",
            headers=_tenant_headers(),
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "s3_presigned"
    assert body["expires_in"] == 300
    assert body["url"].startswith("https://fondok-raw-test.s3.amazonaws.com/")
    assert "X-Amz-Signature" in body["url"]

    assert len(calls) == 1
    call = calls[0]
    assert call["op"] == "get_object"
    assert call["ExpiresIn"] == 300
    params = call["Params"]
    assert params["Bucket"] == "fondok-raw-test"
    assert params["Key"] == f"fondok/raw/{seeded['tenant']}/{seeded['deal_a']}/abc-{FILENAME}"
    assert params["ResponseContentType"] == "application/pdf"
    assert params["ResponseContentDisposition"] == f'inline; filename="{FILENAME}"'

    # Hygiene: the stub row must not leak into the local-store tests below.
    async with factory() as session:
        await session.execute(
            text("DELETE FROM documents WHERE id = :id AND tenant_id = :tenant"),
            {"id": doc_s3, "tenant": seeded["tenant"]},
        )
        await session.commit()


# ─────────────────────────── /download/signed ───────────────────────────


async def test_signed_route_serves_bytes_with_valid_token(seeded) -> None:
    """The tab's request: no session, no tenant header — just the link."""
    async with await _client() as c:
        body = await _mint(c, seeded)
        r = await c.get(body["url"])
    assert r.status_code == 200, r.text
    assert r.content == PDF_BYTES
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.headers["content-disposition"] == f'inline; filename="{FILENAME}"'


async def test_signed_route_403_on_tampered_token(seeded) -> None:
    async with await _client() as c:
        body = await _mint(c, seeded)
        parts = urlsplit(body["url"])
        qs = parse_qs(parts.query)
        token = qs["token"][0]
        flipped = ("0" if token[-1] != "0" else "1") + token[1:]
        bad = f"{parts.path}?token={flipped}&exp={qs['exp'][0]}"
        r = await c.get(bad)
        # Changing ``exp`` without re-signing is just another tamper.
        later = f"{parts.path}?token={token}&exp={int(qs['exp'][0]) + 3600}"
        r2 = await c.get(later)
        # Garbage shapes are refused the same way, not 422'd.
        r3 = await c.get(f"{parts.path}?token=nope&exp=abc")
        r4 = await c.get(parts.path)
    assert r.status_code == 403, r.text
    assert r2.status_code == 403, r2.text
    assert r3.status_code == 403, r3.text
    assert r4.status_code == 403, r4.text


async def test_signed_route_403_on_expired_link(seeded) -> None:
    """A correctly signed link whose ``exp`` has passed is still refused."""
    from app.api.documents import _download_signing_key, _download_token

    deal = UUID(seeded["deal_a"])
    doc = UUID(seeded["doc_a"])
    exp = int(time.time()) - 1
    token = _download_token(deal, doc, exp, _download_signing_key())
    async with await _client() as c:
        r = await c.get(
            f"/deals/{deal}/documents/{doc}/download/signed?token={token}&exp={exp}"
        )
    assert r.status_code == 403, r.text
    assert "expired" in r.json()["detail"]


async def test_signed_route_token_is_bound_to_deal_and_document(seeded) -> None:
    """Re-using a valid signature under a different deal id fails the HMAC
    (the message is ``deal_id|doc_id|exp``), so a link cannot be re-pointed."""
    async with await _client() as c:
        body = await _mint(c, seeded)
        parts = urlsplit(body["url"])
        repointed = parts.path.replace(seeded["deal_a"], seeded["deal_b"]) + "?" + parts.query
        r = await c.get(repointed)
    assert r.status_code == 403, r.text


async def test_download_url_uses_explicit_secret_when_configured(seeded, monkeypatch) -> None:
    """``DOCUMENT_URL_SIGNING_SECRET`` keys the HMAC; a link minted under one
    secret is invalid under another (rotation revokes outstanding links)."""
    from app.config import get_settings

    monkeypatch.setenv("DOCUMENT_URL_SIGNING_SECRET", "test-secret-one")
    get_settings.cache_clear()
    async with await _client() as c:
        body = await _mint(c, seeded)
        ok = await c.get(body["url"])
        monkeypatch.setenv("DOCUMENT_URL_SIGNING_SECRET", "test-secret-two")
        get_settings.cache_clear()
        rotated = await c.get(body["url"])
    assert ok.status_code == 200, ok.text
    assert rotated.status_code == 403, rotated.text


async def test_production_without_secret_refuses_to_mint(seeded, monkeypatch) -> None:
    """Fail loud: a production worker on the local store with no secret
    gets a 503 from ``/download-url`` instead of a per-process key."""
    from app.config import get_settings

    monkeypatch.delenv("DOCUMENT_URL_SIGNING_SECRET", raising=False)
    monkeypatch.setenv("DEPLOYMENT_ENVIRONMENT", "production")
    get_settings.cache_clear()
    async with await _client() as c:
        r = await c.get(
            f"/deals/{seeded['deal_a']}/documents/{seeded['doc_a']}/download-url",
            headers=_tenant_headers(),
        )
    assert r.status_code == 503, r.text
    assert "DOCUMENT_URL_SIGNING_SECRET" in r.json()["detail"]


# ─────────────────────── locked auth (production posture) ───────────────────────


async def test_locked_mode_download_url_requires_a_jwt(seeded, monkeypatch) -> None:
    """With the header path closed, minting a link is a tenant-scoped read
    like any other: no JWT → 401 (even with a tenant header)."""
    _flip_header_path(monkeypatch, "0")
    async with await _client() as c:
        r = await c.get(
            f"/deals/{seeded['deal_a']}/documents/{seeded['doc_a']}/download-url",
            headers=_tenant_headers(),
        )
        bare = await c.get(
            f"/deals/{seeded['deal_a']}/documents/{seeded['doc_a']}/download-url",
        )
    assert r.status_code == 401, r.text
    assert bare.status_code == 401, bare.text


async def test_locked_mode_signed_route_serves_with_a_valid_token(seeded, monkeypatch) -> None:
    """The whole point: the new tab has no JWT and no tenant header, and the
    signed link still works because the signature is the credential."""
    async with await _client() as c:
        body = await _mint(c, seeded)  # minted while the header path is open
        _flip_header_path(monkeypatch, "0")
        r = await c.get(body["url"])
        # …and the OLD route is still locked for that same tab.
        old = await c.get(
            f"/deals/{seeded['deal_a']}/documents/{seeded['doc_a']}/download"
        )
    assert r.status_code == 200, r.text
    assert r.content == PDF_BYTES
    assert old.status_code == 401, old.text
