"""Sam #6 / FON-96 — the market / submarket taxonomy list (reference data).

The ``str_markets`` table holds the market and submarket definitions Fondok
offers in the Market / Submarket pickers. Two loader formats feed it
(``scripts/load_str_markets.py``):

* the original STR CSV with vendor ids (``str_market_id`` / ``submarket_id``
  copied verbatim);
* FON-96 — the CoStar geography export, which carries NO vendor ids. Fondok
  mints deterministic *taxonomy keys* from the names and stores them in the
  same two columns, whose meaning widens to "taxonomy key"::

      market_key    = 'costar:' + slug(market_name)         costar:northhaven-ts-usa
      submarket_key = market_key + '/' + slug(submarket)    costar:northhaven-ts-usa/riverside

  ``slug`` = NFKD → ASCII, lowercase, ``[^a-z0-9]+`` → ``-``, trimmed. The
  key is never presented as a vendor identifier; the UI hides it.

Until a file is loaded the table is empty and every reader here answers
"nothing loaded": the search returns ``[]``, a key lookup returns ``None``.
Nothing in this module ever invents a key, a market or a submarket name.

Licensing (FON-96): the CoStar export must not reach an external workspace
until CoStar confirms the licence. :func:`may_serve_str_markets` resolves
the caller's registry row — an ``internal`` tenant always sees rows; an
``external`` (or unregistered) tenant only when BOTH the loaded dataset's
``licensed_for_external`` flag and ``MARKET_TAXONOMY_SERVE_EXTERNAL`` are on.

Why the reads below carry no ``tenant_id`` predicate: ``str_markets`` and
``reference_datasets`` are vendor reference data, identical for every
workspace, with no ``tenant_id`` column and no customer or deal data in them
(see the DDL comments in ``app/migrations.py``; both are listed as
intentionally unscoped in ``app/tenant_middleware.py``). A deal's chosen
market is stored on the tenant-scoped ``deals`` row, never here.

Every reader probes the catalog first, so a database that predates the
migrations answers "nothing loaded" instead of 500ing.
"""

from __future__ import annotations

import contextlib
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_TABLE_CACHE_KEY = "fondok_has_str_markets_table"
_DATASET_TABLE_CACHE_KEY = "fondok_has_reference_datasets_table"

#: The legacy STR CSV header the loader requires, in order.
CSV_COLUMNS: tuple[str, ...] = (
    "str_market_id",
    "market_name",
    "submarket_id",
    "submarket_name",
    "state",
    "country",
)

#: FON-96 — the prefix of every key minted from the CoStar geography export.
MARKET_KEY_PREFIX = "costar:"
#: ``str_markets.source`` for rows loaded from the CoStar geography export.
SOURCE_COSTAR_GEOGRAPHY = "costar_geography_export"
#: ``reference_datasets.dataset`` for this table.
DATASET_NAME = "str_markets"

MarketType = Literal["metro", "regional"]
MARKET_TYPES: frozenset[str] = frozenset({"metro", "regional"})

#: A CoStar metro market is named ``"<City> - <ST> USA"``; a regional market
#: is named in words ("<Region> USA"). Anchored, so a name has to match the
#: whole pattern to count as metro.
_METRO_NAME_RE = re.compile(r"^(?P<city>.+?) - (?P<state>[A-Z]{2}) USA$")


@dataclass(frozen=True)
class StrMarketRow:
    str_market_id: str
    market_name: str
    submarket_id: str | None
    submarket_name: str | None
    state: str | None
    country: str | None
    # FON-96 — NULL on rows loaded by the legacy STR format.
    market_type: str | None = None
    source: str | None = None
    dataset_version: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def label(self) -> str:
        """What the picker shows: ``"<market> · Metro market"`` for a market
        row, ``"<submarket> · submarket of <market>"`` for a submarket row.
        Never the key."""
        if self.submarket_id:
            return f"{self.submarket_name} · submarket of {self.market_name}"
        if self.market_type == "metro":
            return f"{self.market_name} · Metro market"
        if self.market_type == "regional":
            return f"{self.market_name} · Regional market"
        return f"{self.market_name} · Market"


