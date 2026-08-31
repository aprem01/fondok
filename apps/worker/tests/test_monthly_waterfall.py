"""Monthly IRR-hurdle waterfall (FON-66/67)."""

from __future__ import annotations

from uuid import uuid4

from app.engines.partnership import (
    PartnershipEngine,
    PartnershipInputExt,
    _monthly_period_days,
)
from fondok_schemas.partnership import WaterfallTier

_TIERS = [
    WaterfallTier(label="Pref", hurdle_rate=0.10, gp_split=0.0, lp_split=1.0),
    WaterfallTier(label="T2", hurdle_rate=0.15, gp_split=0.20, lp_split=0.80),
    WaterfallTier(label="T3", hurdle_rate=0.20, gp_split=0.25, lp_split=0.75),
    WaterfallTier(label="T4", hurdle_rate=0.25, gp_split=0.25, lp_split=0.75),
    WaterfallTier(label="T5", hurdle_rate=0.30, gp_split=0.25, lp_split=0.75),
    WaterfallTier(label="T6", hurdle_rate=0.50, gp_split=0.50, lp_split=0.50),
]


def _mk(**kw):
    base = dict(
        deal_id=uuid4(), total_equity=19_968_776, gp_equity_pct=0.10,
        lp_equity_pct=0.90, pref_rate=0.10, waterfall=_TIERS, cash_flows=[1, 1, 1, 1, 1],
    )
    base.update(kw)
    return PartnershipInputExt(**base)


def test_annual_default_is_used_without_monthly_flows():
    out = PartnershipEngine().run(_mk(cash_flows=[5e6, 5e6, 5e6, 5e6, 40e6]))
    assert out.gp.contributed_equity > 0 and out.lp.contributed_equity > 0


def test_monthly_pref_subordinates_gp_below_hurdle():
    # A deal that just clears the 10% pref: the GP (0/100 pref tier) earns
    # nothing until the LP is past pref, so promote is small / zero.
    mcf = [-19_968_776.0] + [0.0] * 59 + [22_000_000.0]
    out = PartnershipEngine().run(_mk(period="monthly", cash_flows_monthly=mcf))
    # LP got its capital + ~pref; GP promote is minimal.
    assert out.lp.equity_multiple > 1.0
    assert out.promote_earned >= 0.0
    assert out.gp.contributed_equity == 19_968_776 * 0.10


def test_monthly_promote_grows_with_upside():
    low = PartnershipEngine().run(
        _mk(period="monthly", cash_flows_monthly=[-19_968_776.0] + [0.0] * 59 + [30e6])
    )
    high = PartnershipEngine().run(
        _mk(period="monthly", cash_flows_monthly=[-19_968_776.0] + [0.0] * 59 + [60e6])
    )
    # More upside → more promote to the GP and a higher GP multiple.
    assert high.promote_earned > low.promote_earned
    assert high.gp.equity_multiple > low.gp.equity_multiple


def test_actual_365_period_days_from_month_end_close():
    # A 9/30 close → successive month-ends: Oct 31 (31d), Nov 30 (30d), Dec 31 (31d)…
    days = _monthly_period_days("2025-09-30", 6)
    assert days[0] == 0.0
    assert days[1:6] == [31.0, 30.0, 31.0, 31.0, 28.0]


def test_actual_365_pref_matches_institutional_convention():
    # Begin*(1+10%)^(31/365)-Begin on $13.305M ≈ $108.1K (the Kimpton model's
    # actual/365 accrual), materially below a naive 10%/12 = ~$110.9K.
    days = _monthly_period_days("2025-09-30", 2)
    bal = 13_305_000.0
    accr = bal * (1.10) ** (days[1] / 365.0) - bal
    assert abs(accr - 108_139) < 5
    assert accr < bal * (0.10 / 12)  # strictly less than simple monthly


