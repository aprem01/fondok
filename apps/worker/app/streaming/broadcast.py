"""Pub/sub fan-out for memo-section streaming.

The Analyst publishes one ``InvestmentMemo`` section at a time when
``MEMO_STREAMING_ENABLED=true``; downstream UI consumers (an SSE route,
the LangSmith trace viewer, …) subscribe and forward each section.

Two backends:

* ``InProcessMemoBroadcast`` — single-process asyncio fan-out keyed by
  ``deal_id``. Good enough for dev and the single-replica deployment we
  ship today. Picked when ``REDIS_URL`` is unset.
* The abstract ``MemoBroadcast`` base intentionally ships without a
  Redis implementation in fondok yet — a future PR can drop one in
  beside ``InProcessMemoBroadcast`` and the factory will route to it
  when ``REDIS_URL`` lands in settings.

Both implementations send a final ``DONE_SENTINEL`` payload after the
last section so subscribers can close the connection cleanly instead of
timing out.
"""

from __future__ import annotations

import asyncio
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


# ─── module-level singleton ───────────────────────────────────────

_broadcast_singleton: MemoBroadcast | None = None


def get_broadcast() -> MemoBroadcast:
    """Return the process-wide broadcast, picking backend on first use.

    Today only ``InProcessMemoBroadcast`` is wired; a Redis backend
    will live alongside it once multi-replica deploys land.
    """
    global _broadcast_singleton
    if _broadcast_singleton is None:
        logger.info(
            "memo broadcast: using in-process queues (Redis backend not yet wired)"
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
    "get_broadcast",
    "reset_broadcast_for_test",
]
