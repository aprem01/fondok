"""Open, list and end Fondok staff support access to a firm.

* ``POST   /admin/support-access``          — ``{tenant_id, reason, hours}``
* ``GET    /admin/support-access``          — the caller's live grants
* ``DELETE /admin/support-access/{grant_id}`` — end one of the caller's grants

For the FIRM's own admins (any workspace admin, no staff flag needed):

* ``GET    /admin/support-access/on-my-firm``            — live grants on their firm
* ``DELETE /admin/support-access/on-my-firm/{grant_id}`` — end one; logged as ended by the firm

Pre-launch standing membership (until :data:`app.support_access.FULL_LAUNCH`):

* ``POST /admin/support-access/membership-event`` — ``{tenant_id, joined, user_id?, email?}``;
  records in the FIRM's audit log that a Fondok person joined/left its
  organization as an admin. The membership itself is changed in Clerk by the
  web app, which calls this first-class so no standing access goes unlogged.

Only a verified Fondok staff admin (``fondok_staff`` claim with two-factor on)
may use these — not the older "admin of the Fondok workspace" rule, because
reading a client's deals is the most sensitive thing a Fondok person can do.
The target must be a registered, active CLIENT firm. Opening and ending are
written to the firm's own audit log. See :mod:`app.support_access`.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from .. import support_access as support
from ..auth import AuthContext, get_current_auth, is_fondok_staff, require_role
from ..tenants import get_tenant

router = APIRouter()


class OpenSupportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    reason: str = Field(min_length=10, max_length=500)
    hours: int


class MembershipEventBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    joined: bool
    user_id: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)


def _require_staff(auth: AuthContext) -> str:
    if not is_fondok_staff(auth) or not auth.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="support_access_requires_staff")
    return auth.user_id


@router.post("/support-access", status_code=status.HTTP_201_CREATED)
async def open_support_access(
    body: OpenSupportBody,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> dict[str, Any]:
    user_id = _require_staff(auth)
    if body.hours not in support.ALLOWED_HOURS:
        raise HTTPException(status_code=422, detail=f"hours must be one of {list(support.ALLOWED_HOURS)}")
    if not support.long_grant_allowed(body.hours):
        raise HTTPException(
            status_code=422,
            detail=f"long_support_grants_closed: grants over {support.LONG_GRANT_MAX_HOURS} h can no longer be opened",
        )
    reason = " ".join(body.reason.split())
    if len(reason) < 10:
        raise HTTPException(status_code=422, detail="Give a reason of at least 10 characters.")
    firm = await get_tenant(body.tenant_id, use_cache=False)
    if firm is None or firm.is_internal or not firm.is_serving:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="firm_not_available_for_support")
    existing = await support.active_grant(staff_user_id=user_id, tenant_id=firm.id)
    if existing is not None:
        return {**existing.as_dict(), "firm": firm.name, "reused": True}
    grant = await support.create_grant(tenant_id=firm.id, staff_user_id=user_id, reason=reason, hours=body.hours)
    logged = await support.audit_in_firm(
        tenant_id=firm.id,
        actor_id=user_id,
        action=support.ACTION_STARTED,
        grant=grant,
        metadata={"hours": body.hours, "read_only": True},
    )
    if not logged:
        # No access the firm cannot see: cancel the grant and refuse.
        await support.end_grant(grant.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="support_access_audit_unavailable",
            headers={"Retry-After": "2"},
        )
    return {**grant.as_dict(), "firm": firm.name, "reused": False}


@router.post("/support-access/membership-event", status_code=status.HTTP_201_CREATED)
async def record_membership_event(
    body: MembershipEventBody,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> dict[str, Any]:
    user_id = _require_staff(auth)
    firm = await get_tenant(body.tenant_id, use_cache=False)
    if firm is None or firm.is_internal:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="firm_not_available_for_support")
    member = body.user_id or user_id
    logged = await support.audit_staff_membership(
        tenant_id=firm.id, actor_id=user_id, joined=body.joined, member_user_id=member, member_email=body.email
    )
    if not logged:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="support_access_audit_unavailable",
            headers={"Retry-After": "2"},
        )
    return {"logged": True, "action": support.ACTION_STAFF_JOINED if body.joined else support.ACTION_STAFF_LEFT}


@router.get("/support-access")
async def list_support_access(
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> dict[str, Any]:
    user_id = _require_staff(auth)
    out = []
    for g in await support.active_grants_for(user_id):
        firm = await get_tenant(g.tenant_id)
        out.append({**g.as_dict(), "firm": firm.name if firm else None})
    return {"grants": out}


@router.get("/support-access/on-my-firm")
async def list_support_access_on_my_firm(
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """Live Fondok support sessions on the caller's own firm. The staff
    member's identity is not shown — only that Fondok has access, why, and
    until when."""
    grants = await support.active_grants_on(auth.tenant_id)
    return {
        "grants": [
            {k: v for k, v in g.as_dict().items() if k != "staff_user_id"} for g in grants
        ]
    }


@router.delete("/support-access/on-my-firm/{grant_id}")
async def end_support_access_on_my_firm(
    grant_id: UUID,
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """A firm admin ends Fondok's support access to their firm."""
    grant = await support.get_grant(grant_id)
    if grant is None or grant.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="grant_not_found")
    if grant.ended_at is None:
        await support.end_grant(grant.id)
        await support.audit_in_firm(
            tenant_id=grant.tenant_id,
            actor_id=auth.user_id,
            action=support.ACTION_ENDED,
            grant=grant,
            metadata={"ended_by": "firm_admin"},
        )
    return {"ended": True, "id": str(grant.id)}


@router.delete("/support-access/{grant_id}")
async def end_support_access(
    grant_id: UUID,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> dict[str, Any]:
    user_id = _require_staff(auth)
    grant = await support.get_grant(grant_id)
    if grant is None or grant.staff_user_id != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="grant_not_found")
    if grant.ended_at is None:
        await support.end_grant(grant.id)
        await support.audit_in_firm(
            tenant_id=grant.tenant_id, actor_id=user_id, action=support.ACTION_ENDED, grant=grant
        )
    return {"ended": True, "id": str(grant.id)}
