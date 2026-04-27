"""Market data — submarket KPIs, comp sets, and transaction comps."""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class BuyerType(str, Enum):
    REIT = "REIT"
    INSTITUTIONAL = "Institutional"
    PE_FUND = "PE Fund"
    PRIVATE = "Private"
    OWNER_OPERATOR = "Owner Operator"
    SOVEREIGN_WEALTH = "Sovereign Wealth"
    FAMILY_OFFICE = "Family Office"
    OTHER = "Other"


class MarketDataSource(str, Enum):
    STR = "STR"
    KALIBRI = "Kalibri Labs"
    COSTAR = "CoStar"
    INTERNAL = "Internal"
    OTHER = "Other"


class MarketData(BaseModel):
    """Submarket-level performance snapshot."""

    model_config = ConfigDict(extra="forbid")

    submarket: Annotated[str, Field(min_length=1, max_length=200)]
    market: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    occupancy: Annotated[float, Field(ge=0.0, le=1.0)]
    adr: Annotated[float, Field(ge=0)]
    revpar: Annotated[float, Field(ge=0)]
    supply_growth: float
    demand_growth: float
    yoy_revpar: float | None = None
    inventory_rooms: Annotated[int, Field(ge=0)] | None = None
    inventory_hotels: Annotated[int, Field(ge=0)] | None = None
    as_of: date
    source: MarketDataSource = MarketDataSource.STR


class TransactionComp(BaseModel):
    """A single comparable hotel sale."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    market: Annotated[str, Field(min_length=1, max_length=200)]
    date: date
    keys: Annotated[int, Field(gt=0)]
    sale_price: Annotated[float, Field(gt=0)]
    price_per_key: Annotated[float, Field(gt=0)]
    cap_rate: Annotated[float, Field(ge=0.0, le=0.30)]
    buyer_type: BuyerType
    buyer_name: str | None = None


class CompSet(BaseModel):
    """Curated list of competitive properties or transaction comps."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: str | None = None
    properties_count: Annotated[int, Field(ge=0)]
    transactions: list[TransactionComp] = Field(default_factory=list)
    used_in_deal_ids: list[UUID] = Field(default_factory=list)
    starred: bool = False
    hidden: bool = False
    updated_at: date
