"""FON-116 / FON-117 — topline PR 2: penetration × ramp × benchmark × per-year
growth in the revenue engine (``topline-growth-plan.md`` §2.4), pure.

Locks every formula and edge case of §2.4:

* ``pen_y = V_y | P × r_y (y < S, ramp defined) | P`` and
  ``occ_y = min(0.95, B_occ,y × pen ÷ 100)``, ``adr_y = B_adr,y × pen ÷ 100``;
* default ramp ``[0.85, 0.95]`` for S = 3 only, ``[0.85]`` for S = 2, UNDEFINED
  for years 3..S−1 when S ≥ 4 (100 %, ``undefined_for_stabilization_year``) —
  no interpolation is invented;
* hold shorter than S: the ramp keeps applying, the year never reaches 100 %
  and the record says so;
* a metric with no benchmark runs on the subject's own starting (T-12) level
  with ``benchmark_unavailable`` — penetration / ramp are NOT applied to it;
* a year with no growth for a metric holds that metric flat
  (``flat_no_growth``); the other metric grows;
* the 0.95 occupancy cap is kept and surfaced as ``capped_95``;
* displacement multiplies on top exactly as before; the T-12 reference rows
  pass through and move no number;
* per-year growth lists: a constant list equal to the scalar runs the scalar
  path byte-for-byte; a varying list compounds year by year and the traces
  name every step; segments read ADR growth from the list.
"""

from __future__ import annotations

import math
import os
from typing import Any
from uuid import UUID

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from fondok_schemas.underwriting import (  # noqa: E402
    BenchmarkYear,
    GrowthYear,
    IndexDriverYear,
    RevenueEngineInput,
    RevenueSegment,
    ToplineDrivers,
    ToplineReference,
)
from pydantic import ValidationError  # noqa: E402

from app.engines.revenue import (  # noqa: E402
    LEGACY_TOPLINE_METHODOLOGY_VERSION,
    OCCUPANCY_CAP,
    TOPLINE_METHODOLOGY_VERSION,
    RevenueEngine,
    default_ramp_by_year,
)

TOL = 1e-9
DEAL = UUID("11111111-1111-1111-1111-111111111111")
B_OCC = 0.70
B_ADR = 200.0


def _bench(hold: int, *, occ: float | None = B_OCC, adr: float | None = B_ADR,
           gaps: dict[int, dict[str, Any]] | None = None) -> list[BenchmarkYear]:
    out = []
    for y in range(1, hold + 1):
        kw: dict[str, Any] = {"occupancy": occ, "adr": adr}
        if gaps and y in gaps:
            kw.update(gaps[y])
        out.append(
            BenchmarkYear(
                year=y, calendar_year=2025 + y, basis="actual" if y == 1 else "forecast",
                level="comp_set", label=f"STR comp set {2025 + y}", doc_name="STR Trend.xlsx", page=3,
                **kw,
            )
        )
    return out


def _topline(hold: int = 5, stabilization_year: int = 3, **kw: Any) -> ToplineDrivers:
    base: dict[str, Any] = dict(
        benchmark_level="comp_set",
        benchmark_label="Actual · STR comp set TTM Aug 2025 · STR Trend.xlsx",
        benchmark_by_year=_bench(hold),
        stabilization_year=stabilization_year,
    )
    base.update(kw)
    return ToplineDrivers(**base)


def _input(**kw: Any) -> RevenueEngineInput:
    base: dict[str, Any] = dict(
        deal_id=DEAL, keys=100, starting_occupancy=0.75, starting_adr=250.0,
        occupancy_growth=0.01, adr_growth=0.03, hold_years=5,
    )
    base.update(kw)
    return RevenueEngineInput(**base)


def _run(**kw: Any):
    return RevenueEngine().run(_input(**kw))


def _by_year(out) -> dict[int, IndexDriverYear]:
    return {r.year: r for r in out.topline_by_year}


def _by_name(trace) -> dict[str, Any]:
    return {i.name: i for i in trace.inputs}


# ─────────────────────────── default ramp table ───────────────────────────


def test_default_ramp_is_defined_for_s2_and_s3_only() -> None:
    assert default_ramp_by_year(1) == []
    assert default_ramp_by_year(2) == [0.85]
    assert default_ramp_by_year(3) == [0.85, 0.95]
    # S ≥ 4: years 3..S−1 are UNDEFINED — never interpolated.
    assert default_ramp_by_year(4) == [0.85, 0.95, None]
    assert default_ramp_by_year(6) == [0.85, 0.95, None, None, None]


# ─────────────────────────── §2.4 core formula ───────────────────────────


