"""Wave 4 W4.3 — Activity Feed + tenant-wide Compliance Explorer.

Two endpoints, both tenant-scoped via ``X-Tenant-Id`` (matches the rest
of the worker API surface):

* ``GET /deals/{id}/audit`` — paginated audit log for a single deal,
  newest first, with optional ``action`` / ``entity_type`` / ``severity``
  / ``since`` / ``until`` filters. Powers the per-deal Activity tab.

* ``GET /audit/explorer`` — tenant-wide search across every action /
  every actor. Powers the ``/audit`` page (Compliance Explorer). The
  payload includes a ``total`` count so the UI can render pagination.

The audit log itself is append-only (see :mod:`app.audit` + the
Postgres trigger in :mod:`app.migrations`). These endpoints are
read-only — no mutating verbs land in this router.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import list_audit_log, search_audit_log
from ..database import get_session
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── response shapes ──────────────────────────


class AuditEntry(BaseModel):
    """One row in the audit log surfaced to the Activity Feed.

    Mirrors :class:`app.audit.log_audit`'s persisted shape but with the
    JSONB columns parsed into Python objects on the way out. We deliver
    ``before``/``after``/``tags``/``payload`` as already-parsed dicts so
    the Next.js side never has to crack a JSON string.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    deal_id: str | None = None
    actor_id: str | None = None
    actor_email: str | None = None
    actor_ip: str | None = None
    user_agent: str | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    severity: str = "info"
    diff_summary: str | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    tags: list[str] | None = None
    payload: dict[str, Any] | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    created_at: datetime


class DealAuditResponse(BaseModel):
    """Per-deal Activity Feed payload."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    limit: int
    offset: int
    total: int
    entries: list[AuditEntry]


class ExplorerResponse(BaseModel):
    """Tenant-wide Compliance Explorer payload."""

    model_config = ConfigDict(extra="forbid")

    limit: int
    offset: int
    total: int
    entries: list[AuditEntry]


# ─────────────────────────── helpers ──────────────────────────


def _parse_json_blob(raw: Any) -> Any:
    """Best-effort parse of a JSONB / TEXT column.

    Postgres + asyncpg hand us a parsed dict already; SQLite hands us a
    string. Both shapes flow into the same Pydantic field, so we
    normalize here. Malformed JSON returns ``None`` so a single bad row
    never breaks the whole feed.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _row_to_entry(row: dict[str, Any]) -> AuditEntry:
    """Normalize an audit_log row into the API shape."""
    payload = _parse_json_blob(row.get("payload"))
    before = _parse_json_blob(row.get("before"))
    after = _parse_json_blob(row.get("after"))
    tags = _parse_json_blob(row.get("tags"))

    # tags must be a flat list of strings — defensive coercion guards
    # against legacy rows that wrote a dict by accident.
    if isinstance(tags, list):
        tags = [str(t) for t in tags if t is not None]
    elif tags is not None:
        tags = None

    created_at = row.get("created_at")
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at)
        except ValueError:
            created_at = datetime.utcnow()
    return AuditEntry(
        id=str(row.get("id")),
        tenant_id=str(row.get("tenant_id")),
        deal_id=str(row["deal_id"]) if row.get("deal_id") else None,
        actor_id=row.get("actor_id"),
        actor_email=row.get("actor_email"),
        actor_ip=row.get("actor_ip"),
        user_agent=row.get("user_agent"),
        action=row.get("action") or "",
        resource_type=row.get("resource_type") or "",
        resource_id=row.get("resource_id"),
        severity=row.get("severity") or "info",
        diff_summary=row.get("diff_summary"),
        before=before if isinstance(before, dict) else None,
        after=after if isinstance(after, dict) else None,
        tags=tags,
        payload=payload if isinstance(payload, dict) else None,
        input_hash=row.get("input_hash"),
        output_hash=row.get("output_hash"),
        created_at=created_at,  # type: ignore[arg-type]
    )