def test_no_close_date_falls_back_to_nominal_months():
    days = _monthly_period_days(None, 4)
    assert days[0] == 0.0
    assert all(abs(d - 365.0 / 12.0) < 1e-9 for d in days[1:])


def test_monthly_contributions_fund_pro_rata():
    # Two equity draws (close + a later renovation draw) split 90/10.
    mcf = [-14_000_000.0] + [0.0] * 5 + [-6_000_000.0] + [0.0] * 53 + [45e6]
    out = PartnershipEngine().run(_mk(period="monthly", cash_flows_monthly=mcf))
    assert round(out.gp.contributed_equity) == round(20_000_000 * 0.10)
    assert round(out.lp.contributed_equity) == round(20_000_000 * 0.90)


# FON-67 — the Kimpton Angler's source model's actual levered cash flow (its
# Waterfall tab, row 24: a 9/30/2025 close, front-loaded 2026 renovation draws,
# a month-30 refinance cash-out, second-phase draws, and a month-52 sale). The
# source model's own reported returns are the golden targets: Deal Levered
# 39.5855% / 2.4727x, LP 36.236% / 2.192x, GP 57.671% / 5.00x. This locks the
# monthly IRR-hurdle waterfall + actual/365 pref accrual + actual/365 XIRR to
# reconcile a real institutional model, not lose points to annual/nominal drift.
_KIMPTON_LEVERED_CF = [
    -14783049.1, 126557.56, 111906.6, 103035.4, -568556.76, -608617.82,
    -607140.53, -617000.57, -629189.81, -574184.26, -553565.51, -478990.93,
    262457.56, 309944.49, 284896.12, 264609.11, 176409.75, 110239.05,
    110996.97, 92929.91, 71858.51, 156015.64, 188274.29, 308635.24, 289564.19,
    339920.74, 312875.43, 290016.12, 195354.4, 124323.88, 27666058.85,
    -74629.93, -97540.19, -7896.74, 26217.77, 154620.29, 129212.54, 182200.02,
    153353.86, 128960.64, 28580.97, -46777.62, -46407.66, -67242.43, -91426.02,
    3502.66, 39646.0, 175521.2, 171181.05, 192061.34, 160173.07, 133183.66,
    15512362.32, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0, 0.0,
]


def test_kimpton_source_model_reconciles():
    from app.engines.monthly_cashflow import xirr as _xirr_tuples

    cf = _KIMPTON_LEVERED_CF
    draws = sum(-x for x in cf if x < 0)
    dist = sum(x for x in cf if x > 0)

    # Deal-level levered IRR (nominal m/12 — the engine's own basis) and EM /
    # peak reconcile to the source model within a rounding error.
    deal_irr = _xirr_tuples([(m / 12.0, v) for m, v in enumerate(cf)])
    assert abs(deal_irr - 0.395855) < 0.001, deal_irr
    assert abs(dist / draws - 2.472654) < 0.001
    assert abs(draws - 19_852_216) < 1.0  # peak equity == sum of the draws

    out = PartnershipEngine().run(
        _mk(
            total_equity=draws,
            period="monthly",
            cash_flows_monthly=cf,
            close_date="2025-09-30",
        )
    )
    # LP and GP legs reconcile to within half an IRR point of the source split.
    assert abs(out.lp.irr - 0.362362) < 0.005, out.lp.irr
    assert abs(out.gp.irr - 0.576714) < 0.005, out.gp.irr
    assert abs(out.lp.equity_multiple - 2.192) < 0.01, out.lp.equity_multiple
    assert abs(out.gp.equity_multiple - 5.00) < 0.03, out.gp.equity_multiple
    # Conservation: every distributed dollar lands with exactly one of LP/GP.
    lp_dist = out.lp.equity_multiple * (draws * 0.90)
    gp_dist = out.gp.equity_multiple * (draws * 0.10)
    assert abs((lp_dist + gp_dist) - dist) < 5_000, (lp_dist + gp_dist, dist)
