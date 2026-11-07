"""Fondok staff support access — a time-limited, read-only window into one firm.

A Fondok staff admin (verified ``fondok_staff`` claim, two-factor on) opens a
grant for one registered client firm with a reason and a duration. While the
grant is live, a request carrying ``X-Support-Tenant: <tenant uuid>`` is served
AS THAT FIRM (so row-level security scopes every read to it) with these limits:

* read-only — any method other than GET/HEAD/OPTIONS is refused;
* the role is ``member`` — admin-only reads stay closed;
* the start, every distinct page viewed and the end are written to the FIRM'S
  OWN audit log, where its admins can see them.

Nothing here makes the staff user a member of the firm, and nothing outlives
``expires_at``: an expired grant simply stops matching.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text

from .database import get_engine

logger = logging.getLogger(__name__)

SUPPORT_HEADER = "X-Support-Tenant"
#: Full launch (Prem, 2026-11-07). Until then Fondok staff may hold standing
#: admin memberships in client firms (Firms page "Join as admin", logged in the
#: firm's audit log) and may open 90-day read-only grants. Both close on this
#: date by code; moving it is a deliberate change.
FULL_LAUNCH = datetime(2027, 6, 27, tzinfo=UTC)
#: 90 days (2160 h): pre-launch option; still read-only and logged. New grants
#: longer than a day cannot be OPENED after ``LONG_GRANT_OPEN_UNTIL``; grants
#: already open run to their own expiry.
ALLOWED_HOURS = (1, 4, 24, 2160)
LONG_GRANT_MAX_HOURS = 24
LONG_GRANT_OPEN_UNTIL = FULL_LAUNCH


def long_grant_allowed(hours: int, now: datetime | None = None) -> bool:
    """Whether a grant of ``hours`` may be opened now (long ones sunset)."""
    if hours <= LONG_GRANT_MAX_HOURS:
        return True
    return (now or datetime.now(UTC)) < LONG_GRANT_OPEN_UNTIL
READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
#: Paths where the header is ignored (managing grants is not done "as" a firm).
EXEMPT_PREFIXES = ("/admin/support-access",)

ACTION_STARTED = "support.access_started"
ACTION_VIEWED = "support.viewed"
ACTION_ENDED = "support.access_ended"
#: Pre-launch standing membership (a Fondok person joined/left the firm's org).
ACTION_STAFF_JOINED = "support.staff_joined"
ACTION_STAFF_LEFT = "support.staff_left"


@dataclass(frozen=True)
class SupportGrant:
    id: UUID
    tenant_id: UUID
    staff_user_id: str
    reason: str
    created_at: datetime | None
    expires_at: datetime
    ended_at: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "staff_user_id": self.staff_user_id,
            "reason": self.reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
        }


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _row(r: Any) -> SupportGrant:
    m = r._mapping
    expires = _dt(m["expires_at"])
    assert expires is not None
    return SupportGrant(
        id=UUID(str(m["id"])),
        tenant_id=UUID(str(m["tenant_id"])),
        staff_user_id=str(m["staff_user_id"]),
        reason=str(m["reason"]),
        created_at=_dt(m["created_at"]),
        expires_at=expires,
        ended_at=_dt(m["ended_at"]),
    )


async def create_grant(*, tenant_id: UUID, staff_user_id: str, reason: str, hours: int) -> SupportGrant:
    now = datetime.now(UTC)
    gid = uuid.uuid4()
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO support_grants (id, tenant_id, staff_user_id, reason, created_at, expires_at) "
                "VALUES (:id, :t, :u, :r, :c, :e)"
            ),
            {
                "id": str(gid),
                "t": str(tenant_id),
                "u": staff_user_id,
                "r": reason,
                "c": now,
                "e": now + timedelta(hours=hours),
            },
        )
    return SupportGrant(gid, tenant_id, staff_user_id, reason, now, now + timedelta(hours=hours), None)


async def active_grant(*, staff_user_id: str, tenant_id: UUID) -> SupportGrant | None:
    """The caller's live grant for this firm, or ``None``. Read on every
    support request (no cache) so ending a grant takes effect at once."""
    now = datetime.now(UTC)
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, tenant_id, staff_user_id, reason, created_at, expires_at, ended_at "
                    "FROM support_grants WHERE staff_user_id = :u AND tenant_id = :t "
                    "AND ended_at IS NULL AND expires_at > :now ORDER BY expires_at DESC"
                ),
                {"u": staff_user_id, "t": str(tenant_id), "now": now},
            )
        ).fetchall()
    return _row(rows[0]) if rows else None


async def active_grants_for(staff_user_id: str) -> list[SupportGrant]:
    now = datetime.now(UTC)
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, tenant_id, staff_user_id, reason, created_at, expires_at, ended_at "
                    "FROM support_grants WHERE staff_user_id = :u AND ended_at IS NULL "
                    "AND expires_at > :now ORDER BY expires_at DESC"
                ),
                {"u": staff_user_id, "now": now},
            )
        ).fetchall()
    return [_row(r) for r in rows]


async def active_grants_on(tenant_id: UUID) -> list[SupportGrant]:
    """Live grants on ONE firm — what that firm's admins may see and end."""
    now = datetime.now(UTC)
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, tenant_id, staff_user_id, reason, created_at, expires_at, ended_at "
                    "FROM support_grants WHERE tenant_id = :t AND ended_at IS NULL "
                    "AND expires_at > :now ORDER BY expires_at DESC"
                ),
                {"t": str(tenant_id), "now": now},
            )
        ).fetchall()
    return [_row(r) for r in rows]


