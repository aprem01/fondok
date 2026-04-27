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

    async def fake_run_analyst_streaming(_payload: Any) -> None:
        bc = get_broadcast()
        await asyncio.sleep(0)  # let the subscriber attach
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
    async def fake_load_payload(d: str) -> dict[str, Any]:
        return {"deal_id": d}

    monkeypatch.setattr(deals_module, "_load_deal_payload", fake_load_payload)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Trigger generation.
        r = await client.post(f"/deals/{deal_id}/memo/generate")
        assert r.status_code == 200
        assert r.json() == {"status": "started", "deal_id": deal_id}

        # Stream sections. ASGITransport buffers, so we read the full body.
        async with client.stream("GET", f"/deals/{deal_id}/memo/stream") as resp:
            assert resp.status_code == 200
            ctype = resp.headers.get("content-type", "")
            assert ctype.startswith("text/event-stream"), f"got {ctype!r}"
            assert resp.headers.get("cache-control") == "no-cache"
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
            text = body.decode("utf-8")

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

    async def consume_one_then_quit() -> None:
        async for evt in bc.subscribe(channel):
            received.append(evt)
            break  # bail mid-stream

    consumer = asyncio.create_task(consume_one_then_quit())
    await asyncio.sleep(0)

    await bc.publish(channel, {"event": "section", "data": {"section_id": "x"}})

    await asyncio.wait_for(consumer, timeout=1.0)

    assert len(received) == 1
    # After the subscriber exits, the channel's queue list should be empty.
    # Internal attribute access is acceptable here since this test exists
    # specifically to guard the cleanup contract.
    assert bc._subs.get(channel) in (None, [])
