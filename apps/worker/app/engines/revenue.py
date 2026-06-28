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
"""

from __future__ import annotations

from fondok_schemas.underwriting import (
    RevenueEngineInput,
    RevenueEngineOutput,
    RevenueProjectionYear,
    SegmentYear,
)

from .base import BaseEngine

DAYS_PER_YEAR = 365


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

        for y in range(1, payload.hold_years + 1):
            if y == 1:
                occ = baseline_occ * (1.0 - y1_occ_disp)
                adr = baseline_adr * (1.0 - y1_adr_disp)
            else:
                # Year 2+ compounds from the stabilized baseline,
                # not from the displaced Y1, so PIP year doesn't
                # cascade into permanently depressed out-years.
                occ = min(
                    0.95,
                    baseline_occ * (1.0 + payload.occupancy_growth) ** (y - 1),
                )
                adr = baseline_adr * (1.0 + payload.adr_growth) ** (y - 1)
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
                        # Y1 displacement applies inside each segment.
                        # ``starting_adr`` baseline; segment ADR uses
                        # its own anchor with same displacement %.
                        seg_adr_yn = seg.adr * (1.0 - y1_adr_disp)
                    else:
                        seg_adr_yn = seg.adr * (1.0 + seg_g) ** (y - 1)
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


__all__ = ["RevenueEngine"]
