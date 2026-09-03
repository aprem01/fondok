"""F&B revenue engine — outlet-level food, beverage, banquet projections.

Layered on top of :class:`RevenueEngine`. For limited-service hotels the
F&B and ancillary ratios are small; for full-service / lifestyle assets
F&B can be 20-30 percent of total revenue and ancillary 5-10 percent.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fondok_schemas.provenance import ValueInput, ValueTrace, apply_states
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
    # FON-25/65 — per-value provenance sidecar (see provenance.py). Keyed by
    # dotted output path (e.g. "years[0].fb_revenue"). Pass-through revenue
    # lines read as ``linked`` (they come from the Revenue engine's grounded
    # projection); ratio-estimated lines and the year total read as
    # ``calculated``. Empty by default.
    provenance: dict[str, ValueTrace] = Field(default_factory=dict)


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
        # FON-25/65 — per-value provenance sidecar for the F&B revenue lines.
        prov: dict[str, ValueTrace] = {}
        for idx, proj in enumerate(payload.revenue.years):
            rooms = proj.rooms_revenue
            # Prefer the revenue projection's GROUNDED F&B (derived from the
            # T-12's actual F&B-per-occupied-room) over a ratio estimate.
            # The prior ``max()`` let a SEED-default fb_ratio (0.29) override
            # a real, lower actual (~0.24), overstating total revenue and
            # inflating Year-1 NOI margin (QA Harbor Palms: $2.42M actual →
            # $2.92M, $12.99M → $13.60M, 30.0% → 34.7%). Fall back to the
            # ratio only when the projection carries no F&B (no T-12 actual).
            fb_grounded = proj.fb_revenue > 0
            fb = proj.fb_revenue if fb_grounded else rooms * fb_ratio
            # Resort Fees pass through from the revenue projection (which
            # the loader populates from T-12 actuals for Y1 and scales
            # forward by RevPAR growth). Falls back to 0 when no T-12
            # actual exists.
            resort_fees = proj.resort_fees
            other_grounded = proj.other_revenue > 0
            other = proj.other_revenue if other_grounded else rooms * other_ratio
            total = rooms + fb + resort_fees + other
            years.append(
                FBRevenueYear(
                    year=proj.year,
                    rooms_revenue=rooms,
                    fb_revenue=fb,
                    resort_fees=resort_fees,
                    other_revenue=other,
                    total_revenue=total,
                )
            )

            # Rooms revenue is pulled straight from the Revenue engine's
            # projection — a cross-engine ``linked`` value.
            prov[f"years[{idx}].rooms_revenue"] = ValueTrace(
                value=rooms,
                inputs=[
                    ValueInput(
                        name="rooms_revenue",
                        value=rooms,
                        traces_to=f"revenue.years[{idx}].rooms_revenue",
                    )
                ],
                note="Rooms revenue from the Revenue engine's projection.",
            )
            # F&B — linked when the Revenue projection carried a grounded F&B
            # figure; otherwise a ratio estimate off rooms revenue (calculated).
            if fb_grounded:
                prov[f"years[{idx}].fb_revenue"] = ValueTrace(
                    value=fb,
                    inputs=[
                        ValueInput(
                            name="fb_revenue",
                            value=fb,
                            traces_to=f"revenue.years[{idx}].fb_revenue",
                        )
                    ],
                    note="Grounded F&B from the Revenue projection (T-12 anchored).",
                )
            else:
                prov[f"years[{idx}].fb_revenue"] = ValueTrace(
                    value=fb,
                    formula="fb_revenue = rooms_revenue × fb_ratio",
                    inputs=[
                        ValueInput(
                            name="rooms_revenue",
                            value=rooms,
                            traces_to=f"years[{idx}].rooms_revenue",
                        ),
                        ValueInput(name="fb_ratio", value=fb_ratio),
                    ],
                    note="Ratio estimate — no T-12 F&B actual on this year.",
                )
            # Resort fees pass through from the Revenue projection.
            prov[f"years[{idx}].resort_fees"] = ValueTrace(
                value=resort_fees,
                inputs=[
                    ValueInput(
                        name="resort_fees",
                        value=resort_fees,
                        traces_to=f"revenue.years[{idx}].resort_fees",
                    )
                ],
                note="Resort fees passed through from the Revenue projection.",
            )
            # Other operated — linked when grounded, else ratio estimate.
            if other_grounded:
                prov[f"years[{idx}].other_revenue"] = ValueTrace(
                    value=other,
                    inputs=[
                        ValueInput(
                            name="other_revenue",
                            value=other,
                            traces_to=f"revenue.years[{idx}].other_revenue",
                        )
                    ],
                    note="Grounded other operated revenue from the Revenue projection.",
                )
            else:
                prov[f"years[{idx}].other_revenue"] = ValueTrace(
                    value=other,
                    formula="other_revenue = rooms_revenue × other_ratio",
                    inputs=[
                        ValueInput(
                            name="rooms_revenue",
                            value=rooms,
                            traces_to=f"years[{idx}].rooms_revenue",
                        ),
                        ValueInput(name="other_ratio", value=other_ratio),
                    ],
                    note="Ratio estimate — no T-12 other-operated actual on this year.",
                )
            # Total revenue is composed by this engine (same-engine inputs →
            # calculated, not linked).
            prov[f"years[{idx}].total_revenue"] = ValueTrace(
                value=total,
                formula="total_revenue = rooms_revenue + fb_revenue + resort_fees + other_revenue",
                inputs=[
                    ValueInput(name="rooms_revenue", value=rooms, traces_to=f"years[{idx}].rooms_revenue"),
                    ValueInput(name="fb_revenue", value=fb, traces_to=f"years[{idx}].fb_revenue"),
                    ValueInput(name="resort_fees", value=resort_fees, traces_to=f"years[{idx}].resort_fees"),
                    ValueInput(name="other_revenue", value=other, traces_to=f"years[{idx}].other_revenue"),
                ],
            )

        return FBRevenueOutput(
            deal_id=payload.deal_id,
            years=years,
            fb_ratio_used=fb_ratio,
            other_ratio_used=other_ratio,
            provenance=apply_states(prov),
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
