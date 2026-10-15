"""FON-87 — the revenue engine traces ``years[i].occupancy`` / ``.adr`` /
``.revpar`` (and the ``occupied_rooms`` hop between them and rooms revenue).

The Future P&L lineage drawer asks for exactly these paths
(``ProjectionsSection.lineageRequestFor`` → ``engine:revenue.years[i].adr``)
and said "No trace available" because the revenue provenance only traced the
revenue LINES. These lock, for both the legacy growth path and the Index
Analysis driver path:

  1. every projected year carries the four traces, and each trace's value is
     the output figure it describes;
  2. each trace's named inputs reproduce its value through its formula —
     the starting value × displacement, the anchor compounded at the growth
     assumption, or benchmark × target ÷ 100 — so the rationale is
     arithmetically honest, never decorative;
  3. the inputs assert the assumption keys / ``traces_to`` pointers the
     lineage walk links on, and every pointer resolves;
  4. the engine's OUTPUT is unchanged — the sidecar is separate from the
     figures (the goldens read the figures).
  5. on a persisted run the lineage graph carries the ``engine:revenue.
     years[i].adr`` / ``.occupancy`` nodes the drawer looks up, and they walk
     to the starting assumptions.
"""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-fon87-rate-traces.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

from fondok_schemas.underwriting import (  # noqa: E402
    IndexDriverYear,
    PIPDisplacement,
    RevenueEngineInput,
)

from app.engines.revenue import DAYS_PER_YEAR, RevenueEngine  # noqa: E402

TOL = 1e-9
RATE_PATHS = ("occupancy", "adr", "revpar", "occupied_rooms")


def _input(**kw: Any) -> RevenueEngineInput:
    base: dict[str, Any] = dict(
        deal_id=UUID("22222222-2222-2222-2222-222222222222"),
        keys=132,
        starting_occupancy=0.712,
        starting_adr=287.0,
        occupancy_growth=0.01,
        adr_growth=0.03,
        fb_revenue_per_occupied_room=40.0,
        other_revenue_pct_of_rooms=0.05,
        hold_years=5,
    )
    base.update(kw)
    return RevenueEngineInput(**base)


def _by_name(trace) -> dict[str, Any]:
    return {inp.name: inp for inp in trace.inputs}


def _reconcile(trace, expected: float) -> None:
    assert math.isclose(trace.value, expected, rel_tol=TOL, abs_tol=TOL), (trace.formula, trace.value, expected)


# ─────────────────────────── 1 + 4. shape and byte-identity ───────────────────


def test_every_year_traces_the_rate_lines_with_the_output_figures() -> None:
    out = RevenueEngine().run(_input())
    for i, yr in enumerate(out.years):
        for path in RATE_PATHS:
            assert f"years[{i}].{path}" in out.provenance, path
        assert out.provenance[f"years[{i}].occupancy"].value == yr.occupancy
        assert out.provenance[f"years[{i}].adr"].value == yr.adr
        assert out.provenance[f"years[{i}].revpar"].value == yr.revpar
        assert out.provenance[f"years[{i}].occupied_rooms"].value == pytest.approx(
            132 * DAYS_PER_YEAR * yr.occupancy
        )
        # Tagged like every other trace (FON-65 state vocabulary).
        assert out.provenance[f"years[{i}].adr"].state == "calculated"


def test_output_figures_are_untouched_by_the_sidecar() -> None:
    """The sidecar is separate from the outputs: the years serialize to the
    same bytes whether or not provenance is read. (The goldens read years.)"""
    a = RevenueEngine().run(_input())
    b = RevenueEngine().run(_input())
    assert [y.model_dump_json() for y in a.years] == [y.model_dump_json() for y in b.years]
    # And the rate figures still equal the arithmetic the engine has always done.
    assert a.years[0].occupancy == 0.712 and a.years[0].adr == 287.0
    assert a.years[1].occupancy == pytest.approx(min(0.95, 0.712 * 1.01))
    assert a.years[1].adr == pytest.approx(287.0 * 1.03)


# ─────────────────────────── 2 + 3. legacy growth path ─────────────────────────


def test_legacy_year_one_names_the_starting_assumption_and_the_displacement() -> None:
    out = RevenueEngine().run(
        _input(y1_occupancy_displacement_pct=0.15, y1_adr_displacement_pct=0.08)
    )
    occ = out.provenance["years[0].occupancy"]
    assert occ.formula == "occupancy = starting_occupancy × (1 − y1_occupancy_displacement_pct)"
    by = _by_name(occ)
    assert by["starting_occupancy"].assumption_key == "starting_occupancy"
    assert by["starting_occupancy"].value == 0.712
    assert by["y1_occupancy_displacement_pct"].assumption_key == "y1_occupancy_displacement_pct"
    _reconcile(occ, 0.712 * (1 - 0.15))

    adr = out.provenance["years[0].adr"]
    assert adr.formula == "adr = starting_adr × (1 − y1_adr_displacement_pct)"
    _reconcile(adr, 287.0 * (1 - 0.08))
    assert _by_name(adr)["starting_adr"].assumption_key == "starting_adr"


