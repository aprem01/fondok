"""FON-67 — the monthly cash-flow model reconciles to the Kimpton source.

The annual-period engine loses several IRR points to intra-year timing and the
partial stub years (9/30 acquisition, 9/30 exit). The monthly model places every
flow on its real month and takes XIRR, closing that gap: it lands within ~1.5
points of the source's 39.59% levered / 19.53% unlevered — the remaining sliver
is the exact monthly NOI curve, which only the source's monthly tab pins down.
"""

from __future__ import annotations

from app.engines.monthly_cashflow import (
    MonthlyCashFlowInput,
    build_monthly_cashflow,
    xirr,
)


def _kimpton_input() -> MonthlyCashFlowInput:
    senior, refi = 23_187_000.0, 51_240_548.0
    # Rates backed out of the source's own clean operating years:
    #   2027 CF = NOI − senior DS  →  senior rate = (3,995,804 − 2,447,736)/senior
    #   2029 CF = NOI − refi DS    →  refi rate   = (4,594,429 − 651,996)/refi
    r_senior = (3_995_804 - 2_447_736) / senior
    r_refi = (4_594_429 - 651_996) / refi
    refi_month = 30
    monthly_ds = [
        (senior * r_senior / 12.0) if m <= refi_month else (refi * r_refi / 12.0)
        for m in range(1, 61)
    ]
    total_capital = 36_436_802 + 4_943_400 + 284_041 + 299_057 + 728_736
    reno = 4_943_400.0
    gross = 4_440_841 / 0.065
    return MonthlyCashFlowInput(
        hold_years=5,
        noi_by_year=[1_013_046.0, 3_025_953.0, 3_995_804.0, 4_253_258.0, 4_594_429.0],
        acquisition_month_offset=9,  # 9/30 close → 2025 is a 3-month stub
        monthly_debt_service=monthly_ds,
        equity_at_close=(total_capital - reno) - senior,
        total_capital_at_close=total_capital - reno,
        deferred_capital=reno,
        deferred_capital_start_month=4,   # renovation across 2026
        deferred_capital_end_month=15,
        refi_month=refi_month,
        refi_net_cash_out=refi - senior - refi * 0.01,  # proceeds − payoff − 1% fee
        exit_net_proceeds_levered=gross - gross * 0.015 - gross * 0.0105 - refi,
        exit_net_proceeds_unlevered=gross - gross * 0.015 - gross * 0.0105,
    )


def test_monthly_reconciles_to_kimpton_source() -> None:
    r = build_monthly_cashflow(_kimpton_input())
    # Within ~1.5 points of the source targets (39.59% / 19.53%).
    assert abs(r.levered_irr - 0.3959) < 0.02, r.levered_irr
    assert abs(r.unlevered_irr - 0.1953) < 0.02, r.unlevered_irr
    # And materially above the annual engine's result (~34% levered).
    assert r.levered_irr > 0.36


def test_monthly_beats_annual_on_timing() -> None:
    """The monthly model must exceed a naive annual-period IRR of the same
    economics — intra-year cash timing is worth real points."""
    inp = _kimpton_input()
    r = build_monthly_cashflow(inp)
    # Annual-period IRR: lump each year's levered CF at year-end.
    annual = [(-inp.equity_at_close)]
    for y in range(inp.hold_years):
        noi = inp.noi_by_year[y]
        ds = sum(inp.monthly_debt_service[y * 12:(y + 1) * 12])
        cap = inp.deferred_capital if y == 1 else 0.0  # 2026
        refi = inp.refi_net_cash_out if y == 2 else 0.0
        ex = inp.exit_net_proceeds_levered if y == inp.hold_years - 1 else 0.0
        annual.append(noi - ds - cap + refi + ex)
    annual_irr = xirr([(float(i), cf) for i, cf in enumerate(annual)])
    assert r.levered_irr > annual_irr


def test_no_refi_no_deferral_is_plain_series() -> None:
    r = build_monthly_cashflow(
        MonthlyCashFlowInput(
            hold_years=5,
            noi_by_year=[3_000_000.0] * 5,
            monthly_debt_service=[150_000.0] * 60,
            equity_at_close=20_000_000.0,
            total_capital_at_close=43_000_000.0,
            exit_net_proceeds_levered=25_000_000.0,
            exit_net_proceeds_unlevered=48_000_000.0,
        )
    )
    assert 0.0 < r.levered_irr < 1.0
    assert 0.0 < r.unlevered_irr < 1.0
    assert len(r.levered_monthly) == 61  # month 0 + 60 months
