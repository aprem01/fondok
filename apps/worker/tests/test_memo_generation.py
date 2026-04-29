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


# ─────────────────── 6. Real source documents flow into Analyst ───────────────────


@pytest.mark.asyncio
async def test_load_deal_payload_uses_real_extracted_documents() -> None:
    """When a deal has ``EXTRACTED`` documents with parsed page text, the
    Analyst payload must surface those real ids + page excerpts so memo
    citations deep-link back to the source PDF instead of pointing at the
    Kimpton fixture filenames.
    """
    import json
    from datetime import UTC, datetime

    from app.api.deals import _load_deal_payload
    from app.database import get_session_factory

    deal_id = uuid4()
    await _insert_deal(deal_id)

    doc_uuid = uuid4()
    extraction_data = {
        "parser": "pymupdf",
        "total_pages": 2,
        "content_hash": "0" * 64,
        "parsed_at": datetime.now(UTC).isoformat(),
        "pages": [
            {
                "page_num": 1,
                "text": "Kimpton Angler Hotel — Property Overview\n214 keys, Miami Beach.",
                "tables": [],
                "metadata": {},
            },
            {
                "page_num": 2,
                "text": "T-12 Trailing Twelve Months\nTotal Revenue: $24,567,890\nNOI: $7,890,123",
                "tables": [],
                "metadata": {},
            },
        ],
    }
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, parser, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, :filename, :doc_type, 'EXTRACTED',
                    :ts, :pc, :parser, :data
                )
                """
            ),
            {
                "id": str(doc_uuid),
                "deal": str(deal_id),
                "tenant": "00000000-0000-0000-0000-000000000001",
                "filename": "kimpton-T12.pdf",
                "doc_type": "T12",
                "ts": datetime.now(UTC),
                "pc": 2,
                "parser": "pymupdf",
                "data": json.dumps(extraction_data),
            },
        )
        await session.commit()

        payload = await _load_deal_payload(str(deal_id), session=session)

    # The payload must carry exactly the real document, not the Kimpton
    # fixture appendix (which has multiple synthetic ``doc-NN`` entries).
    assert len(payload.source_documents) == 1, [
        d.document_id for d in payload.source_documents
    ]
    src = payload.source_documents[0]
    assert src.document_id == str(doc_uuid)
    assert src.filename == "kimpton-T12.pdf"
    assert src.doc_type == "T12"
    assert src.page_count == 2
    # Per-page text must round-trip from extraction_data.
    assert 1 in src.excerpts_by_page
    assert 2 in src.excerpts_by_page
    assert "Kimpton Angler" in src.excerpts_by_page[1]
    assert "$24,567,890" in src.excerpts_by_page[2]


@pytest.mark.asyncio
async def test_load_deal_payload_falls_back_to_fixture_without_session() -> None:
    """Demo path (no DB session) keeps the Kimpton fixture so the
    streaming smoke flow stays green."""
    from app.api.deals import _load_deal_payload

    payload = await _load_deal_payload("kimpton-angler-2026")

    # Fixture path emits the synthetic ``doc-NN`` ids.
    assert len(payload.source_documents) > 0
    for d in payload.source_documents:
        assert d.document_id.startswith("doc-")
        assert d.doc_type == "reference"


# ─────────────────── 7. Real numeric inputs replace fixture spread ───────────────────


@pytest.mark.asyncio
async def test_load_deal_payload_hydrates_real_spread_engines_variance() -> None:
    """When EXTRACTED docs + extraction_results + engine_outputs all
    exist on a deal, the Analyst payload must surface the deal's real
    metadata, build a USALIFinancials spread from extracted T-12 fields,
    pass real engine outputs, and emit a deterministic variance report
    when both broker and actuals are available — never the Kimpton
    fixture numbers underneath citations of a real PDF.
    """
    import json
    from datetime import UTC, datetime

    from app.api.deals import _load_deal_payload
    from app.database import get_session_factory

    deal_id = uuid4()
    tenant_id = "00000000-0000-0000-0000-000000000001"

    factory = get_session_factory()
    async with factory() as session:
        # Insert a deal with rich metadata so deal_data must come back
        # as the real row, not the Kimpton fixture.
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, city, keys, brand, service,
                    deal_stage, return_profile, status, ai_confidence,
                    created_at, updated_at
                ) VALUES (
                    :id, :tenant, :name, :city, :keys, :brand, :service,
                    :stage, :return_profile, 'Underwriting', 0.0, :ts, :ts
                )
                """
            ),
            {
                "id": str(deal_id),
                "tenant": tenant_id,
                "name": "Coral Bay Resort",
                "city": "Miami Beach",
                "keys": 214,
                "brand": "Marriott",
                "service": "Full Service",
                "stage": "LOI",
                "return_profile": "Core+",
                "ts": datetime.now(UTC),
            },
        )

        # T-12 document with extracted USALI fields (actuals).
        t12_doc_id = uuid4()
        t12_extraction = {
            "parser": "pymupdf",
            "total_pages": 1,
            "content_hash": "0" * 64,
            "parsed_at": datetime.now(UTC).isoformat(),
            "pages": [
                {"page_num": 1, "text": "T-12 statement page", "tables": [], "metadata": {}}
            ],
        }
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'coral-bay-T12.pdf', 'T12',
                    'EXTRACTED', :ts, 1, :data
                )
                """
            ),
            {
                "id": str(t12_doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "ts": datetime.now(UTC),
                "data": json.dumps(t12_extraction),
            },
        )
        # Actuals: T-12 USALI fields. ``_load_critic_inputs`` buckets
        # T12 fields directly into the actuals side.
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": str(t12_doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "fields": json.dumps(
                    [
                        {"field_name": "total_revenue", "value": 24_567_890.0},
                        {"field_name": "rooms_revenue", "value": 18_000_000.0},
                        {"field_name": "fb_revenue", "value": 5_500_000.0},
                        {"field_name": "other_revenue", "value": 1_067_890.0},
                        {"field_name": "noi", "value": 7_890_123.0},
                        {"field_name": "occupancy", "value": 0.712},
                        {"field_name": "adr", "value": 241.0},
                    ]
                ),
                "ts": datetime.now(UTC),
            },
        )

        # OM document with broker proforma headlines (broker side).
        om_doc_id = uuid4()
        om_extraction = {
            "parser": "pymupdf",
            "total_pages": 1,
            "content_hash": "1" * 64,
            "parsed_at": datetime.now(UTC).isoformat(),
            "pages": [
                {"page_num": 1, "text": "OM page", "tables": [], "metadata": {}}
            ],
        }
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status,
                    uploaded_at, page_count, extraction_data
                ) VALUES (
                    :id, :deal, :tenant, 'coral-bay-OM.pdf', 'OM',
                    'EXTRACTED', :ts, 1, :data
                )
                """
            ),
            {
                "id": str(om_doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "ts": datetime.now(UTC),
                "data": json.dumps(om_extraction),
            },
        )
        # Broker side: OM headlines deliberately rosier than T-12 so
        # variance flags fire (NOI 9.6M broker vs 7.9M actual).
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields,
                    confidence_report, agent_version, created_at
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields, '{}', 'test', :ts
                )
                """
            ),
            {
                "id": str(uuid4()),
                "doc": str(om_doc_id),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "fields": json.dumps(
                    [
                        {"field_name": "broker_proforma.total_revenue", "value": 28_000_000.0},
                        {"field_name": "broker_proforma.rooms_revenue", "value": 19_500_000.0},
                        {"field_name": "broker_proforma.fb_revenue", "value": 6_900_000.0},
                        {"field_name": "broker_proforma.noi", "value": 9_600_000.0},
                        {"field_name": "broker_proforma.occupancy", "value": 0.78},
                        {"field_name": "broker_proforma.adr", "value": 255.0},
                    ]
                ),
                "ts": datetime.now(UTC),
            },
        )

        # Engine outputs — at least one row so engine_results isn't empty.
        await session.execute(
            text(
                """
                INSERT INTO engine_outputs (
                    id, deal_id, tenant_id, run_id, engine_name, status,
                    inputs, outputs, error, started_at, completed_at,
                    runtime_ms
                ) VALUES (
                    :id, :deal, :tenant, :run, 'returns', 'complete',
                    '{}', :outputs, NULL, :ts, :ts, 12
                )
                """
            ),
            {
                "id": str(uuid4()),
                "deal": str(deal_id),
                "tenant": tenant_id,
                "run": str(uuid4()),
                "outputs": json.dumps(
                    {
                        "levered_irr": 0.231,
                        "equity_multiple": 2.37,
                        "year1_cash_on_cash": 0.062,
                    }
                ),
                "ts": datetime.now(UTC).isoformat(),
            },
        )
        await session.commit()

        payload = await _load_deal_payload(str(deal_id), session=session)

    # ── deal_data must be the real deal, not Kimpton ───────────────────
    assert payload.deal_data["name"] == "Coral Bay Resort", payload.deal_data
    assert payload.deal_data["city"] == "Miami Beach"
    assert payload.deal_data["keys"] == 214
    assert payload.deal_data["brand"] == "Marriott"

    # ── normalized_spread comes from the T-12 actuals ──────────────────
    assert payload.normalized_spread is not None
    assert payload.normalized_spread.noi == pytest.approx(7_890_123.0)
    assert payload.normalized_spread.total_revenue == pytest.approx(24_567_890.0)

    # ── engine_results carries the real engine outputs ────────────────
    assert "returns" in payload.engine_results
    returns_blob = payload.engine_results["returns"]
    assert returns_blob.get("levered_irr") == pytest.approx(0.231)
    assert returns_blob.get("equity_multiple") == pytest.approx(2.37)

    # ── variance_report fires CRITICAL/WARN flags on NOI delta ────────
    assert payload.variance_report is not None
    assert len(payload.variance_report.flags) > 0
    flag_fields = {f.field for f in payload.variance_report.flags}
    # NOI variance should fire — broker $9.6M vs actual $7.89M is ~22% off.
    assert "noi" in flag_fields, flag_fields
    noi_flag = next(f for f in payload.variance_report.flags if f.field == "noi")
    assert noi_flag.actual == pytest.approx(7_890_123.0)
    assert noi_flag.broker == pytest.approx(9_600_000.0)
    # Severity enum surfaces title-cased values ("Critical" / "Warn" / "Info").
    assert noi_flag.severity.value in ("Warn", "Critical")