def test_s3_default_penetration_and_ramp() -> None:
    out = _run(topline=_topline())
    occ = [y.occupancy for y in out.years]
    adr = [y.adr for y in out.years]
    assert occ[0] == pytest.approx(B_OCC * 100.0 * 0.85 / 100.0, rel=TOL)
    assert occ[1] == pytest.approx(B_OCC * 100.0 * 0.95 / 100.0, rel=TOL)
    assert occ[2:] == pytest.approx([B_OCC] * 3, rel=TOL)
    assert adr[0] == pytest.approx(B_ADR * 0.85, rel=TOL)
    assert adr[1] == pytest.approx(B_ADR * 0.95, rel=TOL)
    assert adr[2:] == pytest.approx([B_ADR] * 3, rel=TOL)
    rec = _by_year(out)
    assert [rec[y].ramp_pct for y in range(1, 6)] == [0.85, 0.95, None, None, None]
    assert [rec[y].ramp_state for y in range(1, 6)] == [
        "ramped", "ramped", "stabilized", "stabilized", "stabilized",
    ]
    assert [rec[y].mpi_target for y in range(1, 6)] == pytest.approx([85.0, 95.0, 100.0, 100.0, 100.0])
    assert [rec[y].ari_target for y in range(1, 6)] == pytest.approx([85.0, 95.0, 100.0, 100.0, 100.0])
    for y in range(1, 6):
        assert rec[y].occupancy_status == "driven" and rec[y].adr_status == "driven"
        assert rec[y].benchmark_level == "comp_set"
        assert rec[y].mpi_target_source == "scalar" and rec[y].ari_target_source == "scalar"
        assert rec[y].penetration_occ_source == "default" and rec[y].penetration_adr_source == "default"
        assert rec[y].benchmark_occupancy == B_OCC and rec[y].benchmark_adr == B_ADR
        assert rec[y].benchmark_doc == "STR Trend.xlsx" and rec[y].benchmark_page == 3
        assert rec[y].calendar_year == 2025 + y
        # The record echoes the figures that ran.
        assert rec[y].occupancy == out.years[y - 1].occupancy
        assert rec[y].adr == out.years[y - 1].adr
        assert rec[y].rgi_derived == pytest.approx(rec[y].mpi_target * rec[y].ari_target / 100.0)
    assert out.topline_methodology_version == TOPLINE_METHODOLOGY_VERSION == 2
    # The legacy echo stays empty: nothing legacy drove this run.
    assert out.index_drivers_by_year == []


def test_s2_default_ramp_is_a_single_year() -> None:
    out = _run(topline=_topline(stabilization_year=2))
    rec = _by_year(out)
    assert out.years[0].occupancy == pytest.approx(B_OCC * 0.85, rel=TOL)
    assert [y.occupancy for y in out.years[1:]] == pytest.approx([B_OCC] * 4, rel=TOL)
    assert rec[1].ramp_pct == 0.85 and rec[1].ramp_state == "ramped"
    assert rec[2].ramp_state == "stabilized" and rec[2].ramp_pct is None


def test_s4_default_ramp_is_undefined_for_year_three() -> None:
    out = _run(topline=_topline(stabilization_year=4))
    rec = _by_year(out)
    occ = [y.occupancy for y in out.years]
    assert occ[0] == pytest.approx(B_OCC * 0.85, rel=TOL)
    assert occ[1] == pytest.approx(B_OCC * 0.95, rel=TOL)
    # Year 3 is NOT interpolated: 100 % with the undefined state surfaced.
    assert occ[2] == pytest.approx(B_OCC, rel=TOL)
    assert rec[3].ramp_pct is None
    assert rec[3].ramp_state == "undefined_for_stabilization_year"
    assert rec[3].mpi_target == pytest.approx(100.0)
    assert "Year 3" in (rec[3].reason or "") and "ramp" in (rec[3].reason or "").lower()
    assert rec[4].ramp_state == "stabilized" and rec[5].ramp_state == "stabilized"


def test_s6_default_ramp_is_undefined_for_years_three_to_five() -> None:
    out = _run(topline=_topline(hold=6, stabilization_year=6), hold_years=6)
    rec = _by_year(out)
    assert [rec[y].ramp_state for y in range(1, 7)] == [
        "ramped", "ramped",
        "undefined_for_stabilization_year", "undefined_for_stabilization_year",
        "undefined_for_stabilization_year", "stabilized",
    ]
    assert [y.occupancy for y in out.years[2:]] == pytest.approx([B_OCC] * 4, rel=TOL)


def test_analyst_ramp_replaces_the_default_for_any_s() -> None:
    out = _run(topline=_topline(stabilization_year=4, ramp_by_year=[0.80, 0.90, 0.97]))
    rec = _by_year(out)
    assert [y.occupancy for y in out.years] == pytest.approx(
        [B_OCC * 0.80, B_OCC * 0.90, B_OCC * 0.97, B_OCC, B_OCC], rel=TOL
    )
    assert [rec[y].ramp_state for y in range(1, 6)] == ["ramped", "ramped", "ramped", "stabilized", "stabilized"]
    assert rec[3].ramp_pct == 0.97


