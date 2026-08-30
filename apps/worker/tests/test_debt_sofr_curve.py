"""Debt engine — monthly SOFR forward curve pricing (FON-63)."""

from __future__ import annotations

from uuid import uuid4

from app.engines.debt import DebtEngine, DebtEngineInputExt

_BASE = dict(
    loan_amount=23_187_000,
    ltv=0.65,
    interest_rate=0.075,
    term_years=5,
    amortization_years=30,
    interest_only_years=4,  # IO senior
    noi_by_year=[1_013_046, 3_025_953, 3_995_804, 4_253_258, 4_594_429],
)


def _run(**extra):
    return DebtEngine().run(DebtEngineInputExt(deal_id=uuid4(), **_BASE, **extra))


def test_no_curve_uses_flat_rate_unchanged():
    out = _run()
    # IO senior: year-1 DS = loan * flat rate.
    assert round(out.schedule[0].debt_service) == round(23_187_000 * 0.075)


def test_flat_curve_plus_spread_prices_senior():
    # A flat 4.3% curve + 3.5% spread = 7.8% all-in.
    out = _run(sofr_curve=[0.043] * 60, senior_spread_pct=0.035)
    assert round(out.schedule[0].debt_service) == round(23_187_000 * 0.078)


def test_rising_curve_averages_per_year():
    # SOFR ramps 4.0% -> 6.0% over 60 months; year-1 DS uses the year-1 average.
    curve = [0.04 + 0.02 * (i / 59) for i in range(60)]
    out = _run(sofr_curve=curve, senior_spread_pct=0.035)
    yr1_avg = sum(curve[:12]) / 12
    expected = 23_187_000 * (yr1_avg + 0.035)
    assert abs(out.schedule[0].debt_service - expected) < 1.0


def test_curve_without_spread_falls_back_to_flat():
    # A curve with no senior_spread_pct must not change the flat behavior.
    out = _run(sofr_curve=[0.043] * 60)
    assert round(out.schedule[0].debt_service) == round(23_187_000 * 0.075)


def test_refi_prices_off_curve_average_over_post_refi_period():
    # Refi at year 3 (75% LTV x stabilized value), IO, priced curve + 4% spread.
    curve = [0.05] * 60
    out = _run(
        sofr_curve=curve,
        senior_spread_pct=0.035,
        debt_stack_overrides={
            "refi_test_year": 3,
            "refi_market_ltv_pct": 0.75,
            "refi_stabilized_value": 68_320_731,
            "refi_fee_pct": 0.01,
            "refi_spread_pct": 0.04,
        },
    )
    assert out.refi_year == 3
    # Post-refi debt service = refi proceeds ($51,240,548) * (5% + 4%).
    refi_proceeds = 0.75 * 68_320_731
    expected_refi_ds = refi_proceeds * (0.05 + 0.04)
    # Year 4 (index 3) is post-refi.
    assert abs(out.debt_service_by_year[3] - expected_refi_ds) < 2.0
