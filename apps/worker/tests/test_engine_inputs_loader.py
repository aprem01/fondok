"""Integration tests for ``_load_engine_inputs`` — the bridge between
extracted T-12 / OM data and the engine's assumption dict.

Pins the contract Sam QA #1, #16, and (downstream) #15 hinge on:

* T-12 expense actuals (insurance, utilities, S&M, A&G, etc.) flow
  through ``_load_t12_expense_actuals`` into ``base['t12_expense_actuals']``.
* T-12 revenue actuals (occupancy, ADR, rooms revenue, F&B revenue,
  other revenue, resort fees) flow through ``_load_t12_revenue_actuals``
  and override ``starting_occupancy`` / ``starting_adr`` / derive
  ``fb_revenue_per_occupied_room`` / ``other_revenue_pct_of_rooms``.
* Partial extraction degrades gracefully — missing keys fall back to
  the Kimpton seed, never crash, never poison the assumption dict.

These tests use a real per-test SQLite DB so they exercise the SQL
path (JOIN documents, JSON extraction). Hermetic — no Anthropic call,
no Railway dependency.
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

# Force a per-test SQLite DB BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-engine-inputs-loader.db"
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
        for tbl in ("extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


_TENANT = "00000000-0000-0000-0000-000000000001"


async def _insert_deal(deal_id: UUID, *, name: str, keys: int, purchase: float) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    purchase_price, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'Underwriting', 0.0, :keys,
                    :pp, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": _TENANT,
                "name": name,
                "keys": keys,
                "pp": purchase,
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_t12_extraction(
    deal_id: UUID, *, fields: list[dict[str, object]]
) -> None:
    """Insert an EXTRACTED T-12 document with the given extraction fields."""
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    extraction = {
        "parser": "pymupdf",
        "total_pages": 1,
        "content_hash": "0" * 64,
        "parsed_at": datetime.now(UTC).isoformat(),
        "pages": [{"page_num": 1, "text": "T-12 page", "tables": [], "metadata": {}}],
    }
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'T12.pdf', 'T12', 'EXTRACTED',
                    :ts, 1, :data
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "ts": datetime.now(UTC),
                "data": json.dumps(extraction),
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
                "doc": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "fields": json.dumps(fields),
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_load_engine_inputs_uses_t12_expense_and_revenue_actuals() -> None:
    """A deal with extracted T-12 expense + revenue lines must produce an
    assumption dict where the engine reads come from the T-12, not the
    Kimpton seed."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Coral Bay Resort", keys=214, purchase=42_000_000)

    await _insert_t12_extraction(
        deal_id,
        fields=[
            # Operational KPIs
            {"field_name": "p_and_l_usali.operational_kpis.occupancy_pct", "value": 0.823},
            {"field_name": "p_and_l_usali.operational_kpis.adr_usd", "value": 241.0},
            # Revenue dollars
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 18_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.fb_revenue", "value": 5_500_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 1_200_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.resort_fees", "value": 800_000.0},
            # Expense lines
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_160_000.0},
            {"field_name": "p_and_l_usali.fixed_charges.property_taxes", "value": 850_000.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 290_000.0},
            {"field_name": "p_and_l_usali.undistributed.sales_marketing", "value": 800_000.0},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    # Deal-level overrides come through.
    assert base["keys"] == 214
    assert base["purchase_price"] == pytest.approx(42_000_000.0)

    # T-12 revenue actuals override the Kimpton seed.
    assert base["starting_occupancy"] == pytest.approx(0.823)
    assert base["starting_adr"] == pytest.approx(241.0)

    # F&B per-occupied-room derived: fb_revenue / (occ × keys × 365).
    occupied = 0.823 * 214 * 365
    expected_fb_per_room = 5_500_000.0 / occupied
    assert base["fb_revenue_per_occupied_room"] == pytest.approx(expected_fb_per_room, rel=1e-3)

    # Other-revenue pct includes resort_fees + misc on top of other_revenue.
    expected_other_pct = (1_200_000.0 + 800_000.0) / 18_000_000.0
    assert base["other_revenue_pct_of_rooms"] == pytest.approx(expected_other_pct, rel=1e-3)

    # T-12 expense actuals are stashed for the expense engine to consume.
    actuals = base["t12_expense_actuals"]
    assert actuals["insurance"] == pytest.approx(1_160_000.0)
    assert actuals["property_taxes"] == pytest.approx(850_000.0)
    assert actuals["utilities"] == pytest.approx(290_000.0)
    assert actuals["sales_marketing"] == pytest.approx(800_000.0)


@pytest.mark.asyncio
async def test_load_engine_inputs_partial_t12_falls_back_to_kimpton() -> None:
    """When the T-12 only has occupancy + ADR (no revenue dollars, no
    expense lines), the loader still applies what it has and falls back
    to Kimpton defaults for the rest. Partial extraction must not cap
    the assumption dict at zeros — that would crash the revenue engine.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Sparse T-12 Deal", keys=180, purchase=30_000_000)

    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.71},
            {"field_name": "adr_usd", "value": 200.0},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    # Occupancy + ADR overrode the seed.
    assert base["starting_occupancy"] == pytest.approx(0.71)
    assert base["starting_adr"] == pytest.approx(200.0)

    # F&B / other ratios kept the Kimpton defaults — no T-12 dollars to
    # derive from, but the engine still needs a non-zero anchor.
    assert base["fb_revenue_per_occupied_room"] > 0
    assert base["other_revenue_pct_of_rooms"] > 0

    # Expense actuals dict is empty (engine falls back to USALI ratios).
    assert base["t12_expense_actuals"] == {}


@pytest.mark.asyncio
async def test_load_engine_inputs_no_extraction_uses_full_kimpton_seed() -> None:
    """Sanity: a deal with NO T-12 extraction returns the Kimpton seed
    unchanged (modulo the deal-row override of keys + purchase_price).
    """
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _kimpton_assumptions,
        _load_engine_inputs,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="No Docs Deal", keys=132, purchase=36_400_000)

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    seed = _kimpton_assumptions()
    # Every Kimpton-seeded value should be present unchanged.
    for key in (
        "starting_occupancy",
        "starting_adr",
        "fb_revenue_per_occupied_room",
        "other_revenue_pct_of_rooms",
        "mgmt_fee_pct",
        "ffe_reserve_pct",
        "ltv",
        "interest_rate",
    ):
        assert base[key] == seed[key], f"{key} drifted from seed"

    # No expense actuals — empty dict (not missing key).
    assert base["t12_expense_actuals"] == {}


@pytest.mark.asyncio
async def test_load_engine_inputs_normalizes_percent_occupancy() -> None:
    """Extractor sometimes emits occupancy as 71.0 (percent) and sometimes
    as 0.71 (ratio). The loader must coerce to a 0..1 ratio either way —
    a 71.0 leaking into the engine would compute ``71.0 × keys × 365`` and
    produce a million-fold over-projection.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Pct Occupancy Deal", keys=180, purchase=30_000_000)

    await _insert_t12_extraction(
        deal_id,
        fields=[{"field_name": "occupancy_pct", "value": 71.5}],  # percent form
    )

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id))

    # Coerced down to a 0..1 ratio and clamped under 0.99.
    assert 0.0 < base["starting_occupancy"] < 1.0
    assert base["starting_occupancy"] == pytest.approx(0.715, abs=0.001)