def test_analyst_ramp_with_a_gap_is_no_ramp_for_that_year_not_undefined_default() -> None:
    out = _run(topline=_topline(stabilization_year=4, ramp_by_year=[0.80, None, 0.97]))
    rec = _by_year(out)
    assert out.years[1].occupancy == pytest.approx(B_OCC, rel=TOL)
    assert rec[2].ramp_state == "no_ramp" and rec[2].ramp_pct is None


def test_ramp_fractions_are_validated() -> None:
    with pytest.raises(ValidationError):
        _topline(ramp_by_year=[0.85, 1.6])
    with pytest.raises(ValidationError):
        _topline(ramp_by_year=[0.0, 0.95])


def test_hold_shorter_than_stabilization_keeps_the_ramp_and_says_so() -> None:
    out = _run(
        hold_years=3,
        topline=_topline(hold=3, stabilization_year=5, ramp_by_year=[0.85, 0.90, 0.95, 0.98]),
    )
    rec = _by_year(out)
    assert [y.occupancy for y in out.years] == pytest.approx([B_OCC * 0.85, B_OCC * 0.90, B_OCC * 0.95], rel=TOL)
    # The projection never reaches 100 %: the last year runs at ITS ramp.
    assert rec[3].ramp_pct == 0.95
    assert rec[3].ramp_state == "stabilization_after_hold"
    assert "Stabilization (Year 5) is after the hold" in (rec[3].reason or "")
    assert "Year 3 runs at ramp 0.95" in (rec[3].reason or "")
    assert rec[1].ramp_state == "ramped" and rec[2].ramp_state == "ramped"


def test_hold_shorter_than_stabilization_with_the_undefined_default() -> None:
    out = _run(hold_years=3, topline=_topline(hold=3, stabilization_year=5))
    rec = _by_year(out)
    assert out.years[2].occupancy == pytest.approx(B_OCC, rel=TOL)
    assert rec[3].ramp_state == "undefined_for_stabilization_year"


def test_scalar_analyst_penetration_scales_with_the_ramp() -> None:
    out = _run(topline=_topline(penetration_occ=110.0, penetration_occ_source="analyst_override", penetration_adr=92.0, penetration_adr_source="analyst_override"))
    rec = _by_year(out)
    assert out.years[0].occupancy == pytest.approx(B_OCC * 110.0 * 0.85 / 100.0, rel=TOL)
    assert out.years[2].occupancy == pytest.approx(B_OCC * 1.10, rel=TOL)
    assert out.years[1].adr == pytest.approx(B_ADR * 92.0 * 0.95 / 100.0, rel=TOL)
    assert rec[1].mpi_target == pytest.approx(110.0 * 0.85)
    assert rec[1].penetration_occ_source == "analyst_override"
    assert rec[1].penetration_adr_source == "analyst_override"


def test_per_year_penetration_override_beats_the_formula_for_that_year() -> None:
    out = _run(topline=_topline(
        penetration_adr_by_year=[None, 97.0, None, None, 104.0],
        penetration_occ_by_year=[None, None, None, 100.0, None],
    ))
    rec = _by_year(out)
    # Year 2 ADR is 97 (not 100 × 0.95); Year 2 occupancy still ramps.
    assert out.years[1].adr == pytest.approx(B_ADR * 0.97, rel=TOL)
    assert out.years[1].occupancy == pytest.approx(B_OCC * 0.95, rel=TOL)
    assert rec[2].ari_target == 97.0 and rec[2].ari_target_source == "by_year"
    assert rec[2].mpi_target == pytest.approx(95.0) and rec[2].mpi_target_source == "scalar"
    # Year 5 ADR 104 while occupancy stays at the stabilized 100.
    assert out.years[4].adr == pytest.approx(B_ADR * 1.04, rel=TOL)
    assert rec[5].ari_target_source == "by_year"
    # An override equal to the formula value is still an override.
    assert rec[4].mpi_target == 100.0 and rec[4].mpi_target_source == "by_year"
    assert rec[4].ramp_state == "stabilized"
    # Years 1, 3 run the formula.
    assert out.years[0].adr == pytest.approx(B_ADR * 0.85, rel=TOL)
    assert out.years[2].adr == pytest.approx(B_ADR, rel=TOL)
    assert rec[1].ari_target_source == "scalar"


