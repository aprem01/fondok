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
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed

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


async def _insert_deal(
    deal_id: UUID,
    *,
    name: str,
    keys: int,
    purchase: float,
    field_overrides: dict[str, object] | None = None,
) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence, keys,
                    purchase_price, field_overrides, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'In Progress', 0.0, :keys,
                    :pp, :fo, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": _TENANT,
                "name": name,
                "keys": keys,
                "pp": purchase,
                # The column is NOT NULL with a ``'{}'`` default; writing the
                # same empty object keeps the pre-existing tests on the row
                # they always inserted.
                "fo": json.dumps(field_overrides or {}),
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
        _default_assumptions,
        _load_engine_inputs,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="No Docs Deal", keys=132, purchase=36_400_000)

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

    seed = _default_assumptions()
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
async def test_expense_zero_guard_and_same_period_fill() -> None:
    """The zero-leak guard still applies (a 0.0 line never becomes an
    actual). A line the period's primary statement does not carry (here the
    dropped zero) is filled from ANOTHER statement of the SAME period — the
    next-ranked one, as it reads, never a median of them (before W5 the three
    statements' insurance lines were medianed per line, a blend no document
    carries).
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_expense_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Expense Corroboration Deal", keys=250, purchase=60_000_000)

    # Doc A — annual, insurance MISSING (extractor emitted 0.0) + utilities.
    # The most complete statement, so it ranks first for the period.
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
    # Doc C — TTM, insurance $1,200,000 (newest upload, so it ranks second).
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

    # The 0.0 is never Year-1 insurance; the next same-period statement's
    # own line is, unblended.
    assert actuals["insurance"] == pytest.approx(1_200_000.0)
    # Utilities comes off the period's primary statement.
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
    # A key the T-12 grounded carries no SEED reason. It does carry a review
    # note: this real T-12's only rooms-revenue line is a monthly slice, so
    # its revenue lines cannot foot to its stated total (W5 round 3).
    occ = reasons["starting_occupancy"]
    assert occ["code"] is ReasonCode.NEEDS_REVIEW
    assert "no full-period rooms revenue line" in occ["detail"]
    assert occ["detail"].count("does not foot") == 1


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

    deal_id = KIMPTON_FIXTURE_DEAL_ID
    tenant_id = str(uuid4())
    run_id = str(uuid4())
    original = engine_runner.persist_for_run
    engine_runner.persist_for_run = _recorder
    try:
        factory = get_session_factory()
        async with factory() as session:
            await engine_runner.run_all_engines(
                session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id,
                assumptions=kimpton_seed(),
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

    deal_id = KIMPTON_FIXTURE_DEAL_ID
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
                assumptions=kimpton_seed(),
            )
    finally:
        engine_runner.persist_for_run = original

    assert results["revenue"]["status"] == "complete"


# ── 2026-10 — the unlabelled T-12 must be the base year ──────────────────────


async def _insert_financial_extraction(
    deal_id: UUID,
    *,
    doc_type: str,
    filename: str,
    fields: list[dict[str, object]],
    fiscal_year: int | None = None,
    ts: datetime,
) -> tuple[UUID, UUID]:
    """Insert an EXTRACTED financial document of any P&L-family ``doc_type``
    with an optional analyst-pinned ``fiscal_year``; return ``(doc_id, er_id)``.
    ``ts`` is both ``uploaded_at`` and the extraction ``created_at`` so a
    test controls upload order explicitly."""
    from app.database import get_session_factory

    factory = get_session_factory()
    doc_id = uuid4()
    er_id = uuid4()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, fiscal_year
                ) VALUES (
                    :id, :deal, :tenant, :filename, :doc_type, 'EXTRACTED',
                    :ts, 1, :fiscal_year
                )
                """
            ),
            {
                "id": str(doc_id),
                "deal": str(deal_id),
                "tenant": _TENANT,
                "filename": filename,
                "doc_type": doc_type,
                "ts": ts,
                "fiscal_year": fiscal_year,
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


# Tester 1 pinned fiscal_year 2025 on the T-12 row; tester 2's row has it
# NULL (and extracted_period_year NULL). Both must ground on the T-12.
@pytest.mark.parametrize("t12_fiscal_year", [2025, None], ids=["fy2025", "fy_null"])
@pytest.mark.asyncio
async def test_unlabelled_t12_outranks_dated_annual_pnl(t12_fiscal_year: int | None) -> None:
    """Live bug (2026-10, two external testers' deals). The deal's T-12
    ("The Angler's - March 2025 Financials.xlsx", sibling-template path)
    emits NO ``period_type`` and NO ``period_ending``, so the old ranking
    put it in the unknown tier (below a monthly) with recency 0, and the
    "Angler's 2024 Full Year Detailed P&L" — whose extractor mapped one
    restaurant concession to the F&B totals — drove Year-1: $9.6M revenue
    instead of $13.8M, NOI $858K, levered IRR -100%.

    With the shared ``financial_source_sort_key`` a T-12 is full-year by
    doc_type and the current period when undated, so it ranks first and
    first-wins grounds Year-1 on it. The T-12 is uploaded FIRST (older), so
    upload recency would still favour the P&L — tier + recency must decide.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _load_t12_expense_actuals,
        _load_t12_revenue_actuals,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="The Angler's", keys=132, purchase=36_400_000)
    t0 = datetime.now(UTC) - timedelta(minutes=10)

    t12_doc, t12_er = await _insert_financial_extraction(
        deal_id,
        doc_type="T12",
        filename="The Angler's - March 2025 Financials.xlsx",
        fiscal_year=t12_fiscal_year,
        ts=t0,
        fields=[
            # No period_type, no period_ending — exactly what the live row carries.
            {
                "field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue",
                "value": 3_216_620.0,
                "confidence": 1.0,
            },
            {"field_name": "p_and_l_usali.operational_kpis.adr_usd", "value": 232.77},
            {"field_name": "p_and_l_usali.operational_kpis.occupancy_pct", "value": 0.83},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_332_100.0},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenues", "value": 13_815_324.0},
            {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 2_533_250.0},
        ],
    )
    pnl_doc, _pnl_er = await _insert_financial_extraction(
        deal_id,
        doc_type="PNL",
        filename="Angler's 2024 Full Year Detailed P&L.xlsx",
        ts=t0 + timedelta(minutes=5),  # newer upload
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
            {
                "field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue",
                "value": 96_528.1,
            },
            {"field_name": "p_and_l_usali.operational_kpis.adr_usd", "value": 236.094},
            {"field_name": "p_and_l_usali.operational_kpis.occupancy_pct", "value": 0.81},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_000_000.0},
            {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 55_358.5},
        ],
    )

    factory = get_session_factory()
    async with factory() as session:
        plain = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT
        )
        actuals, provenance = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )
        expenses, expense_prov = await _load_t12_expense_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )

    assert actuals["fb_revenue"] == pytest.approx(3_216_620.0)
    assert actuals["adr"] == pytest.approx(232.77)
    assert actuals["occupancy"] == pytest.approx(0.83)
    assert actuals["rooms_revenue"] == pytest.approx(9_332_100.0)
    # The plain call ranks identically — the winner never depends on the
    # provenance flag.
    assert plain == actuals
    # Provenance names the T-12 row, not the P&L.
    for canonical in ("fb_revenue", "adr", "occupancy", "rooms_revenue"):
        sf = provenance[canonical]
        assert sf.document_id == str(t12_doc), canonical
        assert sf.extraction_result_id == str(t12_er), canonical
        assert sf.resolution.doc_type == "T12", canonical
        assert sf.document_id != str(pnl_doc)
    # The expense twin grounds on the same document.
    assert expenses["fb_dept_expense"] == pytest.approx(2_533_250.0)
    assert expense_prov["fb_dept_expense"].document_id == str(t12_doc)



@pytest.mark.asyncio
async def test_year_one_actuals_come_from_the_primary_statement_only() -> None:
    """One statement, one basis (2026-10, two testers' deals).

    A current T-12 (no period label, no period_ending — the sibling-template
    path emits neither) sits beside labelled 2022–2024 annual P&Ls. Before
    this fix every line that two or more labelled annuals carried was the
    cross-year MEDIAN of those annuals while ADR / occupancy / F&B came from
    the T-12: other revenue hit the 30% cap on a 14% hotel and total revenue
    read $15.3M against the T-12's $13.8M. The corroboration median now only
    spans full-year statements of the SAME period as the top-ranked one, so
    every Year-1 actual here is the T-12's own line.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _load_t12_expense_actuals,
        _load_t12_revenue_actuals,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="One Basis Deal", keys=132, purchase=36_400_000)
    base = datetime(2026, 9, 20, 5, 0, tzinfo=UTC)
    # The T-12 is the OLDEST upload and carries no period fields at all.
    t12_doc, t12_er = await _insert_financial_extraction(
        deal_id,
        doc_type="T12",
        filename="The Angler_s - March 2025 Financials.xlsx",
        fiscal_year=2025,
        ts=base,
        fields=[
            {"field_name": "adr_usd", "value": 232.77},
            {"field_name": "occupancy_pct", "value": 0.83},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_332_100.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 3_216_620.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_266_603.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 18_986.9},
            {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 2_533_250.0},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_429_573.0},
        ],
    )
    annuals = {
        2024: (8_000_000.0, 96_528.1, 2_900_000.0, 55_358.5, 1_500_000.0),
        2023: (7_000_000.0, 2_111_540.0, 3_000_000.0, 2_040_345.0, 1_400_000.0),
        2022: (6_500_000.0, 2_460_440.0, 3_100_000.0, 2_446_510.0, 1_300_000.0),
    }
    for i, (year, (rooms, fb, misc, fb_exp, ins)) in enumerate(annuals.items(), start=1):
        await _insert_financial_extraction(
            deal_id,
            doc_type="PNL",
            filename=f"Angler_s {year} P&L.xlsx",
            fiscal_year=year,
            ts=base + timedelta(minutes=i),
            fields=[
                {"field_name": "p_and_l_usali.period_type", "value": "annual"},
                {"field_name": "p_and_l_usali.period_ending", "value": f"{year}-12-31"},
                {"field_name": "adr_usd", "value": 236.094},
                {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": rooms},
                {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": fb},
                {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": misc},
                {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": fb_exp},
                {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": ins},
            ],
        )

    factory = get_session_factory()
    async with factory() as session:
        rev, rev_prov = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )
        exp, exp_prov = await _load_t12_expense_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )

    # Every revenue line is the T-12's own — no six-year blend.
    assert rev["adr"] == pytest.approx(232.77)
    assert rev["occupancy"] == pytest.approx(0.83)
    assert rev["rooms_revenue"] == pytest.approx(9_332_100.0)
    assert rev["fb_revenue"] == pytest.approx(3_216_620.0)
    assert rev["misc_revenue"] == pytest.approx(1_266_603.0)
    assert rev["other_revenue"] == pytest.approx(18_986.9)
    # And so is every expense line (the old median gave 2,040,345 / 1,400,000).
    assert exp["fb_dept_expense"] == pytest.approx(2_533_250.0)
    assert exp["insurance"] == pytest.approx(1_429_573.0)
    # Provenance names the T-12 row, not a derived cross-document record.
    for canonical in ("rooms_revenue", "fb_revenue", "misc_revenue"):
        assert str(rev_prov[canonical].document_id) == str(t12_doc), canonical
    assert str(exp_prov["fb_dept_expense"].document_id) == str(t12_doc)


@pytest.mark.asyncio
async def test_monthly_sub_period_lines_never_supply_year_one_actuals() -> None:
    """The live T-12 lists its monthly block BEFORE the annual operating
    revenue lines. The last-segment alias fallback let
    ``monthly.apr_2024.rooms_revenue`` ($954K) win the ``rooms_revenue``
    canonical (first seen in the row), so other revenue / rooms came out at
    135% and hit the 30% cap. Sub-period paths are skipped outright; the
    annual line is the actual, in both loaders.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _load_t12_expense_actuals,
        _load_t12_revenue_actuals,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Monthly Guard Deal", keys=132, purchase=36_400_000)
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.monthly.apr_2024.rooms_revenue", "value": 954_187.0},
            {"field_name": "p_and_l_usali.monthly.apr_2024.total_revenues", "value": 1_319_410.0},
            {"field_name": "p_and_l_usali.monthly.apr_2024.insurance", "value": 119_000.0},
            {"field_name": "p_and_l_usali.prior_year.occupancy", "value": 0.0},
            {"field_name": "p_and_l_usali.budget.rooms_revenue", "value": 9_900_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_332_100.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_266_603.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 18_986.9},
            {"field_name": "occupancy_pct", "value": 0.83},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_429_573.0},
        ],
    )
    factory = get_session_factory()
    async with factory() as session:
        rev = await _load_t12_revenue_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
        exp = await _load_t12_expense_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
    assert rev["rooms_revenue"] == pytest.approx(9_332_100.0)
    assert rev["occupancy"] == pytest.approx(0.83)
    assert rev["misc_revenue"] == pytest.approx(1_266_603.0)
    assert exp["insurance"] == pytest.approx(1_429_573.0)
    # The ratio the revenue engine derives from these is the real ~13.8%.
    assert (rev["misc_revenue"] + rev["other_revenue"]) / rev["rooms_revenue"] == pytest.approx(0.1378, abs=0.001)


@pytest.mark.asyncio
async def test_annual_ratios_never_anchor_on_a_monthly_rooms_line() -> None:
    """When the only rooms-revenue line a statement carries is a monthly
    slice (the loader's last-resort fallback, provenance scope "monthly"),
    the other-revenue ratio stays on the seed instead of dividing a year
    of other revenue by one month of rooms (135% → capped 30%). With the
    annual line present the ratio is the real ~13.8%.
    """
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    # Case 1 — monthly rooms only → seed ratio kept.
    deal_a = uuid4()
    await _insert_deal(deal_a, name="Monthly Rooms Only", keys=132, purchase=36_400_000)
    await _insert_t12_extraction(
        deal_a,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.83},
            {"field_name": "adr_usd", "value": 232.77},
            {"field_name": "p_and_l_usali.monthly.apr_2024.rooms_revenue", "value": 954_187.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_266_603.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 18_986.9},
        ],
    )
    # Case 2 — the same statement with its annual rooms line → real ratio.
    deal_b = uuid4()
    await _insert_deal(deal_b, name="Annual Rooms Present", keys=132, purchase=36_400_000)
    await _insert_t12_extraction(
        deal_b,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.83},
            {"field_name": "adr_usd", "value": 232.77},
            {"field_name": "p_and_l_usali.monthly.apr_2024.rooms_revenue", "value": 954_187.0},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_332_100.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_266_603.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 18_986.9},
        ],
    )
    factory = get_session_factory()
    async with factory() as session:
        base_a = await _load_engine_inputs(session, str(deal_a), tenant_id=_TENANT)
        base_b = await _load_engine_inputs(session, str(deal_b), tenant_id=_TENANT)
    assert base_a["other_revenue_pct_of_rooms"] == pytest.approx(0.065)  # seed, not 0.30
    assert base_b["other_revenue_pct_of_rooms"] == pytest.approx(0.1378, abs=0.001)


# ═══════════════════ FON-85 — OM capital keys carry ``om_broker`` ═══════════════


async def _insert_om_extraction(
    deal_id: UUID, *, fields: list[dict[str, object]]
) -> tuple[UUID, UUID]:
    """Insert an EXTRACTED OM with the broker's capital rows; ``(doc_id, er_id)``."""
    return await _insert_financial_extraction(
        deal_id,
        doc_type="OM",
        filename="Offering Memorandum.pdf",
        fields=fields,
        ts=datetime.now(UTC),
    )


async def _base_for(deal_id: UUID) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    factory = get_session_factory()
    async with factory() as session:
        return await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)


_OM_RENO_ROW = {
    "field_name": "broker_proforma.renovation_budget_usd",
    "value": 8_000_000.0,
    "source_page": 31,
    "confidence": 0.9,
}


@pytest.mark.asyncio
async def test_om_renovation_budget_row_is_labelled_om_broker() -> None:
    """FON-85 (Step A). A renovation budget read off the OM's broker pro forma
    is the broker's number: ``sources`` says ``om_broker`` and
    ``source_fields`` still names the exact row. Before this the label stayed
    ``seed`` on a document-grounded value and the web had to treat the
    ``source_fields`` row as the document signal."""
    from app.services.engine_runner import SOURCE_OM_BROKER

    deal_id = uuid4()
    await _insert_deal(deal_id, name="OM PIP Deal", keys=132, purchase=36_400_000)
    await _insert_om_extraction(deal_id, fields=[_OM_RENO_ROW])

    base = await _base_for(deal_id)

    assert base["renovation_budget"] == pytest.approx(8_000_000.0)
    assert base["__sources__"]["renovation_budget"] == SOURCE_OM_BROKER == "om_broker"
    row = base["__source_fields__"]["renovation_budget"]
    assert row["field_name"] == "broker_proforma.renovation_budget_usd"
    assert row["doc_type"] == "OM"
    assert row["source_page"] == 31
    assert row["document_id"] is not None
    # A grounded key is not a seed, so it gets no "no source" reason.
    assert "renovation_budget" not in base["__reasons__"]


@pytest.mark.asyncio
async def test_om_asking_price_is_om_broker_unless_the_deals_row_carries_it() -> None:
    """The same rule for the other OM capital keys. The deals row still
    outranks the OM headline (deals table > OM actuals > seed): a deal with a
    persisted purchase price keeps ``deal_row`` and no OM row is claimed for
    it; a deal without one takes the broker's asking price as ``om_broker``."""
    from app.services.engine_runner import SOURCE_DEAL_ROW, SOURCE_OM_BROKER

    asking = {"field_name": "asking_price.headline_price_usd", "value": 40_000_000.0}

    with_price = uuid4()
    await _insert_deal(with_price, name="Priced on the row", keys=132, purchase=36_400_000)
    await _insert_om_extraction(with_price, fields=[asking, _OM_RENO_ROW])
    base = await _base_for(with_price)
    assert base["purchase_price"] == pytest.approx(36_400_000.0)
    assert base["__sources__"]["purchase_price"] == SOURCE_DEAL_ROW
    assert "purchase_price" not in base["__source_fields__"]
    # …while the renovation budget on the same OM is still the broker's.
    assert base["__sources__"]["renovation_budget"] == SOURCE_OM_BROKER

    without_price = uuid4()
    await _insert_deal(without_price, name="Unpriced", keys=132, purchase=0)
    await _insert_om_extraction(without_price, fields=[asking])
    base = await _base_for(without_price)
    assert base["purchase_price"] == pytest.approx(40_000_000.0)
    assert base["__sources__"]["purchase_price"] == SOURCE_OM_BROKER
    assert base["__source_fields__"]["purchase_price"]["field_name"] == (
        "asking_price.headline_price_usd"
    )


@pytest.mark.asyncio
async def test_no_om_row_keeps_the_renovation_budget_on_the_seed() -> None:
    """No OM → the Kimpton seed stays, labelled ``seed``, with no row and a
    ``no_document`` reason. An OM without a renovation row reads ``no_source``
    — the same ``seed`` label either way."""
    from fondok_schemas.reasons import ReasonCode

    from app.services.engine_runner import SOURCE_SEED

    deal_id = uuid4()
    await _insert_deal(deal_id, name="No OM", keys=132, purchase=36_400_000)
    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(5_280_000.0)
    assert base["__sources__"]["renovation_budget"] == SOURCE_SEED
    assert "renovation_budget" not in base["__source_fields__"]
    assert base["__reasons__"]["renovation_budget"]["code"] is ReasonCode.NO_DOCUMENT

    om_without_reno = uuid4()
    await _insert_deal(om_without_reno, name="OM, no PIP", keys=132, purchase=36_400_000)
    await _insert_om_extraction(
        om_without_reno,
        fields=[{"field_name": "property_overview.year_built", "value": 1962}],
    )
    base = await _base_for(om_without_reno)
    assert base["__sources__"]["renovation_budget"] == SOURCE_SEED
    assert "renovation_budget" not in base["__source_fields__"]
    assert base["__reasons__"]["renovation_budget"]["code"] is ReasonCode.NO_SOURCE


@pytest.mark.asyncio
async def test_analyst_override_still_wins_over_the_om_renovation_budget() -> None:
    """An analyst override that CHANGES the broker's figure wins on value and
    label, and the OM row is not claimed for a number it did not supply."""
    from app.services.engine_runner import SOURCE_ANALYST_OVERRIDE

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        name="Overridden PIP",
        keys=132,
        purchase=36_400_000,
        field_overrides={"renovation_budget": {"value": 9_500_000, "note": "GC bid"}},
    )
    await _insert_om_extraction(deal_id, fields=[_OM_RENO_ROW])

    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(9_500_000.0)
    assert base["__sources__"]["renovation_budget"] == SOURCE_ANALYST_OVERRIDE
    assert "renovation_budget" not in base["__source_fields__"]


@pytest.mark.asyncio
async def test_shadow_override_equal_to_the_om_figure_keeps_om_broker() -> None:
    """FON-65 interplay: re-saving the broker's number unchanged is not an
    override. The value is byte-identical, the row survives and the label
    reports where the number really came from."""
    from app.services.engine_runner import SOURCE_OM_BROKER

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        name="Shadow PIP",
        keys=132,
        purchase=36_400_000,
        field_overrides={"renovation_budget": {"value": 8_000_000, "note": ""}},
    )
    await _insert_om_extraction(deal_id, fields=[_OM_RENO_ROW])

    base = await _base_for(deal_id)
    assert base["renovation_budget"] == pytest.approx(8_000_000.0)
    assert base["__sources__"]["renovation_budget"] == SOURCE_OM_BROKER
    assert base["__source_fields__"]["renovation_budget"]["field_name"] == (
        "broker_proforma.renovation_budget_usd"
    )


# ═══ Sam, 2026-10-15 — an OM-sourced PIP budget never displaces Year 1 ═══


@pytest.mark.asyncio
async def test_om_sourced_pip_budget_alone_does_not_displace_year_one() -> None:
    """FON-85 Step B used to fire a 15% / 8% Year-1 haircut for an $8.0M OM
    PIP on 132 keys. Sam (2026-10-15): renovation impacts come from explicit
    timing / room-displacement / rate assumptions only, never from a budget
    amount — so the OM figure is a capital cost and Year 1 is undisplaced."""
    om_deal = uuid4()
    await _insert_deal(om_deal, name="OM PIP", keys=132, purchase=36_400_000)
    await _insert_om_extraction(om_deal, fields=[_OM_RENO_ROW])
    base = await _base_for(om_deal)
    assert base["__sources__"]["renovation_budget"] == "om_broker"
    assert base["y1_occupancy_displacement_pct"] == 0.0
    assert base["y1_adr_displacement_pct"] == 0.0


@pytest.mark.asyncio
async def test_seed_and_small_om_pips_keep_displacement_off() -> None:
    """The Kimpton $5.28M seed (no OM) and a small OM PIP stay undisplaced."""
    seed_deal = uuid4()
    await _insert_deal(seed_deal, name="Seed PIP", keys=132, purchase=36_400_000)
    base = await _base_for(seed_deal)
    assert base["renovation_budget"] == pytest.approx(5_280_000.0)
    assert base["y1_occupancy_displacement_pct"] == 0.0
    assert base["y1_adr_displacement_pct"] == 0.0

    small_pip = uuid4()
    await _insert_deal(small_pip, name="Cosmetic refresh", keys=132, purchase=36_400_000)
    await _insert_om_extraction(
        small_pip,
        fields=[{**_OM_RENO_ROW, "value": 500_000.0}],
    )
    base = await _base_for(small_pip)
    assert base["__sources__"]["renovation_budget"] == "om_broker"
    assert base["y1_occupancy_displacement_pct"] == 0.0
    assert base["y1_adr_displacement_pct"] == 0.0


# ═════════ FON-85 / R-070 — OM in-place debt keys carry ``om_broker`` too ═════════

_OM_DEBT_ROWS: list[dict[str, object]] = [
    {"field_name": "in_place_debt.loan_balance_usd", "value": 24_000_000.0, "source_page": 44},
    {"field_name": "in_place_debt.interest_rate_pct", "value": 0.0725, "source_page": 44},
    {"field_name": "in_place_debt.ltv_pct", "value": 0.62, "source_page": 44},
    {"field_name": "in_place_debt.amortization_years", "value": 25, "source_page": 44},
    {"field_name": "in_place_debt.term_years", "value": 7, "source_page": 44},
]
_OM_DEBT_KEYS = ("loan_amount", "interest_rate", "ltv", "amortization_years", "term_years")


@pytest.mark.asyncio
async def test_om_in_place_debt_rows_are_labelled_om_broker() -> None:
    """R-070: the Debt tab renders a source chip per input from the label, so
    an OM-grounded loan reading "Seed" was the mislabel testers hit. Every
    in-place-debt key the OM supplies is ``om_broker`` and names its row."""
    from app.services.engine_runner import SOURCE_OM_BROKER

    deal_id = uuid4()
    await _insert_deal(deal_id, name="OM debt", keys=132, purchase=36_400_000)
    await _insert_om_extraction(deal_id, fields=_OM_DEBT_ROWS)

    base = await _base_for(deal_id)
    assert base["loan_amount"] == pytest.approx(24_000_000.0)
    assert base["interest_rate"] == pytest.approx(0.0725)
    assert base["ltv"] == pytest.approx(0.62)
    assert base["amortization_years"] == 25
    assert base["term_years"] == 7
    for key, row in zip(_OM_DEBT_KEYS, _OM_DEBT_ROWS, strict=True):
        assert base["__sources__"][key] == SOURCE_OM_BROKER, key
        sf = base["__source_fields__"][key]
        assert sf["field_name"] == row["field_name"], key
        assert sf["doc_type"] == "OM"
        assert sf["source_page"] == 44
        assert key not in base["__reasons__"]


@pytest.mark.asyncio
async def test_no_om_debt_rows_keep_the_debt_keys_on_the_seed() -> None:
    from fondok_schemas.reasons import ReasonCode

    from app.services.engine_runner import SOURCE_SEED

    deal_id = uuid4()
    await _insert_deal(deal_id, name="No OM debt", keys=132, purchase=36_400_000)
    # An OM with no in-place-debt block changes nothing on the debt side.
    await _insert_om_extraction(deal_id, fields=[_OM_RENO_ROW])

    base = await _base_for(deal_id)
    assert base["ltv"] == pytest.approx(0.65)
    assert base["interest_rate"] == pytest.approx(0.068)
    assert "loan_amount" not in base
    for key in ("interest_rate", "ltv", "amortization_years", "term_years"):
        assert base["__sources__"][key] == SOURCE_SEED, key
        assert key not in base["__source_fields__"]
        assert base["__reasons__"][key]["code"] is ReasonCode.NO_SOURCE


@pytest.mark.asyncio
async def test_analyst_override_still_wins_over_the_om_in_place_debt() -> None:
    """A changed override wins on value and label and the OM row is not
    claimed for it; the sibling keys the analyst left alone stay the broker's."""
    from app.services.engine_runner import SOURCE_ANALYST_OVERRIDE, SOURCE_OM_BROKER

    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        name="Refi quote",
        keys=132,
        purchase=36_400_000,
        field_overrides={"interest_rate": {"value": 0.08, "note": "lender term sheet"}},
    )
    await _insert_om_extraction(deal_id, fields=_OM_DEBT_ROWS)

    base = await _base_for(deal_id)
    assert base["interest_rate"] == pytest.approx(0.08)
    assert base["__sources__"]["interest_rate"] == SOURCE_ANALYST_OVERRIDE
    assert "interest_rate" not in base["__source_fields__"]
    assert base["__sources__"]["ltv"] == SOURCE_OM_BROKER
    assert base["__sources__"]["loan_amount"] == SOURCE_OM_BROKER
    assert base["__source_fields__"]["ltv"]["field_name"] == "in_place_debt.ltv_pct"


