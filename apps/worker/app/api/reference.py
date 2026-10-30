"""Sam #6 / FON-96 — read-only reference data shared by every workspace.

``GET /reference/str-markets`` searches the market / submarket taxonomy list
(the ``str_markets`` table, loaded by ``scripts/load_str_markets.py``). It
returns ``[]`` until a file has been loaded — never a fabricated row.

Licensing gate (FON-96). The list behind the table is the CoStar geography
export, licensed vendor data. The route resolves the caller's tenant
registry row: an ``internal`` tenant always sees the rows; an ``external``
(or unregistered) tenant sees them only when BOTH the loaded dataset's
``licensed_for_external`` flag and the ``MARKET_TAXONOMY_SERVE_EXTERNAL``
setting are on — otherwise ``[]``, which the web app already treats as
"list not available" and falls back to typed entry over the public STR
market names. The route requires an authenticated caller
(``Depends(get_tenant_id)``) even though the data is not tenant-scoped.

The response keeps the original field names (``str_market_id`` /
``submarket_id`` …) for existing clients and adds ``market_key`` /
``submarket_key`` (aliases: the same values, named for what they now are —
Fondok taxonomy keys, not vendor ids), ``market_type`` and a display
``label``. The query itself carries no tenant predicate because
``str_markets`` has no ``tenant_id`` and no customer data (see
``app/services/str_markets.py``).
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..services.str_markets import StrMarketRow, may_serve_str_markets, search_str_markets
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


class StrMarketRecord(BaseModel):
    """One row of the taxonomy list: a market, or a submarket of it."""

    model_config = ConfigDict(extra="forbid")

    str_market_id: str
    market_name: str
    submarket_id: str | None = None
    submarket_name: str | None = None
    state: str | None = None
    country: str | None = None
    # FON-96 — ``metro`` / ``regional``; NULL on rows from the legacy format.
    market_type: str | None = None
    # FON-96 — the taxonomy keys under their own names (same values as the
    # two id columns). Never shown to the analyst as a vendor id.
    market_key: str
    submarket_key: str | None = None
    # What the picker shows beside the name — never the key.
    label: str

    @classmethod
    def from_row(cls, row: StrMarketRow) -> StrMarketRecord:
        return cls(
            str_market_id=row.str_market_id,
            market_name=row.market_name,
            submarket_id=row.submarket_id,
            submarket_name=row.submarket_name,
            state=row.state,
            country=row.country,
            market_type=row.market_type,
            market_key=row.str_market_id,
            submarket_key=row.submarket_id,
            label=row.label,
        )


@router.get("/str-markets", response_model=list[StrMarketRecord])
async def list_str_markets(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    q: Annotated[str | None, Query(max_length=120)] = None,
    str_market_id: Annotated[str | None, Query(max_length=200)] = None,
    market_key: Annotated[str | None, Query(max_length=200)] = None,
    kind: Annotated[Literal["market", "submarket"] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[StrMarketRecord]:
    """Rows whose market / submarket name, state or key contains ``q``
    (case-insensitive), optionally narrowed to one market (``market_key``,
    or its older alias ``str_market_id``) and to ``kind`` (``market`` →
    distinct markets; ``submarket`` → submarkets only). ``[]`` when nothing
    is loaded, or when the list is withheld from this workspace (licensing)."""
    served, why = await may_serve_str_markets(session, tenant_id)
    if not served:
        logger.info("reference.str_markets: withheld tenant=%s (%s)", tenant_id, why)
        return []
    rows = await search_str_markets(
        session,
        q=q,
        str_market_id=market_key or str_market_id,
        kind=kind,
        limit=limit,
    )
    return [StrMarketRecord.from_row(r) for r in rows]