def test_occupancy_cap_is_kept_and_surfaced() -> None:
    out = _run(topline=_topline(benchmark_by_year=_bench(5, occ=0.93), penetration_occ=110.0, penetration_occ_source="analyst_override"))
    rec = _by_year(out)
    # Year 1: 0.93 × 110 × 0.85 ÷ 100 = 0.8695 — below the cap.
    assert out.years[0].occupancy == pytest.approx(0.93 * 110.0 * 0.85 / 100.0, rel=TOL)
    assert rec[1].capped_95 is False
    # Year 3: 0.93 × 1.10 = 1.023 → capped at 0.95, visibly.
    assert out.years[2].occupancy == OCCUPANCY_CAP == 0.95
    assert rec[3].capped_95 is True
    assert rec[3].occupancy_status == "driven"
    # ADR is never capped.
    assert out.years[2].adr == pytest.approx(B_ADR, rel=TOL)
    trace = out.provenance["years[2].occupancy"]
    assert trace.formula.startswith("occupancy = min(0.95, benchmark_occupancy × mpi_target ÷ 100)")
    by = _by_name(trace)
    assert min(0.95, by["benchmark_occupancy"].value * by["mpi_target"].value / 100.0) == pytest.approx(trace.value)


def test_year_one_occupancy_is_capped_too() -> None:
    out = _run(topline=_topline(benchmark_by_year=_bench(5, occ=0.93), penetration_occ=130.0, penetration_occ_source="analyst_override"))
    assert out.years[0].occupancy == 0.95
    assert _by_year(out)[1].capped_95 is True
    assert out.provenance["years[0].occupancy"].formula == "occupancy = min(0.95, benchmark_occupancy × mpi_target ÷ 100)"


# ─────────────────────────── missing benchmark / growth ───────────────────────────


def test_missing_benchmark_for_one_metric_uses_the_subject_level_without_penetration() -> None:
    out = _run(
        topline=_topline(benchmark_by_year=_bench(5, adr=None)),
        adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03],
    )
    rec = _by_year(out)
    # Occupancy is driven; ADR is the subject's own starting level × growth.
    assert out.years[0].occupancy == pytest.approx(B_OCC * 0.85, rel=TOL)
    assert out.years[0].adr == 250.0
    assert out.years[1].adr == 250.0 * (1.0 + 0.03) ** 1
    assert out.years[4].adr == 250.0 * (1.0 + 0.03) ** 4
    for y in range(1, 6):
        assert rec[y].adr_status == "benchmark_unavailable"
        assert rec[y].occupancy_status == "driven"
        assert rec[y].ari_target is None and rec[y].ari_target_source is None
        assert rec[y].benchmark_adr is None
        # The engine names what it knows — the starting figure it was given —
        # never a provenance (T-12 / seed / override) only the loader knows.
        assert "Subject's own starting adr as supplied" in (rec[y].reason or "")
        assert "no comp_set benchmark for ADR" in (rec[y].reason or "")
        assert "T-12" not in (rec[y].reason or "")
    # The ADR trace is the legacy starting-assumption trace — no target named.
    adr1 = out.provenance["years[0].adr"]
    assert adr1.formula == "adr = starting_adr"
    assert "ari_target" not in _by_name(out.provenance["years[2].adr"])


def test_no_benchmark_at_all_runs_the_subject_level_times_growth_exactly_like_the_scalar_path() -> None:
    legacy = _run()
    out = _run(
        topline=ToplineDrivers(
            benchmark_level="none", benchmark_label="No benchmark",
            benchmark_fallback_reason="no STR, market study or CBRE Horizons on the deal",
            benchmark_by_year=[], stabilization_year=3,
        ),
        occupancy_growth_by_year=[None, 0.01, 0.01, 0.01, 0.01],
        adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03],
    )
    assert [y.model_dump() for y in out.years] == [y.model_dump() for y in legacy.years]
    rec = _by_year(out)
    for y in range(1, 6):
        assert rec[y].occupancy_status == "benchmark_unavailable"
        assert rec[y].adr_status == "benchmark_unavailable"
        assert rec[y].benchmark_level == "none"
        assert rec[y].mpi_target is None and rec[y].ari_target is None
    assert "No benchmark — projecting from the subject's own starting occupancy and ADR as supplied" in (rec[1].reason or "")
    assert "no STR, market study or CBRE Horizons on the deal" in (rec[1].reason or "")
    assert out.topline_methodology_version == 2


def test_missing_growth_for_one_metric_holds_it_flat_for_that_year() -> None:
    out = _run(
        topline=_topline(benchmark_by_year=_bench(5, adr=None)),
        adr_growth_by_year=[None, 0.03, None, 0.02, None],
    )
    rec = _by_year(out)
    adr = [y.adr for y in out.years]
    assert adr[0] == 250.0
    assert adr[1] == pytest.approx(250.0 * 1.03, rel=TOL)
    assert adr[2] == adr[1]  # held flat — growth 0 is the absence of growth
    assert adr[3] == pytest.approx(250.0 * 1.03 * 1.02, rel=TOL)
    assert adr[4] == adr[3]
    # No benchmark is the more consequential state and keeps the status; the
    # missing growth figure is carried by the metric's growth source.
    for y in (2, 3, 4, 5):
        assert rec[y].adr_status == "benchmark_unavailable", y
    assert rec[3].adr_growth_source == "unavailable" and rec[5].adr_growth_source == "unavailable"
    assert rec[2].adr_growth_source is None and rec[4].adr_growth_source is None
    assert rec[3].adr_growth_applied is None and rec[4].adr_growth_applied == 0.02
    # Occupancy keeps its benchmark path untouched.
    assert out.years[2].occupancy == pytest.approx(B_OCC, rel=TOL)
    assert rec[3].occupancy_status == "driven"


