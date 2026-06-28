"""Revenue engine — rooms-revenue projection (occupancy x ADR x rooms-available).

Pure deterministic math. Given a starting occupancy and ADR, projects rooms,
F&B and ancillary revenue forward over the underwriting hold. Year 1 is
treated as the post-PIP stabilized year.

Wave 2 P2.1 — institutional revenue segmentation. When
``RevenueEngineInput.segments`` is non-empty, the engine projects rooms
revenue as Σ over five demand segments — transient_bar, transient_ota,
corporate, group, contract — each with its own ADR, mix share, and
channel-cost percentage. The canonical ``rooms_revenue`` line becomes NET
of channel cost so downstream P&L / Returns engines never double-count
distribution drag. When ``segments == []`` the engine runs the original
single-line path with byte-identical math; every legacy test still passes.

Wave 2 P2.4 — structured PIP displacement. When
``RevenueEngineInput.pip_displacement`` is populated, Y1 rooms revenue
scales by the monthly ``pct_rooms_offline_by_month`` schedule (with the
strategy-specific rules captured in ``_y1_revenue_factor_and_adr_drag``),
Y2 occupancy ramps linearly back to baseline over
``occupancy_recovery_months`` (after brand-multiplier adjustment), and Y2+
ADR is uplifted by ``revpar_index_post_reno``. When ``pip_displacement``
is ``None`` (or its strategy is ``"none"``) the engine falls back to the
legacy ``y1_*_displacement_pct`` math byte-for-byte.
"""

from __future__ import annotations

from fondok_schemas.underwriting import (
    PIPDisplacement,
    RevenueEngineInput,
    RevenueEngineOutput,
    RevenueProjectionYear,
    SegmentYear,
)

from .base import BaseEngine

DAYS_PER_YEAR = 365


# ─────────────── Brand displacement multipliers (Wave 2 P2.4) ───────────────
#
# Industry rules of thumb for how brand affiliation shapes the PIP
# recovery curve. Sources: STR Hotels Insights "Brand Standard
# Renovation Impact Survey" (2023); CBRE Hotels Horizons brand-recovery
# commentary. These are NOT hard data — analysts can override per-deal.
#
# Each tuple is ``(recovery_months_multiplier, revpar_index_multiplier)``:
#
#   * Marriott / Hilton — the industry baseline (1.0 / 1.0). Strict
#     brand standards drive a textbook recovery curve.
#   * IHG — slightly slower recovery; programs are stricter on launch
#     QC than Marriott.
#   * Hyatt — faster recovery; strong direct booking channel pulls
#     loyalists back early. Modest extra RevPAR uplift.
#   * Independent / soft-brand — fastest recovery (less brand-standard
#     overhead) but smaller starting RevPAR uplift relative to the
#     national brands.
_BRAND_DISPLACEMENT_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "Marriott": (1.0, 1.0),
    "Hilton": (1.0, 1.0),
    "IHG": (1.05, 1.0),
    "Hyatt": (0.95, 1.02),
    "Independent": (0.85, 1.03),
}


def _resolve_brand_multipliers(brand: str | None) -> tuple[float, float]:
    """Lookup ``(recovery_months_mult, revpar_index_mult)`` for a brand.

    Unknown / None brand defaults to the "Independent" curve (fastest
    recovery, modest uplift) — that's the safest assumption for a
    boutique deal with no published brand standards.
    """
    if brand is None:
        return _BRAND_DISPLACEMENT_MULTIPLIERS["Independent"]
    return _BRAND_DISPLACEMENT_MULTIPLIERS.get(
        brand, _BRAND_DISPLACEMENT_MULTIPLIERS["Independent"]
    )


def _y1_pct_schedule(pip: PIPDisplacement) -> list[float]:
    """Pad the analyst-supplied schedule to a full 12-month Y1 array.

    Months past the end of the supplied list contribute 0% offline
    (the reno wrapped). For ``wing_by_wing`` each pct is capped at 0.5
    inside the engine math, not here, so the audit trail keeps the raw
    analyst input intact.
    """
    sched = list(pip.pct_rooms_offline_by_month)[:12]
    while len(sched) < 12:
        sched.append(0.0)
    return sched


def _y1_revenue_factor_and_adr_drag(pip: PIPDisplacement) -> tuple[float, float]:
    """Return ``(rooms_revenue_factor_y1, y1_adr_drag)`` for the strategy.

    The factor is a single multiplier on what the un-displaced Y1
    rooms revenue would have been. The ADR drag is the multiplicative
    haircut applied to Y1 ADR (always 1.0 except wing_by_wing where
    we apply a 5% drag to reflect construction nuisance).

    Math:

    * ``rolling`` → factor = mean over 12 months of (1 - pct[m])
    * ``full_closure`` → same formula; months at 1.0 contribute 0
    * ``wing_by_wing`` → cap each pct at 0.5, then same formula;
      Y1 ADR drag = 0.95
    * ``none`` → factor = 1.0, drag = 1.0 (PIP object is effectively
      no-op; analyst should leave it None instead).
    """
    if pip.closure_strategy == "none":
        return 1.0, 1.0
    sched = _y1_pct_schedule(pip)
    if pip.closure_strategy == "wing_by_wing":
        sched = [min(0.5, p) for p in sched]
        adr_drag = 0.95
    else:
        adr_drag = 1.0
    factor = sum(1.0 - p for p in sched) / 12.0
    return factor, adr_drag


