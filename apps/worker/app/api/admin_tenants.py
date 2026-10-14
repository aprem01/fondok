"""Tenant registry administration — FON-93 institution workspace isolation.

Endpoints (mounted under ``/admin`` beside :mod:`app.api.admin_cost`)
---------------------------------------------------------------------
* ``GET  /admin/tenants`` — list registry rows. A caller whose own tenant
  is ``kind=internal`` sees every row; any other caller sees only its own
  row (or an empty list when its org is not registered). Nothing here
  reveals another institution's existence to an external tenant.
* ``POST /admin/tenants`` — register (or re-register) a Clerk org as a
  tenant. Only callers whose own tenant is a registered, active
  ``internal`` tenant may do this — the registry is Fondok-operated, an
  external admin cannot grant itself or anyone else a workspace. Upsert
  semantics: posting an org that already exists (for example one the
  Clerk webhook parked as ``pending_review``) updates its name / kind /
  status, which is how an internal admin approves a new institution.

Both are behind ``require_role("admin")``: a ``member`` of an internal
org still cannot administer tenants.

Bootstrapping: the very first internal tenant cannot be created through
``POST`` (nobody is internal yet). ``scripts/register_tenant.py`` writes
it directly against the database from a trusted shell.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..auth import AuthContext, require_role
from ..tenants import (
    KINDS,
    STATUSES,
    Tenant,
    get_tenant,
    list_tenants,
    upsert_tenant,
)
from .deals import coerce_org_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


class RegisterTenantBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    clerk_org_id: str = Field(min_length=5, max_length=200, pattern=r"^org_")
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(default="external")
    status: str = Field(default="active")


async def _caller_tenant(auth: AuthContext) -> Tenant | None:
    return await get_tenant(auth.tenant_id)


@router.get("/tenants")
async def list_registered_tenants(
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """List registry rows — all of them for an internal tenant, else own row."""
    me = await _caller_tenant(auth)
    if me is not None and me.is_serving and me.is_internal:
        rows = await list_tenants()
        scope = "all"
    else:
        rows = [me] if me is not None else []
        scope = "own"
    return {
        "scope": scope,
        "caller_tenant_id": str(auth.tenant_id),
        "tenants": [t.as_dict() for t in rows],
    }


@router.post("/tenants", status_code=status.HTTP_201_CREATED)
async def register_tenant(
    body: RegisterTenantBody,
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """Register a Clerk organization as a tenant (internal callers only)."""
    me = await _caller_tenant(auth)
    if me is None or not me.is_serving or not me.is_internal:
        # 403, not 404: the route's existence is not a secret, the
        # registry's contents are (and we return none of them here).
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="tenant_registry_requires_internal_tenant",
        )
    if body.kind not in KINDS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"kind must be one of {sorted(KINDS)}",
        )
    if body.status not in STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"status must be one of {sorted(STATUSES)}",
        )
    tenant_id = coerce_org_tenant_id(body.clerk_org_id)
    if tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="clerk_org_id must be a Clerk organization id (org_…)",
        )
    stored = await upsert_tenant(
        tenant_id=tenant_id,
        clerk_org_id=body.clerk_org_id,
        name=body.name,
        kind=body.kind,
        status=body.status,
    )
    logger.info(
        "admin/tenants: %s registered org=%s tenant=%s kind=%s status=%s",
        auth.user_id or auth.source,
        body.clerk_org_id,
        tenant_id,
        body.kind,
        body.status,
    )
    return stored.as_dict()


__all__ = ["router"]