def test_per_year_benchmark_gap_holds_the_metric_flat_then_resumes() -> None:
    bench = _bench(5, gaps={3: {"occupancy": None}})
    out = _run(topline=_topline(benchmark_by_year=bench), occupancy_growth_by_year=[None, None, None, None, None])
    rec = _by_year(out)
    # Year 3 has no occupancy benchmark and no growth: Year 2's base carries.
    assert out.years[2].occupancy == pytest.approx(out.years[1].occupancy, rel=TOL)
    assert rec[3].occupancy_status == "benchmark_unavailable"
    assert rec[3].occupancy_growth_source == "unavailable"
    # The ramp state describes the year (ADR still runs it); occupancy has no target.
    assert rec[3].ramp_state == "stabilized" and rec[3].mpi_target is None
    # ADR for Year 3 is still driven (its benchmark exists).
    assert rec[3].adr_status == "driven"
    assert out.years[2].adr == pytest.approx(B_ADR, rel=TOL)
    # Year 4 resumes on the benchmark.
    assert out.years[3].occupancy == pytest.approx(B_OCC, rel=TOL)
    assert rec[4].occupancy_status == "driven"


def test_per_year_benchmark_gap_with_growth_compounds_from_the_last_driven_year() -> None:
    bench = _bench(5, gaps={3: {"adr": None}})
    out = _run(topline=_topline(benchmark_by_year=bench), adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03])
    rec = _by_year(out)
    assert out.years[2].adr == pytest.approx(B_ADR * 0.95 * 1.03, rel=TOL)
    assert rec[3].adr_status == "benchmark_unavailable"
    trace = out.provenance["years[2].adr"]
    assert "anchor_adr" in _by_name(trace)
    assert _by_name(trace)["anchor_adr"].traces_to == "years[1].adr"


# ─────────────────────────── displacement + reference rows ───────────────────────────


def test_displacement_stacks_on_the_topline_exactly_as_before() -> None:
    plain = _run(topline=_topline())
    disp = _run(topline=_topline(), y1_occupancy_displacement_pct=0.10, y1_adr_displacement_pct=0.05)
    assert disp.years[0].occupancy == pytest.approx(plain.years[0].occupancy * 0.90, rel=TOL)
    assert disp.years[0].adr == pytest.approx(plain.years[0].adr * 0.95, rel=TOL)
    # The out-years are untouched by a Year-1 displacement.
    for a, b in zip(plain.years[1:], disp.years[1:], strict=True):
        assert a.occupancy == b.occupancy and a.adr == b.adr
    assert disp.provenance["years[0].occupancy"].formula == (
        "occupancy = min(0.95, benchmark_occupancy × mpi_target ÷ 100) × (1 − y1_occupancy_displacement_pct)"
    )
    # The record carries the figure that RAN (displaced), like the legacy echo.
    assert _by_year(disp)[1].occupancy == disp.years[0].occupancy


def test_renovation_schedule_stacks_on_the_topline() -> None:
    plain = _run(topline=_topline())
    sched = _run(
        topline=_topline(),
        renovation_displacement_schedule=[
            {"start_month": 13, "end_month": 18, "rooms_offline_pct": 0.5, "rate_discount_pct": 0.0}
        ],
    )
    assert sched.years[1].occupancy == pytest.approx(plain.years[1].occupancy * 0.75, rel=TOL)
    assert sched.years[0].occupancy == plain.years[0].occupancy
    assert sched.years[2].occupancy == plain.years[2].occupancy


def test_t12_reference_passes_through_and_moves_no_number() -> None:
    ref = ToplineReference(occupancy=0.78, adr=410.0, revpar=319.8, period_label="T-12 Aug 2025", doc_name="T12.xlsx")
    without = _run(topline=_topline())
    with_ref = _run(topline=_topline(t12_reference=ref))
    assert [y.model_dump() for y in with_ref.years] == [y.model_dump() for y in without.years]
    for rec in with_ref.topline_by_year:
        assert rec.t12_occupancy == 0.78 and rec.t12_adr == 410.0
    for rec in without.topline_by_year:
        assert rec.t12_occupancy is None and rec.t12_adr is None


