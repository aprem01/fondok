"""Observability endpoints — cache stats and per-agent cost rollups.

Reads ``model_calls`` rows from the live DB and projects them onto two
useful views:

* ``GET /observability/cache-stats``        — aggregate cache hit rate
  over the last N model calls (default 100). Includes a per-agent
  breakdown so we can tell whether the Extractor's catalog blocks are
  actually hitting cache while the Analyst's massive prompts are not.

* ``GET /observability/agent-costs?days=N`` — per-agent token spend +
  cache hit rate over the last ``N`` days (default 7).

Both endpoints degrade gracefully when the ``model_calls`` table is
empty (returns zeros, not 500). The web dashboard can show a "no data
yet" badge without special-casing the response shape.

Test/synthetic ingest
---------------------
``POST /observability/_test/model-call`` accepts a single ``ModelCall``
JSON body and inserts it into the ``model_calls`` table. The endpoint
is gated by ``ALLOW_TEST_INGEST=true`` (defaults to true in dev so the
unit tests work; production sets it false). It exists so tests that
don't run the full agent stack can still validate the aggregation
math without standing up a Postgres-only fixture.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from ..budget import _price_for
from ..database import get_engine

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────── helpers ───────────────────────


def _is_test_ingest_enabled() -> bool:
    """Default-on in dev; set ``ALLOW_TEST_INGEST=false`` in prod."""
    return (os.environ.get("ALLOW_TEST_INGEST", "true").lower() != "false")


async def _select_recent_calls(
    *,
    limit: int | None = None,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    """Read model_calls rows ordered by created_at DESC.

    Returns an empty list when the table doesn't exist (sqlite migration
    not run), so the endpoints stay HTTP 200 in that case.
    """
    engine = get_engine()
    where_clauses: list[str] = []
    params: dict[str, Any] = {}
    if since is not None:
        where_clauses.append("created_at >= :since")
        # asyncpg requires datetime objects for TIMESTAMPTZ; SQLite codec
        # accepts datetimes too via the Python adapter.
        params["since"] = since
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    limit_sql = f"LIMIT {int(limit)}" if limit else ""
    sql = text(
        f"""
        SELECT
            id, deal_id, agent_name, model,
            input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens,
            cost_usd, latency_ms, trace_id, status, created_at
        FROM model_calls
        {where_sql}
        ORDER BY created_at DESC
        {limit_sql}
        """
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(sql, params)
            return [dict(r._mapping) for r in result]
    except Exception as exc:
        logger.info("observability: model_calls unavailable (%s)", exc)
        return []


def _hit_rate(read: int, created: int, plain_in: int) -> float:
    """Cache hit rate = cache_read / (cache_read + cache_create + plain_in).

    Plain input is what *would* have been cached on a perfect run, so
    the denominator is the total billable input stream. Returns 0.0
    when nothing's been counted yet.
    """
    denom = read + created + plain_in
    if denom <= 0:
        return 0.0
    return round(read / denom, 4)


def _row_cost(row: dict[str, Any]) -> float:
    """Per-row USD cost. Uses stored cost_usd if non-zero, else the
    cache-aware recomputation."""
    stored = row.get("cost_usd") or 0
    if stored:
        try:
            return float(stored)
        except (TypeError, ValueError):
            pass
    in_price, out_price = _price_for(str(row.get("model") or ""))
    plain_in = int(row.get("input_tokens") or 0)
    out = int(row.get("output_tokens") or 0)
    cc = int(row.get("cache_creation_tokens") or 0)
    cr = int(row.get("cache_read_tokens") or 0)
    return (
        plain_in * in_price
        + cc * in_price * 1.25
        + cr * in_price * 0.10
        + out * out_price
    ) / 1_000_000


# ─────────────────────── /cache-stats ───────────────────────


@router.get("/cache-stats")
async def cache_stats(
    n: int = Query(100, ge=1, le=5000, description="Look at the last N model calls."),
) -> dict[str, Any]:
    """Aggregate cache hit rate over the most recent N calls."""
    rows = await _select_recent_calls(limit=n)

    total_input = 0
    total_cache_read = 0
    total_cache_create = 0
    total_output = 0
    total_cost = 0.0

    by_agent: dict[str, dict[str, Any]] = {}
    for r in rows:
        agent = str(r.get("agent_name") or "unknown")
        plain_in = int(r.get("input_tokens") or 0)
        cr = int(r.get("cache_read_tokens") or 0)
        cc = int(r.get("cache_creation_tokens") or 0)
        out = int(r.get("output_tokens") or 0)
        total_input += plain_in
        total_cache_read += cr
        total_cache_create += cc
        total_output += out
        total_cost += _row_cost(r)

        bucket = by_agent.setdefault(
            agent,
            {
                "agent": agent,
                "calls": 0,
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "output_tokens": 0,
            },
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += plain_in
        bucket["cache_read_tokens"] += cr
        bucket["cache_creation_tokens"] += cc
        bucket["output_tokens"] += out

    per_agent: list[dict[str, Any]] = []
    for agent, b in by_agent.items():
        per_agent.append(
            {
                **b,
                "cache_hit_rate": _hit_rate(
                    b["cache_read_tokens"],
                    b["cache_creation_tokens"],
                    b["input_tokens"],
                ),
            }
        )
    per_agent.sort(key=lambda x: -x["calls"])

    return {
        "samples": len(rows),
        "window": "last_n",
        "n": n,
        "cache_hit_rate": _hit_rate(total_cache_read, total_cache_create, total_input),
        "totals": {
            "input_tokens": total_input,
            "cache_read_tokens": total_cache_read,
            "cache_creation_tokens": total_cache_create,
            "output_tokens": total_output,
            "estimated_cost_usd": round(total_cost, 6),
        },
        "by_agent": per_agent,
    }


# ─────────────────────── /agent-costs ───────────────────────


@router.get("/agent-costs")
async def agent_costs(
    days: int = Query(7, ge=1, le=90, description="Look back this many days."),
) -> dict[str, Any]:
    """Per-agent token spend + cache hit rate over the last N days."""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await _select_recent_calls(since=since)

    by_agent: dict[str, dict[str, Any]] = {}
    for r in rows:
        agent = str(r.get("agent_name") or "unknown")
        plain_in = int(r.get("input_tokens") or 0)
        cr = int(r.get("cache_read_tokens") or 0)
        cc = int(r.get("cache_creation_tokens") or 0)
        out = int(r.get("output_tokens") or 0)
        cost = _row_cost(r)
        b = by_agent.setdefault(
            agent,
            {
                "agent": agent,
                "calls": 0,
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        b["calls"] += 1
        b["input_tokens"] += plain_in
        b["cache_read_tokens"] += cr
        b["cache_creation_tokens"] += cc
        b["output_tokens"] += out
        b["cost_usd"] += cost

    per_agent: list[dict[str, Any]] = []
    for agent, b in by_agent.items():
        per_agent.append(
            {
                **b,
                "cost_usd": round(b["cost_usd"], 6),
                "cache_hit_rate": _hit_rate(
                    b["cache_read_tokens"],
                    b["cache_creation_tokens"],
                    b["input_tokens"],
                ),
            }
        )
    per_agent.sort(key=lambda x: -x["cost_usd"])

    total_cost = sum(b["cost_usd"] for b in per_agent)
    return {
        "samples": len(rows),
        "window": f"last_{days}_days",
        "since": since.isoformat(),
        "by_agent": per_agent,
        "total_cost_usd": round(total_cost, 6),
    }


# ─────────────────────── /_test/model-call ───────────────────────


class _ModelCallIngest(BaseModel):
    """Request body for the test ingest endpoint."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    agent_name: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_creation_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: int | None = Field(default=None, ge=0)
    trace_id: str | None = None
    status: str = Field(default="ok", min_length=1, max_length=40)


