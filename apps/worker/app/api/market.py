"""Market overview + comp-set endpoints.

These routes are intentionally thin until a real STR/CoStar feed is
wired in — the full market-research pass is future work. To stay
useful in the meantime, ``GET /market/{deal_id}/overview`` now reads
the deal row and surfaces what we *do* know (city, keys, brand,
service) so the web app's market header can render with real data
instead of nulls.

The proper STR/CoStar integration will populate ``occupancy_index``,
``adr_index``, ``revpar_index``, and a comp-set list. Until then those
fields stay null and the comps endpoint returns an empty list so the
UI can render an "awaiting market data" empty state.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


class MarketOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    market: str | None = None
    keys: int | None = None
    brand: str | None = None
    service: str | None = None
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
async def market_overview(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> MarketOverview:
    """Pull the deal row and surface its market-relevant fields.

    Real STR-driven RevPAR/ADR/Occupancy indices are still future work
    (TODO(str-integration)); the indices stay null until the feed lands.
    The web app should render the city/keys/brand block from this
    response and treat null indices as "awaiting market data".
    """
    row = (
        await session.execute(
            text(
                """
                SELECT city, keys, brand, service
                  FROM deals
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )
    m = row._mapping
    keys: int | None = None
    if m.get("keys") is not None:
        try:
            keys = int(m["keys"])
        except (TypeError, ValueError):
            keys = None
    return MarketOverview(
        deal_id=deal_id,
        market=m.get("city"),
        keys=keys,
        brand=m.get("brand"),
        service=m.get("service"),
    )


@router.get("/{deal_id}/comps", response_model=CompsResponse)
async def market_comps(deal_id: UUID) -> CompsResponse:
    """Comp-set endpoint.

    TODO(str-integration): pull comp set from the STR/CoStar feed
    keyed off the deal's city. Until then we return an empty list +
    a metadata flag so the UI renders an "awaiting market data" panel
    rather than a blank page.
    """
    return CompsResponse(
        deal_id=deal_id,
        comps=[],
        metadata={"source": "stub", "awaiting_integration": "str-costar"},
    )