# ═════════════ W5 "price" — purchase price precedence, end to end ═════════════


_OM_ASKING_ROW = {
    "field_name": "asking_price.headline_price_usd",
    "value": 40_000_000.0,
    "source_page": 4,
    "confidence": 0.9,
}


@pytest.mark.asyncio
async def test_purchase_price_precedence_override_beats_row_beats_om() -> None:
    """override > deal record > OM asking price. The analyst's entry wins
    over both the deal row and the OM headline; the row wins over the OM."""
    from app.services.engine_runner import (
        SOURCE_ANALYST_OVERRIDE,
        SOURCE_DEAL_ROW,
        SOURCE_OM_BROKER,
    )

    override = {"purchase_price": {"value": 38_000_000, "note": "Our bid"}}

    # All three present → the override.
    all_three = uuid4()
    await _insert_deal(
        all_three, name="All three", keys=132, purchase=36_400_000,
        field_overrides=override,
    )
    await _insert_om_extraction(all_three, fields=[_OM_ASKING_ROW])
    base = await _base_for(all_three)
    assert base["purchase_price"] == pytest.approx(38_000_000.0)
    assert base["__sources__"]["purchase_price"] == SOURCE_ANALYST_OVERRIDE

    # Override + OM, no row → the override.
    override_om = uuid4()
    await _insert_deal(
        override_om, name="Override and OM", keys=132, purchase=0,
        field_overrides=override,
    )
    await _insert_om_extraction(override_om, fields=[_OM_ASKING_ROW])
    base = await _base_for(override_om)
    assert base["purchase_price"] == pytest.approx(38_000_000.0)
    assert base["__sources__"]["purchase_price"] == SOURCE_ANALYST_OVERRIDE

    # Row + OM, no override → the row.
    row_om = uuid4()
    await _insert_deal(row_om, name="Row and OM", keys=132, purchase=36_400_000)
    await _insert_om_extraction(row_om, fields=[_OM_ASKING_ROW])
    base = await _base_for(row_om)
    assert base["purchase_price"] == pytest.approx(36_400_000.0)
    assert base["__sources__"]["purchase_price"] == SOURCE_DEAL_ROW

    # OM only → the OM, with the row it was read off (page included).
    om_only = uuid4()
    await _insert_deal(om_only, name="OM only", keys=132, purchase=0)
    await _insert_om_extraction(om_only, fields=[_OM_ASKING_ROW])
    base = await _base_for(om_only)
    assert base["purchase_price"] == pytest.approx(40_000_000.0)
    assert base["__sources__"]["purchase_price"] == SOURCE_OM_BROKER
    assert base["__source_fields__"]["purchase_price"]["source_page"] == 4


