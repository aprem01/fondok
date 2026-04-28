"""Pub/sub fan-out for memo-section streaming.

The Analyst publishes one ``InvestmentMemo`` section at a time when
``MEMO_STREAMING_ENABLED=true``; downstream UI consumers (an SSE route,
the LangSmith trace viewer, …) subscribe and forward each section.

Two backends:

* ``InProcessMemoBroadcast`` — single-process asyncio fan-out keyed by
  ``deal_id``. Good enough for dev and the single-replica deployment we
  ship today. Picked when ``REDIS_URL`` is unset.
* ``RedisMemoBroadcast`` — Redis pub/sub. Required when there's more
  than one worker replica, since the SSE subscriber may land on a
  different replica from the publisher. The factory routes here when
  ``settings.REDIS_URL`` is configured (Railway provisions it
  automatically when the Redis service is added).

Both implementations send a final ``DONE_SENTINEL`` payload after the
last section so subscribers can close the connection cleanly instead of
timing out.

Memo cache
----------
The module also exposes a process-local ``MemoCache`` that snapshots
each completed section as it's published, plus the final memo envelope
when ``DONE_SENTINEL`` fires. This is what ``GET /memo`` reads so a
client can recover the persisted sections after the SSE stream ends —
without needing a dedicated ``memo_sections`` table yet.

Error sentinel
--------------
``ERROR_SENTINEL`` lets the analyst fail loudly: the SSE handler turns
it into ``event: error\\ndata: {...}`` and closes cleanly, so the UI
shows a real error instead of hanging on a silent socket.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel payload that closes a subscription cleanly. Publishers send
# this after the last section so a downstream SSE route can emit
# ``event: done`` and drop the connection instead of timing out.
DONE_SENTINEL = "__memo_stream_done__"

# Sentinel payload that signals the analyst hit an unrecoverable error.
# The SSE handler converts this to ``event: error`` then closes; the
# memo cache marks the deal as ``failed`` so a subsequent ``GET /memo``
# can surface the failure instead of returning empty arrays silently.
ERROR_SENTINEL = "__memo_stream_error__"


class MemoBroadcast:
    """Abstract pub/sub for memo sections.

    Subclasses implement ``publish`` and ``subscribe`` to fan out a
    drafted section payload to any number of attached subscribers,
    keyed by ``deal_id``.
    """

    async def publish(self, deal_id: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError

    async def subscribe(
        self, deal_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        raise NotImplementedError
        yield  # pragma: no cover - signals AsyncIterator return type

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None


class InProcessMemoBroadcast(MemoBroadcast):
    """asyncio.Queue fan-out keyed by deal_id.

    Single-process only: a subscriber on replica A won't see a publish
    from replica B. ``maxsize`` per queue is set so a stuck subscriber
    can't pin memory indefinitely; we drop the oldest section with a
    warning instead.
    """

    _PER_DEAL_QUEUE_MAX = 64

    def __init__(self) -> None:
        self._subs: dict[str, list[asyncio.Queue[dict[str, Any] | None]]] = (
            defaultdict(list)
        )
        self._lock = asyncio.Lock()

    async def publish(self, deal_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            queues = list(self._subs.get(deal_id, []))
        for q in queues:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning(
                    "memo broadcast: queue full for deal=%s; dropping section",
                    deal_id,
                )

    async def subscribe(
        self, deal_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=self._PER_DEAL_QUEUE_MAX
        )
        async with self._lock:
            self._subs[deal_id].append(q)
        try:
            while True:
                item = await q.get()
                if item is None:
                    return
                yield item
                if item.get("event") == DONE_SENTINEL:
                    return
        finally:
            async with self._lock:
                if q in self._subs.get(deal_id, []):
                    self._subs[deal_id].remove(q)
                if not self._subs.get(deal_id):
                    self._subs.pop(deal_id, None)


class RedisMemoBroadcast(MemoBroadcast):
    """Redis pub/sub fan-out — survives multi-replica deployments.

    One publisher client is reused for the lifetime of the process;
    each ``subscribe`` call opens its own client + pubsub so cleanup
    is per-subscription. This matches the LogiCov pattern that's been
    running in production for institutional-grade pilots.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._publisher: Any = None

    async def _get_publisher(self) -> Any:
        if self._publisher is None:
            import redis.asyncio as aioredis

            self._publisher = aioredis.from_url(
                self._url, decode_responses=True
            )
        return self._publisher

    async def publish(self, deal_id: str, payload: dict[str, Any]) -> None:
        client = await self._get_publisher()
        await client.publish(deal_id, json.dumps(payload, default=str))

    async def subscribe(
        self, deal_id: str
    ) -> AsyncIterator[dict[str, Any]]:
        import redis.asyncio as aioredis

        client = aioredis.from_url(self._url, decode_responses=True)
        pubsub = client.pubsub()
        await pubsub.subscribe(deal_id)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                if not isinstance(data, str):
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning(
                        "memo broadcast: malformed JSON on %s", deal_id
                    )
                    continue
                yield payload
                if payload.get("event") == DONE_SENTINEL:
                    return
        finally:
            try:
                await pubsub.unsubscribe(deal_id)
                await pubsub.close()
            finally:
                await client.close()

    async def close(self) -> None:
        if self._publisher is not None:
            await self._publisher.close()
            self._publisher = None


