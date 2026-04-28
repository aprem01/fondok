"""Regression tests for the IC-memo generation pipeline.

These pin the three production bugs surfaced by the live Railway smoke
test (deal id ``Y2RqN_zZT0KCOHzJwoOzXw`` / ``BbAPmjHVRseqrlp3O8poTA``):

1. ``POST /memo/generate`` returned ``500 Internal Server Error`` when
   the deal had no extracted documents — should be ``400`` with a
   user-actionable detail body.
2. ``GET /memo/stream`` hung for 90s emitting zero bytes when the
   analyst raised — should emit ``event: error`` and close cleanly,
   plus ``event: ping`` heartbeats during long runs.
3. ``GET /memo`` returned ``{sections: [], citations: []}`` silently
   when nothing was generated — should return
   ``status="not_yet_generated"`` so the UI can render the right CTA.

All tests are hermetic: no Anthropic call, no Railway dependency, no
real DB row beyond the per-test SQLite fixture.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import — same pattern
# as test_memo_edits.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-memo-generation.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.streaming import reset_broadcast_for_test, reset_memo_cache_for_test

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("memo_edits", "audit_log", "extraction_results", "documents", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    reset_broadcast_for_test()
    reset_memo_cache_for_test()
    yield
    reset_broadcast_for_test()
    reset_memo_cache_for_test()


async def _insert_deal(deal_id: UUID) -> None:
    """Create a bare deal row so the loader can find it in the DB."""
    from datetime import UTC, datetime

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, ai_confidence,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, 'Draft', 0.0, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": "00000000-0000-0000-0000-000000000001",
                "name": "memo-test deal",
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_extracted_document(deal_id: UUID) -> None:
    """Stamp a row in ``documents`` so the input guard flips green."""
    from datetime import UTC, datetime

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type,
                    status, uploaded_at
                ) VALUES (
                    :id, :deal, :tenant, 'om.pdf', 'OM',
                    'EXTRACTED', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "deal": str(deal_id),
                "tenant": "00000000-0000-0000-0000-000000000001",
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


def _patch_background_tasks_to_fire(monkeypatch: pytest.MonkeyPatch) -> list[asyncio.Task[Any]]:
    """FastAPI awaits BackgroundTasks before returning the response in
    test harnesses that don't actually run the lifespan loop. We stub
    ``add_task`` to spawn a real ``asyncio.Task`` so the POST returns
    immediately and the SSE subscriber can attach before the analyst
    starts publishing.

    Returns the list of spawned tasks so the caller can drain them.
    """
    import fastapi as fastapi_module

    spawned: list[asyncio.Task[Any]] = []

    def fire_and_forget(self: Any, func: Any, *args: Any, **kwargs: Any) -> None:
        spawned.append(asyncio.create_task(func(*args, **kwargs)))

    monkeypatch.setattr(fastapi_module.BackgroundTasks, "add_task", fire_and_forget)
    return spawned


# ─────────────────── 1. POST /memo/generate → 400 ───────────────────


@pytest.mark.asyncio
async def test_memo_generate_400_when_no_proforma() -> None:
    """A real DB-backed deal with zero documents must yield ``400``.

    The body must include the ``code`` discriminator so the web UI can
    route the user to the upload flow without parsing prose.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")

    assert r.status_code == 400, r.text
    body = r.json()
    detail = body.get("detail")
    # FastAPI nests our structured detail under the top-level "detail"
    # key, so we check both the shape and the surfaced fields.
    assert isinstance(detail, dict), detail
    assert detail.get("code") == "memo_inputs_missing"
    assert "broker proforma" in detail.get("detail", "")
    assert "T-12" in detail.get("detail", "")
    assert "proforma" in detail.get("missing", [])
    assert "t12" in detail.get("missing", [])


# ─────────────────── 2. SSE stream emits error ───────────────────


