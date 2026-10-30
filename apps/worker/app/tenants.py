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

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, ValidationError
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


# ─────────────────────────── data policies ───────────────────────────
#
# FON-116 / FON-96 — per-tenant DATA policies (``tenants.data_policies``,
# JSONB on Postgres, JSON text on SQLite). The first policy is the CoStar
# licensing gate: a market default DERIVED from a CoStar report (a submarket
# forecast, a class benchmark) may be offered to a workspace only while its
# policy says ``allow``. Under ``withhold`` the topline resolvers (the next
# PR) yield ``withheld_by_policy`` / reason ``license_withheld`` and the UI
# asks for the growth by hand — the analyst's own uploaded figures are never
# touched, only the platform-derived default is. This is a policy row, not a
# code fork: ``is_internal`` never decides it, flipping a tenant is data.
#
# ``extra="forbid"`` on purpose: a mistyped key is a 422 at the admin API,
# never a silently-ignored key that leaves the tenant on ``allow``.

DataPolicyChoice = Literal["allow", "withhold"]


class TenantDataPolicies(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    costar_derived_defaults: DataPolicyChoice = "allow"

    @property
    def withholds_costar(self) -> bool:
        return self.costar_derived_defaults == "withhold"

    @property
    def withholds_anything(self) -> bool:
        return self.withholds_costar

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def withheld(cls) -> TenantDataPolicies:
        """Every key at ``withhold`` — what an UNREADABLE row resolves to."""
        return cls.model_validate({k: "withhold" for k in cls.model_fields})


#: The policy keys the admin surfaces enumerate (health posture, Settings card).
DATA_POLICY_KEYS: tuple[str, ...] = tuple(TenantDataPolicies.model_fields)


def _parse_data_policies(raw: Any, *, tenant_id: Any) -> tuple[TenantDataPolicies, str | None]:
    """Decode the stored column → ``(policies, error)``.

    A row written before the column existed (``NULL`` / ``''`` / ``{}``) is
    the default (``allow``) with no error. Anything else that is not a valid
    policy document — not JSON, not an object, an unknown key, a misspelt
    value such as ``"withold"`` — **fails closed**: every key reads as
    ``withhold`` and ``error`` says why. Such a value cannot have been
    written through the typed API, so it is a manual edit or corruption; the
    one thing it must never do is serve a CoStar default to a tenant whose
    operator meant to withhold it. The error is logged with the tenant and
    surfaced as ``invalid_rows`` on ``/admin/health``.
    """
    if raw is None or raw == "" or raw == {}:
        return TenantDataPolicies(), None
    data: Any = raw
    error: str | None = None
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            error = f"not JSON ({exc})"
    if error is None:
        try:
            if not isinstance(data, dict):
                raise TypeError(f"expected a JSON object, got {type(data).__name__}")
            return TenantDataPolicies.model_validate(data), None
        except (ValidationError, TypeError) as exc:
            error = f"{type(exc).__name__}: {exc}"
    logger.warning(
        "tenants: data_policies row for tenant %s is invalid and reads as withhold for every key — %s",
        tenant_id,
        error,
    )
    return TenantDataPolicies.withheld(), error


@dataclass(frozen=True)
class Tenant:
    id: UUID
    clerk_org_id: str
    name: str
    kind: str
    status: str
    created_at: datetime | None = None
    data_policies: TenantDataPolicies = field(default_factory=TenantDataPolicies)
    #: Why the stored policy row could not be read (then ``data_policies`` is
    #: every key ``withhold``); ``None`` for a valid or absent row.
    data_policies_error: str | None = None

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
            "data_policies": self.data_policies.as_dict(),
            "data_policies_error": self.data_policies_error,
        }


# ─────────────────────────── cache ───────────────────────────

# A registry HIT lives this long. Writes on THIS replica clear the cache;
# a suspension written on another replica lands here within this window.
_CACHE_TTL_S = 10.0
# A registry MISS (no row) lives only this long, so an org an admin has
# just registered — on any replica — is served within ~2 s instead of
# being refused for a full hit TTL. Still bounds an unregistered org
# hammering the API to one query per 2 s.
_MISS_TTL_S = 2.0
# ``{tenant_id: (fetched_at_monotonic, Tenant | None)}`` — ``None`` is a
# cached miss. A lookup FAILURE is never cached (see ``get_tenant``).
_CACHE: dict[UUID, tuple[float, Tenant | None]] = {}


