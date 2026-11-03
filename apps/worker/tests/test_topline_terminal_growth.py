"""Review I-2 (W5 topline fix) — the exit / terminal growth and the
carry-forward after a forecast ends.

Before: Returns' terminal growth was the last projected year's
``(1 + g_occ)(1 + g_adr) − 1``. On the CoStar Luxury & Upper Upscale path
Year 5 is 2029 (or carried from it): a recovery year, +3.0% occupancy and
+2.8% ADR, so the exit NOI grew at 5.884% for ever — above the retired 4.5%
seed, and mostly from occupancy, which cannot compound in perpetuity. The
carry-forward likewise repeated +3.0% occupancy every year after the report.

Now (methodology, 02-projection "Exit growth"):

* after a document forecast ends, ADR growth carries forward (labelled
  assumption) and occupancy HOLDS at its last level (growth 0, labelled) —
  the 95% cap still applies;
* the terminal growth, when the analyst set no ``revpar_growth``, is the
  source's long-run average (CBRE Hotel Horizons long-run RevPAR change,
  else its long-run ADR change) when one is on file, else the last projected
  year's ADR growth — occupancy is held at its stabilized level — else 0.0
  (``market_forecast_unavailable``).
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.services.topline import LongRunGrowth, build_topline, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution
from app.services.topline_growth import GrowthOverrides, resolve_growth_by_year
from app.tenants import TenantDataPolicies
from tests.fixtures.deal_calendar import calendar_deal_years
from tests.test_topline_kimpton_reference import costar_rows

CAL = [2026, 2027, 2028, 2029, 2030, 2031, 2032]


def _bench() -> BenchmarkResolution:
    return BenchmarkResolution(
        level="comp_set", label="Comp set 2025", occupancy=0.70, adr=300.0,
        base_calendar_year=2025, basis="actual",
    )


def _growth(overrides: GrowthOverrides = GrowthOverrides(), *, cal=CAL):
    return resolve_growth_by_year(
        costar_rows(), overrides, deal_years=calendar_deal_years(cal), hold_years=len(cal),
        class_bucket="luxury_upper_upscale", policy=TenantDataPolicies(), benchmark_base_year=2025,
    )


def test_after_the_forecast_ends_adr_averages_and_occupancy_holds() -> None:
    g = _growth()
    # 2029 is CoStar's last forecast year (+3.0% occupancy, +2.8% ADR).
    assert g.year(4, "occupancy").value == pytest.approx(0.03)
    for y in (5, 6, 7):  # 2030–2032
        occ, adr = g.year(y, "occupancy"), g.year(y, "adr")
        # Decision 1a / FON-121 decision 2: the report's published forecast
        # average (+2.5%, p.4), not 2029's +2.8%.
        assert adr.source == "carried_forward" and adr.value == pytest.approx(0.025)
        assert occ.source == "carried_forward" and occ.value == 0.0
        assert "held at its 2029 level" in occ.label
        assert "CoStar forecast average (as published) +2.5%" in adr.label


def test_terminal_growth_is_the_last_years_adr_growth_without_occupancy() -> None:
    inputs = read_topline_inputs({}, {}, set())
    build = build_topline(_bench(), _growth(cal=CAL[:3]), inputs, hold_years=3, t12=None)
    # Hold ends 2028; the exit NOI is 2029's: the recovery year's +3.0%
    # occupancy does NOT enter.
    assert build.terminal_revpar_growth == pytest.approx(0.028)
    assert build.terminal_revpar_source == "costar_submarket_forecast"
    term = build.status["terminal_revpar_growth"]
    assert term["value"] == pytest.approx(0.028)
    assert term["basis"] == "exit_year_adr_growth"
    assert "occupancy held" in term["label"]


def test_exit_growth_follows_the_post_forecast_rule_even_with_a_long_run_average_on_file() -> None:
    """FON-121 review M3 — the exit-NOI year uses the SAME rule as every
    post-forecast year (the report's published average, else the computed
    one); a CBRE long-run average does not override it."""
    inputs = read_topline_inputs({}, {}, set())
    long_run = LongRunGrowth(value=0.031, source_id="cbre_horizons", label="CBRE Hotel Horizons long-run average RevPAR change 3.1%")
    build = build_topline(_bench(), _growth(cal=CAL[:5]), inputs, hold_years=5, t12=None, long_run=long_run)
    assert build.terminal_revpar_growth == pytest.approx(0.025)
    assert build.status["terminal_revpar_growth"]["basis"] == "exit_year_adr_growth"
    assert build.status["terminal_revpar_growth"]["average_basis"] == "published_average"


def test_the_long_run_average_applies_only_when_no_exit_year_figure_exists() -> None:
    inputs = read_topline_inputs({}, {}, set())
    long_run = LongRunGrowth(value=0.031, source_id="cbre_horizons", label="CBRE Hotel Horizons long-run average RevPAR change 3.1%")
    g = resolve_growth_by_year(
        [], inputs.growth, deal_years=calendar_deal_years(CAL[:5]), hold_years=5,
        class_bucket="all", policy=TenantDataPolicies(),
    )
    build = build_topline(_bench(), g, inputs, hold_years=5, t12=None, long_run=long_run)
    assert build.terminal_revpar_growth == pytest.approx(0.031)
    assert build.terminal_revpar_source == "cbre_horizons"
    assert build.status["terminal_revpar_growth"]["basis"] == "long_run_average"


def test_terminal_growth_with_an_analyst_last_year_adr_uses_it() -> None:
    inputs = read_topline_inputs(
        {"adr_growth_by_year": [None, None, None, None, 0.02], "occupancy_growth_by_year": [None] * 4 + [0.005]},
        {}, {"adr_growth_by_year", "occupancy_growth_by_year"},
    )
    build = build_topline(_bench(), _growth(inputs.growth, cal=CAL[:5]), inputs, hold_years=5, t12=None)
    assert build.terminal_revpar_growth == pytest.approx(0.02)
    assert build.terminal_revpar_source == "analyst_override"


def test_no_figure_at_all_is_zero_and_unavailable() -> None:
    inputs = read_topline_inputs({}, {}, set())
    g = resolve_growth_by_year(
        [], inputs.growth, deal_years=calendar_deal_years(CAL[:5]), hold_years=5,
        class_bucket="all", policy=TenantDataPolicies(),
    )
    build = build_topline(_bench(), g, inputs, hold_years=5, t12=None)
    assert build.terminal_revpar_growth == 0.0
    assert build.terminal_revpar_source == "market_forecast_unavailable"
    assert build.status["terminal_revpar_growth"]["basis"] == "unavailable"


def test_exit_growth_past_the_forecast_is_the_forecast_average() -> None:
    """Sam, 2026-11-03: a hold ending 2029 sells on 2030's NOI, past the
    CoStar forecast, so the exit grows at the forecast average."""
    inputs = read_topline_inputs({}, {}, set())
    build = build_topline(_bench(), _growth(cal=CAL[:4]), inputs, hold_years=4, t12=None)
    term = build.status["terminal_revpar_growth"]
    assert term["basis"] == "exit_year_adr_growth"
    assert "Year 5 (Jan–Dec 2030, the exit-NOI year)" in term["label"] and "forecast average (as published)" in term["label"]
    assert build.terminal_revpar_growth == pytest.approx(0.025, abs=1e-12)


def test_a_carried_analyst_figure_names_the_real_exit_noi_year() -> None:
    """The analyst's Year-5 ADR carries to the exit; the label names the
    exit-NOI year (2031 for a 2026–2030 hold), not the hold's last year."""
    inputs = read_topline_inputs(
        {"adr_growth_by_year": [None, None, None, None, 0.02]}, {}, {"adr_growth_by_year"},
    )
    build = build_topline(_bench(), _growth(inputs.growth, cal=CAL[:5]), inputs, hold_years=5, t12=None)
    assert build.terminal_revpar_growth == pytest.approx(0.02)
    assert "Year 6 (Jan–Dec 2031, the exit-NOI year)" in build.status["terminal_revpar_growth"]["label"]
