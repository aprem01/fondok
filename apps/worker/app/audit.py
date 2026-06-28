"""Centralized append-only audit logging.

Every state-changing operation in the worker writes an ``audit_log``
row via :func:`log_audit`. The contract that lets us hand this to a
Blackstone / Brookfield IT review:

* **Append-only** — Postgres has a trigger blocking ``UPDATE``/``DELETE``
  on ``audit_log`` (see :mod:`app.migrations`). SQLite (dev-only) keeps
  the same shape but can't enforce the trigger.
* **Tamper-evident** — every row stores a SHA-256 of the canonical-JSON
  input and output payloads so we can prove later what the caller saw
  and what the system returned.
* **Best-effort** — :func:`log_audit` never raises out. Audit logging is
  observability, not business logic; a logging failure must never roll
  back the caller's mutation.

The helper coexists with whatever transaction the caller is running:
we ``flush`` (so the row is staged), but we leave commit semantics to
the caller — that way a failed audit write inside a wider transaction
rolls back together with the mutation it was tracking.

Wave 4 W4.3 adds the columns the Activity Feed UI + tenant-wide
Compliance Explorer surface from: ``actor_email`` / ``actor_ip`` /
``user_agent`` (who, from where), ``before`` / ``after`` / ``diff_summary``
(what changed), ``severity`` ('info' / 'warning' / 'critical'), and
``tags`` (free-form labels). Every new column is nullable + defaulted
at the DB layer so existing log_audit callers keep working unchanged.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# Severity vocabulary mirrored by the explorer UI's filter chips.
# 'critical' marks cross-tenant breach attempts + budget kill-switch
# trips so the Compliance Explorer's red badge can light up.
_VALID_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "critical"})


def _sha256_canonical(payload: Any) -> str:
    """SHA-256 of a deterministically encoded JSON payload.

    ``sort_keys=True`` + ``default=str`` give us a byte-for-byte stable
    hash even across Python versions / dict insertion orders, and
    transparently coerce ``UUID``/``datetime``/``Decimal`` values that
    ``json.dumps`` would otherwise reject.
    """
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _is_sqlite_session(session: AsyncSession) -> bool:
    return (
        session.bind is not None
        and session.bind.dialect.name == "sqlite"
    )


def build_diff_summary(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    max_fields: int = 3,
) -> str | None:
    """Return a one-line ``"field: x → y"`` summary of a before/after diff.

    Used by the Activity Feed row so the UI doesn't have to crack open
    the full JSON to render a single line of "what changed". When more
    than ``max_fields`` keys differ we truncate with ``"…"``. Returns
    ``None`` when there's no diff to show (both sides None / identical).
    """
    if not isinstance(before, dict) and not isinstance(after, dict):
        return None
    b = before if isinstance(before, dict) else {}
    a = after if isinstance(after, dict) else {}
    keys = sorted(set(b.keys()) | set(a.keys()))
    parts: list[str] = []
    for k in keys:
        if b.get(k) == a.get(k):
            continue
        parts.append(f"{k}: {b.get(k)!r} → {a.get(k)!r}")
        if len(parts) >= max_fields:
            break
    if not parts:
        return None
    rest = max(0, len([k for k in keys if b.get(k) != a.get(k)]) - max_fields)
    suffix = f" (+{rest} more)" if rest else ""
    return ", ".join(parts) + suffix


async def log_audit(
    session: AsyncSession,
    *,
    tenant_id: UUID | str,
    actor_id: UUID | str | None = None,
    action: str,
    resource_type: str,
    resource_id: UUID | str | None = None,
    input_payload: Any = None,
    output_payload: Any = None,
    metadata: dict[str, Any] | None = None,
    # ─── Wave 4 W4.3 — Activity Feed extensions (all optional) ───
    actor_email: str | None = None,
    actor_ip: str | None = None,
    user_agent: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    diff_summary: str | None = None,
    severity: str = "info",
    tags: list[str] | None = None,
    deal_id: UUID | str | None = None,
) -> None:
    """Insert one append-only ``audit_log`` row. Never raises out.

    Parameters
    ----------
    session
        The active ``AsyncSession``. The caller owns commit semantics;
        we only ``flush`` so the row is staged.
    tenant_id
        Tenant the action took place under. Required for multi-tenant
        isolation when the audit trail is later sliced.
    actor_id
        Who initiated the action. ``None`` is recorded as ``"system"``
        (e.g. a scheduled job, a back-fill).
    action
        Dotted verb describing the event — e.g. ``deal.created``,
        ``memo.edited``, ``document.uploaded``. Conventions live in the
        callers; the audit table doesn't enforce a vocabulary.
    resource_type
        High-level entity the action targeted (``deal``, ``document``,
        ``memo``, ``scenario``, ``override``, ``engine_run``, ``export``,
        ``comp_transaction``, ``portfolio_library_entry``). Used by audit
        dashboards to filter.
    resource_id
        Concrete entity id. When ``resource_type == 'deal'`` this is
        also written into the dedicated ``deal_id`` column so the
        deal-scoped audit query stays a simple equality lookup.
    input_payload, output_payload
        Caller-supplied dicts (or any JSON-serialisable shape). Both
        are SHA-256 hashed and the originals stored under the
        ``payload`` JSONB column so we can verify integrity later
        without trusting the surrounding row.
    metadata
        Free-form context (request id, trace id, …) bundled into
        ``payload['metadata']``.
    actor_email, actor_ip, user_agent
        Display-side identity for the Activity Feed row. All optional —
        the demo persona writes ``actor_id='system'`` with these unset.
    before, after
        Small JSON snapshots of the prior + new state. The Activity
        Feed side-panel renders these as a diff view. Keep the payload
        small (the columns are indexed JSONB on Postgres; storing a
        100KB blob is the wrong vehicle).
    diff_summary
        One-line ``"field_path: 0.07 → 0.075"`` summary used in the
        Activity Feed row. When omitted but ``before``/``after`` are
        dicts, we compute one with :func:`build_diff_summary`.
    severity
        ``'info'`` (default) | ``'warning'`` | ``'critical'``. Anything
        else falls back to ``'info'`` so a typo never escalates a row.
    tags
        Free-form labels rendered as small pills (e.g. ``['wave2',
        'override']``). Stored as a JSON list.
    """
    try:
        input_hash = (
            _sha256_canonical(input_payload) if input_payload is not None else None
        )
        output_hash = (
            _sha256_canonical(output_payload) if output_payload is not None else None
        )

        # Deal-scoped actions populate the dedicated ``deal_id`` column
        # so the existing ``idx_audit_log_deal`` index is hit directly.
        # Wave 4 W4.3 — non-deal resources (scenario, override, engine_run,
        # comp_transaction, export) can also carry a ``deal_id`` so the
        # per-deal Activity Feed surface joins them against the right
        # deal page. When the caller doesn't pass ``deal_id`` directly
        # we fall back to ``metadata['deal_id']`` (existing convention).
        if deal_id is not None:
            deal_col = str(deal_id)
        elif resource_type == "deal" and resource_id is not None:
            deal_col = str(resource_id)
        elif isinstance(metadata, dict) and metadata.get("deal_id"):
            deal_col = str(metadata["deal_id"])
        else:
            deal_col = None

        payload_blob = {
            "input": input_payload,
            "output": output_payload,
            "metadata": metadata,
        }

        # Auto-compute the diff summary when callers supply before/after
        # but no manual summary — keeps every callsite from having to
        # repeat the same one-liner construction.
        if diff_summary is None and (before is not None or after is not None):
            diff_summary = build_diff_summary(before, after)

        sev = severity if severity in _VALID_SEVERITIES else "info"

        is_sqlite = _is_sqlite_session(session)
        # JSONB columns on Postgres want an explicit CAST when we bind a
        # JSON string; SQLite stores them as TEXT and accepts the string
        # directly. Build the SQL fragments accordingly.
        before_blob = json.dumps(before, default=str) if before is not None else None
        after_blob = json.dumps(after, default=str) if after is not None else None
        tags_blob = json.dumps(tags, default=str) if tags is not None else None

        if is_sqlite:
            before_frag = ":before"
            after_frag = ":after"
            tags_frag = ":tags"
        else:
            before_frag = "CAST(:before AS JSONB)"
            after_frag = "CAST(:after AS JSONB)"
            tags_frag = "CAST(:tags AS JSONB)"

        await session.execute(
            text(
                f"""
                INSERT INTO audit_log (
                    id, tenant_id, deal_id, actor_id, action,
                    resource_type, resource_id, input_hash, output_hash,
                    payload, created_at,
                    actor_email, actor_ip, user_agent,
                    before, after, diff_summary, severity, tags
                ) VALUES (
                    :id, :tenant, :deal, :actor, :action,
                    :rtype, :rid, :ihash, :ohash, :payload, :ts,
                    :actor_email, :actor_ip, :user_agent,
                    {before_frag}, {after_frag},
                    :diff_summary, :severity, {tags_frag}
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant": str(tenant_id),
                "deal": deal_col,
                "actor": str(actor_id) if actor_id is not None else "system",
                "action": action,
                "rtype": resource_type,
                "rid": str(resource_id) if resource_id is not None else None,
                "ihash": input_hash,
                "ohash": output_hash,
                "payload": json.dumps(payload_blob, default=str),
                "ts": datetime.now(UTC),
                "actor_email": actor_email,
                "actor_ip": actor_ip,
                "user_agent": user_agent,
                "before": before_blob,
                "after": after_blob,
                "diff_summary": diff_summary,
                "severity": sev,
                "tags": tags_blob,
            },
        )
        await session.flush()
    except Exception as exc:  # noqa: BLE001 — audit must never raise out
        logger.warning(
            "log_audit write failed (action=%s rtype=%s rid=%s): %s",
            action,
            resource_type,
            resource_id,
            exc,
        )