@pytest.mark.asyncio
async def test_memo_stream_emits_error_on_generator_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the analyst raises, the SSE response must include
    ``event: error`` and close cleanly with ``event: done``."""
    from httpx import ASGITransport, AsyncClient

    from app.api import deals as deals_module

    spawned = _patch_background_tasks_to_fire(monkeypatch)

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_extracted_document(deal_id)

    async def fake_load_payload(d: str, *, session: Any = None) -> Any:
        # Bypass the fixture builder; the analyst is mocked anyway.
        from app.agents.analyst import AnalystInput

        return AnalystInput(
            tenant_id="00000000-0000-0000-0000-000000000001",
            deal_id=d,
            deal_data={"id": d},
        )

    monkeypatch.setattr(deals_module, "_load_deal_payload", fake_load_payload)

    async def boom(_payload: Any) -> None:
        # Tiny pause so the SSE subscriber attaches first.
        await asyncio.sleep(0.05)
        raise RuntimeError("anthropic 503 service unavailable")

    import app.agents.analyst as analyst_mod

    monkeypatch.setattr(analyst_mod, "run_analyst_streaming", boom)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")
        assert r.status_code == 200, r.text

        async with client.stream(
            "GET", f"/deals/{deal_id}/memo/stream"
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers.get("content-type", "").startswith(
                "text/event-stream"
            )
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
            text_body = body.decode("utf-8")

    for t in spawned:
        if not t.done():
            t.cancel()

    # The stream must announce itself, surface the error, and close.
    assert "event: start" in text_body, text_body
    assert "event: error" in text_body, text_body
    assert "event: done" in text_body, text_body
    # The error message we raised should be visible to the client.
    assert "anthropic 503" in text_body or "RuntimeError" in text_body


# ─────────────────── 3. SSE heartbeat during long runs ───────────────────


@pytest.mark.asyncio
async def test_memo_stream_heartbeats_during_long_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 30s analyst run must emit at least one ``event: ping``
    heartbeat. We compress the heartbeat interval to 0.05s and fake a
    1.0s analyst run so the test stays fast."""
    from httpx import ASGITransport, AsyncClient

    from app.api import deals as deals_module

    # Compress timing so the test runs in ~1s instead of 30s.
    monkeypatch.setattr(deals_module, "_SSE_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(deals_module, "_SSE_TOTAL_TIMEOUT_SECONDS", 5.0)

    spawned = _patch_background_tasks_to_fire(monkeypatch)

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_extracted_document(deal_id)

    async def fake_load_payload(d: str, *, session: Any = None) -> Any:
        from app.agents.analyst import AnalystInput

        return AnalystInput(
            tenant_id="00000000-0000-0000-0000-000000000001",
            deal_id=d,
            deal_data={"id": d},
        )

    monkeypatch.setattr(deals_module, "_load_deal_payload", fake_load_payload)

    async def slow_analyst(payload: Any) -> None:
        from app.streaming.broadcast import DONE_SENTINEL, get_broadcast

        bc = get_broadcast()
        # Simulate a long analyst run: idle long enough for at least
        # ~10 heartbeat intervals before publishing the terminal event.
        await asyncio.sleep(0.6)
        await bc.publish(
            f"memo:{payload.deal_id}",
            {"event": DONE_SENTINEL, "data": {"sections": 0}},
        )

    import app.agents.analyst as analyst_mod

    monkeypatch.setattr(analyst_mod, "run_analyst_streaming", slow_analyst)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")
        assert r.status_code == 200, r.text

        async with client.stream(
            "GET", f"/deals/{deal_id}/memo/stream"
        ) as resp:
            assert resp.status_code == 200
            body = b""
            async for chunk in resp.aiter_bytes():
                body += chunk
            text_body = body.decode("utf-8")

    for t in spawned:
        if not t.done():
            t.cancel()

    assert text_body.count("event: ping") >= 1, text_body
    assert "event: done" in text_body


# ─────────────────── 4. GET /memo before generation ───────────────────


@pytest.mark.asyncio
async def test_memo_get_404_when_not_generated() -> None:
    """Architectural decision: we return 200 with a status discriminator
    rather than 404, so the UI shape is uniform. This test pins that
    decision and the exact ``status="not_yet_generated"`` value."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "not_yet_generated"
    assert body["sections"] == []
    assert body["citations"] == []
    assert body["error"] is None
    assert body["generated_at"] is None
    # ``deal_id`` round-trips as a string UUID.
    assert UUID(body["deal_id"]) == deal_id


# ─────────────────── 5. Memo persists after a successful run ───────────────────


@pytest.mark.asyncio
async def test_memo_persists_after_successful_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After a happy-path streaming run, ``GET /memo`` returns the
    sections that were published — not an empty array."""
    from httpx import ASGITransport, AsyncClient

    from app.api import deals as deals_module

    spawned = _patch_background_tasks_to_fire(monkeypatch)

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_extracted_document(deal_id)

    async def fake_load_payload(d: str, *, session: Any = None) -> Any:
        from app.agents.analyst import AnalystInput

        return AnalystInput(
            tenant_id="00000000-0000-0000-0000-000000000001",
            deal_id=d,
            deal_data={"id": d},
        )

    monkeypatch.setattr(deals_module, "_load_deal_payload", fake_load_payload)

    async def fake_streaming(payload: Any) -> None:
        """Publish two real-shaped sections + DONE_SENTINEL straight
        through the broadcast and the memo cache, mirroring what the
        real analyst does on the success path."""
        from datetime import UTC, datetime

        from app.streaming.broadcast import (
            DONE_SENTINEL,
            get_broadcast,
            get_memo_cache,
        )

        bc = get_broadcast()
        cache = get_memo_cache()
        channel = f"memo:{payload.deal_id}"
        await asyncio.sleep(0.02)

        sections = [
            {
                "section_id": "investment_thesis",
                "title": "Investment Thesis",
                "body": "Body for thesis.",
                "citations": [
                    {"document_id": "doc-01", "page": 1, "field": None, "excerpt": None}
                ],
            },
            {
                "section_id": "recommendation",
                "title": "Recommendation",
                "body": "Approve with conditions.",
                "citations": [
                    {"document_id": "doc-01", "page": 2, "field": None, "excerpt": None}
                ],
            },
        ]
        for sec in sections:
            await bc.publish(
                channel,
                {"event": "section", "data": sec, "metadata": {}},
            )
            await cache.record_section(payload.deal_id, sec)

        generated_at = datetime.now(UTC).isoformat()
        await bc.publish(
            channel,
            {
                "event": DONE_SENTINEL,
                "data": {"sections": len(sections)},
                "metadata": {"generated_at": generated_at},
            },
        )
        await cache.mark_done(payload.deal_id, generated_at=generated_at)

    import app.agents.analyst as analyst_mod

    monkeypatch.setattr(analyst_mod, "run_analyst_streaming", fake_streaming)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")
        assert r.status_code == 200, r.text

        # Drain the SSE stream so we know the analyst finished + the
        # cache flipped to ``done``.
        async with client.stream("GET", f"/deals/{deal_id}/memo/stream") as resp:
            async for _ in resp.aiter_bytes():
                pass

        r2 = await client.get(f"/deals/{deal_id}/memo")

    for t in spawned:
        if not t.done():
            t.cancel()

    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["status"] == "done", body
    assert len(body["sections"]) == 2, body
    section_ids = {s["section_id"] for s in body["sections"]}
    assert section_ids == {"investment_thesis", "recommendation"}
    assert len(body["citations"]) == 2
    assert body["generated_at"] is not None
    assert body["error"] is None