def _effective_recovery_months(
    pip: PIPDisplacement, brand_recovery_mult: float
) -> int:
    """Brand-adjusted recovery months, clamped to [0, 12] for Y2 accounting.

    Y3 is always full recovery in the engine, so capping at 12 matches
    the projection semantics. We round to the nearest integer month
    because the linear ramp inside ``_y2_occupancy_recovery_factor`` is
    month-indexed.
    """
    raw = int(round(pip.occupancy_recovery_months * brand_recovery_mult))
    return max(0, min(12, raw))


def _y2_occupancy_recovery_factor(
    pip: PIPDisplacement, brand_recovery_mult: float
) -> float:
    """Average Y2 occupancy factor relative to the stabilized baseline.

    Y1 ends with the hotel running at ``y1_revenue_factor`` of baseline.
    Y2 ramps linearly from that depressed start back to 1.0 over
    ``effective_recovery_months`` (capped at 12 within Y2 — Y3 is always
    full recovery). The average is what multiplies the un-displaced Y2
    occupancy.

    For ``closure_strategy='none'`` this returns 1.0 so the legacy code
    path is unchanged.
    """
    if pip.closure_strategy == "none":
        return 1.0
    y1_factor, _ = _y1_revenue_factor_and_adr_drag(pip)
    effective_recovery_months = _effective_recovery_months(
        pip, brand_recovery_mult
    )
    if effective_recovery_months == 0:
        return 1.0
    # Y2 starts at the Y1 ending factor. We model a linear ramp from
    # ``y1_factor`` at month 0 of Y2 to 1.0 at month
    # ``effective_recovery_months``. Months beyond that contribute 1.0.
    # The annual average over 12 months of Y2:
    ramp_total = 0.0
    for m in range(12):
        if m < effective_recovery_months:
            ramp_total += y1_factor + (1.0 - y1_factor) * (
                m / effective_recovery_months
            )
        else:
            ramp_total += 1.0
    return ramp_total / 12.0


