"""Router agent — classifies an incoming document or request into a downstream lane.

Stub: the production router will call Haiku to classify uploaded
documents (OM, STR, P&L, brand approval, …) and route them to the
appropriate Extractor pass. For Phase 2 this returns a deterministic
placeholder so the graph compiles and the API contract holds.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..telemetry import trace_agent

logger = logging.getLogger(__name__)


class RouterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    document_id: str | None = None
    hint: str | None = Field(
        default=None, description="Optional caller hint (e.g. 'STR', 'P&L')"
    )


class RouterOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    route: str = Field(description="Downstream lane: extractor | normalizer | analyst")
    rationale: str = ""
    model_calls: list[Any] = Field(default_factory=list)


@trace_agent("Router")
async def run_router(payload: RouterInput) -> RouterOutput:
    """Stub. Always routes to the extractor."""
    logger.info(
        "router(stub): tenant=%s deal=%s doc=%s hint=%s",
        payload.tenant_id,
        payload.deal_id,
        payload.document_id,
        payload.hint,
    )
    return RouterOutput(
        deal_id=payload.deal_id,
        route="extractor",
        rationale="stub: default route",
    )


__all__ = ["RouterInput", "RouterOutput", "run_router"]
