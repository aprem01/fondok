"""Per-tenant iteration for the background scans — FON-93 row-level security.

Three periodic jobs open a session with no request context and SELECT
across every tenant: the digest scheduler (``pipeline_digest_schedules``
due now), the Anthropic batch poller (open ``pending_batches``) and the
extraction watchdog (``documents`` stuck in a processing status). With
``migrations/rls/001_enable_rls.sql`` applied and the worker connecting as
``fondok_app``, a transaction that published no ``app.tenant_id`` sees
zero rows — so each of those scans would silently find nothing.

This module gives them one shared plan:

* **RLS off** (``DB_SET_TENANT_GUC`` unset, or SQLite): :func:`plan_tenant_scan`
  returns ``tenants=None`` and :func:`scan_by_tenant` runs the caller's
  fetch exactly once, un-scoped — byte-for-byte today's behaviour: same
  SQL, same rows, same order of effects.
* **RLS on**: the scan is run once per tenant, with
  :data:`app.auth.context.current_tenant_id` published BEFORE the fetch
  (the ``after_begin`` hook in ``app.database`` then runs ``set_config``
  for that tenant) and the fetch's SQL carrying ``AND tenant_id = :tenant``
  (the rule for all SQL — and what keeps the owner role, which bypasses
  RLS, from returning every tenant's rows N times). The tenants come
  from, in order:

  1. the registry (``app.tenants.list_tenants()``) when
     ``REQUIRE_REGISTERED_TENANT`` is on and it has rows — an unregistered
     tenant has no data plane, so its rows are not scanned either;
  2. else ``SELECT DISTINCT tenant_id FROM <table> WHERE <scan predicate>``
     read through the owner DSN (``DATABASE_URL_MIGRATIONS``, one
     un-pooled connection per tick, Core — the GUC hook does not fire);
  3. else — no registry, no owner DSN — today's single cross-tenant
     query, which under RLS as ``fondok_app`` returns nothing and is
     logged as such once per tick.

The callers keep their per-row publish (each row's statements run as that
row's tenant) exactly as before; this module only changes how the rows
are *found*.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.context import current_tenant_id
from ..config import get_settings
from ..database import _tenant_guc_is_active, owner_connection
from ..tenants import list_tenants

logger = logging.getLogger(__name__)

ScanSource = Literal["cross_tenant", "registry", "owner_distinct"]


@dataclass(frozen=True)
class ScanPlan:
    """How one tick finds its rows.

    ``tenants`` is ``None`` for a single un-scoped scan (today's path) or
    the ordered tenant ids to iterate. ``source`` says where they came
    from, for logs and tests.
    """

    tenants: tuple[str, ...] | None
    source: ScanSource

    @property
    def per_tenant(self) -> bool:
        return self.tenants is not None


def per_tenant_scans_active() -> bool:
    """True when the scans must iterate tenants: the RLS GUC hook is live
    (``DB_SET_TENANT_GUC`` on AND a Postgres DSN). Read on every tick so
    flipping the flag needs no restart ordering."""
    return _tenant_guc_is_active()


async def _distinct_tenants_via_owner(
    distinct_sql: str, params: Mapping[str, Any] | None
) -> list[str] | None:
    """``distinct_sql`` through the owner DSN; ``None`` when there is no
    owner DSN or the read failed (the caller then falls back)."""
    async with owner_connection() as conn:
        if conn is None:
            return None
        rows = (await conn.execute(text(distinct_sql), dict(params or {}))).fetchall()
    ids = sorted({str(r[0]) for r in rows if r[0] is not None})
    return ids


async def plan_tenant_scan(
    *,
    distinct_tenants_sql: str,
    params: Mapping[str, Any] | None = None,
    job: str = "scan",
) -> ScanPlan:
    """Decide how this tick finds its rows (see the module docstring).

    ``distinct_tenants_sql`` is ``SELECT DISTINCT tenant_id FROM <table>
    WHERE <the scan's own predicate>`` — the same filter the real scan
    uses, so the owner read touches only the rows the job is about to
    look at. Never raises: any failure degrades to the cross-tenant plan.
    """
    if not per_tenant_scans_active():
        return ScanPlan(None, "cross_tenant")
    settings = get_settings()
    if settings.REQUIRE_REGISTERED_TENANT:
        try:
            registered = await list_tenants()
        except Exception as exc:  # list_tenants already swallows; belt and braces
            logger.warning("%s: registry read failed (%s)", job, exc)
            registered = []
        if registered:
            return ScanPlan(tuple(str(t.id) for t in registered), "registry")
    try:
        ids = await _distinct_tenants_via_owner(distinct_tenants_sql, params)
    except Exception as exc:
        logger.warning("%s: owner tenant discovery failed (%s) — cross-tenant scan", job, exc)
        ids = None
    if ids is not None:
        return ScanPlan(tuple(ids), "owner_distinct")
    logger.info(
        "%s: RLS GUC on but no registry rows and no DATABASE_URL_MIGRATIONS — "
        "cross-tenant scan (returns nothing as fondok_app)",
        job,
    )
    return ScanPlan(None, "cross_tenant")


async def scan_by_tenant[T](
    session: AsyncSession,
    plan: ScanPlan,
    fetch: Callable[[AsyncSession, str | None], Awaitable[list[T]]],
) -> list[T]:
    """Run ``fetch(session, tenant)`` per the plan and concatenate.

    Cross-tenant plan: one call with ``tenant=None`` and NO commit — the
    caller ends the transaction exactly where it does today. Per-tenant
    plan: for each tenant, publish it on ``current_tenant_id`` first (so
    the transaction the fetch autobegins carries that tenant's GUC), run
    the fetch with the tenant (the SQL adds ``AND tenant_id = :tenant``),
    then ``commit`` so the next tenant starts its own transaction. The
    caller's ContextVar snapshot/reset around the tick still applies; the
    per-row publish inside its loop is unchanged.
    """
    if plan.tenants is None:
        return await fetch(session, None)
    out: list[T] = []
    for tenant in plan.tenants:
        current_tenant_id.set(tenant)
        try:
            out.extend(await fetch(session, tenant))
        finally:
            # End THIS tenant's transaction whatever happened so the next
            # tenant's statements never run under this tenant's GUC.
            await session.commit()
    return out


__all__ = [
    "ScanPlan",
    "ScanSource",
    "per_tenant_scans_active",
    "plan_tenant_scan",
    "scan_by_tenant",
]
