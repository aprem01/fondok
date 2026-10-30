"""``GET /admin/health`` — ``llm_provider`` + the two last-hour failure counters.

Go-live audit D.4 (2026-10-30). A dead Anthropic key shows up as every
extraction failing; an engine regression shows up as failed engine rows.
Both are now one read away for an internal admin:

* ``llm_provider: {last_error, last_error_at}`` — module state written by
  ``app.llm.record_provider_error``; both null until a provider refusal.
* ``extraction_failures_last_hour`` — ``documents`` rows in FAILED /
  PARSE_FAILED whose status changed in the last hour (``status_changed_at``,
  else ``uploaded_at``).
* ``engine_failures_last_hour`` — ``engine_outputs`` rows that failed in the
  last hour, excluding the ``skipped: upstream …`` cascade rows so one root
  failure counts once per engine that actually raised.

Runs on SQLite here; the SQL binds no dialect-specific timestamp arithmetic
(a date-prefix prefilter, then exact comparison in Python) so the same path
serves Postgres.
"""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-admin-health-failures.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app import llm as llm_mod  # noqa: E402
from app.api import admin_health as admin_health_mod  # noqa: E402
from app.database import get_session_factory  # noqa: E402
from app.migrations import run_startup_migrations  # noqa: E402
from app.tenants import upsert_tenant  # noqa: E402


def _ts(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture(autouse=True)
def _reset_provider_state():
    llm_mod.reset_provider_state()
    yield
    llm_mod.reset_provider_state()


@pytest.fixture
async def seeded():
    await run_startup_migrations()
    internal = uuid4()
    other = uuid4()
    await upsert_tenant(
        tenant_id=internal, clerk_org_id=f"org_int_{internal.hex[:8]}",
        name="Fondok Internal (test)", kind="internal", status="active",
    )
    factory = get_session_factory()
    async with factory() as s:
        # The SQLite file persists across this module's tests: start clean.
        for tbl in ("engine_outputs", "documents", "deals"):
            await s.execute(text(f"DELETE FROM {tbl} WHERE tenant_id IS NOT NULL"))
        await s.commit()
        for tenant in (internal, other):
            deal = str(uuid4())
            await s.execute(
                text("INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                     "VALUES (:id, :tenant, 'Hotel', 'Draft', :ts, :ts)"),
                {"id": deal, "tenant": str(tenant), "ts": _ts(500)},
            )
            # documents: 2 recent failures (one FAILED, one PARSE_FAILED), one
            # old failure, one recent success, one failure whose
            # status_changed_at is NULL but uploaded recently (counts).
            rows = [
                ("FAILED", _ts(10), _ts(120)),
                ("PARSE_FAILED", _ts(50), _ts(120)),
                ("FAILED", _ts(90), _ts(120)),           # outside the hour
                ("EXTRACTED", _ts(5), _ts(120)),         # not a failure
                ("FAILED", None, _ts(20)),               # falls back to uploaded_at
            ]
            for status, changed, uploaded in rows:
                await s.execute(
                    text("INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, "
                         "uploaded_at, status_changed_at) VALUES (:id, :deal, :tenant, 'x.pdf', 'T12', "
                         ":status, :up, :sca)"),
                    {"id": str(uuid4()), "deal": deal, "tenant": str(tenant),
                     "status": status, "up": uploaded, "sca": changed},
                )
            # engine_outputs: one real failure in the hour, one cascade row
            # (skipped) in the hour, one old failure, one success.
            eng = [
                ("failed", "ZeroDivisionError: division by zero", _ts(15)),
                ("failed", "skipped: upstream revenue did not complete", _ts(15)),
                ("failed", "boom", _ts(200)),
                ("complete", None, _ts(3)),
            ]
            for status, error, completed in eng:
                await s.execute(
                    text("INSERT INTO engine_outputs (id, deal_id, tenant_id, run_id, engine_name, status, "
                         "inputs, outputs, error, started_at, completed_at, runtime_ms) VALUES "
                         "(:id, :deal, :tenant, :run, 'revenue', :status, '{}', '{}', :err, :st, :ct, 5)"),
                    {"id": str(uuid4()), "deal": deal, "tenant": str(tenant), "run": str(uuid4()),
                     "status": status, "err": error, "st": completed, "ct": completed},
                )
        await s.commit()
    return internal


@pytest.mark.asyncio
async def test_failure_counters_count_across_tenants_in_the_last_hour(seeded) -> None:
    factory = get_session_factory()
    async with factory() as s:
        counts = await admin_health_mod.failure_counts_last_hour(s)
    # 3 per tenant x 2 tenants (the SQLite / RLS-off path is one cross-tenant scan).
    assert counts["extraction_failures_last_hour"] == 6
    assert counts["engine_failures_last_hour"] == 2
    assert counts["failures_last_hour_scan"] == {"window_hours": 1, "source": "cross_tenant", "tenants": None}


@pytest.mark.asyncio
async def test_admin_health_carries_the_new_blocks(seeded) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/admin/health", headers={"X-Tenant-Id": str(seeded)})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["llm_provider"] == {"last_error": None, "last_error_at": None}
        assert body["extraction_failures_last_hour"] == 6
        assert body["engine_failures_last_hour"] == 2

        # A provider refusal is reflected on the next read — type + time, no message.
        import anthropic
        import httpx

        req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        exc = anthropic.AuthenticationError(
            "invalid x-api-key: sk-ant-SECRET", response=httpx.Response(401, request=req), body=None
        )
        from unittest.mock import patch

        with patch("app.alerting.report_alert"):
            llm_mod.record_provider_error(exc, now=datetime(2026, 10, 30, 9, 0, tzinfo=UTC))
        r2 = await client.get("/admin/health", headers={"X-Tenant-Id": str(seeded)})
        prov = r2.json()["llm_provider"]
        assert prov["last_error"] == "authentication (AuthenticationError 401)"
        assert prov["last_error_at"] == "2026-10-30T09:00:00+00:00"
        assert "SECRET" not in r2.text

        # The public probe stays free of all of it.
        pub = (await client.get("/health")).json()
        for key in ("llm_provider", "extraction_failures_last_hour", "engine_failures_last_hour"):
            assert key not in pub, key
