"""FON-121 — the topline on DEAL years (twelve months from the close).

Domain lead (Sam, Eshan confirmed), Kimpton test case: close 2025-09-30 →
Year 1 = Oct 2025–Sep 2026, Year 3 = Oct 2027–Sep 2028; stabilization shows
"Year 3 (Oct 2027–Sep 2028)". Market forecasts are published by CALENDAR
year, so a deal year's growth is the month-weighted blend of the calendar
years it spans: Oct 2026–Sep 2027 = 3 months of 2026 + 9 months of 2027.
Past the series, ADR grows at the report's published forecast average
(CoStar p.4, +2.5%) — decision 2 — and every figure carries its page chip
(decision 3).
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.engines.deal_years import deal_years
from app.services.topline import build_growth_only, build_topline, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution
from app.services.topline_growth import GrowthOverrides, growth_year, resolve_growth_by_year
from app.tenants import TenantDataPolicies
from tests.test_topline_kimpton_reference import costar_rows

ALLOW = TenantDataPolicies()
KIMPTON_CLOSE = "2025-09-30"
#: CoStar Miami Beach Luxury & Upper Upscale (p.26), occupancy / ADR change.
LUU = {2025: (None, 0.044), 2026: (-0.012, 0.018), 2027: (0.0, -0.006), 2028: (-0.012, 0.038), 2029: (0.03, 0.028)}
PUBLISHED_ADR = 0.025


def _g(rows=None, *, hold=5, s=None, overrides=GrowthOverrides(), policy=ALLOW, close=KIMPTON_CLOSE):
    return resolve_growth_by_year(
        costar_rows() if rows is None else rows, overrides, deal_years=deal_years(close, hold), hold_years=hold,
        class_bucket="luxury_upper_upscale", policy=policy, stabilization_year=s,
    )


def test_a_deal_year_blends_the_two_calendar_years_it_spans() -> None:
    g = _g()
    adr2 = g.year(2, "adr")  # Oct 2026–Sep 2027
    assert adr2.deal_year is not None and adr2.deal_year.label == "Oct 2026–Sep 2027"
    assert adr2.value == pytest.approx(3 / 12 * 0.018 + 9 / 12 * -0.006, abs=1e-15)
    assert adr2.value == 0.0 and adr2.label.endswith("= +0.0% · CoStar Miami Beach submarket (Luxury & Upper Upscale) · Miami Beach-Hospitality-Submarket-2025-12-10.pdf")
    assert adr2.source == "costar_submarket_forecast"
    assert [(cy, m) for cy, m, _g2 in adr2.parts] == [(2026, 3), (2027, 9)]
    assert adr2.label.startswith("Forecast · Oct 2026–Sep 2027 = 3 mo × +1.8% CoStar 2026 forecast p.26")
    assert "+ 9 mo × -0.6% CoStar 2027 forecast p.26" in adr2.label
    assert adr2.chip == "CoStar p.26"
    occ3 = g.year(3, "occupancy")  # Oct 2027–Sep 2028
    assert occ3.value == pytest.approx(3 / 12 * 0.0 + 9 / 12 * -0.012, abs=1e-15)


def test_the_record_publishes_its_span_and_its_calendar_parts() -> None:
    g = _g()
    rec = growth_year(g, 2)
    assert rec.deal_year is not None and rec.deal_year.label == "Oct 2026–Sep 2027"
    assert [p.model_dump() for p in rec.deal_year.calendar_months] == [
        {"calendar_year": 2026, "months": 3}, {"calendar_year": 2027, "months": 9},
    ]
    assert rec.chip == "CoStar p.26"
    d = g.year(2, "adr").as_dict()
    assert d["chip"] == "CoStar p.26"
    assert d["deal_year"]["label"] == "Oct 2026–Sep 2027"
    assert [(p["calendar_year"], p["months"], p["value"]) for p in d["calendar_parts"]] == [
        (2026, 3, 0.018), (2027, 9, -0.006),
    ]


def test_a_year_straddling_the_forecast_end_blends_in_the_published_average() -> None:
    g = _g()
    adr5 = g.year(5, "adr")  # Oct 2029–Sep 2030: 3 mo of 2029 (+2.8%) + 9 mo past the series
    assert adr5.value == pytest.approx(3 / 12 * 0.028 + 9 / 12 * PUBLISHED_ADR, abs=1e-15)
    assert adr5.source == "carried_forward"  # part of it is past the forecast: an assumption
    assert adr5.average_basis == "published_average"
    assert adr5.label.startswith("Assumption · Oct 2029–Sep 2030")
    assert "+ 9 mo × +2.5% CoStar 2030 at the forecast average as published p.4" in adr5.label
    assert adr5.chip == "CoStar p.26, p.4"


def test_exit_noi_year_is_the_deal_year_after_the_hold_at_the_published_average() -> None:
    inputs = read_topline_inputs({}, {}, set())
    g = _g(s=3)
    fwd = g.forward["adr"]
    assert fwd.deal_year is not None and fwd.deal_year.label == "Oct 2030–Sep 2031"
    assert fwd.value == pytest.approx(PUBLISHED_ADR, abs=1e-15)
    build = build_growth_only(g, inputs, hold_years=5, t12=None)
    term = build.status["terminal_revpar_growth"]
    assert build.terminal_revpar_growth == pytest.approx(PUBLISHED_ADR, abs=1e-15)
    assert "Year 6 (Oct 2030–Sep 2031, the exit-NOI year) ADR growth +2.5%" in term["label"]
    assert term["chip"] == "CoStar p.4"
    assert term["average_basis"] == "published_average"


def test_stabilization_names_the_deal_year_span() -> None:
    g = _g(s=3)
    occ4 = g.year(4, "occupancy")
    assert occ4.source == "held_at_stabilization"
    assert "held at its Year 3 (Oct 2027–Sep 2028) stabilized level" in occ4.label


def test_a_missing_calendar_part_makes_the_deal_year_unavailable_never_half_a_figure() -> None:
    # A series that starts in 2027: Oct 2026–Sep 2027 has no figure for its
    # three 2026 months, so the year has no growth (never 9/12 of one).
    rows = [r for r in costar_rows() if ".2026." not in r.lname and ".2025." not in r.lname]
    g = _g(rows)
    adr2 = g.year(2, "adr")
    assert adr2.value is None and adr2.source == "unavailable"
    assert adr2.label.startswith("Oct 2026–Sep 2027: 3 mo of 2026 — ")


def test_withheld_costar_withholds_every_blended_year() -> None:
    g = _g(policy=TenantDataPolicies(costar_derived_defaults="withhold"))
    adr2 = g.year(2, "adr")
    assert adr2.value is None and adr2.source == "withheld_by_policy"


def test_a_year_end_close_keeps_calendar_years_unblended() -> None:
    g = _g(close="2025-12-31")
    adr1 = g.year(1, "adr")  # Jan–Dec 2026
    assert adr1.deal_year.label == "Jan–Dec 2026"  # type: ignore[union-attr]
    assert adr1.value == pytest.approx(0.018)
    assert adr1.label.startswith("Forecast · CoStar Miami Beach submarket 2026")


def test_benchmark_first_step_is_the_deal_years_month_weighted_level() -> None:
    """A CY-2025 benchmark grown to Year 1 = Oct 2025–Sep 2026: 3 months at
    the 2025 level, 9 months at 2025 × (1 + 2026 growth)."""
    inputs = read_topline_inputs({}, {}, set())
    g = resolve_growth_by_year(
        costar_rows(), inputs.growth, deal_years=deal_years(KIMPTON_CLOSE, 5), hold_years=5,
        class_bucket="luxury_upper_upscale", policy=ALLOW, benchmark_base_year=2025, stabilization_year=3,
    )
    bench = BenchmarkResolution(level="comp_set", label="comp 2025", occupancy=0.75, adr=300.0,
                                base_calendar_year=2025, basis="actual")
    build = build_topline(bench, g, inputs, hold_years=5, t12=None)
    by = build.drivers.benchmark_by_year  # type: ignore[union-attr]
    assert by[0].deal_year is not None and by[0].deal_year.label == "Oct 2025–Sep 2026"
    assert by[0].adr == pytest.approx(300.0 * (3 / 12 + 9 / 12 * 1.018), rel=1e-12)
    assert by[0].occupancy == pytest.approx(0.75 * (3 / 12 + 9 / 12 * (1 - 0.012)), rel=1e-12)
    # Year 2 on: the previous deal year × the deal year's blended growth.
    assert by[1].adr == pytest.approx(by[0].adr * (1 + g.year(2, "adr").value), rel=1e-12)  # type: ignore[operator]


def test_a_blend_shows_the_precision_it_applies() -> None:
    """Review M1 — +3.05% is shown as +3.05%, and the label's arithmetic foots."""
    adr4 = _g().year(4, "adr")  # Oct 2028–Sep 2029
    assert adr4.value == pytest.approx(0.0305, abs=1e-12)
    assert adr4.label.startswith("Forecast · Oct 2028–Sep 2029 = 3 mo × +3.8% CoStar 2028 forecast p.26 + 9 mo × +2.8% CoStar 2029 forecast p.26 = +3.05%")
    adr5 = _g().year(5, "adr")
    assert "= +2.575%" in adr5.label


