"""FON-66 — Sam's 9-14 MVP blocker: promote tiers never applied on the annual
waterfall.

Sam configured an 8% preferred return, a 20 / 80 GP / LP equity split and
promote tiers above the pref. Partnership → Cash Flows split every
distribution at the 20 / 80 ownership ratio: LP IRR == GP IRR (31.1%) while
the deal IRR was 32.2%, promote $0, and the allocation preview showed only
Return of Capital / Preferred / Tier 2.

These tests build that scenario straight against the engine (annual path —
the path a deal with year-end timing runs). They assert what an IRR-hurdle
waterfall must do: the residual is poured through each promote band only
until the cumulative LP IRR reaches that band's hurdle, then steps to the
next band, so the GP earns its promote and the GP IRR clears the LP IRR.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.partnership import PartnershipEngine, PartnershipInputExt
from app.engines.returns import irr
from fondok_schemas.partnership import WaterfallTier

_TOTAL_EQUITY = 10_000_000.0
# Operating cash through the hold, then a Year-5 sale: deal IRR ≈ 32%.
_SAM_FLOWS = [800_000.0, 900_000.0, 1_000_000.0, 1_100_000.0, 32_000_000.0]


def _sam_tiers() -> list[WaterfallTier]:
    """Sam's stack: pref band (100% LP to 8%), then promote bands whose
    hurdles sit BELOW the deal IRR, then an open-ended top band."""
    return [
        WaterfallTier(label="Preferred (to 8%)", hurdle_rate=0.08, gp_split=0.0, lp_split=1.0),
        # The first promote band at the 20 / 80 ownership ratio — exactly
        # Sam's configuration, so a waterfall stuck in this band pays NO
        # promote and GP IRR == LP IRR.
        WaterfallTier(label="Tier 2 (to 12%)", hurdle_rate=0.12, gp_split=0.20, lp_split=0.80),
        WaterfallTier(label="Tier 3 (to 15%)", hurdle_rate=0.15, gp_split=0.30, lp_split=0.70),
        WaterfallTier(label="Tier 4 (>15%)", hurdle_rate=0.20, gp_split=0.40, lp_split=0.60),
    ]


def _sam_input(**kw) -> PartnershipInputExt:
    fields: dict = dict(
        deal_id=uuid4(),
        total_equity=_TOTAL_EQUITY,
        gp_equity_pct=0.20,
        lp_equity_pct=0.80,
        pref_rate=0.08,
        waterfall=_sam_tiers(),
        cash_flows=list(_SAM_FLOWS),
    )
    fields.update(kw)
    return PartnershipInputExt(**fields)


def test_scenario_deal_irr_is_about_32_pct() -> None:
    """The fixture is Sam's deal shape: a ~32% levered IRR, well above every hurdle."""
    deal_irr = irr([-_TOTAL_EQUITY] + _SAM_FLOWS)
    assert 0.30 < deal_irr < 0.34, deal_irr


def test_promote_tiers_apply_gp_irr_clears_lp_irr() -> None:
    """Sam's blocker: with promote bands above the pref the GP must earn a
    promote and out-return the LP — not split 20 / 80 all the way up."""
    out = PartnershipEngine().run(_sam_input())
    assert out.promote_amount > 0.0, "promote tiers were never applied"
    assert out.gp.irr > out.lp.irr, (out.gp.irr, out.lp.irr)
    assert out.gp.irr > out.lp.irr + 0.02  # a real promote, not float dust
    assert out.reconciles is True


def test_residual_climbs_every_cleared_band() -> None:
    """The deal IRR (~32%) clears every hurdle, so every promote band above
    the pref carries dollars and the top band holds the residual — the
    preview showed only ROC / Pref / Tier 2 before."""
    out = PartnershipEngine().run(_sam_input())
    labels = [t.label for t in out.tier_allocations]
    assert labels[:2] == ["Return of Capital", "Preferred Return"]
    for lbl in ("Tier 2 (to 12%)", "Tier 3 (to 15%)", "Tier 4 (>15%)"):
        assert lbl in labels, (lbl, labels)
    by_label = {t.label: t for t in out.tier_allocations}
    assert by_label["Tier 4 (>15%)"].total_amount > 0.0
    # The GP's promote is the GP dollars above its 20% pro-rata share of each band.
    expected_promote = sum(
        max(0.0, t.gp_amount - 0.20 * t.total_amount)
        for t in out.tier_allocations
        if t.kind == "promote"
    )
    assert out.promote_amount == pytest.approx(expected_promote, abs=1.0)


def test_lp_irr_sits_at_each_hurdle_when_a_band_is_exhausted() -> None:
    """An IRR-hurdle band pays out exactly until the cumulative LP IRR reaches
    its hurdle. Re-walking the LP flows band by band must land on each hurdle
    (to solver tolerance) before the next band starts."""
    out = PartnershipEngine().run(_sam_input())
    lp_eq = _TOTAL_EQUITY * 0.80
    by_label = {t.label: t for t in out.tier_allocations}
    # Everything but the sale year is swallowed by return of capital here, so
    # the hurdle crossings all happen inside Year 5: LP flows through Year 4
    # are the engine's own, Year 5 is rebuilt band by band.
    prior = out.lp_cash_flows[:4]
    y5_lp = 0.0
    for lbl, hurdle in (
        ("Return of Capital", None),
        ("Preferred Return", None),
        ("Preferred (to 8%)", 0.08),
        ("Tier 2 (to 12%)", 0.12),
        ("Tier 3 (to 15%)", 0.15),
    ):
        row = by_label.get(lbl)
        if row is None:
            continue
        # ROC / pref rows accumulate across years; only their Year-5 share
        # matters here, which is the row's LP total minus Years 1–4 LP flows.
        if hurdle is None:
            continue
        y5_lp += row.lp_amount
        # LP IRR to date through this band's last dollar.
        roc_pref_y5 = (
            by_label["Return of Capital"].lp_amount
            + by_label["Preferred Return"].lp_amount
            - sum(prior)
        )
        flows = [-lp_eq] + prior + [roc_pref_y5 + y5_lp]
        assert irr(flows) == pytest.approx(hurdle, abs=1e-4), (lbl, irr(flows))


def test_below_first_hurdle_is_untouched_no_promote() -> None:
    """A deal that never clears the pref (the Kimpton golden's shape: cash
    short of the equity) pays everything as return of capital — no promote,
    GP IRR == LP IRR, a single ROC row. The fix must not move this."""
    out = PartnershipEngine().run(
        _sam_input(cash_flows=[400_000.0, 400_000.0, 400_000.0, 400_000.0, 6_000_000.0])
    )
    assert out.promote_amount == 0.0
    assert out.gp.irr == pytest.approx(out.lp.irr)
    assert [t.kind for t in out.tier_allocations] == ["return_of_capital"]


def test_single_band_stack_still_pays_residual_to_it() -> None:
    """One promote band only: the residual above the pref all lands in it."""
    out = PartnershipEngine().run(
        _sam_input(
            waterfall=[
                WaterfallTier(label="Tier 1 (to 10%)", hurdle_rate=0.10, gp_split=0.30, lp_split=0.70),
            ]
        )
    )
    assert out.promote_amount > 0.0
    assert out.gp.irr > out.lp.irr
    assert out.reconciles is True
    assert [t.label for t in out.tier_allocations][-1] == "Tier 1 (to 10%)"