def test_growth_provenance_labels_ride_on_the_records() -> None:
    growth = [
        GrowthYear(year=y, calendar_year=2025 + y, occupancy_growth=0.01, adr_growth=0.03,
                   occupancy_source="cbre_horizons", adr_source="cbre_horizons",
                   label=f"Forecast · CBRE Horizons {2025 + y} · All Hotels")
        for y in range(2, 6)
    ]
    out = _run(
        topline=_topline(benchmark_by_year=_bench(5, adr=None), growth_by_year=growth),
        occupancy_growth_by_year=[None, 0.01, 0.01, 0.01, 0.01],
        adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03],
    )
    rec = _by_year(out)
    assert rec[1].growth_label is None and rec[1].adr_growth_source is None
    assert rec[3].growth_label == "Forecast · CBRE Horizons 2028 · All Hotels"
    assert rec[3].adr_growth_source == "cbre_horizons" and rec[3].occupancy_growth_source == "cbre_horizons"


# ─────────────────────────── growth lists without a topline ───────────────────────────


def test_constant_growth_list_equal_to_the_scalar_is_byte_identical() -> None:
    scalar = _run()
    listed = _run(occupancy_growth_by_year=[None, 0.01, 0.01, 0.01, 0.01], adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03])
    assert scalar.model_dump_json() == listed.model_dump_json()
    # Including the traces: the scalar assumption is what the list restates.
    occ3 = listed.provenance["years[2].occupancy"]
    assert occ3.formula == "occupancy = min(0.95, starting_occupancy × (1 + occupancy_growth) ^ years_compounded)"
    assert _by_name(occ3)["occupancy_growth"].assumption_key == "occupancy_growth"


def test_legacy_path_emits_version_one_and_one_record_per_year() -> None:
    out = _run()
    assert out.topline_methodology_version == LEGACY_TOPLINE_METHODOLOGY_VERSION == 1
    rec = _by_year(out)
    assert sorted(rec) == [1, 2, 3, 4, 5]
    for y in range(1, 6):
        assert rec[y].occupancy == out.years[y - 1].occupancy
        assert rec[y].adr == out.years[y - 1].adr
        assert rec[y].occupancy_status == "no_target" and rec[y].adr_status == "no_target"
        assert rec[y].benchmark_level is None and rec[y].mpi_target is None
    assert rec[1].occupancy_growth_applied is None and rec[2].occupancy_growth_applied == 0.01
    assert rec[2].adr_growth_applied == 0.03
    assert out.index_drivers_by_year == []


def test_legacy_drivers_are_echoed_in_both_lists() -> None:
    out = _run(
        occupancy_by_year_override=[None, None, 0.80, None, None],
        index_drivers_by_year=[IndexDriverYear(year=3, benchmark_occupancy=0.72, mpi_target=111.1, mpi_target_source="by_year", occupancy_status="driven")],
    )
    assert out.topline_methodology_version == 1
    rec = _by_year(out)
    assert rec[3].mpi_target == 111.1 and rec[3].occupancy == out.years[2].occupancy
    assert rec[3] == out.index_drivers_by_year[0]
    assert rec[2].occupancy_status == "no_target"


def test_varying_growth_list_compounds_year_by_year_and_the_traces_name_every_step() -> None:
    out = _run(occupancy_growth_by_year=[None, 0.02, 0.03, None, 0.01])
    occ = [y.occupancy for y in out.years]
    assert occ[0] == 0.75
    assert occ[1] == pytest.approx(0.75 * 1.02, rel=TOL)
    assert occ[2] == pytest.approx(0.75 * 1.02 * 1.03, rel=TOL)
    assert occ[3] == occ[2]
    assert occ[4] == pytest.approx(0.75 * 1.02 * 1.03 * 1.01, rel=TOL)
    rec = _by_year(out)
    assert rec[4].occupancy_status == "flat_no_growth" and rec[4].occupancy_growth_applied is None
    assert rec[5].occupancy_status == "no_target" and rec[5].occupancy_growth_applied == 0.01
    # Traces: every step is an input the lineage walk can link.
    t5 = out.provenance["years[4].occupancy"]
    assert t5.formula == (
        "occupancy = min(0.95, starting_occupancy × (1 + occupancy_growth_y2) × (1 + occupancy_growth_y3) "
        "× (1 + occupancy_growth_y4) × (1 + occupancy_growth_y5))"
    )
    by = _by_name(t5)
    assert by["occupancy_growth_y2"].value == 0.02 and by["occupancy_growth_y2"].assumption_key == "occupancy_growth_by_year"
    assert by["occupancy_growth_y4"].value is None  # no figure — never a 0 rate in a trace
    assert "held flat" in (t5.note or "")
    prod = by["starting_occupancy"].value
    for k in ("occupancy_growth_y2", "occupancy_growth_y3", "occupancy_growth_y4", "occupancy_growth_y5"):
        prod *= 1.0 + (by[k].value if by[k].value is not None else 0.0)
    assert math.isclose(min(0.95, prod), t5.value, rel_tol=TOL)
    # ADR (no list) is untouched: the scalar path.
    assert out.provenance["years[4].adr"].formula == "adr = starting_adr × (1 + adr_growth) ^ years_compounded"


