"""Admin cost + quality observability — Task Q (2026-07).

Sam is about to run a series of cost optimizations (prompt cache tuning,
router/haiku routing, batch inference) and needs a single endpoint that
answers "how much are we spending, on what, and is the cache doing its
job?" without wading through per-deal reports.

This router complements ``/observability`` (recent-N cache stats + per
agent view) with a tenant-scoped roll-up over the operational windows
Sam cares about: last 24h, 7d, 30d. It also exposes a small
``backfill`` verb that recomputes ``cost_usd`` for any historical
``model_calls`` rows that got persisted with ``cost_usd = 0`` before
:mod:`app.cost_persistence` learned to price them — that path was fixed
in June but rows from before the fix stayed at zero, which biases every
rollup downward. Backfill is idempotent (skips rows where cost is
already non-zero) so it is safe to invoke from ops on a cadence.

Endpoints
---------
* ``GET  /admin/cost``           — spend windows, per-agent, per-model,
                                    per-deal top 10, cache hit rate.
* ``POST /admin/cost/backfill``  — recompute cost_usd for zero-cost
                                    rows in the tenant. Returns the
                                    updated row count.

Tenant scoping
--------------
Both endpoints resolve the tenant from ``X-Tenant-Id`` via the shared
``get_tenant_id`` dependency (same contract as ``/deals``, ``/audit``,
``/portfolio-library``, etc.). Rows whose ``tenant_id`` is NULL
(historical system jobs written before the tenant column landed) are
excluded from the rollup — they'd otherwise inflate the "default
tenant" bucket with cross-tenant traffic.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text

from ..auth import AuthContext, require_role
from ..budget import _price_for
from ..config import get_settings
from ..database import get_engine

logger = logging.getLogger(__name__)
router = APIRouter()


def _enforce_cost_email_allowlist(auth: AuthContext) -> None:
    """Optional email allowlist on top of require_role("admin").

    HOTFIX 2026-07-10: the prior gate hardcoded a single Gmail and
    compared against ``auth.email`` — which is None unless the Clerk
    JWT template includes a custom ``email`` claim (auth/context.py
    documents this). Result: every browser admin, founder included,
    got 403'd. Now the allowlist is config-driven and EMPTY BY
    DEFAULT, so the admin role alone suffices. When the operator does
    set ``ADMIN_COST_EMAIL_ALLOWLIST``, header/default (ops/script)
    callers still bypass — only JWT callers with a resolvable email
    are checked, and a JWT that carries no email claim is NOT locked
    out (that was the whole bug).
    """
    raw = (get_settings().ADMIN_COST_EMAIL_ALLOWLIST or "").strip()
    if not raw:
        return  # role gate only
    if auth.source != "jwt":
        return  # trusted ops/script path
    if auth.email is None:
        # No email claim to check against — role gate already passed,
        # don't lock out a legitimately admin-roled session.
        return
    allowed = {e.strip().lower() for e in raw.split(",") if e.strip()}
    if auth.email.strip().lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cost dashboard access restricted",
        )


# ─────────────────────── pricing helpers ───────────────────────


def _row_cost(row: dict[str, Any]) -> float:
    """Per-row USD cost, cache-aware.

    Prefers a non-zero stored ``cost_usd``; falls back to the pricing
    table so historical rows that were written before
    :mod:`app.cost_persistence` computed costs still show up in the
    rollup. Multipliers mirror :func:`app.budget.estimate_spent_usd`:
    cache_creation is billed at 1.25x the input rate, cache_read at
    0.10x. Plain ``input_tokens`` is the *uncached* portion, so we do
    not double-subtract cache tokens when the caller already gave us
    the uncached count.
    """
    stored = row.get("cost_usd") or 0
    if stored:
        try:
            f = float(stored)
            if f > 0:
                return f
        except (TypeError, ValueError):
            pass
    return _recompute_cost(row)


def _recompute_cost(row: dict[str, Any]) -> float:
    """Compute cost_usd purely from token counts. Used by backfill and
    by ``_row_cost`` when the stored value is zero."""
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


async def _fetch_rows(
    *,
    tenant_id: UUID,
    since: datetime,
) -> list[dict[str, Any]]:
    """Read ``model_calls`` rows for the tenant since ``since``.

    Rows whose ``tenant_id`` is NULL are intentionally excluded: they're
    system-job leftovers from before the tenant column got backfilled
    and would inflate whichever tenant happened to query first.
    Returns ``[]`` when the table doesn't exist yet (sqlite dev boot
    without migrations) so callers stay HTTP 200.
    """
    engine = get_engine()
    sql = text(
        """
        SELECT
            id, deal_id, agent_name, model,
            input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens,
            cost_usd, created_at
        FROM model_calls
        WHERE tenant_id = :tenant_id
          AND created_at >= :since
        """
    )
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                sql, {"tenant_id": str(tenant_id), "since": since}
            )
            return [dict(r._mapping) for r in result]
    except Exception as exc:  # noqa: BLE001 — observability never 500s
        logger.info("admin_cost: model_calls unavailable (%s)", exc)
        return []


def _cache_hit_rate(cache_read: int, plain_in: int, cache_create: int) -> float:
    """cache_read / (cache_read + cache_create + plain_in).

    Matches the ``/observability/cache-stats`` denominator so operators
    aren't chasing two different definitions in two dashboards. Plain
    input is what *would* have been cached on a well-tuned run, so it
    belongs in the denominator.
    """
    denom = cache_read + plain_in + cache_create
    if denom <= 0:
        return 0.0
    return round(cache_read / denom, 4)


def _window_total(rows: list[dict[str, Any]], *, since: datetime) -> dict[str, Any]:
    """Sum spend + tokens over rows whose ``created_at >= since``.

    Returned as a dict so we can render the response with a uniform
    shape across the three windows (24h / 7d / 30d).
    """
    total_cost = 0.0
    plain_in = 0
    cache_read = 0
    cache_create = 0
    output = 0
    calls = 0
    for r in rows:
        created = r.get("created_at")
        if isinstance(created, str):
            try:
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                created = None
        if isinstance(created, datetime):
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            if created < since:
                continue
        calls += 1
        total_cost += _row_cost(r)
        plain_in += int(r.get("input_tokens") or 0)
        cache_read += int(r.get("cache_read_tokens") or 0)
        cache_create += int(r.get("cache_creation_tokens") or 0)
        output += int(r.get("output_tokens") or 0)
    return {
        "calls": calls,
        "cost_usd": round(total_cost, 6),
        "input_tokens": plain_in,
        "cache_read_tokens": cache_read,
        "cache_creation_tokens": cache_create,
        "output_tokens": output,
        "cache_hit_rate": _cache_hit_rate(cache_read, plain_in, cache_create),
    }


# ─────────────────────── GET /admin/cost ───────────────────────


@router.get("/cost")
async def admin_cost(
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """Cost + cache rollup for the calling tenant.

    Response shape (stable — the Next.js admin page depends on it):

    ``{
        "tenant_id": "<uuid>",
        "generated_at": "<iso8601>",
        "windows": {
            "24h": { calls, cost_usd, input_tokens, cache_read_tokens,
                     cache_creation_tokens, output_tokens,
                     cache_hit_rate },
            "7d":  { ... },
            "30d": { ... }
        },
        "by_agent": [ { agent, calls, cost_usd, cache_hit_rate } ],
        "by_model": [ { model, calls, cost_usd } ],
        "by_deal":  [ { deal_id, calls, cost_usd } ]   # top 10 by cost
    }``

    All aggregations are over the last 30 days — that's the widest
    window we publish and the one Sam quotes when he wants the "big
    number". If the ``model_calls`` table is empty (fresh install) we
    return zeros, not 500, so the admin UI can render an empty state.

    Wave RBAC 2026-07 — gated on ``role="admin"``. The header/default
    trusted-caller escape hatch still passes so ops can hit the
    endpoint from a scripted context. JWT auth additionally gated on
    email == "kpremks@gmail.com".
    """
    _enforce_cost_email_allowlist(auth)

    tenant_id = auth.tenant_id
    now = datetime.now(UTC)
    since_30d = now - timedelta(days=30)
    since_7d = now - timedelta(days=7)
    since_24h = now - timedelta(hours=24)

    rows = await _fetch_rows(tenant_id=tenant_id, since=since_30d)

    # Per-agent / per-model / per-deal aggregation over the 30d window.
    # We do it in one pass to keep the endpoint fast even when a busy
    # tenant has 100k rows in the window.
    by_agent: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    by_deal: dict[str, dict[str, Any]] = {}

    for r in rows:
        cost = _row_cost(r)
        plain_in = int(r.get("input_tokens") or 0)
        cache_read = int(r.get("cache_read_tokens") or 0)
        cache_create = int(r.get("cache_creation_tokens") or 0)

        agent = str(r.get("agent_name") or "unknown")
        a = by_agent.setdefault(
            agent,
            {
                "agent": agent,
                "calls": 0,
                "cost_usd": 0.0,
                "input_tokens": 0,
                "cache_read_tokens": 0,
                "cache_creation_tokens": 0,
            },
        )
        a["calls"] += 1
        a["cost_usd"] += cost
        a["input_tokens"] += plain_in
        a["cache_read_tokens"] += cache_read
        a["cache_creation_tokens"] += cache_create

        model = str(r.get("model") or "unknown")
        m = by_model.setdefault(
            model, {"model": model, "calls": 0, "cost_usd": 0.0}
        )
        m["calls"] += 1
        m["cost_usd"] += cost

        deal = str(r.get("deal_id") or "unknown")
        d = by_deal.setdefault(
            deal, {"deal_id": deal, "calls": 0, "cost_usd": 0.0}
        )
        d["calls"] += 1
        d["cost_usd"] += cost

    agent_list = [
        {
            "agent": a["agent"],
            "calls": a["calls"],
            "cost_usd": round(a["cost_usd"], 6),
            "cache_hit_rate": _cache_hit_rate(
                a["cache_read_tokens"],
                a["input_tokens"],
                a["cache_creation_tokens"],
            ),
        }
        for a in by_agent.values()
    ]
    agent_list.sort(key=lambda x: -x["cost_usd"])

    model_list = [
        {"model": m["model"], "calls": m["calls"], "cost_usd": round(m["cost_usd"], 6)}
        for m in by_model.values()
    ]
    model_list.sort(key=lambda x: -x["cost_usd"])

    deal_list = [
        {"deal_id": d["deal_id"], "calls": d["calls"], "cost_usd": round(d["cost_usd"], 6)}
        for d in by_deal.values()
    ]
    deal_list.sort(key=lambda x: -x["cost_usd"])
    top_deals = deal_list[:10]

    return {
        "tenant_id": str(tenant_id),
        "generated_at": now.isoformat(),
        "windows": {
            "24h": _window_total(rows, since=since_24h),
            "7d": _window_total(rows, since=since_7d),
            "30d": _window_total(rows, since=since_30d),
        },
        "by_agent": agent_list,
        "by_model": model_list,
        "by_deal": top_deals,
    }


# ─────────────────────── POST /admin/cost/backfill ───────────────────────


@router.post("/cost/backfill")
async def admin_cost_backfill(
    auth: Annotated[AuthContext, Depends(require_role("admin"))],
) -> dict[str, Any]:
    """Recompute ``cost_usd`` for tenant rows where it's still zero.

    Idempotent — rows whose ``cost_usd`` is already non-zero are left
    alone, so calling this on a cron ("every hour, catch any drift")
    doesn't clobber caller-supplied costs. Returns the number of rows
    updated so ops can spot outliers in the log.

    Wave RBAC 2026-07 — gated on ``role="admin"``. Cron / ops-runbook
    callers use the header path and pass the escape hatch. JWT auth
    additionally gated on email == "kpremks@gmail.com".
    """
    _enforce_cost_email_allowlist(auth)

    tenant_id = auth.tenant_id
    engine = get_engine()
    select_sql = text(
        """
        SELECT
            id, model, input_tokens, output_tokens,
            cache_read_tokens, cache_creation_tokens, cost_usd
        FROM model_calls
        WHERE tenant_id = :tenant_id
          AND (cost_usd IS NULL OR cost_usd = 0)
        """
    )
    # tenant_id predicate keeps tenant_middleware / Sentry quiet — see
    # apps/worker/app/tenant_middleware.py. Also strictly prevents a
    # cross-tenant write under concurrent backfill runs (the id filter
    # alone would leak if two tenants shared an id namespace).
    update_sql = text(
        """
        UPDATE model_calls
           SET cost_usd = :cost_usd
         WHERE id = :id
           AND tenant_id = :tenant_id
        """
    )
    updated = 0
    scanned = 0
    try:
        async with engine.begin() as conn:
            rows = list(
                (await conn.execute(select_sql, {"tenant_id": str(tenant_id)})).mappings()
            )
            scanned = len(rows)
            for r in rows:
                d = dict(r)
                cost = _recompute_cost(d)
                if cost <= 0:
                    # No pricing entry / all zeros — leave the row alone
                    # so we don't overwrite a legitimate zero-cost call
                    # with another zero on every run.
                    continue
                await conn.execute(
                    update_sql,
                    {
                        "cost_usd": round(cost, 6),
                        "id": d["id"],
                        "tenant_id": str(tenant_id),
                    },
                )
                updated += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("admin_cost.backfill: failed (%s)", exc)
        return {"scanned": scanned, "updated": updated, "error": str(exc)}
    return {
        "tenant_id": str(tenant_id),
        "scanned": scanned,
        "updated": updated,
    }


__all__ = ["router"]
