"""Wave 1 upload-validation tests (B1 + B2).

Covers the two server-side reject paths added in
``apps/worker/app/api/documents.py``:

  * B1 — file size > 50 MB → ``error_kind="too_large"``
  * B2 — extension AND content-type both outside the hotel-doc
    allowlist → ``error_kind="unsupported_type"``

Both paths must produce a synthesized FAILED record per offending file
WITHOUT writing it to the storage backend, so a subsequent retry of the
valid files in the same batch survives.
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Per-module SQLite DB so we don't share state with the other suites.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-upload-validation.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-upload-validation-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


def _build_tiny_pdf(label: str) -> bytes:
    """One-page PDF used by the negative-control (legit file) test."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, f"Wave 1 upload validation — {label}")
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
async def deal_id() -> str:
    """Insert a stub deal so document FKs resolve."""
    from sqlalchemy import text

    from app.config import get_settings
    from app.database import dispose_engine, get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    settings = get_settings()
    factory = get_session_factory()
    new_id = uuid4()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)
                """
            ),
            {
                "id": str(new_id),
                "tenant": settings.DEFAULT_TENANT_ID,
                "name": "Validation Hotel",
                "ts": "2026-06-27 00:00:00",
            },
        )
        await session.commit()
    yield str(new_id)
    await dispose_engine()


@pytest.mark.asyncio
async def test_oversize_file_is_rejected_with_too_large(deal_id: str) -> None:
    """A 51 MB payload must come back as a FAILED row with
    ``error_kind='too_large'`` and never be inserted into the documents
    table (so the storage backend isn't dirtied)."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.api.documents import _MAX_UPLOAD_BYTES
    from app.database import get_session_factory
    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    oversize = b"A" * (_MAX_UPLOAD_BYTES + 1)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[("files", ("big.pdf", oversize, "application/pdf"))],
        )

    # All-failed batch: production returns 422 with the per-row payload
    # (so the client can render the per-file error chips) — see the
    # ``all_failed`` branch in app/api/documents.py upload_documents.
    # Mixed-outcome batches still return 201.
    assert r.status_code == 422, r.text
    rec = r.json()[0]
    assert rec["status"] == "FAILED"
    assert rec["error_kind"] == "too_large"
    assert "50 MB" in rec["error_message"]

    # Confirm nothing landed in documents — the synthesized FAILED
    # record carries a fresh UUID never persisted to the table.
    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT COUNT(*) AS n FROM documents WHERE deal_id = :d"
                ),
                {"d": deal_id},
            )
        ).first()
    assert rows is not None
    assert int(rows._mapping["n"]) == 0


@pytest.mark.asyncio
async def test_unsupported_extension_is_rejected(deal_id: str) -> None:
    """A .heic upload (or any extension outside the allowlist) must
    fail with ``error_kind='unsupported_type'`` and never be persisted."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    body = b"\x00\x00\x00\x18ftypheic" + b"junk" * 100

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[("files", ("phone_photo.heic", body, "image/heic"))],
        )

    # All-failed batch returns 422 (see test_oversize comment).
    assert r.status_code == 422, r.text
    rec = r.json()[0]
    assert rec["status"] == "FAILED"
    assert rec["error_kind"] == "unsupported_type"
    assert "PDF" in rec["error_message"]

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text("SELECT COUNT(*) AS n FROM documents WHERE deal_id = :d"),
                {"d": deal_id},
            )
        ).first()
    assert rows is not None
    assert int(rows._mapping["n"]) == 0


@pytest.mark.asyncio
async def test_pdf_with_stripped_mime_still_accepted(deal_id: str) -> None:
    """Real-world broker uploads sometimes send an
    ``application/octet-stream`` content-type even though the file is
    a legit PDF. The extension wins in that case — we don't want a
    blanket reject."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    pdf = _build_tiny_pdf("stripped-mime")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[
                ("files", ("teaser.pdf", pdf, "application/octet-stream")),
            ],
        )

    assert r.status_code == 201, r.text
    rec = r.json()[0]
    # Not a FAILED row — extension carries the day.
    assert rec["status"] != "FAILED"
    assert rec["error_kind"] != "unsupported_type"


@pytest.mark.asyncio
async def test_mixed_batch_isolates_failures(deal_id: str) -> None:
    """A batch upload with one bad file + one good file must produce
    two records — the bad one FAILED with ``unsupported_type``, the
    good one persisted at PARSING (or further along after the
    background task settles)."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    pdf = _build_tiny_pdf("mixed-batch")
    bad = b"<html>not a hotel doc</html>"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[
                ("files", ("ok.pdf", pdf, "application/pdf")),
                ("files", ("page.html", bad, "text/html")),
            ],
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body) == 2
    by_name = {row["filename"]: row for row in body}
    assert by_name["page.html"]["status"] == "FAILED"
    assert by_name["page.html"]["error_kind"] == "unsupported_type"
    assert by_name["ok.pdf"]["status"] != "FAILED"
