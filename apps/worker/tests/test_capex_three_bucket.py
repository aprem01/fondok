"""Tests for Wave 2 P2.5 - Capex three-bucket split (PIP / Non-PIP / ROI).

The three buckets are:

* PIP - brand-mandated, total dollar amount phased over Y1+ via
  ``timing_pct_by_year`` (sums to 1.0).
* Non-PIP - ongoing FF&E reserve as % of revenue with a per-key floor.
* ROI capex - discretionary investments with their own NOI lift curve.

The helper materializes a per-year capex schedule and a NOI-lift
overlay; backward compat is guaranteed by keeping ``CapexPlan`` defaults
no-op against the legacy ``ffe_reserve_pct`` flow on the Expense engine.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.engines.capex_plan import apply_roi_lift_to_noi, build_capex_schedule
from fondok_schemas.underwriting import (
    CapexPlan,
    NonPIPCapex,
    PIPCapex,
    ROICapex,
)


# 200-key Marriott select-service deal — used across the spec example.
KEYS = 200
HOLD = 5
REVENUE_BY_YEAR = [15_000_000.0, 16_000_000.0, 17_000_000.0, 18_000_000.0, 19_000_000.0]


def test_default_capex_plan_matches_legacy_reserve_pct() -> None:
    """Empty CapexPlan with default 4% reserve must render Non-PIP
    figures that match what the Expense engine would deduct via
    ``ffe_reserve_pct`` (legacy backward-compat sanity)."""
    plan = CapexPlan()
    assert plan.pip is None
    assert plan.non_pip.annual_pct_of_revenue == pytest.approx(0.04)
    assert plan.roi_projects == []
    schedule = build_capex_schedule(
        plan, hold_years=HOLD, revenue_by_year=REVENUE_BY_YEAR, room_count=KEYS
    )
    # Y1 non-pip = max(4% x 15M, 200 x 1500) = max(600K, 300K) = 600K
    assert schedule[0].non_pip_usd == pytest.approx(600_000.0)
    # Legacy ffe_reserve_pct x revenue = exact same number.
    assert schedule[0].non_pip_usd == pytest.approx(
        REVENUE_BY_YEAR[0] * plan.non_pip.annual_pct_of_revenue
    )
    # No PIP, no ROI - total = non-pip only.
    for sched in schedule:
        assert sched.pip_usd == 0.0
        assert sched.roi_investment_usd == 0.0
        assert sched.roi_noi_lift_usd == 0.0
        assert sched.total_capex_usd == pytest.approx(sched.non_pip_usd)


def test_pip_phasing_40_60_split() -> None:
    """$5M PIP phased 40% Y1 / 60% Y2 -> Y1=$2M, Y2=$3M, Y3+=0."""
    plan = CapexPlan(
        pip=PIPCapex(total_usd=5_000_000.0, timing_pct_by_year=[0.4, 0.6])
    )
    schedule = build_capex_schedule(
        plan, hold_years=HOLD, revenue_by_year=REVENUE_BY_YEAR, room_count=KEYS
    )
    assert schedule[0].pip_usd == pytest.approx(2_000_000.0)
    assert schedule[1].pip_usd == pytest.approx(3_000_000.0)
    assert schedule[2].pip_usd == pytest.approx(0.0)
    assert schedule[3].pip_usd == pytest.approx(0.0)


def test_pip_phasing_sum_validation_rejects_imbalanced() -> None:
    """[0.3, 0.4] sums to 0.7 - must raise ValidationError, not run."""
    with pytest.raises(ValidationError) as exc_info:
        PIPCapex(total_usd=5_000_000.0, timing_pct_by_year=[0.3, 0.4])
    # Spec wants the validator to call out the sum-to-one constraint.
    assert "sum to 1.0" in str(exc_info.value)


def test_non_pip_floor_kicks_in_below_minimum() -> None:
    """Small revenue ($1M) x 4% = $40K is below the 200 x $1500 = $300K
    per-key floor. Result must be the floor."""
    plan = CapexPlan()  # default non_pip 4% + 1500/key
    schedule = build_capex_schedule(
        plan, hold_years=1, revenue_by_year=[1_000_000.0], room_count=200
    )
    # 4% x 1M = 40K; 200 x 1500 = 300K. Floor wins.
    assert schedule[0].non_pip_usd == pytest.approx(300_000.0)


def test_non_pip_pct_dominates_above_floor() -> None:
    """$20M revenue x 4% = $800K beats the 200 x $1500 = $300K floor.
    Result must be the % of revenue."""
    plan = CapexPlan()
    schedule = build_capex_schedule(
        plan, hold_years=1, revenue_by_year=[20_000_000.0], room_count=200
    )
    assert schedule[0].non_pip_usd == pytest.approx(800_000.0)


def test_roi_lift_starts_year_after_investment() -> None:
    """Invest Y2 -> NOI lift starts Y3 month 1; Y1 and Y2 lift = 0."""
    project = ROICapex(
        project_name="Solar + battery",
        initial_investment_usd=1_500_000.0,
        investment_year=2,
        annual_noi_lift_usd=250_000.0,
        ramp_months=12,
    )
    plan = CapexPlan(roi_projects=[project])
    schedule = build_capex_schedule(
        plan, hold_years=HOLD, revenue_by_year=REVENUE_BY_YEAR, room_count=KEYS
    )
    # Y1: no investment, no lift
    assert schedule[0].roi_investment_usd == pytest.approx(0.0)
    assert schedule[0].roi_noi_lift_usd == pytest.approx(0.0)
    # Y2: investment hits, no lift yet
    assert schedule[1].roi_investment_usd == pytest.approx(1_500_000.0)
    assert schedule[1].roi_noi_lift_usd == pytest.approx(0.0)
    # Y3: lift starts ramping
    assert schedule[2].roi_noi_lift_usd > 0


def test_roi_lift_ramps_linearly_over_12_months() -> None:
    """ramp_months=12: first full year after investment averages 50% of
    annual lift (linear ramp from 0 to 100% over 12 months)."""
    project = ROICapex(
        project_name="Solar",
        initial_investment_usd=1_500_000.0,
        investment_year=2,
        annual_noi_lift_usd=250_000.0,
        ramp_months=12,
    )
    plan = CapexPlan(roi_projects=[project])
    schedule = build_capex_schedule(
        plan, hold_years=HOLD, revenue_by_year=REVENUE_BY_YEAR, room_count=KEYS
    )
    # Y3 = first year after investment_year=2; ramp goes 0% -> 100% over
    # 12 months; average = 50% x 250K = 125K.
    assert schedule[2].roi_noi_lift_usd == pytest.approx(125_000.0)


def test_roi_lift_full_after_ramp_period() -> None:
    """Y4 onward (ramp done) -> full ``annual_noi_lift_usd``."""
    project = ROICapex(
        project_name="Solar",
        initial_investment_usd=1_500_000.0,
        investment_year=2,
        annual_noi_lift_usd=250_000.0,
        ramp_months=12,
    )
    plan = CapexPlan(roi_projects=[project])
    schedule = build_capex_schedule(
        plan, hold_years=HOLD, revenue_by_year=REVENUE_BY_YEAR, room_count=KEYS
    )
    # Y4, Y5 = fully ramped.
    assert schedule[3].roi_noi_lift_usd == pytest.approx(250_000.0)
    assert schedule[4].roi_noi_lift_usd == pytest.approx(250_000.0)


def test_multiple_roi_projects_aggregate() -> None:
    """Two ROI projects -> per-year lift is the SUM across projects."""
    p1 = ROICapex(
        project_name="Solar",
        initial_investment_usd=1_500_000.0,
        investment_year=2,
        annual_noi_lift_usd=250_000.0,
        ramp_months=12,
    )
    p2 = ROICapex(
        project_name="F&B build-out",
        initial_investment_usd=800_000.0,
        investment_year=2,
        annual_noi_lift_usd=150_000.0,
        ramp_months=12,
    )
    plan = CapexPlan(roi_projects=[p1, p2])
    schedule = build_capex_schedule(
        plan, hold_years=HOLD, revenue_by_year=REVENUE_BY_YEAR, room_count=KEYS
    )
    # Y2 investment = 1.5M + 0.8M = 2.3M
    assert schedule[1].roi_investment_usd == pytest.approx(2_300_000.0)
    # Y3 lift = 125K (Solar) + 75K (F&B half-ramped) = 200K
    assert schedule[2].roi_noi_lift_usd == pytest.approx(200_000.0)
    # Y4 fully ramped = 250K + 150K = 400K
    assert schedule[3].roi_noi_lift_usd == pytest.approx(400_000.0)


def test_legacy_capex_reserve_pct_still_works_when_capex_plan_default() -> None:
    """Pure backward-compat sanity: a CapexPlan() with NO PIP and NO
    ROI projects produces a schedule whose total_capex equals the
    non-pip line — same dollars the Expense engine already deducts
    via ``ffe_reserve_pct``."""
    plan = CapexPlan()
    schedule = build_capex_schedule(
        plan, hold_years=HOLD, revenue_by_year=REVENUE_BY_YEAR, room_count=KEYS
    )
    # Each year: total_capex == non_pip_usd (PIP and ROI both 0).
    for sched, rev in zip(schedule, REVENUE_BY_YEAR):
        expected = max(rev * 0.04, KEYS * 1500.0)
        assert sched.total_capex_usd == pytest.approx(expected)
    # And the ROI NOI overlay is a no-op on plain NOI series.
    noi_series = [3_000_000.0, 3_100_000.0, 3_200_000.0, 3_300_000.0, 3_400_000.0]
    adjusted = apply_roi_lift_to_noi(noi_series, plan)
    assert adjusted == noi_series


def test_source_provenance_pip_om_propagates() -> None:
    """A PIP seeded from an OM extraction must carry source=``pip_om``
    end-to-end (schema-level test - the loader stamps the constant,
    here we verify the field actually survives Pydantic round-trip)."""
    from app.services.engine_runner import SOURCE_PIP_OM

    pip = PIPCapex(
        total_usd=8_000_000.0,
        timing_pct_by_year=[0.5, 0.5],
        source=SOURCE_PIP_OM,
    )
    plan = CapexPlan(pip=pip)
    # Round-trip through JSON to mirror what the engine_runner does
    # when persisting / re-loading the assumption tree.
    blob = plan.model_dump_json()
    re_plan = CapexPlan.model_validate_json(blob)
    assert re_plan.pip is not None
    assert re_plan.pip.source == SOURCE_PIP_OM
    assert re_plan.pip.source == "pip_om"
