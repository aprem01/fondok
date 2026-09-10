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
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

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

    # Sam QA #11: when the T-12 carries Resort Fees as a distinct line,
    # they get routed to ``starting_resort_fees`` and DROPPED from the
    # other-revenue pool. The remaining other_revenue_pct_of_rooms only
    # captures genuine "other" (1,200,000 / 18,000,000 = 6.67%).
    assert base["starting_resort_fees"] == pytest.approx(800_000.0)
    expected_other_pct = 1_200_000.0 / 18_000_000.0
    assert base["other_revenue_pct_of_rooms"] == pytest.approx(expected_other_pct, rel=1e-3)

    # T-12 expense actuals are stashed for the expense engine to consume.
    actuals = base["t12_expense_actuals"]
    assert actuals["insurance"] == pytest.approx(1_160_000.0)
    assert actuals["property_taxes"] == pytest.approx(850_000.0)
    assert actuals["utilities"] == pytest.approx(290_000.0)
    assert actuals["sales_marketing"] == pytest.approx(800_000.0)

    # Phase 2.1 — every one of those expense lines names the row it came
    # off, keyed by the same canonical name the override panel edits.
    src = base["__source_fields__"]
    assert src["insurance"]["field_name"] == "p_and_l_usali.fixed_charges.insurance"
    assert src["utilities"]["field_name"] == "p_and_l_usali.undistributed.utilities"
    assert src["insurance"]["doc_type"] == "T12"
    assert src["insurance"]["basis"] == "actual"
    # …and so do the revenue-derived anchors.
    assert src["starting_resort_fees"]["field_name"] == (
        "p_and_l_usali.operating_revenue.resort_fees"
    )
    assert src["fb_revenue_per_occupied_room"]["concept"] == "fb_revenue"


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
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

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
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

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
async def test_revenue_actuals_median_corroborates_across_full_year_docs() -> None:
    """Corroboration fix (real deal 7a9928e0): when 2+ FULL-YEAR statements
    supply the same revenue line but one is mis-extracted, the grounded
    value must be the MEDIAN of the full-year values — not the first-ranked
    (blindly frozen) outlier.

    Scenario: an annual T-12 read F&B as $96,528 while a second annual and a
    TTM read the correct ~$2.7-2.9M. First-wins had frozen the $96,528.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_revenue_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Corroboration Deal", keys=300, purchase=90_000_000)

    # Doc A — annual, F&B mis-extracted as $96,528 (the outlier).
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_500_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 96_528.0},
        ],
    )
    # Doc B — annual, correct F&B $2,737,410.
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_500_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_737_410.0},
        ],
    )
    # Doc C — TTM, correct F&B $2,914,380.
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "ttm"},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_500_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_914_380.0},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        actuals = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )

    # Median of [96_528, 2_737_410, 2_914_380] == 2_737_410 — the mis-read
    # outlier can no longer freeze Year-1 F&B.
    assert actuals["fb_revenue"] == pytest.approx(2_737_410.0)
    assert actuals["fb_revenue"] != pytest.approx(96_528.0)
    assert 2_700_000.0 <= actuals["fb_revenue"] <= 2_950_000.0
    # Rooms corroborates identically across all three → unchanged at $9.5M.
    assert actuals["rooms_revenue"] == pytest.approx(9_500_000.0)


@pytest.mark.asyncio
async def test_revenue_actuals_single_full_year_doc_unchanged() -> None:
    """Single-source guard: with only ONE full-year doc supplying F&B, the
    loader keeps prior first-wins behavior (no median) — so single-source
    deals can never regress, even when the lone value is unusual.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_revenue_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Single Full-Year Deal", keys=200, purchase=40_000_000)

    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_500_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 96_528.0},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        actuals = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )

    # Only one full-year source — first-wins, value passed through as-is.
    assert actuals["fb_revenue"] == pytest.approx(96_528.0)
    assert actuals["rooms_revenue"] == pytest.approx(9_500_000.0)


