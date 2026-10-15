"""FON-91 — a document can never sit at EXTRACTING forever.

Covers:

* the watchdog marks a row stuck in a processing status past
  ``EXTRACTION_STUCK_MINUTES`` FAILED / ``timeout`` with a message naming
  the stage + elapsed time, stamps ``status_changed_at`` and audit-logs it;
* a fresh processing row and a terminal row are left untouched;
* ``uploaded_at`` is the fallback clock when ``status_changed_at`` is NULL;
* every row is handled under ITS tenant (``current_tenant_id``);
* the pipeline marks FAILED on ``asyncio.CancelledError`` (re-raised) and
  on an ordinary exception — from a fresh session;
* ``POST .../extract`` refuses a fresh EXTRACTING row (409) and accepts a
  stale one (202, audited, job id handed to the pipeline);
  ``POST .../reprocess`` refuses a fresh one the same way.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

# Pin SQLite BEFORE any app.* import (same pattern as the other modules).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-extraction-watchdog.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
# Tests drive ``tick_once`` by hand — keep every background loop off.
os.environ["EXTRACTION_WATCHDOG_ENABLED"] = "False"
os.environ["DIGEST_SCHEDULER_ENABLED"] = "False"

from sqlalchemy import text  # noqa: E402

from app.api import documents as docs_module  # noqa: E402
from app.auth.context import current_tenant_id  # noqa: E402
from app.services import extraction_watchdog as watchdog  # noqa: E402

_OTHER_TENANT = "00000000-0000-0000-0000-00000000f091"


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture
async def db_env():
    """Migrated sqlite DB + one deal per tenant; yields (factory, tenant, deal)."""
    from app.config import get_settings
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    tenant_id = str(get_settings().DEFAULT_TENANT_ID)
    deal_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        for tenant, deal in ((tenant_id, deal_id), (_OTHER_TENANT, str(uuid4()))):
            await session.execute(
                text(
                    "INSERT INTO deals (id, tenant_id, name, status, created_at, "
                    "updated_at) VALUES (:id, :tenant, 'Watchdog Hotel', 'Draft', "
                    ":ts, :ts)"
                ),
                {"id": deal, "tenant": tenant, "ts": "2026-07-01 00:00:00"},
            )
        await session.commit()
    yield factory, tenant_id, deal_id


async def _seed_doc(
    factory: Any,
    *,
    tenant_id: str,
    deal_id: str,
    status: str,
    status_changed_minutes_ago: float | None,
    uploaded_minutes_ago: float = 120.0,
    extraction_data: dict[str, Any] | None = None,
) -> str:
    doc_id = str(uuid4())
    payload = extraction_data if extraction_data is not None else {
        "parser": "test-fixture",
        "total_pages": 1,
        "content_hash": uuid4().hex * 2,
        "pages": [{"page_num": 1, "text": "Rooms revenue 1,000", "tables": [], "metadata": {}}],
    }
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, "
                "status, uploaded_at, status_changed_at, content_hash, storage_key, "
                "parser, extraction_data) VALUES (:id, :deal, :tenant, :fn, 'T12', "
                ":status, :up, :sca, :ch, :sk, 'test-fixture', :data)"
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fn": f"{doc_id}.xlsx",
                "status": status,
                "up": _iso(uploaded_minutes_ago),
                "sca": (
                    _iso(status_changed_minutes_ago)
                    if status_changed_minutes_ago is not None
                    else None
                ),
                "ch": payload.get("content_hash") or uuid4().hex * 2,
                "sk": f"raw/{doc_id}",
                "data": json.dumps(payload),
            },
        )
        await session.commit()
    return doc_id


async def _row(factory: Any, doc_id: str, tenant_id: str) -> dict[str, Any]:
    async with factory() as session:
        r = (
            await session.execute(
                text(
                    "SELECT status, status_changed_at, extraction_data FROM documents "
                    "WHERE id = :id AND tenant_id = :t"
                ),
                {"id": doc_id, "t": tenant_id},
            )
        ).first()
    assert r is not None
    m = dict(r._mapping)
    raw = m.get("extraction_data")
    m["extraction_data"] = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    return m


async def _audit_actions(factory: Any, *, tenant_id: str, doc_id: str) -> list[str]:
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT action FROM audit_log WHERE tenant_id = :t "
                    "AND resource_id = :rid ORDER BY created_at"
                ),
                {"t": tenant_id, "rid": doc_id},
            )
        ).fetchall()
    return [r[0] for r in rows]


# ─────────────────────────── watchdog tick ───────────────────────────


async def test_watchdog_times_out_a_stuck_extracting_row(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    stuck = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=45,
    )
    before = await _row(factory, stuck, tenant_id)

    n = await watchdog.tick_once()

    assert n >= 1
    after = await _row(factory, stuck, tenant_id)
    assert after["status"] == "FAILED"
    ed = after["extraction_data"]
    assert ed["error_kind"] == "timeout"
    assert ed["error_message"].startswith("Timed out after 45 min in Extracting")
    assert ed["failed_from_status"] == "EXTRACTING"
    # parse-stage payload survives the merge (forensics)
    assert ed["pages"] and ed["parser"] == "test-fixture"
    # the failure is itself a status change — the clock is re-stamped
    assert after["status_changed_at"] != before["status_changed_at"]
    assert docs_module._status_age_minutes(after) < 1.0
    assert watchdog.AUDIT_ACTION in await _audit_actions(
        factory, tenant_id=tenant_id, doc_id=stuck
    )


@pytest.mark.parametrize("stage", ["PARSING", "CLASSIFYING"])
async def test_watchdog_names_the_stage(db_env, stage: str) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status=stage, status_changed_minutes_ago=31,
    )
    await watchdog.tick_once()
    after = await _row(factory, doc, tenant_id)
    assert after["status"] == "FAILED"
    assert docs_module._stage_label(stage) in after["extraction_data"]["error_message"]


async def test_watchdog_leaves_fresh_and_terminal_rows_alone(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    fresh = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=5,
    )
    # Terminal rows are never candidates, however old.
    done = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTED", status_changed_minutes_ago=600,
    )
    uploaded = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="UPLOADED", status_changed_minutes_ago=600,
    )
    await watchdog.tick_once()
    assert (await _row(factory, fresh, tenant_id))["status"] == "EXTRACTING"
    assert (await _row(factory, done, tenant_id))["status"] == "EXTRACTED"
    assert (await _row(factory, uploaded, tenant_id))["status"] == "UPLOADED"


async def test_watchdog_falls_back_to_uploaded_at(db_env) -> None:
    """A row written before the column existed has status_changed_at NULL;
    the upload time is the only clock and it still counts."""
    factory, tenant_id, deal_id = db_env
    old = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=None, uploaded_minutes_ago=90,
    )
    recent = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=None, uploaded_minutes_ago=2,
    )
    await watchdog.tick_once()
    assert (await _row(factory, old, tenant_id))["status"] == "FAILED"
    assert (await _row(factory, recent, tenant_id))["status"] == "EXTRACTING"


async def test_watchdog_threshold_is_configurable(db_env, monkeypatch) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=12,
    )
    real = watchdog.get_settings()
    monkeypatch.setattr(
        watchdog, "get_settings",
        lambda: real.model_copy(update={"EXTRACTION_STUCK_MINUTES": 10}),
    )
    await watchdog.tick_once()
    after = await _row(factory, doc, tenant_id)
    assert after["status"] == "FAILED"
    assert "(limit 10 min)" in after["extraction_data"]["error_message"]


async def test_watchdog_runs_each_row_under_its_own_tenant(db_env, monkeypatch) -> None:
    factory, tenant_id, deal_id = db_env
    async with factory() as session:
        other_deal = (
            await session.execute(
                text("SELECT id FROM deals WHERE tenant_id = :t LIMIT 1"),
                {"t": _OTHER_TENANT},
            )
        ).scalar()
    mine = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=40,
    )
    theirs = await _seed_doc(
        factory, tenant_id=_OTHER_TENANT, deal_id=str(other_deal),
        status="EXTRACTING", status_changed_minutes_ago=40,
    )

    seen: dict[str, str | None] = {}
    real_log_audit = watchdog.log_audit

    async def _spy(session, **kwargs):
        seen[str(kwargs["resource_id"])] = current_tenant_id.get()
        return await real_log_audit(session, **kwargs)

    monkeypatch.setattr(watchdog, "log_audit", _spy)
    outer = current_tenant_id.set("outer-sentinel")
    try:
        await watchdog.tick_once()
        # the tick never leaks the last row's tenant to its caller
        assert current_tenant_id.get() == "outer-sentinel"
    finally:
        current_tenant_id.reset(outer)

    assert seen[mine] == tenant_id
    assert seen[theirs] == _OTHER_TENANT
    assert (await _row(factory, mine, tenant_id))["status"] == "FAILED"
    assert (await _row(factory, theirs, _OTHER_TENANT))["status"] == "FAILED"
    # audit rows land under the right tenant
    assert watchdog.AUDIT_ACTION in await _audit_actions(
        factory, tenant_id=_OTHER_TENANT, doc_id=theirs
    )
    assert await _audit_actions(factory, tenant_id=tenant_id, doc_id=theirs) == []


async def test_watchdog_start_is_a_noop_when_disabled(monkeypatch) -> None:
    # Another test module may already have populated the settings cache with
    # the default (enabled); pin the flag for THIS test and clear the cache so
    # the result does not depend on module ordering.
    from app.config import get_settings

    monkeypatch.setenv("EXTRACTION_WATCHDOG_ENABLED", "False")
    get_settings.cache_clear()
    try:
        assert watchdog.start_watchdog() is False
        await watchdog.stop_watchdog()
    finally:
        get_settings.cache_clear()


# ─────────────────────────── pipeline failure paths ───────────────────────────


async def test_pipeline_marks_failed_on_cancellation_and_reraises(db_env, monkeypatch) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="UPLOADED", status_changed_minutes_ago=1,
    )
    monkeypatch.setenv("EVALS_MOCK", "false")

    async def _cancelled(**kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(docs_module, "_run_graph_extraction", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        await docs_module._run_extraction_pipeline(
            deal_id=deal_id, doc_id=doc, tenant_id=tenant_id, job_id="job-cancel-1"
        )
    after = await _row(factory, doc, tenant_id)
    assert after["status"] == "FAILED"
    ed = after["extraction_data"]
    assert ed["error_kind"] == "cancelled"
    assert ed["failed_from_status"] == "EXTRACTING"
    assert ed["failed_job_id"] == "job-cancel-1"
    assert ed["pages"], "parse payload must survive the failure merge"


async def test_pipeline_marks_failed_on_unexpected_exception(db_env, monkeypatch) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="UPLOADED", status_changed_minutes_ago=1,
    )
    monkeypatch.setenv("EVALS_MOCK", "false")

    async def _boom(**kwargs):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(docs_module, "_run_graph_extraction", _boom)
    await docs_module._run_extraction_pipeline(
        deal_id=deal_id, doc_id=doc, tenant_id=tenant_id
    )
    after = await _row(factory, doc, tenant_id)
    assert after["status"] == "FAILED"
    assert after["extraction_data"]["error_kind"] == "other"
    assert "connection reset" in after["extraction_data"]["error_raw"]
    assert docs_module._status_age_minutes(after) < 1.0


async def test_parse_job_marks_failed_on_cancellation(db_env, monkeypatch) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="PARSING", status_changed_minutes_ago=1,
    )

    async def _cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(docs_module, "parse_document", _cancelled)
    with pytest.raises(asyncio.CancelledError):
        await docs_module._run_parse_and_extract(
            doc_id=doc, deal_id=deal_id, tenant_id=tenant_id,
            body=b"%PDF-1.4", filename="x.pdf",
        )
    after = await _row(factory, doc, tenant_id)
    assert after["status"] == "FAILED"
    assert after["extraction_data"]["error_kind"] == "cancelled"


def test_error_classifier_knows_timeout_and_cancel() -> None:
    assert docs_module._classify_extraction_error(TimeoutError())[0] == "timeout"
    assert docs_module._classify_extraction_error(asyncio.CancelledError())[0] == "cancelled"
    assert docs_module._classify_extraction_error(RuntimeError("x"))[0] == "other"


# ─────────────────────────── /extract + /reprocess guards ───────────────────────────


async def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_extract_refuses_a_fresh_extracting_row(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=3,
    )
    pipeline = AsyncMock()
    with patch.object(docs_module, "_run_extraction_pipeline", pipeline):
        async with await _client() as client:
            r = await client.post(f"/deals/{deal_id}/documents/{doc}/extract")
    assert r.status_code == 409, r.text
    assert "still Extracting" in r.json()["detail"]
    pipeline.assert_not_awaited()
    assert (await _row(factory, doc, tenant_id))["status"] == "EXTRACTING"


async def test_extract_accepts_a_stale_extracting_row(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="EXTRACTING", status_changed_minutes_ago=45,
    )
    pipeline = AsyncMock()
    with patch.object(docs_module, "_run_extraction_pipeline", pipeline):
        async with await _client() as client:
            r = await client.post(f"/deals/{deal_id}/documents/{doc}/extract?force=1")
    assert r.status_code == 202, r.text
    body = r.json()
    pipeline.assert_awaited_once()
    kwargs = pipeline.await_args.kwargs
    assert kwargs["doc_id"] == doc and kwargs["force"] is True
    # the route's job id is the one the pipeline logs every stage under
    assert kwargs["job_id"] == body["job_id"]
    assert "document.retry_stuck" in await _audit_actions(
        factory, tenant_id=tenant_id, doc_id=doc
    )


async def test_extract_still_accepts_terminal_rows(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="FAILED", status_changed_minutes_ago=1,
    )
    pipeline = AsyncMock()
    with patch.object(docs_module, "_run_extraction_pipeline", pipeline):
        async with await _client() as client:
            r = await client.post(f"/deals/{deal_id}/documents/{doc}/extract")
    assert r.status_code == 202, r.text
    pipeline.assert_awaited_once()


async def test_extract_uses_uploaded_at_when_status_clock_is_null(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    fresh = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="CLASSIFYING", status_changed_minutes_ago=None, uploaded_minutes_ago=4,
    )
    with patch.object(docs_module, "_run_extraction_pipeline", AsyncMock()):
        async with await _client() as client:
            r = await client.post(f"/deals/{deal_id}/documents/{fresh}/extract")
    assert r.status_code == 409, r.text


async def test_reprocess_refuses_a_fresh_processing_row(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="PARSING", status_changed_minutes_ago=2,
    )
    async with await _client() as client:
        r = await client.post(f"/deals/{deal_id}/documents/{doc}/reprocess")
    assert r.status_code == 409, r.text
    assert "still Parsing" in r.json()["detail"]


# ─────────────────────────── #18 re-extraction ───────────────────────────


async def _audit_rows(
    factory: Any, *, tenant_id: str, doc_id: str, action: str
) -> list[dict[str, Any]]:
    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT actor_id, actor_email, payload FROM audit_log "
                    "WHERE tenant_id = :t AND resource_id = :rid AND action = :a "
                    "ORDER BY created_at"
                ),
                {"t": tenant_id, "rid": doc_id, "a": action},
            )
        ).fetchall()
    out = []
    for r in rows:
        m = dict(r._mapping)
        raw = m.get("payload")
        m["payload"] = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        out.append(m)
    return out


def _as_analyst(tenant_id: str):
    """Override the route's ``get_current_auth`` with a JWT-shaped analyst so
    the audit row's actor can be asserted (the X-Tenant-Id test path has no
    user)."""
    from uuid import UUID

    from app.auth import get_current_auth
    from app.auth.context import AuthContext
    from app.main import app

    ctx = AuthContext(
        tenant_id=UUID(tenant_id), user_id="user_reextract", role="member",
        source="jwt", email="analyst@fondok.test",
    )
    app.dependency_overrides[get_current_auth] = lambda: ctx
    return app


@pytest.mark.parametrize("prev_status", ["EXTRACTED", "FAILED"])
async def test_extract_re_runs_a_finished_document_with_force_and_audits_actor(
    db_env, prev_status: str
) -> None:
    from app.auth import get_current_auth

    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status=prev_status, status_changed_minutes_ago=2,
    )
    pipeline = AsyncMock()
    app = _as_analyst(tenant_id)
    try:
        with patch.object(docs_module, "_run_extraction_pipeline", pipeline):
            async with await _client() as client:
                # no ?force — a finished document is ALWAYS re-run fresh
                r = await client.post(f"/deals/{deal_id}/documents/{doc}/extract")
    finally:
        app.dependency_overrides.pop(get_current_auth, None)
    assert r.status_code == 202, r.text
    pipeline.assert_awaited_once()
    kwargs = pipeline.await_args.kwargs
    assert kwargs["force"] is True
    assert kwargs["job_id"] == r.json()["job_id"]
    assert (await _row(factory, doc, tenant_id))["status"] == "CLASSIFYING"
    rows = await _audit_rows(
        factory, tenant_id=tenant_id, doc_id=doc, action="document.re_extract_requested"
    )
    assert len(rows) == 1
    assert rows[0]["actor_id"] == "user_reextract"
    assert rows[0]["actor_email"] == "analyst@fondok.test"
    meta = rows[0]["payload"]["metadata"]
    assert meta["previous_status"] == prev_status
    assert meta["endpoint"] == "extract"
    assert meta["force"] is True and meta["force_requested"] is False
    assert meta["pipeline_version"] == docs_module.EXTRACTION_PIPELINE_VERSION
    assert meta["job_id"] == r.json()["job_id"]


async def test_extract_on_a_not_yet_extracted_row_is_not_a_re_extract(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="UPLOADED", status_changed_minutes_ago=1,
    )
    pipeline = AsyncMock()
    with patch.object(docs_module, "_run_extraction_pipeline", pipeline):
        async with await _client() as client:
            r = await client.post(f"/deals/{deal_id}/documents/{doc}/extract")
    assert r.status_code == 202, r.text
    assert pipeline.await_args.kwargs["force"] is False
    assert "document.re_extract_requested" not in await _audit_actions(
        factory, tenant_id=tenant_id, doc_id=doc
    )


async def test_re_extract_refused_while_genuinely_processing(db_env) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="CLASSIFYING", status_changed_minutes_ago=1,
    )
    pipeline = AsyncMock()
    with patch.object(docs_module, "_run_extraction_pipeline", pipeline):
        async with await _client() as client:
            r = await client.post(f"/deals/{deal_id}/documents/{doc}/extract?force=true")
    assert r.status_code == 409, r.text
    pipeline.assert_not_awaited()
    assert await _audit_actions(factory, tenant_id=tenant_id, doc_id=doc) == []


async def test_re_extract_is_tenant_scoped(db_env) -> None:
    factory, _tenant_id, _deal_id = db_env
    # an EXTRACTED document that belongs to ANOTHER tenant's deal
    async with factory() as session:
        other_deal = (
            await session.execute(
                text("SELECT id FROM deals WHERE tenant_id = :t LIMIT 1"),
                {"t": _OTHER_TENANT},
            )
        ).scalar_one()
    doc = await _seed_doc(
        factory, tenant_id=_OTHER_TENANT, deal_id=str(other_deal),
        status="EXTRACTED", status_changed_minutes_ago=5,
    )
    pipeline = AsyncMock()
    with patch.object(docs_module, "_run_extraction_pipeline", pipeline):
        async with await _client() as client:
            r = await client.post(f"/deals/{other_deal}/documents/{doc}/extract")
    assert r.status_code == 404, r.text
    pipeline.assert_not_awaited()
    assert (await _row(factory, doc, _OTHER_TENANT))["status"] == "EXTRACTED"


async def test_reprocess_of_a_failed_document_is_a_forced_audited_re_extract(
    db_env,
) -> None:
    from app.auth import get_current_auth

    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="PARSE_FAILED", status_changed_minutes_ago=3,
    )

    class _Store:
        async def get(self, key: str) -> bytes:
            return b"%PDF-1.4 stored"

    job = AsyncMock()
    app = _as_analyst(tenant_id)
    try:
        with patch.object(docs_module, "get_raw_store", lambda settings: _Store()), \
                patch.object(docs_module, "_run_parse_and_extract", job):
            async with await _client() as client:
                r = await client.post(f"/deals/{deal_id}/documents/{doc}/reprocess")
    finally:
        app.dependency_overrides.pop(get_current_auth, None)
    assert r.status_code == 202, r.text
    job.assert_awaited_once()
    assert job.await_args.kwargs["force"] is True
    rows = await _audit_rows(
        factory, tenant_id=tenant_id, doc_id=doc, action="document.re_extract_requested"
    )
    assert len(rows) == 1 and rows[0]["actor_id"] == "user_reextract"
    assert rows[0]["payload"]["metadata"]["endpoint"] == "reprocess"
    assert rows[0]["payload"]["metadata"]["previous_status"] == "PARSE_FAILED"


async def test_parse_and_extract_hands_force_to_the_extraction_stage(
    db_env, monkeypatch
) -> None:
    factory, tenant_id, deal_id = db_env
    doc = await _seed_doc(
        factory, tenant_id=tenant_id, deal_id=deal_id,
        status="PARSING", status_changed_minutes_ago=0,
    )
    captured: dict[str, Any] = {}

    async def _inner(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(docs_module, "_run_parse_and_extract_inner", _inner)
    await docs_module._run_parse_and_extract(
        doc_id=doc, deal_id=deal_id, tenant_id=tenant_id,
        body=b"%PDF-1.4", filename="x.pdf", force=True,
    )
    assert captured["force"] is True
