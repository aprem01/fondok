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

import calendar
from datetime import date
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.partnership import (
    PartnerReturn,
    PartnershipInput,
    PartnershipOutput,
    WaterfallTier,
)

from .base import BaseEngine
from .returns import irr, xirr


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
    # FON-66/67 — monthly waterfall. ``period='monthly'`` runs an institutional
    # monthly IRR-hurdle waterfall over ``cash_flows_monthly`` (the levered
    # monthly series: month 0 = close, negative = equity draw, positive =
    # distribution). ``'annual'`` keeps the legacy annual waterfall. The caller
    # auto-selects monthly for deals with sub-annual timing (mid-year close,
    # off-cycle exit, or a refi), else annual — so existing deals are unchanged.
    period: Literal["annual", "monthly"] = "annual"
    cash_flows_monthly: list[float] = Field(default_factory=list)
    # FON-66/67 — acquisition close date (ISO). The monthly waterfall accrues
    # the preferred return on an actual/365 fractional-year basis between the
    # real month-end dates — Begin*(1+pref)^(days/365)-Begin — matching an
    # institutional model. None falls back to nominal 365/12-day periods.
    close_date: str | None = None


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


def _monthly_period_days(close_iso: str | None, n: int) -> list[float]:
    """Actual days in each monthly accrual period (index 0 = close, no accrual).

    Dates are the acquisition close date followed by successive month-ends, so
    the preferred return compounds over the true day count between cash-flow
    dates. Falls back to nominal 365/12-day periods when no close date is set.
    """
    if n <= 0:
        return []
    days = [0.0] * n
    close: date | None = None
    if isinstance(close_iso, str) and close_iso.strip():
        try:
            close = date.fromisoformat(close_iso.strip()[:10])
        except ValueError:
            close = None
    if close is None:
        for k in range(1, n):
            days[k] = 365.0 / 12.0
        return days
    prev = close
    year, month = close.year, close.month
    for k in range(1, n):
        month += 1
        if month > 12:
            month = 1
            year += 1
        cur = date(year, month, calendar.monthrange(year, month)[1])
        days[k] = float((cur - prev).days)
        prev = cur
    return days


class PartnershipEngine(BaseEngine[PartnershipInputExt, PartnershipOutputExt]):
    """Distribute annual cash through a tiered preferred-return waterfall."""

    name = "partnership"

    def _run_monthly(self, payload: PartnershipInputExt) -> PartnershipOutputExt:
        """Institutional monthly IRR-hurdle waterfall.

        Walks the levered monthly cash-flow series (month 0 = close): negative
        months are equity draws funded pro-rata (LP/GP %), positive months are
        distributions split tier-by-tier. Each tier tracks the LP capital
        account compounded at that tier's hurdle rate; the LP has cleared a
        hurdle when its balance is paid down to zero, at which point the split
        steps to the next tier. Mirrors a standard PE promote waterfall (and
        the Kimpton source model's monthly Waterfall tab).
        """
        gp_pct = payload.gp_equity_pct
        lp_pct = payload.lp_equity_pct
        tiers = sorted(payload.waterfall, key=lambda t: t.hurdle_rate)
        n = len(tiers)
        lp_bal = [0.0] * n  # LP capital compounded at each tier's hurdle

        gp_cf: list[float] = []
        lp_cf: list[float] = []
        gp_contrib_total = 0.0
        lp_contrib_total = 0.0
        promote_total = 0.0

        # Actual/365 day counts between the real month-end cash-flow dates.
        period_days = _monthly_period_days(payload.close_date, len(payload.cash_flows_monthly))

        for idx, cf in enumerate(payload.cash_flows_monthly):
            # Accrue the preferred return on an actual/365 fractional-year basis:
            # Begin*(1+hurdle)^(days/365) - Begin, matching an institutional model.
            frac = period_days[idx] / 365.0
            if frac > 0:
                for i, t in enumerate(tiers):
                    lp_bal[i] *= (1.0 + t.hurdle_rate) ** frac

            gp_take = 0.0
            lp_take = 0.0
            if cf < -1e-9:
                # Equity draw funded pro-rata; adds to every hurdle balance.
                draw = -cf
                lp_c = draw * lp_pct
                gp_c = draw * gp_pct
                for i in range(n):
                    lp_bal[i] += lp_c
                gp_cf.append(-gp_c)
                lp_cf.append(-lp_c)
                lp_contrib_total += lp_c
                gp_contrib_total += gp_c
                continue

            remaining = cf
            for i, t in enumerate(tiers):
                if remaining <= 1e-9:
                    break
                if lp_bal[i] <= 1e-9:
                    continue  # LP already cleared this hurdle
                lp_split = t.lp_split
                # Cash needed at this tier so the LP's share zeroes its balance.
                dist_to_clear = lp_bal[i] / lp_split if lp_split > 1e-9 else remaining
                dist = min(remaining, dist_to_clear)
                lp_share = dist * lp_split
                gp_share = dist * t.gp_split
                promote_total += max(0.0, gp_share - dist * gp_pct)
                lp_take += lp_share
                gp_take += gp_share
                for j in range(n):
                    lp_bal[j] -= lp_share  # distribution counts toward every hurdle
                remaining -= dist
            if remaining > 1e-9:  # residual → top tier
                top = tiers[-1]
                ls = remaining * top.lp_split
                gs = remaining * top.gp_split
                promote_total += max(0.0, gs - remaining * gp_pct)
                lp_take += ls
                gp_take += gs
                for j in range(n):
                    lp_bal[j] -= ls
            gp_cf.append(gp_take)
            lp_cf.append(lp_take)

        # Annualized IRRs via XIRR over the monthly time points (month/12 years).
        times = [m / 12.0 for m in range(len(gp_cf))]
        gp_irr = xirr(times, gp_cf)
        lp_irr = xirr(times, lp_cf)

        gp_dist = sum(v for v in gp_cf if v > 0)
        lp_dist = sum(v for v in lp_cf if v > 0)
        gp_em = gp_dist / gp_contrib_total if gp_contrib_total else 0.0
        lp_em = lp_dist / lp_contrib_total if lp_contrib_total else 0.0

        return PartnershipOutputExt(
            deal_id=payload.deal_id,
            gp=PartnerReturn(
                partner="GP",
                contributed_equity=gp_contrib_total,
                distributions=gp_dist,
                irr=gp_irr,
                equity_multiple=gp_em,
            ),
            lp=PartnerReturn(
                partner="LP",
                contributed_equity=lp_contrib_total,
                distributions=lp_dist,
                irr=lp_irr,
                equity_multiple=lp_em,
            ),
            promote_earned=promote_total,
            gp_cash_flows=gp_cf,
            lp_cash_flows=lp_cf,
            promote_amount=promote_total,
        )

    def run(self, payload: PartnershipInputExt) -> PartnershipOutputExt:
        if payload.period == "monthly" and payload.cash_flows_monthly:
            return self._run_monthly(payload)
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
