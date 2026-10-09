"""FON-41 E-011 — Excel-like comments on individual P&L cells.

A reviewer can attach a note to ONE financial cell, see the thread's history,
resolve it, and revisit / filter the commented cells later. A thread is every
``cell_comments`` row sharing ``(tenant_id, deal_id, cell_key)``.

``cell_key`` is the cell's stable id — never a screen position:

* Historical P&L: ``hist:<document_id>::<field_name>`` — the extracted line
  the cell shows (the same id the historicals Excel round-trip carries in its
  hidden id columns, so a comment and an import talk about the same cell).
* Future P&L: ``proj:<engine>.years[<i>].<path>`` — the engine output path and
  the 0-based model-year index (the same root the lineage drawer opens).

Resolving stamps ``resolved_at`` / ``resolved_by``; nothing is ever deleted,
so the history survives. Every statement carries a ``tenant_id`` predicate
(pinned by ``tests/test_cell_comments_sql_tenant_predicates.py``) and every
route first checks the deal belongs to the caller's tenant (404 otherwise).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..auth import AuthContext, get_current_auth
from ..database import get_session
from .deals import _assert_deal_belongs_to_tenant

logger = logging.getLogger(__name__)
router = APIRouter()

#: ``hist:<uuid>::<field_name>`` or ``proj:<engine>.years[<i>].<path>``.
CELL_KEY_RE = re.compile(
    r"^(?:hist:[0-9a-fA-F-]{36}::\S.{0,300}"
    r"|proj:[a-z_]+\.years\[\d{1,3}\](?:\.[A-Za-z0-9_]+){1,4})$"
)


class CellComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    cell_key: str
    cell_label: str | None = None
    body: str
    author_id: str | None = None
    author_email: str | None = None
    created_at: datetime
    resolved_at: datetime | None = None
    resolved_by: str | None = None


class CreateCommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_key: Annotated[str, Field(min_length=6, max_length=400)]
    body: Annotated[str, Field(min_length=1, max_length=4000)]
    #: Human label shown in the "Commented cells" list ("Rooms Revenue · FY2023").
    cell_label: Annotated[str, Field(max_length=200)] | None = None

    @field_validator("cell_key")
    @classmethod
    def _key_shape(cls, v: str) -> str:
        if not CELL_KEY_RE.match(v):
            raise ValueError(
                "cell_key must be hist:<document_id>::<field_name> or "
                "proj:<engine>.years[<i>].<path>"
            )
        return v

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("comment body is blank")
        return v.strip()


class ResolveThreadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_key: Annotated[str, Field(min_length=6, max_length=400)]
    #: ``True`` resolves every open comment on the cell; ``False`` re-opens them.
    resolved: bool = True


class ResolveThreadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_key: str
    resolved: bool
    updated: int


def _coerce_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def _row_to_comment(m: Any) -> CellComment:
    return CellComment(
        id=UUID(str(m["id"])),
        deal_id=UUID(str(m["deal_id"])),
        cell_key=m["cell_key"],
        cell_label=m["cell_label"],
        body=m["body"],
        author_id=m["author_id"],
        author_email=m["author_email"],
        created_at=_coerce_dt(m["created_at"]) or datetime.now(UTC),
        resolved_at=_coerce_dt(m["resolved_at"]),
        resolved_by=m["resolved_by"],
    )


@router.get("/{deal_id}/comments", response_model=list[CellComment])
async def list_cell_comments(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    cell_key: str | None = None,
) -> list[CellComment]:
    """Every comment on the deal (oldest first), or one cell's thread.

    The web groups the flat list by ``cell_key`` for the markers, the thread
    panel and the "Commented cells" filter — one fetch per P&L view.
    """
    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    params: dict[str, Any] = {"deal": str(deal_id), "tenant": tenant}
    if cell_key:
        params["cell"] = cell_key
        stmt = text(
            "SELECT id, deal_id, cell_key, cell_label, body, author_id, author_email, "
            "created_at, resolved_at, resolved_by FROM cell_comments "
            "WHERE deal_id = :deal AND tenant_id = :tenant AND cell_key = :cell "
            "ORDER BY created_at ASC"
        )
    else:
        stmt = text(
            "SELECT id, deal_id, cell_key, cell_label, body, author_id, author_email, "
            "created_at, resolved_at, resolved_by FROM cell_comments "
            "WHERE deal_id = :deal AND tenant_id = :tenant "
            "ORDER BY created_at ASC LIMIT 5000"
        )
    rows = await session.execute(stmt, params)
    return [_row_to_comment(r._mapping) for r in rows.fetchall()]


@router.post(
    "/{deal_id}/comments",
    response_model=CellComment,
    status_code=status.HTTP_201_CREATED,
)
async def create_cell_comment(
    deal_id: UUID,
    body: CreateCommentBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> CellComment:
    """Add a comment to a cell's thread (creating the thread if new)."""
    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    cid = uuid4()
    now = datetime.now(UTC)
    await session.execute(
        text(
            """
            INSERT INTO cell_comments
                (id, tenant_id, deal_id, cell_key, cell_label, body,
                 author_id, author_email, created_at)
            VALUES
                (:id, :tenant, :deal, :cell, :label, :body,
                 :author_id, :author_email, :ts)
            """
        ),
        {
            "id": str(cid),
            "tenant": tenant,
            "deal": str(deal_id),
            "cell": body.cell_key,
            "label": body.cell_label,
            "body": body.body,
            "author_id": auth.user_id,
            "author_email": auth.email,
            "ts": now,
        },
    )
    try:
        await log_audit(
            session,
            tenant_id=tenant,
            actor_id=auth.user_id,
            actor_email=auth.email,
            action="cell_comment.created",
            resource_type="cell_comment",
            resource_id=str(cid),
            after={"cell_key": body.cell_key, "body": body.body},
            tags=["comment", "fon-41"],
            metadata={"deal_id": str(deal_id), "cell_key": body.cell_key},
            deal_id=str(deal_id),
        )
    except Exception:
        logger.warning("create_cell_comment: audit log failed", exc_info=True)
    await session.commit()
    return CellComment(
        id=cid,
        deal_id=deal_id,
        cell_key=body.cell_key,
        cell_label=body.cell_label,
        body=body.body,
        author_id=auth.user_id,
        author_email=auth.email,
        created_at=now,
    )