# ─── memo cache (process-local snapshot of streamed sections) ─────


class MemoCache:
    """In-process per-deal snapshot of the streamed memo.

    The Analyst publishes each completed section to the broadcast; we
    listen on the same publish path and append the section here so
    ``GET /deals/{id}/memo`` can return the latest persisted view
    without spinning up a real ``memo_sections`` table.

    State per deal:

    * ``status`` — ``in_progress`` after the first section lands,
      ``done`` after ``DONE_SENTINEL``, ``failed`` after
      ``ERROR_SENTINEL``.
    * ``sections`` — the most-recent envelope per ``section_id``,
      newest write wins so a re-draft replaces the old body.
    * ``error`` — populated only when ``status == "failed"``.
    * ``generated_at`` — ISO timestamp of the most-recent terminal
      transition (done/failed).
    """

    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def record_section(self, deal_id: str, section: dict[str, Any]) -> None:
        sid = section.get("section_id")
        if not sid:
            return
        async with self._lock:
            entry = self._state.setdefault(
                deal_id,
                {
                    "status": "in_progress",
                    "sections": {},
                    "error": None,
                    "generated_at": None,
                },
            )
            entry["sections"][sid] = section
            if entry["status"] == "not_yet_generated":
                entry["status"] = "in_progress"

    async def mark_done(
        self, deal_id: str, *, generated_at: str | None = None
    ) -> None:
        async with self._lock:
            entry = self._state.setdefault(
                deal_id,
                {
                    "status": "done",
                    "sections": {},
                    "error": None,
                    "generated_at": generated_at,
                },
            )
            entry["status"] = "done"
            entry["generated_at"] = generated_at
            entry["error"] = None

    async def mark_failed(
        self,
        deal_id: str,
        *,
        message: str,
        generated_at: str | None = None,
    ) -> None:
        async with self._lock:
            entry = self._state.setdefault(
                deal_id,
                {
                    "status": "failed",
                    "sections": {},
                    "error": message,
                    "generated_at": generated_at,
                },
            )
            entry["status"] = "failed"
            entry["error"] = message
            entry["generated_at"] = generated_at

    async def clear(self, deal_id: str) -> None:
        """Drop any cached state for ``deal_id``.

        Called when a new run kicks off so a previous ``failed`` /
        ``done`` snapshot doesn't leak into the next run's
        ``GET /memo`` response before the first new section lands.
        """
        async with self._lock:
            self._state.pop(deal_id, None)

    async def get(self, deal_id: str) -> dict[str, Any] | None:
        async with self._lock:
            entry = self._state.get(deal_id)
            if entry is None:
                return None
            sections = list(entry["sections"].values())
            citations: list[dict[str, Any]] = []
            for sec in sections:
                for c in sec.get("citations") or []:
                    citations.append(c)
            return {
                "status": entry["status"],
                "sections": sections,
                "citations": citations,
                "error": entry["error"],
                "generated_at": entry["generated_at"],
            }

    def reset_for_test(self) -> None:
        self._state.clear()


_memo_cache_singleton: MemoCache | None = None


def get_memo_cache() -> MemoCache:
    """Return the process-wide memo cache singleton."""
    global _memo_cache_singleton
    if _memo_cache_singleton is None:
        _memo_cache_singleton = MemoCache()
    return _memo_cache_singleton


