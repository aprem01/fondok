"""Sam, 2026-11-04 — which default ramp a deal runs.

An operating hotel moves in a straight line from today's index to the target at
the stabilized year; a new or reopened hotel runs target × 85 / 95 / 100 counted
from the opening. Against a class or market average (not a comp set) the default
target holds today's derived index flat.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest
from fondok_schemas.underwriting import RevenueEngineInput, ToplineReference

from app.engines.revenue import RevenueEngine, default_ramp_by_year
from app.services.topline import build_topline, ramp_mode_for, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution
from app.services.topline_growth import resolve_growth_by_year
from app.tenants import TenantDataPolicies
from tests.fixtures.deal_calendar import calendar_deal_years

CAL = [2026, 2027, 2028, 2029, 2030]
# The Kimpton Angler's STR comp set, TTM May 2025 (MPI 109.8, ARI 75.1).
STR = {"level": "comp_set", "label": "STR comp set", "occupancy": 0.755, "adr": 310.78,
       "base_calendar_year": 2026, "basis": "actual", "published_mpi": 109.8, "published_ari": 75.1}
CLASS = {"level": "submarket", "label": "CoStar submarket L&UU", "occupancy": 0.689, "adr": 409.18,
         "base_calendar_year": 2026, "basis": "actual"}
T12 = ToplineReference(occupancy=0.8326, adr=236.09)


def _run(bench: dict, base: dict | None = None, *, t12=T12, applied: set[str] | None = None, hold: int = 5):
    base = {"acquisition_close_date": "2025-12-31", **(base or {})}
    inputs = read_topline_inputs(base, {}, applied or set())
    g = resolve_growth_by_year([], inputs.growth, deal_years=calendar_deal_years(CAL), hold_years=hold,
                               class_bucket="all", policy=TenantDataPolicies())
    build = build_topline(BenchmarkResolution(**bench), g, inputs, hold_years=hold, t12=t12)
    out = RevenueEngine().run(RevenueEngineInput(
        deal_id="7b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a99", keys=100, starting_occupancy=0.7,
        starting_adr=300.0, hold_years=hold, acquisition_close_date="2025-12-31",
        occupancy_growth=0.0, adr_growth=0.0,
        occupancy_growth_by_year=build.occupancy_growth_by_year,
        adr_growth_by_year=build.adr_growth_by_year, topline=build.drivers,
    ))
    return build, {r.year: r for r in out.topline_by_year}, {y.year: y for y in out.years}


@pytest.mark.parametrize(
    ("base", "mode"),
    [
        ({}, "operating"),
        ({"deal_type": "acquisition"}, "operating"),
        ({"y1_occupancy_displacement_pct": 0.1}, "operating"),
        ({"renovation_displacement_schedule": [{"start_month": 1}]}, "operating"),
        ({"reopening_date": "2027-03-01"}, "new_or_reopened"),
        ({"reopening_date": "2024-06-01", "acquisition_close_date": "2025-12-31"}, "new_or_reopened"),
        ({"reopening_date": "2021-01-01", "acquisition_close_date": "2025-12-31"}, "operating"),
        ({"deal_type": "development"}, "new_or_reopened"),
        ({"deal_type": "Development"}, "new_or_reopened"),
        ({"deal_type": "adaptive_reuse"}, "new_or_reopened"),
    ],
)
def test_which_mode_a_deal_runs(base: dict, mode: str) -> None:
    assert ramp_mode_for(base) == mode


def test_an_operating_hotel_runs_the_straight_line_from_todays_index() -> None:
    build, rec, years = _run(STR)
    assert rec[1].mpi_target == pytest.approx(109.8 - 9.8 / 3)
    assert rec[2].mpi_target == pytest.approx(109.8 - 9.8 * 2 / 3)
    assert rec[3].mpi_target == pytest.approx(100.0)
    assert rec[1].ari_target == pytest.approx(75.1 + 24.9 / 3)
    assert rec[3].ari_target == pytest.approx(100.0)
    assert rec[1].mpi_start_index == pytest.approx(109.8)
    assert rec[3].mpi_start_index is None
    assert rec[1].ramp_state == "straight_line" and rec[1].ramp_pct is None
    # Sam's QA figures, before growth: 80.4 % / $259.
    assert years[1].occupancy == pytest.approx(0.8043, abs=1e-3)
    assert years[1].adr == pytest.approx(259.2, abs=0.1)
    assert build.year1_occupancy == pytest.approx(years[1].occupancy)
    assert build.year1_adr == pytest.approx(years[1].adr)
    assert build.status["ramp_mode"] == "operating"
    assert build.status["ramp_source"] == "straight_line"
    assert build.status["ramp_flag"] is None


def test_a_by_year_business_plan_wins_over_the_straight_line() -> None:
    _, rec, years = _run(
        STR,
        {"index_mpi_target_by_year": [100, 100, 100, 100, 100], "index_ari_target_by_year": [97, 98, 99, 100, 100]},
        applied={"index_mpi_target_by_year", "index_ari_target_by_year"},
    )
    assert rec[1].mpi_target == 100 and rec[1].ari_target == 97
    assert rec[1].mpi_target_source == "by_year"
    assert rec[1].mpi_start_index is None
    assert years[1].occupancy == pytest.approx(0.755)
    assert years[1].adr == pytest.approx(310.78 * 0.97)


def test_an_analyst_ramp_wins_over_the_straight_line() -> None:
    build, rec, _ = _run(STR, {"penetration_ramp_by_year": [0.9, 0.95]}, applied={"penetration_ramp_by_year"})
    assert rec[1].mpi_target == pytest.approx(90.0) and rec[1].ramp_state == "ramped"
    assert build.status["ramp_source"] == "analyst_override"


def test_against_a_class_average_the_derived_index_holds_flat() -> None:
    build, rec, years = _run(CLASS)
    assert build.drivers.penetration_adr_source == "hold_index"
    assert build.drivers.penetration_adr == pytest.approx(236.09 / 409.18 * 100, abs=0.01)
    for y in (1, 2, 3, 4):
        assert rec[y].ari_target == pytest.approx(build.drivers.penetration_adr)
        assert years[y].adr == pytest.approx(236.09, abs=0.05)
    assert build.status["current_ari"]["basis"] == "t12_over_benchmark"
    assert "Derived" in build.status["current_ari"]["label"]


def test_against_a_class_average_an_analyst_target_is_still_the_end_of_the_line() -> None:
    build, rec, _ = _run(CLASS, {"index_ari_target": 70}, applied={"index_ari_target"})
    start = 236.09 / 409.18 * 100
    assert build.drivers.penetration_adr_source == "analyst_override"
    assert rec[1].ari_target == pytest.approx(start + (70 - start) / 3, abs=0.01)
    assert rec[3].ari_target == pytest.approx(70)


def test_a_costar_comp_set_derives_the_index_and_still_targets_100() -> None:
    bench = {**STR, "published_mpi": None, "published_ari": None}
    build, rec, _ = _run(bench)
    assert build.drivers.penetration_adr == 100.0 and build.drivers.penetration_adr_source == "default"
    assert rec[1].ari_start_index == pytest.approx(236.09 / 310.78 * 100, abs=0.01)


def test_operating_with_no_index_takes_the_default_ramp_and_says_so() -> None:
    build, rec, _ = _run(CLASS, t12=None)
    assert rec[1].mpi_target == pytest.approx(85.0) and rec[1].ramp_state == "ramped"
    assert "isn't available" in build.status["ramp_flag"]


def test_a_year_1_entry_leaves_the_other_metric_on_the_line() -> None:
    _, rec, _ = _run(STR, {"starting_adr": 280.0}, applied={"starting_adr"})
    assert rec[1].adr_status == "explicit_value"
    assert rec[1].mpi_target == pytest.approx(109.8 - 9.8 / 3)


def test_stabilization_after_the_hold_never_reaches_the_target() -> None:
    _, rec, _ = _run(STR, {"stabilization_year": 7}, applied={"stabilization_year"}, hold=5)
    assert rec[5].mpi_target == pytest.approx(109.8 - 9.8 * 5 / 7)
    assert rec[5].ramp_state == "stabilization_after_hold"
    assert "never reaches" in (rec[5].reason or "")


def test_a_reopened_hotel_counts_85_95_100_from_the_reopening() -> None:
    # Close 2025-12-31, reopening 2027-03-01: the first full deal year starts
    # 2027-12-31, Year 3 → stabilized in Year 5.
    build, rec, _ = _run(STR, {"reopening_date": "2027-03-01"})
    assert build.drivers.stabilization_year == 5
    assert build.drivers.ramp_start_year == 3
    assert [round(rec[y].ramp_pct, 2) if rec[y].ramp_pct else None for y in range(1, 6)] == [0.85, 0.85, 0.85, 0.95, None]
    assert "before the first full year after opening" in (rec[1].reason or "")
    assert rec[1].mpi_start_index is None
    assert build.status["ramp_mode"] == "new_or_reopened"
    assert "still earning revenue" in build.status["ramp_flag"]
    assert build.status["opening_date_missing"] is False


def test_a_development_with_no_opening_date_counts_from_the_close_and_flags_it() -> None:
    build, rec, _ = _run(STR, {"deal_type": "development"})
    assert [rec[y].ramp_pct for y in (1, 2)] == [0.85, 0.95]
    assert build.status["opening_date_missing"] is True
    assert "No opening date" in build.status["ramp_flag"]


def test_a_development_with_a_date_uses_it() -> None:
    build, _, _ = _run(STR, {"deal_type": "development", "reopening_date": "2027-03-01"})
    assert build.drivers.ramp_start_year == 3 and build.status["opening_date_missing"] is False


def test_a_schedule_clears_the_pre_opening_flag() -> None:
    build, _, _ = _run(STR, {"reopening_date": "2027-03-01", "renovation_displacement_schedule": [{"start_month": 1}]})
    assert build.status["ramp_flag"] is None


def test_a_reopened_hotel_against_a_class_average_holds_the_index_as_its_target() -> None:
    build, rec, _ = _run(CLASS, {"reopening_date": "2026-06-01"})
    assert build.drivers.penetration_adr_source == "hold_index"
    assert rec[1].mpi_start_index is None
    assert rec[1].ari_target == pytest.approx(236.09 / 409.18 * 100 * 0.85, abs=0.01)


def test_a_ground_up_hotel_against_a_class_average_flags_the_100_target() -> None:
    build, _, _ = _run(CLASS, {"deal_type": "development", "reopening_date": "2025-12-15"}, t12=None)
    assert build.drivers.penetration_adr == 100.0
    assert "100% of a class or market average" in build.status["ramp_flag"]


def test_one_metric_without_an_index_is_flagged_and_keeps_its_own_ramp() -> None:
    build, rec, _ = _run(CLASS, t12=ToplineReference(adr=236.09))
    assert rec[1].ari_start_index is not None and rec[1].mpi_start_index is None
    assert rec[1].mpi_target == pytest.approx(85.0)
    assert "occupancy index isn't available" in build.status["ramp_flag"]


def test_a_full_by_year_grid_is_named_as_such_with_no_false_flag() -> None:
    build, rec, _ = _run(
        CLASS,
        {"index_mpi_target_by_year": [100, 100], "index_ari_target_by_year": [97, 98]},
        applied={"index_mpi_target_by_year", "index_ari_target_by_year"},
        t12=None,
    )
    assert build.status["ramp_source"] == "by_year"
    assert build.status["ramp_flag"] is None
    assert rec[1].ramp_state != "straight_line"


def test_both_year_1_rates_typed_raise_no_ramp_flag() -> None:
    build, _, _ = _run(
        CLASS, {"starting_occupancy": 0.7, "starting_adr": 280.0}, applied={"starting_occupancy", "starting_adr"}, t12=None,
    )
    assert build.status["ramp_flag"] is None


def test_a_development_with_no_benchmark_says_what_the_date_is_for() -> None:
    build, _, _ = _run({"level": "none", "label": "No benchmark"}, {"deal_type": "development"}, t12=None)
    assert build.status["opening_date_missing"] is True
    assert "stabilized year counts from the close" in build.status["ramp_flag"]


def test_the_default_ramp_list_counts_from_its_start_year() -> None:
    assert default_ramp_by_year(3) == [0.85, 0.95]
    assert default_ramp_by_year(4) == [0.85, 0.95, None]
    assert default_ramp_by_year(5, 3) == [0.85, 0.85, 0.85, 0.95]
    assert default_ramp_by_year(2, 0) == [0.95]
