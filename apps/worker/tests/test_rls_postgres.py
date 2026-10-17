"""FON-93 — row-level security against a REAL Postgres, as ``fondok_app``.

Opt-in: set ``FONDOK_TEST_POSTGRES_URL`` to the OWNER DSN of a THROWAWAY
database (``postgresql://owner:pw@host:port/db``). Skipped cleanly when
unset. ``docs/TESTING.md`` → "Postgres RLS integration test" has the
docker one-liner.

What it does to that database (so: throwaway only):

1. ``run_startup_migrations()`` with ``DATABASE_URL_MIGRATIONS`` = the owner
   DSN and ``DATABASE_URL`` = the same database as ``fondok_app`` — the
   exact production shape after §7 of ``docs/WORKSPACE_ISOLATION.md``;
2. applies ``migrations/rls/001_enable_rls.sql`` as the owner (creates the
   ``fondok_app`` role, grants DML, enables + forces RLS, the policies) and
   sets the role's password to ``fondok_app_test``;
3. TRUNCATEs the tenant-scoped tables it seeds, then seeds two tenants' rows
   (deal, document stuck EXTRACTING, extraction_results, engine_outputs, a
   due digest schedule, an open batch) and registers both tenants;
4. connects as ``fondok_app`` through the worker's own session factory with
   ``DB_SET_TENANT_GUC=1`` and proves: no GUC → 0 rows; GUC → only that
   tenant's rows across ``deals`` / ``documents`` / ``extraction_results`` /
   ``engine_outputs``; a blank tenant never raises; a write for another
   tenant is refused; and the digest scheduler, the batch poller and the
   extraction watchdog each find BOTH tenants' rows by iterating tenants.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.engine import make_url

OWNER_URL = os.environ.get("FONDOK_TEST_POSTGRES_URL", "").strip()

# One event loop for the whole module: the engines the module fixture builds
# must be driven by the loop the tests run on (asyncpg futures are
# loop-bound — pytest-asyncio 1.x would otherwise give each test its own).
pytestmark = [
    pytest.mark.skipif(
        not OWNER_URL,
        reason="FONDOK_TEST_POSTGRES_URL not set — Postgres RLS integration test skipped",
    ),
    pytest.mark.asyncio(loop_scope="module"),
]

APP_ROLE = "fondok_app"
APP_PASSWORD = "fondok_app_test"
TENANT_A = "aaaaaaaa-0000-4000-8000-00000000000a"
TENANT_B = "bbbbbbbb-0000-4000-8000-00000000000b"
TABLES = ("deals", "documents", "extraction_results", "engine_outputs")

_RLS_SQL = Path(__file__).resolve().parents[1] / "migrations" / "rls" / "001_enable_rls.sql"


def _plain(url: str) -> str:
    """asyncpg wants ``postgresql://``, not the SQLAlchemy driver form."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _app_url(owner: str) -> str:
    u = make_url(_plain(owner))
    return u.set(username=APP_ROLE, password=APP_PASSWORD).render_as_string(hide_password=False)


