"""Settings endpoints — tenant-level configuration surface."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from ..config import get_settings as _get_app_settings

logger = logging.getLogger(__name__)
router = APIRouter()


class TenantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    default_budget_usd: float
    llm_provider: str
    overrides: dict[str, Any] = Field(default_factory=dict)


@router.get("/tenant", response_model=TenantSettings)
async def tenant_settings() -> TenantSettings:
    """Stub: surfaces a few of the global Settings as a tenant view."""
    s = _get_app_settings()
    return TenantSettings(
        tenant_id=s.DEFAULT_TENANT_ID,
        default_budget_usd=s.DEFAULT_DEAL_BUDGET_USD,
        llm_provider=s.LLM_PROVIDER,
    )
