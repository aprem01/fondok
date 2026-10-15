"""Sam #6 — STR's market / submarket definition list (reference data).

The ``str_markets`` table holds STR's own market and submarket definitions,
loaded from STR's file by ``scripts/load_str_markets.py``. Until that file
arrives the table is empty and every reader here answers "nothing loaded":
the search returns ``[]``, an id lookup returns ``None``. Nothing in this
module ever invents an STR identifier or a submarket name.

Why the reads below carry no ``tenant_id`` predicate: ``str_markets`` is
vendor reference data, identical for every workspace, with no ``tenant_id``
column and no customer or deal data in it (see the DDL comment in
``app/migrations.py``; it is listed as intentionally unscoped in
``app/tenant_middleware.py``). A deal's chosen market is stored on the
tenant-scoped ``deals`` row, never here.

Every reader probes the catalog first, so a database that predates the
migration answers "nothing loaded" instead of 500ing.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_TABLE_CACHE_KEY = "fondok_has_str_markets_table"

#: The CSV header the loader requires, in order. Also the response shape.
CSV_COLUMNS: tuple[str, ...] = (
    "str_market_id",
    "market_name",
    "submarket_id",
    "submarket_name",
    "state",
    "country",
)


@dataclass(frozen=True)
class StrMarketRow:
    str_market_id: str
    market_name: str
    submarket_id: str | None
    submarket_name: str | None
    state: str | None
    country: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def row_key(str_market_id: str, submarket_id: str | None) -> str:
    """Primary key of a reference row: ``<market id>|<submarket id or ''>``."""
    return f"{str_market_id}|{submarket_id or ''}"


async def has_str_markets_table(session: AsyncSession) -> bool:
    """Does the live DB carry ``str_markets``? Catalog introspection (a
    failed SELECT would poison an open transaction); memoised per session."""
    try:
        cached = session.info.get(_TABLE_CACHE_KEY)
    except Exception:
        cached = None
    if isinstance(cached, bool):
        return cached
    try:
        is_sqlite = session.bind is not None and session.bind.dialect.name == "sqlite"
        if is_sqlite:
            sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'str_markets'"
        else:
            sql = (
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_name = 'str_markets'"
            )
        answer = (await session.execute(text(sql))).first() is not None
    except Exception:
        answer = False
    with contextlib.suppress(Exception):
        session.info[_TABLE_CACHE_KEY] = answer
    return answer


def _row(m: Any) -> StrMarketRow:
    return StrMarketRow(
        str_market_id=str(m["str_market_id"]),
        market_name=str(m["market_name"]),
        submarket_id=m.get("submarket_id") or None,
        submarket_name=m.get("submarket_name") or None,
        state=m.get("state") or None,
        country=m.get("country") or None,
    )


async def search_str_markets(
    session: AsyncSession,
    *,
    q: str | None = None,
    str_market_id: str | None = None,
    limit: int = 50,
) -> list[StrMarketRow]:
    """Rows whose market / submarket name, state or id contains ``q``
    (case-insensitive), optionally narrowed to one STR market. ``[]`` when
    the table is absent or empty."""
    if not await has_str_markets_table(session):
        return []
    where: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
    needle = (q or "").strip().lower()
    if needle:
        where.append(
            "(LOWER(market_name) LIKE :q OR LOWER(COALESCE(submarket_name, '')) LIKE :q "
            "OR LOWER(COALESCE(state, '')) LIKE :q OR LOWER(str_market_id) LIKE :q "
            "OR LOWER(COALESCE(submarket_id, '')) LIKE :q)"
        )
        params["q"] = f"%{needle}%"
    if str_market_id:
        where.append("str_market_id = :mid")
        params["mid"] = str_market_id
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    # Reference data — no tenant predicate by design (module docstring).
    rows = await session.execute(
        text(
            f"""
            SELECT str_market_id, market_name, submarket_id, submarket_name,
                   state, country
              FROM str_markets
              {clause}
             ORDER BY market_name, COALESCE(submarket_name, '')
             LIMIT :limit
            """
        ),
        params,
    )
    return [_row(r._mapping) for r in rows.fetchall()]


async def get_str_market(session: AsyncSession, str_market_id: str) -> StrMarketRow | None:
    """One row of STR market ``str_market_id`` (its market-level name), or
    ``None`` when that id is not in the loaded list / nothing is loaded."""
    if not str_market_id or not await has_str_markets_table(session):
        return None
    row = (
        await session.execute(
            text(
                """
                SELECT str_market_id, market_name, submarket_id, submarket_name,
                       state, country
                  FROM str_markets
                 WHERE str_market_id = :mid
                 ORDER BY COALESCE(submarket_id, '')
                 LIMIT 1
                """
            ),
            {"mid": str_market_id},
        )
    ).first()
    return _row(row._mapping) if row is not None else None


__all__ = [
    "CSV_COLUMNS",
    "StrMarketRow",
    "get_str_market",
    "has_str_markets_table",
    "row_key",
    "search_str_markets",
]
