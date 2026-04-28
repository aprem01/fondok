"""Tests for the memo streaming pub/sub + SSE endpoint.

These tests do NOT call Claude. The Analyst's section-drafting loop
is replaced by a small fixture that publishes six canned sections to
the in-process broadcast, exercising the same publish→subscribe flow
the live Opus path uses.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

# Force the SQLite dev DSN before any app modules import — same pattern
# as test_smoke.py — so settings don't bleed in from the developer shell.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


# ───────────────────────── helpers ─────────────────────────


def _canned_sections() -> list[dict[str, Any]]:
    """Six fake memo sections — one per required ``MemoSectionId``."""
    return [
        {
            "section_id": sid,
            "title": sid.replace("_", " ").title(),
            "body": f"Body for {sid}.",
            "citations": [
                {"document_id": "doc-01", "page": 1, "field": None, "excerpt": None}
            ],
        }
        for sid in [
            "investment_thesis",
            "market_analysis",
            "deal_overview",
            "financial_analysis",
            "risk_factors",
            "recommendation",
        ]
    ]


# ─────────────────── 1. broadcast roundtrip ───────────────────


@pytest.mark.asyncio
async def test_broadcast_publish_subscribe_roundtrip() -> None:
    """Publish 3 sections + DONE_SENTINEL → subscriber receives all 4."""
    from app.streaming import (
        DONE_SENTINEL,
        InProcessMemoBroadcast,
        reset_broadcast_for_test,
    )

    reset_broadcast_for_test()
    bc = InProcessMemoBroadcast()
    channel = "memo:roundtrip-deal"

    received: list[dict[str, Any]] = []

    async def consume() -> None:
        async for evt in bc.subscribe(channel):
            received.append(evt)

    consumer = asyncio.create_task(consume())
    # Give the subscriber a tick to register on the channel.
    await asyncio.sleep(0)

    for i in range(3):
        await bc.publish(
            channel,
            {"event": "section", "data": {"section_id": f"s{i}", "body": f"body {i}"}},
        )
    await bc.publish(
        channel, {"event": DONE_SENTINEL, "data": {"sections": 3}}
    )

    # Consumer auto-exits when it sees DONE_SENTINEL.
    await asyncio.wait_for(consumer, timeout=2.0)

    assert len(received) == 4
    assert [r["event"] for r in received[:3]] == ["section", "section", "section"]
    assert received[-1]["event"] == DONE_SENTINEL
    assert received[-1]["data"] == {"sections": 3}


# ─────────────────── 2. SSE endpoint smoke test ───────────────────


@pytest.mark.asyncio
async def test_memo_stream_endpoint_returns_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /memo/generate then GET /memo/stream returns text/event-stream
    with one ``section`` event per published section and a final ``done``.

    The Analyst is mocked so no Anthropic call fires.
    """
    from httpx import ASGITransport, AsyncClient

    from app.api import deals as deals_module
    from app.streaming import DONE_SENTINEL, get_broadcast, reset_broadcast_for_test

    reset_broadcast_for_test()

    sections = _canned_sections()
    deal_id = "stream-test-deal"

    # FastAPI's BackgroundTasks awaits the task before completing the
    # response — that interleaving is fine in production but it would
    # cause the in-test POST to block on the publisher, which itself
    # is waiting for the SSE subscriber. We sidestep that by stubbing
    # ``add_task`` to spawn a real asyncio.Task instead.
    import fastapi as fastapi_module

    real_add_task = fastapi_module.BackgroundTasks.add_task

    spawned: list[asyncio.Task[Any]] = []

    def fire_and_forget(self: Any, func: Any, *args: Any, **kwargs: Any) -> None:
        spawned.append(asyncio.create_task(func(*args, **kwargs)))

    monkeypatch.setattr(fastapi_module.BackgroundTasks, "add_task", fire_and_forget)

    async def fake_run_analyst_streaming(_payload: Any) -> None:
        bc = get_broadcast()
        # Tiny pause so the SSE subscriber attaches before we fan out.
        await asyncio.sleep(0.05)
        for idx, sec in enumerate(sections, start=1):
            await bc.publish(
                f"memo:{deal_id}",
                {
                    "event": "section",
                    "data": sec,
                    "metadata": {
                        "input_tokens": 100 * idx,
                        "output_tokens": 50 * idx,
                        "model": "claude-opus-4-7",
                        "section_index": idx,
                        "section_total": len(sections),
                    },
                },
            )
        await bc.publish(
            f"memo:{deal_id}",
            {
                "event": DONE_SENTINEL,
                "data": {"sections": len(sections)},
                "metadata": {
                    "input_tokens": 100 * len(sections),
                    "output_tokens": 50 * len(sections),
                    "model": "claude-opus-4-7",
                },
            },
        )

    # Patch the symbol the route imports lazily.
    import app.agents.analyst as analyst_mod

    monkeypatch.setattr(analyst_mod, "run_analyst_streaming", fake_run_analyst_streaming)

    # Also stub the payload loader so we don't pay a fixture build cost.
    # ``session`` is now a keyword-only path the real loader uses to
    # validate inputs; the fake just ignores it.
    async def fake_load_payload(d: str, *, session: Any = None) -> dict[str, Any]:
        return {"deal_id": d}

    monkeypatch.setattr(deals_module, "_load_deal_payload", fake_load_payload)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")
        assert r.status_code == 200
        assert r.json() == {"status": "started", "deal_id": deal_id}

        async with client.stream("GET", f"/deals/{deal_id}/memo/stream") as resp:
            assert resp.status_code == 200
            ctype = resp.headers.get("content-type", "")
            assert ctype.startswith("text/event-stream"), f"got {ctype!r}"
            assert resp.headers.get("cache-control") == "no-cache"
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
            text = body.decode("utf-8")

    # Quiet warning hygiene — drain spawned tasks.
    for t in spawned:
        if not t.done():
            t.cancel()
    # Restore (monkeypatch handles this on teardown but be explicit).
    fastapi_module.BackgroundTasks.add_task = real_add_task  # type: ignore[method-assign]

    # Six section events + one done event.
    assert text.count("event: section\n") == len(sections), text
    assert text.count("event: done\n") == 1, text
    # Each section's body shows up in the stream.
    for sec in sections:
        assert sec["section_id"] in text


