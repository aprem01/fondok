"""Market overview + comp-set endpoints.

These routes are intentionally thin until a real STR/CoStar feed is
wired in — the full market-research pass is future work. To stay
useful in the meantime, ``GET /market/{deal_id}/overview`` now reads
the deal row and surfaces what we *do* know (city, keys, brand,
service) so the web app's market header can render with real data
instead of nulls.

The transaction-comps endpoint reads ``transaction_comps.<n>.*`` rows
out of the deal's extracted documents (OMs typically include comp
sales tables). Sam called these "critical for anchoring exit cap rate"
in his May 7 call summary — even when the OM only carries 3-5 comps
the exit cap conversation has anchors instead of feel.

The proper STR/CoStar integration will populate ``occupancy_index``,
``adr_index``, ``revpar_index``, and a comp-set list. Until then those
fields stay null and the comps endpoint returns an empty list so the
UI can render an "awaiting market data" empty state.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


class MarketOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    market: str | None = None
    keys: int | None = None
    brand: str | None = None
    service: str | None = None
    occupancy_index: float | None = None
    adr_index: float | None = None
    revpar_index: float | None = None


class Comp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    distance_miles: float | None = None
    keys: int | None = None
    chain_scale: str | None = None
    revpar: float | None = None


class CompsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    comps: list[Comp] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/{deal_id}/overview", response_model=MarketOverview)
async def market_overview(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> MarketOverview:
    """Pull the deal row and surface its market-relevant fields.

    Real STR-driven RevPAR/ADR/Occupancy indices are still future work
    (TODO(str-integration)); the indices stay null until the feed lands.
    The web app should render the city/keys/brand block from this
    response and treat null indices as "awaiting market data".
    """
    row = (
        await session.execute(
            text(
                """
                SELECT city, keys, brand, service
                  FROM deals
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )
    m = row._mapping
    keys: int | None = None
    if m.get("keys") is not None:
        try:
            keys = int(m["keys"])
        except (TypeError, ValueError):
            keys = None
    return MarketOverview(
        deal_id=deal_id,
        market=m.get("city"),
        keys=keys,
        brand=m.get("brand"),
        service=m.get("service"),
    )


@router.get("/{deal_id}/comps", response_model=CompsResponse)
async def market_comps(deal_id: UUID) -> CompsResponse:
    """Comp-set endpoint.

    TODO(str-integration): pull comp set from the STR/CoStar feed
    keyed off the deal's city. Until then we return an empty list +
    a metadata flag so the UI renders an "awaiting market data" panel
    rather than a blank page.
    """
    return CompsResponse(
        deal_id=deal_id,
        comps=[],
        metadata={"source": "stub", "awaiting_integration": "str-costar"},
    )


# ─────────────────────────── transaction comps ───────────────────────────


class TransactionCompEntry(BaseModel):
    """One comparable hotel sale parsed out of an OM's comp table."""

    model_config = ConfigDict(extra="forbid")

    name: str
    market: str | None = None
    sale_date: str | None = None  # ISO date string when known; free-form otherwise
    keys: int | None = None
    sale_price_usd: float | None = None
    price_per_key_usd: float | None = None
    cap_rate_pct: float | None = None
    buyer_name: str | None = None
    buyer_type: str | None = None
    source_document_id: str | None = None
    source_page: int | None = None


class TransactionCompsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    comps: list[TransactionCompEntry] = Field(default_factory=list)
    median_price_per_key: float | None = Field(
        default=None,
        description=(
            "Median $/key across the returned comps — the headline anchor "
            "for the exit-cap conversation. Null when fewer than 1 comp."
        ),
    )
    median_cap_rate_pct: float | None = None
    note: str | None = None


# Map extractor field paths to the canonical column on a comp entry.
# The extractor emits ``transaction_comps.<n>.<field>`` rows; ``<n>`` is
# 1-indexed in the order the comps appear in the source table. Field
# names are loose-matched (snake_case + alternate aliases).
_TXN_FIELD_ALIASES: dict[str, str] = {
    "name": "name",
    "hotel_name": "name",
    "property_name": "name",
    "market": "market",
    "city": "market",
    "submarket": "market",
    "sale_date": "sale_date",
    "date_of_sale": "sale_date",
    "transaction_date": "sale_date",
    "date": "sale_date",
    "keys": "keys",
    "rooms": "keys",
    "key_count": "keys",
    "sale_price": "sale_price_usd",
    "sale_price_usd": "sale_price_usd",
    "price": "sale_price_usd",
    "transaction_price": "sale_price_usd",
    "price_per_key": "price_per_key_usd",
    "price_per_key_usd": "price_per_key_usd",
    "ppk": "price_per_key_usd",
    "cap_rate": "cap_rate_pct",
    "cap_rate_pct": "cap_rate_pct",
    "going_in_cap": "cap_rate_pct",
    "buyer": "buyer_name",
    "buyer_name": "buyer_name",
    "purchaser": "buyer_name",
    "buyer_type": "buyer_type",
    "buyer_class": "buyer_type",
}


