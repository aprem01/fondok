"""Off-cycle disposition (exit_month override) in the monthly model (FON-67)."""

from __future__ import annotations

from app.engines.monthly_cashflow import MonthlyCashFlowInput, build_monthly_cashflow

_BASE = dict(
    hold_years=5,
    noi_by_year=[4_052_184, 3_025_953, 3_995_804, 4_253_258, 4_594_429],
    monthly_debt_service=[130_000] * 60,
    equity_at_close=19_968_776,
    total_capital_at_close=43_155_776,
    acquisition_month_offset=9,
    refi_month=30,
    refi_net_cash_out=27_541_143,
    exit_net_proceeds_levered=17_080_000,
    exit_net_proceeds_unlevered=66_500_000,
)


def test_none_exit_month_is_clean_hold():
    # No override → unchanged 60-month exit (byte-for-byte legacy behavior).
    a = build_monthly_cashflow(MonthlyCashFlowInput(**_BASE))
    b = build_monthly_cashflow(MonthlyCashFlowInput(**_BASE, exit_month=None))
    assert a.levered_irr == b.levered_irr
    assert a.equity_multiple == b.equity_multiple


def test_earlier_exit_raises_irr_and_lowers_em():
    full = build_monthly_cashflow(MonthlyCashFlowInput(**_BASE))
    early = build_monthly_cashflow(MonthlyCashFlowInput(**_BASE, exit_month=52))
    # Shorter hold: exit proceeds arrive sooner (higher IRR) but there's less
    # total operating cash (lower equity multiple).
    assert early.levered_irr > full.levered_irr
    assert early.equity_multiple < full.equity_multiple


def test_exit_month_clamped_to_term():
    # An override beyond the term is clamped to hold_years * 12 (= no-op here).
    clamped = build_monthly_cashflow(MonthlyCashFlowInput(**_BASE, exit_month=999))
    full = build_monthly_cashflow(MonthlyCashFlowInput(**_BASE))
    assert clamped.levered_irr == full.levered_irr
