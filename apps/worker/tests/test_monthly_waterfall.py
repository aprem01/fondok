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
