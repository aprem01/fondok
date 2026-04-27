"""Market overview + comp-set endpoints."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class MarketOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    market: str | None = None
    occupancy_index: float | None = None
    adr_index: float | None = None
    revpar_index: float | None = None


class Comp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    distance_miles: float | None = None
    keys: int | None = None
    chain_scale: str | None = None
    revpar: float | None = None


class CompsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    comps: list[Comp] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/{deal_id}/overview", response_model=MarketOverview)
async def market_overview(deal_id: UUID) -> MarketOverview:
    """Stub."""
    return MarketOverview(deal_id=deal_id)


@router.get("/{deal_id}/comps", response_model=CompsResponse)
async def market_comps(deal_id: UUID) -> CompsResponse:
    """Stub."""
    return CompsResponse(deal_id=deal_id)
