"""Operator health view — ``GET /admin/health`` (FON-93 / client-readiness sweep).

The public ``GET /health`` is a liveness probe anyone on the internet can
read, so it says only ``{status, version, build_sha, build_time, db,
degraded_reasons}`` plus the boot invariants. Everything that describes the
deployment itself — storage bucket and region, the auth posture flags, the
embeddings model, the per-tenant extraction-cache counters (keyed by Clerk
org id), the last LLM provider refusal and the last-hour failure counters —
is infrastructure and tenant-enumeration detail and lives here, behind the
same gate ``POST /admin/tenants`` uses: an ``admin`` of a registered,
active **internal** tenant. An external admin gets 403; the response never
tells them what they are missing.

Failure counters (go-live audit D.4, 2026-10-30)
------------------------------------------------
``extraction_failures_last_hour`` counts ``documents`` rows in FAILED /
PARSE_FAILED whose status changed in the last hour (``status_changed_at``,
else ``uploaded_at``); ``engine_failures_last_hour`` counts ``engine_outputs``
rows that failed in the last hour, excluding the ``skipped: upstream …``
cascade rows so one root failure counts once per engine that raised. A dead
provider key reads as the first number climbing while ``llm_provider`` names
the refusal; an engine regression reads as the second.

The SQL carries no dialect-specific timestamp arithmetic: a date-prefix
prefilter (``substr(CAST(ts AS TEXT), 1, 10)`` — a no-op cast on SQLite's
TEXT columns, the text form on Postgres) keeps the scan to the last two days
of failures, and the exact one-hour comparison happens in Python on the
parsed timestamps. Under row-level security the counts are gathered the way
the background scans gather their rows — once per tenant through
``services.tenant_scan`` — so an internal admin's session, whose GUC is its
own tenant, still sees the platform total; ``failures_last_hour_scan`` says
which path produced the numbers.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, require_role
from ..auth.context import current_tenant_id
from ..database import get_session
from ..tenants import get_tenant
from .health import ops_snapshot, public_snapshot

logger = logging.getLogger(__name__)
router = APIRouter()

ADMIN_HEALTH_REQUIRES_INTERNAL_TENANT = "admin_health_requires_internal_tenant"

FAILURE_WINDOW = timedelta(hours=1)
_FAILED_DOC_STATUSES = ("FAILED", "PARSE_FAILED")


def _timestamps_after(rows: list[Any], cutoff: datetime) -> int:
    from .documents import _coerce_timestamp

    n = 0
    for (value,) in rows:
        ts = _coerce_timestamp(value)
        if ts is not None and ts >= cutoff:
            n += 1
    return n


async def _failed_document_timestamps(
    session: AsyncSession, *, day: str, tenant: str | None
) -> list[Any]:
    from .documents import _documents_has_status_changed_at

    ts_expr = (
        "COALESCE(status_changed_at, uploaded_at)"
        if await _documents_has_status_changed_at(session)
        else "uploaded_at"
    )
    placeholders = ", ".join(f":s{i}" for i in range(len(_FAILED_DOC_STATUSES)))
    params: dict[str, Any] = {f"s{i}": s for i, s in enumerate(_FAILED_DOC_STATUSES)}
    params["day"] = day
    # ``tenant_id`` is named in the statement because this is a deliberate
    # cross-tenant aggregate (a count, no row data) served to internal
    # admins only — the same shape the extraction watchdog's scan uses, and
    # what ``tenant_middleware`` requires of any SELECT on this table. Under
    # RLS the per-tenant plan adds the real predicate.
    scope = ""
    if tenant is not None:
        scope = " AND tenant_id = :tenant"
        params["tenant"] = tenant
    sql = (
        f"SELECT {ts_expr} AS ts, tenant_id FROM documents"
        f" WHERE status IN ({placeholders})"
        f" AND substr(CAST({ts_expr} AS TEXT), 1, 10) >= :day{scope}"
    )
    rows = (await session.execute(text(sql), params)).fetchall()
    return [(r[0],) for r in rows]


async def _failed_engine_timestamps(
    session: AsyncSession, *, day: str, tenant: str | None
) -> list[Any]:
    params: dict[str, Any] = {"day": day}
    scope = ""
    if tenant is not None:
        scope = " AND tenant_id = :tenant"
        params["tenant"] = tenant
    sql = (
        "SELECT completed_at AS ts, tenant_id FROM engine_outputs"
        " WHERE status = 'failed'"
        " AND (error IS NULL OR error NOT LIKE 'skipped:%')"
        f" AND substr(CAST(completed_at AS TEXT), 1, 10) >= :day{scope}"
    )
    rows = (await session.execute(text(sql), params)).fetchall()
    return [(r[0],) for r in rows]


async def _count_failures(
    session: AsyncSession,
    *,
    table: str,
    distinct_tenants_sql: str,
    fetch,
    cutoff: datetime,
) -> tuple[int | None, dict[str, Any]]:
    """One counter: plan the scan for ``table``, run it, count. Returns
    ``(count_or_None, {"source", "tenants"})``."""
    from ..services.tenant_scan import plan_tenant_scan, scan_by_tenant

    plan = await plan_tenant_scan(distinct_tenants_sql=distinct_tenants_sql, job=f"admin_health_{table}")
    scan = {
        "source": plan.source,
        "tenants": len(plan.tenants) if plan.tenants is not None else None,
    }
    try:
        if plan.per_tenant:
            # The request session already holds a transaction that began
            # under the ADMIN's tenant (public_snapshot / ops_snapshot ran
            # in it). ``scan_by_tenant`` publishes each tenant and relies on
            # the ``after_begin`` hook to set the GUC — which fires only when
            # a transaction BEGINS. End the admin's transaction here so the
            # first tenant's fetch starts its own and is scoped to it;
            # without this the first tenant silently counts zero under RLS.
            await session.commit()
        rows = await scan_by_tenant(session, plan, fetch)
        return _timestamps_after(rows, cutoff), scan
    except Exception as exc:
        logger.warning("admin_health: %s failure count failed: %s", table, exc)
        await session.rollback()
        return None, scan


async def failure_counts_last_hour(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, Any]:
    """``{extraction_failures_last_hour, engine_failures_last_hour,
    failures_last_hour_scan}`` — see the module docstring. A counter whose
    query failed is ``None`` (never 0) and the failure is logged."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cutoff = now - FAILURE_WINDOW
    # One day of slack on the prefix so a timestamp written in another
    # zone's text form still enters the exact comparison.
    day = (cutoff - timedelta(days=1)).date().isoformat()

    # ``scan_by_tenant`` publishes each tenant on the ContextVar; restore the
    # caller's tenant afterwards so the rest of the request runs as them.
    token = current_tenant_id.set(current_tenant_id.get())
    try:
        docs, docs_scan = await _count_failures(
            session,
            table="documents",
            distinct_tenants_sql=(
                "SELECT DISTINCT tenant_id FROM documents WHERE status IN ('FAILED', 'PARSE_FAILED')"
            ),
            fetch=lambda s, t: _failed_document_timestamps(s, day=day, tenant=t),
            cutoff=cutoff,
        )
        engines, engines_scan = await _count_failures(
            session,
            table="engine_outputs",
            distinct_tenants_sql=(
                "SELECT DISTINCT tenant_id FROM engine_outputs WHERE status = 'failed'"
            ),
            fetch=lambda s, t: _failed_engine_timestamps(s, day=day, tenant=t),
            cutoff=cutoff,
        )
    finally:
        current_tenant_id.reset(token)
    return {
        "extraction_failures_last_hour": docs,
        "engine_failures_last_hour": engines,
        "failures_last_hour_scan": {
            "window_hours": int(FAILURE_WINDOW.total_seconds() // 3600),
            "documents": docs_scan,
            "engine_outputs": engines_scan,
        },
    }


@router.get("/health")
async def admin_health(
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """Public probe fields + the operator blocks (``raw_store``, ``auth``,
    ``semantic_search``, ``extraction_cache``, ``llm_provider``) + the
    last-hour failure counters. Internal tenants only."""
    me = await get_tenant(auth.tenant_id)
    if me is None or not me.is_serving or not me.is_internal:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ADMIN_HEALTH_REQUIRES_INTERNAL_TENANT,
        )
    body = await public_snapshot(session)
    body.update(await ops_snapshot(session))
    body.update(await failure_counts_last_hour(session))
    return body
