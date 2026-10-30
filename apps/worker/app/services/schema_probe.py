"""Catalog probe for columns the startup migration may not have added yet.

The worker reads several ``deals`` columns through "the column, when it
exists, else a NULL stand-in" so an un-migrated database (a redeploy racing
the migration, a local dev DB) reads NULL and skips writes instead of 500ing
(``api/deals.py`` for ``proposed_brand``, the market columns, the lifecycle
columns; ``services/pipeline.py`` for ``lifecycle``). This is the one probe
behind every such read: catalog introspection (never a trial SELECT — a
failed statement inside an open transaction poisons the session), any
failure answers False, memoised on ``session.info`` for the session's life.
"""

from __future__ import annotations

import contextlib
import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def table_has_column(
    session: AsyncSession, table: str, column: str, *, cache_key: str | None = None
) -> bool:
    """Does the live ``table`` carry ``column``?

    ``cache_key`` defaults to ``fondok_{table}_has_{column}``; callers that
    predate this module keep their historical keys so a session that was
    probed through the old helper answers the same.
    """
    key = cache_key or f"fondok_{table}_has_{column}"
    try:
        cached = session.info.get(key)
    except Exception:
        cached = None
    if isinstance(cached, bool):
        return cached
    try:
        is_sqlite = (
            session.bind is not None and session.bind.dialect.name == "sqlite"
        )
        if is_sqlite:
            rows = await session.execute(text(f"PRAGMA table_info({table})"))
            have = {str(r[1]).lower() for r in rows.fetchall()}
        else:
            rows = await session.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :table"
                ),
                {"table": table},
            )
            have = {str(r[0]).lower() for r in rows.fetchall()}
        answer = column.lower() in have
    except Exception:
        answer = False
    with contextlib.suppress(Exception):
        session.info[key] = answer
    return answer


__all__ = ["table_has_column"]