def _coerce_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(float(str(v).replace(",", "").strip().rstrip("%").rstrip("$")))
    except (TypeError, ValueError):
        return None


def _coerce_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        cleaned = (
            str(v).replace(",", "").replace("$", "").replace("%", "").strip()
        )
        return float(cleaned)
    except (TypeError, ValueError):
        return None


def _normalize_cap_rate(v: float | None) -> float | None:
    """Cap rates ship as both ``8.5`` and ``0.085``. Normalize to 0..30."""
    if v is None:
        return None
    if v <= 1.0:
        return v * 100.0
    return v


@router.get("/{deal_id}/transaction-comps", response_model=TransactionCompsResponse)
async def transaction_comps(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> TransactionCompsResponse:
    """Return comparable hotel sales the extractor pulled off this deal's docs.

    OMs almost always include a "Comparable Sales" table with hotel
    name, sale date, keys, sale price, $/key, and cap rate. The extractor
    emits each row as ``transaction_comps.<n>.<field>``. We aggregate
    them, derive median $/key + median cap rate, and return.
    """
    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT er.fields, er.document_id, d.tenant_id, d.doc_type
                      FROM extraction_results er
                      JOIN documents d ON d.id = er.document_id
                     WHERE er.deal_id = :deal
                       AND d.tenant_id = :tenant
                     ORDER BY er.created_at DESC
                    """
                ),
                {"deal": str(deal_id), "tenant": str(tenant_id)},
            )
        ).fetchall()
    except Exception:  # noqa: BLE001
        return TransactionCompsResponse(deal_id=deal_id, comps=[], note="db-error")

    # Aggregate rows from all docs into a per-index bucket. The first
    # extracted value for any given (n, field) wins so a later doc
    # doesn't clobber an OM's cleaner field unless the OM was empty.
    buckets: dict[int, dict[str, Any]] = {}
    sources: dict[int, tuple[str | None, int | None]] = {}

    for r in rows:
        raw = r._mapping["fields"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw, list):
            continue
        doc_id = str(r._mapping["document_id"]) if r._mapping["document_id"] else None
        for f in raw:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            if not name.startswith("transaction_comps."):
                continue
            try:
                _, idx_part, *rest = name.split(".")
                idx = int(idx_part)
                tail = ".".join(rest).lower()
            except (ValueError, IndexError):
                continue
            canonical = _TXN_FIELD_ALIASES.get(tail)
            if canonical is None:
                continue
            value = f.get("value")
            if value in (None, ""):
                continue
            entry = buckets.setdefault(idx, {})
            entry.setdefault(canonical, value)
            if idx not in sources:
                page = f.get("source_page")
                sources[idx] = (
                    doc_id,
                    int(page) if isinstance(page, (int, float)) else None,
                )

    # Materialize TransactionCompEntry rows.
    comps: list[TransactionCompEntry] = []
    for idx in sorted(buckets):
        b = buckets[idx]
        name = b.get("name")
        if not name or not isinstance(name, str):
            continue
        keys_int = _coerce_int(b.get("keys"))
        sale_price = _coerce_float(b.get("sale_price_usd"))
        ppk = _coerce_float(b.get("price_per_key_usd"))
        if ppk is None and sale_price is not None and keys_int and keys_int > 0:
            ppk = round(sale_price / keys_int, 2)
        cap = _normalize_cap_rate(_coerce_float(b.get("cap_rate_pct")))
        sale_date = b.get("sale_date")
        if sale_date is not None and not isinstance(sale_date, str):
            try:
                sale_date = str(sale_date)
            except Exception:  # noqa: BLE001
                sale_date = None
        market = b.get("market")
        if market is not None and not isinstance(market, str):
            market = str(market)
        buyer_name = b.get("buyer_name")
        if buyer_name is not None and not isinstance(buyer_name, str):
            buyer_name = str(buyer_name)
        buyer_type = b.get("buyer_type")
        if buyer_type is not None and not isinstance(buyer_type, str):
            buyer_type = str(buyer_type)

        doc_id, page = sources.get(idx, (None, None))
        comps.append(
            TransactionCompEntry(
                name=str(name).strip(),
                market=market,
                sale_date=sale_date,
                keys=keys_int,
                sale_price_usd=sale_price,
                price_per_key_usd=ppk,
                cap_rate_pct=cap,
                buyer_name=buyer_name,
                buyer_type=buyer_type,
                source_document_id=doc_id,
                source_page=page,
            )
        )

    # Headline anchors — median $/key + median cap rate.
    def _median(xs: list[float]) -> float | None:
        if not xs:
            return None
        s = sorted(xs)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    median_ppk = _median([c.price_per_key_usd for c in comps if c.price_per_key_usd])
    median_cap = _median([c.cap_rate_pct for c in comps if c.cap_rate_pct])

    note: str | None = None
    if not comps:
        note = (
            "No transaction comps extracted yet. Upload an OM with a "
            "'Comparable Sales' table to populate this view."
        )

    return TransactionCompsResponse(
        deal_id=deal_id,
        comps=comps,
        median_price_per_key=median_ppk,
        median_cap_rate_pct=median_cap,
        note=note,
    )
