"""In-process poller for Anthropic Message Batches — Task V wire-up (2026-07).

Spins up an asyncio task at FastAPI startup that loops every
``ANALYST_BATCH_POLL_SECONDS`` (default 300s) and calls
:func:`app.agents.analyst_batch.poll_pending_batches`. On each tick
open batches are checked; ended batches drain their results into the
memo cache and persist a half-price ``ModelCall`` row.

Design mirrors ``services.digest_scheduler`` intentionally:

* Same start/stop lifecycle (``start_poller``/``stop_poller``) so the
  lifespan hook in ``app.main`` treats both loops identically.
* Same "expose ``tick_once`` for tests" idiom — tests pin the flag off
  and drive one iteration by hand without spinning up a real loop.

Gating
------
``ANALYST_BATCH_API_ENABLED=False`` (the default) means:

* ``start_poller()`` no-ops and returns False — no background task is
  ever created, no CPU/DB overhead on the sync-only setup.
* Even if the flag flips at runtime after startup, the poller stays
  off until the next process restart. That's fine — the flag flip is
  operational (Sam sets it in Railway) and Railway restarts on env
  change.

Multi-replica safety
--------------------
If Railway ever fans out to N replicas, every replica would run this
loop and every replica would poll the same batches. That's safe
because:

* Batch status transitions are monotonic (queued → in_progress →
  complete/failed/expired), so two workers observing the same batch
  will land on the same terminal state.
* The memo cache write and cost persist are idempotent — the last
  write wins, and the values are the same regardless of who wrote them.
* The DB update is a plain UPDATE with ``WHERE batch_id = :bid`` — no
  row is duplicated even under contention.

So a distributed leader-election layer is deferred; when it's needed
we swap this loop for an external cron that hits an admin endpoint,
same trade-off ``digest_scheduler`` will make.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..agents.analyst_batch import poll_pending_batches
from ..config import get_settings

logger = logging.getLogger(__name__)


_LOOP_TASK: asyncio.Task[Any] | None = None
_STOP_EVENT: asyncio.Event | None = None


async def tick_once() -> dict[str, int]:
    """Run a single poll iteration.

    Returns the tally emitted by ``poll_pending_batches`` so tests and
    log lines can report on activity.
    """
    return await poll_pending_batches()


async def _run_loop(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    interval = max(1.0, float(settings.ANALYST_BATCH_POLL_SECONDS))
    logger.info("analyst_batch_poller: started (interval=%.1fs)", interval)
    while not stop_event.is_set():
        try:
            tally = await tick_once()
            if tally.get("completed") or tally.get("failed") or tally.get("expired"):
                logger.info(
                    "analyst_batch_poller: tick tally=%s", tally
                )
        except Exception as ex:  # noqa: BLE001 - never crash the loop
            logger.exception("analyst_batch_poller: tick failed: %s", ex)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("analyst_batch_poller: stopped")


def start_poller() -> bool:
    """Kick off the background poll loop. Idempotent.

    No-ops when ``ANALYST_BATCH_API_ENABLED=False`` (the default).
    Returns True when a new task was spawned.
    """
    global _LOOP_TASK, _STOP_EVENT
    settings = get_settings()
    if not settings.ANALYST_BATCH_API_ENABLED:
        logger.info("analyst_batch_poller: disabled via settings (flag off)")
        return False
    if _LOOP_TASK is not None and not _LOOP_TASK.done():
        return False
    _STOP_EVENT = asyncio.Event()
    loop = asyncio.get_event_loop()
    _LOOP_TASK = loop.create_task(
        _run_loop(_STOP_EVENT), name="analyst_batch_poller"
    )
    return True


async def stop_poller() -> None:
    """Signal the loop to exit and await completion."""
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


__all__ = ["tick_once", "start_poller", "stop_poller"]