def test_a_blend_across_sources_chips_each_part_with_its_own_vendor() -> None:
    """Review M2 — CBRE ends 2026, CoStar carries 2027: Oct 2026–Sep 2027 reads
    "CBRE p.4 · CoStar p.26", never CBRE for CoStar's page."""
    from tests.test_topline_resolvers import cbre_rows

    rows = [*cbre_rows({2025: (0.01, 0.02), 2026: (0.01, 0.02)}), *costar_rows()]
    adr2 = _g(rows).year(2, "adr")
    assert adr2.chip == "CBRE p.4 · CoStar p.26"


def test_no_close_date_with_an_as_of_keeps_the_calendar_year_placement() -> None:
    """Review M5 — the assumed (as-of) calendar places forecasts on CALENDAR
    years as before: Year 1 = the calendar year of the first operating month."""
    from datetime import date

    from app.services.topline import calendar_anchor

    a = calendar_anchor(None, underwriting_as_of=date(2025, 6, 30))
    assert a.assumed and [y.label for y in a.years(3)] == ["Jan–Dec 2025", "Jan–Dec 2026", "Jan–Dec 2027"]
    a = calendar_anchor(None, underwriting_as_of=date(2025, 12, 15))
    assert a.years(1)[0].label == "Jan–Dec 2026"
    # A real close keeps deal years.
    assert calendar_anchor(date(2025, 6, 30), underwriting_as_of=None).years(1)[0].label == "Jul 2025–Jun 2026"
