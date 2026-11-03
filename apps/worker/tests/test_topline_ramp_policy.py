"""Review I-1 (W5 topline fix) — WHICH deals the default 85 / 95 ramp applies to
is ONE named policy point (``services.topline.DEFAULT_RAMP_POLICY``), so the
domain lead's decision is a one-line change.

Today's value is ``all_deals`` (Sam, 2026-11-01: 100 % penetration at
stabilization with an 85 / 95 / 100 ramp). The alternative the review raised —
the ramp is a lease-up concept, so apply it only to a repositioning — is
``repositioning_only`` (a reopening date or a renovation displacement on the
deal); ``never`` applies the stabilized penetration from Year 1. These tests
pin every option's behaviour without changing the default.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

import pytest

from app.engines.revenue import RevenueEngine
from app.services import topline as tl
from app.services.topline import build_topline, default_ramp_applies, read_topline_inputs
from app.services.topline_benchmark import BenchmarkResolution
from app.services.topline_growth import resolve_growth_by_year
from app.tenants import TenantDataPolicies
from tests.fixtures.deal_calendar import calendar_deal_years
from fondok_schemas.underwriting import RevenueEngineInput

CAL = [2026, 2027, 2028, 2029, 2030]


def _bench() -> BenchmarkResolution:
    return BenchmarkResolution(
        level="comp_set", label="Comp set 2026", occupancy=0.70, adr=300.0,
        base_calendar_year=2026, basis="actual",
    )


def _run(base: dict, policy: str):
    inputs = read_topline_inputs(base, {}, set(), ramp_policy=policy)
    g = resolve_growth_by_year([], inputs.growth, deal_years=calendar_deal_years(CAL), hold_years=5,
                               class_bucket="all", policy=TenantDataPolicies())
    build = build_topline(_bench(), g, inputs, hold_years=5, t12=None)
    out = RevenueEngine().run(RevenueEngineInput(
        deal_id="7b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a99", keys=100, starting_occupancy=0.7,
        starting_adr=300.0, hold_years=5, acquisition_close_date="2025-12-31",
        occupancy_growth=0.0, adr_growth=0.0,
        occupancy_growth_by_year=build.occupancy_growth_by_year,
        adr_growth_by_year=build.adr_growth_by_year, topline=build.drivers,
    ))
    return build, out


def test_the_policy_point_is_one_named_constant_and_defaults_to_all_deals() -> None:
    assert tl.DEFAULT_RAMP_POLICY == "all_deals"
    assert set(tl.RAMP_POLICIES) == {"all_deals", "repositioning_only", "never"}


@pytest.mark.parametrize(
    ("policy", "base", "applies"),
    [
        ("all_deals", {}, True),
        ("never", {"reopening_date": "2026-03-01"}, False),
        ("repositioning_only", {}, False),
        ("repositioning_only", {"reopening_date": "2026-03-01"}, True),
        ("repositioning_only", {"y1_occupancy_displacement_pct": 0.15}, True),
        ("repositioning_only", {"renovation_displacement_schedule": [{"start_month": 1}]}, True),
        ("repositioning_only", {"y1_occupancy_displacement_pct": 0.0}, False),
    ],
)
def test_which_deals_the_default_ramp_applies_to(policy, base, applies) -> None:
    assert default_ramp_applies(base, policy) is applies


def test_all_deals_runs_the_85_95_ramp() -> None:
    build, out = _run({}, "all_deals")
    assert build.status["ramp_by_year"] == [0.85, 0.95]
    assert build.status["ramp_source"] == "default"
    assert build.status["ramp_policy"] == "all_deals"
    assert out.years[0].occupancy == pytest.approx(0.70 * 0.85)
    assert out.years[2].occupancy == pytest.approx(0.70)


def test_a_deal_outside_the_policy_runs_the_stabilized_penetration_from_year_one() -> None:
    build, out = _run({}, "repositioning_only")
    assert build.status["ramp_by_year"] == []
    assert build.status["ramp_source"] == "not_applied_by_policy"
    assert build.status["ramp_policy"] == "repositioning_only"
    for y in out.years:
        assert y.occupancy == pytest.approx(0.70)
        assert y.adr == pytest.approx(300.0)
    rec = out.topline_by_year[0]
    assert rec.ramp_pct is None
    assert "ramp policy" in (rec.reason or "")


def test_an_analyst_ramp_always_wins_over_the_policy() -> None:
    base = {"penetration_ramp_by_year": [0.9, 0.97]}
    inputs = read_topline_inputs(base, {}, {"penetration_ramp_by_year"}, ramp_policy="never")
    assert inputs.ramp == (0.9, 0.97)
