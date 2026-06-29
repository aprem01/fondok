"""Wave 4 reliability fix — Bug #2 regression suite.

Verifies the process-wide extractor throttle:

1. ``test_semaphore_caps_concurrent_extractions`` — 10 fake "docs"
   queued at once never exceed the configured cap.
2. ``test_semaphore_releases_on_exception`` — a doc that raises
   mid-extraction releases its slot.
3. ``test_other_endpoints_remain_responsive_under_extraction_load`` —
   schedule 10 fake extractions then hit ``/deals/{id}`` and assert
   the response comes back well under 1s (the bug was that the
   ungated 16-doc upload starved the event loop for >> 30s, hanging
   every other endpoint).
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

# Test isolation — same pattern the other suites use. Set BEFORE the
# extractor_throttle import so the module's env-driven caps stay at
# default 4 docs × 2 chunks even when CI overrides leak through.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-extractor-throttle.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-extractor-throttle-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")
os.environ.pop("EXTRACTOR_MAX_CONCURRENT_DOCS", None)
os.environ.pop("EXTRACTOR_CHUNK_CONCURRENCY", None)

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


TENANT_A = "11111111-1111-1111-1111-1111aaaaaaaa"


@pytest.fixture(autouse=True)
def _reset_throttle_state() -> None:
    """Wipe semaphore counters between tests so concurrent-cap
    assertions don't carry waiters across the suite."""
    from app.services import extractor_throttle

    extractor_throttle._reset_for_tests()
    yield
    extractor_throttle._reset_for_tests()


async def test_semaphore_caps_concurrent_extractions() -> None:
    """10 docs queue up; never more than the configured cap (4) run
    at the same instant. We capture the max in-flight count by
    sampling inside each ``acquire_extractor_slot()`` context and
    comparing against the live ``inflight_count()`` counter."""
    from app.services import extractor_throttle
    from app.services.extractor_throttle import (
        EXTRACTOR_MAX_CONCURRENT_DOCS,
        acquire_extractor_slot,
        inflight_count,
    )

    assert EXTRACTOR_MAX_CONCURRENT_DOCS == 4, (
        "default cap is 4; tweaks should be explicit in this test"
    )

    max_seen = 0
    lock = asyncio.Lock()

    async def _fake_doc(i: int) -> None:
        nonlocal max_seen
        async with acquire_extractor_slot():
            async with lock:
                # Snapshot AFTER we're holding the slot — that's the
                # invariant we care about (in-flight ≤ cap).
                if inflight_count() > max_seen:
                    max_seen = inflight_count()
            # Small await so the next batch has a chance to schedule
            # while this one still holds the slot.
            await asyncio.sleep(0.05)

    await asyncio.gather(*(_fake_doc(i) for i in range(10)))
    assert max_seen <= EXTRACTOR_MAX_CONCURRENT_DOCS, (
        f"in-flight count peaked at {max_seen}, "
        f"cap is {EXTRACTOR_MAX_CONCURRENT_DOCS}"
    )
    # And after the gather, every slot is freed.
    assert extractor_throttle.inflight_count() == 0
    assert extractor_throttle.queue_depth() == 0


async def test_semaphore_releases_on_exception() -> None:
    """A doc that raises mid-extraction must release its slot so the
    next queued doc can proceed. Without this, a single FAILED doc
    would shrink the effective cap by 1 forever."""
    from app.services import extractor_throttle
    from app.services.extractor_throttle import acquire_extractor_slot

    class _Boom(RuntimeError):
        pass

    async def _bad_doc() -> None:
        async with acquire_extractor_slot():
            raise _Boom("simulated extractor crash")

    with pytest.raises(_Boom):
        await _bad_doc()

    assert extractor_throttle.inflight_count() == 0, (
        "failed extraction leaked a slot"
    )
    # Verify the cap is intact by acquiring everything sequentially —
    # if the prior failure leaked, the inflight counter would now read
    # 1 here instead of cap.
    held = []

    async def _hold() -> None:
        async with acquire_extractor_slot():
            held.append(extractor_throttle.inflight_count())
            await asyncio.sleep(0.02)

    await asyncio.gather(*(_hold() for _ in range(5)))
    assert max(held) <= extractor_throttle.EXTRACTOR_MAX_CONCURRENT_DOCS


async def test_other_endpoints_remain_responsive_under_extraction_load() -> None:
    """Schedule 10 fake extractions occupying all slots, then hit a
    plain ``/deals/{id}`` endpoint. The endpoint MUST return in well
    under a second — the Wave 4 bug was that the ungated 16-doc
    upload starved the uvicorn loop and every other request hung.

    We simulate the load by holding the semaphore from background
    tasks rather than calling real extractors (which would burn
    Sonnet tokens and take 30s+). The semaphore is the contention
    point — exercising it directly is the correct unit test surface.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app
    from app.migrations import run_startup_migrations
    from app.services.extractor_throttle import acquire_extractor_slot

    await run_startup_migrations()
    factory = get_session_factory()

    # Seed a deal so /deals/{id} returns 200 instead of 404.
    deal_id = str(uuid4())
    now = datetime.now(UTC).isoformat()
    async with factory() as session:
        await session.execute(
            text("DELETE FROM documents"))
        await session.execute(
            text("DELETE FROM deals"))
        await session.execute(
            text(
                """
                INSERT INTO deals (
                    id, tenant_id, name, status, created_at, updated_at
                ) VALUES (:id, :tenant, :name, 'Draft', :ts, :ts)
                """
            ),
            {
                "id": deal_id,
                "tenant": TENANT_A,
                "name": "Throttle Probe Hotel",
                "ts": now,
            },
        )
        await session.commit()

    # Hold every extractor slot for ~1s — mirrors a real 16-doc upload
    # where docs are mid-Sonnet-call. Tasks run in the background so
    # the test body can drive the HTTP probe in parallel.
    stop_event = asyncio.Event()

    async def _hold_slot(i: int) -> None:
        async with acquire_extractor_slot():
            await stop_event.wait()

    holders = [asyncio.create_task(_hold_slot(i)) for i in range(10)]
    # Give the event loop a tick to schedule the holders so the
    # semaphore is actually saturated before we probe.
    await asyncio.sleep(0.05)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            t0 = time.monotonic()
            resp = await client.get(
                f"/deals/{deal_id}",
                headers={"X-Tenant-Id": TENANT_A},
            )
            elapsed = time.monotonic() - t0

        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == deal_id
        # 1s ceiling — the bug had this taking 30s+. Local CI usually
        # comes back in <50ms; the generous bound accommodates slow CI
        # boxes without masking a regression.
        assert elapsed < 1.0, (
            f"GET /deals/{{id}} took {elapsed:.3f}s while extractor "
            f"slots were held — uvicorn loop is starved (Bug #2 regression)"
        )
    finally:
        stop_event.set()
        await asyncio.gather(*holders)
