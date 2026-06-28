"""Wizard-driven upload metadata: ``user_doc_types[]`` + ``fiscal_years[]``.

Wave 1 ROADMAP #1 (guided per-category onboarding) ships an extended
upload endpoint that lets the wizard pre-tag each file with the
analyst's intent — "Annual / T-12 for 2024", "Monthly P&L for 2025" —
so the Router agent's downstream classification can either confirm the
choice or flag a mismatch (``misclassified=True``) instead of silently
overwriting analyst intent.

This module asserts:

* Posting the two arrays alongside ``files`` writes the analyst tag onto
  ``documents.user_provided_doc_type`` and the year onto
  ``documents.fiscal_year``.
* ``doc_type`` itself defaults to the analyst tag (so downstream engines
  that filter on ``doc_type`` see the right bucket immediately —
  important because parse+extract runs as a background task and the
  list endpoint is polled before extraction settles).
* Omitting both arrays preserves the legacy ``File[]`` bulk-upload
  contract used by the Data Room drop zone.
* Pre-classified upload does not crash extraction in the
  ``EVALS_MOCK=true`` short-circuit (regression guard — the mock path
  bypasses the Router so misclassified stays False end-to-end).
"""

from __future__ import annotations

import io
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

# Per-module SQLite DB so we don't share state with test_documents.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-upload-metadata.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-upload-metadata-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


def _build_tiny_pdf(label: str) -> bytes:
    """Synthesize a one-page PDF with reportlab. Each test uses a
    distinct label so the SHA-256 dedup check doesn't collapse
    multiple test files onto one row."""
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, f"Wizard upload — {label}")
    c.drawString(72, 700, "Net Operating Income: $1,234,567")
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture
async def deal_id() -> str:
    """Insert a stub deal row directly so document FKs resolve."""
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
                "name": "Wizard Hotel",
                "ts": "2026-06-27 00:00:00",
            },
        )
        await session.commit()
    yield str(new_id)
    await dispose_engine()


@pytest.mark.asyncio
async def test_upload_persists_user_doc_types_and_fiscal_years(
    deal_id: str,
) -> None:
    """When the wizard sends ``user_doc_types[]`` + ``fiscal_years[]``
    index-aligned with ``files[]``, every row carries the analyst's
    pre-categorization. ``doc_type`` defaults to the analyst tag so the
    downstream engines see the right bucket pre-extraction."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()

    pdf_a = _build_tiny_pdf("financial-2024")
    pdf_b = _build_tiny_pdf("financial-2025")
    pdf_c = _build_tiny_pdf("om")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[
                ("files", ("annual_2024.pdf", pdf_a, "application/pdf")),
                ("files", ("monthly_may_2025.pdf", pdf_b, "application/pdf")),
                ("files", ("offering_memo.pdf", pdf_c, "application/pdf")),
            ],
            data={
                "user_doc_types": ["T12", "PNL_MONTHLY", "OM"],
                # OM has no year — wizard sends empty string to keep
                # index alignment with the files array.
                "fiscal_years": ["2024", "2025", ""],
            },
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body) == 3

    by_name = {row["filename"]: row for row in body}

    t12 = by_name["annual_2024.pdf"]
    assert t12["user_provided_doc_type"] == "T12"
    assert t12["fiscal_year"] == 2024
    assert t12["misclassified"] is False
    # User intent wins over the filename heuristic for the initial
    # doc_type assignment so the engines see the right bucket.
    assert t12["doc_type"] == "T12"

    monthly = by_name["monthly_may_2025.pdf"]
    assert monthly["user_provided_doc_type"] == "PNL_MONTHLY"
    assert monthly["fiscal_year"] == 2025
    assert monthly["doc_type"] == "PNL_MONTHLY"

    om = by_name["offering_memo.pdf"]
    assert om["user_provided_doc_type"] == "OM"
    assert om["fiscal_year"] is None
    assert om["doc_type"] == "OM"


@pytest.mark.asyncio
async def test_legacy_upload_without_wizard_metadata_still_works(
    deal_id: str,
) -> None:
    """Posting ``files`` alone (the Data Room bulk drop) must still
    succeed — ``user_provided_doc_type`` and ``fiscal_year`` come back
    as ``None`` and ``misclassified`` defaults to ``False``."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    pdf = _build_tiny_pdf("legacy-bulk-upload")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files={"files": ("legacy_drop.pdf", pdf, "application/pdf")},
        )

    assert r.status_code == 201, r.text
    rec = r.json()[0]
    assert rec["user_provided_doc_type"] is None
    assert rec["fiscal_year"] is None
    assert rec["misclassified"] is False
    # Filename heuristic still kicks in — neither "legacy" nor "drop" is
    # a recognized doc-type token so we fall through to the T12 default.
    assert rec["doc_type"] == "T12"


@pytest.mark.asyncio
async def test_implausible_fiscal_year_is_dropped(deal_id: str) -> None:
    """Out-of-range years (0, 99, 9999) collapse to ``None`` so a sloppy
    form POST can't pollute the column. Hotel acquisitions cover roughly
    1900–2100; anything outside that window is treated as missing."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    pdf = _build_tiny_pdf("implausible-year")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[("files", ("annual_xx.pdf", pdf, "application/pdf"))],
            data={
                "user_doc_types": ["T12"],
                "fiscal_years": ["99"],
            },
        )

    assert r.status_code == 201, r.text
    rec = r.json()[0]
    assert rec["user_provided_doc_type"] == "T12"
    assert rec["fiscal_year"] is None


@pytest.mark.asyncio
async def test_list_documents_surfaces_wizard_signals(deal_id: str) -> None:
    """The list endpoint must include ``user_provided_doc_type``,
    ``fiscal_year``, and ``misclassified`` on every row so the Data
    Room can render the misclassification banner without a second
    fetch."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    pdf = _build_tiny_pdf("list-roundtrip")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        up = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[("files", ("annual_2023.pdf", pdf, "application/pdf"))],
            data={
                "user_doc_types": ["T12"],
                "fiscal_years": ["2023"],
            },
        )
        assert up.status_code == 201, up.text

        ls = await client.get(f"/deals/{deal_id}/documents")
        assert ls.status_code == 200, ls.text
        rows = ls.json()
        assert any(
            r["user_provided_doc_type"] == "T12"
            and r["fiscal_year"] == 2023
            and r["misclassified"] is False
            for r in rows
        ), rows


@pytest.mark.asyncio
async def test_doc_id_is_uuid_in_response(deal_id: str) -> None:
    """Spot-check the response shape — the ``id`` field must round-trip
    through ``UUID()``. Regression guard for the SELECT column list
    extensions in ``_row_to_record`` — if a future column drops one of
    the canonical fields the parser falls over here first."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    pdf = _build_tiny_pdf("uuid-shape")

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files=[("files", ("uuid_shape.pdf", pdf, "application/pdf"))],
            data={
                "user_doc_types": ["T12"],
                "fiscal_years": ["2024"],
            },
        )

    assert r.status_code == 201, r.text
    rec = r.json()[0]
    # Raises ValueError if the id field is malformed.
    UUID(rec["id"])
