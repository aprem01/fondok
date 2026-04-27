"""Partnership / waterfall — GP/LP economics on top of Returns Engine outputs."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .underwriting import ReturnsEngineOutput


class WaterfallTier(BaseModel):
    """One tier in a preferred-return waterfall."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=80)]
    hurdle_rate: Annotated[float, Field(ge=0.0, le=1.0)]
    gp_split: Annotated[float, Field(ge=0.0, le=1.0)]
    lp_split: Annotated[float, Field(ge=0.0, le=1.0)]

    @model_validator(mode="after")
    def _splits_sum_to_one(self) -> "WaterfallTier":
        total = round(self.gp_split + self.lp_split, 6)
        if total != 1.0:
            raise ValueError(
                f"gp_split + lp_split must equal 1.0 (got {total})"
            )
        return self


class PartnershipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    returns: ReturnsEngineOutput
    total_equity: Annotated[float, Field(gt=0)]
    gp_equity_pct: Annotated[float, Field(ge=0.0, le=1.0)]
    lp_equity_pct: Annotated[float, Field(ge=0.0, le=1.0)]
    waterfall: list[WaterfallTier] = Field(min_length=1)
    catch_up: bool = False


class PartnerReturn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner: Annotated[str, Field(pattern=r"^(GP|LP)$")]
    contributed_equity: Annotated[float, Field(ge=0)]
    distributions: Annotated[float, Field(ge=0)]
    irr: float
    equity_multiple: Annotated[float, Field(ge=0)]


class PartnershipOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    gp: PartnerReturn
    lp: PartnerReturn
    promote_earned: Annotated[float, Field(ge=0)] = 0.0
