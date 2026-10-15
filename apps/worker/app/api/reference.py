"""Sam #6 — read-only reference data shared by every workspace.

``GET /reference/str-markets`` searches STR's market / submarket definition
list (the ``str_markets`` table, loaded by ``scripts/load_str_markets.py``).
It returns ``[]`` until STR's file has been loaded — never a fabricated row.

The route requires an authenticated caller (``Depends(get_tenant_id)``) even
though the data is not tenant-scoped: the reference list is licensed vendor
data, so it is served only to signed-in workspaces. The query itself carries
no tenant predicate because ``str_markets`` has no ``tenant_id`` and no
customer data (see ``app/services/str_markets.py``).
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..services.str_markets import search_str_markets
from .deals import get_tenant_id

router = APIRouter()


class StrMarketRecord(BaseModel):
    """One row of STR's definition list: a market, or a submarket of it."""

    model_config = ConfigDict(extra="forbid")

    str_market_id: str
    market_name: str
    submarket_id: str | None = None
    submarket_name: str | None = None
    state: str | None = None
    country: str | None = None


@router.get("/str-markets", response_model=list[StrMarketRecord])
async def list_str_markets(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],  # noqa: ARG001 — auth gate
    q: Annotated[str | None, Query(max_length=120)] = None,
    str_market_id: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> list[StrMarketRecord]:
    """Rows whose market / submarket name, state or STR id contains ``q``
    (case-insensitive), optionally narrowed to one STR market. ``[]`` when
    no STR definition list has been loaded."""
    rows = await search_str_markets(
        session, q=q, str_market_id=str_market_id, limit=limit
    )
    return [StrMarketRecord(**r.as_dict()) for r in rows]
