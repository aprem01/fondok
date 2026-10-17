"""In-process digest scheduler (Wave 4 W4.5).

Spins up an asyncio task at FastAPI startup that loops every
``DIGEST_SCHEDULER_TICK_SECONDS`` (default 60s). On each tick we
``SELECT`` schedules whose ``next_run_at <= NOW()`` and dispatch
them, then re-compute ``next_run_at`` from the cadence.

This is intentionally a no-frills loop. Production deployments with
multiple worker replicas should swap this out for a real scheduler
(Celery beat, an external cron-tab, SQS-driven worker) — running
this loop on every replica would dispatch the same schedule N times
per tick. For now, every Fondok deploy runs a single worker process
so the in-process loop is fine.

Test harness
------------
The loop exposes ``tick_once`` so tests can drive a single iteration
without sleeping for the tick interval. Tests pin
``DIGEST_SCHEDULER_ENABLED=False`` so the FastAPI lifespan doesn't
start a background task that pollutes the asyncio loop, then call
``tick_once`` directly. See ``test_pipeline_digests.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.context import current_tenant_id
from ..config import get_settings
from ..database import get_session_factory
from .pipeline_digest import (
    build_digest_payload,
    compute_next_run_at,
    dispatch_digest,
)
from .tenant_scan import plan_tenant_scan, scan_by_tenant

logger = logging.getLogger(__name__)


_LOOP_TASK: asyncio.Task[Any] | None = None
_STOP_EVENT: asyncio.Event | None = None


_SCHEDULE_COLUMNS = (
    "id, tenant_id, name, saved_view_id, cadence, weekday, hour_utc, "
    "delivery, slack_webhook_url, email_recipients, "
    "include_kpi_summary, include_recently_mutated, "
    "include_deals_meeting_target, include_full_table, "
    "is_active, last_run_at, next_run_at, created_at, updated_at"
)


def _row_to_dict(row: Any) -> dict[str, Any]:
    m = dict(row._mapping)
    recipients = m.get("email_recipients")
    if isinstance(recipients, str):
        import json as _json

        try:
            recipients = _json.loads(recipients)
        except _json.JSONDecodeError:
            recipients = []
    if not isinstance(recipients, list):
        recipients = []
    m["email_recipients"] = recipients
    m["hour_utc"] = int(m.get("hour_utc") or 13)
    return m


_DUE_PREDICATE = """
                 WHERE is_active = :active
                   AND next_run_at IS NOT NULL
                   AND next_run_at <= :now
"""


def _now_param(session: AsyncSession, now: datetime) -> Any:
    """SQLite stores ``next_run_at`` as TEXT and compares as TEXT, so we
    bind an ISO-8601 string explicitly to keep the comparison sane
    (otherwise SQLAlchemy emits a ``YYYY-MM-DD HH:MM:SS.ffffff``
    coercion without TZ that mis-orders against our ``+00:00``-stamped
    rows). Postgres takes the datetime directly."""
    is_sqlite = (
        session.bind is not None and session.bind.dialect.name == "sqlite"
    )
    return now.astimezone(UTC).isoformat() if is_sqlite else now


async def _fetch_due_schedules(
    session: AsyncSession, *, now: datetime, tenant_id: str | None = None
) -> list[dict[str, Any]]:
    """Pull every active schedule whose ``next_run_at`` is in the past.

    ``next_run_at`` is updated on dispatch, so the "what fires next?"
    decision is a single indexed query — no need to recompute cadence
    in SQL.

    ``tenant_id`` (FON-93, per-tenant scans under RLS) adds
    ``AND tenant_id = :tenant``; ``None`` is today's cross-tenant scan,
    unchanged.
    """
    params: dict[str, Any] = {"active": True, "now": _now_param(session, now)}
    scope = ""
    if tenant_id is not None:
        scope = "                   AND tenant_id = :tenant\n"
        params["tenant"] = str(tenant_id)
    rows = (
        await session.execute(
            text(
                f"""
                SELECT {_SCHEDULE_COLUMNS}
                  FROM pipeline_digest_schedules
                {_DUE_PREDICATE}{scope}"""
            ),
            params,
        )
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


async def _mark_dispatched(
    session: AsyncSession,
    *,
    schedule_id: str,
    tenant_id: str,
    cadence: str,
    hour_utc: int,
    weekday: int | None,
    now: datetime,
) -> None:
    next_run = compute_next_run_at(
        cadence=cadence,
        hour_utc=hour_utc,
        weekday=weekday,
        now=now,
    )
    await session.execute(
        # tenant_id predicate keeps tenant_middleware / Sentry quiet — see
        # apps/worker/app/tenant_middleware.py.
        text(
            """
            UPDATE pipeline_digest_schedules
               SET last_run_at = :now,
                   next_run_at = :next_run,
                   updated_at = :now
             WHERE id = :id
               AND tenant_id = :tenant
            """
        ),
        {
            "id": schedule_id,
            "tenant": str(tenant_id),
            "now": now,
            "next_run": next_run,
        },
    )
    await session.commit()


async def tick_once(now: datetime | None = None) -> int:
    """Run one scheduler iteration. Returns the number of dispatched
    schedules.

    Reusable by both the background loop and the test harness.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    factory = get_session_factory()
    tenant_token = current_tenant_id.set(current_tenant_id.get())
    try:
        async with factory() as session:
            return await _tick_with_session(session, now=now)
    finally:
        # Never leak the last schedule's tenant into the loop's next tick.
        current_tenant_id.reset(tenant_token)


