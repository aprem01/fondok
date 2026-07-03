"""Pipeline view aggregator (Wave 3 W3.5).

Builds a per-deal snapshot of headline underwriting metrics for the
multi-deal Pipeline page (``GET /deals/pipeline``).

Design notes
------------
* **Latest-run-per-engine join.** Each engine writes one row per run.
  We need the most-recent row PER (deal_id, engine_name) and we need
  it for several engines at once (returns / debt / capital / expense).
  The straightforward window-function approach
  ``ROW_NUMBER() OVER (PARTITION BY deal_id, engine_name ORDER BY
  started_at DESC) = 1`` is portable between Postgres and SQLite
  (SQLite supports window functions since 3.25, well before our
  bundled 3.39+).
* **Cache.** A 60-second in-process LRU keyed by tenant prevents the
  same query from running on every click — analysts open the
  Pipeline view dozens of times an hour. Mutations on the deal /
  engine pipeline invalidate via ``invalidate(tenant_id)``.
* **Filtering + sorting** run in Python after the SQL pull. The pull
  itself is a single tenant-scoped query that returns every deal +
  its latest-run rollup, which keeps the SQL simple and lets the
  rest of the dispatcher run on a small list (analysts rarely have
  > 500 active deals in a tenant).
* **No fixture fallbacks.** Empty engine rows degrade to NULL so the
  UI can dash the cell — we never substitute a Kimpton seed here.
"""

from __future__ import annotations

import json
import logging
import statistics
import time
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# 60-second cache TTL — enough to absorb a click-storm without serving
# stale numbers after a Run-Model finish (which always invalidates).
_CACHE_TTL_SECONDS = 60.0
# tenant_id_str → (expires_at, snapshot)
_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}

# Valid sort tokens. Keep the registry tight so unknown sort params
# return a clear 400 rather than silently no-op'ing the order.
SORT_KEYS: dict[str, tuple[str, bool]] = {
    # token → (row_key, reverse?)
    "irr_desc": ("levered_irr", True),
    "irr_asc": ("levered_irr", False),
    "em_desc": ("equity_multiple", True),
    "em_asc": ("equity_multiple", False),
    "per_key_asc": ("price_per_key", False),
    "per_key_desc": ("price_per_key", True),
    "cap_rate_asc": ("exit_cap_rate", False),
    "cap_rate_desc": ("exit_cap_rate", True),
    "noi_y1_desc": ("noi_y1", True),
    "noi_y1_asc": ("noi_y1", False),
    "name_asc": ("name", False),
    "name_desc": ("name", True),
    "last_activity_desc": ("last_activity_at", True),
    "last_activity_asc": ("last_activity_at", False),
}

DEFAULT_SORT = "last_activity_desc"


def invalidate(tenant_id: UUID | str | None = None) -> None:
    """Drop the cache. Call after engine runs / deal mutations.

    ``tenant_id=None`` clears every tenant — useful in tests where the
    autouse DB reset shouldn't leak cached rows into the next test.
    """
    if tenant_id is None:
        _CACHE.clear()
        return
    _CACHE.pop(str(tenant_id), None)


