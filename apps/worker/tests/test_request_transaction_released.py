"""Incident 2026-11-03, round 2 — a request never carries a transaction into
its background task or its streamed response.

FastAPI runs BackgroundTasks (and streams a StreamingResponse body) BEFORE a
request-scoped ``yield`` dependency exits, so a transaction the handler left
open on the ``get_session`` session stayed open — one pooled connection
checked out, ACCESS SHARE locks held — for the whole background job. The
reviewer reproduced it on Postgres through a plain upload whose LAST file is
a duplicate (the duplicate check's read was never committed) and through
``POST /memo/generate``: ``idle in transaction`` for the whole job, a
concurrent ``ALTER TABLE`` blocked.

These tests drive the real endpoints over HTTP with only the background body
stubbed; inside it, ``engine.pool.checkedout()`` must be 0 (an AsyncSession
holds a pooled connection exactly while it has a transaction open).
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-request-txn.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.setdefault("DOCUMENT_STORAGE_ROOT", str(Path(tempfile.gettempdir()) / "fondok-tests-request-txn-storage"))
os.environ["EXTRACTION_WATCHDOG_ENABLED"] = "False"
os.environ["DIGEST_SCHEDULER_ENABLED"] = "False"

from sqlalchemy import text  # noqa: E402

_TENANT = "00000000-0000-0000-0000-000000000001"
_HEADERS = {"X-Tenant-Id": _TENANT}
_PDF = (Path(__file__).parent / "fixtures" / "sample_t12.pdf").read_bytes()


def _held() -> int:
    from app.database import get_engine

    return get_engine().pool.checkedout()


@pytest.fixture
async def deal_id() -> str:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.storage import reset_raw_store_cache

    reset_raw_store_cache()
    await run_startup_migrations()
    did = str(uuid4())
    async with get_session_factory()() as s:
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                "VALUES (:id, :t, 'Request Txn Hotel', 'Draft', :ts, :ts)"
            ),
            {"id": did, "t": _TENANT, "ts": datetime.now(UTC)},
        )
        await s.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, "
                "uploaded_at, content_hash) VALUES (:id, :d, :t, 'old.pdf', 'T12', 'EXTRACTED', "
                ":ts, :ch)"
            ),
            {
                "id": str(uuid4()),
                "d": did,
                "t": _TENANT,
                "ts": datetime.now(UTC),
                "ch": hashlib.sha256(_PDF).hexdigest(),
            },
        )
        await s.commit()
    assert _held() == 0
    return did


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers=_HEADERS)


async def test_upload_whose_last_file_is_a_duplicate_releases_the_session(
    deal_id: str, monkeypatch
) -> None:
    from app.api import documents as docs_module

    held: list[int] = []

    async def _batch(*, batch):
        held.append(_held())

    monkeypatch.setattr(docs_module, "_run_parse_and_extract_batch", _batch)
    files = [
        ("files", ("new.pdf", _PDF + b"\n% new bytes\n", "application/pdf")),
        ("files", ("dup.pdf", _PDF, "application/pdf")),  # same bytes as old.pdf
    ]
    async with _client() as client:
        r = await client.post(f"/deals/{deal_id}/documents/upload", files=files)
    assert r.status_code == 201, r.text
    assert held == [0], f"connections held during the upload's background job: {held}"


async def test_memo_generate_releases_the_session_before_the_background_analyst(
    deal_id: str, monkeypatch
) -> None:
    from app.agents.analyst import AnalystInput
    from app.api import deals as deals_module

    held: list[int] = []

    async def _payload(*args: Any, **kwargs: Any):
        session = kwargs.get("session") or args[0]
        await session.execute(text("SELECT id FROM documents WHERE tenant_id = :t"), {"t": _TENANT})
        return AnalystInput(tenant_id=_TENANT, deal_id=deal_id, deal_data={"id": deal_id})

    async def _analyst(payload: Any) -> None:
        held.append(_held())

    monkeypatch.setattr(deals_module, "_load_deal_payload", _payload)
    monkeypatch.setattr(deals_module, "_safe_run_analyst_streaming", _analyst)
    async with _client() as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")
    assert r.status_code == 200, r.text
    assert held == [0], f"connections held during the memo background task: {held}"


async def test_run_model_kickoff_releases_the_session(deal_id: str, monkeypatch) -> None:
    from app.api import model as model_module

    held: list[int] = []

    async def _run_all(*args: Any) -> None:
        held.append(_held())

    monkeypatch.setattr(model_module, "_background_run_all", _run_all)
    async with _client() as client:
        r = await client.post(f"/deals/{deal_id}/engines/run")
    assert r.status_code == 202, r.text
    assert held == [0], f"connections held during the Run Model background task: {held}"


async def test_every_route_gets_the_transaction_release() -> None:
    """The release is structural (an app-level dependency), not per handler."""
    from app.database import release_request_transaction
    from app.main import app

    assert any(d.dependency is release_request_transaction for d in app.router.dependencies)


async def test_memo_sse_stream_holds_no_connection_while_streaming(deal_id: str, monkeypatch) -> None:
    """``GET /memo/stream`` reads the deal (``assert_deal_active``), then
    streams for minutes — each open tab used to hold ACCESS SHARE on
    ``deals`` and a pooled connection for the whole stream. Probed from
    inside the stream body (httpx's ASGI transport buffers the response)."""
    from app.streaming import broadcast as broadcast_module

    held: list[int] = []

    async def _subscribe(*args: Any, **kwargs: Any):
        held.append(_held())
        return
        yield  # an (empty) async generator

    monkeypatch.setattr(broadcast_module, "subscribe_with_heartbeat", _subscribe)
    async with _client() as client:
        r = await client.get(f"/deals/{deal_id}/memo/stream")
    assert r.status_code == 200
    assert held == [0], f"connections held while the SSE body streams: {held}"


# ───────────── in-request LLM calls (review I2) ─────────────


async def test_due_diligence_generate_holds_no_connection_across_the_llm(
    deal_id: str, monkeypatch
) -> None:
    from app.agents import due_diligence as dd_agent
    from app.agents.due_diligence import DueDiligenceOutput

    held: list[int] = []

    async def _run(payload: Any) -> DueDiligenceOutput:
        held.append(_held())
        return DueDiligenceOutput(deal_id=deal_id, questions=[])

    monkeypatch.setattr(dd_agent, "run_due_diligence", _run)
    async with _client() as client:
        r = await client.post(f"/deals/{deal_id}/due-diligence/generate")
    assert r.status_code == 200, r.text
    assert held == [0], f"connections held during the due-diligence LLM call: {held}"


async def test_ask_holds_no_connection_across_the_researcher_llm(deal_id: str, monkeypatch) -> None:
    from types import SimpleNamespace

    from app.agents import researcher as researcher_agent
    from app.agents.researcher import ResearcherOutput
    from app.api import dossier as dossier_api

    held: list[int] = []
    real_build = dossier_api.build_dossier

    async def _build(*args: Any, **kwargs: Any):
        await real_build(*args, **kwargs)  # the real reads, on the request session
        return SimpleNamespace(confidence=SimpleNamespace(docs_extracted=1))

    async def _run(payload: Any) -> ResearcherOutput:
        held.append(_held())
        return ResearcherOutput(deal_id=deal_id, answer="a")

    monkeypatch.setattr(dossier_api, "build_dossier", _build)
    monkeypatch.setattr(researcher_agent, "ResearcherInput", lambda **kw: kw)
    monkeypatch.setattr(researcher_agent, "run_researcher", _run)
    async with _client() as client:
        r = await client.post(f"/deals/{deal_id}/ask", json={"question": "NOI?"})
    assert r.status_code == 200, r.text
    assert held == [0], f"connections held during the researcher LLM call: {held}"


async def test_memo_batch_submit_holds_no_connection_across_the_anthropic_call(
    deal_id: str, monkeypatch
) -> None:
    from app.agents import analyst_batch
    from app.agents.analyst import AnalystInput
    from app.api import deals as deals_module
    from app.config import get_settings

    held: list[int] = []

    async def _payload(*args: Any, **kwargs: Any):
        session = kwargs.get("session") or args[0]
        await session.execute(text("SELECT id FROM documents WHERE tenant_id = :t"), {"t": _TENANT})
        return AnalystInput(tenant_id=_TENANT, deal_id=deal_id, deal_data={"id": deal_id})

    async def _submit(payload: Any, **kwargs: Any):
        held.append(_held())
        return None  # falls back to streaming

    async def _analyst(payload: Any) -> None:
        return None

    monkeypatch.setenv("ANALYST_BATCH_API_ENABLED", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(deals_module, "_load_deal_payload", _payload)
    monkeypatch.setattr(analyst_batch, "run_analyst_batch", _submit)
    monkeypatch.setattr(deals_module, "_safe_run_analyst_streaming", _analyst)
    try:
        async with _client() as client:
            r = await client.post(f"/deals/{deal_id}/memo/generate")
    finally:
        get_settings.cache_clear()
    assert r.status_code == 200, r.text
    assert held == [0], f"connections held during the batch submit: {held}"
