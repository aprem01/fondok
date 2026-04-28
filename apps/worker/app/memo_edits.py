"""Memo-section edit history service.

Institutional LPs (Blackstone, Brookfield, …) treat IC-memo revisions
as a regulated change-log: every save of a section produces an
append-only row capturing who edited what, with both the original and
replacement bodies. We never UPDATE or DELETE — operators can only
insert new edits.

The API layer (:mod:`app.api.deals`) shells out to this module for both
the write path (:func:`record_edit`) and the read path
(:func:`list_edits`), keeping the routing thin.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


async def record_edit(
    session: AsyncSession,
    *,
    tenant_id: str | UUID,
    deal_id: str | UUID,
    section_id: str,
    actor_id: str | UUID,
    original_body: str,
    new_body: str,
    comment: str | None = None,
) -> UUID:
    """Append a ``memo_edits`` row and return its id.

    The caller owns commit semantics — we ``flush`` so the row is
    staged inside whatever transaction is in flight. ``record_edit``
    co-exists with :func:`app.audit.log_audit` so a failed audit write
    rolls back the edit too.

    Returns
    -------
    UUID
        The new edit's id.
    """
    edit_id = uuid4()
    now = datetime.now(UTC)

    await session.execute(
        text(
            """
            INSERT INTO memo_edits (
                id, tenant_id, deal_id, section_id, actor_id,
                original_body, new_body, comment, created_at
            ) VALUES (
                :id, :tenant, :deal, :sid, :actor,
                :orig, :new, :comment, :ts
            )
            """
        ),
        {
            "id": str(edit_id),
            "tenant": str(tenant_id),
            "deal": str(deal_id),
            "sid": section_id,
            "actor": str(actor_id),
            "orig": original_body,
            "new": new_body,
            "comment": comment,
            "ts": now,
        },
    )
    await session.flush()
    return edit_id


async def list_edits(
    session: AsyncSession,
    *,
    deal_id: str | UUID,
    section_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return chronological edit history for a deal (newest first).

    Pass ``section_id`` to scope to a single section (e.g. when the UI
    is showing the revision history for one part of the memo).
    """
    params: dict[str, Any] = {"deal": str(deal_id)}
    where = "deal_id = :deal"
    if section_id is not None:
        where += " AND section_id = :sid"
        params["sid"] = section_id

    rows = await session.execute(
        text(
            f"""
            SELECT id, tenant_id, deal_id, section_id, actor_id,
                   original_body, new_body, comment, created_at
              FROM memo_edits
             WHERE {where}
             ORDER BY created_at DESC, id DESC
            """
        ),
        params,
    )

    out: list[dict[str, Any]] = []
    for r in rows.fetchall():
        m = r._mapping
        out.append(
            {
                "id": str(m["id"]),
                "tenant_id": str(m["tenant_id"]),
                "deal_id": str(m["deal_id"]),
                "section_id": m["section_id"],
                "actor_id": m["actor_id"],
                "original_body": m["original_body"],
                "new_body": m["new_body"],
                "comment": m["comment"],
                "created_at": _coerce_dt(m["created_at"]).isoformat(),
            }
        )
    return out


__all__ = ["list_edits", "record_edit"]
