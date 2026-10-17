"""Extraction watchdog (FON-91) — no document sits in a processing state forever.

2026-10-08: a forced re-extraction of a 74-sheet P&L stayed ``EXTRACTING``
for 37 minutes with no newer ``extraction_results`` row and no
``error_kind`` — the background task had died (the failure write shared
the pipeline's poisoned session, and ``asyncio.CancelledError`` from a
worker redeploy is a ``BaseException`` the ``except Exception`` never saw).
``api/documents.py`` now closes both holes; this module is the belt to
those braces: whatever killed a task, the row it owned gets a terminal
status.

Loop
----
Spins up an asyncio task at FastAPI startup that runs ``tick_once`` once
immediately (a worker that just booted owns NO in-flight task, so any
row still in a processing status older than the threshold is already
orphaned) and then every ``EXTRACTION_WATCHDOG_TICK_SECONDS`` (default
60s). Each tick:

1. scan of ``documents`` for rows in ``PARSING`` / ``CLASSIFYING`` /
   ``EXTRACTING`` — the SELECT names ``tenant_id`` so ``tenant_middleware``
   sees a tenant reference, the same convention ``digest_scheduler`` uses.
   With RLS off it is today's single cross-tenant SELECT; with the RLS
   GUC hook live it runs once per tenant, each under that tenant's GUC
   (``services/tenant_scan.py``);
2. per row: publish THAT row's tenant on ``current_tenant_id`` (FON-93 —
   the RLS GUC hook reads it on the next transaction), compute the age
   from ``COALESCE(status_changed_at, uploaded_at)`` and, when it exceeds
   ``EXTRACTION_STUCK_MINUTES`` (default 30), mark the row ``FAILED``
   with ``error_kind="timeout"`` and a message naming the stage and the
   elapsed time. The write is optimistic (``AND status = <observed>``)
   so a row that advanced between the scan and the write is left alone;
3. one ``audit_log`` row per timed-out document
   (``document.extraction_timed_out``, severity ``warning``).

Design mirrors ``services.digest_scheduler`` intentionally: same
start/stop lifecycle, same ``tick_once`` test seam, same single-replica
caveat (N replicas would each run the loop — harmless here because the
write is idempotent and status-guarded).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..auth.context import current_tenant_id
from ..config import get_settings
from ..database import get_session_factory
from .tenant_scan import plan_tenant_scan, scan_by_tenant

logger = logging.getLogger(__name__)


_LOOP_TASK: asyncio.Task[Any] | None = None
_STOP_EVENT: asyncio.Event | None = None

#: ``error_kind`` written on every row the watchdog fails.
TIMEOUT_ERROR_KIND = "timeout"
AUDIT_ACTION = "document.extraction_timed_out"


def timeout_message(*, stage: str, elapsed_minutes: float, threshold_minutes: int) -> str:
    """User-facing copy stored on the row. The web parses the leading
    ``Timed out after N min`` to render the Data Room badge, so keep the
    prefix stable."""
    return (
        f"Timed out after {int(elapsed_minutes)} min in {stage} — the "
        f"extraction job stopped reporting progress (limit "
        f"{threshold_minutes} min). Nothing is wrong with the file; "
        "click Retry to re-run."
    )


def _processing_predicate() -> tuple[str, dict[str, Any]]:
    """``WHERE status IN (…)`` over the processing statuses + its binds."""
    from ..api.documents import DOC_PROCESSING_STATUSES

    statuses = sorted(DOC_PROCESSING_STATUSES)
    placeholders = ", ".join(f":st{i}" for i in range(len(statuses)))
    return (
        f"WHERE status IN ({placeholders})",
        {f"st{i}": s for i, s in enumerate(statuses)},
    )


async def _fetch_processing_rows(
    session: AsyncSession, *, tenant_id: str | None = None
) -> list[dict[str, Any]]:
    """Every document in a processing status, across tenants.

    ``tenant_id`` in the column list is what satisfies the
    tenant_middleware predicate check for this deliberately cross-tenant
    scan (see module docstring). ``status_changed_at`` is column-probed
    — a worker ahead of the migration reads NULL and falls back to
    ``uploaded_at``. ``tenant_id`` (FON-93, per-tenant scans under RLS)
    adds ``AND tenant_id = :tenant``; ``None`` is today's cross-tenant
    scan, unchanged.
    """
    from ..api.documents import _with_status_changed_at

    predicate, params = _processing_predicate()
    scope = ""
    if tenant_id is not None:
        scope = "\n           AND tenant_id = :tenant"
        params["tenant"] = str(tenant_id)
    sql = await _with_status_changed_at(
        session,
        f"""
        SELECT id, deal_id, tenant_id, filename, status,
               uploaded_at /*status_changed_at*/
          FROM documents
         {predicate}{scope}
        """,
    )
    rows = (await session.execute(text(sql), params)).fetchall()
    return [dict(r._mapping) for r in rows]


async def _time_out_row(
    session: AsyncSession,
    *,
    row: dict[str, Any],
    elapsed_minutes: float,
    threshold_minutes: int,
    now: datetime,
) -> bool:
    """Mark one stuck row FAILED / timeout + audit it. Returns True when the
    row was updated (False when it advanced in the meantime)."""
    from ..api.documents import (
        _mark_document_failed,
        _stage_label,
    )

    doc_id = str(row["id"])
    tenant_id = str(row["tenant_id"])
    observed_status = str(row.get("status") or "")
    stage = _stage_label(observed_status)
    message = timeout_message(
        stage=stage,
        elapsed_minutes=elapsed_minutes,
        threshold_minutes=threshold_minutes,
    )
    updated = await _mark_document_failed(
        doc_id=doc_id,
        tenant_id=tenant_id,
        error_kind=TIMEOUT_ERROR_KIND,
        error_message=message,
        error_raw=(
            f"watchdog: {observed_status} for {elapsed_minutes:.1f} min "
            f"(threshold {threshold_minutes} min)"
        ),
        expected_statuses={observed_status},
    )
    if not updated:
        return False
    logger.warning(
        "extraction_watchdog: doc=%s deal=%s tenant=%s stuck in %s for %.1f min "
        "(threshold %d) — marked FAILED error_kind=%s",
        doc_id,
        row.get("deal_id"),
        tenant_id,
        observed_status,
        elapsed_minutes,
        threshold_minutes,
        TIMEOUT_ERROR_KIND,
    )
    try:
        await log_audit(
            session,
            tenant_id=tenant_id,
            action=AUDIT_ACTION,
            resource_type="document",
            resource_id=doc_id,
            deal_id=str(row["deal_id"]) if row.get("deal_id") else None,
            severity="warning",
            before={"status": observed_status},
            after={"status": "FAILED", "error_kind": TIMEOUT_ERROR_KIND},
            metadata={
                "stage": stage,
                "elapsed_minutes": round(elapsed_minutes, 1),
                "threshold_minutes": threshold_minutes,
                "filename": row.get("filename"),
                "detected_at": now.isoformat(),
            },
            tags=["fon-91", "watchdog", "timeout"],
        )
        await session.commit()
    except Exception as ex:
        logger.exception(
            "extraction_watchdog: audit write failed for doc=%s: %s", doc_id, ex
        )
        with contextlib.suppress(Exception):
            await session.rollback()
    return True


async def tick_once(now: datetime | None = None) -> int:
    """Run one watchdog iteration. Returns the number of rows timed out.

    Reusable by both the background loop and the test harness.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    factory = get_session_factory()
    tenant_token = current_tenant_id.set(current_tenant_id.get())
    try:
        async with factory() as session:
            return await _tick_with_session(session, now=now)
    finally:
        # Never leak the last row's tenant into the loop's next tick.
        current_tenant_id.reset(tenant_token)