@pytest.mark.asyncio
async def test_no_price_anywhere_is_refused_and_never_inferred_from_a_cap_rate() -> None:
    """An unpriced OM that states a broker cap rate (and a price per key)
    never yields a purchase price: no NOI ÷ cap, no per-key × keys. The key
    stays absent with the ``missing_identity_inputs`` reason."""
    om_cap_only = uuid4()
    await _insert_deal(om_cap_only, name="Unpriced OM", keys=132, purchase=0)
    await _insert_om_extraction(
        om_cap_only,
        fields=[
            {"field_name": "broker_proforma.cap_rate", "value": 0.07},
            {"field_name": "broker_proforma.noi_usd", "value": 2_500_000.0},
        ],
    )
    base = await _base_for(om_cap_only)
    assert "purchase_price" not in base
    assert base["__reasons__"]["purchase_price"]["code"] == "missing_identity_inputs"


# ═══════ Sam finding 2c — the T-12 department revenue behind each cost ratio ═══════


@pytest.mark.asyncio
async def test_t12_department_revenue_reaches_the_expense_engine() -> None:
    """The T-12 rows that ground the revenue anchors are also the denominators
    of the departmental cost ratios: rooms revenue, F&B revenue, and the other
    pool (other + misc, resort fees on their own line left out)."""
    from app.services.engine_runner import _build_input_for

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Dept Ratio Deal", keys=214, purchase=42_000_000)
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.operational_kpis.occupancy_pct", "value": 0.823},
            {"field_name": "p_and_l_usali.operational_kpis.adr_usd", "value": 241.0},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 18_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.fb_revenue", "value": 5_500_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 1_200_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 100_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.resort_fees", "value": 800_000.0},
            {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 4_235_000.0},
            {"field_name": "p_and_l_usali.departmental_expenses.rooms", "value": 4_500_000.0},
            {"field_name": "p_and_l_usali.departmental_expenses.other_operated", "value": 600_000.0},
        ],
    )
    base = await _base_for(deal_id)
    assert base["t12_dept_revenue"] == {
        "rooms_revenue": pytest.approx(18_000_000.0),
        "fb_revenue": pytest.approx(5_500_000.0),
        "other_revenue": pytest.approx(1_300_000.0),
    }
    assert base["t12_expense_actuals"].get("fb_dept_expense") == pytest.approx(4_235_000.0)

    from app.engines.revenue import RevenueEngine
    from app.engines.fb_revenue import FBRevenueEngine

    deal_uuid = deal_id
    acc: dict = {}
    acc["revenue"] = RevenueEngine().run(_build_input_for("revenue", deal_uuid, base, acc))
    acc["fb"] = FBRevenueEngine().run(_build_input_for("fb", deal_uuid, base, acc))
    exp_in = _build_input_for("expense", deal_uuid, base, acc)
    assert exp_in.t12_dept_revenue == base["t12_dept_revenue"]


