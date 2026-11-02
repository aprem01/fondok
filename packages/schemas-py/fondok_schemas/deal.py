"""Deal — the top-level project entity owned by an analyst."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .common import Risk


class DealStatus(str, Enum):
    """Where a deal stands — the seven-status lifecycle (Wave 5).

    ``Draft`` and ``In Progress`` are AUTOMATIC: they follow
    ``deals.lifecycle`` (a wizard draft is ``Draft``; Finish makes it
    ``In Progress``). ``Ready for Review``, ``IC Ready``, ``IC Approved`` and
    ``IC Not Approved`` are set by the team. ``Archived`` has its own route
    and an archived deal is restored to ``In Progress``.
    """

    DRAFT = "Draft"
    IN_PROGRESS = "In Progress"
    READY_FOR_REVIEW = "Ready for Review"
    IC_READY = "IC Ready"
    IC_APPROVED = "IC Approved"
    IC_NOT_APPROVED = "IC Not Approved"
    ARCHIVED = "Archived"


#: Every stored status, in lifecycle order (the Postgres CHECK and the web
#: filter list both read this order).
DEAL_STATUSES: tuple[str, ...] = tuple(s.value for s in DealStatus)

#: Statuses a member of the tenant may set with ``POST /deals/{id}/status``.
#: ``In Progress`` is here so a deal can be moved back (and restored from
#: Archived); ``Draft`` follows the wizard and ``Archived`` has its own route.
MANUAL_DEAL_STATUSES: frozenset[str] = frozenset(
    {
        DealStatus.IN_PROGRESS.value,
        DealStatus.READY_FOR_REVIEW.value,
        DealStatus.IC_READY.value,
        DealStatus.IC_APPROVED.value,
        DealStatus.IC_NOT_APPROVED.value,
    }
)

#: Manual statuses that need a completed model run first — the committee
#: reviews and decides on a modelled deal, never on an unrun one.
RUN_GATED_DEAL_STATUSES: frozenset[str] = frozenset(
    {
        DealStatus.IC_READY.value,
        DealStatus.IC_APPROVED.value,
        DealStatus.IC_NOT_APPROVED.value,
    }
)


def status_for_lifecycle(lifecycle: str | None) -> str:
    """The automatic status a deal's lifecycle implies: a wizard draft is
    ``Draft``; anything else (``active``, or a schema without the column)
    is ``In Progress``."""
    return (
        DealStatus.DRAFT.value
        if lifecycle == "draft"
        else DealStatus.IN_PROGRESS.value
    )


class DealStage(str, Enum):
    TEASER = "Teaser"
    UNDER_NDA = "Under NDA"
    LOI = "LOI"
    PSA = "PSA"


class Service(str, Enum):
    """Property type (FON-115, Sam 2026-10-20) — the standard hotel-RE
    taxonomy. Luxury / Lifestyle are brand-style and live in ``positioning``
    (STR chain scale) and ``brand``, not here."""

    FULL_SERVICE = "Full-Service"
    SELECT_SERVICE = "Select-Service"
    LIMITED_SERVICE = "Limited-Service"
    EXTENDED_STAY = "Extended-Stay"
    RESORT = "Resort"


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
