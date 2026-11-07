"""Platform overview for Fondok's internal admins — every firm on one page.

``GET /admin/platform?days=30`` answers "how is each firm using Fondok, what
does it cost us, and what is going wrong?" for the people who run Fondok:
deals and documents, AI spend and error rate, engine failures, extraction
failures and stuck documents, verification quality, and who is active.

Access
------
Only a Fondok admin may call it: a Fondok staff admin (account-level role
on the verified JWT, any workspace) or an admin of a registered, active
``internal`` tenant — the same rule as ``GET /admin/tenants`` answering
``scope: "all"``. Everyone else gets 403, so an external firm can
never learn that another firm exists, let alone its numbers.

Isolation
---------
Nothing here bypasses row-level security. The firms come from the tenant
registry; for each one the endpoint publishes that tenant on
``current_tenant_id`` (the ``TenantSession`` hook then sets the Postgres GUC
for the transaction), runs aggregate-only SQL that also carries
``tenant_id = :t``, and commits before moving to the next firm — the same
plan the background scans use (``services/tenant_scan.py``). Only counts,
sums and timestamps leave the database: no deal names, no document text.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, require_role_or_staff
from ..auth.context import current_tenant_id
from ..budget import _price_for
from ..database import get_session_factory
from ..tenants import Tenant, caller_is_fondok_admin, list_tenants

logger = logging.getLogger(__name__)
router = APIRouter()

ALLOWED_DAYS = (7, 30, 90)
#: A document still pending/processing after this long is reported as stuck.
STUCK_AFTER = timedelta(minutes=30)
#: An active firm with no activity for this long is flagged as quiet.
QUIET_AFTER = timedelta(days=14)
#: Failure-rate threshold above which a firm gets an "attention" signal.
FAILURE_RATE_ALERT = 0.05
#: Average verification pass rate below which quality is flagged.
PASS_RATE_ALERT = 0.80


# ─────────────────────────── small helpers ───────────────────────────


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    s = str(value)
    return s or None


def _as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _day(value: Any) -> str:
    """``DATE(created_at)`` comes back as a ``date`` on Postgres and a string
    on SQLite; normalise to ``YYYY-MM-DD``."""
    return str(value)[:10]


def _token_cost(model: str, plain_in: int, out: int, cc: int, cr: int) -> float:
    """Cost of tokens whose row stored no ``cost_usd`` (mirrors admin_cost)."""
    in_price, out_price = _price_for(model)
    return (plain_in * in_price + cc * in_price * 1.25 + cr * in_price * 0.10 + out * out_price) / 1_000_000


async def _rows(session: AsyncSession, sql: str, params: dict[str, Any], label: str) -> list[dict[str, Any]]:
    """Run one aggregate query; a missing table or column degrades to ``[]``
    (logged) so one broken read never blanks the whole page."""
    try:
        result = await session.execute(text(sql), params)
        return [dict(r._mapping) for r in result]
    except Exception as exc:
        logger.info("admin_platform: %s unavailable (%s)", label, exc)
        await session.rollback()
        # Rollback ended the transaction; re-publishing is automatic because
        # current_tenant_id is still set for the next autobegin.
        return []


# ─────────────────────────── per-firm rollup ───────────────────────────


async def _firm_rollup(session: AsyncSession, tenant: str, since: datetime, now: datetime) -> dict[str, Any]:
    p = {"t": tenant, "since": since}

    deals_by_status = await _rows(
        session,
        "SELECT COALESCE(status, 'unknown') AS status, COUNT(*) AS n FROM deals "
        "WHERE tenant_id = :t GROUP BY COALESCE(status, 'unknown')",
        p,
        "deals.status",
    )
    deal_counts = await _rows(
        session,
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN created_at >= :since THEN 1 ELSE 0 END) AS created, "
        "SUM(CASE WHEN updated_at >= :since THEN 1 ELSE 0 END) AS touched "
        "FROM deals WHERE tenant_id = :t",
        p,
        "deals.counts",
    )
    deals_daily = await _rows(
        session,
        "SELECT DATE(created_at) AS day, COUNT(*) AS n FROM deals "
        "WHERE tenant_id = :t AND created_at >= :since GROUP BY DATE(created_at)",
        p,
        "deals.daily",
    )

    docs_by_status = await _rows(
        session,
        "SELECT UPPER(COALESCE(status, 'UNKNOWN')) AS status, COUNT(*) AS n, "
        "COALESCE(SUM(page_count), 0) AS pages FROM documents "
        "WHERE tenant_id = :t AND uploaded_at >= :since "
        "GROUP BY UPPER(COALESCE(status, 'UNKNOWN'))",
        p,
        "documents.status",
    )
    docs_stuck = await _rows(
        session,
        "SELECT COUNT(*) AS n FROM documents WHERE tenant_id = :t "
        "AND UPPER(COALESCE(status, '')) NOT IN ('EXTRACTED', 'FAILED') "
        "AND COALESCE(status_changed_at, uploaded_at) < :stuck_before",
        {**p, "stuck_before": now - STUCK_AFTER},
        "documents.stuck",
    )
    docs_by_type = await _rows(
        session,
        "SELECT COALESCE(doc_type, 'unknown') AS doc_type, COUNT(*) AS n, "
        "SUM(CASE WHEN UPPER(COALESCE(status, '')) = 'FAILED' THEN 1 ELSE 0 END) AS failed "
        "FROM documents WHERE tenant_id = :t AND uploaded_at >= :since "
        "GROUP BY COALESCE(doc_type, 'unknown')",
        p,
        "documents.type",
    )

    ai_groups = await _rows(
        session,
        "SELECT COALESCE(agent_name, 'unknown') AS agent, COALESCE(model, 'unknown') AS model, "
        "COUNT(*) AS calls, "
        "SUM(CASE WHEN LOWER(COALESCE(status, 'ok')) IN ('ok', 'success') THEN 0 ELSE 1 END) AS errors, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN cost_usd ELSE 0 END), 0) AS stored_cost, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE input_tokens END), 0) AS z_in, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE output_tokens END), 0) AS z_out, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE cache_creation_tokens END), 0) AS z_cc, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE cache_read_tokens END), 0) AS z_cr, "
        "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
        "COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens, "
        "COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens, "
        "COALESCE(SUM(output_tokens), 0) AS output_tokens, "
        "AVG(latency_ms) AS avg_latency_ms "
        "FROM model_calls WHERE tenant_id = :t AND created_at >= :since "
        "GROUP BY COALESCE(agent_name, 'unknown'), COALESCE(model, 'unknown')",
        p,
        "model_calls.groups",
    )
    ai_daily = await _rows(
        session,
        "SELECT DATE(created_at) AS day, COALESCE(model, 'unknown') AS model, COUNT(*) AS calls, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN cost_usd ELSE 0 END), 0) AS stored_cost, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE input_tokens END), 0) AS z_in, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE output_tokens END), 0) AS z_out, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE cache_creation_tokens END), 0) AS z_cc, "
        "COALESCE(SUM(CASE WHEN cost_usd > 0 THEN 0 ELSE cache_read_tokens END), 0) AS z_cr "
        "FROM model_calls WHERE tenant_id = :t AND created_at >= :since "
        "GROUP BY DATE(created_at), COALESCE(model, 'unknown')",
        p,
        "model_calls.daily",
    )
    ai_deals = await _rows(
        session,
        "SELECT COUNT(DISTINCT deal_id) AS n FROM model_calls "
        "WHERE tenant_id = :t AND created_at >= :since AND deal_id IS NOT NULL",
        p,
        "model_calls.deals",
    )

    engines = await _rows(
        session,
        "SELECT COALESCE(engine_name, 'unknown') AS engine, COUNT(*) AS runs, "
        "SUM(CASE WHEN LOWER(COALESCE(status, '')) IN ('failed', 'error') THEN 1 ELSE 0 END) AS failed, "
        "AVG(runtime_ms) AS avg_runtime_ms, MAX(CASE WHEN LOWER(COALESCE(status, '')) IN ('failed', 'error') "
        "THEN started_at END) AS last_failed_at "
        "FROM engine_outputs WHERE tenant_id = :t AND started_at >= :since "
        "GROUP BY COALESCE(engine_name, 'unknown')",
        p,
        "engine_outputs",
    )
    engine_runs = await _rows(
        session,
        "SELECT COUNT(DISTINCT run_id) AS n FROM engine_outputs WHERE tenant_id = :t AND started_at >= :since",
        p,
        "engine_outputs.runs",
    )

    activity = await _rows(
        session,
        "SELECT COUNT(*) AS events, COUNT(DISTINCT actor_id) AS people FROM audit_log "
        "WHERE tenant_id = :t AND created_at >= :since",
        p,
        "audit_log.window",
    )
    last_activity = await _rows(
        session,
        "SELECT MAX(created_at) AS at FROM audit_log WHERE tenant_id = :t",
        p,
        "audit_log.last",
    )
    actions = await _rows(
        session,
        "SELECT action, COUNT(*) AS n FROM audit_log WHERE tenant_id = :t AND created_at >= :since "
        "GROUP BY action ORDER BY COUNT(*) DESC LIMIT 12",
        p,
        "audit_log.actions",
    )
    activity_daily = await _rows(
        session,
        "SELECT DATE(created_at) AS day, COUNT(*) AS n, COUNT(DISTINCT actor_id) AS people FROM audit_log "
        "WHERE tenant_id = :t AND created_at >= :since GROUP BY DATE(created_at)",
        p,
        "audit_log.daily",
    )
    people = await _rows(
        session,
        "SELECT actor_id, COUNT(*) AS events, MAX(created_at) AS last_at FROM audit_log "
        "WHERE tenant_id = :t AND created_at >= :since AND actor_id IS NOT NULL "
        "GROUP BY actor_id ORDER BY COUNT(*) DESC LIMIT 25",
        p,
        "audit_log.people",
    )

    memo_edits = await _rows(
        session,
        "SELECT COUNT(*) AS n FROM memo_edits WHERE tenant_id = :t AND created_at >= :since",
        p,
        "memo_edits",
    )
    comments = await _rows(
        session,
        "SELECT SUM(CASE WHEN created_at >= :since THEN 1 ELSE 0 END) AS created, "
        "SUM(CASE WHEN resolved_at IS NULL THEN 1 ELSE 0 END) AS open "
        "FROM cell_comments WHERE tenant_id = :t",
        p,
        "cell_comments",
    )
    verification = await _rows(
        session,
        "SELECT COUNT(*) AS n, AVG(pass_rate) AS avg_pass_rate, MIN(pass_rate) AS min_pass_rate "
        "FROM verification_reports WHERE tenant_id = :t AND created_at >= :since",
        p,
        "verification_reports",
    )
    critic = await _rows(
        session,
        "SELECT LOWER(COALESCE(severity, 'unknown')) AS severity, COUNT(*) AS n FROM critic_findings "
        "WHERE tenant_id = :t AND created_at >= :since GROUP BY LOWER(COALESCE(severity, 'unknown'))",
        p,
        "critic_findings",
    )

    # ── shape ──
    def first(rows: list[dict[str, Any]], key: str, default: Any = 0) -> Any:
        if not rows:
            return default
        v = rows[0].get(key)
        return default if v is None else v

    ai_cost = 0.0
    ai_calls = 0
    ai_errors = 0
    tok = {"input": 0, "cache_read": 0, "cache_creation": 0, "output": 0}
    latency_weighted = 0.0
    by_agent: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    for g in ai_groups:
        cost = float(g["stored_cost"] or 0) + _token_cost(
            str(g["model"]), int(g["z_in"] or 0), int(g["z_out"] or 0), int(g["z_cc"] or 0), int(g["z_cr"] or 0)
        )
        calls = int(g["calls"] or 0)
        errors = int(g["errors"] or 0)
        ai_cost += cost
        ai_calls += calls
        ai_errors += errors
        tok["input"] += int(g["input_tokens"] or 0)
        tok["cache_read"] += int(g["cache_read_tokens"] or 0)
        tok["cache_creation"] += int(g["cache_creation_tokens"] or 0)
        tok["output"] += int(g["output_tokens"] or 0)
        if g.get("avg_latency_ms") is not None:
            latency_weighted += float(g["avg_latency_ms"]) * calls
        a = by_agent.setdefault(g["agent"], {"agent": g["agent"], "calls": 0, "errors": 0, "cost_usd": 0.0})
        a["calls"] += calls
        a["errors"] += errors
        a["cost_usd"] += cost
        m = by_model.setdefault(g["model"], {"model": g["model"], "calls": 0, "cost_usd": 0.0})
        m["calls"] += calls
        m["cost_usd"] += cost

    cost_daily: dict[str, float] = {}
    calls_daily: dict[str, int] = {}
    for d in ai_daily:
        day = _day(d["day"])
        cost_daily[day] = cost_daily.get(day, 0.0) + float(d["stored_cost"] or 0) + _token_cost(
            str(d["model"]), int(d["z_in"] or 0), int(d["z_out"] or 0), int(d["z_cc"] or 0), int(d["z_cr"] or 0)
        )
        calls_daily[day] = calls_daily.get(day, 0) + int(d["calls"] or 0)

    cache_denom = tok["input"] + tok["cache_read"] + tok["cache_creation"]
    docs_status = {str(r["status"]): int(r["n"] or 0) for r in docs_by_status}
    docs_total = sum(docs_status.values())
    docs_failed = docs_status.get("FAILED", 0)
    engine_list = [
        {
            "engine": e["engine"],
            "runs": int(e["runs"] or 0),
            "failed": int(e["failed"] or 0),
            "avg_runtime_ms": round(float(e["avg_runtime_ms"]), 1) if e.get("avg_runtime_ms") is not None else None,
            "last_failed_at": _iso(e.get("last_failed_at")),
        }
        for e in engines
    ]
    engine_total = sum(e["runs"] for e in engine_list)
    engine_failed = sum(e["failed"] for e in engine_list)
    deals_with_ai = int(first(ai_deals, "n"))

    return {
        "deals": {
            "total": int(first(deal_counts, "total")),
            "created": int(first(deal_counts, "created")),
            "touched": int(first(deal_counts, "touched")),
            "by_status": sorted(
                ({"status": str(r["status"]), "count": int(r["n"] or 0)} for r in deals_by_status),
                key=lambda r: -r["count"],
            ),
            "daily": {_day(r["day"]): int(r["n"] or 0) for r in deals_daily},
        },
        "documents": {
            "uploaded": docs_total,
            "extracted": docs_status.get("EXTRACTED", 0),
            "failed": docs_failed,
            "stuck": int(first(docs_stuck, "n")),
            "pages": sum(int(r["pages"] or 0) for r in docs_by_status),
            "failure_rate": round(docs_failed / docs_total, 4) if docs_total else None,
            "by_type": sorted(
                (
                    {"doc_type": str(r["doc_type"]), "count": int(r["n"] or 0), "failed": int(r["failed"] or 0)}
                    for r in docs_by_type
                ),
                key=lambda r: -r["count"],
            ),
        },
        "ai": {
            "calls": ai_calls,
            "errors": ai_errors,
            "error_rate": round(ai_errors / ai_calls, 4) if ai_calls else None,
            "cost_usd": round(ai_cost, 4),
            "cost_per_deal_usd": round(ai_cost / deals_with_ai, 4) if deals_with_ai else None,
            "deals_with_ai": deals_with_ai,
            "cache_hit_rate": round(tok["cache_read"] / cache_denom, 4) if cache_denom else None,
            "avg_latency_ms": round(latency_weighted / ai_calls, 1) if ai_calls and latency_weighted else None,
            "tokens": tok,
            "by_agent": sorted(
                ({**a, "cost_usd": round(a["cost_usd"], 4)} for a in by_agent.values()), key=lambda r: -r["cost_usd"]
            )[:12],
            "by_model": sorted(
                ({**m, "cost_usd": round(m["cost_usd"], 4)} for m in by_model.values()), key=lambda r: -r["cost_usd"]
            ),
            "daily_cost_usd": {k: round(v, 4) for k, v in cost_daily.items()},
            "daily_calls": calls_daily,
        },
        "engines": {
            "model_runs": int(first(engine_runs, "n")),
            "outputs": engine_total,
            "failed": engine_failed,
            "failure_rate": round(engine_failed / engine_total, 4) if engine_total else None,
            "by_engine": sorted(engine_list, key=lambda r: (-r["failed"], -r["runs"])),
        },
        "activity": {
            "events": int(first(activity, "events")),
            "active_people": int(first(activity, "people")),
            "last_activity_at": _iso(first(last_activity, "at", None)),
            "top_actions": [{"action": str(r["action"]), "count": int(r["n"] or 0)} for r in actions],
            "daily_events": {_day(r["day"]): int(r["n"] or 0) for r in activity_daily},
            "daily_people": {_day(r["day"]): int(r["people"] or 0) for r in activity_daily},
            "people": [
                {"actor_id": str(r["actor_id"]), "events": int(r["events"] or 0), "last_at": _iso(r.get("last_at"))}
                for r in people
            ],
        },
        "collaboration": {
            "memo_edits": int(first(memo_edits, "n")),
            "comments_created": int(first(comments, "created")),
            "comments_open": int(first(comments, "open")),
        },
        "quality": {
            "verification_reports": int(first(verification, "n")),
            "avg_pass_rate": round(float(first(verification, "avg_pass_rate", 0)), 4)
            if first(verification, "avg_pass_rate", None) is not None
            else None,
            "min_pass_rate": round(float(first(verification, "min_pass_rate", 0)), 4)
            if first(verification, "min_pass_rate", None) is not None
            else None,
            "critic_findings": {str(r["severity"]): int(r["n"] or 0) for r in critic},
        },
    }


# ─────────────────────────── signals ───────────────────────────


def _signals(tenant: Tenant, firm: dict[str, Any], now: datetime, days: int) -> list[dict[str, Any]]:
    """Plain-language "needs attention" / "going well" lines for one firm.

    Every line is derived from the numbers above — no thresholds hide data;
    they only decide which lines get surfaced first.
    """
    out: list[dict[str, Any]] = []
    name = tenant.name

    def add(kind: str, area: str, text_: str) -> None:
        out.append({"kind": kind, "area": area, "tenant_id": str(tenant.id), "firm": name, "text": text_})

    if tenant.status == "pending_review":
        add("bad", "access", "Waiting for approval: its team cannot sign in until a Fondok admin approves the firm.")
    docs = firm["documents"]
    if docs["stuck"]:
        add("bad", "extraction", f"{docs['stuck']} document(s) stuck in processing for over 30 minutes.")
    if docs["failed"]:
        worst = next((t for t in docs["by_type"] if t["failed"]), None)
        tail = f" Most failures: {worst['doc_type']}." if worst else ""
        add("bad", "extraction", f"{docs['failed']} of {docs['uploaded']} uploaded document(s) failed to extract.{tail}")
    eng = firm["engines"]
    if eng["failed"] and (eng["failure_rate"] or 0) >= FAILURE_RATE_ALERT:
        top = next((e for e in eng["by_engine"] if e["failed"]), None)
        tail = f" Worst: {top['engine']} ({top['failed']} failed)." if top else ""
        add("bad", "model", f"{eng['failed']} model step(s) failed ({round((eng['failure_rate'] or 0) * 100)}%).{tail}")
    ai = firm["ai"]
    if ai["errors"] and (ai["error_rate"] or 0) >= FAILURE_RATE_ALERT:
        add("bad", "ai", f"{ai['errors']} AI call(s) errored ({round((ai['error_rate'] or 0) * 100)}% of {ai['calls']}).")
    q = firm["quality"]
    if q["avg_pass_rate"] is not None and q["avg_pass_rate"] < PASS_RATE_ALERT:
        add("bad", "quality", f"Verification pass rate averages {round(q['avg_pass_rate'] * 100)}%.")
    high = q["critic_findings"].get("high", 0) + q["critic_findings"].get("critical", 0)
    if high:
        add("bad", "quality", f"{high} high-severity reviewer finding(s) raised on its deals.")
    last = _as_dt(firm["activity"]["last_activity_at"])
    # Fondok's own workspace is not a customer: no adoption warnings for it.
    if tenant.status == "active" and tenant.kind != "internal":
        if last is None:
            add("bad", "adoption", "No activity recorded yet.")
        elif now - last > QUIET_AFTER:
            add("bad", "adoption", f"Quiet: no activity for {(now - last).days} days.")
    if firm["collaboration"]["comments_open"] >= 10:
        add("bad", "collaboration", f"{firm['collaboration']['comments_open']} open comments waiting for an answer.")

    deals = firm["deals"]
    if deals["created"]:
        add("good", "adoption", f"{deals['created']} new deal(s) in the last {days} days.")
    if firm["activity"]["active_people"] >= 2:
        add("good", "adoption", f"{firm['activity']['active_people']} people active in the last {days} days.")
    if docs["uploaded"] and not docs["failed"] and not docs["stuck"]:
        add("good", "extraction", f"All {docs['uploaded']} uploaded document(s) extracted cleanly.")
    if q["avg_pass_rate"] is not None and q["avg_pass_rate"] >= 0.9:
        add("good", "quality", f"Verification pass rate averages {round(q['avg_pass_rate'] * 100)}%.")
    if eng["outputs"] and not eng["failed"]:
        add("good", "model", f"Every model step ran cleanly ({eng['model_runs']} run(s)).")
    if firm["collaboration"]["memo_edits"] or firm["collaboration"]["comments_created"]:
        add(
            "good",
            "collaboration",
            f"{firm['collaboration']['memo_edits']} memo edit(s) and "
            f"{firm['collaboration']['comments_created']} comment(s) — the team is working in Fondok.",
        )
    return out


# ─────────────────────────── endpoint ───────────────────────────


async def _require_internal(auth: AuthContext) -> None:
    if not await caller_is_fondok_admin(auth):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="internal_admin_only")


def _sum_daily(firms: list[dict[str, Any]], path: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, float]:
    total: dict[str, float] = {}
    for f in firms:
        for day, v in path(f).items():
            total[day] = total.get(day, 0) + v
    return total


async def _collect(
    tenants: list[Tenant],
    since: datetime,
    now: datetime,
    rollup: Callable[[AsyncSession, str, datetime, datetime], Awaitable[dict[str, Any]]] = _firm_rollup,
) -> list[tuple[Tenant, dict[str, Any]]]:
    out: list[tuple[Tenant, dict[str, Any]]] = []
    caller_tenant = current_tenant_id.get()
    try:
        async with get_session_factory()() as session:
            for t in tenants:
                # Publish THIS firm before its transaction autobegins so the
                # RLS GUC names it; commit after so the next firm starts clean.
                current_tenant_id.set(str(t.id))
                try:
                    out.append((t, await rollup(session, str(t.id), since, now)))
                finally:
                    await session.commit()
    finally:
        current_tenant_id.set(caller_tenant)
    return out


@router.get("/platform")
async def admin_platform(
    auth: Annotated[AuthContext, Depends(require_role_or_staff("admin"))],
    days: Annotated[int, Query()] = 30,
) -> dict[str, Any]:
    """Every firm's usage, cost, failures and quality over the last ``days``."""
    await _require_internal(auth)
    if days not in ALLOWED_DAYS:
        raise HTTPException(status_code=422, detail=f"days must be one of {list(ALLOWED_DAYS)}")
    now = datetime.now(UTC)
    since = now - timedelta(days=days)

    tenants = await list_tenants()
    collected = await _collect(tenants, since, now)

    firms: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []
    for t, data in collected:
        firm_signals = _signals(t, data, now, days)
        signals.extend(firm_signals)
        firms.append(
            {
                "tenant_id": str(t.id),
                "clerk_org_id": t.clerk_org_id,
                "name": t.name,
                "kind": t.kind,
                "status": t.status,
                "created_at": _iso(t.created_at),
                "attention": sum(1 for s in firm_signals if s["kind"] == "bad"),
                **data,
            }
        )

    def total(path: Callable[[dict[str, Any]], float | int]) -> float:
        return sum(path(f) for f in firms)

    ai_calls = int(total(lambda f: f["ai"]["calls"]))
    ai_errors = int(total(lambda f: f["ai"]["errors"]))
    docs_up = int(total(lambda f: f["documents"]["uploaded"]))
    docs_failed = int(total(lambda f: f["documents"]["failed"]))
    eng_out = int(total(lambda f: f["engines"]["outputs"]))
    eng_failed = int(total(lambda f: f["engines"]["failed"]))
    cost = total(lambda f: f["ai"]["cost_usd"])

    # Cost by model across firms (what to optimise first).
    by_model: dict[str, dict[str, Any]] = {}
    for f in firms:
        for m in f["ai"]["by_model"]:
            row = by_model.setdefault(m["model"], {"model": m["model"], "calls": 0, "cost_usd": 0.0})
            row["calls"] += m["calls"]
            row["cost_usd"] += m["cost_usd"]
    by_agent: dict[str, dict[str, Any]] = {}
    for f in firms:
        for a in f["ai"]["by_agent"]:
            row = by_agent.setdefault(a["agent"], {"agent": a["agent"], "calls": 0, "errors": 0, "cost_usd": 0.0})
            row["calls"] += a["calls"]
            row["errors"] += a["errors"]
            row["cost_usd"] += a["cost_usd"]

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "since": since.isoformat(),
        "totals": {
            "firms": len(firms),
            "firms_active": sum(1 for f in firms if f["status"] == "active"),
            "firms_pending": sum(1 for f in firms if f["status"] == "pending_review"),
            "deals_total": int(total(lambda f: f["deals"]["total"])),
            "deals_created": int(total(lambda f: f["deals"]["created"])),
            "documents_uploaded": docs_up,
            "documents_failed": docs_failed,
            "documents_stuck": int(total(lambda f: f["documents"]["stuck"])),
            "document_failure_rate": round(docs_failed / docs_up, 4) if docs_up else None,
            "pages_processed": int(total(lambda f: f["documents"]["pages"])),
            "ai_calls": ai_calls,
            "ai_errors": ai_errors,
            "ai_error_rate": round(ai_errors / ai_calls, 4) if ai_calls else None,
            "ai_cost_usd": round(cost, 4),
            "model_runs": int(total(lambda f: f["engines"]["model_runs"])),
            "engine_failure_rate": round(eng_failed / eng_out, 4) if eng_out else None,
            "active_people": int(total(lambda f: f["activity"]["active_people"])),
            "events": int(total(lambda f: f["activity"]["events"])),
            "memo_edits": int(total(lambda f: f["collaboration"]["memo_edits"])),
            "comments_created": int(total(lambda f: f["collaboration"]["comments_created"])),
        },
        "daily": {
            "cost_usd": {k: round(v, 4) for k, v in _sum_daily(firms, lambda f: f["ai"]["daily_cost_usd"]).items()},
            "events": _sum_daily(firms, lambda f: f["activity"]["daily_events"]),
            "deals_created": _sum_daily(firms, lambda f: f["deals"]["daily"]),
        },
        "cost_by_model": sorted(
            ({**m, "cost_usd": round(m["cost_usd"], 4)} for m in by_model.values()), key=lambda r: -r["cost_usd"]
        ),
        "cost_by_agent": sorted(
            ({**a, "cost_usd": round(a["cost_usd"], 4)} for a in by_agent.values()), key=lambda r: -r["cost_usd"]
        )[:15],
        "signals": sorted(signals, key=lambda s: (s["kind"] != "bad", s["firm"])),
        "firms": sorted(firms, key=lambda f: (-f["attention"], -f["ai"]["cost_usd"], f["name"].lower())),
    }
