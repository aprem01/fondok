"""Persist ``ModelCall`` rows emitted by the agents into ``model_calls``.

Each agent (Router / Extractor / Normalizer / Variance / Analyst) builds
a typed :class:`fondok_schemas.ModelCall` after every LLM round-trip and
returns it in its envelope's ``model_calls`` field. Historically those
envelopes were threaded through the LangGraph state but never written to
the database, so ``GET /deals/{id}/costs`` and
``GET /observability/agent-costs`` always returned zero.

This module closes that gap. ``persist_model_calls`` is the single
INSERT path used by every agent; it is intentionally fire-and-forget:
any failure (table missing in dev, transient connection error, etc.)
logs and returns rather than raising — observability must never break a
real extraction or memo run.

Cost accounting
---------------
``ModelCall.cost_usd`` is set to 0 by the agents because they don't
own the pricing table. This helper recomputes the dollar cost from
the per-model rates in :mod:`app.budget` so the persisted row is the
authoritative record for the cost dashboard.

Datetime binding
----------------
We pass ``datetime`` objects (not ISO strings) into the SQL parameters
so asyncpg's TIMESTAMPTZ codec doesn't choke on string parameters.
SQLite accepts both via the Python adapter, so dev/tests still work.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .budget import _price_for
from .database import get_session_factory

logger = logging.getLogger(__name__)


def _attr(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _compute_cost_usd(call: Any) -> float:
    """Compute USD cost from token counts, honoring a non-zero stored value."""
    stored = _attr(call, "cost_usd", 0) or 0
    try:
        stored_f = float(stored)
    except (TypeError, ValueError):
        stored_f = 0.0
    if stored_f > 0:
        return stored_f

    model = str(_attr(call, "model", "") or "")
    in_tok = int(_attr(call, "input_tokens", 0) or 0)
    out_tok = int(_attr(call, "output_tokens", 0) or 0)
    cache_create = int(_attr(call, "cache_creation_input_tokens", 0) or 0)
    cache_read = int(_attr(call, "cache_read_input_tokens", 0) or 0)
    # Anthropic's input_tokens is the *uncached* portion of the input
    # stream. If a caller bundled them into input_tokens we still want
    # to avoid double-counting.
    if in_tok >= cache_create + cache_read:
        plain_in = max(in_tok - cache_create - cache_read, 0)
    else:
        plain_in = in_tok
    in_price, out_price = _price_for(model)
    raw = (
        plain_in * in_price
        + cache_create * in_price * 1.25
        + cache_read * in_price * 0.10
        + out_tok * out_price
    ) / 1_000_000
    return round(raw, 6)


def _latency_ms(call: Any) -> int | None:
    """Derive call latency. Prefers stored ``latency_ms``; falls back to
    ``completed_at - started_at`` when both timestamps are present."""
    stored = _attr(call, "latency_ms", None)
    if stored is not None:
        try:
            return int(stored)
        except (TypeError, ValueError):
            pass
    started = _attr(call, "started_at", None)
    completed = _attr(call, "completed_at", None)
    if isinstance(started, datetime) and isinstance(completed, datetime):
        try:
            return max(int((completed - started).total_seconds() * 1000), 0)
        except Exception:  # noqa: BLE001
            return None
    return None


async def persist_model_calls(
    session: AsyncSession,
    *,
    deal_id: str | UUID,
    tenant_id: str | UUID | None,
    calls: list[Any] | None,
    commit: bool = False,
) -> int:
    """Append ``ModelCall``-shaped rows to the ``model_calls`` table.

    Returns the number of rows successfully inserted. Never raises —
    persistence failures are logged and swallowed so the calling agent
    still returns its typed output. Costs are recomputed from the
    pricing table when the call carries ``cost_usd=0`` so the dashboard
    has a non-zero number even if the agent didn't price the call.

    When ``commit=True`` a session-level commit is issued after the
    inserts. Most callers leave it ``False`` and let their own
    transaction enclose the writes.
    """
    if not calls:
        return 0

    inserted = 0
    sql = text(
        """
        INSERT INTO model_calls (
            id, deal_id, tenant_id, agent_name, model,
            input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens,
            cost_usd, latency_ms, trace_id, status, created_at
        ) VALUES (
            :id, :deal_id, :tenant_id, :agent_name, :model,
            :input_tokens, :output_tokens,
            :cache_read_tokens, :cache_creation_tokens,
            :cost_usd, :latency_ms, :trace_id, :status, :created_at
        )
        """
    )

    for call in calls:
        try:
            params: dict[str, Any] = {
                "id": str(uuid4()),
                "deal_id": str(deal_id),
                "tenant_id": str(tenant_id) if tenant_id is not None else None,
                "agent_name": str(_attr(call, "agent_name", "") or "unknown"),
                "model": str(_attr(call, "model", "") or "unknown"),
                "input_tokens": int(_attr(call, "input_tokens", 0) or 0),
                "output_tokens": int(_attr(call, "output_tokens", 0) or 0),
                "cache_read_tokens": int(
                    _attr(call, "cache_read_input_tokens", 0) or 0
                ),
                "cache_creation_tokens": int(
                    _attr(call, "cache_creation_input_tokens", 0) or 0
                ),
                "cost_usd": _compute_cost_usd(call),
                "latency_ms": _latency_ms(call),
                "trace_id": str(_attr(call, "trace_id", "") or "") or None,
                "status": str(_attr(call, "status", "ok") or "ok"),
                # asyncpg TIMESTAMPTZ wants a datetime — never an ISO
                # string. SQLite accepts datetime via the Python adapter
                # so dev/tests still work without conversion.
                "created_at": _attr(call, "completed_at", None)
                or _attr(call, "started_at", None)
                or datetime.now(UTC),
            }
            await session.execute(sql, params)
            inserted += 1
        except Exception as exc:  # noqa: BLE001 — observability never raises
            logger.warning(
                "persist_model_calls: insert failed (deal=%s agent=%s): %s",
                deal_id,
                _attr(call, "agent_name", "?"),
                exc,
            )

    if commit and inserted > 0:
        try:
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            logger.warning("persist_model_calls: commit failed: %s", exc)

    return inserted


async def persist_model_calls_standalone(
    *,
    deal_id: str | UUID,
    tenant_id: str | UUID | None,
    calls: list[Any] | None,
) -> int:
    """Open a fresh session and persist ``calls`` in a single commit.

    Convenience wrapper agents call after returning from their LLM
    round-trip when they don't already hold a session. Errors are
    swallowed so the agent's return value is unaffected.
    """
    if not calls:
        return 0
    try:
        factory = get_session_factory()
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist_model_calls: session factory unavailable: %s", exc)
        return 0
    try:
        async with factory() as session:
            n = await persist_model_calls(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                calls=calls,
            )
            if n:
                try:
                    await session.commit()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "persist_model_calls: standalone commit failed: %s", exc
                    )
                    return 0
            return n
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist_model_calls: standalone session failed: %s", exc)
        return 0


__all__ = [
    "persist_model_calls",
    "persist_model_calls_standalone",
]