def reset_memo_cache_for_test() -> None:
    """Test hook — drop the cache singleton (and any state it holds)."""
    global _memo_cache_singleton
    if _memo_cache_singleton is not None:
        _memo_cache_singleton.reset_for_test()
    _memo_cache_singleton = None


# ─── subscriber helper: heartbeat + timeout wrapper ───────────────


async def subscribe_with_heartbeat(
    broadcast: "MemoBroadcast",
    channel: str,
    *,
    heartbeat_seconds: float = 15.0,
    total_timeout_seconds: float = 300.0,
) -> AsyncIterator[dict[str, Any]]:
    """Wrap ``broadcast.subscribe`` with a per-iteration heartbeat + total timeout.

    Yields three categories of payload:

    * Real payloads from the underlying broadcast — passed through unchanged.
    * Synthetic ``{"event": "ping"}`` payloads emitted every
      ``heartbeat_seconds`` of subscriber idleness so the SSE channel
      doesn't go silent (intermediate proxies tear down idle TCP
      connections at ~60s; a 15s heartbeat keeps everything warm and
      gives the client a "stream is alive" signal).
    * A synthetic ``{"event": ERROR_SENTINEL, ...}`` payload if
      ``total_timeout_seconds`` elapses without a ``DONE_SENTINEL`` /
      ``ERROR_SENTINEL`` resolution. After emitting the timeout the
      iterator returns.

    The wrapper preserves cancellation semantics: ``aclose()`` on the
    returned iterator cancels the underlying subscription cleanly.
    """
    started = time.monotonic()
    sub = broadcast.subscribe(channel)
    sub_iter = sub.__aiter__()
    try:
        while True:
            elapsed = time.monotonic() - started
            remaining = total_timeout_seconds - elapsed
            if remaining <= 0:
                yield {
                    "event": ERROR_SENTINEL,
                    "data": {
                        "message": (
                            "memo stream timed out after "
                            f"{int(total_timeout_seconds)}s with no terminal event"
                        ),
                        "code": "stream_timeout",
                    },
                }
                return
            wait = min(heartbeat_seconds, remaining)
            try:
                next_item = await asyncio.wait_for(sub_iter.__anext__(), timeout=wait)
            except asyncio.TimeoutError:
                yield {"event": "ping", "data": {"elapsed_s": int(elapsed + wait)}}
                continue
            except StopAsyncIteration:
                return
            yield next_item
            ev = next_item.get("event") if isinstance(next_item, dict) else None
            if ev in (DONE_SENTINEL, ERROR_SENTINEL):
                return
    finally:
        # Be defensive: not every backend's iterator implements aclose.
        aclose = getattr(sub, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # noqa: BLE001 - defensive
                pass


# ─── module-level singleton ───────────────────────────────────────

_broadcast_singleton: MemoBroadcast | None = None


def get_broadcast() -> MemoBroadcast:
    """Return the process-wide broadcast, picking backend on first use.

    ``REDIS_URL`` (set on Railway when the Redis service is
    provisioned) selects ``RedisMemoBroadcast`` so a multi-replica
    deploy can fan out sections across pods. Without it we fall back
    to ``InProcessMemoBroadcast`` for single-process dev / single-pod
    deployments.
    """
    global _broadcast_singleton
    if _broadcast_singleton is None:
        from ..config import get_settings

        settings = get_settings()
        if settings.REDIS_URL:
            host_hint = settings.REDIS_URL.split("@")[-1]
            logger.info("memo broadcast: using Redis at %s", host_hint)
            _broadcast_singleton = RedisMemoBroadcast(settings.REDIS_URL)
        else:
            logger.info(
                "memo broadcast: using in-process queues (REDIS_URL not set)"
            )
            _broadcast_singleton = InProcessMemoBroadcast()
    return _broadcast_singleton


def reset_broadcast_for_test() -> None:
    """Test hook — drop the singleton so the next ``get_broadcast()`` re-reads
    settings. Production code never calls this."""
    global _broadcast_singleton
    _broadcast_singleton = None


__all__ = [
    "DONE_SENTINEL",
    "ERROR_SENTINEL",
    "InProcessMemoBroadcast",
    "MemoBroadcast",
    "MemoCache",
    "RedisMemoBroadcast",
    "get_broadcast",
    "get_memo_cache",
    "reset_broadcast_for_test",
    "reset_memo_cache_for_test",
    "subscribe_with_heartbeat",
]