async def _tick_with_session(session: AsyncSession, *, now: datetime) -> int:
    from ..api.documents import _status_age_minutes

    settings = get_settings()
    threshold = int(settings.EXTRACTION_STUCK_MINUTES)
    # FON-93: with RLS off this is today's single cross-tenant scan; with
    # the GUC hook live it is one scan per tenant, each under that
    # tenant's GUC (services/tenant_scan.py, docs/WORKSPACE_ISOLATION.md §7).
    predicate, predicate_params = _processing_predicate()
    plan = await plan_tenant_scan(
        distinct_tenants_sql=f"SELECT DISTINCT tenant_id FROM documents {predicate}",
        params=predicate_params,
        job="extraction_watchdog",
    )

    async def _fetch(s: AsyncSession, tenant: str | None) -> list[dict[str, Any]]:
        if tenant is None:  # today's call, verbatim
            return await _fetch_processing_rows(s)
        return await _fetch_processing_rows(s, tenant_id=tenant)

    rows = await scan_by_tenant(session, plan, _fetch)
    # End the scan's transaction so each row below starts its own and the
    # ``after_begin`` hook publishes THAT row's tenant (FON-93).
    await session.commit()
    timed_out = 0
    for row in rows:
        tenant_id = row.get("tenant_id")
        current_tenant_id.set(str(tenant_id) if tenant_id else None)
        try:
            age = _status_age_minutes(row, now=now)
            if age is None:
                logger.warning(
                    "extraction_watchdog: doc=%s has no readable status "
                    "timestamp — skipping",
                    row.get("id"),
                )
                continue
            if age < threshold:
                continue
            if await _time_out_row(
                session,
                row=row,
                elapsed_minutes=age,
                threshold_minutes=threshold,
                now=now,
            ):
                timed_out += 1
        except Exception as ex:
            logger.exception(
                "extraction_watchdog: failed on doc=%s: %s", row.get("id"), ex
            )
    return timed_out


