"""Tenant registry — FON-93 institution workspace isolation.

A *tenant* is one institution's workspace. Its id is the UUIDv5 the
worker derives from the Clerk organization id (see
:func:`app.api.deals.coerce_org_tenant_id`); every tenant-scoped table
carries that UUID in ``tenant_id``.

Before FON-93 any Clerk organization was a tenant the moment a member
presented a JWT for it. That is the wrong default for a product sold to
institutions: an analyst who self-creates a Clerk org gets a data plane
nobody provisioned. The ``tenants`` table turns tenancy into an explicit
grant:

* ``kind``   — ``internal`` (Fondok's own staff/testers) or ``external``
               (a customer institution). Internal tenants may administer
               the registry and read cross-tenant observability.
* ``status`` — ``active`` serves traffic; ``suspended`` and
               ``pending_review`` (what the Clerk webhook writes for an
               org nobody registered) are refused with 403
               ``tenant_not_registered`` once
               ``settings.REQUIRE_REGISTERED_TENANT`` is on.

Reads go through a tiny in-process cache (``_CACHE_TTL_S``) so the
per-request check is one dict lookup in steady state. Writes clear it.
The table is created by :mod:`app.migrations` (additive, idempotent); on
a DB that predates it every read degrades to "not registered" rather
than raising, so an old database never 500s — it only refuses once the
operator has explicitly turned enforcement on.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text

from .database import get_engine

logger = logging.getLogger(__name__)

TenantKind = Literal["internal", "external"]
TenantStatus = Literal["active", "suspended", "pending_review"]

KINDS: frozenset[str] = frozenset({"internal", "external"})
STATUSES: frozenset[str] = frozenset({"active", "suspended", "pending_review"})

# Only this status is served. ``suspended`` and ``pending_review`` are
# both "registered in the table but not granted" — kept distinct so the
# admin list can tell a webhook arrival from a deliberate suspension.
SERVING_STATUS = "active"


@dataclass(frozen=True)
class Tenant:
    id: UUID
    clerk_org_id: str
    name: str
    kind: str
    status: str
    created_at: datetime | None = None

    @property
    def is_internal(self) -> bool:
        return self.kind == "internal"

    @property
    def is_serving(self) -> bool:
        return self.status == SERVING_STATUS

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "clerk_org_id": self.clerk_org_id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─────────────────────────── cache ───────────────────────────

_CACHE_TTL_S = 30.0
# ``{tenant_id: (fetched_at_monotonic, Tenant | None)}`` — ``None`` caches a
# miss too, so an unregistered org hammering the API is one query per TTL.
_CACHE: dict[UUID, tuple[float, Tenant | None]] = {}


def reset_tenant_cache() -> None:
    """Drop every cached row. Called after writes and by tests."""
    _CACHE.clear()


def _row_to_tenant(row: Any) -> Tenant:
    m = row._mapping
    created = m.get("created_at")
    if isinstance(created, str):
        try:
            created = datetime.fromisoformat(created)
        except ValueError:
            created = None
    return Tenant(
        id=UUID(str(m["id"])),
        clerk_org_id=str(m["clerk_org_id"]),
        name=str(m["name"] or ""),
        kind=str(m["kind"]),
        status=str(m["status"]),
        created_at=created,
    )


_SELECT_COLUMNS = "id, clerk_org_id, name, kind, status, created_at"


async def get_tenant(tenant_id: UUID | str, *, use_cache: bool = True) -> Tenant | None:
    """Return the registry row for ``tenant_id`` or ``None``.

    Never raises: a DB without the ``tenants`` table (worker older than
    the migration, or the migration failed) logs once at WARNING and
    returns ``None``.
    """
    tid = UUID(str(tenant_id))
    if use_cache:
        hit = _CACHE.get(tid)
        if hit is not None and (time.monotonic() - hit[0]) < _CACHE_TTL_S:
            return hit[1]
    tenant: Tenant | None = None
    try:
        async with get_engine().connect() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT {_SELECT_COLUMNS} FROM tenants WHERE id = :id"),
                    {"id": str(tid)},
                )
            ).first()
        if row is not None:
            tenant = _row_to_tenant(row)
    except Exception as exc:
        logger.warning("tenants: lookup failed for %s (%s)", tid, exc)
        return None
    _CACHE[tid] = (time.monotonic(), tenant)
    return tenant


async def is_registered(tenant_id: UUID | str) -> bool:
    """True when the tenant exists and is ``active``."""
    tenant = await get_tenant(tenant_id)
    return tenant is not None and tenant.is_serving


async def is_internal(tenant_id: UUID | str) -> bool:
    """True when the tenant is a registered, active, ``internal`` tenant."""
    tenant = await get_tenant(tenant_id)
    return tenant is not None and tenant.is_serving and tenant.is_internal


async def list_tenants() -> list[Tenant]:
    """Every registry row, oldest first. Empty on a DB without the table."""
    try:
        async with get_engine().connect() as conn:
            rows = (
                await conn.execute(
                    text(f"SELECT {_SELECT_COLUMNS} FROM tenants ORDER BY created_at, clerk_org_id")
                )
            ).fetchall()
    except Exception as exc:
        logger.warning("tenants: list failed (%s)", exc)
        return []
    return [_row_to_tenant(r) for r in rows]


async def upsert_tenant(
    *,
    tenant_id: UUID,
    clerk_org_id: str,
    name: str,
    kind: str,
    status: str,
    preserve_existing: bool = False,
) -> Tenant:
    """Insert or update one registry row and return the stored row.

    ``preserve_existing=True`` (the webhook path) only inserts: an org that
    an admin already registered keeps its kind/status/name, so a replayed
    ``organization.created`` can never downgrade an ``active`` tenant to
    ``pending_review``.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {sorted(KINDS)}, got {kind!r}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
    if not clerk_org_id.startswith("org_"):
        raise ValueError("clerk_org_id must be a Clerk organization id (org_…)")
    now = datetime.now(UTC)
    engine = get_engine()
    async with engine.begin() as conn:
        existing = (
            await conn.execute(
                text(f"SELECT {_SELECT_COLUMNS} FROM tenants WHERE id = :id"),
                {"id": str(tenant_id)},
            )
        ).first()
        if existing is None:
            await conn.execute(
                text(
                    """
                    INSERT INTO tenants (id, clerk_org_id, name, kind, status, created_at)
                    VALUES (:id, :org, :name, :kind, :status, :created_at)
                    """
                ),
                {
                    "id": str(tenant_id),
                    "org": clerk_org_id,
                    "name": name,
                    "kind": kind,
                    "status": status,
                    "created_at": now,
                },
            )
        elif not preserve_existing:
            await conn.execute(
                text(
                    """
                    UPDATE tenants
                       SET name = :name, kind = :kind, status = :status
                     WHERE id = :id
                    """
                ),
                {"id": str(tenant_id), "name": name, "kind": kind, "status": status},
            )
    reset_tenant_cache()
    stored = await get_tenant(tenant_id, use_cache=False)
    assert stored is not None  # we just wrote it
    return stored


__all__ = [
    "KINDS",
    "SERVING_STATUS",
    "STATUSES",
    "Tenant",
    "get_tenant",
    "is_internal",
    "is_registered",
    "list_tenants",
    "reset_tenant_cache",
    "upsert_tenant",
]
