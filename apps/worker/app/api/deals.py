"""Deal lifecycle endpoints — CRUD, status, HITL gates, memo streaming.

Phase-2 stubs: every route returns a typed Pydantic response so the
web client can wire against a stable contract while the agent + engine
implementations land underneath.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── request bodies ───────────────────────────


class CreateDealBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    city: str | None = None
    keys: int | None = Field(default=None, ge=1)
    service: str | None = None


class Gate1Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(approve|reject|edit)$")
    notes: str | None = None


class Gate2Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: str = Field(pattern=r"^(go|no-go|conditional)$")
    notes: str | None = None


# ─────────────────────────── response shapes ───────────────────────────


class DealSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    city: str | None = None
    keys: int | None = None
    service: str | None = None
    status: str = "Draft"
    deal_stage: str | None = None
    risk: str | None = None
    ai_confidence: float | None = None
    created_at: datetime
    updated_at: datetime


class DealStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: str
    deal_stage: str | None = None
    last_event: str | None = None


class GateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    gate: str
    accepted: bool = True
    next_state: str | None = None


class MemoEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    sections: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


# ─────────────────────────── routes ───────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _stub_summary(name: str = "Stub Deal", deal_id: UUID | None = None) -> DealSummary:
    settings = get_settings()
    return DealSummary(
        id=deal_id or uuid4(),
        tenant_id=UUID(settings.DEFAULT_TENANT_ID),
        name=name,
        status="Draft",
        created_at=_now(),
        updated_at=_now(),
    )


@router.get("", response_model=list[DealSummary])
async def list_deals() -> list[DealSummary]:
    """Stub: returns an empty list until the DB-backed query lands."""
    return []


@router.post("", response_model=DealSummary, status_code=status.HTTP_201_CREATED)
async def create_deal(body: CreateDealBody) -> DealSummary:
    """Stub: echoes the request body as a freshly minted deal."""
    return _stub_summary(name=body.name)


@router.get("/{deal_id}", response_model=DealSummary)
async def get_deal(deal_id: UUID) -> DealSummary:
    return _stub_summary(deal_id=deal_id)


@router.get("/{deal_id}/status", response_model=DealStatusResponse)
async def get_deal_status(deal_id: UUID) -> DealStatusResponse:
    return DealStatusResponse(id=deal_id, status="Draft", last_event="created")


@router.post("/{deal_id}/gate1", response_model=GateResponse)
async def gate1(deal_id: UUID, body: Gate1Body) -> GateResponse:
    """HITL Gate 1: accept / reject / edit the normalized spread."""
    logger.info("gate1(stub): deal=%s decision=%s", deal_id, body.decision)
    return GateResponse(
        id=deal_id,
        gate="gate1",
        accepted=body.decision == "approve",
        next_state="run_engines",
    )


@router.post("/{deal_id}/gate2", response_model=GateResponse)
async def gate2(deal_id: UUID, body: Gate2Body) -> GateResponse:
    """HITL Gate 2: final recommendation on the IC memo."""
    logger.info("gate2(stub): deal=%s recommendation=%s", deal_id, body.recommendation)
    return GateResponse(
        id=deal_id, gate="gate2", accepted=True, next_state="finalize"
    )


@router.get("/{deal_id}/memo", response_model=MemoEnvelope)
async def get_memo(deal_id: UUID) -> MemoEnvelope:
    """Final IC memo (JSON envelope). Stub returns empty sections."""
    return MemoEnvelope(deal_id=deal_id)


@router.get("/{deal_id}/memo/stream")
async def stream_memo(deal_id: UUID) -> dict[str, str]:
    """SSE stream of memo sections as the Analyst writes them.

    Stub: SSE wiring lands once the streaming Analyst is in place.
    """
    return {"deal_id": str(deal_id), "stream": "stub"}
