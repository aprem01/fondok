"""Wave 5 — the seven-status deal lifecycle.

Draft, In Progress, Ready for Review, IC Ready, IC Approved, IC Not Approved,
Archived.

* ``Draft`` / ``In Progress`` are automatic: they follow ``deals.lifecycle``
  (create, activate, duplicate never copy a manual status).
* ``POST /deals/{id}/status`` is the one way a member sets a manual status:
  validated, tenant-scoped, audited ``deal.status_changed`` with from → to
  and the actor. IC Ready / IC Approved / IC Not Approved need a completed
  model run. An archived deal can only be restored to In Progress.
* ``PATCH /deals/{id}`` no longer accepts ``status``.
* The one-time backfill (idempotent, re-run on every boot) moves finished
  wizard deals at Draft / In Review to In Progress, keeps IC Ready and
  Archived, then a CHECK (Postgres) / trigger pair (SQLite) limits the column
  to the seven values.

Pattern: ``tests/test_deals_drafts.py`` (per-module SQLite DB, header tenancy).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-deal-status.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-deal-status-storage"
)
_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)

TENANT_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
HDR_A = {"X-Tenant-Id": TENANT_A}
HDR_B = {"X-Tenant-Id": TENANT_B}

SEVEN = (
    "Draft",
    "In Progress",
    "Ready for Review",
    "IC Ready",
    "IC Approved",
    "IC Not Approved",
    "Archived",
)


# ─────────────────────────── fixtures / helpers ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("audit_log", "documents", "scenarios", "engine_outputs", "deals"):
            with contextlib.suppress(Exception):
                await session.execute(text(f"DELETE FROM {tbl}"))
        await session.commit()
    from app.services.pipeline import invalidate

    invalidate()
    yield


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create(client, headers: dict[str, str] = HDR_A, **body: Any) -> dict[str, Any]:
    body.setdefault("name", "Status Hotel")
    r = await client.post("/deals", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _set(client, deal_id: str, status: str, headers: dict[str, str] = HDR_A):
    return await client.post(f"/deals/{deal_id}/status", json={"status": status}, headers=headers)


async def _sql(stmt: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    from sqlalchemy import text

    from app.database import get_session_factory

    async with get_session_factory()() as session:
        result = await session.execute(text(stmt), params or {})
        rows = [dict(r._mapping) for r in result.fetchall()] if result.returns_rows else []
        await session.commit()
    return rows


async def _status_of(deal_id: str) -> str | None:
    rows = await _sql(
        "SELECT status FROM deals WHERE id = :id AND tenant_id = :t",
        {"id": deal_id, "t": TENANT_A},
    )
    return rows[0]["status"] if rows else None


async def _audit(deal_id: str, action: str, tenant: str = TENANT_A) -> list[dict[str, Any]]:
    rows = await _sql(
        "SELECT actor_id, payload, before, after FROM audit_log "
        "WHERE resource_id = :rid AND action = :action AND tenant_id = :t ORDER BY rowid",
        {"rid": deal_id, "action": action, "t": tenant},
    )
    out = []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        out.append({"actor_id": r["actor_id"], **(payload or {}),
                    "before": json.loads(r["before"]) if r["before"] else None,
                    "after": json.loads(r["after"]) if r["after"] else None})
    return out


async def _seed_run(
    deal_id: str,
    *,
    status: str = "complete",
    tenant: str = TENANT_A,
    per_engine: dict[str, str] | None = None,
) -> None:
    """A full canonical engine chain (one run_id across every base engine)."""
    from app.services.engine_runner import CANONICAL_ENGINES

    run_id = str(uuid4())
    for name in CANONICAL_ENGINES:
        engine_status = (per_engine or {}).get(name, status)
        await _sql(
            "INSERT INTO engine_outputs (id, deal_id, tenant_id, run_id, engine_name, "
            "status, inputs, outputs, started_at, completed_at) VALUES "
            "(:id, :deal, :t, :run, :name, :status, '{}', '{}', "
            "'2026-10-30 00:00:00', '2026-10-30 00:00:01')",
            {"id": str(uuid4()), "deal": deal_id, "t": tenant, "run": run_id,
             "name": name, "status": engine_status},
        )


# ─────────────────────────── the vocabulary ───────────────────────────


def test_the_vocabulary_is_exactly_the_seven_statuses() -> None:
    from fondok_schemas import (
        DEAL_STATUSES,
        MANUAL_DEAL_STATUSES,
        RUN_GATED_DEAL_STATUSES,
        status_for_lifecycle,
    )

    assert DEAL_STATUSES == SEVEN
    assert {
        "In Progress", "Ready for Review", "IC Ready", "IC Approved", "IC Not Approved",
    } == MANUAL_DEAL_STATUSES
    assert {"IC Ready", "IC Approved", "IC Not Approved"} == RUN_GATED_DEAL_STATUSES
    assert status_for_lifecycle("draft") == "Draft"
    assert status_for_lifecycle("active") == "In Progress"


# ─────────────────────────── automatic statuses ───────────────────────────


@pytest.mark.asyncio
async def test_create_and_activate_set_the_automatic_status() -> None:
    async with _client() as client:
        direct = await _create(client, name="Direct Hotel")
        assert direct["status"] == "In Progress"
        draft = await _create(client, name="Wizard Hotel", lifecycle="draft")
        assert draft["status"] == "Draft"
        r = await client.post(f"/deals/{draft['id']}/activate", headers=HDR_A)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "In Progress"


@pytest.mark.asyncio
async def test_duplicate_sets_the_copy_status_from_its_lifecycle() -> None:
    async with _client() as client:
        src = await _create(client, name="Committee Hotel")
        await _seed_run(src["id"])
        assert (await _set(client, src["id"], "IC Approved")).status_code == 200
        r = await client.post(f"/deals/{src['id']}/duplicate", json={}, headers=HDR_A)
        assert r.status_code in (200, 201), r.text
        copy = r.json()["deal"]
        assert copy["status"] == "In Progress"
        assert await _status_of(copy["id"]) == "In Progress"
        assert await _status_of(src["id"]) == "IC Approved"


# ─────────────────────────── POST /deals/{id}/status ───────────────────────────


@pytest.mark.asyncio
async def test_ready_for_review_needs_no_run_and_is_audited_with_from_to_and_actor() -> None:
    async with _client() as client:
        deal = await _create(client)
        r = await _set(client, deal["id"], "Ready for Review")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Ready for Review"
        assert await _status_of(deal["id"]) == "Ready for Review"
    rows = await _audit(deal["id"], "deal.status_changed")
    assert len(rows) == 1
    assert rows[0]["output"]["from"] == "In Progress"
    assert rows[0]["output"]["to"] == "Ready for Review"
    assert rows[0]["actor_id"]  # who did it is always recorded
    assert rows[0]["before"] == {"status": "In Progress"}
    assert rows[0]["after"] == {"status": "Ready for Review"}


@pytest.mark.parametrize("target", ["IC Ready", "IC Approved", "IC Not Approved"])
@pytest.mark.asyncio
async def test_ic_statuses_are_refused_without_a_completed_model_run(target: str) -> None:
    async with _client() as client:
        deal = await _create(client)
        r = await _set(client, deal["id"], target)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "model_run_required"
        assert detail["message"] == (
            f"Run the model first — {target} needs a completed model run with no "
            "failed engine."
        )
        assert await _status_of(deal["id"]) == "In Progress"
        assert not await _audit(deal["id"], "deal.status_changed")

        await _seed_run(deal["id"])
        r = await _set(client, deal["id"], target)
        assert r.status_code == 200, r.text
        assert await _status_of(deal["id"]) == target


@pytest.mark.asyncio
async def test_a_run_still_in_flight_is_not_a_completed_run() -> None:
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"], status="running")
        r = await _set(client, deal["id"], "IC Ready")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "model_run_required"


@pytest.mark.parametrize("target", ["IC Ready", "IC Approved", "IC Not Approved"])
@pytest.mark.asyncio
async def test_a_run_with_a_failed_engine_does_not_unlock_the_ic_statuses(target: str) -> None:
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"], per_engine={"returns": "failed", "debt": "failed"})
        r = await _set(client, deal["id"], target)
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "model_run_required"
        assert detail["reason"] == "failed_engines"
        assert detail["failed_engines"] == ["debt", "returns"]
        assert detail["message"] == (
            f"The latest model run has a failed engine (Debt, Returns) — {target} "
            "needs a completed model run with no failed engine. Fix the inputs and "
            "re-run the model."
        )
        assert await _status_of(deal["id"]) == "In Progress"


@pytest.mark.asyncio
async def test_a_not_applicable_engine_is_fine() -> None:
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"], per_engine={"partnership": "not_applicable"})
        r = await _set(client, deal["id"], "IC Ready")
        assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_only_the_latest_run_counts() -> None:
    """A clean older run does not unlock the statuses when the latest failed."""
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"])
        await _sql(
            "UPDATE engine_outputs SET started_at = '2026-10-01 00:00:00' "
            "WHERE deal_id = :d AND tenant_id = :t",
            {"d": deal["id"], "t": TENANT_A},
        )
        await _seed_run(deal["id"], per_engine={"returns": "failed"})
        r = await _set(client, deal["id"], "IC Ready")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["reason"] == "failed_engines"


@pytest.mark.asyncio
async def test_a_stale_expected_status_is_refused_and_changes_nothing() -> None:
    """A page that still shows In Progress must not overwrite IC Approved."""
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"])
        assert (await _set(client, deal["id"], "IC Approved")).status_code == 200
        r = await client.post(
            f"/deals/{deal['id']}/status",
            json={"status": "IC Ready", "expected_status": "In Progress"},
            headers=HDR_A,
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "status_changed"
        assert detail["current"] == "IC Approved"
        assert "IC Approved" in detail["message"]
        assert await _status_of(deal["id"]) == "IC Approved"
        assert len(await _audit(deal["id"], "deal.status_changed")) == 1

        r = await client.post(
            f"/deals/{deal['id']}/status",
            json={"status": "IC Ready", "expected_status": "IC Approved"},
            headers=HDR_A,
        )
        assert r.status_code == 200, r.text
        assert await _status_of(deal["id"]) == "IC Ready"


@pytest.mark.asyncio
async def test_another_tenants_run_does_not_unlock_the_ic_statuses() -> None:
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"], tenant=TENANT_B)
        r = await _set(client, deal["id"], "IC Ready")
        assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_every_manual_transition_between_the_five_settable_statuses() -> None:
    from fondok_schemas import MANUAL_DEAL_STATUSES

    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"])
        current = "In Progress"
        for src in sorted(MANUAL_DEAL_STATUSES):
            for dst in sorted(MANUAL_DEAL_STATUSES):
                if src == dst:
                    continue
                if current != src:
                    assert (await _set(client, deal["id"], src)).status_code == 200
                    current = src
                r = await _set(client, deal["id"], dst)
                assert r.status_code == 200, f"{src} → {dst}: {r.text}"
                assert r.json()["status"] == dst
                current = dst


@pytest.mark.parametrize("target", ["Draft", "Archived"])
@pytest.mark.asyncio
async def test_automatic_and_archive_statuses_are_not_settable(target: str) -> None:
    async with _client() as client:
        deal = await _create(client)
        r = await _set(client, deal["id"], target)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "status_not_settable"
        assert await _status_of(deal["id"]) == "In Progress"


@pytest.mark.parametrize("junk", ["In Review", "Active", "ic ready", ""])
@pytest.mark.asyncio
async def test_a_value_outside_the_seven_is_422(junk: str) -> None:
    async with _client() as client:
        deal = await _create(client)
        r = await _set(client, deal["id"], junk)
        assert r.status_code == 422, r.text
        assert await _status_of(deal["id"]) == "In Progress"


@pytest.mark.asyncio
async def test_a_draft_is_409_deal_is_draft() -> None:
    async with _client() as client:
        draft = await _create(client, lifecycle="draft")
        r = await _set(client, draft["id"], "Ready for Review")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "deal_is_draft"
        assert await _status_of(draft["id"]) == "Draft"


@pytest.mark.asyncio
async def test_an_archived_deal_is_only_restored_to_in_progress() -> None:
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"])
        assert (await _set(client, deal["id"], "IC Ready")).status_code == 200
        assert (await client.delete(f"/deals/{deal['id']}", headers=HDR_A)).status_code == 200
        assert await _status_of(deal["id"]) == "Archived"

        r = await _set(client, deal["id"], "IC Approved")
        assert r.status_code == 409, r.text
        assert r.json()["detail"]["code"] == "deal_archived"
        assert await _status_of(deal["id"]) == "Archived"

        r = await _set(client, deal["id"], "In Progress")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "In Progress"
    restored = await _audit(deal["id"], "deal.status_changed")
    assert [(a["output"]["from"], a["output"]["to"]) for a in restored] == [
        ("In Progress", "IC Ready"),
        ("Archived", "In Progress"),
    ]


@pytest.mark.asyncio
async def test_setting_the_current_status_again_changes_and_audits_nothing() -> None:
    async with _client() as client:
        deal = await _create(client)
        assert (await _set(client, deal["id"], "Ready for Review")).status_code == 200
        r = await _set(client, deal["id"], "Ready for Review")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Ready for Review"
    assert len(await _audit(deal["id"], "deal.status_changed")) == 1


@pytest.mark.asyncio
async def test_another_tenant_gets_404_and_changes_nothing() -> None:
    async with _client() as client:
        deal = await _create(client)
        r = await _set(client, deal["id"], "Ready for Review", headers=HDR_B)
        assert r.status_code == 404, r.text
        assert await _status_of(deal["id"]) == "In Progress"
    assert not await _audit(deal["id"], "deal.status_changed")
    assert not await _audit(deal["id"], "deal.status_changed", tenant=TENANT_B)


@pytest.mark.asyncio
async def test_status_change_shows_on_the_pipeline_at_once() -> None:
    async with _client() as client:
        deal = await _create(client, keys=100)
        r = await client.get("/deals/pipeline", headers=HDR_A)
        assert {d["deal_id"]: d["status"] for d in r.json()["deals"]}[deal["id"]] == "In Progress"
        assert (await _set(client, deal["id"], "Ready for Review")).status_code == 200
        r = await client.get("/deals/pipeline", headers=HDR_A)
        assert {d["deal_id"]: d["status"] for d in r.json()["deals"]}[deal["id"]] == "Ready for Review"


# ─────────────────────────── PATCH no longer writes status ───────────────────────────


@pytest.mark.asyncio
async def test_patch_refuses_status_and_points_at_the_status_endpoint() -> None:
    async with _client() as client:
        deal = await _create(client)
        r = await client.patch(f"/deals/{deal['id']}", json={"status": "IC Ready"}, headers=HDR_A)
        assert r.status_code == 422, r.text
        assert "/status" in r.text
        assert await _status_of(deal["id"]) == "In Progress"
        # Every other field still patches.
        r = await client.patch(f"/deals/{deal['id']}", json={"name": "Renamed"}, headers=HDR_A)
        assert r.status_code == 200, r.text


# ─────────────────────────── the one-time backfill ───────────────────────────


async def _drop_status_guard() -> None:
    """Let the test plant legacy rows the guard (rightly) refuses."""
    await _sql("DROP TRIGGER IF EXISTS deals_status_chk_insert")
    await _sql("DROP TRIGGER IF EXISTS deals_status_chk_update")


async def _plant(lifecycle: str, status: str) -> str:
    deal_id = str(uuid4())
    await _sql(
        "INSERT INTO deals (id, tenant_id, name, status, lifecycle, created_at, updated_at) "
        "VALUES (:id, :t, :name, :status, :lc, '2026-10-01', '2026-10-01')",
        {"id": deal_id, "t": TENANT_A, "name": f"{lifecycle}/{status}", "status": status, "lc": lifecycle},
    )
    return deal_id


@pytest.mark.asyncio
async def test_backfill_moves_finished_wizards_to_in_progress_and_is_idempotent() -> None:
    from app.migrations import run_startup_migrations

    await _drop_status_guard()
    planted = {
        ("active", "Draft"): "In Progress",
        ("active", "In Review"): "In Progress",
        ("active", "IC Ready"): "IC Ready",
        ("active", "Archived"): "Archived",
        ("draft", "Draft"): "Draft",
        # The brief: IC Ready and Archived keep their status — a wizard
        # draft's too.
        ("draft", "IC Ready"): "IC Ready",
        ("draft", "Archived"): "Archived",
        # A draft PATCHed to another label before this change goes back to
        # Draft (the wizard is unfinished).
        ("draft", "In Review"): "Draft",
        ("draft", "Onboarding"): "Draft",
        # Junk a free-text PATCH could store: the lifecycle decides, so the
        # CHECK can always be added.
        ("active", "Active"): "In Progress",
        ("active", "Under Review"): "In Progress",
    }
    ids = {key: await _plant(*key) for key in planted}

    await run_startup_migrations()
    await run_startup_migrations()  # every boot re-runs it: a no-op the second time

    for key, expected in planted.items():
        assert await _status_of(ids[key]) == expected, key

    moved = {k for k, v in planted.items() if k[1] != v}
    for key, deal_id in ids.items():
        rows = await _audit(deal_id, "deal.status_migrated")
        if key in moved:
            assert len(rows) == 1, key
            assert rows[0]["actor_id"] == "system:migration"
            assert rows[0]["before"] == {"status": key[1]}
            assert rows[0]["after"] == {"status": planted[key]}
        else:
            assert rows == [], key


@pytest.mark.asyncio
async def test_the_guard_refuses_any_value_outside_the_seven() -> None:
    from sqlalchemy.exc import IntegrityError

    async with _client() as client:
        deal = await _create(client)
    for value in SEVEN:
        await _sql("UPDATE deals SET status = :s WHERE id = :id AND tenant_id = :t",
                   {"s": value, "id": deal["id"], "t": TENANT_A})
    with pytest.raises(IntegrityError, match="deals_status_chk"):
        await _sql("UPDATE deals SET status = 'In Review' WHERE id = :id AND tenant_id = :t",
                   {"id": deal["id"], "t": TENANT_A})
    with pytest.raises(IntegrityError, match="deals_status_chk"):
        await _plant("active", "Under Review")


def test_postgres_status_migration_is_one_time_rls_safe_and_fatal() -> None:
    from app.migrations import FATAL_MIGRATIONS, MIGRATIONS

    by_name = dict(MIGRATIONS)
    names = [n for n, _ in MIGRATIONS]
    assert names.index("deals.status_default_in_progress") < names.index("deals.status_v2")
    assert "SET DEFAULT 'In Progress'" in by_name["deals.status_default_in_progress"]
    sql = by_name["deals.status_v2"]
    # One-time: the whole block is skipped once the CHECK exists.
    assert "IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'deals_status_chk')" in sql
    # Forced RLS is lifted for the backfill and restored in the same block.
    for table in ("deals", "audit_log"):
        assert f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
    assert sql.index("NO FORCE") < sql.index("UPDATE deals") < sql.index("ADD CONSTRAINT deals_status_chk")
    assert sql.index("ADD CONSTRAINT deals_status_chk") < sql.index("ALTER TABLE deals FORCE")
    chk = sql[sql.index("ADD CONSTRAINT deals_status_chk"):]
    for value in SEVEN:
        assert f"'{value}'" in chk
    assert "'In Review'" not in chk
    assert "deals.status_v2" in FATAL_MIGRATIONS


@pytest.mark.asyncio
async def test_a_row_that_omits_status_is_an_in_progress_deal() -> None:
    deal_id = str(uuid4())
    await _sql(
        "INSERT INTO deals (id, tenant_id, name, created_at, updated_at) "
        "VALUES (:id, :t, 'Bare row', '2026-10-01', '2026-10-01')",
        {"id": deal_id, "t": TENANT_A},
    )
    assert await _status_of(deal_id) == "In Progress"


@pytest.mark.asyncio
async def test_a_fatal_migration_failure_stops_the_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.migrations as migrations

    broken = [*migrations.SQLITE_MIGRATIONS, ("deals.status_v2_backfill", "UPDATE no_such_table SET x = 1")]
    monkeypatch.setattr(migrations, "SQLITE_MIGRATIONS", broken)
    with pytest.raises(migrations.FatalMigrationError, match=r"deals\.status_v2_backfill"):
        await migrations.run_startup_migrations()


@pytest.mark.asyncio
async def test_a_non_fatal_migration_failure_is_only_logged(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.migrations as migrations

    broken = [*migrations.SQLITE_MIGRATIONS, ("probe.not_fatal", "UPDATE no_such_table SET x = 1")]
    monkeypatch.setattr(migrations, "SQLITE_MIGRATIONS", broken)
    await migrations.run_startup_migrations()  # no raise


@pytest.mark.asyncio
async def test_lifespan_refuses_to_start_on_a_fatal_migration(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main_mod
    from app.migrations import FatalMigrationError

    async def _boom() -> None:
        raise FatalMigrationError("migration deals.status_v2 failed")

    monkeypatch.setattr(main_mod, "run_startup_migrations", _boom)
    with pytest.raises(FatalMigrationError):
        async with main_mod.app.router.lifespan_context(main_mod.app):
            pass


# ─────────────── round 3 (re-review R1): unavailable ≠ failed ───────────────


def _wrapped(dbapi_exc: BaseException, *, invalidated: bool = False) -> BaseException:
    """An exception shaped as SQLAlchemy raises it: a DBAPIError whose
    ``orig`` / ``__cause__`` is the driver's exception."""
    from sqlalchemy.exc import DBAPIError

    err = DBAPIError.instance(
        "SELECT 1", {}, dbapi_exc, Exception, connection_invalidated=invalidated
    )
    err.__cause__ = dbapi_exc
    return err