def _decode(value: Any) -> Any:
    """JSONB columns: dicts on Postgres, JSON strings on SQLite."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # NaN / Inf — pretend missing rather than poison percentile math.
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _coerce_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _row_from_deal(
    deal_row: dict[str, Any],
    engines_by_name: dict[str, dict[str, Any]],
    doc_count: int,
) -> dict[str, Any]:
    """Project a single deal + its latest-run engine rollup into the
    PipelineDealRow shape (kept as a plain dict so we can sort/filter
    in pure Python before Pydantic-validating in the route layer).
    """
    returns_out = _decode(engines_by_name.get("returns", {}).get("outputs"))
    debt_out = _decode(engines_by_name.get("debt", {}).get("outputs"))
    expense_out = _decode(engines_by_name.get("expense", {}).get("outputs"))
    capital_in = _decode(engines_by_name.get("capital", {}).get("inputs"))
    capital_out = _decode(engines_by_name.get("capital", {}).get("outputs"))

    # Pull each metric defensively — engines may have partial state.
    levered_irr = (
        _coerce_float(returns_out.get("levered_irr")) if returns_out else None
    )
    equity_multiple = (
        _coerce_float(returns_out.get("equity_multiple"))
        if returns_out
        else None
    )
    # Y1 DSCR comes off the debt schedule's first year. ``avg_dscr`` is
    # the engine-level rollup; we expose Y1 specifically because analysts
    # gate go/no-go on Y1 coverage before they care about the average.
    dscr_y1: float | None = None
    if debt_out and isinstance(debt_out.get("schedule"), list) and debt_out["schedule"]:
        dscr_y1 = _coerce_float(debt_out["schedule"][0].get("dscr"))

    # NOI Y1 + stabilized — Y1 is the first projected year; stabilized
    # is the final hold year (Year N). Both come from the expense engine.
    noi_y1: float | None = None
    noi_stabilized: float | None = None
    if expense_out and isinstance(expense_out.get("years"), list) and expense_out["years"]:
        years = expense_out["years"]
        # Prefer ``noi_institutional`` (cap-rate basis) when present —
        # legacy rows fall back to ``noi``.
        first = years[0]
        last = years[-1]
        noi_y1 = (
            _coerce_float(first.get("noi_institutional"))
            or _coerce_float(first.get("noi"))
        )
        noi_stabilized = (
            _coerce_float(last.get("noi_institutional"))
            or _coerce_float(last.get("noi"))
        )

    # Exit cap rate is an INPUT to the returns engine — surface it from
    # capital_in.assumptions if the returns inputs aren't around, but
    # the engine_runner stamps ``assumptions.exit_cap_rate`` onto the
    # returns inputs blob directly. Look there first.
    returns_in = _decode(engines_by_name.get("returns", {}).get("inputs"))
    exit_cap_rate: float | None = None
    if returns_in:
        assumptions = returns_in.get("assumptions") or {}
        exit_cap_rate = _coerce_float(assumptions.get("exit_cap_rate"))

    # Price-per-key prefers the capital engine's computed value (it
    # includes closing/PIP in some configurations); falls back to a
    # simple ``purchase_price / keys`` divide when the engine hasn't run.
    keys = deal_row.get("keys")
    purchase_price = _coerce_float(deal_row.get("purchase_price"))
    price_per_key: float | None = None
    if capital_out:
        price_per_key = _coerce_float(capital_out.get("price_per_key"))
    if price_per_key is None and purchase_price and keys:
        try:
            price_per_key = purchase_price / int(keys)
        except (TypeError, ValueError, ZeroDivisionError):
            price_per_key = None

    # PIP total — pulled from the capital engine inputs (analyst-set
    # renovation_budget). ``capex_plan`` field-overrides also flow into
    # ``renovation_budget`` via the engine_runner's loader, so this single
    # field captures both code-path origins.
    pip_total_usd: float | None = None
    if capital_in is not None:
        pip_total_usd = _coerce_float(capital_in.get("renovation_budget"))

    last_engine_run_at: datetime | None = None
    for envelope in engines_by_name.values():
        ts = _coerce_dt(envelope.get("completed_at")) or _coerce_dt(
            envelope.get("started_at")
        )
        if ts is None:
            continue
        if last_engine_run_at is None or ts > last_engine_run_at:
            last_engine_run_at = ts

    last_activity_at = (
        _coerce_dt(deal_row.get("updated_at"))
        or last_engine_run_at
        or _coerce_dt(deal_row.get("created_at"))
        or datetime.utcnow()
    )

    target_irr = _coerce_float(deal_row.get("target_irr"))
    target_irr_met: bool | None = None
    if target_irr is not None and levered_irr is not None:
        target_irr_met = levered_irr >= target_irr

    return {
        "deal_id": str(deal_row["id"]),
        "name": deal_row["name"],
        "state": deal_row.get("state") or "ONBOARDING",
        "status": deal_row.get("status") or "Draft",
        "city": deal_row.get("city"),
        "brand": deal_row.get("brand"),
        "deal_stage": deal_row.get("deal_stage"),
        "keys": int(keys) if keys is not None else None,
        "purchase_price": purchase_price,
        "price_per_key": price_per_key,
        "noi_y1": noi_y1,
        "noi_stabilized": noi_stabilized,
        "exit_cap_rate": exit_cap_rate,
        "levered_irr": levered_irr,
        "equity_multiple": equity_multiple,
        "dscr_y1": dscr_y1,
        "document_count": doc_count,
        "last_engine_run_at": last_engine_run_at,
        "last_activity_at": last_activity_at,
        "pip_total_usd": pip_total_usd,
        "target_irr": target_irr,
        "target_irr_met": target_irr_met,
    }


async def build_pipeline_snapshot(
    session: AsyncSession, *, tenant_id: UUID
) -> list[dict[str, Any]]:
    """Return one dict per deal (untruncated, unsorted, unfiltered).

    The result is cached for 60s per tenant. Cache is invalidated by
    ``invalidate(tenant_id)`` after every engine run / deal mutation
    so the analyst never sees a stale snapshot post-action.
    """
    tenant_id_str = str(tenant_id)
    cached = _CACHE.get(tenant_id_str)
    if cached is not None:
        expires_at, snapshot = cached
        if expires_at > time.time():
            return snapshot

    # 1) Pull every deal for the tenant. Excludes Archived rows so the
    # pipeline view focuses on active capital allocation.
    deal_rows = await session.execute(
        text(
            """
            SELECT id, tenant_id, name, city, keys, brand, deal_stage,
                   status, state, purchase_price, target_irr,
                   created_at, updated_at
              FROM deals
             WHERE tenant_id = :tenant
               AND COALESCE(status, '') != 'Archived'
            """
        ),
        {"tenant": tenant_id_str},
    )
    deals = [dict(r._mapping) for r in deal_rows.fetchall()]
    if not deals:
        _CACHE[tenant_id_str] = (time.time() + _CACHE_TTL_SECONDS, [])
        return []

    deal_ids = [str(d["id"]) for d in deals]

    # 2) Pull the latest engine row per (deal, engine) via a window
    # function. SQLite and Postgres both support ROW_NUMBER OVER
    # PARTITION BY (SQLite >= 3.25).
    placeholders = ", ".join(f":id_{i}" for i in range(len(deal_ids)))
    params: dict[str, Any] = {f"id_{i}": d for i, d in enumerate(deal_ids)}
    engine_rows = await session.execute(
        text(
            f"""
            SELECT *
              FROM (
                SELECT deal_id, engine_name, status,
                       inputs, outputs, started_at, completed_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY deal_id, engine_name
                           ORDER BY started_at DESC
                       ) AS rn
                  FROM engine_outputs
                 WHERE deal_id IN ({placeholders})
              ) latest
             WHERE rn = 1
            """
        ),
        params,
    )

    # deal_id → engine_name → envelope
    by_deal: dict[str, dict[str, dict[str, Any]]] = {}
    for r in engine_rows.fetchall():
        m = dict(r._mapping)
        deal_key = str(m["deal_id"])
        by_deal.setdefault(deal_key, {})[m["engine_name"]] = m

    # 3) Per-deal document counts so the table row can show "12 docs".
    # One small grouped query keeps this O(1) DB round-trips per call.
    # tenant_id predicate satisfies tenant_middleware (Sam Sentry
    # 2026-07-03 fe07db42) — the deal_ids in `placeholders` come from
    # the deals table which is already tenant-filtered upstream, so this
    # is a belt-and-braces guard, not a semantics change.
    doc_rows = await session.execute(
        text(
            f"""
            SELECT deal_id, COUNT(*) AS n
              FROM documents
             WHERE deal_id IN ({placeholders})
               AND tenant_id = :tenant
             GROUP BY deal_id
            """
        ),
        {**params, "tenant": tenant_id_str},
    )
    doc_counts: dict[str, int] = {
        str(r._mapping["deal_id"]): int(r._mapping["n"])
        for r in doc_rows.fetchall()
    }

    snapshot: list[dict[str, Any]] = []
    for deal in deals:
        deal_key = str(deal["id"])
        engines_by_name = by_deal.get(deal_key, {})
        snapshot.append(
            _row_from_deal(
                deal,
                engines_by_name=engines_by_name,
                doc_count=doc_counts.get(deal_key, 0),
            )
        )

    _CACHE[tenant_id_str] = (time.time() + _CACHE_TTL_SECONDS, snapshot)
    return snapshot


def apply_filters(
    rows: list[dict[str, Any]],
    *,
    state: str | None = None,
    min_irr: float | None = None,
    max_irr: float | None = None,
    min_per_key: float | None = None,
    max_per_key: float | None = None,
    deal_stage: str | None = None,
    target_met: bool | None = None,
) -> list[dict[str, Any]]:
    """In-memory filter pass. NULL values fail the predicate for that
    field — e.g., ``min_irr=0.15`` drops deals where IRR isn't computed
    yet, since "is this deal hitting 15% IRR?" is unknown not no.
    """
    def keep(row: dict[str, Any]) -> bool:
        if state is not None and row.get("state") != state:
            return False
        if deal_stage is not None and row.get("deal_stage") != deal_stage:
            return False
        if min_irr is not None:
            irr = row.get("levered_irr")
            if irr is None or irr < min_irr:
                return False
        if max_irr is not None:
            irr = row.get("levered_irr")
            if irr is None or irr > max_irr:
                return False
        if min_per_key is not None:
            ppk = row.get("price_per_key")
            if ppk is None or ppk < min_per_key:
                return False
        if max_per_key is not None:
            ppk = row.get("price_per_key")
            if ppk is None or ppk > max_per_key:
                return False
        if target_met is True and row.get("target_irr_met") is not True:
            return False
        if target_met is False and row.get("target_irr_met") is not False:
            return False
        return True

    return [r for r in rows if keep(r)]


def apply_sort(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Sort by ``sort`` key. Unknown tokens fall back to the default."""
    key, reverse = SORT_KEYS.get(sort, SORT_KEYS[DEFAULT_SORT])

    # Pythonic sort: None sorts LAST regardless of direction, so an
    # analyst doesn't see a screenful of "deals with no IRR" at the top
    # when picking "Highest IRR".
    def sort_key(row: dict[str, Any]) -> tuple[int, Any]:
        value = row.get(key)
        if value is None:
            return (1, 0)
        # str vs float vs datetime: comparable within their type, and
        # the (0, …) prefix groups all non-NULL values ahead of NULLs.
        return (0, value)

    return sorted(rows, key=sort_key, reverse=reverse)


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile. Returns None for an empty list."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    frac = k - lo
    return float(s[lo]) * (1 - frac) + float(s[hi]) * frac