async def _count_deal_audit(
    session: AsyncSession, *, tenant_id: UUID, deal_id: UUID, **filters: Any
) -> int:
    """COUNT(*) matching the same filters as the listing query.

    Hand-rolled (rather than reusing search_audit_log) so the per-deal
    endpoint can take advantage of the ``idx_audit_log_deal`` index
    directly without falling back to a full tenant scan when callers
    omit ``q``.
    """
    clauses: list[str] = ["tenant_id = :tenant", "deal_id = :deal"]
    params: dict[str, Any] = {
        "tenant": str(tenant_id),
        "deal": str(deal_id),
    }
    if filters.get("action"):
        clauses.append("action = :action")
        params["action"] = filters["action"]
    if filters.get("resource_type"):
        clauses.append("resource_type = :rtype")
        params["rtype"] = filters["resource_type"]
    if filters.get("severity"):
        clauses.append("severity = :sev")
        params["sev"] = filters["severity"]
    if filters.get("since") is not None:
        clauses.append("created_at >= :since")
        params["since"] = filters["since"]
    if filters.get("until") is not None:
        clauses.append("created_at <= :until")
        params["until"] = filters["until"]
    sql = text(f"SELECT COUNT(*) FROM audit_log WHERE {' AND '.join(clauses)}")
    row = (await session.execute(sql, params)).first()
    return int(row[0]) if row is not None else 0


# ─────────────────────────── routes ───────────────────────────


@router.get(
    "/{deal_id}/audit",
    response_model=DealAuditResponse,
)
async def get_deal_audit(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    action: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> DealAuditResponse:
    """Activity Feed payload for a single deal.

    Tenant-scoped: cross-tenant deal ids return an empty feed rather
    than 404 — both shapes leak the same info, and the empty feed
    keeps the UI's state machine simpler.
    """
    rows = await list_audit_log(
        session,
        tenant_id=str(tenant_id),
        deal_id=str(deal_id),
        action=action,
        resource_type=entity_type,
        severity=severity,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    total = await _count_deal_audit(
        session,
        tenant_id=tenant_id,
        deal_id=deal_id,
        action=action,
        resource_type=entity_type,
        severity=severity,
        since=since,
        until=until,
    )
    entries = [_row_to_entry(r) for r in rows]
    return DealAuditResponse(
        deal_id=deal_id,
        limit=limit,
        offset=offset,
        total=total,
        entries=entries,
    )


# ─────────────────────── tenant-wide explorer ─────────────────────────
#
# Mounted at ``/audit/explorer`` (no deal scope). Permission-gated to
# admin-role for now (or any-role if no RBAC layer — flag for follow-up).
# Today the only role layer is the tenant header itself; the response
# carries an ``rbac_lock`` field the UI uses to render the "Admin only"
# state when the role check eventually lands.

explorer_router = APIRouter()


@explorer_router.get(
    "/explorer",
    response_model=ExplorerResponse,
)
async def get_audit_explorer(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    q: Annotated[str | None, Query(description="Free-text search across action / actor / diff_summary")] = None,
    actor: Annotated[str | None, Query()] = None,
    entity_type: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
) -> ExplorerResponse:
    """Tenant-wide audit search powering the Compliance Explorer.

    Permission model (Wave 4 W4.3): the worker doesn't yet ship an
    RBAC layer — every signed-in user can hit this endpoint inside
    their own tenant. The UI renders the "Admin only" lock state
    locally when ``role !== 'admin'`` so the design surface is in
    place when the role check lands.
    """
    rows, total = await search_audit_log(
        session,
        tenant_id=str(tenant_id),
        q=q,
        actor_id=actor,
        entity_type=entity_type,
        severity=severity,
        since=since,
        until=until,
        limit=limit,
        offset=offset,
    )
    entries = [_row_to_entry(r) for r in rows]
    return ExplorerResponse(
        limit=limit,
        offset=offset,
        total=total,
        entries=entries,
    )