class TenantRegistryUnavailable(RuntimeError):
    """The ``tenants`` table could not be read (connection error, table
    missing, timeout). Distinct from "no row": the caller must not treat it
    as "not registered"."""


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
    policies, policy_error = _parse_data_policies(m.get("data_policies"), tenant_id=m["id"])
    return Tenant(
        id=UUID(str(m["id"])),
        clerk_org_id=str(m["clerk_org_id"]),
        name=str(m["name"] or ""),
        kind=str(m["kind"]),
        status=str(m["status"]),
        created_at=created,
        data_policies=policies,
        data_policies_error=policy_error,
    )


_SELECT_COLUMNS = "id, clerk_org_id, name, kind, status, created_at, data_policies"


async def get_tenant(
    tenant_id: UUID | str, *, use_cache: bool = True, raise_on_error: bool = False
) -> Tenant | None:
    """Return the registry row for ``tenant_id`` or ``None``.

    A lookup FAILURE (connection error, no ``tenants`` table because the
    worker predates the migration) is never cached. By default it logs at
    WARNING and returns ``None`` — the informational callers
    (``/settings/tenant``, the admin list) degrade to "no registry row".
    With ``raise_on_error=True`` it raises :class:`TenantRegistryUnavailable`
    instead, which is what the enforcement path needs: an outage must read
    as 503, never as "not registered".

    Hits are cached ``_CACHE_TTL_S``; misses only ``_MISS_TTL_S``.
    """
    tid = UUID(str(tenant_id))
    if use_cache:
        hit = _CACHE.get(tid)
        if hit is not None:
            ttl = _CACHE_TTL_S if hit[1] is not None else _MISS_TTL_S
            if (time.monotonic() - hit[0]) < ttl:
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
        if raise_on_error:
            raise TenantRegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
        return None
    _CACHE[tid] = (time.monotonic(), tenant)
    return tenant


async def is_registered(tenant_id: UUID | str) -> bool:
    """True when the tenant exists and is ``active``.

    Raises :class:`TenantRegistryUnavailable` when the registry cannot be
    read — the auth dependency maps that to 503 ``tenant_registry_unavailable``
    rather than a (cached, wrong) 403.
    """
    tenant = await get_tenant(tenant_id, raise_on_error=True)
    return tenant is not None and tenant.is_serving


async def is_internal(tenant_id: UUID | str) -> bool:
    """True when the tenant is a registered, active, ``internal`` tenant."""
    tenant = await get_tenant(tenant_id)
    return tenant is not None and tenant.is_serving and tenant.is_internal


async def list_tenants(*, raise_on_error: bool = False) -> list[Tenant]:
    """Every registry row, oldest first.

    A read failure (no table, connection error) logs and returns ``[]`` by
    default — the informational admin list degrades to "no rows". With
    ``raise_on_error=True`` it raises :class:`TenantRegistryUnavailable`
    instead, for callers that must not confuse an outage with an empty
    registry (the ``/admin/health`` posture).
    """
    try:
        async with get_engine().connect() as conn:
            rows = (
                await conn.execute(
                    text(f"SELECT {_SELECT_COLUMNS} FROM tenants ORDER BY created_at, clerk_org_id")
                )
            ).fetchall()
    except Exception as exc:
        logger.warning("tenants: list failed (%s)", exc)
        if raise_on_error:
            raise TenantRegistryUnavailable(f"{type(exc).__name__}: {exc}") from exc
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


async def update_tenant(
    tenant_id: UUID,
    *,
    name: str | None = None,
    status: str | None = None,
) -> Tenant | None:
    """Change ``name`` and/or ``status`` on an EXISTING row; never inserts.

    The Clerk webhook's ``organization.updated`` (name refresh) and
    ``organization.deleted`` (→ ``suspended``) path. ``kind`` is never
    touched here, and ``status`` may only move to a non-serving value —
    an org is re-activated only by an internal admin through
    :func:`upsert_tenant`. Returns ``None`` when there is no row for the
    tenant (an org nobody registered or parked), so the caller can
    acknowledge the delivery without creating a row.
    """
    if status is not None and status not in STATUSES:
        raise ValueError(f"status must be one of {sorted(STATUSES)}, got {status!r}")
    if status == SERVING_STATUS:
        raise ValueError("update_tenant never re-activates — use upsert_tenant from an admin path")
    sets: list[str] = []
    params: dict[str, Any] = {"id": str(tenant_id)}
    if name is not None and name.strip():
        sets.append("name = :name")
        params["name"] = name.strip()
    if status is not None:
        sets.append("status = :status")
        params["status"] = status
    if not sets:
        return await get_tenant(tenant_id, use_cache=False)
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"UPDATE tenants SET {', '.join(sets)} WHERE id = :id"),
            params,
        )
        touched = int(result.rowcount or 0)
    reset_tenant_cache()
    if touched == 0:
        return None
    return await get_tenant(tenant_id, use_cache=False)