def test_short_growth_list_holds_the_years_past_its_end_flat() -> None:
    out = _run(adr_growth_by_year=[None, 0.03])
    adr = [y.adr for y in out.years]
    assert adr[1] == pytest.approx(250.0 * 1.03, rel=TOL)
    assert adr[2] == adr[1] == adr[3] == adr[4]
    rec = _by_year(out)
    assert rec[3].adr_status == rec[5].adr_status == "flat_no_growth"


def test_segments_read_adr_growth_from_the_list_and_the_segment_override_still_wins() -> None:
    segs = [
        RevenueSegment(name="transient_bar", mix_pct=0.5, adr=300.0, channel_cost_pct=0.0),
        RevenueSegment(name="corporate", mix_pct=0.5, adr=200.0, channel_cost_pct=0.0, adr_growth=0.05),
    ]
    out = _run(segments=segs, adr_growth_by_year=[None, 0.02, 0.04, None, 0.01])
    y3 = {s.name: s for s in out.years[2].segment_breakdown}
    assert y3["transient_bar"].adr == pytest.approx(300.0 * 1.02 * 1.04, rel=TOL)
    assert y3["corporate"].adr == pytest.approx(200.0 * 1.05 ** 2, rel=TOL)
    y4 = {s.name: s for s in out.years[3].segment_breakdown}
    assert y4["transient_bar"].adr == y3["transient_bar"].adr
    # A constant list equal to the scalar keeps the segmented path byte-identical.
    a = _run(segments=segs)
    b = _run(segments=segs, adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03])
    assert a.model_dump_json() == b.model_dump_json()


# ─────────────────────────── input contract ───────────────────────────


def test_topline_and_legacy_drivers_are_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="topline"):
        _input(topline=_topline(), occupancy_by_year_override=[None, 0.8])
    with pytest.raises(ValidationError, match="topline"):
        _input(topline=_topline(), index_drivers_by_year=[IndexDriverYear(year=1)])


def test_growth_list_must_agree_with_the_topline_growth_records() -> None:
    growth = [GrowthYear(year=2, occupancy_growth=0.02, adr_growth=None, occupancy_source="cbre_horizons", adr_source="unavailable", label="x")]
    with pytest.raises(ValidationError, match="growth_by_year"):
        _input(topline=_topline(growth_by_year=growth), occupancy_growth_by_year=[None, 0.05])
    with pytest.raises(ValidationError, match="growth_by_year"):
        _input(topline=_topline(growth_by_year=growth), adr_growth_by_year=[None, 0.03])
    # Agreeing lists (and None = None) are accepted.
    _input(topline=_topline(growth_by_year=growth), occupancy_growth_by_year=[None, 0.02], adr_growth_by_year=[None, None])


def test_growth_list_entries_are_bounded() -> None:
    with pytest.raises(ValidationError):
        _input(occupancy_growth_by_year=[None, 0.6])


# ─────────────────────────── traces reconcile ───────────────────────────


def _reconcile_rate(trace, cap: float | None) -> None:
    by = _by_name(trace)
    if "benchmark_occupancy" in by or "benchmark_adr" in by:
        bench = by.get("benchmark_occupancy") or by.get("benchmark_adr")
        target = by.get("mpi_target") or by.get("ari_target")
        v = bench.value * target.value / 100.0
    else:
        base = by.get("starting_occupancy") or by.get("starting_adr") or by.get("anchor_occupancy") or by.get("anchor_adr")
        v = base.value
        if "years_compounded" in by:
            g = by.get("occupancy_growth") or by.get("adr_growth")
            v = v * (1.0 + g.value) ** by["years_compounded"].value
        else:
            for name, inp in by.items():
                if "_growth_y" in name:
                    v *= 1.0 + (inp.value if inp.value is not None else 0.0)
    if cap is not None:
        v = min(cap, v)
    for name in ("y1_occupancy_displacement_pct", "y1_adr_displacement_pct"):
        if name in by:
            v *= 1.0 - by[name].value
    for name in ("schedule_occupancy_factor", "schedule_adr_factor"):
        if name in by:
            v *= by[name].value
    assert math.isclose(v, trace.value, rel_tol=1e-9, abs_tol=1e-9), (trace.formula, v, trace.value)


