"""Tests for the expense engine's T-12 override path.

Pins Sam QA #1: when a deal has extracted T-12 expense lines, the
engine MUST use them as the Year-1 anchor instead of synthesizing
from USALI benchmark ratios. The synthesized path also can't have
two USALI lines (S&M and Utilities) collapsed onto the same
multiplier — they were both 0.24 before and produced identical
$905K figures on Sam's deal.

These tests exercise the engine in isolation (no DB, no agent loop)
so they're fast and pin the math contract directly.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from app.engines.fb_revenue import FBRevenueOutput, FBRevenueYear


def _revenue_payload(*, total: float = 13_600_000.0) -> FBRevenueOutput:
    """Five-year flat revenue payload sized to a typical mid-scale hotel."""
    rooms = total * 0.74
    fb = total * 0.22
    other = total - rooms - fb
    years = [
        FBRevenueYear(
            year=y,
            rooms_revenue=rooms,
            fb_revenue=fb,
            other_revenue=other,
            total_revenue=total,
        )
        for y in range(1, 6)
    ]
    return FBRevenueOutput(
        deal_id=uuid4(),
        years=years,
        fb_ratio_used=0.22,
        other_ratio_used=0.04,
    )


def _base_input(
    *, t12_actuals: dict[str, float] | None = None
) -> ExpenseEngineInput:
    return ExpenseEngineInput(
        deal_id=uuid4(),
        revenue=_revenue_payload(),
        hotel_type="full",
        mgmt_fee_pct=0.03,
        ffe_reserve_pct=0.04,
        expense_growth=0.035,
        grow_opex_independently=True,
        t12_actuals=t12_actuals or {},
    )


def test_synthesized_y1_no_sm_utilities_collision() -> None:
    """Without T-12 actuals the engine still must NOT produce identical
    S&M and Utilities figures — that was Sam's specific tell ($905K = $905K)
    that the formula had collapsed two distinct USALI lines onto the
    same multiplier.
    """
    out = ExpenseEngine().run(_base_input())
    y1 = out.years[0]
    assert y1.undistributed.sales_marketing > 0
    assert y1.undistributed.utilities > 0
    assert y1.undistributed.sales_marketing != y1.undistributed.utilities, (
        "S&M and Utilities must use distinct ratio weights"
    )
    # Sourced list should be empty when no actuals supplied.
    assert out.sourced_from_t12 == []


def test_t12_actuals_override_y1_undistributed_lines() -> None:
    """When T-12 actuals are supplied, every Y1 line that has a key in
    ``t12_actuals`` must take its value from the override — not from a
    benchmark ratio. Out-years still grow the Y1 anchor at expense_growth.
    """
    actuals = {
        "sales_marketing": 800_000.0,
        "utilities": 290_000.0,
        "administrative_general": 600_000.0,
    }
    out = ExpenseEngine().run(_base_input(t12_actuals=actuals))

    y1 = out.years[0]
    assert y1.undistributed.sales_marketing == pytest.approx(800_000.0)
    assert y1.undistributed.utilities == pytest.approx(290_000.0)
    assert y1.undistributed.administrative_general == pytest.approx(600_000.0)
    # Lines that weren't supplied (information_telecom, property_operations)
    # still come from the synthesized share of the undistributed pool.
    assert y1.undistributed.information_telecom > 0
    assert y1.undistributed.property_operations > 0

    # Out-years grow the Y1 anchor at expense_growth, not the benchmark.
    y2 = out.years[1]
    growth = (1 + 0.035) ** 1
    assert y2.undistributed.sales_marketing == pytest.approx(800_000.0 * growth)
    assert y2.undistributed.utilities == pytest.approx(290_000.0 * growth)

    # sourced_from_t12 lists every override key, sorted.
    assert out.sourced_from_t12 == sorted(actuals.keys())


def test_t12_actuals_override_fixed_charges_and_fees() -> None:
    """Insurance, property taxes, mgmt fee, FF&E reserve all take T-12
    values when supplied. Sam's deal showed Insurance $457K (synth) vs
    actual $1.16M — that's the gap this assertion pins shut.
    """
    actuals = {
        "insurance": 1_160_000.0,
        "property_taxes": 850_000.0,
        "mgmt_fee": 410_000.0,
        "ffe_reserve": 540_000.0,
    }
    out = ExpenseEngine().run(_base_input(t12_actuals=actuals))
    y1 = out.years[0]
    assert y1.fixed_charges.insurance == pytest.approx(1_160_000.0)
    assert y1.fixed_charges.property_taxes == pytest.approx(850_000.0)
    assert y1.mgmt_fee == pytest.approx(410_000.0)
    assert y1.ffe_reserve == pytest.approx(540_000.0)
    assert set(out.sourced_from_t12) == set(actuals.keys())


def test_t12_actuals_partial_override_falls_back_to_ratio() -> None:
    """When only some lines are supplied, the rest still come from the
    synthesized share — graceful degradation per partial extraction."""
    actuals = {"insurance": 1_160_000.0}  # only insurance; nothing else
    out = ExpenseEngine().run(_base_input(t12_actuals=actuals))
    y1 = out.years[0]
    assert y1.fixed_charges.insurance == pytest.approx(1_160_000.0)
    # Property taxes still synthesized — non-zero, derived from the
    # fixed-charges pool weighted share (0.55 of pool).
    assert y1.fixed_charges.property_taxes > 0
    # Departmental wasn't touched; still synthesized from defaults.
    assert y1.dept_expenses.rooms > 0
    assert y1.dept_expenses.food_beverage > 0
    assert out.sourced_from_t12 == ["insurance"]
