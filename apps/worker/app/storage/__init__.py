"""Pluggable raw-document object store.

Two backends:
* ``LocalRawStore`` — writes under ``DOCUMENT_STORAGE_ROOT``. Default
  in dev. Containers on Railway lose this disk on restart, so we don't
  use it in prod.
* ``S3RawStore`` — uploads with optional SSE-KMS to a configured
  bucket. Required for any real LP / sponsor pilot.

Selection happens via ``get_raw_store()`` reading
``Settings.OBJECT_STORE_BACKEND``.
"""

from .raw_store import (
    LocalRawStore,
    RawStore,
    S3RawStore,
    StorageError,
    get_raw_store,
    reset_raw_store_cache,
)

__all__ = [
    "LocalRawStore",
    "RawStore",
    "S3RawStore",
    "StorageError",
    "get_raw_store",
    "reset_raw_store_cache",
]
