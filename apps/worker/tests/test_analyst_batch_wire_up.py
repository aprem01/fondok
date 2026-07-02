"""Wire-up tests for the analyst Message Batches lane — Task V (2026-07).

Complements ``test_analyst_batch_api.py`` (which covers submit / poll /
ingest at the module level) with end-to-end tests that exercise the
actual FastAPI routes:

* ``POST /deals/{id}/memo/generate`` with the flag ON routes through
  ``run_analyst_batch`` and returns ``202 Accepted`` with the batch id.
* ``POST /deals/{id}/memo/generate`` with the flag OFF stays on the
  sync streaming path (regression guard — flag defaults to False so no
  existing tenant is affected).
* ``GET /deals/{id}/memo/status`` reports the correct shape for every
  pending_batches state (pending / in_progress / complete / failed /
  expired / not_queued).

All tests are hermetic: no Anthropic HTTP calls, no analyst LLM calls.
The batch client is stubbed with a per-test fake and the sync path is
patched to a no-op so BackgroundTasks doesn't try to hit Claude.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

# Per-test SQLite DB — must be set BEFORE any ``app.*`` module import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-batch-wire-up.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_TENANT = "00000000-0000-0000-0000-000000000001"
_TENANT_HEADERS = {"X-Tenant-Id": _TENANT}


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db(monkeypatch: pytest.MonkeyPatch) -> Any:
    from app.config import get_settings
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.streaming import reset_broadcast_for_test, reset_memo_cache_for_test

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "pending_batches",
            "memo_edits",
            "audit_log",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    reset_broadcast_for_test()
    reset_memo_cache_for_test()

    # Default flag state = off; each test flips it explicitly.
    get_settings.cache_clear()  # type: ignore[attr-defined]
    settings = get_settings()
    monkeypatch.setattr(
        settings, "ANALYST_BATCH_API_ENABLED", False, raising=False
    )
    yield
    reset_broadcast_for_test()
    reset_memo_cache_for_test()


async def _insert_deal(deal_id: UUID) -> None:
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
                "tenant": _TENANT,
                "name": "batch wire-up deal",
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_extracted_document(deal_id: UUID) -> None:
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
                "tenant": _TENANT,
                "ts": datetime.now(UTC),
            },
        )
        await session.commit()


async def _insert_pending_batch(
    *,
    deal_id: str,
    batch_id: str,
    status_: str,
    error: str | None = None,
) -> None:
    from app.database import get_session_factory

    factory = get_session_factory()
    completed_at = (
        datetime.now(UTC).isoformat()
        if status_ in ("complete", "failed", "expired")
        else None
    )
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO pending_batches (
                    id, batch_id, deal_id, tenant_id, agent_name,
                    status, submitted_at, completed_at, error
                ) VALUES (
                    :id, :batch_id, :deal_id, :tenant_id, 'analyst',
                    :status, :submitted_at, :completed_at, :error
                )
                """
            ),
            {
                "id": str(uuid4()),
                "batch_id": batch_id,
                "deal_id": deal_id,
                "tenant_id": _TENANT,
                "status": status_,
                "submitted_at": datetime.now(UTC).isoformat(),
                "completed_at": completed_at,
                "error": error,
            },
        )
        await session.commit()


def _patch_sync_path_to_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make BackgroundTasks a no-op so the sync path can't fire Claude
    even if it accidentally gets picked. The batch tests still need to
    verify that certain flag states DO fall through to sync — we assert
    the response shape, not the follow-up analyst call.
    """
    import fastapi as fastapi_module

    def noop(self: Any, func: Any, *args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(fastapi_module.BackgroundTasks, "add_task", noop)


class _StubBatchClient:
    """Fake ``BatchClient`` returning a canned submit response."""

    def __init__(self, batch_id: str = "batch_wire_1") -> None:
        self.batch_id = batch_id
        self.submit_calls: list[list[dict[str, Any]]] = []

    def submit(self, requests: list[dict[str, Any]]) -> Any:
        self.submit_calls.append(requests)
        return {"id": self.batch_id}

    def retrieve(self, batch_id: str) -> Any:  # pragma: no cover
        return {"processing_status": "in_progress"}

    def results(self, batch_id: str) -> Any:  # pragma: no cover
        return []


# ─────────────────── 1. POST /memo/generate with flag ON ───────────────────


@pytest.mark.asyncio
async def test_memo_generate_routes_to_batch_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag ON → endpoint returns 202 + queued + batch_id, pending row created.

    Guards the wire-up contract: when Sam flips
    ``ANALYST_BATCH_API_ENABLED=true`` in Railway env, every subsequent
    ``POST /memo/generate`` MUST hand the draft to the batch API instead
    of firing the streaming analyst. Regression here means we silently
    keep paying the 100%-rate bill after the operator thought they'd
    turned on the discount.
    """
    from httpx import ASGITransport, AsyncClient

    from app.agents import analyst_batch as analyst_batch_mod
    from app.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        settings, "ANALYST_BATCH_API_ENABLED", True, raising=False
    )

    stub = _StubBatchClient(batch_id="batch_wire_ok")
    # Force ``run_analyst_batch`` to use our stub instead of constructing
    # a real BatchClient (which would need ANTHROPIC_API_KEY).
    real_run = analyst_batch_mod.run_analyst_batch

    async def wrapped(payload: Any, **kwargs: Any) -> Any:
        return await real_run(payload, client=stub, **kwargs)

    monkeypatch.setattr(analyst_batch_mod, "run_analyst_batch", wrapped)
    # ``deals`` imports ``run_analyst_batch`` inside the function body,
    # so the module-level patch above suffices.

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_extracted_document(deal_id)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["batch_id"] == "batch_wire_ok"
    assert body["deal_id"] == str(deal_id)

    # Batch API was actually hit once.
    assert len(stub.submit_calls) == 1

    # A ``pending_batches`` row landed for this deal.
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT batch_id, status FROM pending_batches "
                    "WHERE deal_id = :d"
                ),
                {"d": str(deal_id)},
            )
        ).first()
    assert row is not None
    assert row._mapping["batch_id"] == "batch_wire_ok"
    assert row._mapping["status"] == "queued"


