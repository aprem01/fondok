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


async def _seed_run(deal_id: str, *, status: str = "complete", tenant: str = TENANT_A) -> None:
    """A full canonical engine chain (one run_id across every base engine)."""
    from app.services.engine_runner import CANONICAL_ENGINES

    run_id = str(uuid4())
    for name in CANONICAL_ENGINES:
        await _sql(
            "INSERT INTO engine_outputs (id, deal_id, tenant_id, run_id, engine_name, "
            "status, inputs, outputs, started_at, completed_at) VALUES "
            "(:id, :deal, :t, :run, :name, :status, '{}', '{}', "
            "'2026-10-30 00:00:00', '2026-10-30 00:00:01')",
            {"id": str(uuid4()), "deal": deal_id, "t": tenant, "run": run_id,
             "name": name, "status": status},
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
        assert "Run the model" in detail["message"]
        assert target in detail["message"]
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
        # A draft PATCHed to another label before this change: Draft holds
        # iff the wizard is unfinished.
        ("draft", "In Review"): "Draft",
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


def test_postgres_check_constraint_lists_exactly_the_seven() -> None:
    from app.migrations import MIGRATIONS

    by_name = dict(MIGRATIONS)
    names = [n for n, _ in MIGRATIONS]
    assert names.index("deals.status_v2_backfill") < names.index("deals.add_status_chk")
    chk = by_name["deals.add_status_chk"]
    assert "deals_status_chk" in chk
    for value in SEVEN:
        assert f"'{value}'" in chk
    assert "'In Review'" not in chk
