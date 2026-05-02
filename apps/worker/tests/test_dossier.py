"""Tests for the DealDossier composer + /dossier endpoint contract.

The dossier is the substrate the Researcher Q&A agent runs on; if
its shape drifts, every grounded answer drifts with it.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Per-test SQLite DB before any app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-dossier.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "engine_outputs",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


_TENANT = "00000000-0000-0000-0000-000000000001"


async def _seed_full_deal(deal_id: UUID) -> tuple[UUID, UUID]:
    """Insert a deal + extracted T-12 + extracted OM with realistic fields.

    Returns (t12_doc_id, om_doc_id).
    """
    from app.database import get_session_factory

    factory = get_session_factory()
    t12_doc_id = uuid4()
    om_doc_id = uuid4()
    now = datetime.now(UTC)

    async with factory() as session:
        # Deal row
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, city, keys, brand, service,
                    deal_stage, return_profile, status, ai_confidence,
                    purchase_price, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, :city, :keys, :brand, :service,
                    'LOI', 'Core+', 'Underwriting', 0.85,
                    :pp, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": _TENANT,
                "name": "Coral Bay Resort",
                "city": "Miami Beach",
                "keys": 214,
                "brand": "Marriott",
                "service": "Full Service",
                "pp": 42_000_000.0,
                "ts": now,
            },
        )

        # T-12 doc + extraction
        t12_extraction_data = {
            "parser": "pymupdf",
            "total_pages": 1,
            "content_hash": "0" * 64,
            "parsed_at": now.isoformat(),
            "pages": [
                {
                    "page_num": 1,
                    "text": "T-12 statement summary page — Coral Bay Resort 2025-04 to 2026-03",
                    "tables": [],
                    "metadata": {},
                }
            ],
        }
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, parser, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'coral-bay-T12.pdf', 'T12',
                    'EXTRACTED', :ts, 1, 'pymupdf', :data
                )
                """
            ),
            {
                "id": str(t12_doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "ts": now,
                "data": json.dumps(t12_extraction_data),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields, :cr, 'test', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": str(t12_doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "fields": json.dumps(
                    [
                        {
                            "field_name": "p_and_l_usali.operating_revenue.rooms_revenue",
                            "value": 18_000_000.0,
                            "unit": "USD",
                            "source_page": 1,
                            "confidence": 0.95,
                            "raw_text": "Rooms Revenue: $18,000,000",
                        },
                        {
                            "field_name": "p_and_l_usali.operating_revenue.fb_revenue",
                            "value": 5_500_000.0,
                            "unit": "USD",
                            "source_page": 1,
                            "confidence": 0.93,
                            "raw_text": "F&B Revenue: $5,500,000",
                        },
                        {
                            "field_name": "p_and_l_usali.net_operating_income.noi_usd",
                            "value": 7_890_123.0,
                            "unit": "USD",
                            "source_page": 1,
                            "confidence": 0.90,
                            "raw_text": "Net Operating Income: $7,890,123",
                        },
                        {
                            "field_name": "occupancy_pct",
                            "value": 0.712,
                            "unit": "ratio",
                            "source_page": 1,
                            "confidence": 0.88,
                        },
                        {
                            "field_name": "adr_usd",
                            "value": 241.0,
                            "unit": "USD",
                            "source_page": 1,
                            "confidence": 0.92,
                        },
                    ]
                ),
                "cr": json.dumps(
                    {
                        "overall": 0.92,
                        "by_field": {},
                        "low_confidence_fields": [],
                        "requires_human_review": False,
                    }
                ),
                "ts": now,
            },
        )

        # OM doc + extraction (broker proforma) — overstated NOI.
        om_extraction_data = {
            "parser": "pymupdf",
            "total_pages": 1,
            "content_hash": "1" * 64,
            "parsed_at": now.isoformat(),
            "pages": [
                {
                    "page_num": 1,
                    "text": "Offering Memorandum — Coral Bay Resort, broker pro forma",
                    "tables": [],
                    "metadata": {},
                }
            ],
        }
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, parser, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'coral-bay-OM.pdf', 'OM',
                    'EXTRACTED', :ts, 1, 'llamaparse', :data
                )
                """
            ),
            {
                "id": str(om_doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "ts": now,
                "data": json.dumps(om_extraction_data),
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": str(om_doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "fields": json.dumps(
                    [
                        {
                            "field_name": "broker_proforma.noi_usd",
                            "value": 9_600_000.0,
                            "source_page": 1,
                            "confidence": 0.85,
                        },
                        {
                            "field_name": "broker_proforma.occupancy_pct",
                            "value": 0.78,
                            "source_page": 1,
                            "confidence": 0.85,
                        },
                        # Need broker total_revenue (or the rooms / fb /
                        # other components that sum into it) so the
                        # critic-input loader can build a USALIFinancials
                        # for the broker side; otherwise the spread is
                        # None and the variance pass has nothing to
                        # compare against.
                        {
                            "field_name": "broker_proforma.total_revenue_usd",
                            "value": 28_000_000.0,
                            "source_page": 1,
                            "confidence": 0.90,
                        },
                        {
                            "field_name": "broker_proforma.rooms_revenue_usd",
                            "value": 19_500_000.0,
                            "source_page": 1,
                            "confidence": 0.90,
                        },
                        {
                            "field_name": "broker_proforma.fb_revenue_usd",
                            "value": 6_900_000.0,
                            "source_page": 1,
                            "confidence": 0.88,
                        },
                    ]
                ),
                "ts": now,
            },
        )

        # One engine output for completeness
        await session.execute(
            text(
                """
                INSERT INTO engine_outputs (
                    id, deal_id, tenant_id, run_id, engine_name, status,
                    inputs, outputs, error, started_at, completed_at,
                    runtime_ms
                ) VALUES (
                    :id, :deal, :tenant, :run, 'returns', 'complete',
                    '{}', :outputs, NULL, :ts, :ts, 12
                )
                """
            ),
            {
                "id": str(uuid4()),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "run": str(uuid4()),
                "outputs": json.dumps(
                    {
                        "levered_irr": 0.231,
                        "equity_multiple": 2.37,
                        "year1_cash_on_cash": 0.062,
                    }
                ),
                "ts": now.isoformat(),
            },
        )

        await session.commit()
    return t12_doc_id, om_doc_id


# ─────────────────────── builder tests ───────────────────────


@pytest.mark.asyncio
async def test_build_dossier_full_deal_composes_all_layers() -> None:
    """A deal with deal row + T-12 + OM + engine + variance produces a
    dossier with every layer populated and confidence rolled up."""
    from app.database import get_session_factory
    from app.dossier import build_dossier

    deal_id = uuid4()
    t12_doc_id, om_doc_id = await _seed_full_deal(deal_id)

    factory = get_session_factory()
    async with factory() as session:
        dossier = await build_dossier(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )

    # Deal metadata
    assert dossier.deal["name"] == "Coral Bay Resort"
    assert dossier.deal["city"] == "Miami Beach"
    assert dossier.deal["keys"] == 214
    assert dossier.deal["brand"] == "Marriott"

    # Documents — both T-12 and OM
    assert len(dossier.documents) == 2
    doc_ids = {d.document_id for d in dossier.documents}
    assert str(t12_doc_id) in doc_ids
    assert str(om_doc_id) in doc_ids
    t12_doc = next(d for d in dossier.documents if d.document_id == str(t12_doc_id))
    assert t12_doc.doc_type == "T12"
    assert t12_doc.field_count == 5
    assert t12_doc.overall_confidence == pytest.approx(0.92)
    assert 1 in t12_doc.excerpts_by_page

    # Extracted fields — 5 from T-12 + 5 from OM
    assert len(dossier.extracted_fields) == 10
    noi_field = next(
        f
        for f in dossier.extracted_fields
        if "noi" in f.name and "broker" not in f.name
    )
    assert noi_field.value == pytest.approx(7_890_123.0)
    assert noi_field.confidence == pytest.approx(0.90)
    assert noi_field.citations[0].document_id == str(t12_doc_id)
    assert noi_field.citations[0].page == 1

    # Engines
    assert len(dossier.engines) == 1
    assert dossier.engines[0].name == "returns"
    assert dossier.engines[0].outputs["levered_irr"] == pytest.approx(0.231)

    # Variance — broker NOI overstated, should fire flags
    assert len(dossier.variance) > 0
    flag_fields = {v.field for v in dossier.variance}
    assert "noi" in flag_fields

    # Confidence rollup
    assert dossier.confidence.docs_extracted == 2
    assert dossier.confidence.docs_total == 2
    assert dossier.confidence.has_om is True
    assert dossier.confidence.has_t12_actuals is True
    assert dossier.confidence.extracted_field_count == 10
    assert 0.85 < dossier.confidence.avg_field_confidence < 0.95


@pytest.mark.asyncio
async def test_build_dossier_empty_deal_returns_zero_state() -> None:
    """A deal with no documents returns a valid dossier with zero
    confidence and no variance flags — never raises."""
    from app.database import get_session_factory
    from app.dossier import build_dossier

    deal_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant, 'Empty Deal', 'Draft', 0.0, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": _TENANT,
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()
        dossier = await build_dossier(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )

    assert dossier.deal["name"] == "Empty Deal"
    assert dossier.documents == []
    assert dossier.extracted_fields == []
    assert dossier.variance == []
    assert dossier.spread_actuals is None
    assert dossier.spread_broker is None
    assert dossier.confidence.docs_extracted == 0
    assert dossier.confidence.has_t12_actuals is False
    assert dossier.confidence.has_om is False


@pytest.mark.asyncio
async def test_build_dossier_skip_excerpts_flag_trims_payload() -> None:
    """``include_page_excerpts=False`` strips per-page text from each doc."""
    from app.database import get_session_factory
    from app.dossier import build_dossier

    deal_id = uuid4()
    await _seed_full_deal(deal_id)

    factory = get_session_factory()
    async with factory() as session:
        full = await build_dossier(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT,
            include_page_excerpts=True,
        )
        trimmed = await build_dossier(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT,
            include_page_excerpts=False,
        )

    assert all(
        d.excerpts_by_page for d in full.documents if d.status == "EXTRACTED"
    )
    assert all(d.excerpts_by_page == {} for d in trimmed.documents)
    # All other fields are identical.
    assert full.confidence == trimmed.confidence
    assert len(full.extracted_fields) == len(trimmed.extracted_fields)


# ─────────────────────── endpoint contract tests ───────────────────────


@pytest.mark.asyncio
async def test_dossier_endpoint_returns_typed_payload() -> None:
    """``GET /deals/{id}/dossier`` returns the typed dossier as JSON."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _seed_full_deal(deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(f"/deals/{deal_id}/dossier")
        assert r.status_code == 200, r.text
        body = r.json()

    assert body["deal_id"] == str(deal_id)
    assert body["deal"]["name"] == "Coral Bay Resort"
    assert len(body["documents"]) == 2
    assert body["confidence"]["docs_extracted"] == 2
    assert body["confidence"]["has_om"] is True


@pytest.mark.asyncio
async def test_ask_endpoint_empty_state_when_no_extractions() -> None:
    """``POST /deals/{id}/ask`` returns a structured empty-state when
    the dossier has no extracted documents — no LLM call fires."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    factory_module = __import__("app.database", fromlist=["get_session_factory"])
    factory = factory_module.get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant, 'No Docs Deal', 'Draft', 0.0, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": _TENANT,
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/ask",
            json={"question": "What is the broker NOI?"},
        )
        assert r.status_code == 200, r.text
        body = r.json()

    assert body["deal_id"] == str(deal_id)
    assert body["question"] == "What is the broker NOI?"
    assert body["answer"] == ""
    assert body["citations"] == []
    assert body["confidence"] == 0.0
    assert "no extracted documents" in (body["note"] or "")