@dataclass(frozen=True)
class ReferenceDataset:
    dataset: str
    version: str
    source_sha256: str
    row_count: int
    market_count: int | None
    submarket_count: int | None
    state_count: int | None
    licensed_for_external: bool
    licence_note: str | None
    loaded_at: Any
    loaded_by: str | None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        loaded = d.get("loaded_at")
        if loaded is not None and not isinstance(loaded, str):
            with contextlib.suppress(Exception):
                d["loaded_at"] = loaded.isoformat()
        return d


# ─────────────────────────── keys ───────────────────────────


def slugify(name: str) -> str:
    """NFKD → ASCII, lowercase, runs of anything but ``[a-z0-9]`` → ``-``,
    trimmed. Deterministic: the same name always gives the same slug."""
    decomposed = unicodedata.normalize("NFKD", str(name or ""))
    # Accents fold onto their base letter; any other non-ASCII character
    # (a curly apostrophe, a dash variant) is a separator like its ASCII
    # cousin, so the curly and the straight apostrophe give the same slug.
    ascii_name = "".join(
        ch if ord(ch) < 128 else ("" if unicodedata.combining(ch) else "-")
        for ch in decomposed
    ).lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


def market_key(market_name: str) -> str:
    """``costar:<slug(market_name)>``."""
    return f"{MARKET_KEY_PREFIX}{slugify(market_name)}"


def submarket_key(market_name: str, submarket_name: str) -> str:
    """``<market_key>/<slug(submarket_name)>`` — the same submarket name under
    two markets gives two different keys."""
    return f"{market_key(market_name)}/{slugify(submarket_name)}"


def infer_market_type(market_name: str) -> MarketType:
    """``metro`` when the name is a ``"<City> - <ST> USA"`` market name, else
    ``regional`` (a state-area market named in words)."""
    return "metro" if _METRO_NAME_RE.match(" ".join(str(market_name or "").split())) else "regional"


def metro_name_state(market_name: str) -> str | None:
    """The ``ST`` of a metro market name, or ``None`` for a regional name."""
    m = _METRO_NAME_RE.match(" ".join(str(market_name or "").split()))
    return m.group("state") if m else None


def row_key(str_market_id: str, submarket_id: str | None) -> str:
    """Primary key of a reference row: ``<market id>|<submarket id or ''>``
    (legacy STR format). The CoStar format uses the taxonomy key itself —
    see :func:`primary_key`."""
    return f"{str_market_id}|{submarket_id or ''}"


def primary_key(row: StrMarketRow) -> str:
    """``str_markets.id`` for ``row``: the submarket key / market key when the
    row carries taxonomy keys, the legacy ``<id>|<id>`` form otherwise."""
    if row.str_market_id.startswith(MARKET_KEY_PREFIX):
        return row.submarket_id or row.str_market_id
    return row_key(row.str_market_id, row.submarket_id)


# ─────────────────────────── catalog probes ───────────────────────────


async def _table_exists(session: AsyncSession, table: str, cache_key: str) -> bool:
    try:
        cached = session.info.get(cache_key)
    except Exception:
        cached = None
    if isinstance(cached, bool):
        return cached
    try:
        is_sqlite = session.bind is not None and session.bind.dialect.name == "sqlite"
        if is_sqlite:
            sql = "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :t"
        else:
            sql = "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
        answer = (await session.execute(text(sql), {"t": table})).first() is not None
    except Exception:
        answer = False
    with contextlib.suppress(Exception):
        session.info[cache_key] = answer
    return answer


async def has_str_markets_table(session: AsyncSession) -> bool:
    """Does the live DB carry ``str_markets``? Catalog introspection (a
    failed SELECT would poison an open transaction); memoised per session."""
    return await _table_exists(session, "str_markets", _TABLE_CACHE_KEY)


async def has_reference_datasets_table(session: AsyncSession) -> bool:
    return await _table_exists(session, "reference_datasets", _DATASET_TABLE_CACHE_KEY)


async def has_market_type_column(session: AsyncSession) -> bool:
    """FON-96 — does ``str_markets`` carry the three additive columns
    (``market_type`` / ``source`` / ``dataset_version``; one migration batch)?"""
    if not await has_str_markets_table(session):
        return False
    from .schema_probe import table_has_column

    return await table_has_column(session, "str_markets", "market_type")


def _row(m: Any) -> StrMarketRow:
    return StrMarketRow(
        str_market_id=str(m["str_market_id"]),
        market_name=str(m["market_name"]),
        submarket_id=m.get("submarket_id") or None,
        submarket_name=m.get("submarket_name") or None,
        state=m.get("state") or None,
        country=m.get("country") or None,
        market_type=m.get("market_type") or None,
        source=m.get("source") or None,
        dataset_version=m.get("dataset_version") or None,
    )


