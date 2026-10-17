"""FON-93 — the worker as the non-owner ``fondok_app`` role (SQLite half).

What is provable without Postgres:

* ``DATABASE_URL_MIGRATIONS`` unset → startup DDL runs on THE serving
  engine (today's behaviour); set → a dedicated engine that
  ``run_startup_migrations`` uses, the session factory never binds to, and
  ``dispose_migrations_engine`` tears down. ``/health.db.migrations_dsn``
  reports ``same`` / ``owner`` (presence only).
* The GUC hook publishes nothing for ``None`` AND for a blank tenant, so a
  connection with no tenant never calls ``set_config`` with a value the
  policy's ``::uuid`` cast would reject.
* ``services.tenant_scan``: RLS off → one un-scoped scan (the fetch is
  called exactly as today); RLS on → the registry when enforcement is on
  and it has rows, else ``DISTINCT tenant_id`` through the owner DSN, else
  today's query — and each tenant is published BEFORE its fetch with a
  commit between tenants.
* The three background scans, with per-tenant mode forced on SQLite: rows of
  registered tenants are found, rows of an unregistered tenant are not; with
  it off every row is found (today).

``tests/test_rls_postgres.py`` runs the same scans against a real Postgres
with the policies applied, as ``fondok_app``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-rls-readiness.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
os.environ.setdefault("EVALS_MOCK", "true")
os.environ["DIGEST_SCHEDULER_ENABLED"] = "False"
os.environ["EXTRACTION_WATCHDOG_ENABLED"] = "False"
os.environ["EMAIL_BACKEND"] = "log_only"

from sqlalchemy import text  # noqa: E402

import app.database as database  # noqa: E402
from app.auth.context import current_tenant_id  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services import tenant_scan  # noqa: E402

TENANT_A = "aaaaaaaa-0000-0000-0000-00000000000a"
TENANT_B = "bbbbbbbb-0000-0000-0000-00000000000b"
TENANT_C = "cccccccc-0000-0000-0000-00000000000c"  # never registered

_OWNER_DB = Path(tempfile.gettempdir()) / "fondok-tests-rls-readiness-owner.db"


@pytest.fixture(autouse=True)
async def _clean(monkeypatch):
    """Enforcement off, no owner DSN, migrated serving DB with empty
    tenant-scoped tables and a cold registry cache; the dedicated engine
    is always disposed so nothing leaks into the next test."""
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache

    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "0")
    monkeypatch.setenv("DB_SET_TENANT_GUC", "0")
    monkeypatch.delenv("DATABASE_URL_MIGRATIONS", raising=False)
    get_settings.cache_clear()
    reset_tenant_cache()
    await run_startup_migrations()
    async with database.get_engine().begin() as conn:
        for table in (
            "tenants",
            "pipeline_digest_schedules",
            "pending_batches",
            "documents",
            "deals",
            "audit_log",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))
    try:
        yield
    finally:
        await database.dispose_migrations_engine()
        get_settings.cache_clear()
        reset_tenant_cache()


def _use_owner_sqlite(monkeypatch) -> str:
    """Point ``DATABASE_URL_MIGRATIONS`` at a second sqlite file."""
    if _OWNER_DB.exists():
        _OWNER_DB.unlink()
    url = f"sqlite+aiosqlite:///{_OWNER_DB}"
    monkeypatch.setenv("DATABASE_URL_MIGRATIONS", url)
    get_settings.cache_clear()
    return url


# ───────────────────────── migrations engine ─────────────────────────


def test_migrations_engine_is_the_serving_engine_when_unset(monkeypatch) -> None:
    assert get_settings().async_migrations_database_url is None
    assert get_settings().migrations_dsn_kind == "same"
    assert database.get_migrations_engine() is database.get_engine()
    assert database.migrations_engine_is_dedicated() is False
    assert database.migrations_dsn_kind() == "same"
    # A blank value is "unset" too (Railway leaves empty strings behind).
    monkeypatch.setenv("DATABASE_URL_MIGRATIONS", "   ")
    get_settings.cache_clear()
    assert get_settings().async_migrations_database_url is None
    assert database.get_migrations_engine() is database.get_engine()


def test_migrations_dsn_is_normalised_like_the_serving_dsn(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL_MIGRATIONS", "postgresql://owner:pw@db:5432/fondok")
    get_settings.cache_clear()
    s = get_settings()
    assert s.async_migrations_database_url == "postgresql+asyncpg://owner:pw@db:5432/fondok"
    assert s.sync_migrations_database_url == "postgresql://owner:pw@db:5432/fondok"
    assert s.migrations_dsn_kind == "owner"


async def test_dedicated_migrations_engine_runs_ddl_and_is_disposable(monkeypatch) -> None:
    from app.migrations import run_startup_migrations

    url = _use_owner_sqlite(monkeypatch)
    engine = database.get_migrations_engine()
    assert engine is not database.get_engine()
    assert str(engine.url) == url
    assert database.get_migrations_engine() is engine  # cached
    assert database.migrations_engine_is_dedicated() is True
    assert database.migrations_dsn_kind() == "owner"
    # The factory is bound to the SERVING engine only — the GUC hook (a
    # TenantSession listener) can never fire on the DDL engine.
    assert database.get_session_factory().kw.get("bind") is database.get_engine()

    await run_startup_migrations()
    async with engine.connect() as conn:
        tables = {
            r[0]
            for r in (
                await conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))
            ).fetchall()
        }
    assert {"deals", "documents", "tenants"} <= tables

    await database.dispose_migrations_engine()
    assert database._migrations_engine is None
    # Idempotent.
    await database.dispose_migrations_engine()


async def test_health_reports_migrations_dsn_presence_only(monkeypatch) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = (await c.get("/health")).json()
    assert body["db"] == {"status": "ok", "migrations_dsn": "same"}

    _use_owner_sqlite(monkeypatch)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        body = (await c.get("/health")).json()
    assert body["db"] == {"status": "ok", "migrations_dsn": "owner"}
    assert "sqlite" not in json.dumps(body["db"])  # never the URL


# ───────────────────────── GUC hook: None / blank ─────────────────────────


def test_publish_tenant_guc_skips_none_and_blank_tenants() -> None:
    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[Any, dict[str, Any]]] = []

        def execute(self, stmt, params):
            self.calls.append((stmt, params))

    conn = _Conn()
    for value in (None, "", "   "):
        token = current_tenant_id.set(value)
        try:
            database._publish_tenant_guc(None, None, conn)
        finally:
            current_tenant_id.reset(token)
    assert conn.calls == []
    token = current_tenant_id.set(f" {TENANT_A} ")
    try:
        database._publish_tenant_guc(None, None, conn)
    finally:
        current_tenant_id.reset(token)
    assert conn.calls == [(database.TENANT_GUC_SQL, {"tenant": TENANT_A})]


# ───────────────────────── tenant_scan plans ─────────────────────────


async def test_plan_is_cross_tenant_when_rls_is_off() -> None:
    assert tenant_scan.per_tenant_scans_active() is False  # sqlite, flag off
    plan = await tenant_scan.plan_tenant_scan(distinct_tenants_sql="SELECT 1", job="t")
    assert plan == tenant_scan.ScanPlan(None, "cross_tenant")
    assert plan.per_tenant is False


async def test_scan_by_tenant_calls_fetch_once_unscoped_when_cross_tenant() -> None:
    class _Session:
        commits = 0

        async def commit(self):
            self.commits += 1

    seen: list[tuple[str | None, str | None]] = []

    async def _fetch(session, tenant):
        seen.append((tenant, current_tenant_id.get()))
        return ["row"]

    session = _Session()
    rows = await tenant_scan.scan_by_tenant(session, tenant_scan.ScanPlan(None, "cross_tenant"), _fetch)
    assert rows == ["row"]
    assert seen == [(None, None)]
    assert session.commits == 0  # the caller ends the transaction, as today


async def test_scan_by_tenant_publishes_each_tenant_before_its_fetch() -> None:
    class _Session:
        commits = 0

        async def commit(self):
            self.commits += 1

    seen: list[tuple[str | None, str | None]] = []

    async def _fetch(session, tenant):
        seen.append((tenant, current_tenant_id.get()))
        return [f"{tenant}:1", f"{tenant}:2"]

    session = _Session()
    token = current_tenant_id.set(None)
    try:
        rows = await tenant_scan.scan_by_tenant(
            session, tenant_scan.ScanPlan((TENANT_A, TENANT_B), "registry"), _fetch
        )
    finally:
        current_tenant_id.reset(token)
    assert rows == [f"{TENANT_A}:1", f"{TENANT_A}:2", f"{TENANT_B}:1", f"{TENANT_B}:2"]
    assert seen == [(TENANT_A, TENANT_A), (TENANT_B, TENANT_B)]
    assert session.commits == 2  # one transaction per tenant


async def _register(*tenants: str) -> None:
    from app.tenants import upsert_tenant

    for i, t in enumerate(tenants):
        await upsert_tenant(
            tenant_id=UUID(t),
            clerk_org_id=f"org_{t[:8]}",
            name=f"Firm {i}",
            kind="external",
            status="active",
        )


async def test_plan_uses_the_registry_when_enforcement_is_on(monkeypatch) -> None:
    monkeypatch.setattr(tenant_scan, "per_tenant_scans_active", lambda: True)
    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "1")
    get_settings.cache_clear()
    await _register(TENANT_A, TENANT_B)
    plan = await tenant_scan.plan_tenant_scan(
        distinct_tenants_sql="SELECT DISTINCT tenant_id FROM pending_batches", job="t"
    )
    assert plan.source == "registry"
    assert plan.tenants == (TENANT_A, TENANT_B)


async def test_plan_falls_back_to_owner_distinct_when_registry_is_empty_or_enforcement_off(
    monkeypatch,
) -> None:
    from app.migrations import run_startup_migrations

    monkeypatch.setattr(tenant_scan, "per_tenant_scans_active", lambda: True)
    _use_owner_sqlite(monkeypatch)
    await run_startup_migrations()  # creates the owner file's schema
    async with database.get_migrations_engine().begin() as conn:
        for t in (TENANT_B, TENANT_A, TENANT_A):
            await conn.execute(
                text(
                    "INSERT INTO pending_batches (id, batch_id, deal_id, tenant_id, status) "
                    "VALUES (:id, :bid, :deal, :tenant, 'queued')"
                ),
                {"id": str(uuid4()), "bid": f"b-{uuid4()}", "deal": str(uuid4()), "tenant": t},
            )
    sql = "SELECT DISTINCT tenant_id FROM pending_batches WHERE status IN ('queued', 'in_progress')"

    # Enforcement off → the registry is not consulted even if seeded.
    await _register(TENANT_C)
    plan = await tenant_scan.plan_tenant_scan(distinct_tenants_sql=sql, job="t")
    assert plan.source == "owner_distinct"
    assert plan.tenants == (TENANT_A, TENANT_B)  # sorted, de-duplicated

    # Enforcement on but the registry empty → same fallback.
    async with database.get_engine().begin() as conn:
        await conn.execute(text("DELETE FROM tenants"))
    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "1")
    get_settings.cache_clear()
    plan = await tenant_scan.plan_tenant_scan(distinct_tenants_sql=sql, job="t")
    assert plan.source == "owner_distinct"
    assert plan.tenants == (TENANT_A, TENANT_B)

    # The discovery read never leaves an owner connection pooled behind.
    assert database._migrations_engine is not None  # the DDL engine, separate
    await database.dispose_migrations_engine()


async def test_plan_degrades_to_cross_tenant_without_owner_dsn_or_on_owner_failure(
    monkeypatch,
) -> None:
    monkeypatch.setattr(tenant_scan, "per_tenant_scans_active", lambda: True)
    plan = await tenant_scan.plan_tenant_scan(
        distinct_tenants_sql="SELECT DISTINCT tenant_id FROM pending_batches", job="t"
    )
    assert plan == tenant_scan.ScanPlan(None, "cross_tenant")

    _use_owner_sqlite(monkeypatch)  # a fresh file: the table does not exist there
    plan = await tenant_scan.plan_tenant_scan(
        distinct_tenants_sql="SELECT DISTINCT tenant_id FROM no_such_table", job="t"
    )
    assert plan == tenant_scan.ScanPlan(None, "cross_tenant")


# ───────────────────────── the three scans, per-tenant mode forced ─────────────────────────


def _iso(minutes_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()


async def _seed_all(tenants: tuple[str, ...]) -> dict[str, dict[str, str]]:
    """One due schedule, one open batch and one stuck document per tenant."""
    ids: dict[str, dict[str, str]] = {}
    async with database.get_session_factory()() as session:
        for t in tenants:
            deal, doc, sched, batch = (str(uuid4()) for _ in range(4))
            ids[t] = {"deal": deal, "doc": doc, "schedule": sched, "batch": batch}
            await session.execute(
                text(
                    "INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at) "
                    "VALUES (:id, :tenant, 'RLS Hotel', 'Draft', :ts, :ts)"
                ),
                {"id": deal, "tenant": t, "ts": "2026-07-01 00:00:00"},
            )
            await session.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, "
                    "uploaded_at, status_changed_at, content_hash, storage_key, parser, "
                    "extraction_data) VALUES (:id, :deal, :tenant, 'x.xlsx', 'T12', 'EXTRACTING', "
                    ":up, :sca, :ch, :sk, 'test-fixture', '{}')"
                ),
                {
                    "id": doc,
                    "deal": deal,
                    "tenant": t,
                    "up": _iso(180),
                    "sca": _iso(120),
                    "ch": uuid4().hex * 2,
                    "sk": f"raw/{doc}",
                },
            )
            await session.execute(
                text(
                    "INSERT INTO pipeline_digest_schedules (id, tenant_id, name, saved_view_id, "
                    "cadence, weekday, hour_utc, delivery, slack_webhook_url, email_recipients, "
                    "include_kpi_summary, include_recently_mutated, include_deals_meeting_target, "
                    "include_full_table, is_active, next_run_at, created_at, updated_at) VALUES "
                    "(:id, :tenant, 'Overdue', NULL, 'daily', NULL, 13, 'slack', :slack, '[]', "
                    "1, 0, 0, 0, 1, :next_run, :now, :now)"
                ),
                {
                    "id": sched,
                    "tenant": t,
                    "slack": "https://hooks.slack.com/services/T0/B0/x",
                    "next_run": _iso(5),
                    "now": _iso(0),
                },
            )
            await session.execute(
                text(
                    "INSERT INTO pending_batches (id, batch_id, deal_id, tenant_id, agent_name, "
                    "status, submitted_at) VALUES (:id, :bid, :deal, :tenant, 'analyst', "
                    "'queued', :ts)"
                ),
                {"id": batch, "bid": f"msgbatch_{batch}", "deal": deal, "tenant": t, "ts": _iso(10)},
            )
        await session.commit()
    return ids


class _InProgressClient:
    def __init__(self) -> None:
        self.retrieved: list[tuple[str, str | None]] = []

    def retrieve(self, batch_id: str) -> dict[str, str]:
        self.retrieved.append((batch_id, current_tenant_id.get()))
        return {"processing_status": "in_progress"}

    def results(self, batch_id: str) -> Any:  # pragma: no cover — never ended
        raise AssertionError("results() must not be called for in_progress batches")


@pytest.mark.parametrize("per_tenant", [False, True])
async def test_scans_find_registered_tenants_rows_only_in_per_tenant_mode(
    monkeypatch, per_tenant: bool
) -> None:
    """Per-tenant mode (what RLS needs): A and B are found, unregistered C
    is not, and each tenant is published before its scan. Mode off:
    today's behaviour — every row is found."""
    from app.agents import analyst_batch
    from app.services import digest_scheduler, extraction_watchdog

    monkeypatch.setattr(tenant_scan, "per_tenant_scans_active", lambda: per_tenant)
    monkeypatch.setenv("REQUIRE_REGISTERED_TENANT", "1")
    get_settings.cache_clear()
    await _register(TENANT_A, TENANT_B)
    ids = await _seed_all((TENANT_A, TENANT_B, TENANT_C))
    expected = {TENANT_A, TENANT_B} if per_tenant else {TENANT_A, TENANT_B, TENANT_C}

    # ── digest scheduler
    scan_publishes: list[tuple[str | None, str | None]] = []
    real_due = digest_scheduler._fetch_due_schedules

    async def _spy_due(session, *, now, tenant_id=None):
        scan_publishes.append((tenant_id, current_tenant_id.get()))
        return await real_due(session, now=now, tenant_id=tenant_id)

    dispatched: list[str] = []
    monkeypatch.setattr(digest_scheduler, "_fetch_due_schedules", _spy_due)
    monkeypatch.setattr(
        digest_scheduler,
        "dispatch_digest",
        lambda schedule, payload: dispatched.append(str(schedule["tenant_id"])),
    )
    with patch("app.services.pipeline_digest._post_slack", return_value=(True, None)):
        count = await digest_scheduler.tick_once()
    assert count == len(expected)
    assert set(dispatched) == expected
    if per_tenant:
        assert scan_publishes == [(TENANT_A, TENANT_A), (TENANT_B, TENANT_B)]
    else:
        assert scan_publishes == [(None, None)]

    # ── batch poller
    client = _InProgressClient()
    tally = await analyst_batch.poll_pending_batches(client=client)
    assert tally["checked"] == len(expected) and tally["pending"] == len(expected)
    assert {t for _, t in client.retrieved} == expected  # per-row publish kept
    async with database.get_session_factory()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tenant_id, status FROM pending_batches "
                    "WHERE tenant_id IN (:a, :b, :c)"
                ),
                {"a": TENANT_A, "b": TENANT_B, "c": TENANT_C},
            )
        ).fetchall()
    assert {r[0] for r in rows if r[1] == "in_progress"} == expected

    # ── extraction watchdog
    timed_out = await extraction_watchdog.tick_once()
    assert timed_out == len(expected)
    async with database.get_session_factory()() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT tenant_id, status FROM documents WHERE tenant_id IN (:a, :b, :c)"
                ),
                {"a": TENANT_A, "b": TENANT_B, "c": TENANT_C},
            )
        ).fetchall()
    assert {r[0] for r in rows if r[1] == "FAILED"} == expected
    assert ids[TENANT_C]["doc"]  # seeded; untouched in per-tenant mode