@pytest.mark.asyncio
async def test_no_t12_revenue_means_no_department_revenue() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, name="No Docs Dept", keys=132, purchase=36_400_000)
    base = await _base_for(deal_id)
    assert base["t12_dept_revenue"] == {}


@pytest.mark.asyncio
async def test_monthly_rooms_line_is_never_a_department_revenue() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Monthly Rooms Dept", keys=132, purchase=36_400_000)
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.83},
            {"field_name": "adr_usd", "value": 232.77},
            {"field_name": "p_and_l_usali.monthly.apr_2024.rooms_revenue", "value": 954_187.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_266_603.0},
        ],
    )
    base = await _base_for(deal_id)
    assert "rooms_revenue" not in base["t12_dept_revenue"]
    # The other pool is only a department revenue when it grounded the ratio.
    assert "other_revenue" not in base["t12_dept_revenue"]


# ═══════ Sam finding 4 — the Year-1 management fee is contractual ═══════


async def _expense_input_for(base: dict, deal_id: UUID):
    from app.engines.fb_revenue import FBRevenueEngine
    from app.engines.revenue import RevenueEngine
    from app.services.engine_runner import _build_input_for

    acc: dict = {}
    acc["revenue"] = RevenueEngine().run(_build_input_for("revenue", deal_id, base, acc))
    acc["fb"] = FBRevenueEngine().run(_build_input_for("fb", deal_id, base, acc))
    return _build_input_for("expense", deal_id, base, acc)


_T12_WITH_FEE = [
    {"field_name": "p_and_l_usali.operational_kpis.occupancy_pct", "value": 0.80},
    {"field_name": "p_and_l_usali.operational_kpis.adr_usd", "value": 300.0},
    {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 10_000_000.0},
    {"field_name": "p_and_l_usali.fees_and_reserves.mgmt_fee", "value": 619_834.0},
]


@pytest.mark.asyncio
async def test_extracted_t12_fee_never_reaches_year_one() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, name="T12 Fee", keys=120, purchase=40_000_000)
    await _insert_t12_extraction(deal_id, fields=_T12_WITH_FEE)
    base = await _base_for(deal_id)
    assert base["t12_expense_actuals"].get("mgmt_fee") == pytest.approx(619_834.0)
    exp_in = await _expense_input_for(base, deal_id)
    assert exp_in.mgmt_fee_y1_override is None


@pytest.mark.asyncio
async def test_analyst_year_one_fee_reaches_the_engine() -> None:
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        name="Analyst Fee",
        keys=120,
        purchase=40_000_000,
        field_overrides={"mgmt_fee": {"value": 410_000.0, "note": "HMA minimum fee"}},
    )
    await _insert_t12_extraction(deal_id, fields=_T12_WITH_FEE)
    base = await _base_for(deal_id)
    exp_in = await _expense_input_for(base, deal_id)
    assert exp_in.mgmt_fee_y1_override == pytest.approx(410_000.0)



# ═══════ Review C-1 — a cost ratio pairs one statement's expense and revenue ═══════


async def _insert_doc_rows(deal_id: UUID, rows: list[dict[str, object]]) -> None:
    await _insert_t12_extraction(deal_id, fields=rows)


_ANGLER_KPIS = [
    {"field_name": "p_and_l_usali.operational_kpis.occupancy_pct", "value": 0.8326},
    {"field_name": "p_and_l_usali.operational_kpis.adr_usd", "value": 236.09},
]


@pytest.mark.asyncio
async def test_rooms_revenue_far_from_occupancy_times_adr_is_not_a_denominator() -> None:
    # The orig Angler shape: rooms revenue read as 552.774 off a
    # cost-of-services row; occupancy × ADR × 132 keys × 365 ≈ $9.47M.
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler orig shape", keys=132, purchase=36_400_000)
    await _insert_doc_rows(
        deal_id,
        [
            *_ANGLER_KPIS,
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 552.774},
            {"field_name": "p_and_l_usali.departmental_expenses.rooms", "value": 2_769_483.0},
        ],
    )
    base = await _base_for(deal_id)
    assert "rooms_revenue" not in base["t12_dept_revenue"]
    # The engine then prices the T-12 rooms expense on projected revenue: sane.
    from app.engines.expense import ExpenseEngine
    from app.engines.fb_revenue import FBRevenueEngine
    from app.engines.revenue import RevenueEngine
    from app.services.engine_runner import _build_input_for

    acc: dict = {}
    acc["revenue"] = RevenueEngine().run(_build_input_for("revenue", deal_id, base, acc))
    acc["fb"] = FBRevenueEngine().run(_build_input_for("fb", deal_id, base, acc))
    out = ExpenseEngine().run(_build_input_for("expense", deal_id, base, acc))
    ratio = out.line_methods["rooms_dept_expense"].value
    assert 0.2 < ratio < 0.4
    assert all(y.noi > -1e7 for y in out.years)