# ─────────────────── 3. cancellation cleanup ───────────────────


@pytest.mark.asyncio
async def test_memo_stream_cancellation() -> None:
    """Closing a subscription mid-stream must release the queue from the
    broadcast's internal subscriber map."""
    from app.streaming import InProcessMemoBroadcast, reset_broadcast_for_test

    reset_broadcast_for_test()
    bc = InProcessMemoBroadcast()
    channel = "memo:cancel-deal"

    received: list[dict[str, Any]] = []

    # Drive the async generator manually so we can explicitly call
    # ``aclose()`` — otherwise the finally-block cleanup that removes
    # the queue from ``_subs`` runs only when the generator is GC'd,
    # which Python doesn't guarantee is prompt enough for an assertion.
    sub = bc.subscribe(channel)
    consumer = asyncio.create_task(sub.__anext__())
    # Yield once so the generator registers its queue.
    await asyncio.sleep(0)

    await bc.publish(channel, {"event": "section", "data": {"section_id": "x"}})

    received.append(await asyncio.wait_for(consumer, timeout=1.0))

    # Close the generator — runs the cleanup finally-block.
    await sub.aclose()

    assert len(received) == 1
    # After the subscriber exits, the channel must be fully removed.
    assert bc._subs.get(channel) in (None, [])


# ─────────────────── 4. factory wiring ───────────────────


def test_get_broadcast_picks_redis_when_url_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Factory routes to ``RedisMemoBroadcast`` when ``REDIS_URL`` is set.

    We don't actually open a Redis connection here — the publisher
    client is built lazily on the first ``publish`` call. Asserting
    the singleton's class is enough to prove the wiring.
    """
    from app.config import get_settings
    from app.streaming import (
        InProcessMemoBroadcast,
        RedisMemoBroadcast,
        get_broadcast,
        reset_broadcast_for_test,
    )

    reset_broadcast_for_test()
    settings = get_settings()
    monkeypatch.setattr(settings, "REDIS_URL", "redis://fake-host:6379/0")
    try:
        bc = get_broadcast()
        assert isinstance(bc, RedisMemoBroadcast)
        assert not isinstance(bc, InProcessMemoBroadcast)
    finally:
        reset_broadcast_for_test()
        monkeypatch.setattr(settings, "REDIS_URL", None)


# ─────────────────── 5. Redis backend roundtrip (opt-in) ───────────────────


@pytest.mark.asyncio
async def test_redis_broadcast_roundtrip() -> None:
    """Live Redis pub/sub roundtrip — skipped when no ``REDIS_URL`` env.

    Set ``REDIS_URL=redis://localhost:6379/0`` to opt in. We publish
    three sections + a DONE_SENTINEL and assert the subscriber sees
    all four in order before exiting cleanly.
    """
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        pytest.skip("REDIS_URL not set — skipping live Redis backend test")

    try:
        import redis.asyncio  # noqa: F401
    except ImportError:
        pytest.skip("redis package not installed")

    from app.streaming import DONE_SENTINEL, RedisMemoBroadcast

    bc = RedisMemoBroadcast(redis_url)
    channel = f"memo:redis-roundtrip-{os.getpid()}"

    received: list[dict[str, Any]] = []

    async def consume() -> None:
        async for evt in bc.subscribe(channel):
            received.append(evt)

    consumer = asyncio.create_task(consume())
    # Give the subscriber a tick to attach to the channel.
    await asyncio.sleep(0.1)

    try:
        for i in range(3):
            await bc.publish(
                channel,
                {
                    "event": "section",
                    "data": {"section_id": f"s{i}", "body": f"body {i}"},
                },
            )
        await bc.publish(
            channel, {"event": DONE_SENTINEL, "data": {"sections": 3}}
        )

        # Subscriber auto-exits on DONE_SENTINEL.
        await asyncio.wait_for(consumer, timeout=5.0)
    finally:
        await bc.close()

    assert len(received) == 4
    assert received[-1]["event"] == DONE_SENTINEL
    assert received[-1]["data"] == {"sections": 3}
