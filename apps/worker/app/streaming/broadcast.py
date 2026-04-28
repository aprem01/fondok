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
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import defaultdict
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Sentinel payload that closes a subscription cleanly. Publishers send
# this after the last section so a downstream SSE route can emit
# ``event: done`` and drop the connection instead of timing out.
DONE_SENTINEL = "__memo_stream_done__"


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
    "InProcessMemoBroadcast",
    "MemoBroadcast",
    "RedisMemoBroadcast",
    "get_broadcast",
    "reset_broadcast_for_test",
]
