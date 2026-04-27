"""AI analysis endpoints (memo drafting, variance, deal-level Q&A)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = None
    section: str | None = None


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    job_id: str
    status: str = "queued"


class VarianceFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    sponsor_value: Any | None = None
    underwriter_value: Any | None = None
    delta: float | None = None
    rationale: str | None = None


class VarianceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    flags: list[VarianceFlag] = Field(default_factory=list)


@router.post("/{deal_id}/analyze", response_model=AnalysisResponse)
async def analyze(deal_id: UUID, body: AnalysisRequest) -> AnalysisResponse:
    """Stub: kicks off an Analyst run for a section or freeform prompt."""
    logger.info("analysis(stub): deal=%s section=%s", deal_id, body.section)
    return AnalysisResponse(deal_id=deal_id, job_id="stub-job")


@router.get("/{deal_id}/variance", response_model=VarianceReport)
async def get_variance(deal_id: UUID) -> VarianceReport:
    """Stub."""
    return VarianceReport(deal_id=deal_id)
