"""Tests for the append-only memo-section edit history.

Covers:
* ``test_record_and_list_roundtrip`` — write multiple edits and read
  them back newest-first; the section_id filter narrows correctly.
* ``test_memo_edit_endpoint_writes_audit`` — the API route records the
  edit AND stamps an ``audit_log`` row in the same transaction.
* ``test_postgres_trigger_blocks_mutation`` — append-only trigger
  exists in the migration list (Postgres-only enforcement; on SQLite
  we just assert the schema is shaped so the trigger SQL can attach).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-memo-edits.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("memo_edits", "audit_log"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


@pytest.mark.asyncio
async def test_record_and_list_roundtrip() -> None:
    """Insert three edits across two sections, verify ordering + filter."""
    from app.database import get_session_factory
    from app.memo_edits import list_edits, record_edit

    tenant = str(uuid4())
    deal = str(uuid4())
    actor = "user-alice"

    factory = get_session_factory()
    async with factory() as session:
        # Section A — two edits.
        await record_edit(
            session,
            tenant_id=tenant,
            deal_id=deal,
            section_id="investment_thesis",
            actor_id=actor,
            original_body="v1 thesis",
            new_body="v2 thesis (added comparable)",
            comment="add Loews comp",
        )
        await record_edit(
            session,
            tenant_id=tenant,
            deal_id=deal,
            section_id="investment_thesis",
            actor_id=actor,
            original_body="v2 thesis (added comparable)",
            new_body="v3 thesis (refined)",
        )
        # Section B — one edit.
        await record_edit(
            session,
            tenant_id=tenant,
            deal_id=deal,
            section_id="risk_factors",
            actor_id=actor,
            original_body="initial risks",
            new_body="initial risks + brand",
        )
        await session.commit()

        # All-deal listing returns 3 rows.
        all_rows = await list_edits(session, deal_id=deal)
        assert len(all_rows) == 3

        # Section filter narrows to 2.
        thesis_rows = await list_edits(
            session, deal_id=deal, section_id="investment_thesis"
        )
        assert len(thesis_rows) == 2
        # Newest first → v3 then v2.
        assert thesis_rows[0]["new_body"] == "v3 thesis (refined)"
        assert thesis_rows[1]["new_body"] == "v2 thesis (added comparable)"
        # Comment round-trips on the row that had one.
        assert thesis_rows[1]["comment"] == "add Loews comp"
        # And the row that didn't carries None.
        assert thesis_rows[0]["comment"] is None


@pytest.mark.asyncio
async def test_memo_edit_endpoint_writes_audit() -> None:
    """POST /memo/{section_id}/edits stamps both a memo_edits row AND an
    audit_log row in one transaction."""
    from httpx import ASGITransport, AsyncClient

    from app.database import get_session_factory
    from app.main import app

    deal_id = str(uuid4())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            f"/deals/{deal_id}/memo/investment_thesis/edits",
            json={
                "original_body": "draft thesis",
                "new_body": "final thesis with comp set",
                "comment": "ready for IC",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["section_id"] == "investment_thesis"
        assert body["new_body"] == "final thesis with comp set"
        assert body["original_body"] == "draft thesis"
        assert body["comment"] == "ready for IC"

        # GET the history.
        r = await client.get(f"/deals/{deal_id}/memo/edits")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert len(rows) == 1
        assert rows[0]["section_id"] == "investment_thesis"

        # Section-scoped GET returns same.
        r = await client.get(
            f"/deals/{deal_id}/memo/edits?section_id=investment_thesis"
        )
        assert r.status_code == 200
        assert len(r.json()) == 1

        # Other-section filter returns empty.
        r = await client.get(
            f"/deals/{deal_id}/memo/edits?section_id=does_not_exist"
        )
        assert r.json() == []

    # And an audit_log row exists for the edit.
    factory = get_session_factory()
    async with factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT action, resource_type, resource_id, output_hash
                  FROM audit_log
                 WHERE resource_id = :rid
                 ORDER BY created_at DESC
                """
            ),
            {"rid": deal_id},
        )
        actions = [
            (r._mapping["action"], r._mapping["resource_type"])
            for r in rows.fetchall()
        ]
    assert ("memo.edited", "memo") in actions


@pytest.mark.asyncio
async def test_record_edit_returns_uuid() -> None:
    """``record_edit`` returns the freshly minted UUID so the caller can
    cite it in an audit row's metadata."""
    from uuid import UUID as UUIDType

    from app.database import get_session_factory
    from app.memo_edits import record_edit

    factory = get_session_factory()
    async with factory() as session:
        edit_id = await record_edit(
            session,
            tenant_id=str(uuid4()),
            deal_id=str(uuid4()),
            section_id="recommendation",
            actor_id="user-bob",
            original_body="hold",
            new_body="conditional go",
        )
        await session.commit()
    assert isinstance(edit_id, UUIDType)


def test_postgres_append_only_trigger_in_migrations() -> None:
    """Sanity check: the Postgres ``MIGRATIONS`` list ships an
    ``UPDATE OR DELETE`` trigger so a real Postgres deploy enforces
    append-only at the DB layer (SQLite can't enforce it)."""
    from app.migrations import MIGRATIONS

    sql_blobs = [sql for _name, sql in MIGRATIONS]
    joined = "\n".join(sql_blobs)
    assert "memo_edits_block_mutation" in joined
    assert "memo_edits_no_update_delete" in joined
    assert "BEFORE UPDATE OR DELETE ON memo_edits" in joined