@router.post("/{deal_id}/comments/resolve", response_model=ResolveThreadResponse)
async def resolve_cell_thread(
    deal_id: UUID,
    body: ResolveThreadBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> ResolveThreadResponse:
    """Resolve (or re-open) a cell's whole thread. Rows are stamped, never deleted."""
    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    params: dict[str, Any] = {
        "deal": str(deal_id),
        "tenant": tenant,
        "cell": body.cell_key,
    }
    if body.resolved:
        params.update(ts=datetime.now(UTC), by=auth.email or auth.user_id)
        result = await session.execute(
            text(
                "UPDATE cell_comments SET resolved_at = :ts, resolved_by = :by "
                "WHERE deal_id = :deal AND tenant_id = :tenant "
                "AND cell_key = :cell AND resolved_at IS NULL"
            ),
            params,
        )
    else:
        result = await session.execute(
            text(
                "UPDATE cell_comments SET resolved_at = NULL, resolved_by = NULL "
                "WHERE deal_id = :deal AND tenant_id = :tenant "
                "AND cell_key = :cell AND resolved_at IS NOT NULL"
            ),
            params,
        )
    updated = int(result.rowcount or 0)
    if updated:
        try:
            await log_audit(
                session,
                tenant_id=tenant,
                actor_id=auth.user_id,
                actor_email=auth.email,
                action="cell_comment.resolved" if body.resolved else "cell_comment.reopened",
                resource_type="cell_comment",
                resource_id=body.cell_key,
                tags=["comment", "fon-41"],
                metadata={"deal_id": str(deal_id), "cell_key": body.cell_key},
                deal_id=str(deal_id),
            )
        except Exception:
            logger.warning("resolve_cell_thread: audit log failed", exc_info=True)
    await session.commit()
    return ResolveThreadResponse(cell_key=body.cell_key, resolved=body.resolved, updated=updated)


__all__ = ["CELL_KEY_RE", "router"]
