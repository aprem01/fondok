"""Departmental expense rides its own department's revenue (Sam, Kimpton
Angler finding 2, 2026-11).

Prod showed F&B departmental expense at 130% of F&B revenue: the Year-1 T-12
expense dollars sat against a renovation-displaced revenue, and every later
year grew the dollars at 3.5% while F&B revenue grew ~0.8%. The USALI
convention is a departmental cost ratio to the department's own revenue:

* the ratio is fixed in Year 1 — the T-12 departmental expense ÷ the T-12
  revenue of that department when the T-12 states both, else the hotel-type
  (or applied benchmark) ratio;
* every year, Year 1 included, departmental expense = that ratio × the year's
  projected department revenue, so a displaced year's expense is displaced
  the same way its revenue is.

Undistributed and fixed charges keep their dollar growth.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from app.engines.fb_revenue import FBRevenueOutput, FBRevenueYear

ROOMS = [10_000_000.0, 10_800_000.0, 11_500_000.0, 12_100_000.0, 12_600_000.0]
FB = [2_000_000.0, 2_100_000.0, 2_150_000.0, 2_200_000.0, 2_260_000.0]
OTHER = [600_000.0, 650_000.0, 690_000.0, 720_000.0, 760_000.0]


def _revenue(
    rooms: list[float] = ROOMS, fb: list[float] = FB, other: list[float] = OTHER
) -> FBRevenueOutput:
    return FBRevenueOutput(
        deal_id=uuid4(),
        years=[
            FBRevenueYear(
                year=i + 1,
                rooms_revenue=r,
                fb_revenue=f,
                other_revenue=o,
                total_revenue=r + f + o,
            )
            for i, (r, f, o) in enumerate(zip(rooms, fb, other))
        ],
        fb_ratio_used=0.2,
        other_ratio_used=0.06,
    )


def _run(**kw) -> object:
    payload = {
        "deal_id": uuid4(),
        "revenue": _revenue(),
        "hotel_type": "full",
        "expense_growth": 0.035,
        "grow_opex_independently": True,
        **kw,
    }
    return ExpenseEngine().run(ExpenseEngineInput(**payload))


def test_out_year_departmental_expense_is_the_ratio_to_its_own_revenue() -> None:
    out = _run()
    # full-service defaults: rooms 30%, F&B 75%, other 50%.
    for i, y in enumerate(out.years):
        assert y.dept_expenses.rooms == pytest.approx(ROOMS[i] * 0.30)
        assert y.dept_expenses.food_beverage == pytest.approx(FB[i] * 0.75)
        assert y.dept_expenses.other_operated == pytest.approx(OTHER[i] * 0.50)


def test_undistributed_and_fixed_keep_dollar_growth() -> None:
    out = _run()
    y1, y3 = out.years[0], out.years[2]
    assert y3.undistributed.total == pytest.approx(y1.undistributed.total * 1.035**2)
    assert y3.fixed_charges.total == pytest.approx(y1.fixed_charges.total * 1.035**2)


def test_t12_ratio_sets_every_year_including_year_one() -> None:
    # T-12: F&B expense 2.61M on F&B revenue 3.39M (77%).
    out = _run(
        t12_actuals={"fb_dept_expense": 2_610_000.0},
        t12_dept_revenue={"fb_revenue": 3_390_000.0},
    )
    ratio = 2_610_000.0 / 3_390_000.0
    for i, y in enumerate(out.years):
        assert y.dept_expenses.food_beverage == pytest.approx(FB[i] * ratio)
        assert y.dept_expenses.food_beverage / FB[i] == pytest.approx(ratio)
    # Rooms has no actual: hotel-type ratio.
    assert out.years[0].dept_expenses.rooms == pytest.approx(ROOMS[0] * 0.30)


def test_displaced_year_one_expense_is_displaced_with_its_revenue() -> None:
    # Year-1 F&B revenue cut 12.5% by a renovation; the T-12 says 77%.
    displaced = [FB[0] * 0.875, *FB[1:]]
    out = _run(
        revenue=_revenue(fb=displaced),
        t12_actuals={"fb_dept_expense": 1_540_000.0},
        t12_dept_revenue={"fb_revenue": 2_000_000.0},
    )
    y1 = out.years[0]
    assert y1.dept_expenses.food_beverage == pytest.approx(1_540_000.0 * 0.875)
    assert y1.dept_expenses.food_beverage / displaced[0] == pytest.approx(0.77)


def test_actual_without_department_revenue_anchors_year_one_dollars() -> None:
    # No T-12 F&B revenue to divide by: Year 1 is the T-12 dollars, and the
    # ratio carried forward is those dollars over the projected Year-1 revenue.
    out = _run(t12_actuals={"fb_dept_expense": 1_500_000.0})
    assert out.years[0].dept_expenses.food_beverage == pytest.approx(1_500_000.0)
    ratio = 1_500_000.0 / FB[0]
    for i, y in enumerate(out.years[1:], start=1):
        assert y.dept_expenses.food_beverage == pytest.approx(FB[i] * ratio)


def test_ratio_mode_without_independent_growth_carries_the_t12_ratio_too() -> None:
    out = _run(
        grow_opex_independently=False,
        t12_actuals={"rooms_dept_expense": 2_400_000.0},
        t12_dept_revenue={"rooms_revenue": 9_600_000.0},
    )
    for i, y in enumerate(out.years):
        assert y.dept_expenses.rooms == pytest.approx(ROOMS[i] * 0.25)


def test_line_methods_publish_the_departmental_ratio() -> None:
    out = _run(
        t12_actuals={"fb_dept_expense": 2_610_000.0},
        t12_dept_revenue={"fb_revenue": 3_390_000.0},
    )
    fb = out.line_methods["fb_dept_expense"]
    assert fb.method == "pct_revenue"
    assert fb.source == "default"
    assert fb.value == pytest.approx(2_610_000.0 / 3_390_000.0)
    assert "T-12" in (fb.note or "")
    rooms = out.line_methods["rooms_dept_expense"]
    assert rooms.method == "pct_revenue"
    assert rooms.value == pytest.approx(0.30)
    # Undistributed lines still grow.
    assert out.line_methods["utilities"].method == "growth"


def test_departmental_trace_names_the_ratio() -> None:
    out = _run(
        t12_actuals={"fb_dept_expense": 2_610_000.0},
        t12_dept_revenue={"fb_revenue": 3_390_000.0},
    )
    tr = out.provenance["years[2].dept_expenses.food_beverage"]
    assert tr.value == pytest.approx(out.years[2].dept_expenses.food_beverage)
    assert "fb_revenue" in (tr.formula or "")
    names = {i.name for i in tr.inputs}
    assert {"fb_revenue", "fb_dept_ratio"} <= names


# ─── Round 2 (review C-1 / I-2) — plausibility band and undisplaced basis ───

from fondok_schemas.reasons import ReasonCode  # noqa: E402

from app.engines.expense import DEPT_RATIO_BAND  # noqa: E402


def _displaced_revenue(fb_factor: float, rooms_factor: float = 1.0) -> FBRevenueOutput:
    out = _revenue(
        rooms=[ROOMS[0] * rooms_factor, *ROOMS[1:]],
        fb=[FB[0] * fb_factor, *FB[1:]],
        other=[OTHER[0] * rooms_factor, *OTHER[1:]],
    )
    y1 = out.years[0]
    y1.fb_displacement_factor = fb_factor
    y1.rooms_displacement_factor = rooms_factor
    y1.other_displacement_factor = rooms_factor
    return out


def test_band_is_defined_once_and_holds_the_platform_ratios() -> None:
    from app.engines.expense import HOTEL_TYPE_DEFAULTS

    for d in HOTEL_TYPE_DEFAULTS.values():
        for line, key in (
            ("rooms_dept_expense", "rooms_dept_pct"),
            ("fb_dept_expense", "fb_dept_pct"),
            ("other_dept_expense", "other_dept_pct"),
        ):
            lo, hi = DEPT_RATIO_BAND[line]
            assert lo < d[key] <= hi


def test_absurd_paired_ratio_is_flagged_and_falls_back_to_the_dollars() -> None:
    # The orig Angler shape: T-12 rooms expense 2,769,483 over a "rooms
    # revenue" of 552.77 (a cost_of_services row) — 5,010×.
    out = _run(
        t12_actuals={"rooms_dept_expense": 2_769_483.0},
        t12_dept_revenue={"rooms_revenue": 552.774},
    )
    ratio = 2_769_483.0 / ROOMS[0]
    for i, y in enumerate(out.years):
        assert y.dept_expenses.rooms == pytest.approx(ROOMS[i] * ratio)
        tr = out.provenance[f"years[{i}].dept_expenses.rooms"]
        assert tr.reason is ReasonCode.NEEDS_REVIEW
        assert tr.state == "needs_review"
        assert "552" in (tr.note or "")
    assert out.years[4].noi > 0
    lm = out.line_methods["rooms_dept_expense"]
    assert lm.value == pytest.approx(ratio)
    assert "review" in (lm.note or "").lower()


def test_both_t12_bases_absurd_fall_back_to_the_platform_ratio() -> None:
    out = _run(
        t12_actuals={"fb_dept_expense": 50_000_000.0},
        t12_dept_revenue={"fb_revenue": 10.0},
    )
    for i, y in enumerate(out.years):
        assert y.dept_expenses.food_beverage == pytest.approx(FB[i] * 0.75)
        assert out.provenance[f"years[{i}].dept_expenses.food_beverage"].reason is (
            ReasonCode.NEEDS_REVIEW
        )


def test_in_band_ratio_carries_no_review_flag() -> None:
    out = _run(
        t12_actuals={"fb_dept_expense": 2_610_000.0},
        t12_dept_revenue={"fb_revenue": 3_390_000.0},
    )
    for i in range(5):
        assert out.provenance[f"years[{i}].dept_expenses.food_beverage"].reason is None


def test_dollar_basis_divides_by_undisplaced_year_one_revenue() -> None:
    # Review I-2: a 12.5% Year-1 displacement must not bake into the ratio.
    out = _run(
        revenue=_displaced_revenue(0.875),
        t12_actuals={"fb_dept_expense": 1_500_000.0},
    )
    ratio = 1_500_000.0 / FB[0]  # 75% of the UNDISPLACED Year-1 F&B revenue
    assert out.line_methods["fb_dept_expense"].value == pytest.approx(ratio)
    y1 = out.years[0]
    assert y1.dept_expenses.food_beverage == pytest.approx(1_500_000.0 * 0.875)
    for i, y in enumerate(out.years[1:], start=1):
        assert y.dept_expenses.food_beverage == pytest.approx(FB[i] * ratio)