def build_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Portfolio-level KPIs over the filtered row set."""
    irrs = [r["levered_irr"] for r in rows if r.get("levered_irr") is not None]
    ems = [r["equity_multiple"] for r in rows if r.get("equity_multiple") is not None]
    per_keys = [
        r["price_per_key"] for r in rows if r.get("price_per_key") is not None
    ]
    caps = [
        r["exit_cap_rate"] for r in rows if r.get("exit_cap_rate") is not None
    ]
    deals_with_target = [r for r in rows if r.get("target_irr") is not None]
    deals_meeting_target = [r for r in deals_with_target if r.get("target_irr_met") is True]

    deals_by_state: dict[str, int] = {}
    for r in rows:
        s = r.get("state") or "ONBOARDING"
        deals_by_state[s] = deals_by_state.get(s, 0) + 1

    return {
        "deal_count": len(rows),
        "median_irr": statistics.median(irrs) if irrs else None,
        "p25_irr": _percentile(irrs, 0.25) if irrs else None,
        "p75_irr": _percentile(irrs, 0.75) if irrs else None,
        "median_em": statistics.median(ems) if ems else None,
        "median_per_key": statistics.median(per_keys) if per_keys else None,
        "median_cap_rate": statistics.median(caps) if caps else None,
        "deals_meeting_target_irr": len(deals_meeting_target),
        "deals_with_target_irr": len(deals_with_target),
        "deals_by_state": deals_by_state,
    }
