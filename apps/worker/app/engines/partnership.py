"""Partnership engine — JV waterfall, promote tiers, GP/LP returns split.

Implements an annual European-style waterfall:

    Tier 0  Return of contributed capital, pro-rata
    Tier 1  Preferred return (e.g. 8%) on unreturned capital, pro-rata
    Tier 2+ Promote tiers — distribute residual at GP/LP split until
            cumulative LP IRR meets the next hurdle, then move to the
            next tier

Annual cash flows are walked year-by-year so cumulative LP IRR controls
which tier the residual lands in.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.partnership import (
    PartnerReturn,
    PartnershipInput,
    PartnershipOutput,
    WaterfallTier,
)

from .base import BaseEngine
from .returns import irr


class PartnershipInputExt(BaseModel):
    """Self-contained input — bypasses the canonical PartnershipInput when the
    caller already has a flat cash flow series rather than a ReturnsEngineOutput.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    total_equity: Annotated[float, Field(gt=0)]
    gp_equity_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.10
    lp_equity_pct: Annotated[float, Field(ge=0.0, le=1.0)] = 0.90
    pref_rate: Annotated[float, Field(ge=0.0, le=0.30)] = 0.08
    waterfall: list[WaterfallTier] = Field(min_length=1)
    cash_flows: list[float] = Field(
        min_length=1,
        description="Annual project distributable cash; index 0 = Year 1.",
    )
    catch_up: bool = False


class PartnershipOutputExt(PartnershipOutput):
    model_config = ConfigDict(extra="forbid")

    gp_cash_flows: list[float] = Field(default_factory=list)
    lp_cash_flows: list[float] = Field(default_factory=list)
    promote_amount: Annotated[float, Field(ge=0)] = 0.0


def _lp_irr_to_date(
    lp_contributed: float, lp_distributions_so_far: list[float]
) -> float:
    flows = [-lp_contributed] + lp_distributions_so_far
    return irr(flows)


class PartnershipEngine(BaseEngine[PartnershipInputExt, PartnershipOutputExt]):
    """Distribute annual cash through a tiered preferred-return waterfall."""

    name = "partnership"

    def run(self, payload: PartnershipInputExt) -> PartnershipOutputExt:
        gp_eq = payload.total_equity * payload.gp_equity_pct
        lp_eq = payload.total_equity * payload.lp_equity_pct

        gp_unreturned = gp_eq
        lp_unreturned = lp_eq
        gp_pref_accrued = 0.0
        lp_pref_accrued = 0.0
        promote_total = 0.0

        gp_cf: list[float] = []
        lp_cf: list[float] = []

        # Sort waterfall tiers by hurdle ascending — we step up tiers as the
        # cumulative LP IRR clears each hurdle.
        tiers_sorted = sorted(payload.waterfall, key=lambda t: t.hurdle_rate)

        lp_distributions: list[float] = []

        for cash in payload.cash_flows:
            remaining = cash
            gp_take = 0.0
            lp_take = 0.0

            # Accrue preferred return on unreturned capital (annual compounding).
            gp_pref_accrued += gp_unreturned * payload.pref_rate
            lp_pref_accrued += lp_unreturned * payload.pref_rate

            # Tier 0 — return of capital, pro-rata
            total_unreturned = gp_unreturned + lp_unreturned
            if remaining > 0 and total_unreturned > 0:
                ret = min(remaining, total_unreturned)
                gp_share = ret * (gp_unreturned / total_unreturned)
                lp_share = ret - gp_share
                gp_unreturned -= gp_share
                lp_unreturned -= lp_share
                gp_take += gp_share
                lp_take += lp_share
                remaining -= ret

            # Tier 1 — preferred return, pro-rata until pref accruals are paid
            total_pref = gp_pref_accrued + lp_pref_accrued
            if remaining > 0 and total_pref > 0:
                pay = min(remaining, total_pref)
                gp_share = pay * (gp_pref_accrued / total_pref) if total_pref else 0.0
                lp_share = pay - gp_share
                gp_pref_accrued -= gp_share
                lp_pref_accrued -= lp_share
                gp_take += gp_share
                lp_take += lp_share
                remaining -= pay

            # Promote tiers — climb tiers as cumulative LP IRR clears hurdles
            for tier in tiers_sorted:
                if remaining <= 0:
                    break
                # Estimate LP IRR if we add a tiny bit to this tier
                trial_lp = lp_take
                # Conservative step: pour a slice and check IRR
                slice_size = min(remaining, max(1.0, remaining / max(1, len(tiers_sorted))))
                lp_slice = slice_size * tier.lp_split
                gp_slice = slice_size * tier.gp_split
                # Project cumulative LP IRR if this slice is paid
                projected_lp_dist = list(lp_distributions)
                # The current year's distribution-in-progress
                # — append working year LP take + slice to test
                if len(projected_lp_dist) < len(payload.cash_flows):
                    # Pad the in-progress year
                    in_progress = lp_take + lp_slice
                    projected_lp_dist.append(in_progress)
                trial_irr = _lp_irr_to_date(lp_eq, projected_lp_dist)

                if trial_irr <= tier.hurdle_rate + 1e-6:
                    # Pour the entire remaining cash through this tier
                    gp_share = remaining * tier.gp_split
                    lp_share = remaining * tier.lp_split
                    promote_share = max(0.0, gp_share - remaining * payload.gp_equity_pct)
                    promote_total += promote_share
                    gp_take += gp_share
                    lp_take += lp_share
                    remaining = 0.0
                    break

            # If we still have residual, it goes to the highest tier
            if remaining > 0:
                top = tiers_sorted[-1]
                gp_share = remaining * top.gp_split
                lp_share = remaining * top.lp_split
                promote_total += max(0.0, gp_share - remaining * payload.gp_equity_pct)
                gp_take += gp_share
                lp_take += lp_share
                remaining = 0.0

            gp_cf.append(gp_take)
            lp_cf.append(lp_take)
            lp_distributions.append(lp_take)

        # Final IRRs and multiples
        gp_flows = [-gp_eq] + gp_cf
        lp_flows = [-lp_eq] + lp_cf
        gp_irr = irr(gp_flows)
        lp_irr = irr(lp_flows)

        gp_distributions_total = sum(gp_cf)
        lp_distributions_total = sum(lp_cf)
        gp_em = gp_distributions_total / gp_eq if gp_eq else 0.0
        lp_em = lp_distributions_total / lp_eq if lp_eq else 0.0

        return PartnershipOutputExt(
            deal_id=payload.deal_id,
            gp=PartnerReturn(
                partner="GP",
                contributed_equity=gp_eq,
                distributions=gp_distributions_total,
                irr=gp_irr,
                equity_multiple=gp_em,
            ),
            lp=PartnerReturn(
                partner="LP",
                contributed_equity=lp_eq,
                distributions=lp_distributions_total,
                irr=lp_irr,
                equity_multiple=lp_em,
            ),
            promote_earned=promote_total,
            gp_cash_flows=gp_cf,
            lp_cash_flows=lp_cf,
            promote_amount=promote_total,
        )


__all__ = [
    "PartnershipEngine",
    "PartnershipInputExt",
    "PartnershipOutputExt",
]


# Keep canonical types importable from this module.
_ = (PartnershipInput,)