@router.post("/_test/model-call", status_code=201)
async def ingest_model_call(body: _ModelCallIngest) -> dict[str, Any]:
    """Insert a synthetic ModelCall row into the local model_calls table.

    Only enabled when ``ALLOW_TEST_INGEST`` is not set to "false". The
    endpoint exists so unit tests can validate the observability
    aggregation math without standing up a real agent run.
    """
    if not _is_test_ingest_enabled():
        raise HTTPException(status_code=404, detail="test ingest disabled")
    engine = get_engine()
    row_id = str(uuid4())
    sql = text(
        """
        INSERT INTO model_calls
            (id, deal_id, agent_name, model,
             input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens,
             cost_usd, latency_ms, trace_id, status, created_at)
        VALUES
            (:id, :deal_id, :agent_name, :model,
             :input_tokens, :output_tokens,
             :cache_read_tokens, :cache_creation_tokens,
             :cost_usd, :latency_ms, :trace_id, :status, :created_at)
        """
    )
    params: dict[str, Any] = {
        "id": row_id,
        "deal_id": body.deal_id,
        "agent_name": body.agent_name,
        "model": body.model,
        "input_tokens": body.input_tokens,
        "output_tokens": body.output_tokens,
        "cache_read_tokens": body.cache_read_tokens,
        "cache_creation_tokens": body.cache_creation_tokens,
        "cost_usd": body.cost_usd,
        "latency_ms": body.latency_ms,
        "trace_id": body.trace_id,
        "status": body.status,
        # asyncpg requires a datetime instance for TIMESTAMPTZ; SQLite
        # accepts datetimes too via the Python adapter, so a single shape
        # works for both backends.
        "created_at": datetime.now(UTC),
    }
    try:
        async with engine.begin() as conn:
            await conn.execute(sql, params)
    except Exception as exc:
        logger.warning("observability: ingest failed (%s)", exc)
        raise HTTPException(status_code=500, detail=f"ingest failed: {exc}") from exc
    return {"id": row_id, "ok": True}


__all__ = ["router"]