@pytest.mark.asyncio
async def test_expense_and_revenue_from_different_documents_do_not_pair() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Two statements", keys=132, purchase=36_400_000)
    await _insert_doc_rows(
        deal_id,
        [*_ANGLER_KPIS, {"field_name": "p_and_l_usali.operating_revenue.fb_revenue", "value": 2_019_520.0}],
    )
    await _insert_doc_rows(
        deal_id,
        [{"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 2_290_364.0}],
    )
    base = await _base_for(deal_id)
    assert base["t12_expense_actuals"].get("fb_dept_expense") == pytest.approx(2_290_364.0)
    assert "fb_revenue" not in base["t12_dept_revenue"]


@pytest.mark.asyncio
async def test_analyst_expense_override_unpairs_the_department() -> None:
    deal_id = uuid4()
    await _insert_deal(
        deal_id,
        name="Analyst F&B expense",
        keys=132,
        purchase=36_400_000,
        field_overrides={"fb_dept_expense": {"value": 2_000_000.0, "note": "corrected"}},
    )
    await _insert_doc_rows(
        deal_id,
        [
            *_ANGLER_KPIS,
            {"field_name": "p_and_l_usali.operating_revenue.fb_revenue", "value": 3_390_000.0},
            {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 2_610_000.0},
        ],
    )
    base = await _base_for(deal_id)
    assert base["t12_expense_actuals"]["fb_dept_expense"] == pytest.approx(2_000_000.0)
    assert "fb_revenue" not in base["t12_dept_revenue"]


@pytest.mark.asyncio
async def test_ffe_reserve_t12_dollars_never_reach_year_one_but_an_analyst_figure_does() -> None:
    rows = [
        *_T12_WITH_FEE,
        {"field_name": "p_and_l_usali.fees_and_reserves.ffe_reserve", "value": 480_135.0},
    ]
    plain = uuid4()
    await _insert_deal(plain, name="T12 reserve", keys=120, purchase=40_000_000)
    await _insert_t12_extraction(plain, fields=rows)
    exp_in = await _expense_input_for(await _base_for(plain), plain)
    assert exp_in.ffe_reserve_y1_override is None

    analyst = uuid4()
    await _insert_deal(
        analyst, name="Analyst reserve", keys=120, purchase=40_000_000,
        field_overrides={"ffe_reserve": {"value": 500_000.0, "note": "lender reserve"}},
    )
    await _insert_t12_extraction(analyst, fields=rows)
    exp_in = await _expense_input_for(await _base_for(analyst), analyst)
    assert exp_in.ffe_reserve_y1_override == pytest.approx(500_000.0)


# ═══════ Review I-1 — the management agreement (LEASES) sets the fee terms ═══════

from fondok_schemas.reasons import ReasonCode  # noqa: E402


async def _insert_hma(deal_id: UUID, fields: list[dict[str, object]]) -> tuple[UUID, UUID]:
    return await _insert_financial_extraction(
        deal_id,
        doc_type="LEASES",
        filename="Angler_s HMA Summary.docx",
        fields=fields,
        ts=datetime.now(UTC),
    )


_HMA_ROWS = [
    {"field_name": "broker_proforma.management_fee_base_pct", "value": 0.0325, "source_page": 2},
    {"field_name": "incentive_management_fee_pct", "value": 0.15, "source_page": 3},
    {"field_name": "incentive_management_fee_hurdle_pct", "value": 0.10, "source_page": 3},
    {"field_name": "management_fee_in_lieu_usd", "value": 76_000.0},
]


@pytest.mark.asyncio
async def test_hma_base_fee_sets_mgmt_fee_pct_with_its_row() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, name="HMA deal", keys=132, purchase=36_400_000)
    _doc, er = await _insert_hma(deal_id, _HMA_ROWS)
    base = await _base_for(deal_id)
    assert base["mgmt_fee_pct"] == pytest.approx(0.0325)
    assert base["__sources__"]["mgmt_fee_pct"] == "management_agreement"
    src = base["__source_fields__"]["mgmt_fee_pct"]
    assert src["field_name"] == "broker_proforma.management_fee_base_pct"
    assert src["doc_type"] == "LEASES"
    assert src["extraction_result_id"] == str(er)


@pytest.mark.asyncio
async def test_hma_incentive_with_a_hurdle_is_disclosed_not_modelled() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, name="HMA incentive", keys=132, purchase=36_400_000)
    await _insert_hma(deal_id, _HMA_ROWS)
    base = await _base_for(deal_id)
    assert base.get("incentive_mgmt_fee_pct_of_gop") is None
    r = base["__reasons__"]["incentive_mgmt_fee_pct_of_gop"]
    assert r["code"] is ReasonCode.AWAITING_ANALYST
    assert "15.0%" in r["detail"] and "10.0%" in r["detail"]
    assert "not modelled" in r["detail"]


@pytest.mark.asyncio
async def test_hma_incentive_on_gop_without_hurdle_is_modelled() -> None:
    deal_id = uuid4()
    await _insert_deal(deal_id, name="HMA GOP incentive", keys=132, purchase=36_400_000)
    await _insert_hma(
        deal_id,
        [
            {"field_name": "management_fee_base_pct", "value": 3.0},
            {"field_name": "incentive_management_fee_pct", "value": 10.0},
            {"field_name": "incentive_management_fee_basis", "value": "Gross Operating Profit after base fee"},
        ],
    )
    base = await _base_for(deal_id)
    assert base["mgmt_fee_pct"] == pytest.approx(0.03)
    assert base["incentive_mgmt_fee_pct_of_gop"] == pytest.approx(0.10)
    assert base["__sources__"]["incentive_mgmt_fee_pct_of_gop"] == "management_agreement"
    assert "incentive_mgmt_fee_pct_of_gop" not in base["__reasons__"]


@pytest.mark.asyncio
async def test_analyst_fee_override_beats_the_hma() -> None:
    deal_id = uuid4()
    await _insert_deal(
        deal_id, name="HMA override", keys=132, purchase=36_400_000,
        field_overrides={"mgmt_fee_pct": {"value": 0.04, "note": "amended HMA"}},
    )
    await _insert_hma(deal_id, _HMA_ROWS)
    base = await _base_for(deal_id)
    assert base["mgmt_fee_pct"] == pytest.approx(0.04)
    assert base["__sources__"]["mgmt_fee_pct"] == "analyst_override"

# ═══════ W5 loader — exact alias first, one statement per period, notices ═══════
#
# Real deal b67bfdee (read-only probe, 2026-11): the FY2024 Detailed P&L lists
# ``minor_operated_departments.cost_of_services.rooms_revenue_usd`` ($552.77, a
# cost-of-services sub-line) BEFORE ``operating_revenue.rooms_revenue``
# ($9,496,407.22). The tail alias ``rooms_revenue_usd`` let the cost line claim
# rooms revenue; the corroboration median then averaged it with the FY2024
# Summary CSV ($8.9M) to $4.45M, other revenue read 31% of rooms and the 30%
# cap clipped it silently, and F&B per occupied room came out at the mean of
# the two statements ($50.35, matching neither).

_DETAILED_PNL = "Copy of Angler_s 2024 Full Year Detailed P&L.xlsm"
_SUMMARY_CSV = "Angler_FY2024_Summary_PL.csv"


def _detailed_pnl_fields() -> list[dict[str, object]]:
    return [
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.832567, "source_page": 3},
        {"field_name": "ttm_performance.subject.adr_usd", "value": 236.094, "source_page": 3},
        {
            "field_name": "p_and_l_usali.minor_operated_departments.cost_of_services.rooms_revenue_usd",
            "value": 552.774,
            "source_page": 5,
        },
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_739_040.71, "source_page": 5},
        {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 17_806.42, "source_page": 5},
        {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_228_475.94, "source_page": 5},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_496_407.22, "source_page": 5},
        {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 13_481_730.29, "source_page": 5},
        {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 2_100_000.0, "source_page": 6},
        {"field_name": "p_and_l_usali.undistributed.utilities", "value": 288_000.0, "source_page": 6},
        {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_160_000.0, "source_page": 6},
    ]


def _summary_csv_fields() -> list[dict[str, object]]:
    return [
        {"field_name": "p_and_l_usali.period_type", "value": "annual"},
        {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_900_000.0},
        {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 1_300_000.0},
        {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 320_000.0},
        {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 900_000.0},
    ]


async def _insert_angler_fy2024_pair(deal_id: UUID) -> tuple[UUID, UUID]:
    """The two FY2024 statements of deal b67bfdee; ``(detailed_doc, summary_doc)``."""
    base = datetime(2026, 9, 20, 5, 0, tzinfo=UTC)
    detailed, _ = await _insert_financial_extraction(
        deal_id,
        doc_type="PNL",
        filename=_DETAILED_PNL,
        fiscal_year=2024,
        ts=base,
        fields=_detailed_pnl_fields(),
    )
    summary, _ = await _insert_financial_extraction(
        deal_id,
        doc_type="PNL",
        filename=_SUMMARY_CSV,
        ts=base + timedelta(minutes=1),  # newer upload — completeness still ranks it second
        fields=_summary_csv_fields(),
    )
    return detailed, summary


@pytest.mark.asyncio
async def test_exact_alias_beats_a_cost_section_tail_match() -> None:
    """An exact full-path alias wins over a tail match anywhere in the row, so
    the Detailed P&L's rooms revenue is its operating-revenue line."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_revenue_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler Detailed Only", keys=132, purchase=36_400_000)
    await _insert_financial_extraction(
        deal_id,
        doc_type="PNL",
        filename=_DETAILED_PNL,
        fiscal_year=2024,
        ts=datetime(2026, 9, 20, tzinfo=UTC),
        fields=_detailed_pnl_fields(),
    )
    factory = get_session_factory()
    async with factory() as session:
        rev, prov = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )
    assert rev["rooms_revenue"] == pytest.approx(9_496_407.22)
    assert prov["rooms_revenue"].resolution.field_name == (
        "p_and_l_usali.operating_revenue.rooms_revenue"
    )


@pytest.mark.asyncio
async def test_a_tail_match_never_comes_from_the_opposite_section() -> None:
    """With no exact alias, the tail fallback still applies — but never to a
    revenue key from a cost / expense section, nor to an expense key from a
    revenue section."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _load_t12_expense_actuals,
        _load_t12_revenue_actuals,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Opposite Sections", keys=132, purchase=36_400_000)
    await _insert_t12_extraction(
        deal_id,
        fields=[
            # Cost-section lines whose tails are revenue aliases.
            {"field_name": "p_and_l_usali.minor_operated_departments.cost_of_services.rooms_revenue_usd", "value": 552.774},
            {"field_name": "p_and_l_usali.departmental_expenses.fb_revenue", "value": 4_100.0},
            # A revenue-section line whose tail is an expense alias.
            {"field_name": "p_and_l_usali.operating_revenue.utilities", "value": 12_000.0},
            # A tail match outside any opposite section still resolves.
            {"field_name": "summary.other_revenue_usd", "value": 18_986.9},
            {"field_name": "summary.insurance", "value": 1_429_573.0},
        ],
    )
    factory = get_session_factory()
    async with factory() as session:
        rev = await _load_t12_revenue_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
        exp = await _load_t12_expense_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
    assert "rooms_revenue" not in rev
    assert "fb_revenue" not in rev
    assert rev["other_revenue"] == pytest.approx(18_986.9)
    assert "utilities" not in exp
    assert exp["insurance"] == pytest.approx(1_429_573.0)


