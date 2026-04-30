"""Revenue engine — rooms-revenue projection (occupancy x ADR x rooms-available).

Pure deterministic math. Given a starting occupancy and ADR, projects rooms,
F&B and ancillary revenue forward over the underwriting hold. Year 1 is
treated as the post-PIP stabilized year.
"""

from __future__ import annotations

from fondok_schemas.underwriting import (
    RevenueEngineInput,
    RevenueEngineOutput,
    RevenueProjectionYear,
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

        occ = payload.starting_occupancy
        adr = payload.starting_adr
        resort_fees = payload.starting_resort_fees

        for y in range(1, payload.hold_years + 1):
            if y > 1:
                occ = min(0.95, occ * (1.0 + payload.occupancy_growth))
                adr = adr * (1.0 + payload.adr_growth)
                resort_fees = resort_fees * (1.0 + payload.resort_fees_growth)

            occupied = rooms_available * occ
            rooms_revenue = occupied * adr
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