@pytest.mark.asyncio
async def test_expense_actuals_median_corroboration_respects_zero_guard() -> None:
    """Expense corroboration mirrors revenue AND respects the ≤0 drop guard:
    a zero-valued line never enters the candidate pool, so the median is
    taken over real positive full-year values only. A single-source line
    still passes through first-wins.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_expense_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Expense Corroboration Deal", keys=250, purchase=60_000_000)

    # Doc A — annual, insurance MISSING (extractor emitted 0.0) + utilities.
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 0.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 290_000.0},
        ],
    )
    # Doc B — annual, insurance $1,160,000.
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_160_000.0},
        ],
    )
    # Doc C — TTM, insurance $1,200,000.
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "ttm"},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_200_000.0},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        actuals = await _load_t12_expense_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )

    # The 0.0 was dropped; median of the two real full-year values used.
    assert actuals["insurance"] == pytest.approx(1_180_000.0)
    assert actuals["insurance"] != pytest.approx(0.0)
    # Utilities appears on a single doc — first-wins, unchanged.
    assert actuals["utilities"] == pytest.approx(290_000.0)


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
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

    # Coerced down to a 0..1 ratio and clamped under 0.99.
    assert 0.0 < base["starting_occupancy"] < 1.0
    assert base["starting_occupancy"] == pytest.approx(0.715, abs=0.001)


# ═══════════ Phase 2.1 — with_provenance: which ROW supplied the value ═════


_REAL_T12 = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "real_payloads"
    / "anglers_t12_real.json"
)


async def _insert_real_anglers_t12(deal_id: UUID) -> tuple[UUID, UUID]:
    """Insert the REAL Anglers T-12 extraction; return ``(doc_id, er_id)``.

    Same shape as ``_insert_t12_extraction`` but hands back the identity so
    the provenance assertions can pin ``document_id`` /
    ``extraction_result_id`` exactly, not just "some uuid".
    """
    from app.database import get_session_factory

    payload = json.loads(_REAL_T12.read_text(encoding="utf-8"))
    fields = payload["fields"]
    factory = get_session_factory()
    doc_id = uuid4()
    er_id = uuid4()
    ts = datetime.now(UTC)
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count
                ) VALUES (
                    :id, :deal, :tenant, 'anglers_t12.xlsx', 'T12',
                    'EXTRACTED', :ts, :pages
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "ts": ts,
                "pages": payload.get("page_count") or 1,
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
                "id": str(er_id),
                "doc": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "fields": json.dumps(fields),
                "ts": ts,
            },
        )
        await session.commit()
    return doc_id, er_id


@pytest.mark.asyncio
async def test_with_provenance_names_the_exact_row_on_the_real_t12() -> None:
    """``with_provenance=True`` returns the EXACT extraction row behind each
    canonical line of the real Anglers T-12 — path, page and row identity.

    Occupancy / ADR / RevPAR all live on page 4 of the workbook under
    ``ttm_summary_per_om.*``; the loader has always used them, it just threw
    away which row they came from. Pinning the page here is what makes
    "click the number → jump to the source" a contract rather than a hope.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_revenue_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler's Hotel", keys=132, purchase=36_400_000)
    doc_id, er_id = await _insert_real_anglers_t12(deal_id)

    factory = get_session_factory()
    async with factory() as session:
        actuals, provenance = await _load_t12_revenue_actuals(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT,
            with_provenance=True,
        )

    expected = {
        "occupancy": ("ttm_summary_per_om.occupancy_pct", 4),
        "adr": ("ttm_summary_per_om.adr_usd", 4),
        "revpar": ("ttm_summary_per_om.revpar_usd", 4),
        # The gated alias set resolves rooms revenue off the January 2025
        # monthly block (page 6) — pinned as-is; Phase 2.1 does not widen
        # the vocabulary, it only records what the loader already chose.
        "rooms_revenue": ("p_and_l_usali.monthly.jan_2025.rooms_revenue_usd", 6),
    }
    assert set(provenance) == set(actuals)
    for canonical, (field_name, page) in expected.items():
        sf = provenance[canonical]
        assert sf.resolution.field_name == field_name, canonical
        assert sf.resolution.source_page == page, canonical
        assert sf.resolution.value == pytest.approx(actuals[canonical])
        assert sf.document_id == str(doc_id)
        assert sf.extraction_result_id == str(er_id)
        assert sf.resolution.doc_type == "T12"
        # No ``report_as_of`` on this document → unknown, never invented.
        assert sf.as_of is None