async def _tick_with_session(session: AsyncSession, *, now: datetime) -> int:
    dispatched = 0
    # FON-93: with RLS off this is today's single cross-tenant scan; with
    # the GUC hook live it is one scan per tenant, each under that
    # tenant's GUC (services/tenant_scan.py, docs/WORKSPACE_ISOLATION.md §7).
    plan = await plan_tenant_scan(
        distinct_tenants_sql=(
            "SELECT DISTINCT tenant_id FROM pipeline_digest_schedules"
            + _DUE_PREDICATE
        ),
        params={"active": True, "now": _now_param(session, now)},
        job="digest_scheduler",
    )

    async def _fetch(s: AsyncSession, tenant: str | None) -> list[dict[str, Any]]:
        if tenant is None:  # today's call, verbatim
            return await _fetch_due_schedules(s, now=now)
        return await _fetch_due_schedules(s, now=now, tenant_id=tenant)

    due = await scan_by_tenant(session, plan, _fetch)
    # End the scan's transaction so each schedule below starts its own and
    # the ``after_begin`` hook publishes THAT schedule's tenant.
    await session.commit()
    for schedule in due:
        tenant_id = schedule.get("tenant_id")
        schedule_id = schedule.get("id")
        # FON-93: every statement for this schedule runs as its tenant.
        current_tenant_id.set(str(tenant_id) if tenant_id else None)
        try:
            payload = await build_digest_payload(
                session,
                tenant_id=tenant_id,
                schedule=schedule,
                now=now,
            )
            dispatch_digest(schedule, payload)
        except Exception as ex:  # noqa: BLE001 — never crash the loop
            logger.exception(
                "digest_scheduler: dispatch failed for %s: %s",
                schedule_id,
                ex,
            )
        try:
            await _mark_dispatched(
                session,
                schedule_id=str(schedule_id),
                tenant_id=str(tenant_id),
                cadence=schedule.get("cadence", "daily"),
                hour_utc=int(schedule.get("hour_utc") or 13),
                weekday=schedule.get("weekday"),
                now=now,
            )
        except Exception as ex:  # noqa: BLE001
            logger.exception(
                "digest_scheduler: mark_dispatched failed for %s: %s",
                schedule_id,
                ex,
            )
        dispatched += 1
    return dispatched


async def _run_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    interval = max(1.0, float(settings.DIGEST_SCHEDULER_TICK_SECONDS))
    logger.info(
        "digest_scheduler: started (tick=%.1fs)", interval
    )
    while not stop_event.is_set():
        try:
            count = await tick_once()
            if count:
                logger.info(
                    "digest_scheduler: dispatched %d schedule(s)", count
                )
        except Exception as ex:  # noqa: BLE001
            logger.exception("digest_scheduler: tick failed: %s", ex)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("digest_scheduler: stopped")


def start_scheduler() -> bool:
    """Kick off the background loop. Idempotent; returns True when
    a new task was spawned.

    No-ops when ``DIGEST_SCHEDULER_ENABLED=False`` (tests, multi-replica
    deployments that drive cron externally).
    """
    global _LOOP_TASK, _STOP_EVENT
    settings = get_settings()
    if not settings.DIGEST_SCHEDULER_ENABLED:
        logger.info("digest_scheduler: disabled via settings")
        return False
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return False
    _STOP_EVENT = asyncio.Event()
    loop = asyncio.get_event_loop()
    _LOOP_TASK = loop.create_task(
        _run_loop(_STOP_EVENT), name="digest_scheduler"
    )
    return True


async def stop_scheduler() -> None:
    """Signal the loop to exit + await its completion."""
    global _LOOP_TASK, _STOP_EVENT
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _LOOP_TASK is not None:
        try:
            await asyncio.wait_for(_LOOP_TASK, timeout=5.0)
        except asyncio.TimeoutError:
            _LOOP_TASK.cancel()
        except Exception:  # noqa: BLE001
            pass
        finally:
            _LOOP_TASK = None
            _STOP_EVENT = None


__all__ = ["tick_once", "start_scheduler", "stop_scheduler"]
