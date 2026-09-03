"""Financials Projections assumption keys → engine wiring (opt-in).

Pins the three keys the Financials Projections panel edits and persists to
``field_overrides`` so they actually drive the model:

* ``resort_fee_per_night`` + ``resort_fee_capture_y1/y2/y3`` — bottom-up
  resort-fee revenue = per-night × occupied-room-nights × capture ramp,
  replacing the flat ``starting_resort_fees`` anchor.
* ``other_expense_growth`` — grows the undistributed ("other") expense
  category at its own rate instead of the blanket ``expense_growth``.

Guardrail: with none of the keys set the output must be byte-identical to
today. Each block below proves both the no-op fallback and the opt-in
behavior. The engines are exercised in isolation (no DB / agent loop) so
the math contract is pinned directly; the ``_coerce_*`` tests cover the
``engine_runner`` field_overrides → engine-input mapping.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from app.engines.fb_revenue import FBRevenueOutput, FBRevenueYear
from app.engines.revenue import RevenueEngine
from app.services.engine_runner import (
    _coerce_capture_pct,
    _coerce_other_expense_growth,
    _coerce_resort_fee_per_night,
)
from fondok_schemas.underwriting import RevenueEngineInput

KEYS = 200
DAYS = 365


def _revenue_input(**overrides) -> RevenueEngineInput:
    kwargs = dict(
        deal_id=uuid4(),
        keys=KEYS,
        starting_occupancy=0.75,
        starting_adr=300.0,
        occupancy_growth=0.0,  # flat occupancy → occupied-nights constant
        adr_growth=0.04,
        fb_revenue_per_occupied_room=80.0,
        other_revenue_pct_of_rooms=0.06,
        hold_years=5,
    )
    kwargs.update(overrides)
    return RevenueEngineInput(**kwargs)


def _fb_revenue_payload(*, total: float = 13_600_000.0) -> FBRevenueOutput:
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


def _expense_input(**overrides) -> ExpenseEngineInput:
    kwargs = dict(
        deal_id=uuid4(),
        revenue=_fb_revenue_payload(),
        hotel_type="full",
        mgmt_fee_pct=0.03,
        ffe_reserve_pct=0.04,
        expense_growth=0.035,
        grow_opex_independently=True,
    )
    kwargs.update(overrides)
    return ExpenseEngineInput(**kwargs)


# ── (a) keys unset → output equals baseline ─────────────────────────────


def test_revenue_unset_keys_are_byte_identical() -> None:
    """Passing the new resort-fee keys as ``None`` reproduces the exact
    flat ``starting_resort_fees`` projection — the feature is opt-in."""
    baseline = RevenueEngine().run(
        _revenue_input(starting_resort_fees=900_000.0, resort_fees_growth=0.04)
    )
    explicit_none = RevenueEngine().run(
        _revenue_input(
            starting_resort_fees=900_000.0,
            resort_fees_growth=0.04,
            resort_fee_per_night=None,
            resort_fee_capture_y1=None,
            resort_fee_capture_y2=None,
            resort_fee_capture_y3=None,
        )
    )
    for b, e in zip(baseline.years, explicit_none.years, strict=True):
        assert e.resort_fees == pytest.approx(b.resort_fees)
        assert e.total_revenue == pytest.approx(b.total_revenue)
    # And the flat path still grows the anchor at resort_fees_growth.
    assert baseline.years[0].resort_fees == pytest.approx(900_000.0)
    assert baseline.years[1].resort_fees == pytest.approx(900_000.0 * 1.04)


def test_expense_unset_other_growth_is_byte_identical() -> None:
    """``other_expense_growth=None`` grows undistributed at ``expense_growth``
    exactly as today."""
    baseline = ExpenseEngine().run(_expense_input())
    explicit_none = ExpenseEngine().run(_expense_input(other_expense_growth=None))
    for b, e in zip(baseline.years, explicit_none.years, strict=True):
        assert e.undistributed.total == pytest.approx(b.undistributed.total)
        assert e.noi == pytest.approx(b.noi)
    # Undistributed out-years still compound at expense_growth (0.035).
    y1_undist = baseline.years[0].undistributed.total
    assert baseline.years[1].undistributed.total == pytest.approx(
        y1_undist * 1.035
    )
    assert baseline.years[2].undistributed.total == pytest.approx(
        y1_undist * 1.035**2
    )


# ── (b) resort_fee_per_night set → build-up drives resort fees ───────────


def test_resort_fee_per_night_drives_revenue_with_capture_ramp() -> None:
    per_night = 30.0
    cap = (0.6, 0.8, 0.95)
    out = RevenueEngine().run(
        _revenue_input(
            # A flat anchor is present too — the build-up must REPLACE it,
            # never add to it (no double count).
            starting_resort_fees=900_000.0,
            resort_fee_per_night=per_night,
            resort_fee_capture_y1=cap[0],
            resort_fee_capture_y2=cap[1],
            resort_fee_capture_y3=cap[2],
        )
    )

    # resort_fees == per_night × occupied-room-nights × capture[year],
    # where occupied-room-nights = occupancy × keys × 365 (the engine basis).
    def expected(year_idx: int, capture: float) -> float:
        occ = out.years[year_idx].occupancy
        return per_night * (occ * KEYS * DAYS) * capture

    assert out.years[0].resort_fees == pytest.approx(expected(0, cap[0]))
    assert out.years[1].resort_fees == pytest.approx(expected(1, cap[1]))
    assert out.years[2].resort_fees == pytest.approx(expected(2, cap[2]))
    # y3 capture carries forward for every year past 3.
    assert out.years[3].resort_fees == pytest.approx(expected(3, cap[2]))
    assert out.years[4].resort_fees == pytest.approx(expected(4, cap[2]))

    # Occupancy is flat here, so the capture ramp alone must lift Y1<Y2<Y3
    # and hold Y3==Y4==Y5.
    rf = [y.resort_fees for y in out.years]
    assert rf[0] < rf[1] < rf[2]
    assert rf[2] == pytest.approx(rf[3]) == pytest.approx(rf[4])

    # Build-up replaced the flat anchor: Y1 is NOT the flat 900k.
    assert out.years[0].resort_fees != pytest.approx(900_000.0)

    # resort_fees stays additive room-adjacent revenue (no double count).
    y1 = out.years[0]
    assert y1.total_revenue == pytest.approx(
        y1.rooms_revenue + y1.fb_revenue + y1.resort_fees + y1.other_revenue
    )
    # Provenance trace is emitted for the build-up line.
    assert "years[0].resort_fees" in out.provenance


def test_resort_fee_capture_carries_forward_single_value() -> None:
    """Only Y1 capture supplied → it carries across the whole hold."""
    out = RevenueEngine().run(
        _revenue_input(resort_fee_per_night=25.0, resort_fee_capture_y1=0.7)
    )
    per_night, capture = 25.0, 0.7
    for i, y in enumerate(out.years):
        assert y.resort_fees == pytest.approx(
            per_night * (y.occupancy * KEYS * DAYS) * capture
        )


# ── (c) other_expense_growth set → undistributed grows at its own rate ───


def test_other_expense_growth_drives_only_undistributed() -> None:
    baseline = ExpenseEngine().run(_expense_input())  # other=None → 0.035
    tuned = ExpenseEngine().run(_expense_input(other_expense_growth=0.08))

    y1_undist = baseline.years[0].undistributed.total
    # Y1 anchor is identical (growth only applies to out-years).
    assert tuned.years[0].undistributed.total == pytest.approx(y1_undist)

    # Out-year undistributed grows at other_expense_growth (0.08), NOT the
    # blanket expense_growth (0.035).
    assert tuned.years[1].undistributed.total == pytest.approx(y1_undist * 1.08)
    assert tuned.years[2].undistributed.total == pytest.approx(
        y1_undist * 1.08**2
    )
    # And it genuinely differs from the expense_growth baseline.
    assert tuned.years[1].undistributed.total != pytest.approx(
        baseline.years[1].undistributed.total
    )

    # Departmental + fixed charges are UNCHANGED — they still grow at
    # expense_growth, so both runs match line-for-line.
    for b, t in zip(baseline.years, tuned.years, strict=True):
        assert t.dept_expenses.total == pytest.approx(b.dept_expenses.total)
        assert t.fixed_charges.total == pytest.approx(b.fixed_charges.total)
        assert t.mgmt_fee == pytest.approx(b.mgmt_fee)
        assert t.ffe_reserve == pytest.approx(b.ffe_reserve)


# ── step 3: engine_runner field_overrides → engine-input coercers ────────


def test_coerce_resort_fee_per_night() -> None:
    assert _coerce_resort_fee_per_night("35") == 35.0
    assert _coerce_resort_fee_per_night(35.0) == 35.0
    assert _coerce_resort_fee_per_night(0) is None  # 0 → off
    assert _coerce_resort_fee_per_night(-5) is None  # negative → off
    assert _coerce_resort_fee_per_night(None) is None
    assert _coerce_resort_fee_per_night("") is None
    assert _coerce_resort_fee_per_night("abc") is None


def test_coerce_capture_pct_clamps() -> None:
    assert _coerce_capture_pct("0.6") == 0.6
    assert _coerce_capture_pct(1.5) == 1.0  # clamp high
    assert _coerce_capture_pct(-0.2) == 0.0  # clamp low
    assert _coerce_capture_pct(None) is None
    assert _coerce_capture_pct("nope") is None


def test_coerce_other_expense_growth_clamps() -> None:
    assert _coerce_other_expense_growth("0.08") == 0.08
    assert _coerce_other_expense_growth(0.9) == 0.5  # clamp to schema bound
    assert _coerce_other_expense_growth(-0.9) == -0.5
    assert _coerce_other_expense_growth(None) is None
    assert _coerce_other_expense_growth("") is None
