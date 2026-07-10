"""Process-level extraction throttle (Wave 4 reliability fix — Bug #2).

The upload endpoint schedules a per-upload background batch
(:func:`apps.worker.app.api.documents._run_parse_and_extract_batch`)
that fans out documents at ``_PARSE_BATCH_CONCURRENCY``. Each document's
extractor itself fans out its ~5-page chunks at ``_EXTRACTOR_MAX_CONCURRENCY``
inside :mod:`apps.worker.app.agents.extractor`. Stacked, a 16-doc upload
could schedule up to 16 docs × 4 chunks = 64 concurrent Sonnet calls,
saturating Anthropic rate limits AND the worker's single uvicorn event
loop. While extractions ran, other endpoints (``/deals/{id}``,
``/deals``, ``/deals/{id}/validation/broker-questions``…) hung because
the loop was starved (Sam QA Wave 4).

This module installs a **process-level** semaphore that caps the number
of documents actively in the extraction phase ACROSS all uploads:

* ``EXTRACTOR_MAX_CONCURRENT_DOCS`` (default 4) — global doc cap.
* ``EXTRACTOR_MAX_CHUNK_CONCURRENCY`` (default 2) — per-doc chunk cap;
  the single source of truth also read by
  :mod:`apps.worker.app.agents.extractor`. The legacy env name
  ``EXTRACTOR_CHUNK_CONCURRENCY`` is still accepted for it via a
  pydantic validation alias (see :class:`app.config.Settings`).

Combined cap: 4 docs × 2 chunks = 8 concurrent Sonnet calls regardless
of how many docs the analyst uploaded in one batch.

We also emit a rate-limited info log when the semaphore queue depth
grows past 8 so an operator notices a sustained backlog without spamming
logs every time a new doc enters the queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from ..config import get_settings

logger = logging.getLogger(__name__)


def _read_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    """Parse a positive-int env var; fall back to ``default`` on garbage."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "extractor_throttle: %s=%r is not an int — falling back to %d",
            name,
            raw,
            default,
        )
        return default
    return max(minimum, val)


# Resolved once at import time. The process-wide semaphore must be a
# single instance for the cap to bind across uploads, so we can't pick
# the size up per-call. Operators bounce the worker to change the cap.
EXTRACTOR_MAX_CONCURRENT_DOCS: int = _read_int_env(
    "EXTRACTOR_MAX_CONCURRENT_DOCS", default=4
)
# Per-doc chunk cap. Single source of truth: the pydantic settings
# field ``EXTRACTOR_MAX_CHUNK_CONCURRENCY`` (which also accepts the
# legacy ``EXTRACTOR_CHUNK_CONCURRENCY`` env name via a validation
# alias — see :class:`app.config.Settings`). Sourcing it here rather
# than reading ``os.environ["EXTRACTOR_CHUNK_CONCURRENCY"]`` keeps this
# process-wide throttle and the per-doc fan-out in
# :mod:`app.agents.extractor` bound to the SAME knob, so an ops
# throttle during a rate-limit incident applies to both halves. The
# config field is clamped ge=1/le=4; that is the authoritative cap.
EXTRACTOR_CHUNK_CONCURRENCY: int = get_settings().EXTRACTOR_MAX_CHUNK_CONCURRENCY

_EXTRACTOR_SEM: asyncio.Semaphore = asyncio.Semaphore(EXTRACTOR_MAX_CONCURRENT_DOCS)

# ``_waiting`` tracks the number of acquirers currently blocked in
# ``__aenter__``. ``asyncio.Semaphore`` doesn't expose this directly, so
# we increment/decrement around the acquire ourselves. Used only for the
# rate-limited backlog log below.
_waiting: int = 0
_inflight: int = 0

# Backlog log throttle: never more than one info line per minute even if
# the queue stays deep the entire upload.
_BACKLOG_LOG_INTERVAL_S = 60.0
_BACKLOG_DEPTH_THRESHOLD = 8
_last_backlog_log_ts: float = 0.0


def _maybe_log_backlog(queue_depth: int) -> None:
    global _last_backlog_log_ts
    if queue_depth <= _BACKLOG_DEPTH_THRESHOLD:
        return
    now = time.monotonic()
    if now - _last_backlog_log_ts < _BACKLOG_LOG_INTERVAL_S:
        return
    _last_backlog_log_ts = now
    logger.info(
        "extraction backlog: %d docs queued — consider adjusting "
        "EXTRACTOR_MAX_CONCURRENT_DOCS (currently %d)",
        queue_depth,
        EXTRACTOR_MAX_CONCURRENT_DOCS,
    )


@asynccontextmanager
async def acquire_extractor_slot() -> AsyncIterator[None]:
    """Reserve one of the ``EXTRACTOR_MAX_CONCURRENT_DOCS`` extraction slots.

    Yields after the semaphore is acquired; releases on exit (including
    exception paths — exceptions inside the wrapped block do NOT leak a
    slot). Designed to be wrapped around the per-doc extraction step in
    :func:`apps.worker.app.api.documents._run_parse_and_extract` so the
    cap applies regardless of how many uploads are queued at once.
    """
    global _waiting, _inflight
    _waiting += 1
    try:
        _maybe_log_backlog(_waiting)
        await _EXTRACTOR_SEM.acquire()
    except BaseException:
        _waiting -= 1
        raise
    _waiting -= 1
    _inflight += 1
    try:
        yield
    finally:
        _inflight -= 1
        _EXTRACTOR_SEM.release()


def queue_depth() -> int:
    """Number of acquirers currently blocked waiting for a slot.

    Exposed for tests + a future ``/admin/extractor`` health endpoint.
    Not part of the public API surface in production.
    """
    return _waiting


def inflight_count() -> int:
    """Number of acquirers currently holding a slot (mid-extraction)."""
    return _inflight


def _reset_for_tests() -> None:
    """Reinitialize the semaphore between tests.

    Only the worker test suite should call this. Production code never
    touches the semaphore through anything but ``acquire_extractor_slot``.
    """
    global _EXTRACTOR_SEM, _waiting, _inflight, _last_backlog_log_ts
    _EXTRACTOR_SEM = asyncio.Semaphore(EXTRACTOR_MAX_CONCURRENT_DOCS)
    _waiting = 0
    _inflight = 0
    _last_backlog_log_ts = 0.0
