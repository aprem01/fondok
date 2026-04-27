"""Analyst agent — drafts the IC memo from the normalized spread + engine outputs.

Stub: the production analyst is the Opus-4.7 writer that consumes
normalized financials, engine outputs (revenue, F&B, debt, returns,
sensitivity), and market comps to produce the IC narrative. Phase 2
returns an empty draft.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..telemetry import trace_agent

logger = logging.getLogger(__name__)


class AnalystInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    normalized_spread: Any | None = None
    engine_results: dict[str, Any] = Field(default_factory=dict)


class AnalystOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    memo: Any | None = None
    model_calls: list[Any] = Field(default_factory=list)


@trace_agent("Analyst")
async def run_analyst(payload: AnalystInput) -> AnalystOutput:
    """Stub."""
    logger.info(
        "analyst(stub): tenant=%s deal=%s engines=%s",
        payload.tenant_id,
        payload.deal_id,
        list(payload.engine_results.keys()),
    )
    return AnalystOutput(deal_id=payload.deal_id, memo=None)


__all__ = ["AnalystInput", "AnalystOutput", "run_analyst"]
