"""DB-backed CRUD tests for /deals.

Verifies that the deal endpoints persist to the live SQLite DB across
requests, write audit_log rows on every mutation, and roll up the
status pill from the documents table.
"""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-deals-crud.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-deals-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate state between tests so the DB is deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001 — table may not exist yet
                pass
        await session.commit()
    yield


def _build_sample_pdf() -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    _, height = LETTER
    text = c.beginText(72, height - 72)
    text.setFont("Helvetica-Bold", 14)
    text.textLine("Status Roll-Up Sample PDF")
    text.setFont("Helvetica", 10)
    text.textLine("Net Operating Income: $1,000,000")
    c.drawText(text)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="session")
def sample_pdf_bytes() -> bytes:
    return _build_sample_pdf()


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_create_deal_persists() -> None:
    """POST /deals then GET /deals/{id} round-trips every field."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={
                "name": "Persisted Hotel",
                "city": "Miami, FL",
                "keys": 150,
                "service": "Select Service",
                "deal_stage": "Teaser",
                "return_profile": "Value Add",
                "brand": "Hilton Garden Inn",
                "positioning": "Upscale",
                "purchase_price": 25000000,
            },
        )
        assert r.status_code == 201, r.text
        created = r.json()
        deal_id = created["id"]
        UUID(deal_id)
        assert created["name"] == "Persisted Hotel"
        assert created["status"] == "Draft"
        assert created["ai_confidence"] == 0.0

        # Fresh GET — proves it actually landed in the DB.
        r = await client.get(f"/deals/{deal_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["id"] == deal_id
        assert body["name"] == "Persisted Hotel"
        assert body["city"] == "Miami, FL"
        assert body["keys"] == 150
        assert body["service"] == "Select Service"
        assert body["deal_stage"] == "Teaser"
        assert body["return_profile"] == "Value Add"
        assert body["brand"] == "Hilton Garden Inn"
        assert body["positioning"] == "Upscale"
        assert float(body["purchase_price"]) == 25000000.0

        # 404 on unknown id.
        r = await client.get("/deals/00000000-0000-0000-0000-000000000999")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_deals_returns_persisted() -> None:
    """POST 3 deals — GET /deals returns 3, newest first."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        names = ["Hotel Alpha", "Hotel Beta", "Hotel Gamma"]
        for n in names:
            r = await client.post("/deals", json={"name": n, "city": "Austin"})
            assert r.status_code == 201, r.text
            # Tiny gap so created_at orders deterministically on SQLite.
            await asyncio.sleep(0.01)

        r = await client.get("/deals")
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        listed = [d["name"] for d in body]
        # Newest first → Gamma, Beta, Alpha (assumes the autouse fixture
        # purged any leftover deals from prior tests).
        assert listed[:3] == list(reversed(names))


@pytest.mark.asyncio
async def test_patch_deal_updates_and_logs_audit() -> None:
    """PATCH mutates the row, returns the updated record, writes audit_log."""
    from sqlalchemy import text

    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "PatchTarget", "city": "NYC", "keys": 100},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        r = await client.patch(
            f"/deals/{deal_id}",
            json={
                "name": "Patched Hotel",
                "deal_stage": "LOI",
                "ai_confidence": 0.75,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Patched Hotel"
        assert body["deal_stage"] == "LOI"
        assert body["ai_confidence"] == 0.75
        # Untouched fields preserved.
        assert body["city"] == "NYC"
        assert body["keys"] == 100

        # 404 on missing.
        r = await client.patch(
            "/deals/00000000-0000-0000-0000-000000000999",
            json={"name": "ghost"},
        )
        assert r.status_code == 404

    # Audit log: should have a 'deal.created' and 'deal.updated' row.
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT action, payload FROM audit_log
                 WHERE resource_id = :rid
                 ORDER BY created_at ASC
                """
            ),
            {"rid": deal_id},
        )
        actions = [r._mapping["action"] for r in rows.fetchall()]
    assert "deal.created" in actions
    assert "deal.updated" in actions


@pytest.mark.asyncio
async def test_status_aggregates_from_documents(
    sample_pdf_bytes: bytes,
) -> None:
    """Status pill rolls from 'draft' → 'extracting' → 'ready' as docs progress."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    os.environ["EVALS_MOCK"] = "true"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deals", json={"name": "StatusDeal"})
        deal_id = r.json()["id"]

        # No docs → draft.
        r = await client.get(f"/deals/{deal_id}/status")
        assert r.status_code == 200
        body = r.json()
        assert body["last_event"] == "draft"
        assert body["docs_total"] == 0

        # Upload 2 docs.
        for fname in ("doc1.pdf", "doc2.pdf"):
            r = await client.post(
                f"/deals/{deal_id}/documents/upload",
                files={"files": (fname, sample_pdf_bytes, "application/pdf")},
            )
            assert r.status_code == 201, r.text

        # Right after upload (status=UPLOADED) → extracting bucket.
        r = await client.get(f"/deals/{deal_id}/status")
        body = r.json()
        assert body["docs_total"] == 2
        assert body["last_event"] in ("extracting",)

        # Drive both docs through extraction.
        r = await client.get(f"/deals/{deal_id}/documents")
        for d in r.json():
            er = await client.post(
                f"/deals/{deal_id}/documents/{d['id']}/extract"
            )
            assert er.status_code == 202

        # Poll until both EXTRACTED.
        for _ in range(40):
            r = await client.get(f"/deals/{deal_id}/status")
            body = r.json()
            if body["docs_extracted"] == 2:
                break
            await asyncio.sleep(0.1)

        assert body["docs_extracted"] == 2
        assert body["last_event"] == "ready"
        # Confidence rollup picks up the mocked 0.9 overall.
        assert body["ai_confidence"] is not None
        assert body["ai_confidence"] == pytest.approx(0.9, rel=0.05)


@pytest.mark.asyncio
async def test_archive_does_not_delete() -> None:
    """DELETE /deals/{id} flips status to 'Archived' but keeps the row."""
    from sqlalchemy import text

    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post("/deals", json={"name": "ToArchive"})
        deal_id = r.json()["id"]

        r = await client.delete(f"/deals/{deal_id}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "Archived"

        # Still readable via GET.
        r = await client.get(f"/deals/{deal_id}")
        assert r.status_code == 200
        assert r.json()["status"] == "Archived"

        # 404 archive on missing.
        r = await client.delete("/deals/00000000-0000-0000-0000-000000000999")
        assert r.status_code == 404

    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text("SELECT status FROM deals WHERE id = :id"),
            {"id": deal_id},
        )
        row = rows.first()
        assert row is not None, "deal row should still be in the DB"
        assert row._mapping["status"] == "Archived"

        # And an audit row exists for the archive.
        audit_rows = await session.execute(
            text(
                "SELECT action FROM audit_log WHERE resource_id = :rid "
                "ORDER BY created_at ASC"
            ),
            {"rid": deal_id},
        )
        actions = [r._mapping["action"] for r in audit_rows.fetchall()]
    assert "deal.archived" in actions