# ─────────────────────────── data policies I/O ───────────────────────────


async def load_tenant_data_policy(tenant_id: UUID | str) -> TenantDataPolicies:
    """The data policy the loaders and resolvers must honour for ``tenant_id``.

    Served from the registry cache (``_CACHE_TTL_S``; a write on this replica
    clears it). A tenant with no registry row — an unregistered org while
    enforcement is off — gets the default (``allow``). A registry READ
    FAILURE raises :class:`TenantRegistryUnavailable`: an outage must never
    read as ``allow`` for a tenant whose row says ``withhold``, so the engine
    run records a load error instead of running on the wrong policy.
    """
    tenant = await get_tenant(tenant_id, raise_on_error=True)
    return tenant.data_policies if tenant is not None else TenantDataPolicies()


async def set_tenant_data_policies(
    tenant_id: UUID | str, policies: TenantDataPolicies
) -> Tenant | None:
    """Replace the policy row for an EXISTING tenant; never inserts.

    Returns the stored row, or ``None`` when there is no registry row for
    ``tenant_id`` (the admin API maps that to 404 — a workspace must be
    registered before it can carry a policy). Clears the registry cache so
    the next ``load_tenant_data_policy`` / read-cache fingerprint sees it.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        touched = await write_tenant_data_policies(
            conn, tenant_id=tenant_id, policies=policies, dialect_name=engine.dialect.name
        )
    reset_tenant_cache()
    if touched == 0:
        return None
    return await get_tenant(tenant_id, use_cache=False)


async def write_tenant_data_policies(
    executor: Any, *, tenant_id: UUID | str, policies: TenantDataPolicies, dialect_name: str
) -> int:
    """Execute the policy UPDATE on ``executor`` (an ``AsyncConnection`` or an
    ``AsyncSession``) inside the CALLER's transaction and return the row
    count. No commit, no cache reset — the caller owns both, which is what
    lets the admin API write the policy and its audit row atomically
    (:func:`set_tenant_data_policies` is the one-shot form)."""
    blob = json.dumps(policies.as_dict(), sort_keys=True)
    # JSONB needs an explicit cast on Postgres; SQLite stores the JSON text.
    value_frag = ":p" if dialect_name == "sqlite" else "CAST(:p AS JSONB)"
    result = await executor.execute(
        text(f"UPDATE tenants SET data_policies = {value_frag} WHERE id = :id"),
        {"id": str(UUID(str(tenant_id))), "p": blob},
    )
    return int(result.rowcount or 0)


async def data_policies_posture() -> dict[str, Any]:
    """The ``data_policies`` block of ``GET /admin/health``: how many
    registered tenants exist, how many withhold anything and how many rows
    could not be read (and therefore withhold everything). Counts only — no
    tenant is named. A registry outage is reported as ``error`` (the
    exception type), never as zero tenants."""
    try:
        rows = await list_tenants(raise_on_error=True)
    except Exception as exc:
        cause = exc.__cause__
        return {
            "error": type(cause).__name__ if cause is not None else type(exc).__name__,
            "keys": list(DATA_POLICY_KEYS),
        }
    return {
        "tenants_total": len(rows),
        "tenants_with_withhold": sum(1 for t in rows if t.data_policies.withholds_anything),
        "invalid_rows": sum(1 for t in rows if t.data_policies_error is not None),
        "keys": list(DATA_POLICY_KEYS),
    }


__all__ = [
    "DATA_POLICY_KEYS",
    "KINDS",
    "SERVING_STATUS",
    "STATUSES",
    "DataPolicyChoice",
    "Tenant",
    "TenantDataPolicies",
    "TenantRegistryUnavailable",
    "data_policies_posture",
    "get_tenant",
    "is_internal",
    "is_registered",
    "list_tenants",
    "load_tenant_data_policy",
    "reset_tenant_cache",
    "set_tenant_data_policies",
    "update_tenant",
    "upsert_tenant",
    "write_tenant_data_policies",
]