def test_legacy_year_one_without_displacement_is_the_bare_assumption() -> None:
    out = RevenueEngine().run(_input())
    occ = out.provenance["years[0].occupancy"]
    assert occ.formula == "occupancy = starting_occupancy"
    assert [i.name for i in occ.inputs] == ["starting_occupancy"]
    assert "No Year-1 displacement" in (occ.note or "")


def test_legacy_out_years_compound_the_undisplaced_baseline_at_the_growth_assumption() -> None:
    out = RevenueEngine().run(_input(y1_occupancy_displacement_pct=0.15, y1_adr_displacement_pct=0.08))
    for i in range(1, 5):
        y = i + 1
        occ = out.provenance[f"years[{i}].occupancy"]
        assert occ.formula == "occupancy = min(0.95, starting_occupancy × (1 + occupancy_growth) ^ years_compounded)"
        by = _by_name(occ)
        assert by["occupancy_growth"].assumption_key == "occupancy_growth"
        assert by["years_compounded"].value == y - 1
        # The displacement never cascades: the input is the un-displaced start.
        assert "y1_occupancy_displacement_pct" not in by
        _reconcile(occ, min(0.95, by["starting_occupancy"].value * (1 + by["occupancy_growth"].value) ** (y - 1)))

        adr = out.provenance[f"years[{i}].adr"]
        assert adr.formula == "adr = starting_adr × (1 + adr_growth) ^ years_compounded"
        aby = _by_name(adr)
        _reconcile(adr, aby["starting_adr"].value * (1 + aby["adr_growth"].value) ** (y - 1))


def test_revpar_and_occupied_rooms_chain_to_rooms_revenue_and_occupancy() -> None:
    out = RevenueEngine().run(_input())
    for i, yr in enumerate(out.years):
        rp = out.provenance[f"years[{i}].revpar"]
        assert rp.formula == "revpar = rooms_revenue ÷ rooms_available"
        by = _by_name(rp)
        assert by["rooms_revenue"].traces_to == f"years[{i}].rooms_revenue"
        assert by["rooms_available"].assumption_key == "keys"
        _reconcile(rp, by["rooms_revenue"].value / by["rooms_available"].value)
        assert rp.value == pytest.approx(yr.occupancy * yr.adr)

        occd = out.provenance[f"years[{i}].occupied_rooms"]
        oby = _by_name(occd)
        assert oby["keys"].assumption_key == "keys"
        assert oby["occupancy"].traces_to == f"years[{i}].occupancy"
        _reconcile(occd, oby["keys"].value * oby["days"].value * oby["occupancy"].value)

        # rooms_revenue now points at both factors' own traces.
        rooms = _by_name(out.provenance[f"years[{i}].rooms_revenue"])
        assert rooms["occupied_rooms"].traces_to == f"years[{i}].occupied_rooms"
        assert rooms["adr"].traces_to == f"years[{i}].adr"
        # …while still asserting the Year-1 assumption keys the walk links.
        assert rooms["occupied_rooms"].assumption_key == "starting_occupancy"
        assert rooms["adr"].assumption_key == "starting_adr"


def test_no_dangling_pointers_on_either_path() -> None:
    for payload in (_input(), _index_input()):
        out = RevenueEngine().run(payload)
        keys = set(out.provenance)
        for path, trace in out.provenance.items():
            for inp in trace.inputs:
                if inp.traces_to is not None:
                    assert inp.traces_to in keys, (path, inp.traces_to)


# ─────────────────────────── 2 + 3. Index Analysis driver path ─────────────────


def _index_input() -> RevenueEngineInput:
    """Year 1 ADR driven (scalar ARI), Year 3 occupancy driven (by-year MPI),
    Year 4 ADR driven (by-year ARI) — the shape ``test_index_targets_by_year``
    runs. Driver values are benchmark × target ÷ 100 exactly."""
    return _input(
        occupancy_by_year_override=[None, None, 0.72 * 111.1 / 100.0, None, None],
        adr_by_year_override=[210.0 * 100.0 / 100.0, None, None, 250.0 * 104.0 / 100.0, None],
        index_drivers_by_year=[
            IndexDriverYear(
                year=1, benchmark_adr=210.0, ari_target=100.0, ari_target_source="scalar",
                benchmark_adr_source="STR comp-set TTM", benchmark_doc="STR Trend May 2025.xlsx",
                benchmark_page=3, adr_status="driven",
            ),
            IndexDriverYear(
                year=3, benchmark_occupancy=0.72, mpi_target=111.1, mpi_target_source="by_year",
                benchmark_occupancy_source="CBRE Horizons 2027 forecast",
                benchmark_doc="Miami Horizons Q3 2025.xlsx", benchmark_page=4, occupancy_status="driven",
            ),
            IndexDriverYear(
                year=4, benchmark_adr=250.0, ari_target=104.0, ari_target_source="by_year", adr_status="driven",
            ),
        ],
    )