def test_database_unavailable_errors_are_told_apart_from_sql_errors() -> None:
    import sqlite3

    import asyncpg.exceptions as pg

    from app.migrations import _database_unavailable

    unavailable = [
        ConnectionRefusedError(61, "Connect call failed ('127.0.0.1', 5432)"),
        OSError("Network is unreachable"),
        TimeoutError(),
        _wrapped(pg.ConnectionDoesNotExistError("connection was closed in the middle of operation")),
        _wrapped(pg.CannotConnectNowError("the database system is starting up")),
        _wrapped(pg.AdminShutdownError("terminating connection due to administrator command")),
        _wrapped(pg.TooManyConnectionsError("sorry, too many clients already")),
        _wrapped(pg.InterfaceError("connection is closed")),
        _wrapped(Exception("server closed the connection unexpectedly"), invalidated=True),
    ]
    for exc in unavailable:
        assert _database_unavailable(exc), repr(exc)

    sql_errors = [
        _wrapped(pg.InsufficientPrivilegeError("must be owner of table deals")),
        _wrapped(pg.CheckViolationError('check constraint "deals_status_chk" is violated by some row')),
        _wrapped(pg.PostgresSyntaxError('syntax error at or near "DO"')),
        _wrapped(sqlite3.OperationalError("no such table: no_such_table")),
        ValueError("not a database error at all"),
    ]
    for exc in sql_errors:
        assert not _database_unavailable(exc), repr(exc)