class RevenueEngine(BaseEngine[RevenueEngineInput, RevenueEngineOutput]):
    """Project total revenue across rooms, F&B and other operated departments."""

    name = "revenue"

    def run(self, payload: RevenueEngineInput) -> RevenueEngineOutput:
        keys = payload.keys
        rooms_available = keys * DAYS_PER_YEAR
        years: list[RevenueProjectionYear] = []

        # `starting_*` represents the STABILIZED baseline (typically
        # the T-12 actual or the CBRE Year-1 forecast). Year 1 may be
        # disrupted by renovation displacement; Year 2 onwards compound
        # forward from the un-displaced baseline so a heavy PIP doesn't
        # permanently depress the projection.
        baseline_occ = payload.starting_occupancy
        baseline_adr = payload.starting_adr
        resort_fees = payload.starting_resort_fees
        y1_occ_disp = payload.y1_occupancy_displacement_pct
        y1_adr_disp = payload.y1_adr_displacement_pct
        use_segments = bool(payload.segments)

        # Wave 2 P2.4 — structured PIP displacement (closure strategy +
        # % rooms offline + brand recovery curve). When set with a real
        # strategy, this object overrides the legacy flat-pct path. When
        # ``None`` (or strategy == 'none'), the legacy math below runs
        # byte-identically.
        pip_v2 = payload.pip_displacement if (
            payload.pip_displacement is not None
            and payload.pip_displacement.closure_strategy != "none"
        ) else None
        if pip_v2 is not None:
            brand_recovery_mult, brand_revpar_mult = _resolve_brand_multipliers(
                pip_v2.brand
            )
            y1_revenue_factor, y1_adr_drag = _y1_revenue_factor_and_adr_drag(pip_v2)
            y2_occ_recovery_factor = _y2_occupancy_recovery_factor(
                pip_v2, brand_recovery_mult
            )
            effective_revpar_index = (
                pip_v2.revpar_index_post_reno * brand_revpar_mult
            )
        else:
            y1_revenue_factor = 1.0
            y1_adr_drag = 1.0
            y2_occ_recovery_factor = 1.0
            effective_revpar_index = 1.0

        for y in range(1, payload.hold_years + 1):
            if y == 1:
                if pip_v2 is not None:
                    # Y1 effective occ + ADR. We REPORT the year's
                    # effective occupancy (baseline × y1_revenue_factor)
                    # so the projection year's revpar = rooms_revenue /
                    # rooms_available reconciles. ADR drag only applies
                    # under wing_by_wing.
                    occ = baseline_occ * y1_revenue_factor
                    adr = baseline_adr * y1_adr_drag
                else:
                    occ = baseline_occ * (1.0 - y1_occ_disp)
                    adr = baseline_adr * (1.0 - y1_adr_disp)
            else:
                # Year 2+ compounds from the stabilized baseline,
                # not from the displaced Y1, so PIP year doesn't
                # cascade into permanently depressed out-years.
                base_occ_y = min(
                    0.95,
                    baseline_occ * (1.0 + payload.occupancy_growth) ** (y - 1),
                )
                base_adr_y = baseline_adr * (1.0 + payload.adr_growth) ** (y - 1)
                if pip_v2 is not None and y == 2:
                    occ = base_occ_y * y2_occ_recovery_factor
                    adr = base_adr_y * effective_revpar_index
                elif pip_v2 is not None:
                    # Y3+ — full recovery, full uplift.
                    occ = base_occ_y
                    adr = base_adr_y * effective_revpar_index
                else:
                    occ = base_occ_y
                    adr = base_adr_y
                resort_fees = resort_fees * (1.0 + payload.resort_fees_growth)

            occupied = rooms_available * occ

            # ─── Rooms revenue: segmented vs. single-line ───
            segment_breakdown: list[SegmentYear] = []
            if use_segments:
                # Per-segment yearly math. Each segment carries its own
                # ADR (post growth + Y1 displacement) and a channel-cost
                # percentage that captures OTA commissions, TMC fees,
                # group attrition, etc. Aggregate `rooms_revenue` is
                # NET of channel cost — this is the canonical line the
                # downstream P&L / Returns engines read.
                gross_total = 0.0
                net_total = 0.0
                for seg in payload.segments:
                    seg_g = (
                        seg.adr_growth
                        if seg.adr_growth is not None
                        else payload.adr_growth
                    )
                    if y == 1:
                        if pip_v2 is not None:
                            # Y1 segment ADR uses wing_by_wing drag (if
                            # applicable) — segments share the same
                            # construction-nuisance haircut.
                            seg_adr_yn = seg.adr * y1_adr_drag
                        else:
                            # Y1 displacement applies inside each segment.
                            # ``starting_adr`` baseline; segment ADR uses
                            # its own anchor with same displacement %.
                            seg_adr_yn = seg.adr * (1.0 - y1_adr_disp)
                    else:
                        seg_adr_grown = seg.adr * (1.0 + seg_g) ** (y - 1)
                        if pip_v2 is not None:
                            seg_adr_yn = seg_adr_grown * effective_revpar_index
                        else:
                            seg_adr_yn = seg_adr_grown
                    seg_occupied = occupied * seg.mix_pct
                    seg_gross = seg_occupied * seg_adr_yn
                    seg_channel = seg_gross * seg.channel_cost_pct
                    seg_net = seg_gross - seg_channel
                    gross_total += seg_gross
                    net_total += seg_net
                    segment_breakdown.append(
                        SegmentYear(
                            name=seg.name,
                            mix_pct=seg.mix_pct,
                            occupied_rooms=seg_occupied,
                            adr=seg_adr_yn,
                            channel_cost_pct=seg.channel_cost_pct,
                            gross_revenue=seg_gross,
                            net_revenue=seg_net,
                        )
                    )
                gross_rooms_revenue = gross_total
                rooms_revenue = net_total
                channel_cost_total = gross_total - net_total
            else:
                # Legacy single-line path — byte-identical to pre-Wave-2.
                rooms_revenue = occupied * adr
                gross_rooms_revenue = rooms_revenue
                channel_cost_total = 0.0

            revpar = rooms_revenue / rooms_available if rooms_available else 0.0

            fb_revenue = occupied * payload.fb_revenue_per_occupied_room
            other_revenue = rooms_revenue * payload.other_revenue_pct_of_rooms
            total_revenue = rooms_revenue + fb_revenue + resort_fees + other_revenue

            years.append(
                RevenueProjectionYear(
                    year=y,
                    occupancy=occ,
                    adr=adr,
                    revpar=revpar,
                    rooms_revenue=rooms_revenue,
                    fb_revenue=fb_revenue,
                    resort_fees=resort_fees,
                    other_revenue=other_revenue,
                    total_revenue=total_revenue,
                    segment_breakdown=segment_breakdown,
                    gross_rooms_revenue=gross_rooms_revenue,
                    channel_cost_total=channel_cost_total,
                )
            )

        if len(years) >= 2 and years[0].total_revenue > 0:
            n = len(years) - 1
            cagr = (years[-1].total_revenue / years[0].total_revenue) ** (1 / n) - 1
        else:
            cagr = 0.0

        return RevenueEngineOutput(
            deal_id=payload.deal_id,
            years=years,
            total_revenue_cagr=cagr,
        )


__all__ = [
    "RevenueEngine",
    "_BRAND_DISPLACEMENT_MULTIPLIERS",
    "_resolve_brand_multipliers",
]
