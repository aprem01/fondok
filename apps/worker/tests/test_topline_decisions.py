"""Domain-lead decisions 2026-11-03 (FON-116 / FON-117), both topline modes.

1a — ADR growth AFTER a forecast series ends is the report's PUBLISHED
     forecast average when it states one (FON-121 decision 2 — Kimpton
     CoStar p.4 +2.5%, the submarket overall, labelled "as published" with
     the page), else the arithmetic average of that series' forecast-tagged
     ADR growth years (labelled "computed", naming the years), never the
     last year carried forward. Exit growth follows the same
     convention when the exit year is past the series. Occupancy past the
     series stays held.
1b — occupancy is FLAT after stabilization: from the stabilization year on it
     holds at its stabilization-year level (growth 0 into every later year,
     source ``held_at_stabilization``) — only an explicit analyst PER-YEAR
     occupancy figure still moves it. ADR keeps growing. Cap 0.95 unchanged.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.services.topline import build_topline, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution
from app.services.topline_growth import GrowthOverrides, resolve_growth_by_year
from app.tenants import TenantDataPolicies
from tests.fixtures.deal_calendar import calendar_deal_years
from tests.test_topline_kimpton_reference import costar_rows
from tests.test_topline_resolvers import cbre_rows

ALLOW = TenantDataPolicies()
CAL7 = list(range(2026, 2033))
#: CoStar Miami Beach Luxury & Upper Upscale ADR change, forecast-tagged years
#: of the Dec-2025 report (p.26): 2025 +4.4%, 2026 +1.8%, 2027 −0.6%, 2028
#: +3.8%, 2029 +2.8% (2024 is tagged actual).
LUU_ADR_FORECAST = {2025: 0.044, 2026: 0.018, 2027: -0.006, 2028: 0.038, 2029: 0.028}
LUU_ADR_AVG = sum(LUU_ADR_FORECAST.values()) / len(LUU_ADR_FORECAST)
#: The report's own "Forecast Average" ADR change (overview p.4, all classes).
PUBLISHED_ADR_AVG = 0.025


def costar_rows_without_published_average() -> list:
    """The same report as extracted before the published-average contract."""
    return [r for r in costar_rows() if not r.lname.startswith("market_study.performance.forecast_avg_")]


def _g(rows, overrides=GrowthOverrides(), *, cal=CAL7, s=None, bucket="luxury_upper_upscale", hold=None):
    return resolve_growth_by_year(
        rows, overrides, deal_years=calendar_deal_years(cal), hold_years=hold or len(cal), class_bucket=bucket,
        policy=ALLOW, stabilization_year=s,
    )


# ─────────────────────────── 1a — post-forecast ADR ───────────────────────────


def test_adr_after_the_costar_series_is_the_published_forecast_average() -> None:
    g = _g(costar_rows())
    for y in (5, 6, 7):  # 2030–2032, past the 2029 forecast end
        adr = g.year(y, "adr")
        assert adr.source == "carried_forward"
        assert adr.average_basis == "published_average"
        assert adr.value == pytest.approx(PUBLISHED_ADR_AVG, abs=1e-15)
        assert adr.label.startswith("Assumption · CoStar forecast average (as published) +2.5%")
        # The published figure is the submarket overall, the rows are the bucket.
        assert "submarket overall; the per-year rows are Luxury & Upper Upscale" in adr.label
        assert "p.4" in adr.label
        assert adr.page == 4 and adr.chip == "CoStar p.4"
        # Occupancy past the series stays held (unchanged rule).
        occ = g.year(y, "occupancy")
        assert occ.source == "carried_forward" and occ.value == 0.0


def test_no_published_average_falls_back_to_the_computed_one() -> None:
    g = _g(costar_rows_without_published_average())
    assert LUU_ADR_AVG == pytest.approx(0.0244)
    for y in (5, 6, 7):
        adr = g.year(y, "adr")
        assert adr.source == "carried_forward"
        assert adr.average_basis == "computed_average"
        assert adr.value == pytest.approx(LUU_ADR_AVG, abs=1e-15)
        assert "2025–2029 forecast average, computed (+2.4%)" in adr.label
        assert adr.label.startswith("Assumption · CoStar Miami Beach submarket")
        assert adr.chip == "CoStar p.26"


def test_the_average_uses_forecast_tagged_years_only() -> None:
    rows = [
        *cbre_rows({2024: (0.0, 0.10)}, period="actual"),
        *cbre_rows({2026: (0.01, 0.02), 2027: (0.0, 0.04)}),
    ]
    g = _g(rows, cal=[2026, 2027, 2028, 2029])
    adr28 = g.year(3, "adr")
    assert adr28.source == "carried_forward"
    assert adr28.value == pytest.approx(0.03, abs=1e-15)  # (2% + 4%) / 2, the 2024 actual is not averaged
    assert "CBRE Hotel Horizons 2026–2027 forecast average, computed (+3.0%)" in adr28.label
    assert "the report publishes no forecast average" in adr28.label


def test_exit_growth_past_the_series_uses_the_forecast_average() -> None:
    rows = costar_rows()
    inputs = read_topline_inputs({}, {}, set())
    g = resolve_growth_by_year(
        rows, inputs.growth, deal_years=calendar_deal_years(CAL7[:5]), hold_years=5,
        class_bucket="luxury_upper_upscale", policy=ALLOW, stabilization_year=3,
    )
    bench = BenchmarkResolution(level="comp_set", label="comp", occupancy=0.75, adr=300.0,
                                base_calendar_year=2025, basis="actual")
    build = build_topline(bench, g, inputs, hold_years=5, t12=None)
    assert build.terminal_revpar_growth == pytest.approx(PUBLISHED_ADR_AVG, abs=1e-15)
    assert build.status["terminal_revpar_growth"]["basis"] == "exit_year_adr_growth"
    assert "forecast average (as published)" in build.status["terminal_revpar_growth"]["label"]
    assert build.status["terminal_revpar_growth"]["chip"] == "CoStar p.4"


def test_exit_growth_is_the_forward_years_growth_past_the_series() -> None:
    """The exit NOI is the year after the hold. A hold ending 2029 sells on
    2030's NOI, which is past the forecast, so the exit grows at the forecast
    average (Sam, 2026-11-03), not at 2029's +2.8%."""
    inputs = read_topline_inputs({}, {}, set())
    g = resolve_growth_by_year(
        costar_rows(), inputs.growth, deal_years=calendar_deal_years([2025, 2026, 2027, 2028, 2029]), hold_years=5,
        class_bucket="luxury_upper_upscale", policy=ALLOW, stabilization_year=3,
    )
    bench = BenchmarkResolution(level="comp_set", label="comp", occupancy=0.75, adr=300.0,
                                base_calendar_year=2025, basis="actual")
    build = build_topline(bench, g, inputs, hold_years=5, t12=None)
    assert build.terminal_revpar_growth == pytest.approx(PUBLISHED_ADR_AVG, abs=1e-15)
    assert "Year 6 (Jan–Dec 2030, the exit-NOI year)" in build.status["terminal_revpar_growth"]["label"]
    assert "forecast average (as published)" in build.status["terminal_revpar_growth"]["label"]