async def get_grant(grant_id: UUID) -> SupportGrant | None:
    async with get_engine().connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id, tenant_id, staff_user_id, reason, created_at, expires_at, ended_at "
                    "FROM support_grants WHERE id = :id"
                ),
                {"id": str(grant_id)},
            )
        ).fetchall()
    return _row(rows[0]) if rows else None


async def end_grant(grant_id: UUID) -> None:
    async with get_engine().begin() as conn:
        await conn.execute(
            text("UPDATE support_grants SET ended_at = :now WHERE id = :id AND ended_at IS NULL"),
            {"id": str(grant_id), "now": datetime.now(UTC)},
        )


# ─────────────────────────── audit into the firm's own log ───────────────────────────

#: (grant id, path) pairs already logged by this process — one row per page,
#: not one per poll. Bounded; a restart only means a page is logged again.
_VIEWED: set[tuple[str, str]] = set()
_VIEWED_MAX = 20_000


async def _audit_firm_row(
    *,
    tenant_id: UUID,
    actor_id: str | None,
    action: str,
    resource_type: str,
    resource_id: UUID | str | None,
    metadata: dict[str, Any],
) -> bool:
    """Write one audit row under the FIRM's tenant (publishing it so the RLS
    GUC allows the insert), then restore the caller's tenant. Never raises;
    returns whether the row was committed, so callers can fail closed."""
    from .audit import log_audit
    from .auth.context import current_tenant_id
    from .database import get_session_factory

    token = current_tenant_id.set(str(tenant_id))
    try:
        async with get_session_factory()() as session:
            await log_audit(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                metadata={"by": "Fondok support", **metadata},
                tags=["support"],
                raise_on_error=True,
            )
            await session.commit()
    except Exception as exc:
        logger.error("support_access: audit %s for tenant %s failed: %s", action, tenant_id, exc)
        return False
    finally:
        current_tenant_id.reset(token)
    return True


async def audit_in_firm(
    *,
    tenant_id: UUID,
    actor_id: str | None,
    action: str,
    grant: SupportGrant,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """Audit a support-grant event in the firm's own log (see :func:`_audit_firm_row`)."""
    return await _audit_firm_row(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type="support_access",
        resource_id=grant.id,
        metadata={
            "reason": grant.reason,
            "expires_at": grant.expires_at.isoformat(),
            **(metadata or {}),
        },
    )


async def audit_staff_membership(
    *,
    tenant_id: UUID,
    actor_id: str | None,
    joined: bool,
    member_user_id: str,
    member_email: str | None = None,
) -> bool:
    """Record that a Fondok person joined or left the firm's organization as a
    standing admin (pre-launch only). Written to the FIRM's log so its admins
    see who from Fondok is in their workspace and since when."""
    meta: dict[str, Any] = {"member_user_id": member_user_id, "pre_launch_until": FULL_LAUNCH.isoformat()}
    if member_email:
        meta["member_email"] = member_email
    return await _audit_firm_row(
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=ACTION_STAFF_JOINED if joined else ACTION_STAFF_LEFT,
        resource_type="staff_membership",
        resource_id=member_user_id,
        metadata=meta,
    )


async def audit_view_once(*, grant: SupportGrant, actor_id: str | None, path: str, query: str = "") -> bool:
    """Log a page (path + query, so each search is visible) once per grant.
    Remembered only after the row commits, so a failed write is retried and
    the caller can refuse the request rather than serve it unlogged."""
    key = (str(grant.id), f"{path}?{query}" if query else path)
    if key in _VIEWED:
        return True
    meta: dict[str, Any] = {"path": path}
    if query:
        meta["query"] = query[:500]
    ok = await audit_in_firm(
        tenant_id=grant.tenant_id, actor_id=actor_id, action=ACTION_VIEWED, grant=grant, metadata=meta
    )
    if ok:
        if len(_VIEWED) >= _VIEWED_MAX:
            _VIEWED.clear()
        _VIEWED.add(key)
    return ok


__all__ = [
    "ACTION_ENDED",
    "ACTION_STAFF_JOINED",
    "ACTION_STAFF_LEFT",
    "ACTION_STARTED",
    "ACTION_VIEWED",
    "ALLOWED_HOURS",
    "EXEMPT_PREFIXES",
    "FULL_LAUNCH",
    "LONG_GRANT_MAX_HOURS",
    "LONG_GRANT_OPEN_UNTIL",
    "READ_METHODS",
    "SUPPORT_HEADER",
    "SupportGrant",
    "active_grant",
    "active_grants_for",
    "active_grants_on",
    "audit_in_firm",
    "audit_staff_membership",
    "audit_view_once",
    "create_grant",
    "end_grant",
    "get_grant",
    "long_grant_allowed",
]