# Columns returned by ``list_audit_log`` + ``search_audit_log`` so the
# downstream callers (API serializer + tests) stay in one place. Single
# source of truth for "what fields does the audit row carry".
_AUDIT_SELECT_COLUMNS = (
    "id, tenant_id, deal_id, actor_id, action, "
    "resource_type, resource_id, input_hash, output_hash, "
    "payload, created_at, actor_email, actor_ip, user_agent, "
    "before, after, diff_summary, severity, tags"
)


async def list_audit_log(
    session: AsyncSession,
    *,
    tenant_id: UUID | str,
    limit: int = 100,
    actor_id: UUID | str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: UUID | str | None = None,
    deal_id: UUID | str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return ``audit_log`` rows for ``tenant_id``, newest first.

    **ALWAYS tenant-scoped.** No code path may read ``audit_log`` without
    passing ``tenant_id`` — the signature enforces it by making the
    argument keyword-only and required. This is the only sanctioned
    reader; ad-hoc ``SELECT * FROM audit_log`` calls elsewhere in the
    codebase are explicitly disallowed and will be caught by the
    SQLAlchemy ``tenant_middleware`` listener.

    The shape of the returned dicts mirrors the ``audit_log`` table:
    ``id``, ``tenant_id``, ``deal_id``, ``actor_id``, ``action``,
    ``resource_type``, ``resource_id``, ``input_hash``, ``output_hash``,
    ``payload`` (JSONB or JSON string depending on dialect),
    ``created_at`` + the Wave 4 W4.3 columns ``actor_email`` /
    ``actor_ip`` / ``user_agent`` / ``before`` / ``after`` /
    ``diff_summary`` / ``severity`` / ``tags``.

    Parameters
    ----------
    tenant_id
        Required. The query is rejected (``ValueError``) if missing or
        empty — guards against ``None`` slipping in from an unauthenticated
        code path.
    limit
        Hard-capped at 1000 to keep the dashboard query bounded.
    offset
        Pagination offset (default 0). Capped at 100_000 to prevent
        runaway scans.
    actor_id, action, resource_type, resource_id, deal_id, severity
        Optional filters. All match exactly; no LIKE / fuzzy.
    since, until
        Optional inclusive ``created_at`` bounds (UTC). Useful when
        the IT review asks "show me everything between Jan 1 and Mar 31".

    Raises
    ------
    ValueError
        If ``tenant_id`` is falsy. Better to crash than to return a
        cross-tenant result set.
    """
    if not tenant_id:
        raise ValueError(
            "list_audit_log: tenant_id is required — cross-tenant reads disallowed"
        )

    limit = max(1, min(int(limit), 1000))
    offset = max(0, min(int(offset), 100_000))

    clauses: list[str] = ["tenant_id = :tenant"]
    params: dict[str, Any] = {
        "tenant": str(tenant_id),
        "limit": limit,
        "offset": offset,
    }

    if actor_id is not None:
        clauses.append("actor_id = :actor")
        params["actor"] = str(actor_id)
    if action is not None:
        clauses.append("action = :action")
        params["action"] = action
    if resource_type is not None:
        clauses.append("resource_type = :rtype")
        params["rtype"] = resource_type
    if resource_id is not None:
        clauses.append("resource_id = :rid")
        params["rid"] = str(resource_id)
    if deal_id is not None:
        clauses.append("deal_id = :did")
        params["did"] = str(deal_id)
    if severity is not None and severity in _VALID_SEVERITIES:
        clauses.append("severity = :sev")
        params["sev"] = severity
    if since is not None:
        clauses.append("created_at >= :since")
        params["since"] = since
    if until is not None:
        clauses.append("created_at <= :until")
        params["until"] = until

    where = " AND ".join(clauses)
    sql = text(
        f"""
        SELECT {_AUDIT_SELECT_COLUMNS}
          FROM audit_log
         WHERE {where}
         ORDER BY created_at DESC
         LIMIT :limit OFFSET :offset
        """
    )
    rows = (await session.execute(sql, params)).all()
    return [dict(r._mapping) for r in rows]


async def search_audit_log(
    session: AsyncSession,
    *,
    tenant_id: UUID | str,
    q: str | None = None,
    actor_id: str | None = None,
    entity_type: str | None = None,
    severity: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Tenant-wide audit search powering the Compliance Explorer.

    Returns ``(rows, total_count)`` so the UI can render pagination.
    Free-text ``q`` matches against ``action``, ``actor_id``,
    ``actor_email``, ``diff_summary``, and ``resource_id`` (case-insensitive
    substring on Postgres + SQLite). Everything else is exact-match.

    ``entity_type`` is exposed as a public-facing alias for the column
    ``resource_type`` because the Activity Feed UI speaks in those
    terms (deal / scenario / override / document / engine_run / export /
    comp_transaction / portfolio_library_entry).
    """
    if not tenant_id:
        raise ValueError(
            "search_audit_log: tenant_id is required — cross-tenant reads disallowed"
        )

    limit = max(1, min(int(limit), 1000))
    offset = max(0, min(int(offset), 100_000))

    clauses: list[str] = ["tenant_id = :tenant"]
    params: dict[str, Any] = {
        "tenant": str(tenant_id),
        "limit": limit,
        "offset": offset,
    }
    if actor_id:
        clauses.append("actor_id = :actor")
        params["actor"] = str(actor_id)
    if entity_type:
        clauses.append("resource_type = :rtype")
        params["rtype"] = entity_type
    if severity and severity in _VALID_SEVERITIES:
        clauses.append("severity = :sev")
        params["sev"] = severity
    if since is not None:
        clauses.append("created_at >= :since")
        params["since"] = since
    if until is not None:
        clauses.append("created_at <= :until")
        params["until"] = until
    if q:
        # LOWER() works identically on Postgres + SQLite, and using an
        # explicit lowercase comparison avoids the dialect-specific
        # ILIKE / collation differences. The trailing wildcard is
        # explicit in the parameter so users can search "deal." and
        # not accidentally match every row.
        clauses.append(
            "("
            "LOWER(COALESCE(action, '')) LIKE :q "
            "OR LOWER(COALESCE(actor_id, '')) LIKE :q "
            "OR LOWER(COALESCE(actor_email, '')) LIKE :q "
            "OR LOWER(COALESCE(diff_summary, '')) LIKE :q "
            "OR LOWER(COALESCE(resource_id, '')) LIKE :q"
            ")"
        )
        params["q"] = f"%{q.lower()}%"

    where = " AND ".join(clauses)
    sql = text(
        f"""
        SELECT {_AUDIT_SELECT_COLUMNS}
          FROM audit_log
         WHERE {where}
         ORDER BY created_at DESC
         LIMIT :limit OFFSET :offset
        """
    )
    count_sql = text(
        f"SELECT COUNT(*) FROM audit_log WHERE {where}"
    )
    rows = (await session.execute(sql, params)).all()
    # Count uses the same WHERE — strip the LIMIT/OFFSET params to keep
    # SQLAlchemy from complaining about unused binds.
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    count_row = (await session.execute(count_sql, count_params)).first()
    total = int(count_row[0]) if count_row is not None else 0
    return [dict(r._mapping) for r in rows], total


__all__ = [
    "log_audit",
    "list_audit_log",
    "search_audit_log",
    "build_diff_summary",
]
