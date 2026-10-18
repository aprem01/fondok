"""Operator health view — ``GET /admin/health`` (FON-93 / client-readiness sweep).

The public ``GET /health`` is a liveness probe anyone on the internet can
read, so it says only ``{status, version, db, degraded_reasons}`` plus the
boot invariants. Everything that describes the deployment itself —
storage bucket and region, the auth posture flags, the embeddings model,
the per-tenant extraction-cache counters (keyed by Clerk org id) — is
infrastructure and tenant-enumeration detail and lives here, behind the
same gate ``POST /admin/tenants`` uses: an ``admin`` of a registered,
active **internal** tenant. An external admin gets 403; the response never
tells them what they are missing.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, require_role
from ..database import get_session
from ..tenants import get_tenant
from .health import ops_snapshot, public_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()

ADMIN_HEALTH_REQUIRES_INTERNAL_TENANT = "admin_health_requires_internal_tenant"


@router.get("/health")
async def admin_health(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """Public probe fields + the operator blocks (``raw_store``, ``auth``,
    ``semantic_search``, ``extraction_cache``). Internal tenants only."""
    me = await get_tenant(auth.tenant_id)
    if me is None or not me.is_serving or not me.is_internal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ADMIN_HEALTH_REQUIRES_INTERNAL_TENANT,
        )
    body = await public_snapshot(session)
    body.update(await ops_snapshot(session))
    return body