@pytest.mark.asyncio
async def test_exact_expense_alias_beats_an_earlier_tail_match() -> None:
    """The expense loader resolves exact full paths first too."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_t12_expense_actuals

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Expense Exact First", keys=132, purchase=36_400_000)
    await _insert_t12_extraction(
        deal_id,
        fields=[
            {"field_name": "p_and_l_usali.schedules.workers_comp.insurance", "value": 41_000.0},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_429_573.0},
        ],
    )
    factory = get_session_factory()
    async with factory() as session:
        exp = await _load_t12_expense_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
    assert exp["insurance"] == pytest.approx(1_429_573.0)


@pytest.mark.asyncio
async def test_two_same_period_statements_never_blend() -> None:
    """Deal b67bfdee: the Detailed P&L (primary) supplies every Year-1 line;
    the Summary CSV of the same year is never averaged in, on revenue or
    expenses, and the provenance names the Detailed P&L's own rows."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        _load_t12_expense_actuals,
        _load_t12_revenue_actuals,
    )

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler FY2024", keys=132, purchase=36_400_000)
    detailed, _summary = await _insert_angler_fy2024_pair(deal_id)
    factory = get_session_factory()
    async with factory() as session:
        rev, rev_prov = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )
        exp, exp_prov = await _load_t12_expense_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )
    assert rev["rooms_revenue"] == pytest.approx(9_496_407.22)
    assert rev["fb_revenue"] == pytest.approx(2_739_040.71)
    assert rev["other_revenue"] == pytest.approx(17_806.42)
    assert rev["misc_revenue"] == pytest.approx(1_228_475.94)
    assert exp["insurance"] == pytest.approx(1_160_000.0)
    for canonical in ("rooms_revenue", "fb_revenue", "other_revenue", "misc_revenue"):
        assert rev_prov[canonical].document_id == str(detailed), canonical
        assert rev_prov[canonical].resolution.field_name is not None, canonical
    assert exp_prov["insurance"].document_id == str(detailed)


@pytest.mark.asyncio
async def test_same_period_conflict_is_a_needs_review_notice_naming_both_documents() -> None:
    """The Year-1 anchors come off the Detailed P&L (F&B $68.28 per occupied
    room, other revenue 13.1% of rooms, no clipping) and every key grounded on
    it carries a NEEDS_REVIEW notice naming both statements, both values and
    which one the model used."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler FY2024", keys=132, purchase=36_400_000)
    detailed, _summary = await _insert_angler_fy2024_pair(deal_id)
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

    orn = 0.832567 * 132 * 365
    assert base["fb_revenue_per_occupied_room"] == pytest.approx(2_739_040.71 / orn)
    assert base["fb_revenue_per_occupied_room"] == pytest.approx(68.28, abs=0.01)
    assert base["other_revenue_pct_of_rooms"] == pytest.approx(
        (17_806.42 + 1_228_475.94) / 9_496_407.22
    )
    assert base["other_revenue_pct_of_rooms"] == pytest.approx(0.1312, abs=0.0005)

    reasons = base["__reasons__"]
    src = base["__source_fields__"]
    for key in ("fb_revenue_per_occupied_room", "other_revenue_pct_of_rooms", "starting_occupancy"):
        assert src[key]["document_id"] == str(detailed), key
        assert reasons[key]["code"] is ReasonCode.NEEDS_REVIEW, key
        detail = reasons[key]["detail"]
        assert _SUMMARY_CSV in detail and _DETAILED_PNL in detail, detail
        assert "$9,496,407" in detail and "$8,900,000" in detail, detail
        assert "$2,739,041" in detail and "$1,300,000" in detail, detail
        assert f"uses {_DETAILED_PNL}" in detail, detail
        assert "cap" not in detail.lower(), detail
    # The other ratio was not clipped, so its provenance carries no cap facts.
    assert "uncapped_ratio" not in src["other_revenue_pct_of_rooms"]


@pytest.mark.asyncio
async def test_agreeing_same_period_statements_raise_no_notice() -> None:
    """Two statements of the same period that agree within 5% on every
    headline line are corroboration, not a conflict — no notice."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Agreeing Pair", keys=132, purchase=36_400_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename=_DETAILED_PNL, fiscal_year=2024, ts=base_ts,
        fields=_detailed_pnl_fields(),
    )
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename=_SUMMARY_CSV, ts=base_ts + timedelta(minutes=1),
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_400_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_700_000.0},
        ],
    )
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)
    reasons = base["__reasons__"]
    for key in ("fb_revenue_per_occupied_room", "other_revenue_pct_of_rooms", "starting_occupancy"):
        assert key not in reasons, reasons.get(key)


@pytest.mark.asyncio
async def test_three_statements_reject_an_outlier_primary_as_a_whole_statement() -> None:
    """Real deal 7a9928e0: with three same-period statements the median may
    reject the top-ranked one as an outlier — and then the WHOLE statement
    switches (every line from the next statement), never one line."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs, _load_t12_revenue_actuals
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Corroboration Deal", keys=300, purchase=90_000_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)

    def _t12(rooms: float, fb: float, occ: float) -> list[dict[str, object]]:
        return [
            {"field_name": "p_and_l_usali.period_type", "value": "ttm"},
            {"field_name": "occupancy_pct", "value": occ},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": rooms},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": fb},
        ]

    # Upload order is the last tiebreaker (newest first): the mis-read
    # statement is the newest, so it ranks first.
    await _insert_financial_extraction(
        deal_id, doc_type="T12", filename="T12_c.xlsx", ts=base_ts, fields=_t12(9_600_000.0, 2_914_380.0, 0.82)
    )
    second, _ = await _insert_financial_extraction(
        deal_id, doc_type="T12", filename="T12_b.xlsx", ts=base_ts + timedelta(minutes=1),
        fields=_t12(9_400_000.0, 2_737_410.0, 0.81),
    )
    await _insert_financial_extraction(
        deal_id, doc_type="T12", filename="T12_a_misread.xlsx", ts=base_ts + timedelta(minutes=2),
        fields=_t12(9_500_000.0, 96_528.0, 0.80),
    )
    factory = get_session_factory()
    async with factory() as session:
        rev, prov = await _load_t12_revenue_actuals(
            session, deal_id=str(deal_id), tenant_id=_TENANT, with_provenance=True
        )
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)

    # Every line from the next-ranked statement — rooms and occupancy too,
    # although the mis-read statement's rooms line agreed.
    assert rev["fb_revenue"] == pytest.approx(2_737_410.0)
    assert rev["rooms_revenue"] == pytest.approx(9_400_000.0)
    assert rev["occupancy"] == pytest.approx(0.81)
    assert {p.document_id for p in prov.values()} == {str(second)}
    notice = base["__reasons__"]["fb_revenue_per_occupied_room"]
    assert notice["code"] is ReasonCode.NEEDS_REVIEW
    assert "T12_a_misread.xlsx" in notice["detail"] and "$96,528" in notice["detail"]
    assert "uses T12_b.xlsx" in notice["detail"]


@pytest.mark.asyncio
async def test_three_statements_keep_a_primary_that_is_not_an_outlier() -> None:
    """The median never overrides a primary statement that is merely a few
    percent off its peers: the primary stays, and the disagreement is a
    notice."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs, _load_t12_revenue_actuals
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Close Peers", keys=300, purchase=90_000_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    fb = {"T12_c.xlsx": 2_737_410.0, "T12_b.xlsx": 96_528.0, "T12_a.xlsx": 2_914_380.0}
    for i, (name, value) in enumerate(fb.items()):
        await _insert_financial_extraction(
            deal_id, doc_type="T12", filename=name, ts=base_ts + timedelta(minutes=i),
            fields=[
                {"field_name": "p_and_l_usali.period_type", "value": "ttm"},
                {"field_name": "occupancy_pct", "value": 0.80},
                {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_500_000.0},
                {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": value},
            ],
        )
    factory = get_session_factory()
    async with factory() as session:
        rev = await _load_t12_revenue_actuals(session, deal_id=str(deal_id), tenant_id=_TENANT)
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)
    # T12_a (newest) ranks first and is 6.5% off the median — not an outlier.
    assert rev["fb_revenue"] == pytest.approx(2_914_380.0)
    notice = base["__reasons__"]["fb_revenue_per_occupied_room"]
    assert notice["code"] is ReasonCode.NEEDS_REVIEW
    assert "uses T12_a.xlsx" in notice["detail"]


