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
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..auth import AuthContext, require_role
from ..database import get_session
from ..tenants import (
    KINDS,
    STATUSES,
    DataPolicyChoice,
    Tenant,
    TenantDataPolicies,
    get_tenant,
    list_tenants,
    set_tenant_data_policies,
    upsert_tenant,
)
from .deals import coerce_org_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()

TENANT_DATA_POLICIES_REQUIRE_INTERNAL_TENANT = "tenant_data_policies_require_internal_tenant"
TENANT_NOT_REGISTERED = "tenant_not_registered"
DATA_POLICY_CHANGED_ACTION = "tenant.data_policy_changed"


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
    if tenant_id == auth.tenant_id and (body.kind != "internal" or body.status != "active"):
        # Re-registering the CALLER's own org as external / suspended /
        # pending_review would, with REQUIRE_REGISTERED_TENANT on, lock
        # every internal user out within the cache TTL — and this route
        # needs an internal caller, so nothing over HTTP could undo it
        # (only scripts/register_tenant.py from a shell). Refuse; renames
        # of the own row (kind=internal, status=active) still go through.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot_demote_own_internal_tenant",
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


# ─────────────────────── data policies (FON-116 / FON-96) ───────────────────────


class DataPoliciesPatchBody(BaseModel):
    """A partial update: only the keys present change. ``extra="forbid"`` makes
    a mistyped key a 422 rather than a silent no-op that leaves the tenant on
    ``allow``."""

    model_config = ConfigDict(extra="forbid")

    costar_derived_defaults: DataPolicyChoice | None = None


async def _require_internal_admin(auth: AuthContext) -> Tenant:
    me = await _caller_tenant(auth)
    if me is None or not me.is_serving or not me.is_internal:
        # 403, not 404: the route is not a secret; the target's policy is.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=TENANT_DATA_POLICIES_REQUIRE_INTERNAL_TENANT,
        )
    return me


def _policy_response(target: Tenant) -> dict[str, Any]:
    return {"tenant_id": str(target.id), "data_policies": target.data_policies.as_dict()}


@router.get("/tenants/{tenant_id}/data_policies")
async def get_tenant_data_policies(
    tenant_id: UUID,
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """The data policies of one registered tenant (internal admins only)."""
    await _require_internal_admin(auth)
    target = await get_tenant(tenant_id, use_cache=False)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TENANT_NOT_REGISTERED)
    return _policy_response(target)


@router.patch("/tenants/{tenant_id}/data_policies")
async def patch_tenant_data_policies(
    tenant_id: UUID,
    body: DataPoliciesPatchBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """Change one registered tenant's data policies (internal admins only).

    Merges the supplied keys onto the stored policy and writes the row.
    A change is audited as ``tenant.data_policy_changed`` with the full
    before/after under the ACTING tenant (the internal admin's — under RLS
    the session's tenant), pointed at the target by ``resource_id``. A
    PATCH that changes nothing writes nothing and is not audited.
    """
    me = await _require_internal_admin(auth)
    target = await get_tenant(tenant_id, use_cache=False)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TENANT_NOT_REGISTERED)
    before = target.data_policies
    after = TenantDataPolicies.model_validate(
        {**before.as_dict(), **body.model_dump(exclude_none=True)}
    )
    if after == before:
        return _policy_response(target)
    stored = await set_tenant_data_policies(target.id, after)
    if stored is None:  # the row vanished between the read and the write
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=TENANT_NOT_REGISTERED)
    await log_audit(
        session,
        tenant_id=me.id,
        actor_id=auth.user_id,
        action=DATA_POLICY_CHANGED_ACTION,
        resource_type="tenant",
        resource_id=target.id,
        before=before.as_dict(),
        after=after.as_dict(),
        metadata={"target_tenant_id": str(target.id), "target_clerk_org_id": target.clerk_org_id},
        severity="warning",
        tags=["data_policy"],
    )
    await session.commit()
    logger.info(
        "admin/tenants: %s changed data policies of tenant=%s %s -> %s",
        auth.user_id or auth.source,
        target.id,
        before.as_dict(),
        after.as_dict(),
    )
    return _policy_response(stored)


__all__ = ["router"]