async def _extra_cols(session: AsyncSession) -> str:
    """The FON-96 columns, or NULL stand-ins on a schema without them."""
    if await has_market_type_column(session):
        return "market_type, source, dataset_version"
    return "NULL AS market_type, NULL AS source, NULL AS dataset_version"


# ─────────────────────────── reads ───────────────────────────


async def search_str_markets(
    session: AsyncSession,
    *,
    q: str | None = None,
    str_market_id: str | None = None,
    kind: Literal["market", "submarket"] | None = None,
    limit: int = 50,
) -> list[StrMarketRow]:
    """Rows whose market / submarket name, state or key contains ``q``
    (case-insensitive), optionally narrowed to one market key.

    ``kind="market"`` returns one row per market (``submarket_id`` NULL —
    grouped, so a list loaded without market-level rows still answers);
    ``kind="submarket"`` returns submarket rows only. Ranking: a name that
    STARTS with ``q`` before one that merely contains it, then by name.
    ``[]`` when the table is absent or empty.
    """
    if not await has_str_markets_table(session):
        return []
    where: list[str] = []
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
    needle = (q or "").strip().lower()
    prefix_rank = ""
    if needle:
        params["q"] = f"%{needle}%"
        params["qp"] = f"{needle}%"
        if kind == "market":
            where.append(
                "(LOWER(market_name) LIKE :q OR LOWER(COALESCE(state, '')) LIKE :q "
                "OR LOWER(str_market_id) LIKE :q)"
            )
            prefix_rank = "CASE WHEN LOWER(market_name) LIKE :qp THEN 0 ELSE 1 END"
        else:
            where.append(
                "(LOWER(market_name) LIKE :q OR LOWER(COALESCE(submarket_name, '')) LIKE :q "
                "OR LOWER(COALESCE(state, '')) LIKE :q OR LOWER(str_market_id) LIKE :q "
                "OR LOWER(COALESCE(submarket_id, '')) LIKE :q)"
            )
            prefix_rank = (
                "CASE WHEN LOWER(COALESCE(submarket_name, market_name)) LIKE :qp THEN 0 ELSE 1 END"
            )
    if str_market_id:
        where.append("str_market_id = :mid")
        params["mid"] = str_market_id
    if kind == "submarket":
        where.append("submarket_id IS NOT NULL")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rank = f"{prefix_rank}, " if prefix_rank else ""
    extra = await _extra_cols(session)
    # Reference data — no tenant predicate by design (module docstring).
    if kind == "market":
        if "NULL AS" in extra:
            grouped_extra = extra
        else:
            grouped_extra = (
                "MIN(market_type) AS market_type, MIN(source) AS source, "
                "MIN(dataset_version) AS dataset_version"
            )
        sql = f"""
            SELECT str_market_id, market_name, NULL AS submarket_id, NULL AS submarket_name,
                   MIN(state) AS state, MIN(country) AS country, {grouped_extra}
              FROM str_markets
              {clause}
             GROUP BY str_market_id, market_name
             ORDER BY {rank}market_name
             LIMIT :limit
        """
    else:
        sql = f"""
            SELECT str_market_id, market_name, submarket_id, submarket_name,
                   state, country, {extra}
              FROM str_markets
              {clause}
             ORDER BY {rank}market_name, COALESCE(submarket_name, '')
             LIMIT :limit
        """
    rows = await session.execute(text(sql), params)
    return [_row(r._mapping) for r in rows.fetchall()]