@pytest.mark.asyncio
async def test_other_revenue_cap_is_never_silent() -> None:
    """When the other-revenue ratio is clipped at 30% the clip is recorded on
    the provenance (uncapped ratio + cap) and as a NEEDS_REVIEW notice."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Capped Other", keys=132, purchase=36_400_000)
    await _insert_financial_extraction(
        deal_id, doc_type="T12", filename="Resort_T12.xlsx", ts=datetime(2026, 9, 20, tzinfo=UTC),
        fields=[
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "adr_usd", "value": 300.0},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 5_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 2_000_000.0},
        ],
    )
    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)
    assert base["other_revenue_pct_of_rooms"] == pytest.approx(0.30)
    sf = base["__source_fields__"]["other_revenue_pct_of_rooms"]
    assert sf["uncapped_ratio"] == pytest.approx(0.40)
    assert sf["cap"] == pytest.approx(0.30)
    notice = base["__reasons__"]["other_revenue_pct_of_rooms"]
    assert notice["code"] is ReasonCode.NEEDS_REVIEW
    assert "40.0%" in notice["detail"] and "30%" in notice["detail"]
    assert "$2,000,000" in notice["detail"] and "$5,000,000" in notice["detail"]


# ═══════ W5 round 2 — same-period fill, footing choice, exact periods, link ═══════


async def _base_and_sources(deal_id: UUID) -> tuple[dict, object]:
    from app.api.deals import get_assumption_sources
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    factory = get_session_factory()
    async with factory() as session:
        base = await _load_engine_inputs(session, str(deal_id), tenant_id=_TENANT)
        response = await get_assumption_sources(deal_id, session, UUID(_TENANT))
    return base, response


@pytest.mark.asyncio
async def test_a_missing_line_fills_from_the_same_period_first_with_a_notice() -> None:
    """The primary FY2024 statement lacks insurance and F&B departmental
    expense. Insurance is read from the other FY2024 statement (same period)
    BEFORE the FY2023 statement; F&B dept expense, which only FY2023 carries,
    comes from FY2023. Each filled line names its source and period."""
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Gap Fill", keys=132, purchase=36_400_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    detailed, _ = await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="FY2024 Detailed.xlsx", fiscal_year=2024, ts=base_ts,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "adr_usd", "value": 250.0},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_636_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_000_000.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 300_000.0},
            {"field_name": "p_and_l_usali.undistributed.sales_marketing", "value": 700_000.0},
        ],
    )
    summary, _ = await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="FY2024 Summary.csv", fiscal_year=2024,
        ts=base_ts + timedelta(minutes=1),
        fields=[
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_100_000.0},
        ],
    )
    older, _ = await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="FY2023 P&L.xlsx", fiscal_year=2023,
        ts=base_ts + timedelta(minutes=2),
        fields=[
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 900_000.0},
            {"field_name": "p_and_l_usali.departmental_expenses.food_beverage", "value": 1_500_000.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    exp = base["t12_expense_actuals"]
    src = base["__source_fields__"]
    reasons = base["__reasons__"]

    assert exp["insurance"] == pytest.approx(1_100_000.0)
    assert src["insurance"]["document_id"] == str(summary)
    ins = reasons["insurance"]
    assert ins["code"] is ReasonCode.NEEDS_REVIEW
    assert "FY2024 Summary.csv" in ins["detail"] and "FY2024 Detailed.xlsx" in ins["detail"]
    assert "same period" in ins["detail"] and "FY2024" in ins["detail"]

    assert exp["fb_dept_expense"] == pytest.approx(1_500_000.0)
    assert src["fb_dept_expense"]["document_id"] == str(older)
    fb = reasons["fb_dept_expense"]
    assert fb["code"] is ReasonCode.NEEDS_REVIEW
    assert "FY2023 P&L.xlsx" in fb["detail"] and "different period" in fb["detail"]
    assert "FY2023" in fb["detail"]

    # Lines the primary carries carry no fill notice.
    assert "utilities" not in reasons
    assert src["utilities"]["document_id"] == str(detailed)


_T12_MISREAD = "Angler_s 2024 Full Year Detailed P&L.xlsm"
_DEC_2024 = "The Angler_s - December 2024 Financials.xlsx"


async def _insert_7a9928e0_pair(deal_id: UUID) -> tuple[UUID, UUID]:
    """Deal 7a9928e0 today: the T-12 ranks first but its F&B is the $96,528
    misread and its only rooms line is the prior-year one, so its revenue
    lines cannot foot to its stated total; the December 2024 Financials of
    the same year foot."""
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    t12, _ = await _insert_financial_extraction(
        deal_id, doc_type="T12", filename=_T12_MISREAD, fiscal_year=2024, ts=base_ts,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.8326},
            {"field_name": "adr_usd", "value": 236.09},
            {"field_name": "p_and_l_usali.prior_year.rooms_revenue", "value": 9_807_990.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 96_528.1},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 10_839_200.0},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_429_573.0},
        ],
    )
    dec, _ = await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename=_DEC_2024, fiscal_year=2024,
        ts=base_ts + timedelta(minutes=1),
        fields=[
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_496_410.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_737_410.0},
            {"field_name": "p_and_l_usali.operating_revenue.other_revenue", "value": 18_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.misc_revenue", "value": 1_328_280.0},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 13_580_100.0},
        ],
    )
    return t12, dec


@pytest.mark.asyncio
async def test_a_primary_that_does_not_foot_yields_the_whole_period_to_one_that_does() -> None:
    """I2 (decided): between statements of the same period, the one whose
    revenue lines foot to its own stated total wins when the top-ranked does
    not — the WHOLE statement switches. F&B per occupied room lands on the
    footing statement (~$68), not the misread's $2.41, and the notice names
    both documents and why."""
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="7a9928e0", keys=132, purchase=36_400_000)
    t12, dec = await _insert_7a9928e0_pair(deal_id)
    base, _resp = await _base_and_sources(deal_id)
    src = base["__source_fields__"]

    orn = 0.8326 * 132 * 365
    assert base["fb_revenue_per_occupied_room"] == pytest.approx(2_737_410.0 / orn)
    assert 67.0 < base["fb_revenue_per_occupied_room"] < 69.0
    assert src["fb_revenue_per_occupied_room"]["document_id"] == str(dec)
    assert base["other_revenue_pct_of_rooms"] == pytest.approx((18_000.0 + 1_328_280.0) / 9_496_410.0)
    # Occupancy / ADR / insurance are not on the December statement: they
    # are filled from the T-12 of the same period, with a notice.
    assert src["starting_occupancy"]["document_id"] == str(t12)
    notice = base["__reasons__"]["fb_revenue_per_occupied_room"]
    assert notice["code"] is ReasonCode.NEEDS_REVIEW
    detail = notice["detail"]
    assert _T12_MISREAD in detail and _DEC_2024 in detail
    assert "$96,528" in detail and "$2,737,410" in detail
    assert "foot" in detail
    assert f"uses {_DEC_2024}" in detail
    occ = base["__reasons__"]["starting_occupancy"]
    assert "same period" in occ["detail"] and _T12_MISREAD in occ["detail"]
    # The conflict note says exactly which lines did not come off the
    # statement used, and from where.
    carried = detail.split(f"{_T12_MISREAD}:")[1]
    assert "supplies" in carried
    for line in ("Occupancy", "ADR", "Insurance"):
        assert line in carried, line


@pytest.mark.asyncio
async def test_when_no_statement_foots_the_top_ranked_is_kept() -> None:
    """Footing only decides when it discriminates: neither statement states a
    total, so the top-ranked statement is kept and the conflict is a notice."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="No Totals", keys=132, purchase=36_400_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    await _insert_financial_extraction(
        deal_id, doc_type="T12", filename="A.xlsx", fiscal_year=2024, ts=base_ts,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 96_528.0},
        ],
    )
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="B.xlsx", fiscal_year=2024, ts=base_ts + timedelta(minutes=1),
        fields=[
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_737_410.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    assert base["fb_revenue_per_occupied_room"] == pytest.approx(96_528.0 / (0.80 * 132 * 365))
    detail = base["__reasons__"]["fb_revenue_per_occupied_room"]["detail"]
    assert "uses A.xlsx" in detail and "B.xlsx" in detail


@pytest.mark.asyncio
async def test_partial_period_statements_never_share_an_invented_period() -> None:
    """2e1db521: March and June 2025 monthly financials carry no period end
    (the year-end would be inferred). They are NOT one period: no conflict
    notice, no invented "period ending" date; each only fills, as a
    different period, a line the full-year primary lacks."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="2e1db521", keys=132, purchase=36_400_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="FY2024 Detailed.xlsx", fiscal_year=2024, ts=base_ts,
        fields=[
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_000_000.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 300_000.0},
        ],
    )
    for i, (name, fb, ins) in enumerate(
        [("March 2025 Financials.xlsx", 3_216_620.0, 1_000_000.0), ("Jun 2025 Financials.xlsx", 3_514_040.0, 1_200_000.0)]
    ):
        await _insert_financial_extraction(
            deal_id, doc_type="PNL_MONTHLY", filename=name, fiscal_year=2025,
            ts=base_ts + timedelta(minutes=1 + i),
            fields=[
                {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_332_100.0 + i},
                {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": fb},
                {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": ins},
            ],
        )
    base, _resp = await _base_and_sources(deal_id)
    for key, r in base["__reasons__"].items():
        detail = (r.get("detail") or "") if isinstance(r, dict) else ""
        assert "disagree" not in detail, (key, detail)
        assert "2025-12-31" not in detail, (key, detail)
    # The monthly statements are never annualised for a fill: insurance is
    # the documented default, and the note says why.
    assert "insurance" not in base["t12_expense_actuals"]
    ins = base["__reasons__"]["insurance"]["detail"]
    assert "never annualised" in ins and "FY2024 Detailed.xlsx" in ins


@pytest.mark.asyncio
async def test_the_ledger_document_link_follows_the_statement_the_model_uses() -> None:
    """I4: ``source_documents`` (the ledger's link icon) names the same
    statement as ``source_fields`` — on b67bfdee the Detailed P&L, not the
    Summary CSV the legacy period-type ranking picked."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler FY2024", keys=132, purchase=36_400_000)
    detailed, _summary = await _insert_angler_fy2024_pair(deal_id)
    _base, resp = await _base_and_sources(deal_id)
    for key in ("starting_occupancy", "starting_adr"):
        assert resp.source_fields[key]["document_id"] == str(detailed)
        assert resp.source_documents[key] == str(detailed), key


def test_a_loader_notice_keeps_an_existing_reason() -> None:
    """M2: a key that already carries a reason (an as-of caveat) keeps it
    alongside the review notice — nothing is silently dropped."""
    from fondok_schemas.reasons import ReasonCode

    from app.services.engine_runner import (
        SourceField,
        _loader_notices,
        _reason,
        _StatementConflict,
    )
    from app.ontology.registry import Resolution

    sf = SourceField(
        resolution=Resolution(
            concept="occupancy", value=0.8, field_name="occupancy_pct", source_page=1,
            unit=None, confidence=None, reviewed=None, doc_type="PNL", scope="annual",
            basis="actual", reason=None, candidates=(),
        ),
        document_id="doc-a",
    )
    earlier = _reason(ReasonCode.AS_OF_UNKNOWN, "No reporting date.")
    out = _loader_notices(
        {"starting_occupancy": sf},
        conflicts=[_StatementConflict(document_id="doc-a", detail="A vs B disagree.")],
        fills=[],
        other_ratio_capped_detail=None,
        already={"starting_occupancy": earlier},
    )
    entry = out["starting_occupancy"]
    assert entry["code"] is ReasonCode.NEEDS_REVIEW
    assert "A vs B disagree." in entry["detail"]
    assert entry["also"] == [earlier]


# ═══════ W5 round 3 — single-statement footing, fill order, unverifiable ═══════


def _ttm_monthly_block(line: str, value: float) -> list[dict[str, object]]:
    """Twelve monthly columns of one line — the shape of the sibling-template
    "March 2025 Financials" T-12 whose full-period lines are trailing totals."""
    months = ["apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "jan", "feb", "mar"]
    return [
        {"field_name": f"p_and_l_usali.monthly.{m}_2024.{line}", "value": round(value / 12, 2)}
        for m in months
    ]


@pytest.mark.asyncio
async def test_a_single_primary_that_cannot_foot_carries_a_review_note() -> None:
    """R2-I1 (2e1db521): one FY2024 statement, F&B $96,528 and no full-period
    rooms line (only prior-year rooms). No sibling to compare with — the
    footing check still runs on it, and the revenue-derived assumptions it
    grounds say why they need review. No threshold is invented."""
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="2e1db521", keys=132, purchase=36_400_000)
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename=_DETAILED_PNL, fiscal_year=2024,
        ts=datetime(2026, 9, 20, tzinfo=UTC),
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
            {"field_name": "occupancy_pct", "value": 0.832567},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": "$10M"},
            {"field_name": "p_and_l_usali.prior_year.rooms_revenue", "value": 9_807_990.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 96_528.1},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 10_839_200.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    note = base["__reasons__"]["fb_revenue_per_occupied_room"]
    assert note["code"] is ReasonCode.NEEDS_REVIEW
    assert _DETAILED_PNL in note["detail"]
    assert "no full-period rooms revenue line" in note["detail"]
    assert "$10,839,200" in note["detail"]
    assert note["detail"].count(_DETAILED_PNL) == 1


@pytest.mark.asyncio
async def test_a_primary_whose_lines_miss_its_total_carries_a_review_note() -> None:
    """The computed miss is named with both figures."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Miss", keys=132, purchase=36_400_000)
    await _insert_financial_extraction(
        deal_id, doc_type="T12", filename="T12.xlsx", ts=datetime(2026, 9, 20, tzinfo=UTC),
        fields=[
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 1_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 13_000_000.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    detail = base["__reasons__"]["fb_revenue_per_occupied_room"]["detail"]
    assert "$10,000,000" in detail and "$13,000,000" in detail


@pytest.mark.asyncio
async def test_a_footing_primary_and_an_unverifiable_one_carry_no_footing_note() -> None:
    """A statement that foots, or that states no total (unverifiable — the
    absence of a check, not a failure), carries no footing note."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Foots", keys=132, purchase=36_400_000)
    await _insert_financial_extraction(
        deal_id, doc_type="T12", filename="T12.xlsx", ts=datetime(2026, 9, 20, tzinfo=UTC),
        fields=[
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_000_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 1_000_000.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    assert "fb_revenue_per_occupied_room" not in base["__reasons__"]


@pytest.mark.asyncio
async def test_an_unverifiable_primary_never_loses_to_a_footing_sibling() -> None:
    """Concern 3: a complete statement whose total line was not extracted is
    unverifiable, not failing — it keeps the period over a summary that
    foots."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Unverifiable", keys=132, purchase=36_400_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    detailed, _ = await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="Detailed.xlsx", fiscal_year=2024, ts=base_ts,
        fields=[
            {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_500_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 2_700_000.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 300_000.0},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_400_000.0},
        ],
    )
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="Summary.csv", fiscal_year=2024, ts=base_ts + timedelta(minutes=1),
        fields=[
            {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_900_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue", "value": 1_300_000.0},
            {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 10_200_000.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    assert base["__source_fields__"]["fb_revenue_per_occupied_room"]["document_id"] == str(detailed)


@pytest.mark.asyncio
async def test_fill_takes_the_most_recent_twelve_month_line_within_two_years() -> None:
    """Fill-order rule: a line the primary lacks comes from the same period
    first, then from the MOST RECENT statement whose line covers twelve
    months — a trailing-twelve counts, however the document is typed — never
    from a monthly / YTD slice, and never from a statement more than two years
    older than the Year-1 basis (then the default, with a notice).

    The three prod deals (2e1db521, 76d10a64, 3b4e50d9 shape) took FY2019
    insurance; the March 2025 trailing-twelve now supplies it."""
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Fill Order", keys=132, purchase=36_400_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="FY2024 Detailed.xlsx", fiscal_year=2024, ts=base_ts,
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
            {"field_name": "occupancy_pct", "value": 0.80},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_500_000.0},
            {"field_name": "p_and_l_usali.undistributed.sales_marketing", "value": 700_000.0},
        ],
    )
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="FY2019 P&L.xlsx", fiscal_year=2019, ts=base_ts + timedelta(minutes=1),
        fields=[
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 662_426.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 160_000.0},
        ],
    )
    ttm, _ = await _insert_financial_extraction(
        deal_id, doc_type="PNL_MONTHLY", filename="March 2025 Financials.xlsx", fiscal_year=2025,
        ts=base_ts + timedelta(minutes=2),
        fields=[
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 1_429_570.0},
            *_ttm_monthly_block("insurance", 1_429_570.0),
        ],
    )
    await _insert_financial_extraction(
        deal_id, doc_type="PNL_MONTHLY", filename="April 2025 month.xlsx", fiscal_year=2025,
        ts=base_ts + timedelta(minutes=3),
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "monthly"},
            {"field_name": "p_and_l_usali.fixed_charges.insurance", "value": 119_000.0},
            {"field_name": "p_and_l_usali.undistributed.utilities", "value": 24_000.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    exp = base["t12_expense_actuals"]
    reasons = base["__reasons__"]

    assert exp["insurance"] == pytest.approx(1_429_570.0)
    assert base["__source_fields__"]["insurance"]["document_id"] == str(ttm)
    assert "March 2025 Financials.xlsx" in reasons["insurance"]["detail"]

    # Utilities: only FY2019 (more than two years older) and a monthly slice
    # carry it — neither fills; the default is used, and the analyst is told.
    assert "utilities" not in exp
    note = reasons["utilities"]
    assert note["code"] is ReasonCode.NEEDS_REVIEW
    assert "FY2019 P&L.xlsx" in note["detail"] and "two years" in note["detail"]
    assert "April 2025 month.xlsx" not in note["detail"] or "monthly" in note["detail"]


@pytest.mark.asyncio
async def test_a_year_only_statement_groups_with_a_calendar_one_only_when_the_year_end_is_known() -> None:
    """R2-M2: FY2024 known only by its year is the same period as a statement
    ending 2024-12-31 only when the deal's dated annual statements show a
    December year-end. Here another annual ends 2023-06-30, so it is not
    known — no same-period conflict is claimed."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="Non-calendar", keys=132, purchase=36_400_000)
    base_ts = datetime(2026, 9, 20, tzinfo=UTC)
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename=_DETAILED_PNL, fiscal_year=2024, ts=base_ts,
        fields=_detailed_pnl_fields(),
    )
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename=_SUMMARY_CSV, ts=base_ts + timedelta(minutes=1),
        fields=_summary_csv_fields(),
    )
    await _insert_financial_extraction(
        deal_id, doc_type="PNL", filename="FY2023 June.xlsx", ts=base_ts + timedelta(minutes=2),
        fields=[
            {"field_name": "p_and_l_usali.period_type", "value": "annual"},
            {"field_name": "p_and_l_usali.period_ending", "value": "2023-06-30"},
            {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_000_000.0},
        ],
    )
    base, _resp = await _base_and_sources(deal_id)
    for key, r in base["__reasons__"].items():
        detail = (r.get("detail") or "") if isinstance(r, dict) else ""
        assert "disagree" not in detail, (key, detail)