def test_exit_growth_inside_the_series_is_the_forward_year() -> None:
    """A hold ending 2028 sells on 2029's NOI: 2029's own +2.8%."""
    inputs = read_topline_inputs({}, {}, set())
    g = resolve_growth_by_year(
        costar_rows(), inputs.growth, deal_years=calendar_deal_years([2025, 2026, 2027, 2028]), hold_years=4,
        class_bucket="luxury_upper_upscale", policy=ALLOW, stabilization_year=3,
    )
    bench = BenchmarkResolution(level="comp_set", label="comp", occupancy=0.75, adr=300.0,
                                base_calendar_year=2025, basis="actual")
    build = build_topline(bench, g, inputs, hold_years=4, t12=None)
    assert build.terminal_revpar_growth == pytest.approx(0.028)
    assert "Year 5 (Jan–Dec 2029, the exit-NOI year)" in build.status["terminal_revpar_growth"]["label"]


# ─────────────────────────── 1b — occupancy flat after stabilization ───────────────────────────


def test_occupancy_holds_after_the_stabilization_year_adr_keeps_growing() -> None:
    g = _g(costar_rows(), s=3)
    # Years 1–3 (2026–2028): the document forecast, stabilization year included.
    assert [g.year(y, "occupancy").source for y in (1, 2, 3)] == ["costar_submarket_forecast"] * 3
    assert g.year(3, "occupancy").value == pytest.approx(-0.012)
    # Year 4 (2029): CoStar prints +3.0% occupancy — held at the Year-3 level.
    occ4 = g.year(4, "occupancy")
    assert occ4.source == "held_at_stabilization" and occ4.value == 0.0
    assert occ4.source_id == "occupancy_held_at_stabilization"
    assert "held at its Year 3 (Jan–Dec 2028) stabilized level" in occ4.label
    assert "ADR keeps growing" in occ4.label
    for y in (5, 6, 7):
        assert g.year(y, "occupancy").source == "held_at_stabilization"
    # ADR is untouched by the rule.
    assert g.year(4, "adr").source == "costar_submarket_forecast"
    assert g.year(4, "adr").value == pytest.approx(0.028)


