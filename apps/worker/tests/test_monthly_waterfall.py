"""Monthly IRR-hurdle waterfall (FON-66/67)."""

from __future__ import annotations

from uuid import uuid4

from app.engines.partnership import PartnershipEngine, PartnershipInputExt
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


def test_monthly_contributions_fund_pro_rata():
    # Two equity draws (close + a later renovation draw) split 90/10.
    mcf = [-14_000_000.0] + [0.0] * 5 + [-6_000_000.0] + [0.0] * 53 + [45e6]
    out = PartnershipEngine().run(_mk(period="monthly", cash_flows_monthly=mcf))
    assert round(out.gp.contributed_equity) == round(20_000_000 * 0.10)
    assert round(out.lp.contributed_equity) == round(20_000_000 * 0.90)
