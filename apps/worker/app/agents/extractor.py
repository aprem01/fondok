"""Extractor agent — pulls structured data out of an OM / STR / P&L PDF.

Stub: the production extractor binds Sonnet to a strict Pydantic
envelope (financial periods, occupancy, ADR, line-item expenses, page
+ excerpt citations). Phase 2 returns an empty extraction so the
graph + API stay wired end-to-end.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..telemetry import trace_agent

logger = logging.getLogger(__name__)


class ExtractorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    document_uris: list[str] = Field(default_factory=list)


class ExtractorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    extracted_documents: list[Any] = Field(default_factory=list)
    model_calls: list[Any] = Field(default_factory=list)


@trace_agent("Extractor")
async def run_extractor(payload: ExtractorInput) -> ExtractorOutput:
    """Stub. Echoes the deal_id with an empty extraction payload."""
    logger.info(
        "extractor(stub): tenant=%s deal=%s docs=%d",
        payload.tenant_id,
        payload.deal_id,
        len(payload.document_uris),
    )
    return ExtractorOutput(deal_id=payload.deal_id)


__all__ = ["ExtractorInput", "ExtractorOutput", "run_extractor"]