async def _owner_exec(sql: str, *args: Any) -> None:
    import asyncpg

    conn = await asyncpg.connect(_plain(OWNER_URL))
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _owner_fetch(sql: str, *args: Any) -> list[Any]:
    import asyncpg

    conn = await asyncpg.connect(_plain(OWNER_URL))
    try:
        return list(await conn.fetch(sql, *args))
    finally:
        await conn.close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def rls_db():
    """Owner applies schema + RLS and seeds; the worker's engine/factory are
    re-pointed at the same database as ``fondok_app`` for the module and
    restored afterwards."""
    import app.database as database
    from app.config import get_settings
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache, upsert_tenant

    mp = pytest.MonkeyPatch()
    saved = (database._engine, database._session_factory, database._migrations_engine)
    database._engine = database._session_factory = database._migrations_engine = None
    mp.setenv("DATABASE_URL", _app_url(OWNER_URL))
    mp.setenv("DATABASE_URL_MIGRATIONS", _plain(OWNER_URL))
    mp.setenv("DB_SET_TENANT_GUC", "1")
    mp.setenv("REQUIRE_REGISTERED_TENANT", "1")
    mp.setenv("EXTRACTION_STUCK_MINUTES", "30")
    mp.setenv("DIGEST_SCHEDULER_ENABLED", "False")
    mp.setenv("EXTRACTION_WATCHDOG_ENABLED", "False")
    get_settings.cache_clear()
    reset_tenant_cache()
    try:
        assert get_settings().migrations_dsn_kind == "owner"
        assert database._tenant_guc_is_active() is True

        # 1. DDL as the owner, through the dedicated engine.
        await run_startup_migrations()
        assert database._migrations_engine is not None
        assert database._migrations_engine is not database.get_engine()
        await database.dispose_migrations_engine()  # exactly what lifespan does
        assert database._migrations_engine is None

        # 2. RLS + the app role (idempotent; re-running upgrades the policy).
        await _owner_exec(_RLS_SQL.read_text())
        await _owner_exec(f"ALTER ROLE {APP_ROLE} PASSWORD '{APP_PASSWORD}'")

        # 3. A clean slate for the seeded tables, then two tenants' rows.
        await _owner_exec(
            "TRUNCATE deals, documents, extraction_results, engine_outputs, "
            "pipeline_digest_schedules, pending_batches, audit_log, tenants CASCADE"
        )
        now = datetime.now(UTC)
        ids: dict[str, dict[str, str]] = {}
        for t in (TENANT_A, TENANT_B):
            deal, doc, extraction, output, sched, batch = (str(uuid4()) for _ in range(6))
            ids[t] = {
                "deal": deal,
                "doc": doc,
                "extraction": extraction,
                "output": output,
                "schedule": sched,
                "batch": batch,
            }
            await _owner_exec(
                "INSERT INTO deals (id, tenant_id, name, status) VALUES ($1, $2, 'RLS Hotel', 'Draft')",
                UUID(deal),
                UUID(t),
            )
            await _owner_exec(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, "
                "uploaded_at, status_changed_at, storage_key, parser) VALUES "
                "($1, $2, $3, 'x.xlsx', 'T12', 'EXTRACTING', $4, $5, $6, 'test-fixture')",
                UUID(doc),
                UUID(deal),
                UUID(t),
                now - timedelta(hours=3),
                now - timedelta(hours=2),
                f"raw/{doc}",
            )
            await _owner_exec(
                "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields) "
                "VALUES ($1, $2, $3, $4, '{}'::jsonb)",
                UUID(extraction),
                UUID(doc),
                UUID(deal),
                UUID(t),
            )
            await _owner_exec(
                "INSERT INTO engine_outputs (id, deal_id, tenant_id, run_id, engine_name, status, "
                "outputs, started_at, completed_at) VALUES "
                "($1, $2, $3, $4, 'returns', 'complete', '{}'::jsonb, $5, $5)",
                UUID(output),
                UUID(deal),
                UUID(t),
                uuid4(),
                now,
            )
            await _owner_exec(
                "INSERT INTO pipeline_digest_schedules (id, tenant_id, name, cadence, hour_utc, "
                "delivery, slack_webhook_url, is_active, next_run_at) VALUES "
                "($1, $2, 'Overdue', 'daily', 13, 'slack', "
                "'https://hooks.slack.com/services/T0/B0/x', TRUE, $3)",
                UUID(sched),
                UUID(t),
                now - timedelta(minutes=5),
            )
            await _owner_exec(
                "INSERT INTO pending_batches (id, batch_id, deal_id, tenant_id, agent_name, status, "
                "submitted_at) VALUES ($1, $2, $3, $4, 'analyst', 'queued', $5)",
                UUID(batch),
                f"msgbatch_{batch}",
                UUID(deal),
                UUID(t),
                now - timedelta(minutes=10),
            )
        # The registry is read by the auth layer / the scans before a tenant
        # is known, so it is not RLS-protected — written here as fondok_app.
        for i, t in enumerate((TENANT_A, TENANT_B)):
            await upsert_tenant(
                tenant_id=UUID(t),
                clerk_org_id=f"org_rls_{i}",
                name=f"Firm {i}",
                kind="external",
                status="active",
            )
        yield {"ids": ids}
    finally:
        await database.dispose_engine()
        database._engine, database._session_factory, database._migrations_engine = saved
        mp.undo()
        get_settings.cache_clear()
        reset_tenant_cache()


