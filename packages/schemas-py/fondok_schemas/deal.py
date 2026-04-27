"""Deal — the top-level project entity owned by an analyst."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .common import Risk


class DealStatus(str, Enum):
    DRAFT = "Draft"
    IN_REVIEW = "In Review"
    IC_READY = "IC Ready"
    ARCHIVED = "Archived"


class DealStage(str, Enum):
    TEASER = "Teaser"
    UNDER_NDA = "Under NDA"
    LOI = "LOI"
    PSA = "PSA"


class Service(str, Enum):
    SELECT_SERVICE = "Select Service"
    FULL_SERVICE = "Full Service"
    LIFESTYLE = "Lifestyle"
    LUXURY = "Luxury"
    LIMITED_SERVICE = "Limited Service"
    EXTENDED_STAY = "Extended Stay"


class ReturnProfile(str, Enum):
    CORE = "Core"
    VALUE_ADD = "Value Add"
    OPPORTUNISTIC = "Opportunistic"


class PositioningTier(str, Enum):
    DEFAULT = "Default"
    LUXURY = "Luxury"
    UPSCALE = "Upscale"
    ECONOMY = "Economy"


class Deal(BaseModel):
    """Hotel acquisition deal — the central record."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1, max_length=200)]
    city: Annotated[str, Field(min_length=1, max_length=120)]
    keys: Annotated[int, Field(gt=0, description="Number of guest rooms.")]
    service: Service
    status: DealStatus
    deal_stage: DealStage
    risk: Risk
    ai_confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    created_at: datetime
    updated_at: datetime
    assignee_id: UUID | None = None

    return_profile: ReturnProfile | None = None
    positioning: PositioningTier | None = None
    brand: str | None = None
    purchase_price: Annotated[float | None, Field(default=None, ge=0)] = None


class DealSummary(BaseModel):
    """Lightweight projection used in list/grid views and the dashboard."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    name: str
    city: str
    keys: int
    service: Service
    status: DealStatus
    deal_stage: DealStage
    risk: Risk
    ai_confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    revpar: float | None = None
    irr: float | None = None
    noi: float | None = None
    docs_complete: int | None = None
    docs_total: int | None = None
    assignee_initials: str | None = None
    updated_at: datetime