def test_index_driven_year_one_names_benchmark_target_doc_and_page() -> None:
    out = RevenueEngine().run(_index_input())
    adr = out.provenance["years[0].adr"]
    assert adr.formula == "adr = benchmark_adr × ari_target ÷ 100"
    by = _by_name(adr)
    assert by["benchmark_adr"].value == 210.0 and by["benchmark_adr"].assumption_key is None
    assert by["ari_target"].value == 100.0
    assert by["ari_target"].assumption_key == "index_ari_target"  # scalar target
    _reconcile(adr, 210.0 * 100.0 / 100.0)
    assert out.years[0].adr == adr.value
    note = adr.note or ""
    assert "STR comp-set TTM" in note and "STR Trend May 2025.xlsx" in note and "p.3" in note
    # Occupancy was NOT driven in Year 1 — it stays the starting assumption.
    assert out.provenance["years[0].occupancy"].formula == "occupancy = starting_occupancy"


def test_index_driven_out_year_is_the_capped_benchmark_times_target() -> None:
    out = RevenueEngine().run(_index_input())
    occ = out.provenance["years[2].occupancy"]
    assert occ.formula == "occupancy = min(0.95, benchmark_occupancy × mpi_target ÷ 100)"
    by = _by_name(occ)
    assert by["mpi_target"].assumption_key == "index_mpi_target_by_year"
    _reconcile(occ, min(0.95, 0.72 * 111.1 / 100.0))
    assert out.years[2].occupancy == occ.value
    assert "Miami Horizons Q3 2025.xlsx" in (occ.note or "") and "p.4" in (occ.note or "")


def test_years_after_a_driver_anchor_on_that_years_own_trace() -> None:
    out = RevenueEngine().run(_index_input())
    # Year 4 occupancy: undriven, anchored on Year 3 (the driven year).
    occ4 = out.provenance["years[3].occupancy"]
    assert occ4.formula == "occupancy = min(0.95, anchor_occupancy × (1 + occupancy_growth) ^ years_compounded)"
    by = _by_name(occ4)
    assert by["anchor_occupancy"].traces_to == "years[2].occupancy"
    assert by["anchor_occupancy"].value == out.years[2].occupancy
    assert by["years_compounded"].value == 1
    _reconcile(occ4, min(0.95, out.years[2].occupancy * 1.01))
    assert "Anchored on Year 3" in (occ4.note or "")

    # Year 2 ADR: undriven, anchored on Year 1 — which WAS driven, so the
    # Year-1 driver (benchmark × target) is the anchor it compounds.
    adr2 = out.provenance["years[1].adr"]
    assert adr2.formula == "adr = benchmark_adr × ari_target ÷ 100 × (1 + adr_growth) ^ years_compounded"
    aby = _by_name(adr2)
    assert aby["ari_target"].assumption_key == "index_ari_target"
    assert aby["adr_growth"].assumption_key == "adr_growth"
    _reconcile(adr2, 210.0 * 1.03)
    assert out.years[1].adr == adr2.value

    # Year 5 ADR: anchored on Year 4 (by-year ARI), traced to Year 4's ADR.
    adr5 = out.provenance["years[4].adr"]
    assert _by_name(adr5)["anchor_adr"].traces_to == "years[3].adr"
    _reconcile(adr5, out.years[3].adr * 1.03)


def test_driver_without_a_benchmark_record_names_the_driver_value_itself() -> None:
    """An explicit per-year value and no IndexDriverYear: the trace does not
    invent a benchmark — the driver value is the named input."""
    out = RevenueEngine().run(_input(occupancy_by_year_override=[None, 0.80]))
    occ = out.provenance["years[1].occupancy"]
    assert occ.formula == "occupancy = min(0.95, index_driver_occupancy)"
    assert [i.name for i in occ.inputs] == ["index_driver_occupancy"]
    assert occ.inputs[0].value == 0.80
    assert "explicit per-year driver" in (occ.note or "")
    _reconcile(occ, 0.80)