@pytest.mark.asyncio
async def test_no_document_is_named_twice_in_one_notice() -> None:
    """R2-M3 on 7a9928e0: the conflict note names each statement once."""
    deal_id = uuid4()
    await _insert_deal(deal_id, name="7a9928e0", keys=132, purchase=36_400_000)
    await _insert_7a9928e0_pair(deal_id)
    base, _resp = await _base_and_sources(deal_id)
    for key, r in base["__reasons__"].items():
        detail = (r.get("detail") or "") if isinstance(r, dict) else ""
        for name in (_T12_MISREAD, _DEC_2024):
            assert detail.count(name) <= 1, (key, detail)


@pytest.mark.asyncio
async def test_the_ledger_keeps_an_earlier_reason_code_visible() -> None:
    """The wire carries ``needs_review`` AND the code it joined."""
    from app.api.deals import get_assumption_sources
    from app.database import get_session_factory
    from app.services import engine_runner
    from fondok_schemas.reasons import ReasonCode

    deal_id = uuid4()
    await _insert_deal(deal_id, name="Angler FY2024", keys=132, purchase=36_400_000)
    await _insert_angler_fy2024_pair(deal_id)
    real = engine_runner._load_engine_inputs

    async def _with_earlier(*args, **kwargs):
        base = await real(*args, **kwargs)
        entry = base["__reasons__"]["starting_occupancy"]
        entry["also"] = [{"code": ReasonCode.AS_OF_UNKNOWN, "detail": "No date."}]
        return base

    engine_runner._load_engine_inputs = _with_earlier
    try:
        factory = get_session_factory()
        async with factory() as session:
            resp = await get_assumption_sources(deal_id, session, UUID(_TENANT))
    finally:
        engine_runner._load_engine_inputs = real
    assert resp.reasons["starting_occupancy"] == "needs_review"
    assert resp.reasons_also["starting_occupancy"] == ["as_of_unknown"]
