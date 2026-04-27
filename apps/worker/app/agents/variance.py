"""Variance agent — flags deviation between the OM and the underwriter's model.

Stub: the production variance agent diffs sponsor numbers against the
deterministic engine outputs and surfaces material gaps with rationales.
Phase 2 returns an empty report.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..telemetry import trace_agent

logger = logging.getLogger(__name__)


class VarianceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    sponsor_view: dict[str, Any] = Field(default_factory=dict)
    engine_view: dict[str, Any] = Field(default_factory=dict)


class VarianceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    flags: list[dict[str, Any]] = Field(default_factory=list)
    model_calls: list[Any] = Field(default_factory=list)


@trace_agent("Variance")
async def run_variance(payload: VarianceInput) -> VarianceOutput:
    """Stub."""
    logger.info(
        "variance(stub): tenant=%s deal=%s",
        payload.tenant_id,
        payload.deal_id,
    )
    return VarianceOutput(deal_id=payload.deal_id, flags=[])


__all__ = ["VarianceInput", "VarianceOutput", "run_variance"]