@pytest.mark.parametrize(
    "kw",
    [
        dict(topline=_topline()),
        dict(topline=_topline(stabilization_year=5, ramp_by_year=[0.8, 0.85, 0.9, 0.95])),
        dict(topline=_topline(benchmark_by_year=_bench(5, adr=None)), adr_growth_by_year=[None, 0.03, None, 0.02, 0.01]),
        dict(topline=_topline(benchmark_by_year=_bench(5, gaps={3: {"adr": None}})), adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03]),
        dict(topline=_topline(benchmark_by_year=_bench(5, occ=0.93), penetration_occ=120.0, penetration_occ_source="analyst_override"), y1_occupancy_displacement_pct=0.1),
        dict(occupancy_growth_by_year=[None, 0.02, 0.03, None, 0.01], adr_growth_by_year=[None, 0.03, 0.03, 0.03, 0.03]),
        dict(topline=_topline(), renovation_displacement_schedule=[{"start_month": 1, "end_month": 6, "rooms_offline_pct": 0.3, "rate_discount_pct": 0.1}]),
    ],
)
def test_rate_traces_reconcile_and_point_nowhere_dangling(kw: dict[str, Any]) -> None:
    out = _run(**kw)
    for i in range(len(out.years)):
        occ = out.provenance[f"years[{i}].occupancy"]
        adr = out.provenance[f"years[{i}].adr"]
        assert occ.value == out.years[i].occupancy and adr.value == out.years[i].adr
        _reconcile_rate(occ, cap=0.95)
        _reconcile_rate(adr, cap=None)
    for key, trace in out.provenance.items():
        for inp in trace.inputs:
            if inp.traces_to:
                assert inp.traces_to in out.provenance, (key, inp.traces_to)


# ─────────────────────────── coordinator review fixes ───────────────────────────


def test_raw_stabilization_year_past_the_hold_is_clamped_by_the_engine() -> None:
    """The engine receives S AS SET (not pre-clamped). With S = 6 and a 5-year
    hold: only Years 1..5 are evaluated, no year is ``stabilized``, the ramp
    keeps applying, and Year 5 says ``stabilization_after_hold``."""
    out = _run(
        hold_years=5,
        topline=_topline(hold=5, stabilization_year=6, ramp_by_year=[0.80, 0.85, 0.90, 0.95, 0.98]),
    )
    rec = _by_year(out)
    assert sorted(rec) == [1, 2, 3, 4, 5] and len(out.years) == 5  # the clamp
    assert "stabilized" not in {rec[y].ramp_state for y in rec}
    assert [y.occupancy for y in out.years] == pytest.approx(
        [B_OCC * r for r in (0.80, 0.85, 0.90, 0.95, 0.98)], rel=TOL
    )
    assert rec[5].ramp_state == "stabilization_after_hold" and rec[5].ramp_pct == 0.98
    assert "Stabilization (Year 6) is after the hold; Year 5 runs at ramp 0.98" in (rec[5].reason or "")
    assert [rec[y].ramp_state for y in (1, 2, 3, 4)] == ["ramped"] * 4
    # The input itself still carries the raw value.
    assert _topline(hold=5, stabilization_year=6).stabilization_year == 6


def test_raw_stabilization_year_past_the_hold_with_the_undefined_default_says_both() -> None:
    out = _run(hold_years=5, topline=_topline(hold=5, stabilization_year=6))
    rec = _by_year(out)
    assert rec[5].ramp_state == "undefined_for_stabilization_year"
    assert "Enter the ramp for Years 3–5" in (rec[5].reason or "")
    assert "Stabilization (Year 6) is after the hold; Year 5 runs at ramp 100 % (none defined)" in (rec[5].reason or "")
    assert "after the hold" not in (rec[4].reason or "")


def test_no_benchmark_and_no_growth_keeps_both_signals_on_the_record() -> None:
    """``benchmark_unavailable`` (the more consequential state) is the status;
    the missing growth figure survives as ``*_growth_source == "unavailable"``
    with ``*_growth_applied is None`` — neither overwrites the other."""
    out = _run(
        topline=_topline(benchmark_by_year=_bench(5, adr=None)),
        adr_growth_by_year=[None, None, None, None, None],
    )
    rec = _by_year(out)
    for y in range(2, 6):
        assert rec[y].adr_status == "benchmark_unavailable", y
        assert rec[y].adr_growth_source == "unavailable", y
        assert rec[y].adr_growth_applied is None, y
        assert out.years[y - 1].adr == 250.0  # held at the starting level
    # A GrowthYear source supplied by the loader is never overwritten.
    growth = [GrowthYear(year=3, occupancy_growth=0.01, adr_growth=None, occupancy_source="cbre_horizons", adr_source="withheld_by_policy", label="withheld")]
    out2 = _run(
        topline=_topline(benchmark_by_year=_bench(5, adr=None), growth_by_year=growth),
        adr_growth_by_year=[None, None, None, None, None],
        occupancy_growth_by_year=[None, None, 0.01, None, None],
    )
    assert _by_year(out2)[3].adr_growth_source == "withheld_by_policy"
    # On the legacy path (no benchmark concept) the status is flat_no_growth.
    legacy = _by_year(_run(adr_growth_by_year=[None, None]))
    assert legacy[2].adr_status == "flat_no_growth" and legacy[2].adr_growth_source == "unavailable"
