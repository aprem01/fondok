"""F&B revenue engine — outlet-level food, beverage, banquet projections.

Layered on top of :class:`RevenueEngine`. For limited-service hotels the
F&B and ancillary ratios are small; for full-service / lifestyle assets
F&B can be 20-30 percent of total revenue and ancillary 5-10 percent.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.underwriting import RevenueEngineOutput, RevenueProjectionYear

from .base import BaseEngine


class FBRevenueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    revenue: RevenueEngineOutput
    hotel_type: Literal["limited", "select", "full", "lifestyle", "luxury"] = "full"
    fb_ratio: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    other_ratio: Annotated[float, Field(ge=0.0, le=1.0)] | None = None


class FBRevenueYear(BaseModel):
    model_config = ConfigDict(extra="forbid")

    year: Annotated[int, Field(ge=1)]
    rooms_revenue: Annotated[float, Field(ge=0)]
    fb_revenue: Annotated[float, Field(ge=0)]
    # Resort Fees — a distinct USALI 11th-edition revenue line. Sam QA
    # #11: previously folded into other_revenue, hiding ~$1M/yr on real
    # deals. Defaults to 0 so legacy payloads stay valid; populated
    # when the upstream RevenueProjectionYear carries it (T-12 anchor)
    # OR when fb_revenue's resort-fees ratio is supplied.
    resort_fees: Annotated[float, Field(ge=0)] = 0.0
    other_revenue: Annotated[float, Field(ge=0)]
    total_revenue: Annotated[float, Field(ge=0)]


class FBRevenueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    years: list[FBRevenueYear]
    fb_ratio_used: float
    other_ratio_used: float


# Industry-typical F&B and ancillary share of rooms revenue.
DEFAULT_RATIOS: dict[str, tuple[float, float]] = {
    "limited": (0.02, 0.03),
    "select": (0.10, 0.04),
    "full": (0.29, 0.06),
    "lifestyle": (0.29, 0.06),
    "luxury": (0.45, 0.10),
}


class FBRevenueEngine(BaseEngine[FBRevenueInput, FBRevenueOutput]):
    """Layer F&B and ancillary revenue on top of rooms revenue."""

    name = "fb_revenue"

    def run(self, payload: FBRevenueInput) -> FBRevenueOutput:
        default_fb, default_other = DEFAULT_RATIOS.get(payload.hotel_type, DEFAULT_RATIOS["full"])
        fb_ratio = payload.fb_ratio if payload.fb_ratio is not None else default_fb
        other_ratio = payload.other_ratio if payload.other_ratio is not None else default_other

        years: list[FBRevenueYear] = []
        for proj in payload.revenue.years:
            rooms = proj.rooms_revenue
            fb = max(proj.fb_revenue, rooms * fb_ratio)
            # Resort Fees pass through from the revenue projection (which
            # the loader populates from T-12 actuals for Y1 and scales
            # forward by RevPAR growth). Falls back to 0 when no T-12
            # actual exists.
            resort_fees = proj.resort_fees
            other = max(proj.other_revenue, rooms * other_ratio)
            years.append(
                FBRevenueYear(
                    year=proj.year,
                    rooms_revenue=rooms,
                    fb_revenue=fb,
                    resort_fees=resort_fees,
                    other_revenue=other,
                    total_revenue=rooms + fb + resort_fees + other,
                )
            )

        return FBRevenueOutput(
            deal_id=payload.deal_id,
            years=years,
            fb_ratio_used=fb_ratio,
            other_ratio_used=other_ratio,
        )


__all__ = ["FBRevenueEngine", "FBRevenueInput", "FBRevenueOutput", "FBRevenueYear"]


def project_year_with_fb(
    year: RevenueProjectionYear,
    fb_ratio: float,
    other_ratio: float,
) -> RevenueProjectionYear:
    """Convenience helper: rebuild a RevenueProjectionYear with applied ratios."""
    fb = year.rooms_revenue * fb_ratio
    other = year.rooms_revenue * other_ratio
    return RevenueProjectionYear(
        year=year.year,
        occupancy=year.occupancy,
        adr=year.adr,
        revpar=year.revpar,
        rooms_revenue=year.rooms_revenue,
        fb_revenue=fb,
        other_revenue=other,
        total_revenue=year.rooms_revenue + fb + other,
    )
