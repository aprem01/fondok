"""Settings endpoints — tenant-level configuration surface."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from ..auth import AuthContext, get_current_auth
from ..config import get_settings as _get_app_settings
from ..tenants import get_tenant

logger = logging.getLogger(__name__)
router = APIRouter()


class TenantSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    default_budget_usd: float
    llm_provider: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    # FON-93 — the caller's Clerk org and its registry row (``None`` when
    # the org has not been registered; see ``docs/WORKSPACE_ISOLATION.md``).
    org_id: str | None = None
    registry: dict[str, Any] | None = None


@router.get("/tenant", response_model=TenantSettings)
async def tenant_settings(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> TenantSettings:
    """The caller's tenant, as the worker resolved it for this request.

    FON-93: used to report ``DEFAULT_TENANT_ID`` for everyone — a stub
    that told every institution it was the demo tenant. Now it is the
    tenant the auth dependency resolved (the JWT's org), plus the
    registry row when one exists, so the web app and ops can confirm
    which workspace a session is scoped to.
    """
    s = _get_app_settings()
    registry = await get_tenant(auth.tenant_id)
    return TenantSettings(
        tenant_id=str(auth.tenant_id),
        default_budget_usd=s.DEFAULT_DEAL_BUDGET_USD,
        llm_provider=s.LLM_PROVIDER,
        org_id=auth.org_id,
        registry=registry.as_dict() if registry else None,
    )
