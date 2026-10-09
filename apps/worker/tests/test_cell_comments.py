"""FON-41 E-011 — per-cell comments: CRUD, thread resolve/reopen, tenant scoping."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import text

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

_TENANT_A = "23b0cff3-6f9b-57a9-8d2a-5511f3dd9f7e"
_TENANT_B = "8d0c0d4e-1b52-4d1e-9a31-77c2f0c3a001"


def _auth(tenant: str = _TENANT_A, email: str = "analyst@fondok.test") -> object:
    from app.auth.context import AuthContext

    return AuthContext(
        tenant_id=UUID(tenant),
        user_id="user_analyst",
        role="member",
        source="jwt",
        org_id=None,
        email=email,
    )


async def _deal(tenant: str = _TENANT_A) -> UUID:
    from app.database import get_session_factory

    deal_id = uuid4()
    async with get_session_factory()() as s:
        ts = datetime.now(UTC)
        await s.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, status, ai_confidence, "
                "created_at, updated_at) VALUES (:id,:t,'d','Draft',0.0,:ts,:ts)"
            ),
            {"id": str(deal_id), "t": tenant, "ts": ts},
        )
        await s.commit()
    return deal_id


async def _create(deal_id: UUID, key: str, body: str, auth=None):
    from app.api.cell_comments import CreateCommentBody, create_cell_comment
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        return await create_cell_comment(
            deal_id=deal_id,
            body=CreateCommentBody(cell_key=key, body=body, cell_label="Rooms Revenue · FY 2023"),
            session=s,
            auth=auth or _auth(),
        )


async def _list(deal_id: UUID, auth=None, cell_key: str | None = None):
    from app.api.cell_comments import list_cell_comments
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        return await list_cell_comments(
            deal_id=deal_id, session=s, auth=auth or _auth(), cell_key=cell_key
        )


async def _resolve(deal_id: UUID, key: str, resolved: bool, auth=None):
    from app.api.cell_comments import ResolveThreadBody, resolve_cell_thread
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        return await resolve_cell_thread(
            deal_id=deal_id,
            body=ResolveThreadBody(cell_key=key, resolved=resolved),
            session=s,
            auth=auth or _auth(),
        )


HIST_KEY = f"hist:{uuid4()}::p_and_l_usali.revenue.rooms"
PROJ_KEY = "proj:expense.years[2].dept_expenses.rooms"


@pytest.mark.asyncio
async def test_create_list_thread_history() -> None:
    deal = await _deal()
    c1 = await _create(deal, HIST_KEY, "Ties to the 2023 audited P&L?")
    await _create(deal, HIST_KEY, "Confirmed with the broker.")
    await _create(deal, PROJ_KEY, "Why does rooms expense jump in year 3?")
    assert c1.author_email == "analyst@fondok.test"
    assert c1.resolved_at is None

    allc = await _list(deal)
    assert [c.cell_key for c in allc].count(HIST_KEY) == 2
    thread = await _list(deal, cell_key=HIST_KEY)
    assert [c.body for c in thread] == ["Ties to the 2023 audited P&L?", "Confirmed with the broker."]
    assert thread[0].cell_label == "Rooms Revenue · FY 2023"


@pytest.mark.asyncio
async def test_resolve_and_reopen_keeps_history() -> None:
    deal = await _deal()
    await _create(deal, HIST_KEY, "one")
    await _create(deal, HIST_KEY, "two")
    res = await _resolve(deal, HIST_KEY, True)
    assert res.updated == 2
    thread = await _list(deal, cell_key=HIST_KEY)
    assert len(thread) == 2  # never deleted
    assert all(c.resolved_at is not None for c in thread)
    assert thread[0].resolved_by == "analyst@fondok.test"
    # Resolving again is a no-op; re-opening clears the stamps.
    assert (await _resolve(deal, HIST_KEY, True)).updated == 0
    assert (await _resolve(deal, HIST_KEY, False)).updated == 2
    assert all(c.resolved_at is None for c in await _list(deal, cell_key=HIST_KEY))


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_or_write() -> None:
    deal = await _deal(_TENANT_A)
    await _create(deal, HIST_KEY, "tenant A note")
    other = _auth(_TENANT_B, "intruder@other.test")
    with pytest.raises(HTTPException) as e1:
        await _list(deal, auth=other)
    assert e1.value.status_code == 404
    with pytest.raises(HTTPException) as e2:
        await _create(deal, HIST_KEY, "sneaky", auth=other)
    assert e2.value.status_code == 404
    with pytest.raises(HTTPException) as e3:
        await _resolve(deal, HIST_KEY, True, auth=other)
    assert e3.value.status_code == 404


@pytest.mark.asyncio
async def test_rows_of_another_tenant_never_leak_into_a_thread() -> None:
    """Even a row that names this deal under another tenant stays invisible."""
    from app.database import get_session_factory

    deal = await _deal(_TENANT_A)
    await _create(deal, HIST_KEY, "mine")
    async with get_session_factory()() as s:
        await s.execute(
            text(
                "INSERT INTO cell_comments (id, tenant_id, deal_id, cell_key, body, created_at) "
                "VALUES (:id, :t, :d, :k, 'foreign', :ts)"
            ),
            {"id": str(uuid4()), "t": _TENANT_B, "d": str(deal), "k": HIST_KEY,
             "ts": datetime.now(UTC)},
        )
        await s.commit()
    bodies = [c.body for c in await _list(deal)]
    assert bodies == ["mine"]
    # Resolving the thread touches only this tenant's rows.
    assert (await _resolve(deal, HIST_KEY, True)).updated == 1


def test_cell_key_shape_is_validated() -> None:
    from app.api.cell_comments import CreateCommentBody

    CreateCommentBody(cell_key=PROJ_KEY, body="ok")
    CreateCommentBody(cell_key=HIST_KEY, body="ok")
    for bad in ("rooms_revenue", "hist:not-a-uuid::x", "proj:expense.gop", "proj:Expense.years[0].gop"):
        with pytest.raises(ValidationError):
            CreateCommentBody(cell_key=bad, body="x")
    with pytest.raises(ValidationError):
        CreateCommentBody(cell_key=PROJ_KEY, body="   ")