def test_an_analyst_per_year_occupancy_figure_still_wins_after_stabilization() -> None:
    g = _g(costar_rows(), GrowthOverrides(occupancy_by_year=(None, None, None, None, 0.005)), s=3)
    occ5 = g.year(5, "occupancy")
    assert occ5.source == "analyst_override" and occ5.value == pytest.approx(0.005)
    assert "Year 5 occupancy growth +0.5%" in occ5.label
    assert g.year(4, "occupancy").source == "held_at_stabilization"


def test_the_all_years_occupancy_scalar_stops_at_stabilization() -> None:
    g = _g([], GrowthOverrides(occupancy_all_years=0.01), cal=[2026, 2027, 2028, 2029, 2030], s=2)
    assert [g.year(y, "occupancy").value for y in range(1, 6)] == [0.01, 0.01, 0.0, 0.0, 0.0]
    assert g.year(3, "occupancy").source == "held_at_stabilization"


def test_stabilization_after_the_hold_holds_nothing() -> None:
    g = _g(costar_rows(), cal=[2026, 2027, 2028], s=4)
    assert all(g.year(y, "occupancy").source == "costar_submarket_forecast" for y in (1, 2, 3))


def test_held_years_do_not_count_as_a_sourced_growth_figure() -> None:
    g = _g([], cal=[2026, 2027, 2028, 2029, 2030], s=2)
    assert g.year(4, "occupancy").source == "held_at_stabilization"
    assert g.state(range(2, 6)) == "unavailable"


def test_the_rule_holds_without_a_calendar_too() -> None:
    g = _g([], GrowthOverrides(occupancy_all_years=0.02, adr_all_years=0.03), cal=[], s=3, hold=5)
    assert [g.year(y, "occupancy").value for y in range(1, 6)] == [0.02, 0.02, 0.02, 0.0, 0.0]
    assert [g.year(y, "adr").value for y in range(1, 6)] == [0.03] * 5
    assert "held at its Year 3 stabilized level" in g.year(4, "occupancy").label


def test_revpar_override_after_stabilization_puts_the_whole_rate_on_adr() -> None:
    """FON-69: ADR is derived over the resolved occupancy — held at 0 after
    stabilization, so ADR carries the analyst's RevPAR growth."""
    g = _g([], GrowthOverrides(revpar_all_years=0.04, occupancy_all_years=0.01), cal=[2026, 2027, 2028, 2029], s=2)
    assert g.year(2, "adr").value == pytest.approx(1.04 / 1.01 - 1)
    assert g.year(3, "adr").value == pytest.approx(0.04)
