"""Normalizer agent — maps extracted line items onto the Fondok chart of accounts.

Stub: the production normalizer reconciles units (USD vs $K vs $M),
fiscal calendars, and STR market definitions, then emits a
``NormalizedFinancialSpread``. Phase 2 returns a placeholder envelope.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..telemetry import trace_agent

logger = logging.getLogger(__name__)


class NormalizerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    extracted_documents: list[Any] = Field(default_factory=list)


class NormalizerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    normalized_spread: Any | None = None
    model_calls: list[Any] = Field(default_factory=list)


@trace_agent("Normalizer")
async def run_normalizer(payload: NormalizerInput) -> NormalizerOutput:
    """Stub."""
    logger.info(
        "normalizer(stub): tenant=%s deal=%s docs=%d",
        payload.tenant_id,
        payload.deal_id,
        len(payload.extracted_documents),
    )
    return NormalizerOutput(deal_id=payload.deal_id, normalized_spread=None)


__all__ = ["NormalizerInput", "NormalizerOutput", "run_normalizer"]