async def _run_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    interval = max(1.0, float(settings.EXTRACTION_WATCHDOG_TICK_SECONDS))
    logger.info(
        "extraction_watchdog: started (tick=%.1fs, stuck_after=%d min)",
        interval,
        int(settings.EXTRACTION_STUCK_MINUTES),
    )
    while not stop_event.is_set():
        try:
            count = await tick_once()
            if count:
                logger.warning(
                    "extraction_watchdog: timed out %d stuck document(s)", count
                )
        except Exception as ex:
            logger.exception("extraction_watchdog: tick failed: %s", ex)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("extraction_watchdog: stopped")


def start_watchdog() -> bool:
    """Kick off the background loop. Idempotent; returns True when a new
    task was spawned. The first tick runs immediately (startup sweep).

    No-ops when ``EXTRACTION_WATCHDOG_ENABLED=False`` (tests drive
    ``tick_once`` by hand).
    """
    global _LOOP_TASK, _STOP_EVENT
    settings = get_settings()
    if not settings.EXTRACTION_WATCHDOG_ENABLED:
        logger.info("extraction_watchdog: disabled via settings")
        return False
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return False
    _STOP_EVENT = asyncio.Event()
    loop = asyncio.get_event_loop()
    _LOOP_TASK = loop.create_task(
        _run_loop(_STOP_EVENT), name="extraction_watchdog"
    )
    return True


async def stop_watchdog() -> None:
    """Signal the loop to exit + await its completion."""
    global _LOOP_TASK, _STOP_EVENT
    if _STOP_EVENT is not None:
        _STOP_EVENT.set()
    if _LOOP_TASK is not None:
        try:
            await asyncio.wait_for(_LOOP_TASK, timeout=5.0)
        except TimeoutError:
            _LOOP_TASK.cancel()
        except Exception:
            pass
        finally:
            _LOOP_TASK = None
            _STOP_EVENT = None


__all__ = [
    "AUDIT_ACTION",
    "TIMEOUT_ERROR_KIND",
    "start_watchdog",
    "stop_watchdog",
    "tick_once",
    "timeout_message",
]
