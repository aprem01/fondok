"""Wave 5 (Sam #5) — a deal's AI Confidence is the extraction's real score.

Every prod deal showed "0%": deal creation wrote ``ai_confidence = 0.0`` until
A11 and NO code path ever wrote the column after that. The only computation
was ``GET /deals/{id}/status``, which averaged the extraction confidence and
returned it without persisting it.

Now ``services.deal_confidence`` owns the ONE rollup — the mean of
``confidence_report.overall`` over each extracted document's latest
extraction — and writes it to ``deals.ai_confidence`` whenever an extraction
lands, is corrected, or a document is deleted (NULL when none is left).
``/status`` reads the same helper. A one-time migration nulls the legacy
0.00 values (never computed) and backfills from the extractions.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-deal-ai-confidence.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-deal-ai-confidence-storage"
)
_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)

TENANT = "00000000-0000-0000-0000-000000000001"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    async with get_session_factory()() as session:
        for tbl in ("audit_log", "extraction_results", "documents", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    yield


async def _insert_deal(session, *, ai_confidence=None) -> str:
    from sqlalchemy import text

    deal_id = str(uuid4())
    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, ai_confidence) "
            "VALUES (:id, :t, :n, :c)"
        ),
        {"id": deal_id, "t": TENANT, "n": "Conf deal", "c": ai_confidence},
    )
    return deal_id


async def _insert_doc(session, deal_id: str, *, status: str = "EXTRACTED") -> str:
    from sqlalchemy import text

    doc_id = str(uuid4())
    await session.execute(
        text(
            "INSERT INTO documents (id, deal_id, tenant_id, filename, status) "
            "VALUES (:id, :d, :t, :f, :s)"
        ),
        {"id": doc_id, "d": deal_id, "t": TENANT, "f": f"{doc_id}.pdf", "s": status},
    )
    return doc_id


async def _insert_extraction(session, deal_id: str, doc_id: str, overall, created: str) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO extraction_results "
            "(id, document_id, deal_id, tenant_id, fields, confidence_report, created_at) "
            "VALUES (:id, :doc, :d, :t, '[]', :cr, :c)"
        ),
        {
            "id": str(uuid4()),
            "doc": doc_id,
            "d": deal_id,
            "t": TENANT,
            "cr": json.dumps({"overall": overall}) if overall is not None else json.dumps({}),
            "c": created,
        },
    )


async def _stored(session, deal_id: str):
    from sqlalchemy import text

    row = (
        await session.execute(
            text("SELECT ai_confidence FROM deals WHERE id = :id"), {"id": deal_id}
        )
    ).first()
    return row._mapping["ai_confidence"]


# ─────────────────────────── the rollup ───────────────────────────


@pytest.mark.asyncio
async def test_rollup_is_the_mean_of_each_extracted_documents_latest_score() -> None:
    from app.database import get_session_factory
    from app.services.deal_confidence import compute_deal_ai_confidence

    async with get_session_factory()() as session:
        deal = await _insert_deal(session)
        a = await _insert_doc(session, deal)
        b = await _insert_doc(session, deal)
        failed = await _insert_doc(session, deal, status="FAILED")
        # Doc a was re-extracted: only its latest score counts.
        await _insert_extraction(session, deal, a, 0.40, "2026-10-01T00:00:00")
        await _insert_extraction(session, deal, a, 0.90, "2026-10-02T00:00:00")
        await _insert_extraction(session, deal, b, 0.80, "2026-10-01T00:00:00")
        # A failed document's extraction is not the deal's confidence.
        await _insert_extraction(session, deal, failed, 0.0, "2026-10-03T00:00:00")
        await session.commit()

        conf = await compute_deal_ai_confidence(session, deal_id=deal, tenant_id=TENANT)
    assert conf == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_no_extraction_is_none_never_zero() -> None:
    from app.database import get_session_factory
    from app.services.deal_confidence import (
        compute_deal_ai_confidence,
        refresh_deal_ai_confidence,
    )

    async with get_session_factory()() as session:
        deal = await _insert_deal(session, ai_confidence=0.7)
        await _insert_doc(session, deal, status="UPLOADED")
        await session.commit()
        assert await compute_deal_ai_confidence(session, deal_id=deal, tenant_id=TENANT) is None
        # Refresh writes the truth: with no extraction left the column is NULL.
        assert await refresh_deal_ai_confidence(session, deal_id=deal, tenant_id=TENANT) is None
        await session.commit()
        assert await _stored(session, deal) is None


@pytest.mark.asyncio
async def test_refresh_persists_the_rollup() -> None:
    from app.database import get_session_factory
    from app.services.deal_confidence import refresh_deal_ai_confidence

    async with get_session_factory()() as session:
        deal = await _insert_deal(session)
        doc = await _insert_doc(session, deal)
        await _insert_extraction(session, deal, doc, 0.93, "2026-10-01T00:00:00")
        await session.commit()
        assert await refresh_deal_ai_confidence(session, deal_id=deal, tenant_id=TENANT) == pytest.approx(0.93)
        await session.commit()
        assert await _stored(session, deal) == pytest.approx(0.93)


@pytest.mark.asyncio
async def test_a_non_numeric_overall_is_skipped() -> None:
    from app.database import get_session_factory
    from app.services.deal_confidence import compute_deal_ai_confidence

    async with get_session_factory()() as session:
        deal = await _insert_deal(session)
        a = await _insert_doc(session, deal)
        b = await _insert_doc(session, deal)
        await _insert_extraction(session, deal, a, None, "2026-10-01T00:00:00")
        await _insert_extraction(session, deal, b, 0.6, "2026-10-01T00:00:00")
        await session.commit()
        assert await compute_deal_ai_confidence(session, deal_id=deal, tenant_id=TENANT) == pytest.approx(0.6)


# ─────────────────────────── the migration ───────────────────────────


@pytest.mark.asyncio
async def test_migration_nulls_legacy_zero_and_backfills_from_extractions() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    async with get_session_factory()() as session:
        legacy_no_docs = await _insert_deal(session, ai_confidence=0.0)
        legacy_with_docs = await _insert_deal(session, ai_confidence=0.0)
        null_with_docs = await _insert_deal(session, ai_confidence=None)
        kept = await _insert_deal(session, ai_confidence=0.66)
        d1 = await _insert_doc(session, legacy_with_docs)
        await _insert_extraction(session, legacy_with_docs, d1, 0.4, "2026-10-01T00:00:00")
        await _insert_extraction(session, legacy_with_docs, d1, 0.9, "2026-10-02T00:00:00")
        d2 = await _insert_doc(session, null_with_docs)
        await _insert_extraction(session, null_with_docs, d2, 0.8, "2026-10-01T00:00:00")
        await session.commit()

    await run_startup_migrations()
    # Idempotent: a second boot leaves the same values.
    await run_startup_migrations()

    async with get_session_factory()() as session:
        assert await _stored(session, legacy_no_docs) is None
        assert await _stored(session, legacy_with_docs) == pytest.approx(0.9)
        assert await _stored(session, null_with_docs) == pytest.approx(0.8)
        assert await _stored(session, kept) == pytest.approx(0.66)


# ─────────────────────────── through the API ───────────────────────────


def _sample_pdf() -> bytes:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=LETTER)
    _, height = LETTER
    t = c.beginText(72, height - 72)
    t.setFont("Helvetica", 10)
    t.textLine("Net Operating Income: $1,000,000")
    c.drawText(t)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.mark.asyncio
async def test_extraction_persists_ai_confidence_on_the_deal_and_delete_clears_it() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/deals", json={"name": "ConfDeal"})
        deal_id = r.json()["id"]
        assert r.json()["ai_confidence"] is None
        r = await client.get(f"/deals/{deal_id}/status")
        assert r.json()["ai_confidence"] is None

        r = await client.post(
            f"/deals/{deal_id}/documents/upload",
            files={"files": ("conf.pdf", _sample_pdf(), "application/pdf")},
        )
        assert r.status_code == 201, r.text
        docs = (await client.get(f"/deals/{deal_id}/documents")).json()
        doc_id = docs[0]["id"]
        er = await client.post(f"/deals/{deal_id}/documents/{doc_id}/extract")
        assert er.status_code == 202

        body = {}
        for _ in range(60):
            body = (await client.get(f"/deals/{deal_id}/status")).json()
            stored = (await client.get(f"/deals/{deal_id}")).json()["ai_confidence"]
            if body["docs_extracted"] == 1 and stored is not None and stored == pytest.approx(body["ai_confidence"], abs=0.006):
                break
            await asyncio.sleep(0.1)
        assert body["docs_extracted"] == 1
        # The deal row carries the SAME rollup /status reports — persisted.
        stored = (await client.get(f"/deals/{deal_id}")).json()["ai_confidence"]
        assert stored is not None and stored > 0
        assert stored == pytest.approx(body["ai_confidence"], abs=0.006)

        r = await client.delete(f"/deals/{deal_id}/documents/{doc_id}")
        assert r.status_code in (200, 204), r.text
        assert (await client.get(f"/deals/{deal_id}")).json()["ai_confidence"] is None
        assert (await client.get(f"/deals/{deal_id}/status")).json()["ai_confidence"] is None
