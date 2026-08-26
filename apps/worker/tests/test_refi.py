"""FON-67 — two-phase senior→refinance financing.

The debt engine models a mid-hold refi when the analyst sets a refi year:
it sizes the new loan off the refi-year NOI, retires the senior balance,
and emits a phased debt-service series, the net cash-out to equity, and the
post-refi exit balance. The returns engine injects the cash-out into the
levered stream. These tests pin: no-refi deals are unchanged; a refi with
proceeds above payoff produces a cash-out; and the cash-out lifts levered
returns without touching the unlevered case.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.debt import DebtEngine, DebtEngineInputExt, _compute_refi
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt
from fondok_schemas.underwriting import ModelAssumptions


def _debt_input(**over) -> DebtEngineInputExt:
    base = dict(
        deal_id=uuid4(),
        loan_amount=23_700_000,
        ltv=0.65,
        interest_rate=0.068,
        term_years=5,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=[3_000_000, 3_300_000, 3_600_000, 4_000_000, 4_400_000],
        purchase_price_usd=36_400_000,
        total_capital_usd=44_300_000,
    )
    base.update(over)
    return DebtEngineInputExt(**base)


def _assumptions() -> ModelAssumptions:
    return ModelAssumptions(
        purchase_price=36_400_000, ltv=0.65, interest_rate=0.068,
        amortization_years=30, loan_term_years=5, hold_years=5,
        exit_cap_rate=0.07, revpar_growth=0.045, expense_growth=0.03,
        selling_costs_pct=0.02, closing_costs_pct=0.02,
    )


def test_no_refi_is_single_phase() -> None:
    out = DebtEngine().run(_debt_input())
    assert out.debt_service_by_year == []
    assert out.refi_cash_out == 0.0
    assert out.refi_year is None
    # balance_at_exit falls back to the senior schedule's ending balance.
    assert out.balance_at_exit == pytest.approx(out.schedule[-1].ending_balance)


def test_refi_produces_cash_out_and_phased_ds() -> None:
    out = DebtEngine().run(
        _debt_input(debt_stack_overrides={
            "refi_test_year": 4,
            "refi_market_debt_yield_pct": 0.07,
            "refi_market_dscr_min": 1.25,
            "refi_market_rate_pct": 0.065,
        })
    )
    assert out.refi_year == 4
    assert out.refi_cash_out > 5_000_000  # proceeds exceed the senior payoff
    assert len(out.debt_service_by_year) == 5
    # Years 1–4 = senior debt service; year 5 = the (different) refi service.
    assert out.debt_service_by_year[0] == pytest.approx(out.debt_service_by_year[3])
    assert out.debt_service_by_year[4] != pytest.approx(out.debt_service_by_year[0])
    # Interest-only refi → exit balance equals the refi proceeds.
    assert out.balance_at_exit == pytest.approx(out.debt_service_by_year[4] / 0.065)


def test_refi_year_out_of_range_is_ignored() -> None:
    # A refi at/after the exit year does nothing (single-phase).
    out = DebtEngine().run(
        _debt_input(debt_stack_overrides={"refi_test_year": 5})  # horizon == 5
    )
    assert out.refi_year is None
    assert out.debt_service_by_year == []


def test_compute_refi_sizes_off_min_of_dy_and_dscr() -> None:
    schedule = DebtEngine().run(_debt_input()).schedule
    noi = [3_000_000, 3_300_000, 3_600_000, 4_000_000, 4_400_000]
    senior_ds = [yr.debt_service for yr in schedule]
    ds, cash_out, proceeds, k = _compute_refi(
        {"year": 4.0, "debt_yield": 0.07, "dscr_min": 1.25, "rate": 0.065},
        schedule, noi, 5, senior_ds,
    )
    noi_k = noi[3]
    expected = min(noi_k / 0.07, noi_k / (1.25 * 0.065))
    assert proceeds == pytest.approx(expected)
    assert k == 4


def test_refi_cash_out_lifts_levered_not_unlevered() -> None:
    eng, ret = DebtEngine(), ReturnsEngine()
    d = eng.run(_debt_input())
    ra = _assumptions()
    common = dict(
        deal_id=uuid4(), assumptions=ra, year_one_noi=3_000_000,
        noi_by_year=[3_000_000, 3_300_000, 3_600_000, 4_000_000, 4_400_000],
        annual_debt_service=d.annual_debt_service, loan_amount=23_700_000,
        loan_balance_at_exit=d.balance_at_exit, equity=20_658_900,
    )
    base = ret.run(ReturnsEngineInputExt(**common))
    refi = ret.run(ReturnsEngineInputExt(**{**common, "refi_cash_out": 10_000_000, "refi_year": 4}))
    assert refi.levered_irr > base.levered_irr
    assert refi.equity_multiple > base.equity_multiple
    assert refi.unlevered_irr == pytest.approx(base.unlevered_irr)


def test_zero_cash_out_leaves_returns_unchanged() -> None:
    ret = ReturnsEngine()
    ra = _assumptions()
    common = dict(
        deal_id=uuid4(), assumptions=ra, year_one_noi=3_000_000,
        noi_by_year=[3_000_000, 3_300_000, 3_600_000, 4_000_000, 4_400_000],
        annual_debt_service=1_800_000, loan_amount=23_700_000,
        loan_balance_at_exit=22_000_000, equity=20_658_900,
    )
    base = ret.run(ReturnsEngineInputExt(**common))
    zero = ret.run(ReturnsEngineInputExt(**{**common, "refi_cash_out": 0.0, "refi_year": 4}))
    assert zero.levered_irr == pytest.approx(base.levered_irr)
