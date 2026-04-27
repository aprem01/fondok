"""Engine + scenario endpoints (the underwriting model side)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
router = APIRouter()


class EngineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = Field(
        description=(
            "revenue | fb_revenue | expense | capital | debt | returns | "
            "sensitivity | partnership"
        )
    )
    inputs: dict[str, Any] = Field(default_factory=dict)


class EngineRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    engine: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    overrides: dict[str, Any] = Field(default_factory=dict)


class ScenarioResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    scenario: str
    status: str = "queued"


@router.post("/{deal_id}/engines/run", response_model=EngineRunResponse)
async def run_engine(deal_id: UUID, body: EngineRunRequest) -> EngineRunResponse:
    """Stub: executes a single named engine."""
    logger.info("model(stub): run engine=%s deal=%s", body.engine, deal_id)
    return EngineRunResponse(deal_id=deal_id, engine=body.engine)


@router.post("/{deal_id}/scenarios", response_model=ScenarioResponse)
async def create_scenario(deal_id: UUID, body: ScenarioRequest) -> ScenarioResponse:
    """Stub: spawns a what-if scenario derived from the base case."""
    return ScenarioResponse(deal_id=deal_id, scenario=body.name)