async def get_str_market(session: AsyncSession, str_market_id: str) -> StrMarketRow | None:
    """The market-level row of market ``str_market_id`` (its market name),
    or ``None`` when that key is not in the loaded list / nothing is loaded."""
    if not str_market_id or not await has_str_markets_table(session):
        return None
    extra = await _extra_cols(session)
    row = (
        await session.execute(
            text(
                f"""
                SELECT str_market_id, market_name, submarket_id, submarket_name,
                       state, country, {extra}
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


async def get_str_submarket(session: AsyncSession, submarket_id: str) -> StrMarketRow | None:
    """FON-96 — the submarket row with key ``submarket_id`` (its market key
    is ``row.str_market_id``), or ``None``."""
    if not submarket_id or not await has_str_markets_table(session):
        return None
    extra = await _extra_cols(session)
    row = (
        await session.execute(
            text(
                f"""
                SELECT str_market_id, market_name, submarket_id, submarket_name,
                       state, country, {extra}
                  FROM str_markets
                 WHERE submarket_id = :sid
                 LIMIT 1
                """
            ),
            {"sid": submarket_id},
        )
    ).first()
    return _row(row._mapping) if row is not None else None


async def get_reference_dataset(
    session: AsyncSession, dataset: str = DATASET_NAME
) -> ReferenceDataset | None:
    """The ``reference_datasets`` row for ``dataset``, or ``None`` (not loaded
    through a versioned load, or the table predates the migration)."""
    if not await has_reference_datasets_table(session):
        return None
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT dataset, version, source_sha256, row_count, market_count,
                           submarket_count, state_count, licensed_for_external,
                           licence_note, loaded_at, loaded_by
                      FROM reference_datasets
                     WHERE dataset = :d
                    """
                ),
                {"d": dataset},
            )
        ).first()
    except Exception:
        logger.warning("str_markets: reference_datasets read failed", exc_info=True)
        return None
    if row is None:
        return None
    m = row._mapping
    return ReferenceDataset(
        dataset=str(m["dataset"]),
        version=str(m["version"]),
        source_sha256=str(m["source_sha256"]),
        row_count=int(m["row_count"] or 0),
        market_count=int(m["market_count"]) if m.get("market_count") is not None else None,
        submarket_count=int(m["submarket_count"]) if m.get("submarket_count") is not None else None,
        state_count=int(m["state_count"]) if m.get("state_count") is not None else None,
        licensed_for_external=bool(m.get("licensed_for_external")),
        licence_note=m.get("licence_note") or None,
        loaded_at=m.get("loaded_at"),
        loaded_by=m.get("loaded_by") or None,
    )


async def count_str_markets(session: AsyncSession) -> int:
    """Rows in ``str_markets`` (0 when the table is absent)."""
    if not await has_str_markets_table(session):
        return 0
    try:
        return int((await session.execute(text("SELECT COUNT(*) FROM str_markets"))).scalar() or 0)
    except Exception:
        return 0


# ─────────────────────────── licensing gate ───────────────────────────


async def external_serving_enabled(session: AsyncSession) -> tuple[bool, bool, bool]:
    """``(served, setting_on, dataset_licensed)`` for an EXTERNAL caller:
    served only when both switches are on."""
    from ..config import get_settings

    setting_on = bool(get_settings().MARKET_TAXONOMY_SERVE_EXTERNAL)
    dataset = await get_reference_dataset(session)
    licensed = bool(dataset and dataset.licensed_for_external)
    return setting_on and licensed, setting_on, licensed


async def may_serve_str_markets(session: AsyncSession, tenant_id: UUID | str) -> tuple[bool, str]:
    """May the list be served to ``tenant_id``? ``(True, "internal")`` for a
    registered internal tenant; for anyone else ``(True, "external_licensed")``
    only when both licensing switches are on, otherwise ``(False, <why>)``.
    A tenant with no registry row is treated as external (closed)."""
    from ..tenants import get_tenant

    tenant = None
    with contextlib.suppress(Exception):
        tenant = await get_tenant(tenant_id)
    if tenant is not None and tenant.is_internal:
        return True, "internal"
    served, setting_on, licensed = await external_serving_enabled(session)
    if served:
        return True, "external_licensed"
    if not licensed and not setting_on:
        return False, "withheld_unlicensed_and_disabled"
    if not licensed:
        return False, "withheld_dataset_not_licensed"
    return False, "withheld_setting_off"


__all__ = [
    "CSV_COLUMNS",
    "DATASET_NAME",
    "MARKET_KEY_PREFIX",
    "MARKET_TYPES",
    "SOURCE_COSTAR_GEOGRAPHY",
    "ReferenceDataset",
    "StrMarketRow",
    "count_str_markets",
    "external_serving_enabled",
    "get_reference_dataset",
    "get_str_market",
    "get_str_submarket",
    "has_market_type_column",
    "has_reference_datasets_table",
    "has_str_markets_table",
    "infer_market_type",
    "market_key",
    "may_serve_str_markets",
    "metro_name_state",
    "primary_key",
    "row_key",
    "search_str_markets",
    "slugify",
    "submarket_key",
]
