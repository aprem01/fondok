"""FON-122 follow-up (Sam, 2026-11-26) — the entry NOI carries no penetration ramp.

Sam's QA on the Kimpton deal (price $36.4M): the "undisplaced" Year 1 the entry cap
was struck on still carried the penetration RAMP — Year 1 ADR $263 against a T-12
of $233 — so the cap stripped the renovation's disruption and kept its upside.
The entry basis is now Year 1 AT the T-12's own occupancy and ADR (the index held
at today's level, no movement toward the stabilized target), no closure, no
displacement, Year 1 expense structure, before the FF&E reserve. The projection's
own ``years[]`` never move with it, and a deal whose Year 1 the topline does not
move (the stock Kimpton fixture, every growth-only deal) publishes exactly what it
did before.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

#: The benchmark topline (the ramp exists only there); the deployed growth-only
#: default has no penetration and is pinned unchanged in ``test_opening_closure``.
pytestmark = pytest.mark.usefixtures("topline_benchmark")

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-entry-noi-t12-index.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.underwriting import RevenueEngineInput, ToplineReference  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.engines.expense import ExpenseEngine  # noqa: E402
from app.engines.fb_revenue import FBRevenueEngine  # noqa: E402
from app.engines.revenue import RevenueEngine  # noqa: E402
from app.export.labels import ENTRY_NOI_BASIS  # noqa: E402
from app.services.engine_runner import (  # noqa: E402
    _UNDISPLACED_REVENUE,
    _build_input_for,
    _entry_topline,
    _load_engine_inputs,
    _year_one_penetration_moves,
)
from app.services.topline import build_topline, read_topline_inputs  # noqa: E402
from app.services.topline_benchmark import BenchmarkResolution  # noqa: E402
from app.services.topline_growth import resolve_growth_by_year  # noqa: E402
from app.tenants import TenantDataPolicies  # noqa: E402
from tests.fixtures.deal_calendar import calendar_deal_years  # noqa: E402
from tests.fixtures.kimpton_docs_fixture import seed_kimpton_docs_deal  # noqa: E402

# The Kimpton Angler's STR comp set, TTM May 2025 (MPI 109.8, ARI 75.1) and the
# T-12 behind it (``test_topline_ramp_mode``).
STR = {"level": "comp_set", "label": "STR comp set", "occupancy": 0.755, "adr": 310.78,
       "base_calendar_year": 2026, "basis": "actual", "published_mpi": 109.8, "published_ari": 75.1}
T12 = ToplineReference(occupancy=0.8326, adr=236.09)
QUARTER_OFF_SIX_MONTHS = [{"start_month": 1, "end_month": 6, "rooms_offline_pct": 0.25, "rate_discount_pct": 0.0}]
INDEX_TARGET_KEYS = ("index_mpi_target", "index_ari_target", "index_mpi_target_by_year", "index_ari_target_by_year")


def _drivers(base: dict | None = None, *, applied: set[str] | None = None, t12=T12):
    base = {"acquisition_close_date": "2025-12-31", **(base or {})}
    inputs = read_topline_inputs(base, {}, applied or set())
    g = resolve_growth_by_year([], inputs.growth, deal_years=calendar_deal_years([2026, 2027, 2028, 2029, 2030]),
                               hold_years=5, class_bucket="all", policy=TenantDataPolicies())
    return build_topline(BenchmarkResolution(**STR), g, inputs, hold_years=5, t12=t12)


def _year_one(build, topline):
    out = RevenueEngine().run(RevenueEngineInput(
        deal_id="7b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a99", keys=100, starting_occupancy=0.7,
        starting_adr=300.0, hold_years=5, acquisition_close_date="2025-12-31",
        occupancy_growth=0.0, adr_growth=0.0,
        occupancy_growth_by_year=build.occupancy_growth_by_year,
        adr_growth_by_year=build.adr_growth_by_year, topline=topline,
    ))
    return out.years[0], {r.year: r for r in out.topline_by_year}[1]


def test_the_sub_line_is_the_one_basis_string() -> None:
    from app.export.memo_pdf import ENTRY_CAP_DEFINITION, ENTRY_CAP_LABEL

    assert ENTRY_NOI_BASIS == "Year 1 at T-12 occupancy and ADR · no displacement · before FF&E reserve"
    assert ENTRY_CAP_DEFINITION is ENTRY_NOI_BASIS
    assert ENTRY_CAP_LABEL == "Entry Cap (Year 1, undisplaced)"


def test_the_straight_line_moves_year_1_and_the_entry_topline_holds_todays_index() -> None:
    """Sam's QA shape: an operating hotel on the default straight line. Year 1 ADR
    runs a third of the way from ARI 75.1 to 100 ($259 — "$263 against a T-12 of
    $233" on the deal); the entry run holds 75.1, so Year 1 ADR is the T-12's own
    against the comp set, and occupancy the T-12's own MPI 109.8."""
    build = _drivers()
    topline = build.drivers
    assert _year_one_penetration_moves(topline, 5) == {"occupancy": pytest.approx(109.8), "adr": pytest.approx(75.1)}
    ramped, rec = _year_one(build, topline)
    assert ramped.adr == pytest.approx(259.2, abs=0.1) and rec.ari_target == pytest.approx(75.1 + 24.9 / 3)
    held_topline = _entry_topline(topline, 5)
    assert held_topline is not None
    held, rec_held = _year_one(build, held_topline)
    assert rec_held.ari_target == pytest.approx(75.1) and rec_held.mpi_target == pytest.approx(109.8)
    assert held.adr == pytest.approx(310.78 * 0.751, abs=0.05)  # $233.4 — the T-12's ADR against the comp set
    assert held.occupancy == pytest.approx(0.755 * 1.098, abs=1e-4)
    assert held.adr < ramped.adr
    # Year 1 only: the deal's later years are not the entry run's business.
    assert held_topline.penetration_adr_by_year[1:] == list(topline.penetration_adr_by_year[1:])
    assert held_topline.ramp_by_year == topline.ramp_by_year and held_topline.penetration_adr == topline.penetration_adr


def test_an_analysts_ramp_or_by_year_target_is_movement_too_and_is_held() -> None:
    ramp = _drivers({"penetration_ramp_by_year": [0.9, 0.95]}, applied={"penetration_ramp_by_year"})
    assert _year_one_penetration_moves(ramp.drivers, 5) == {"occupancy": pytest.approx(109.8), "adr": pytest.approx(75.1)}
    _, rec = _year_one(ramp, _entry_topline(ramp.drivers, 5))
    assert rec.ari_target == pytest.approx(75.1) and rec.mpi_target == pytest.approx(109.8)
    plan = _drivers(
        {"index_mpi_target_by_year": [100, 100, 100, 100, 100], "index_ari_target_by_year": [97, 98, 99, 100, 100]},
        applied={"index_mpi_target_by_year", "index_ari_target_by_year"},
    )
    held = _entry_topline(plan.drivers, 5)
    assert held is not None
    _, rec = _year_one(plan, held)
    assert rec.ari_target == pytest.approx(75.1) and rec.mpi_target == pytest.approx(109.8)
    # Years 2+ of the business plan are untouched.
    assert held.penetration_adr_by_year[1:] == [98, 99, 100, 100]


def test_a_deal_whose_year_1_already_runs_at_todays_index_needs_no_entry_run() -> None:
    """No topline (growth-only, the stock Kimpton fixture), no index (no T-12 or a new
    hotel), or a Year 1 already at today's index: nothing to hold, ``None`` — so
    ``run_rate_noi_at_entry`` is what it was."""
    assert _entry_topline(None, 5) is None
    no_t12 = _drivers(t12=None)
    bench = {**STR, "published_mpi": None, "published_ari": None}
    inputs = read_topline_inputs({"acquisition_close_date": "2025-12-31"}, {}, set())
    g = resolve_growth_by_year([], inputs.growth, deal_years=calendar_deal_years([2026, 2027, 2028, 2029, 2030]),
                               hold_years=5, class_bucket="all", policy=TenantDataPolicies())
    derived_none = build_topline(BenchmarkResolution(**bench), g, inputs, hold_years=5, t12=None)
    assert derived_none.drivers.start_index_occ is None and _entry_topline(derived_none.drivers, 5) is None
    assert no_t12.drivers.start_index_occ == pytest.approx(109.8)  # the published index needs no T-12
    # A Year 1 held at today's index by the analyst (targets = today's index) moves nothing.
    flat = _drivers({"index_mpi_target": 109.8, "index_ari_target": 75.1}, applied={"index_mpi_target", "index_ari_target"})
    assert _year_one_penetration_moves(flat.drivers, 5) == {}
    assert _entry_topline(flat.drivers, 5) is None
    # A new or reopened hotel has no today's index: its 85 % lease-up year is left as it runs.
    reopened = _drivers({"reopening_date": "2026-03-01"})
    assert reopened.drivers.ramp_mode == "new_or_reopened" and reopened.drivers.start_index_occ is None
    assert _entry_topline(reopened.drivers, 5) is None


async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache

    await run_startup_migrations()
    reset_tenant_cache()
    async with get_session_factory()() as session:
        for tbl in ("engine_outputs", "extraction_results", "documents", "deals", "tenants"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()


async def _model(**patch_overrides: object) -> dict:
    """The Kimpton DOCUMENTS fixture (T-12 to May 2025: 82.89 % / $233.45, the STR comp
    set and the CoStar forecast) through the loader and the first three engines, with
    the reference index targets removed so the default straight line runs."""
    from app.database import get_session_factory

    await _reset_db()
    async with get_session_factory()() as s:
        deal_id, tenant_id = await seed_kimpton_docs_deal(s)
        row = (await s.execute(text("SELECT field_overrides FROM deals WHERE id = :id"), {"id": deal_id})).first()
        ov = json.loads(row[0])
        for k in INDEX_TARGET_KEYS:
            ov.pop(k, None)
        for k, v in patch_overrides.items():
            if v is None:
                ov.pop(k, None)
            else:
                ov[k] = v
        await s.execute(text("UPDATE deals SET field_overrides = :ov WHERE id = :id"), {"ov": json.dumps(ov), "id": deal_id})
        await s.commit()
    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, deal_id, tenant_id=tenant_id)
    acc: dict = {"deal_id": deal_id, "base": base}
    for name, engine in (("revenue", RevenueEngine), ("fb", FBRevenueEngine), ("expense", ExpenseEngine)):
        payload = _build_input_for(name, deal_id, base, acc)
        acc[f"{name}_input"] = payload
        acc[name] = engine().run(payload)
    return acc


def _year_one_noi_on(acc: dict, revenue_update: dict) -> tuple[float, object]:
    """Year 1 NOI before reserve priced exactly as the engine's entry pass prices it."""
    revenue = RevenueEngine().run(acc["revenue_input"].model_copy(update=revenue_update))
    fb = FBRevenueEngine().run(_build_input_for("fb", acc["deal_id"], acc["base"], {"revenue": revenue}))
    expense = ExpenseEngine().run(acc["expense_input"].model_copy(update={
        "revenue": fb, "entry_revenue": None, "seed_year_one": None,
        "open_shares_by_year": {}, "actual_revenue": None, "actual_occupancy_by_year": None,
    }))
    return expense.years[0].noi_institutional, revenue.years[0]


@pytest.mark.asyncio
async def test_sams_scenario_the_entry_noi_is_year_1_at_the_t12_index_with_no_displacement() -> None:
    """Sam's exact scenario on the documents fixture (price $36.4M): a quarter of the
    rooms off for six months on top of the default straight-line ramp.

    (a) displacement on — the projected Year 1;
    (b) displacement off, ramp kept — what the entry cap WAS struck on;
    (c) displacement off AND the index held at the T-12's — what it is struck on now.
    The published ``run_rate_noi_at_entry`` is (c), below (b) by the ramp's Year-1
    upside; Year 1 occupancy IS the T-12's 82.89 % and ADR the T-12's $233.45 at the
    year's −2.0 % market step; and ``years[]`` are untouched."""
    acc = await _model(renovation_displacement_schedule=QUARTER_OFF_SIX_MONTHS)
    topline = acc["revenue_input"].topline
    t12 = topline.t12_reference
    assert t12.occupancy == pytest.approx(0.8289, abs=1e-4) and t12.adr == pytest.approx(233.45, abs=0.01)
    assert topline.start_index_adr == pytest.approx(75.12, abs=0.01)
    price = float(acc["base"]["purchase_price"])
    assert price == 36_400_000.0

    a = acc["expense"].years[0].noi_institutional
    b, y1_b = _year_one_noi_on(acc, _UNDISPLACED_REVENUE)
    held = _entry_topline(topline, acc["revenue_input"].hold_years)
    assert held is not None
    c, y1_c = _year_one_noi_on(acc, {**_UNDISPLACED_REVENUE, "topline": held})

    assert a < c < b
    assert acc["expense"].run_rate_noi_at_entry == pytest.approx(c)
    assert acc["expense"].run_rate_noi_at_entry != pytest.approx(b)
    # The ramp's upside the old entry NOI carried: Year 1 ADR a third of the way to parity.
    assert y1_b.adr == pytest.approx(254.04, abs=0.05) and y1_b.adr > t12.adr
    # The entry basis: the T-12's own occupancy and ADR (at the year's market step).
    assert y1_c.occupancy == pytest.approx(t12.occupancy, abs=1e-4)
    assert y1_c.adr == pytest.approx(t12.adr * (1 - 0.02), abs=0.05)
    assert acc["revenue"].years[0].adr == pytest.approx(y1_b.adr)  # the projection keeps its ramp
    assert acc["revenue"].years[0].occupancy < y1_b.occupancy  # ...and its displacement
    # The figures Sam will see (pinned so a silent drift is loud): $2.80M / 7.70 %, not $3.06M / 8.41 %.
    assert c == pytest.approx(2_802_953, abs=1.0) and c / price == pytest.approx(0.0770, abs=5e-4)
    assert b == pytest.approx(3_061_325, abs=1.0) and b / price == pytest.approx(0.0841, abs=5e-4)
    assert a == pytest.approx(2_678_659, abs=1.0)

    # ``years[]`` are the displaced deal's own: identical with the entry pass off.
    plain = ExpenseEngine().run(acc["expense_input"].model_copy(update={"entry_revenue": None}))
    assert [y.noi for y in acc["expense"].years] == [y.noi for y in plain.years]
    assert [y.noi_institutional for y in acc["expense"].years] == [y.noi_institutional for y in plain.years]
    # ...and the seed the later years grow from is the undisplaced Year 1 WITH its ramp, not the entry basis.
    assert acc["expense_input"].seed_year_one is not None
    assert acc["expense_input"].seed_year_one.total_revenue == pytest.approx(
        FBRevenueEngine().run(_build_input_for("fb", acc["deal_id"], acc["base"], {
            "revenue": RevenueEngine().run(acc["revenue_input"].model_copy(update=_UNDISPLACED_REVENUE))
        })).years[0].total_revenue
    )
    assert acc["expense_input"].entry_revenue.years[0].total_revenue < acc["expense_input"].seed_year_one.total_revenue


@pytest.mark.asyncio
async def test_a_ramped_deal_with_no_displacement_strikes_the_same_entry_basis() -> None:
    """The basis is one rule: a deal the ramp alone lifts gets the held-index entry
    NOI too, while its projected Year 1 keeps the ramp."""
    acc = await _model()
    assert acc["revenue"].years[0].occupancy_displacement_factor == 1.0
    assert acc["expense_input"].entry_revenue is not None and acc["expense_input"].seed_year_one is None
    held = _entry_topline(acc["revenue_input"].topline, acc["revenue_input"].hold_years)
    c, _ = _year_one_noi_on(acc, {**_UNDISPLACED_REVENUE, "topline": held})
    assert acc["expense"].run_rate_noi_at_entry == pytest.approx(c)
    assert acc["expense"].run_rate_noi_at_entry < acc["expense"].years[0].noi_institutional
    displaced = await _model(renovation_displacement_schedule=QUARTER_OFF_SIX_MONTHS)
    assert displaced["expense"].run_rate_noi_at_entry == pytest.approx(acc["expense"].run_rate_noi_at_entry)


@pytest.mark.asyncio
async def test_the_reference_business_plan_is_held_too_and_years_keep_it() -> None:
    """The fixture's own analyst targets (ARI 97 / 98 / 99 / 100): Year 1's 97 is
    movement toward the plan, so the entry run holds today's 75.1; Years 2+ of the
    plan and the projection's own Year 1 are untouched."""
    from tests.fixtures.kimpton_docs_fixture import KIMPTON_REFERENCE_SCENARIO

    acc = await _model(**{k: KIMPTON_REFERENCE_SCENARIO[k] for k in INDEX_TARGET_KEYS})
    topline = acc["revenue_input"].topline
    assert topline.penetration_adr_by_year[0] == 97
    held = _entry_topline(topline, 5)
    assert held.penetration_adr_by_year[0] == pytest.approx(75.12, abs=0.01)
    assert held.penetration_adr_by_year[1:] == [98, 99, 100]
    rec = {r.year: r for r in acc["revenue"].topline_by_year}[1]
    assert rec.ari_target == 97  # the projection runs the plan
    assert acc["expense"].run_rate_noi_at_entry < acc["expense"].years[0].noi_institutional