async def _count(session, table: str) -> int:
    return int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id IN (:a, :b)"),
                {"a": TENANT_A, "b": TENANT_B},
            )
        ).scalar_one()
    )


async def test_worker_connects_as_the_app_role_without_bypass(rls_db) -> None:
    from app.database import get_session_factory

    async with get_session_factory()() as session:
        who = (await session.execute(text("SELECT current_user"))).scalar_one()
        bypass = (
            await session.execute(
                text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = current_user")
            )
        ).scalar_one()
        forced = (
            await session.execute(
                text(
                    "SELECT bool_and(relrowsecurity AND relforcerowsecurity) FROM pg_class "
                    "WHERE relname IN ('deals', 'documents', 'extraction_results', 'engine_outputs')"
                )
            )
        ).scalar_one()
    assert who == APP_ROLE
    assert bypass is False
    assert forced is True


async def test_no_guc_sees_zero_rows_and_never_raises(rls_db) -> None:
    from app.auth.context import current_tenant_id
    from app.database import get_session_factory

    for value in (None, ""):
        token = current_tenant_id.set(value)
        try:
            async with get_session_factory()() as session:
                for table in TABLES:
                    assert await _count(session, table) == 0, (value, table)
                # The placeholder may read '' after an earlier publish on this
                # pooled connection — the policy's NULLIF makes that 0 rows,
                # not an error.
                (await session.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar_one_or_none()
        finally:
            current_tenant_id.reset(token)


@pytest.mark.parametrize("tenant", [TENANT_A, TENANT_B])
async def test_guc_scopes_every_table_to_the_published_tenant(rls_db, tenant: str) -> None:
    from app.auth.context import current_tenant_id
    from app.database import get_session_factory

    ids = rls_db["ids"][tenant]
    token = current_tenant_id.set(tenant)
    try:
        async with get_session_factory()() as session:
            for table, key in zip(TABLES, ("deal", "doc", "extraction", "output"), strict=True):
                rows = (
                    await session.execute(
                        text(f"SELECT id, tenant_id FROM {table} WHERE tenant_id IN (:a, :b)"),
                        {"a": TENANT_A, "b": TENANT_B},
                    )
                ).fetchall()
                assert [(str(r[0]), str(r[1])) for r in rows] == [(ids[key], tenant)], table
            await session.commit()
            # A second transaction on the same session: the hook re-publishes.
            assert await _count(session, "deals") == 1
    finally:
        current_tenant_id.reset(token)


async def test_write_for_another_tenant_is_refused(rls_db) -> None:
    from sqlalchemy.exc import DBAPIError

    from app.auth.context import current_tenant_id
    from app.database import get_session_factory

    token = current_tenant_id.set(TENANT_A)
    try:
        async with get_session_factory()() as session:
            with pytest.raises(DBAPIError):
                await session.execute(
                    text(
                        "INSERT INTO deals (id, tenant_id, name, status) "
                        "VALUES (:id, :tenant, 'Smuggled', 'Draft')"
                    ),
                    {"id": str(uuid4()), "tenant": TENANT_B},
                )
            await session.rollback()
    finally:
        current_tenant_id.reset(token)
    rows = await _owner_fetch("SELECT count(*) FROM deals WHERE name = 'Smuggled'")
    assert rows[0][0] == 0


async def test_digest_scheduler_finds_both_tenants_under_rls(rls_db, monkeypatch) -> None:
    from app.auth.context import current_tenant_id
    from app.services import digest_scheduler

    plans: list[Any] = []
    real_plan = digest_scheduler.plan_tenant_scan

    async def _spy_plan(**kw):
        plan = await real_plan(**kw)
        plans.append(plan)
        return plan

    dispatched: list[tuple[str, str | None]] = []

    async def _payload(session, *, tenant_id, schedule, now):
        return {"tenant": str(tenant_id)}

    monkeypatch.setattr(digest_scheduler, "plan_tenant_scan", _spy_plan)
    monkeypatch.setattr(digest_scheduler, "build_digest_payload", _payload)
    monkeypatch.setattr(
        digest_scheduler,
        "dispatch_digest",
        lambda schedule, payload: dispatched.append((str(schedule["tenant_id"]), current_tenant_id.get())),
    )
    assert await digest_scheduler.tick_once() == 2
    assert plans and plans[0].source == "registry"
    assert sorted(plans[0].tenants) == [TENANT_A, TENANT_B]
    assert sorted(dispatched) == [(TENANT_A, TENANT_A), (TENANT_B, TENANT_B)]
    # ``_mark_dispatched`` ran as each tenant: both rows advanced.
    rows = await _owner_fetch(
        "SELECT tenant_id::text FROM pipeline_digest_schedules WHERE last_run_at IS NOT NULL"
    )
    assert sorted(r[0] for r in rows) == [TENANT_A, TENANT_B]


async def test_batch_poller_finds_both_tenants_under_rls(rls_db) -> None:
    from app.agents.analyst_batch import poll_pending_batches
    from app.auth.context import current_tenant_id

    class _Client:
        def __init__(self) -> None:
            self.retrieved: list[tuple[str, str | None]] = []

        def retrieve(self, batch_id: str) -> dict[str, str]:
            self.retrieved.append((batch_id, current_tenant_id.get()))
            return {"processing_status": "in_progress"}

    client = _Client()
    tally = await poll_pending_batches(client=client)
    assert tally["checked"] == 2 and tally["pending"] == 2
    expected = {
        (f"msgbatch_{rls_db['ids'][t]['batch']}", t) for t in (TENANT_A, TENANT_B)
    }
    assert set(client.retrieved) == expected
    rows = await _owner_fetch("SELECT tenant_id::text, status FROM pending_batches")
    assert sorted(rows) == sorted([(TENANT_A, "in_progress"), (TENANT_B, "in_progress")])


async def test_extraction_watchdog_times_out_both_tenants_under_rls(rls_db) -> None:
    from app.services import extraction_watchdog

    assert await extraction_watchdog.tick_once() == 2
    # ``error_kind`` lives inside ``extraction_data`` (api/documents.py
    # ``_mark_document_failed``), not in its own column.
    rows = await _owner_fetch(
        "SELECT tenant_id::text, status, extraction_data->>'error_kind' FROM documents"
    )
    assert sorted(tuple(r) for r in rows) == sorted(
        [(TENANT_A, "FAILED", "timeout"), (TENANT_B, "FAILED", "timeout")]
    )
    audits = await _owner_fetch(
        "SELECT tenant_id::text FROM audit_log WHERE action = $1",
        extraction_watchdog.AUDIT_ACTION,
    )
    assert sorted(r[0] for r in audits) == [TENANT_A, TENANT_B]


async def test_owner_distinct_discovery_when_enforcement_is_off(rls_db, monkeypatch) -> None:
    """Registry not consulted (REQUIRE_REGISTERED_TENANT off) → the tenants
    come from ``SELECT DISTINCT tenant_id`` through DATABASE_URL_MIGRATIONS."""
    from app.agents import analyst_batch
    from app.agents.analyst_batch import poll_pending_batches
    from app.config import get_settings
    from app.services import tenant_scan

    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "0")
    get_settings.cache_clear()
    plans: list[Any] = []
    real_plan = tenant_scan.plan_tenant_scan

    async def _spy_plan(**kw):
        plan = await real_plan(**kw)
        plans.append(plan)
        return plan

    monkeypatch.setattr(tenant_scan, "plan_tenant_scan", _spy_plan)

    class _Client:
        def retrieve(self, batch_id: str) -> dict[str, str]:
            return {"processing_status": "in_progress"}

    tally = await poll_pending_batches(client=_Client())
    assert tally["checked"] == 2
    assert plans and plans[0].source == "owner_distinct"
    assert plans[0].tenants == (TENANT_A, TENANT_B)
    assert analyst_batch is not None
