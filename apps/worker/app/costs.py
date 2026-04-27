"""Per-deal cost aggregation.

Pulls ``ModelCall`` rows from the ``model_calls`` table (when present)
or from in-memory ``DealState`` and produces a ``DealCostReport`` for
the web UI. Pricing is sourced from ``budget._PRICING`` so the
dashboard always reflects whatever the budget enforcer is using.

The aggregation is pure — feed it a list of dict-shaped calls and it
returns a typed report. The DB read is a thin wrapper that converts
SQL rows into the same dict shape.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text

from .budget import _price_for, estimate_spent_usd
from .config import get_settings
from .database import get_engine

try:
    from fondok_schemas import AgentCost, DealCostReport, ModelCall
except ImportError:  # pragma: no cover — schemas package always present in prod
    AgentCost = None  # type: ignore[assignment]
    DealCostReport = None  # type: ignore[assignment]
    ModelCall = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# Approximate model -> bucket mapping for the "by model" pie chart.
def _model_bucket(model: str) -> str:
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "other"


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _normalize_call(raw: Any) -> dict[str, Any]:
    """Coerce a raw call (DB row, dict, or ModelCall) to a uniform dict."""
    return {
        "model": str(_attr(raw, "model", "") or ""),
        "agent_name": str(_attr(raw, "agent_name", "") or "unknown"),
        "input_tokens": int(_attr(raw, "input_tokens", 0) or 0),
        "output_tokens": int(_attr(raw, "output_tokens", 0) or 0),
        "cache_read_tokens": int(
            _attr(
                raw,
                "cache_read_tokens",
                _attr(raw, "cache_read_input_tokens", 0),
            )
            or 0
        ),
        "cache_creation_tokens": int(
            _attr(
                raw,
                "cache_creation_tokens",
                _attr(raw, "cache_creation_input_tokens", 0),
            )
            or 0
        ),
        "cost_usd": float(_attr(raw, "cost_usd", 0) or 0),
        "latency_ms": float(_attr(raw, "latency_ms", 0) or 0),
        "trace_id": str(_attr(raw, "trace_id", "") or ""),
        "status": str(_attr(raw, "status", "ok") or "ok"),
        "created_at": _attr(raw, "created_at", None)
        or _attr(raw, "completed_at", None)
        or _attr(raw, "started_at", None),
    }


def _empty_agent(name: str) -> dict[str, Any]:
    return {
        "agent": name,
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "cost_usd": Decimal("0"),
        "_latency_total_ms": 0.0,
    }


def _compute_cost(call: dict[str, Any]) -> Decimal:
    """Use stored cost_usd if non-zero; otherwise recompute from tokens."""
    stored = call.get("cost_usd") or 0
    if stored:
        return Decimal(str(stored))
    in_price, out_price = _price_for(call["model"])
    raw = (call["input_tokens"] * in_price + call["output_tokens"] * out_price) / 1_000_000
    return Decimal(str(round(raw, 6)))


def aggregate_costs(
    raw_calls: list[Any],
    *,
    deal_id: UUID | str,
    budget_usd: float | None = None,
    timeline_limit: int = 50,
) -> Any:
    """Roll up a list of ModelCall-shaped records into a ``DealCostReport``.

    Pure / deterministic — feed it whatever shape the caller has and
    you get a typed report back. Designed so unit tests can hand it
    synthetic dicts without touching the DB.
    """
    if DealCostReport is None or AgentCost is None:  # pragma: no cover
        raise RuntimeError("fondok_schemas not installed; cannot build cost report")

    settings = get_settings()
    budget = (
        Decimal(str(budget_usd))
        if budget_usd is not None
        else Decimal(str(settings.DEFAULT_DEAL_BUDGET_USD or 20.0))
    )

    calls = [_normalize_call(c) for c in (raw_calls or [])]

    by_agent: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    total_cost = Decimal("0")
    total_input = 0
    total_cache_read = 0

    for call in calls:
        agent = call["agent_name"] or "unknown"
        bucket = _model_bucket(call["model"])
        cost = _compute_cost(call)
        total_cost += cost
        total_input += call["input_tokens"]
        total_cache_read += call["cache_read_tokens"]

        for key, target in (("agent", by_agent), ("model", by_model)):
            label = agent if key == "agent" else bucket
            slot = target.setdefault(label, _empty_agent(label))
            slot["calls"] += 1
            slot["input_tokens"] += call["input_tokens"]
            slot["output_tokens"] += call["output_tokens"]
            slot["cache_read_tokens"] += call["cache_read_tokens"]
            slot["cache_creation_tokens"] += call["cache_creation_tokens"]
            slot["cost_usd"] += cost
            slot["_latency_total_ms"] += call["latency_ms"]

    def _finalize(entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for label, e in entries.items():
            avg = (e["_latency_total_ms"] / e["calls"]) if e["calls"] else 0.0
            out[label] = AgentCost(
                agent=label,
                calls=e["calls"],
                input_tokens=e["input_tokens"],
                output_tokens=e["output_tokens"],
                cache_read_tokens=e["cache_read_tokens"],
                cache_creation_tokens=e["cache_creation_tokens"],
                cost_usd=Decimal(str(round(float(e["cost_usd"]), 6))),
                avg_latency_ms=round(avg, 2),
            )
        return out

    by_agent_finalized = _finalize(by_agent)
    by_model_finalized = _finalize(by_model)

    # Cache hit rate = cache_read / (input_tokens + cache_read). Treat
    # cache reads as "would have been billed input" for the rate.
    denom = total_input + total_cache_read
    cache_hit_rate = (total_cache_read / denom) if denom > 0 else 0.0

    # Build the timeline (most recent first, capped). When ``created_at``
    # is missing we fall back to insertion order.
    sorted_calls = sorted(
        calls,
        key=lambda c: (c.get("created_at") or datetime.min.replace(tzinfo=UTC)),
        reverse=True,
    )[:timeline_limit]
    timeline_models: list[Any] = []
    for c in sorted_calls:
        ts = c.get("created_at")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except ValueError:
                ts = datetime.now(UTC)
        elif ts is None:
            ts = datetime.now(UTC)
        timeline_models.append(
            ModelCall(
                model=c["model"] or "unknown",
                input_tokens=c["input_tokens"],
                output_tokens=c["output_tokens"],
                cost_usd=float(_compute_cost(c)),
                trace_id=c["trace_id"] or "n/a",
                started_at=ts,
                completed_at=ts,
                cache_creation_input_tokens=c["cache_creation_tokens"],
                cache_read_input_tokens=c["cache_read_tokens"],
                agent_name=c["agent_name"],
            )
        )

    return DealCostReport(
        deal_id=UUID(str(deal_id)) if not isinstance(deal_id, UUID) else deal_id,
        total_cost_usd=Decimal(str(round(float(total_cost), 6))),
        budget_usd=budget,
        cache_hit_rate=round(cache_hit_rate, 4),
        by_agent=sorted(by_agent_finalized.values(), key=lambda a: -float(a.cost_usd)),
        by_model=by_model_finalized,
        timeline=timeline_models,
        generated_at=datetime.now(UTC),
    )


async def load_model_calls(deal_id: str) -> list[dict[str, Any]]:
    """Read ``model_calls`` rows for ``deal_id`` from the live DB.

    Returns an empty list when the table is missing or empty so the
    endpoint can still return a well-formed (zeroed) report.
    """
    engine = get_engine()
    sql = text(
        """
        SELECT
            id, deal_id, agent_name, model,
            input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens,
            cost_usd, latency_ms, trace_id, status, created_at
        FROM model_calls
        WHERE deal_id = :deal_id
        ORDER BY created_at DESC
        LIMIT 500
        """
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(sql, {"deal_id": str(deal_id)})
            return [dict(r._mapping) for r in result]
    except Exception as exc:  # pragma: no cover — table may not exist in dev
        logger.info("load_model_calls: no rows for deal=%s (%s)", deal_id, exc)
        return []


async def build_cost_report(deal_id: str) -> Any:
    """End-to-end: load → aggregate → return the typed report."""
    calls = await load_model_calls(deal_id)
    settings = get_settings()
    return aggregate_costs(
        calls,
        deal_id=deal_id,
        budget_usd=float(settings.DEFAULT_DEAL_BUDGET_USD or 20.0),
    )


# Side-channel proof estimate so unit tests can sanity-check.
def quick_total_usd(raw_calls: list[Any]) -> float:
    return estimate_spent_usd(raw_calls)


__all__ = [
    "aggregate_costs",
    "build_cost_report",
    "load_model_calls",
    "quick_total_usd",
]