class _UnreachableEngine:
    """A migrations engine whose every connection attempt fails as asyncpg
    does when Postgres is down."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.attempts = 0

    def begin(self):
        engine = self

        class _Ctx:
            async def __aenter__(self):
                engine.attempts += 1
                raise engine.exc

            async def __aexit__(self, *a):
                return False

        return _Ctx()


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError(61, "Connect call failed ('127.0.0.1', 5432)"),
        TimeoutError(),
    ],
    ids=["refused", "timeout"],
)
@pytest.mark.asyncio
async def test_an_unreachable_database_does_not_stop_the_boot(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    """Postgres down across a restart: log, boot degraded, retry next boot —
    never use up the platform's restart budget (re-review R1)."""
    import app.migrations as migrations

    engine = _UnreachableEngine(exc)
    monkeypatch.setattr(migrations, "get_migrations_engine", lambda: engine)
    await migrations.run_startup_migrations()  # no raise
    # every DDL entry tried, plus the one-time ai_confidence backfill and the
    # display-string repair (W5) — each logged, none stopping the boot
    assert engine.attempts == len(migrations.SQLITE_MIGRATIONS) + 2
    assert any("database unavailable" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_sql_error_in_a_fatal_entry_still_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncpg.exceptions as pg

    import app.migrations as migrations

    engine = _UnreachableEngine(_wrapped(pg.InsufficientPrivilegeError("must be owner of table deals")))
    monkeypatch.setattr(migrations, "get_migrations_engine", lambda: engine)
    with pytest.raises(migrations.FatalMigrationError, match="must be owner"):
        await migrations.run_startup_migrations()


def test_the_real_worker_boots_degraded_with_no_database() -> None:
    """The real lifespan, in a fresh process, against a Postgres DSN nothing
    listens on: the worker starts and /health reports the database down."""
    import socket
    import subprocess
    import sys

    with socket.socket() as s:  # a free port with no listener
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    dsn = f"postgresql://nobody:pw@127.0.0.1:{port}/none"
    script = (
        "import os\n"
        "from fastapi.testclient import TestClient\n"
        "from app.main import app\n"
        "with TestClient(app) as c:\n"
        "    r = c.get('/health')\n"
        "    print('BOOTED', r.status_code, r.json().get('db'))\n"
    )
    env = {
        **os.environ,
        "DATABASE_URL": dsn,
        "DATABASE_URL_MIGRATIONS": dsn,
        "DIGEST_SCHEDULER_ENABLED": "False",
        "EXTRACTION_WATCHDOG_ENABLED": "False",
    }
    out = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert "BOOTED 200 fail" in out.stdout, out.stdout[-2000:] + out.stderr[-2000:]


# ─────────────── round 3 (R2 / R3): one gate, honest reasons ───────────────


@pytest.mark.asyncio
async def test_a_run_where_no_engine_applied_is_not_still_running() -> None:
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"], status="not_applicable")
        r = await _set(client, deal["id"], "IC Ready")
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["reason"] == "nothing_computed"
        assert "still running" not in detail["message"]
        assert detail["message"] == (
            "The latest model run computed nothing (no engine applied to this "
            "deal) — IC Ready needs a completed model run with no failed engine."
        )


@pytest.mark.asyncio
async def test_a_run_in_flight_reads_still_running() -> None:
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"], per_engine={"returns": "running"})
        r = await _set(client, deal["id"], "IC Ready")
        assert r.json()["detail"]["reason"] == "in_flight"
        assert "still running" in r.json()["detail"]["message"]


@pytest.mark.parametrize(
    ("per_engine", "ok", "reason"),
    [
        ({}, True, None),
        ({"partnership": "not_applicable"}, True, None),
        ({"returns": "failed"}, False, "failed_engines"),
    ],
)
@pytest.mark.asyncio
async def test_engines_payload_carries_the_worker_gate_for_the_web_hint(
    per_engine: dict[str, str], ok: bool, reason: str | None
) -> None:
    """The web's IC-status hint reads the worker's own gate (R2) — one
    definition, base engines only — never its own reading of the payload."""
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"], per_engine=per_engine)
        r = await client.get(f"/deals/{deal['id']}/engines", headers=HDR_A)
        assert r.status_code == 200, r.text
        gate = r.json()["ic_status_gate"]
        assert gate["ok"] is ok
        assert gate["reason"] == reason


@pytest.mark.asyncio
async def test_a_failed_composed_view_does_not_close_the_gate() -> None:
    """``cash_flow`` is a composed view, not a base engine: its failure is
    shown on its tab but neither the worker gate nor the web hint closes."""
    async with _client() as client:
        deal = await _create(client)
        await _seed_run(deal["id"])
        await _sql(
            "INSERT INTO engine_outputs (id, deal_id, tenant_id, run_id, engine_name, status, "
            "inputs, outputs, started_at, completed_at) SELECT :id, deal_id, tenant_id, run_id, "
            "'cash_flow', 'failed', '{}', '{}', started_at, completed_at FROM engine_outputs "
            "WHERE deal_id = :d AND tenant_id = :t LIMIT 1",
            {"id": str(uuid4()), "d": deal["id"], "t": TENANT_A},
        )
        r = await client.get(f"/deals/{deal['id']}/engines", headers=HDR_A)
        assert r.json()["ic_status_gate"]["ok"] is True
        assert (await _set(client, deal["id"], "IC Ready")).status_code == 200


@pytest.mark.asyncio
async def test_a_deal_never_run_has_a_closed_gate_on_the_engines_payload() -> None:
    async with _client() as client:
        deal = await _create(client)
        r = await client.get(f"/deals/{deal['id']}/engines", headers=HDR_A)
        assert r.json()["ic_status_gate"] == {"ok": False, "reason": "no_run", "failed_engines": []}


@pytest.mark.asyncio
async def test_a_refused_ai_confidence_backfill_stops_the_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confidence backfill is a data migration the app relies on: when the
    database answers and refuses it, the worker must not serve."""
    import asyncpg.exceptions as pg

    import app.migrations as migrations

    async def _refused(*_a: object, **_k: object) -> None:
        raise _wrapped(pg.InsufficientPrivilegeError("must be owner of table deals"))

    monkeypatch.setattr(migrations, "backfill_deal_ai_confidence", _refused)
    with pytest.raises(migrations.FatalMigrationError, match="ai_confidence_backfill"):
        await migrations.run_startup_migrations()


@pytest.mark.asyncio
async def test_an_unreachable_database_during_the_confidence_backfill_boots_degraded(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.migrations as migrations

    async def _unreachable(*_a: object, **_k: object) -> None:
        raise ConnectionRefusedError(61, "Connect call failed ('127.0.0.1', 5432)")

    monkeypatch.setattr(migrations, "backfill_deal_ai_confidence", _unreachable)
    await migrations.run_startup_migrations()  # no raise
    assert any(
        "database unavailable" in r.getMessage() and "ai_confidence_backfill" in r.getMessage()
        for r in caplog.records
    )