@pytest.mark.asyncio
async def test_memo_generate_stays_on_sync_path_when_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flag OFF (default) → the batch client is never touched.

    The sync path returns ``200 {"status": "started", ...}`` so the
    existing frontend contract is unchanged for the default deploy.
    """
    from httpx import ASGITransport, AsyncClient

    from app.agents import analyst_batch as analyst_batch_mod

    _patch_sync_path_to_noop(monkeypatch)

    called: dict[str, int] = {"count": 0}

    async def should_not_be_called(*args: Any, **kwargs: Any) -> Any:
        called["count"] += 1
        return None

    monkeypatch.setattr(
        analyst_batch_mod, "run_analyst_batch", should_not_be_called
    )

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_extracted_document(deal_id)

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.post(f"/deals/{deal_id}/memo/generate")

    assert r.status_code == 200, r.text
    assert r.json() == {"status": "started", "deal_id": str(deal_id)}
    assert called["count"] == 0

    # And no pending_batches row was inserted.
    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                text("SELECT COUNT(*) FROM pending_batches")
            )
        ).first()
    assert rows is not None and rows[0] == 0


# ─────────────────── 2. GET /memo/status shape per state ───────────────────


@pytest.mark.asyncio
async def test_memo_status_reports_not_queued_when_no_row() -> None:
    """No pending row + empty memo cache → ``status='not_queued'``."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "not_queued"
    assert body["batch_id"] is None
    assert body["memo"] is None


@pytest.mark.asyncio
async def test_memo_status_reports_pending_when_row_queued() -> None:
    """A ``queued`` row → ``status='pending'`` + batch_id populated."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_pending_batch(
        deal_id=str(deal_id),
        batch_id="batch_pending_wire",
        status_="queued",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending"
    assert body["batch_id"] == "batch_pending_wire"
    assert body["memo"] is None


@pytest.mark.asyncio
async def test_memo_status_reports_in_progress_when_row_in_progress() -> None:
    """An ``in_progress`` row → ``status='in_progress'``."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_pending_batch(
        deal_id=str(deal_id),
        batch_id="batch_running_wire",
        status_="in_progress",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo/status")

    body = r.json()
    assert body["status"] == "in_progress"
    assert body["batch_id"] == "batch_running_wire"


@pytest.mark.asyncio
async def test_memo_status_reports_failed_with_error() -> None:
    """A ``failed`` row surfaces the DB ``error`` column verbatim."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_pending_batch(
        deal_id=str(deal_id),
        batch_id="batch_failed_wire",
        status_="failed",
        error="tool_use payload rejected by schema validator",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo/status")

    body = r.json()
    assert body["status"] == "failed"
    assert body["batch_id"] == "batch_failed_wire"
    assert "schema validator" in (body["error"] or "")


@pytest.mark.asyncio
async def test_memo_status_reports_expired() -> None:
    """An ``expired`` row surfaces the expiry error."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_pending_batch(
        deal_id=str(deal_id),
        batch_id="batch_expired_wire",
        status_="expired",
        error="batch expired without completion",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo/status")

    body = r.json()
    assert body["status"] == "expired"
    assert body["batch_id"] == "batch_expired_wire"
    assert "expired" in (body["error"] or "").lower()


@pytest.mark.asyncio
async def test_memo_status_returns_memo_when_complete() -> None:
    """A ``complete`` row + populated memo cache → memo body included.

    Simulates the poller having successfully drained a batch: it
    inserted a ``pending_batches`` row (status=complete) and wrote the
    drafted memo into the cache. The status endpoint must return the
    memo payload so the frontend can render it without a second call.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.streaming.broadcast import get_memo_cache

    deal_id = uuid4()
    await _insert_deal(deal_id)
    await _insert_pending_batch(
        deal_id=str(deal_id),
        batch_id="batch_done_wire",
        status_="complete",
    )
    memo_cache = get_memo_cache()
    fake_section = {
        "section_id": "recommendation",
        "title": "Recommendation",
        "body": "Proceed to underwrite at $189 ADR / 68% occupancy.",
        "citations": [],
    }
    await memo_cache.record_section(str(deal_id), fake_section)
    await memo_cache.mark_done(
        str(deal_id), generated_at=datetime.now(UTC).isoformat()
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_TENANT_HEADERS,
    ) as client:
        r = await client.get(f"/deals/{deal_id}/memo/status")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "complete"
    assert body["batch_id"] == "batch_done_wire"
    assert body["memo"] is not None
    assert body["memo"]["status"] == "done"
    section_ids = [s["section_id"] for s in body["memo"]["sections"]]
    assert "recommendation" in section_ids