def test_driver_that_does_not_reconcile_falls_back_to_the_driver_value() -> None:
    """A benchmark record whose product does not reproduce the driver (unit
    normalization upstream) must not be asserted as the formula."""
    out = RevenueEngine().run(
        _input(
            adr_by_year_override=[None, 240.0],
            index_drivers_by_year=[
                IndexDriverYear(year=2, benchmark_adr=200.0, ari_target=110.0, ari_target_source="scalar", adr_status="driven"),
            ],
        )
    )
    adr = out.provenance["years[1].adr"]
    assert adr.formula == "adr = index_driver_adr"
    assert [i.name for i in adr.inputs] == ["index_driver_adr"]
    _reconcile(adr, 240.0)
    assert "does not reproduce it" in (adr.note or "")


# ─────────────────────────── structured PIP displacement ───────────────────────


def test_structured_pip_year_one_and_recovery_factors_are_named() -> None:
    out = RevenueEngine().run(
        _input(
            hold_years=3,
            pip_displacement=PIPDisplacement(
                closure_strategy="wing_by_wing", pct_rooms_offline_by_month=[0.5] * 6 + [0.0] * 6,
                brand="Kimpton", revpar_index_post_reno=1.05, occupancy_recovery_months=12,
            ),
        )
    )
    occ1 = out.provenance["years[0].occupancy"]
    assert occ1.formula == "occupancy = starting_occupancy × y1_revenue_factor"
    by = _by_name(occ1)
    _reconcile(occ1, by["starting_occupancy"].value * by["y1_revenue_factor"].value)
    assert "wing_by_wing" in (occ1.note or "") and "Kimpton" in (occ1.note or "")

    adr1 = out.provenance["years[0].adr"]
    assert adr1.formula == "adr = starting_adr × y1_adr_drag"
    aby = _by_name(adr1)
    _reconcile(adr1, aby["starting_adr"].value * aby["y1_adr_drag"].value)

    occ2 = out.provenance["years[1].occupancy"]
    assert occ2.formula.endswith("× y2_occupancy_recovery_factor")
    oby = _by_name(occ2)
    _reconcile(
        occ2,
        min(0.95, oby["starting_occupancy"].value * (1 + oby["occupancy_growth"].value)) * oby["y2_occupancy_recovery_factor"].value,
    )
    adr3 = out.provenance["years[2].adr"]
    assert adr3.formula.endswith("× revpar_index_post_reno")
    a3 = _by_name(adr3)
    _reconcile(adr3, a3["starting_adr"].value * (1 + a3["adr_growth"].value) ** 2 * a3["revpar_index_post_reno"].value)
    for i, yr in enumerate(out.years):
        assert out.provenance[f"years[{i}].occupancy"].value == yr.occupancy
        assert out.provenance[f"years[{i}].adr"].value == yr.adr


# ─────────────────────────── 5. the lineage graph carries the nodes ────────────


@pytest.fixture
async def _db():
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


@pytest.mark.asyncio
async def test_persisted_run_lineage_carries_the_rate_nodes_the_drawer_asks_for(_db) -> None:
    """``engine:revenue.years[1].adr`` (what ``lineageRequestFor`` opens) is
    a node on the record, reached from the KPI roots, and walks to the
    starting ADR assumption — so the drawer has a chain, not 'No trace'."""
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines
    from app.services.lineage import build_lineage

    deal_id, tenant_id, run_id = str(uuid4()), str(uuid4()), str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, keys, purchase_price) "
                "VALUES (:id, :t, :n, :k, :p)"
            ),
            {"id": deal_id, "t": tenant_id, "n": "FON-87 rate traces", "k": 132, "p": 36_400_000},
        )
        await session.commit()
        await run_all_engines(session, deal_id=deal_id, tenant_id=tenant_id, run_id=run_id)
        await session.commit()
        record = await build_lineage(session, deal_id, tenant_id, run_id=run_id)

    nodes = {n.id: n for n in record.nodes}
    adjacency: dict[str, list[str]] = {}
    for e in record.edges:
        adjacency.setdefault(e.src, []).append(e.dst)

    def reachable(start: str) -> set[str]:
        seen, stack = set(), [start]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            stack.extend(adjacency.get(cur, ()))
        return seen

    for i in range(2):
        for path in ("occupancy", "adr", "revpar", "occupied_rooms"):
            assert f"engine:revenue.years[{i}].{path}" in nodes, path

    # Reached from a headline KPI (the drawer offers kpi:… and engine:… roots).
    from_irr = reachable("kpi:returns.levered_irr")
    assert "engine:revenue.years[1].adr" in from_irr
    assert "engine:revenue.years[1].occupancy" in from_irr

    # And the rate node itself walks to the assumption that grounds it.
    assert "assumption:starting_adr" in reachable("engine:revenue.years[1].adr")
    assert "assumption:starting_occupancy" in reachable("engine:revenue.years[1].occupancy")
    assert "assumption:adr_growth" in reachable("engine:revenue.years[1].adr")
    # No refusal names a rate node: each carries a formula and asserted links.
    assert not [r for r in record.unresolved if (r.concept or "").startswith("engine:revenue.years[1].adr")]