@pytest.mark.asyncio
async def test_with_provenance_classifies_scope_and_basis() -> None:
    """The registry classifies the row the loader picked: a monthly slice is
    reported as ``monthly``, and a T-12 line as ``actual`` basis."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_revenue_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler's Hotel", keys=132, purchase=36_400_000)
    await _insert_real_anglers_t12(deal_id)

    factory = get_session_factory()
    async with factory() as session:
        _actuals, provenance = await _load_t12_revenue_actuals(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT,
            with_provenance=True,
        )

    assert provenance["rooms_revenue"].resolution.scope == "monthly"
    assert provenance["occupancy"].resolution.concept == "occupancy"
    assert {p.resolution.basis for p in provenance.values()} == {"actual"}


@pytest.mark.asyncio
async def test_with_provenance_default_off_is_byte_identical() -> None:
    """The plain call returns the same plain dict it always did — same keys,
    same values, no tuple. Every existing caller is untouched."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _load_t12_expense_actuals,
        _load_t12_revenue_actuals,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler's Hotel", keys=132, purchase=36_400_000)
    await _insert_real_anglers_t12(deal_id)

    factory = get_session_factory()
    async with factory() as session:
        plain_rev = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )
        with_rev, _ = await _load_t12_revenue_actuals(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT,
            with_provenance=True,
        )
        plain_exp = await _load_t12_expense_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )
        with_exp, _ = await _load_t12_expense_actuals(
            session,
            deal_id=str(deal_id),
            tenant_id=_TENANT,
            with_provenance=True,
        )

    assert isinstance(plain_rev, dict) and isinstance(plain_exp, dict)
    assert plain_rev == with_rev
    assert plain_exp == with_exp


@pytest.mark.asyncio
async def test_load_engine_inputs_exposes_source_fields_and_reasons() -> None:
    """``__source_fields__`` / ``__reasons__`` land on ``base`` in the shape
    the endpoint serialises, and never leak into the engine-visible keys."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler's Hotel", keys=132, purchase=36_400_000)
    doc_id, er_id = await _insert_real_anglers_t12(deal_id)

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

    src = base["__source_fields__"]
    assert set(src["starting_occupancy"]) == {
        "document_id",
        "extraction_result_id",
        "field_name",
        "source_page",
        "concept",
        "scope",
        "basis",
        "doc_type",
        "as_of",
    }
    assert src["starting_occupancy"]["field_name"] == (
        "ttm_summary_per_om.occupancy_pct"
    )
    assert src["starting_occupancy"]["source_page"] == 4
    assert src["starting_occupancy"]["document_id"] == str(doc_id)
    assert src["starting_occupancy"]["extraction_result_id"] == str(er_id)
    assert src["starting_adr"]["field_name"] == "ttm_summary_per_om.adr_usd"

    # A deals-row value is NOT document-sourced — no source field for it.
    assert "purchase_price" not in src
    assert "keys" not in src

    # Seeds carry a machine-readable reason. This deal has a T-12 but no OM
    # and no CBRE report.
    reasons = base["__reasons__"]
    assert reasons["exit_cap_rate"]["code"] is ReasonCode.NO_DOCUMENT
    assert reasons["adr_growth"]["code"] is ReasonCode.NO_DOCUMENT
    # The T-12 IS on the deal — the F&B anchor just did not resolve.
    assert reasons["fb_revenue_per_occupied_room"]["code"] is ReasonCode.NO_SOURCE
    # A key the T-12 grounded has no reason at all.
    assert "starting_occupancy" not in reasons


# ═══════════════════ Phase 2.1 — the run-lineage hook ══════════════════


@pytest.mark.asyncio
async def test_run_all_engines_calls_the_lineage_hook() -> None:
    """``run_all_engines`` persists the run's lineage at the end of the
    chain, with ``(session, deal_id, tenant_id, run_id)``."""
    from app.database import get_session_factory
    from app.services import engine_runner

    calls: list[tuple] = []

    async def _recorder(session, deal_id, tenant_id, run_id):
        calls.append((deal_id, tenant_id, run_id))

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    run_id = str(uuid4())
    original = engine_runner.persist_for_run
    engine_runner.persist_for_run = _recorder
    try:
        factory = get_session_factory()
        async with factory() as session:
            await engine_runner.run_all_engines(
                session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id
            )
    finally:
        engine_runner.persist_for_run = original

    assert calls == [(deal_id, tenant_id, run_id)]


@pytest.mark.asyncio
async def test_lineage_hook_failure_never_fails_a_run() -> None:
    """A lineage failure is logged and swallowed — a model run must never
    fail because a side-car write did."""
    from app.database import get_session_factory
    from app.services import engine_runner

    async def _boom(session, deal_id, tenant_id, run_id):
        raise RuntimeError("lineage table not migrated yet")

    deal_id = "kimpton-angler-2026"
    tenant_id = str(uuid4())
    original = engine_runner.persist_for_run
    engine_runner.persist_for_run = _boom
    try:
        factory = get_session_factory()
        async with factory() as session:
            results = await engine_runner.run_all_engines(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                run_id=str(uuid4()),
            )
    finally:
        engine_runner.persist_for_run = original

    assert results["revenue"]["status"] == "complete"
