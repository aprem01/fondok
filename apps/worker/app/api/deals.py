"""Deal lifecycle endpoints — CRUD, status, HITL gates, memo streaming.

CRUD is now real and DB-backed: every mutation persists to the
``deals`` table and writes an append-only ``audit_log`` row. The
status endpoint rolls up document/extraction state so the UI can
render a single "where is this deal" pill without a second query.

The HITL gate + memo endpoints remain thin wrappers around the
LangGraph runtime and the streaming broadcast.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..config import get_settings
from ..costs import build_cost_report
from ..database import get_session
from ..memo_edits import list_edits, record_edit

try:
    from fondok_schemas import DealCostReport
except ImportError:  # pragma: no cover
    DealCostReport = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── tenant resolution ───────────────────────────


async def get_tenant_id(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> UUID:
    """Resolve the tenant for this request.

    Reads the ``X-Tenant-Id`` header set by the web app's `lib/api.ts`
    (mirrors the active Clerk Organization id). When the header is
    missing or unparseable we fall back to ``settings.DEFAULT_TENANT_ID``
    so the unauthenticated demo persona keeps working end-to-end.
    """
    settings = get_settings()
    if x_tenant_id:
        try:
            return UUID(x_tenant_id)
        except ValueError:
            logger.warning(
                "get_tenant_id: malformed X-Tenant-Id header %r — using default",
                x_tenant_id,
            )
    return UUID(settings.DEFAULT_TENANT_ID)


# ─────────────────────────── request bodies ───────────────────────────


class CreateDealBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    city: str | None = None
    keys: int | None = Field(default=None, ge=1)
    service: str | None = None
    deal_stage: str | None = None
    return_profile: str | None = None
    brand: str | None = None
    positioning: str | None = None
    purchase_price: float | None = Field(default=None, ge=0)


class UpdateDealBody(BaseModel):
    """Partial update — every field is optional."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    city: str | None = None
    keys: int | None = Field(default=None, ge=1)
    service: str | None = None
    status: str | None = None
    deal_stage: str | None = None
    risk: str | None = None
    ai_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    return_profile: str | None = None
    brand: str | None = None
    positioning: str | None = None
    purchase_price: float | None = Field(default=None, ge=0)


class Gate1Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: str = Field(pattern=r"^(approve|reject|edit)$")
    notes: str | None = None


class Gate2Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: str = Field(pattern=r"^(go|no-go|conditional)$")
    notes: str | None = None


# ─────────────────────────── response shapes ───────────────────────────


class DealRecord(BaseModel):
    """Full row-level view of a deal — what list/get/patch return."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    city: str | None = None
    keys: int | None = None
    service: str | None = None
    status: str = "Draft"
    deal_stage: str | None = None
    risk: str | None = None
    ai_confidence: float | None = None
    return_profile: str | None = None
    brand: str | None = None
    positioning: str | None = None
    purchase_price: float | None = None
    created_at: datetime
    updated_at: datetime


class DealStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    status: str
    deal_stage: str | None = None
    last_event: str | None = None
    docs_total: int = 0
    docs_extracted: int = 0
    docs_extracting: int = 0
    docs_failed: int = 0
    ai_confidence: float | None = None


class GateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    gate: str
    accepted: bool = True
    next_state: str | None = None


class MemoEnvelope(BaseModel):
    """Persisted-memo view returned by ``GET /deals/{id}/memo``.

    ``status`` discriminates the empty cases the UI used to confuse
    with "successful empty memo":

    * ``not_yet_generated`` — no memo has been kicked off for this
      deal. Sections + citations are empty arrays. The UI should show
      "Generate memo" CTA, not a blank memo.
    * ``in_progress`` — the streaming run is still drafting sections.
      Sections may be partially populated; the client should keep
      listening on ``/memo/stream``.
    * ``failed`` — the analyst raised an unrecoverable error. ``error``
      is populated; the UI should show the message and a retry CTA.
    * ``done`` — the analyst completed; sections + citations are
      canonical and the SSE stream has emitted ``event: done``.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    sections: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "not_yet_generated"
    error: str | None = None
    generated_at: str | None = None


# ─────────────────────────── helpers ───────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).replace(tzinfo=UTC)
        except ValueError:
            pass
    return _now()


def _coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _row_to_record(row: dict[str, Any]) -> DealRecord:
    return DealRecord(
        id=UUID(str(row["id"])),
        tenant_id=UUID(str(row["tenant_id"])),
        name=row["name"],
        city=row.get("city"),
        keys=row.get("keys"),
        service=row.get("service"),
        status=row.get("status") or "Draft",
        deal_stage=row.get("deal_stage"),
        risk=row.get("risk"),
        ai_confidence=_coerce_float(row.get("ai_confidence")),
        return_profile=row.get("return_profile"),
        brand=row.get("brand"),
        positioning=row.get("positioning"),
        purchase_price=_coerce_float(row.get("purchase_price")),
        created_at=_coerce_dt(row.get("created_at")),
        updated_at=_coerce_dt(row.get("updated_at")),
    )


_DEAL_COLUMNS = (
    "id, tenant_id, name, city, keys, service, status, deal_stage, "
    "risk, ai_confidence, return_profile, brand, positioning, "
    "purchase_price, created_at, updated_at"
)


async def _write_audit(
    session: AsyncSession,
    *,
    tenant_id: str,
    deal_id: str,
    actor_id: str,
    action: str,
    payload: dict[str, Any],
) -> None:
    """Thin compatibility wrapper around :func:`app.audit.log_audit`.

    Existing call sites in this module pass a single ``payload`` blob;
    the centralized helper splits input/output and computes SHA-256
    hashes for the IT-review trail. We forward the legacy ``payload``
    as ``output_payload`` so the hash captures the diff that the
    mutation actually applied.
    """
    await log_audit(
        session,
        tenant_id=tenant_id,
        actor_id=actor_id,
        action=action,
        resource_type="deal",
        resource_id=deal_id,
        output_payload=payload,
    )


# ─────────────────────────── routes ───────────────────────────


@router.get("", response_model=list[DealRecord])
async def list_deals(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> list[DealRecord]:
    """Return all deals for the current tenant, newest first."""
    rows = await session.execute(
        text(
            f"""
            SELECT {_DEAL_COLUMNS}
              FROM deals
             WHERE tenant_id = :tenant
             ORDER BY created_at DESC
            """
        ),
        {"tenant": str(tenant_id)},
    )
    return [_row_to_record(dict(r._mapping)) for r in rows.fetchall()]


@router.post("", response_model=DealRecord, status_code=status.HTTP_201_CREATED)
async def create_deal(
    body: CreateDealBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DealRecord:
    """Insert a new deal row + audit log entry."""
    tenant_id_str = str(tenant_id)
    deal_id = uuid4()
    now = _now()

    params = {
        "id": str(deal_id),
        "tenant": tenant_id_str,
        "name": body.name,
        "city": body.city,
        "keys": body.keys,
        "service": body.service,
        "status": "Draft",
        "deal_stage": body.deal_stage,
        "risk": None,
        "ai_confidence": 0.0,
        "return_profile": body.return_profile,
        "brand": body.brand,
        "positioning": body.positioning,
        "purchase_price": body.purchase_price,
        "created_at": now,
        "updated_at": now,
    }

    await session.execute(
        text(
            """
            INSERT INTO deals (
                id, tenant_id, name, city, keys, service, status,
                deal_stage, risk, ai_confidence, return_profile,
                brand, positioning, purchase_price,
                created_at, updated_at
            ) VALUES (
                :id, :tenant, :name, :city, :keys, :service, :status,
                :deal_stage, :risk, :ai_confidence, :return_profile,
                :brand, :positioning, :purchase_price,
                :created_at, :updated_at
            )
            """
        ),
        params,
    )

    await _write_audit(
        session,
        tenant_id=tenant_id_str,
        deal_id=str(deal_id),
        actor_id="system",
        action="deal.created",
        payload={
            "name": body.name,
            "city": body.city,
            "keys": body.keys,
            "service": body.service,
            "deal_stage": body.deal_stage,
        },
    )
    await session.commit()

    logger.info("deals.create: deal=%s tenant=%s name=%r", deal_id, tenant_id_str, body.name)
    return DealRecord(
        id=deal_id,
        tenant_id=tenant_id,
        name=body.name,
        city=body.city,
        keys=body.keys,
        service=body.service,
        status="Draft",
        deal_stage=body.deal_stage,
        risk=None,
        ai_confidence=0.0,
        return_profile=body.return_profile,
        brand=body.brand,
        positioning=body.positioning,
        purchase_price=body.purchase_price,
        created_at=now,
        updated_at=now,
    )


@router.get("/{deal_id}", response_model=DealRecord)
async def get_deal(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DealRecord:
    row = (
        await session.execute(
            text(
                f"""
                SELECT {_DEAL_COLUMNS}
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
    return _row_to_record(dict(row._mapping))


@router.patch("/{deal_id}", response_model=DealRecord)
async def update_deal(
    deal_id: UUID,
    body: UpdateDealBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DealRecord:
    """Partial update. Sends an audit entry with the diff."""
    tenant_id_str = str(tenant_id)

    existing = (
        await session.execute(
            text(
                f"SELECT {_DEAL_COLUMNS} FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": tenant_id_str},
        )
    ).first()
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        # Nothing to update — return the existing row.
        return _row_to_record(dict(existing._mapping))

    set_clauses: list[str] = []
    params: dict[str, Any] = {"id": str(deal_id), "tenant": tenant_id_str}
    for field, value in changes.items():
        set_clauses.append(f"{field} = :{field}")
        params[field] = value
    now = _now()
    set_clauses.append("updated_at = :updated_at")
    params["updated_at"] = now

    await session.execute(
        text(
            f"""
            UPDATE deals
               SET {", ".join(set_clauses)}
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        params,
    )

    await _write_audit(
        session,
        tenant_id=tenant_id_str,
        deal_id=str(deal_id),
        actor_id="system",
        action="deal.updated",
        payload={"changes": changes},
    )
    await session.commit()

    refreshed = (
        await session.execute(
            text(
                f"SELECT {_DEAL_COLUMNS} FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": tenant_id_str},
        )
    ).first()
    assert refreshed is not None  # we just updated it
    logger.info("deals.update: deal=%s changes=%s", deal_id, list(changes.keys()))
    return _row_to_record(dict(refreshed._mapping))


@router.delete("/{deal_id}", response_model=DealRecord)
async def archive_deal(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DealRecord:
    """Soft-delete: flip status to ``Archived``. The row is kept."""
    tenant_id_str = str(tenant_id)

    existing = (
        await session.execute(
            text(
                f"SELECT {_DEAL_COLUMNS} FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": tenant_id_str},
        )
    ).first()
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )

    now = _now()
    await session.execute(
        text(
            """
            UPDATE deals
               SET status = 'Archived', updated_at = :ts
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        {"id": str(deal_id), "tenant": tenant_id_str, "ts": now},
    )

    await _write_audit(
        session,
        tenant_id=tenant_id_str,
        deal_id=str(deal_id),
        actor_id="system",
        action="deal.archived",
        payload={"previous_status": existing._mapping.get("status")},
    )
    await session.commit()

    refreshed = (
        await session.execute(
            text(
                f"SELECT {_DEAL_COLUMNS} FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": tenant_id_str},
        )
    ).first()
    assert refreshed is not None
    logger.info("deals.archive: deal=%s tenant=%s", deal_id, tenant_id_str)
    return _row_to_record(dict(refreshed._mapping))


@router.get("/{deal_id}/status", response_model=DealStatusResponse)
async def get_deal_status(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DealStatusResponse:
    """Aggregate the deal + document state into a single status pill.

    The web UI calls this after every upload/extract to learn whether
    the deal is still ``draft`` (no docs), ``extracting`` (any
    document mid-flight), ``ready`` (every doc extracted), or has
    failures.
    """
    tenant_id_str = str(tenant_id)

    deal_row = (
        await session.execute(
            text(
                """
                SELECT id, status, deal_stage, ai_confidence
                  FROM deals
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(deal_id), "tenant": tenant_id_str},
        )
    ).first()
    if deal_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )

    doc_rows = await session.execute(
        text(
            """
            SELECT status, COUNT(*) AS n
              FROM documents
             WHERE deal_id = :id
             GROUP BY status
            """
        ),
        {"id": str(deal_id)},
    )
    counts: dict[str, int] = {}
    for r in doc_rows.fetchall():
        counts[r._mapping["status"]] = int(r._mapping["n"])

    docs_total = sum(counts.values())
    docs_extracted = counts.get("EXTRACTED", 0)
    docs_extracting = counts.get("EXTRACTING", 0) + counts.get("CLASSIFYING", 0)
    docs_failed = counts.get("FAILED", 0)

    if docs_total == 0:
        agg = "draft"
    elif docs_extracting > 0 or counts.get("UPLOADED", 0) > 0:
        agg = "extracting"
    elif docs_extracted == docs_total:
        agg = "ready"
    elif docs_failed > 0:
        agg = "failed"
    else:
        agg = "draft"

    # Roll up extraction confidence across all extracted docs.
    confidence: float | None = _coerce_float(deal_row._mapping.get("ai_confidence"))
    if docs_extracted:
        cr_rows = await session.execute(
            text(
                """
                SELECT confidence_report
                  FROM extraction_results
                 WHERE deal_id = :id
                """
            ),
            {"id": str(deal_id)},
        )
        scores: list[float] = []
        for r in cr_rows.fetchall():
            blob = r._mapping["confidence_report"]
            if isinstance(blob, str):
                try:
                    blob = json.loads(blob) if blob else None
                except json.JSONDecodeError:
                    blob = None
            if isinstance(blob, dict):
                overall = blob.get("overall")
                if isinstance(overall, (int, float)):
                    scores.append(float(overall))
        if scores:
            confidence = sum(scores) / len(scores)

    deal_status = deal_row._mapping["status"] or "Draft"
    return DealStatusResponse(
        id=deal_id,
        status=deal_status,
        deal_stage=deal_row._mapping.get("deal_stage"),
        last_event=agg,
        docs_total=docs_total,
        docs_extracted=docs_extracted,
        docs_extracting=docs_extracting,
        docs_failed=docs_failed,
        ai_confidence=confidence,
    )


@router.post("/{deal_id}/gate1", response_model=GateResponse)
async def gate1(
    deal_id: UUID,
    body: Gate1Body,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> GateResponse:
    """HITL Gate 1 — accept / reject / edit the normalized spread.

    Persists the decision to ``audit_log`` so the IT-review trail
    captures who approved (or rejected) the extraction before the
    engines run. The graph state-machine is wired separately; this
    route is the canonical record of the decision.
    """
    accepted = body.decision == "approve"
    next_state = "run_engines" if accepted else "halt"
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        actor_id="system",
        action="gate1.decision",
        resource_type="deal",
        resource_id=str(deal_id),
        output_payload={
            "decision": body.decision,
            "notes": body.notes,
            "accepted": accepted,
            "next_state": next_state,
        },
    )
    await session.commit()
    logger.info("gate1: deal=%s decision=%s accepted=%s", deal_id, body.decision, accepted)
    return GateResponse(
        id=deal_id,
        gate="gate1",
        accepted=accepted,
        next_state=next_state,
    )


@router.post("/{deal_id}/gate2", response_model=GateResponse)
async def gate2(
    deal_id: UUID,
    body: Gate2Body,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> GateResponse:
    """HITL Gate 2 — final recommendation on the IC memo.

    Persists the recommendation (go / no-go / conditional) to
    ``audit_log``. The downstream finalize step (memo lock + export)
    reads the latest ``gate2.decision`` row to know whether to proceed.
    """
    accepted = body.recommendation in ("go", "conditional")
    next_state = "finalize" if accepted else "decline"
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        actor_id="system",
        action="gate2.decision",
        resource_type="deal",
        resource_id=str(deal_id),
        output_payload={
            "recommendation": body.recommendation,
            "notes": body.notes,
            "accepted": accepted,
            "next_state": next_state,
        },
    )
    await session.commit()
    logger.info(
        "gate2: deal=%s recommendation=%s accepted=%s",
        deal_id,
        body.recommendation,
        accepted,
    )
    return GateResponse(
        id=deal_id, gate="gate2", accepted=accepted, next_state=next_state
    )


@router.get("/{deal_id}/memo", response_model=MemoEnvelope)
async def get_memo(deal_id: UUID) -> MemoEnvelope:
    """Final IC memo (JSON envelope).

    Backed by the in-process ``MemoCache`` that the Analyst's streaming
    run writes to as each section lands. The cache survives until the
    pod restarts; for the single-replica Railway deployment that's
    sufficient. Multi-replica fan-out swaps the cache to Redis the
    same way ``MemoBroadcast`` does — see ``streaming/broadcast.py``.

    Architectural decision (2026-04-27): when no run has started yet
    we return ``200`` with ``status="not_yet_generated"`` instead of
    ``404``. The web UI was treating an empty ``200 {sections: []}``
    response as "successful empty memo" and rendering blank state; an
    explicit ``status`` discriminator keeps every consumer pointed at
    the right CTA without the cost of a separate HTTP error class.
    Failures, in-progress runs, and successful runs all flow through
    the same shape, just with different ``status`` + ``sections``.
    """
    from ..streaming.broadcast import get_memo_cache

    cache = get_memo_cache()
    snapshot = await cache.get(str(deal_id))
    if snapshot is None:
        return MemoEnvelope(
            deal_id=deal_id,
            sections=[],
            citations=[],
            status="not_yet_generated",
            error=None,
            generated_at=None,
        )
    return MemoEnvelope(
        deal_id=deal_id,
        sections=snapshot["sections"],
        citations=snapshot["citations"],
        status=snapshot["status"],
        error=snapshot["error"],
        generated_at=snapshot["generated_at"],
    )


class CriticReportResponse(BaseModel):
    """Latest persisted Critic report for a deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    summary: str | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    critical_count: int = 0
    warn_count: int = 0
    info_count: int = 0
    created_at: datetime


@router.get("/{deal_id}/critic", response_model=CriticReportResponse)
async def get_critic_report(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> CriticReportResponse:
    """Return the latest CriticReport for ``deal_id``.

    The Critic agent runs after the Variance pass and identifies
    cross-field stories that the per-field variance pass would miss
    (coastal insurance held flat, NOI growth without OpEx pressure,
    etc.). Each finding is grounded in a USALI catalog rule_id or one
    of the ``MULTI_FIELD_*`` cross-field rules.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, summary, report_json, created_at
                  FROM critic_reports
                 WHERE deal_id = :deal AND tenant_id = :tenant
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ),
            {"deal": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no critic report found for deal {deal_id} — "
                "extract a broker proforma + T-12 first"
            ),
        )
    mapping = row._mapping
    raw_report = mapping["report_json"]
    if isinstance(raw_report, str):
        try:
            report = json.loads(raw_report)
        except json.JSONDecodeError:
            report = {}
    else:
        report = dict(raw_report) if raw_report else {}
    findings = report.get("findings") or []
    return CriticReportResponse(
        deal_id=deal_id,
        summary=mapping.get("summary") or report.get("summary"),
        findings=findings,
        critical_count=int(report.get("critical_count") or 0),
        warn_count=int(report.get("warn_count") or 0),
        info_count=int(report.get("info_count") or 0),
        created_at=_coerce_dt(mapping["created_at"]),
    )


@router.get("/{deal_id}/costs", response_model=DealCostReport)
async def get_deal_costs(deal_id: UUID) -> Any:
    """Aggregated LLM cost dashboard for ``deal_id``.

    Reads ``ModelCall`` rows from the ``model_calls`` table (when
    populated) and rolls them up by agent and model bucket. Returns a
    well-formed zeroed report when there's no activity yet so the UI
    can render the empty state without a separate code path.
    """
    return await build_cost_report(str(deal_id))


class VerificationReportResponse(BaseModel):
    """Latest persisted verification report for a deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    pass_rate: float
    created_at: datetime
    report: dict[str, Any]


@router.get(
    "/{deal_id}/verification", response_model=VerificationReportResponse
)
async def get_verification_report(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> VerificationReportResponse:
    """Return the latest deterministic verification report for a deal.

    Each report is the output of ``verify_citations`` over the deal's
    extracted fields against the parser cache — one ``VerificationCheck``
    per cited number, classified ``match`` / ``close`` / ``mismatch`` /
    ``unverifiable``. The reports table is append-only; we always return
    the most recent row.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, pass_rate, report_json, created_at
                  FROM verification_reports
                 WHERE deal_id = :deal AND tenant_id = :tenant
                 ORDER BY created_at DESC
                 LIMIT 1
                """
            ),
            {"deal": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"no verification report found for deal {deal_id} — "
                "extract at least one document first"
            ),
        )
    mapping = row._mapping
    raw_report = mapping["report_json"]
    if isinstance(raw_report, str):
        try:
            report = json.loads(raw_report)
        except json.JSONDecodeError:
            report = {}
    else:
        report = dict(raw_report) if raw_report else {}
    return VerificationReportResponse(
        deal_id=deal_id,
        pass_rate=float(mapping["pass_rate"] or 0.0),
        created_at=_coerce_dt(mapping["created_at"]),
        report=report,
    )


class MemoInputMissing(Exception):
    """Raised by ``_load_deal_payload`` when the deal has no extracted
    financials yet. The route layer translates this into a 400 with a
    user-facing message.

    ``code`` lets the web UI dispatch on which document is missing
    (proforma vs T-12 vs both) without parsing the prose ``message``.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "memo_inputs_missing",
        missing: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.missing = missing or []


async def _count_extracted_documents(
    session: AsyncSession, *, deal_id: str
) -> tuple[int, int]:
    """Return ``(docs_total, docs_extracted)`` for ``deal_id``.

    We treat any document whose status is ``EXTRACTED`` (the terminal
    success state set by the extractor agent) as a real input the
    Analyst can cite. Documents in ``UPLOADED`` / ``CLASSIFYING`` /
    ``EXTRACTING`` / ``FAILED`` don't count — they're either still
    in-flight or failed before producing a spread.
    """
    rows = await session.execute(
        text(
            """
            SELECT status, COUNT(*) AS n
              FROM documents
             WHERE deal_id = :id
             GROUP BY status
            """
        ),
        {"id": deal_id},
    )
    counts: dict[str, int] = {}
    for r in rows.fetchall():
        counts[r._mapping["status"]] = int(r._mapping["n"])
    return sum(counts.values()), counts.get("EXTRACTED", 0)


async def _load_deal_payload(
    deal_id: str, *, session: AsyncSession | None = None
) -> Any:
    """Build an ``AnalystInput`` from the persisted deal + engine state.

    Validation contract:

    * If a DB ``session`` is supplied AND the deal exists in the DB,
      we require at least one ``EXTRACTED`` document — otherwise we
      raise :class:`MemoInputMissing` so the route can return 400 with
      a clear "upload an OM + T-12 first" message instead of 500.
    * If no session is supplied (legacy / fixture path) we fall through
      to the Kimpton Angler fixture so the streaming endpoint demos
      end-to-end. This branch exists to keep ``test_streaming.py`` and
      the seed-data demo flow green; production callers always pass a
      real session via the route layer.

    Source-document handling:

    * When real ``EXTRACTED`` documents exist on the deal, we hydrate
      ``source_documents`` with their actual UUIDs, filenames, doc
      types, and per-page text from ``documents.extraction_data``.
      That gives the Analyst real material to cite and lets the UI's
      citation chips deep-link back to the cited PDF page.
    * Fixture documents (Kimpton appendix) are only used as a fallback
      when no real docs are present (the legacy demo path).

    The deal_data / engine_results / variance fields are still served
    from the Kimpton fixture pending a deal-fetch helper that can
    rebuild a ``USALIFinancials`` spread from extraction JSON.
    """
    from ..agents.analyst import AnalystInput, AnalystSourceDocument
    from ..export.fixtures import kimpton_deal, kimpton_memo, kimpton_model

    settings = get_settings()

    real_source_docs: list[AnalystSourceDocument] = []
    real_payload_fields: dict[str, Any] | None = None

    if session is not None:
        # Confirm the deal exists in this tenant's DB before we even
        # think about loading inputs. Routes already 404 on missing
        # deals, but we double-check here so the agent layer never
        # silently materializes a fixture for a deleted deal.
        deal_row = (
            await session.execute(
                text("SELECT id FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()
        if deal_row is not None:
            docs_total, docs_extracted = await _count_extracted_documents(
                session, deal_id=deal_id
            )
            if docs_total == 0:
                raise MemoInputMissing(
                    "Memo generation requires extracted broker proforma and "
                    "T-12. Upload an OM and a T-12 document first.",
                    code="memo_inputs_missing",
                    missing=["proforma", "t12"],
                )
            if docs_extracted == 0:
                raise MemoInputMissing(
                    "Memo generation requires at least one fully extracted "
                    "document. Wait for extraction to finish (or fix the "
                    "failed parse) before generating the memo.",
                    code="memo_inputs_extraction_pending",
                    missing=["extraction"],
                )
            # Hydrate real source documents from the persisted parser cache.
            real_source_docs = await _load_source_documents(
                session, deal_id=deal_id
            )
            # Hydrate the rest of the Analyst payload (deal metadata,
            # spread, engine outputs, deterministic variance report)
            # from real DB state. We bias to real data wherever possible
            # so the Analyst never grounds its numbers in the fixture
            # while citing pages of a real T-12.
            real_payload_fields = await _build_real_analyst_fields(
                session, deal_id=deal_id
            )

    deal = kimpton_deal()
    deal["id"] = deal_id
    model = kimpton_model()
    memo = kimpton_memo()

    if real_source_docs:
        docs = real_source_docs
    else:
        # Fixture fallback — used only by the seed-data demo / streaming
        # smoke tests where no real documents have been uploaded.
        docs = []
        for idx, fname in enumerate(
            memo.get("appendix", {}).get("documents_reviewed", []), start=1
        ):
            docs.append(
                AnalystSourceDocument(
                    document_id=f"doc-{idx:02d}",
                    filename=fname,
                    doc_type="reference",
                    page_count=1,
                    excerpts_by_page={1: f"Reference excerpt for {fname}."},
                )
            )

    if real_payload_fields is not None:
        # Real-data path — every numeric input traces back to the deal's
        # extractions / engine runs. If any layer is sparse the Analyst
        # sees None / empty for that layer rather than a fixture lie.
        return AnalystInput(
            tenant_id=settings.DEFAULT_TENANT_ID,
            deal_id=deal_id,
            deal_data=real_payload_fields["deal_data"],
            normalized_spread=real_payload_fields["normalized_spread"],
            engine_results=real_payload_fields["engine_results"],
            variance_report=real_payload_fields["variance_report"],
            source_documents=docs,
        )

    return AnalystInput(
        tenant_id=settings.DEFAULT_TENANT_ID,
        deal_id=deal_id,
        deal_data=deal,
        normalized_spread=None,
        engine_results=model,
        variance_report=None,
        source_documents=docs,
    )


async def _build_real_analyst_fields(
    session: AsyncSession, *, deal_id: str
) -> dict[str, Any]:
    """Hydrate the non-source-document fields of an ``AnalystInput`` from
    real DB state.

    Returns a dict with keys ``deal_data``, ``normalized_spread``,
    ``engine_results``, ``variance_report``. Each individual field is
    best-effort: extraction may be partial, engines may not have run,
    variance may be empty if only one of (broker, actuals) exists. We
    fall back to a None / empty value for that layer in those cases —
    never to a fixture. The Analyst's prompt tolerates missing layers
    (it'll just have less material to cite in the financial section).
    """
    # ── deal_data ─────────────────────────────────────────────────────
    deal_data: dict[str, Any] = {"id": deal_id}
    deal_row = (
        await session.execute(
            text(
                """
                SELECT name, city, keys, brand, service, positioning,
                       deal_stage, return_profile, purchase_price, status
                  FROM deals
                 WHERE id = :id
                """
            ),
            {"id": deal_id},
        )
    ).first()
    if deal_row is not None:
        m = deal_row._mapping
        for col in (
            "name",
            "city",
            "keys",
            "brand",
            "service",
            "positioning",
            "deal_stage",
            "return_profile",
            "purchase_price",
            "status",
        ):
            value = m.get(col)
            if value is None:
                continue
            # Coerce numerics so the prompt prints clean.
            if col == "keys":
                try:
                    deal_data[col] = int(value)
                except (TypeError, ValueError):
                    deal_data[col] = value
            elif col == "purchase_price":
                try:
                    deal_data[col] = float(value)
                except (TypeError, ValueError):
                    deal_data[col] = value
            else:
                deal_data[col] = value
        if "city" in deal_data:
            # ``location`` is what the fixture surfaces and downstream
            # formatters look for — keep both shapes alive.
            deal_data["location"] = deal_data["city"]

    # ── broker + actuals (USALIFinancials) ─────────────────────────────
    # ``_load_critic_inputs`` already does the heavy lifting of bucketing
    # extracted fields by doc_type. Lazy import — documents.py imports
    # from this module, so a top-level import would cycle.
    from .documents import _load_critic_inputs

    broker, actuals, _market_context, _keys = await _load_critic_inputs(
        session, deal_id=deal_id
    )

    # Prefer T-12 actuals as the locked spread; fall back to broker so
    # the Analyst still sees something to anchor the financial section
    # when only an OM has been extracted.
    spread = actuals if actuals is not None else broker

    # ── engine_results ────────────────────────────────────────────────
    # Pull every engine's latest persisted ``outputs`` blob; the prompt
    # iterates a flat dict, so we surface only the ``outputs`` payload
    # (not the run wrapper) keyed by engine name.
    from ..services.engine_runner import get_latest_outputs

    raw_engines = await get_latest_outputs(session, deal_id=deal_id)
    engine_results: dict[str, Any] = {}
    for name, envelope in raw_engines.items():
        if not isinstance(envelope, dict):
            continue
        outputs = envelope.get("outputs")
        if isinstance(outputs, dict) and outputs:
            engine_results[name] = outputs

    # ── variance_report ───────────────────────────────────────────────
    # Deterministic flags only — no LLM narration here. The Analyst
    # only needs the rule_id + delta to surface variances; per-flag
    # narrative notes are a nice-to-have we skip to keep memo gen fast.
    variance_report = None
    if actuals is not None and broker is not None:
        try:
            from uuid import UUID as _UUID, uuid5 as _uuid5
            from fondok_schemas.variance import VarianceReport
            from ..agents.variance import (
                VarianceBrokerField,
                _build_flags,
                _to_uuid as _variance_to_uuid,
            )

            # Mirror ``USALIFinancials`` onto the broker-side payload
            # the variance builder consumes — one VarianceBrokerField
            # per known field.
            broker_fields: list[VarianceBrokerField] = []
            for field_name, value in (
                ("noi", broker.noi),
                ("rooms_revenue", broker.rooms_revenue),
                ("fb_revenue", broker.fb_revenue),
                ("total_revenue", broker.total_revenue),
                ("departmental_expenses", broker.dept_expenses.total),
                ("undistributed_expenses", broker.undistributed.total),
                ("gop", broker.gop),
                ("mgmt_fee", broker.mgmt_fee),
                ("ffe_reserve", broker.ffe_reserve),
                ("fixed_charges", broker.fixed_charges.total),
                ("insurance", broker.fixed_charges.insurance),
                ("occupancy", broker.occupancy),
                ("adr", broker.adr),
                ("revpar", broker.revpar),
            ):
                if value is None:
                    continue
                try:
                    broker_fields.append(
                        VarianceBrokerField(field=field_name, value=float(value))
                    )
                except Exception:  # noqa: BLE001
                    continue

            deal_uuid = _variance_to_uuid(deal_id)
            flags = _build_flags(
                deal_uuid=deal_uuid,
                actuals=actuals,
                broker_fields=broker_fields,
            )
            variance_report = VarianceReport(deal_id=deal_uuid, flags=flags)
        except Exception as exc:  # noqa: BLE001 - variance is best-effort
            logger.warning(
                "memo: variance build failed for deal=%s: %s — proceeding without flags",
                deal_id,
                exc,
            )
            variance_report = None

    return {
        "deal_data": deal_data,
        "normalized_spread": spread,
        "engine_results": engine_results,
        "variance_report": variance_report,
    }


# Per-page excerpt cap. Opus 4.7 has 1M context, but every additional
# character is paid input tokens; 3000 chars is enough headroom for the
# Analyst to locate supporting evidence on most pages without bloating
# the prompt past the prompt-cache 4-block budget.
_SOURCE_DOC_PAGE_CHAR_CAP = 3000


async def _load_source_documents(
    session: AsyncSession, *, deal_id: str
) -> list[Any]:
    """Build ``AnalystSourceDocument`` list from real ``EXTRACTED`` rows.

    Reads the parser cache on ``documents.extraction_data['pages']`` so
    the Analyst sees actual per-page text and can emit citations whose
    ``document_id`` matches the real DB UUID — letting the UI deep-link
    back to the source PDF page.
    """
    from ..agents.analyst import AnalystSourceDocument

    rows = await session.execute(
        text(
            """
            SELECT id, filename, doc_type, page_count, extraction_data
              FROM documents
             WHERE deal_id = :deal AND status = 'EXTRACTED'
             ORDER BY uploaded_at ASC
            """
        ),
        {"deal": deal_id},
    )

    out: list[AnalystSourceDocument] = []
    for r in rows.fetchall():
        m = r._mapping
        raw = m["extraction_data"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                raw = None
        pages_blob = (raw or {}).get("pages") or []

        excerpts: dict[int, str] = {}
        for p in pages_blob:
            try:
                page_num = int(p.get("page_num", 0))
            except (TypeError, ValueError):
                continue
            if page_num < 1:
                continue
            text_value = (p.get("text") or "").strip()
            if not text_value:
                continue
            if len(text_value) > _SOURCE_DOC_PAGE_CHAR_CAP:
                text_value = text_value[:_SOURCE_DOC_PAGE_CHAR_CAP] + "…[truncated]"
            excerpts[page_num] = text_value

        if not excerpts:
            # Skip documents with no usable page text — citing them
            # produces broken deep-links.
            continue

        page_count_value = m.get("page_count")
        try:
            page_count = max(1, int(page_count_value)) if page_count_value else max(excerpts)
        except (TypeError, ValueError):
            page_count = max(excerpts)

        out.append(
            AnalystSourceDocument(
                document_id=str(m["id"]),
                filename=str(m["filename"]),
                doc_type=(m.get("doc_type") or None),
                page_count=page_count,
                excerpts_by_page=excerpts,
            )
        )
    return out


# SSE timing — heartbeat keeps intermediate proxies (Railway's edge,
# nginx, browser fetch) from killing an idle connection; the absolute
# timeout is the upper bound on how long a single Analyst run can
# stream before we forcibly close with an error event.
_SSE_HEARTBEAT_SECONDS = 15.0
_SSE_TOTAL_TIMEOUT_SECONDS = 300.0


async def _safe_run_analyst_streaming(payload: Any) -> None:
    """Wrap ``run_analyst_streaming`` so a raised exception always
    surfaces via the broadcast (and the memo cache) instead of
    silently dying inside FastAPI's BackgroundTasks runner.

    Without this wrapper a Claude API failure / quota error / network
    blip would crash the background task, leave the SSE subscriber
    blocked on ``await q.get()`` until its 90s proxy timeout fired,
    and produce zero diagnostic signal in Railway logs. We catch the
    error, log a full traceback, and publish ``ERROR_SENTINEL`` so the
    SSE handler emits ``event: error`` and closes cleanly.
    """
    from ..agents.analyst import run_analyst_streaming
    from ..streaming.broadcast import (
        ERROR_SENTINEL,
        get_broadcast,
        get_memo_cache,
    )

    deal_id = getattr(payload, "deal_id", None) or "unknown"
    try:
        await run_analyst_streaming(payload)
    except Exception as exc:  # noqa: BLE001 - error path
        logger.exception(
            "memo/generate: background analyst run failed for deal=%s", deal_id
        )
        message = f"memo generation crashed: {type(exc).__name__}: {exc}"
        try:
            broadcast = get_broadcast()
            await broadcast.publish(
                f"memo:{deal_id}",
                {
                    "event": ERROR_SENTINEL,
                    "data": {"message": message, "code": "analyst_crashed"},
                    "metadata": {"deal_id": deal_id},
                },
            )
        except Exception as inner:  # pragma: no cover - defensive
            logger.warning(
                "memo/generate: error broadcast publish failed (%s)", inner
            )
        try:
            cache = get_memo_cache()
            await cache.mark_failed(
                str(deal_id),
                message=message,
                generated_at=datetime.now(UTC).isoformat(),
            )
        except Exception as inner:  # pragma: no cover - defensive
            logger.warning(
                "memo/generate: cache mark_failed swallowed (%s)", inner
            )


@router.post("/{deal_id}/memo/generate")
async def trigger_memo_generation(
    deal_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    """Kick off the streaming Opus memo draft. Returns immediately.

    The Analyst publishes one section at a time to the in-process
    ``MemoBroadcast`` keyed by ``memo:{deal_id}``; clients should
    immediately open ``GET /deals/{deal_id}/memo/stream`` to receive
    the sections via SSE.

    Failure modes:

    * ``400 Bad Request`` — the deal exists in the DB but has no
      extracted documents yet. Body is ``{"detail": "...",
      "code": "memo_inputs_missing", "missing": [...]}`` so the UI
      can route the user to the upload flow.
    * ``500 Internal Server Error`` — only when the input loader
      itself blew up unexpectedly (DB outage, etc.). The actual
      exception is logged for Railway log-grep.

    The background task is wrapped in :func:`_safe_run_analyst_streaming`
    so any error inside the analyst is surfaced via the SSE channel
    (``event: error``) instead of leaving the stream hanging.
    """
    try:
        payload = await _load_deal_payload(deal_id, session=session)
    except MemoInputMissing as exc:
        logger.info(
            "memo/generate: input precondition failed for deal=%s code=%s",
            deal_id,
            exc.code,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "detail": exc.message,
                "code": exc.code,
                "missing": exc.missing,
            },
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface the real cause
        logger.exception(
            "memo/generate: failed to build analyst payload for deal=%s", deal_id
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "detail": f"memo generation failed to start: {exc}",
                "code": "memo_payload_failed",
            },
        ) from exc

    # Reset any prior failed/done state so the new run starts clean —
    # otherwise GET /memo would briefly show the previous run's status
    # until the first new section lands.
    from ..streaming.broadcast import get_memo_cache

    cache = get_memo_cache()
    snapshot = await cache.get(deal_id)
    if snapshot is not None and snapshot["status"] in ("failed", "done"):
        await cache.clear(deal_id)

    background_tasks.add_task(_safe_run_analyst_streaming, payload)
    logger.info("memo/generate: scheduled streaming draft for deal=%s", deal_id)
    return {"status": "started", "deal_id": deal_id}


@router.get("/{deal_id}/memo/stream")
async def stream_memo(deal_id: str) -> StreamingResponse:
    """SSE stream of memo sections as the Analyst writes them.

    Lifecycle of an SSE response:

    1. ``event: start`` — emitted synchronously on subscribe so the
       client can confirm the connection is live (avoids the
       previously-observed 90s zero-byte hang).
    2. Zero or more ``event: section`` payloads — one per drafted
       memo section, with the ``MemoSection`` JSON in ``data``.
    3. ``event: ping`` every 15s of subscriber idleness — keeps the
       connection warm through Railway's edge / browser fetch
       buffering and signals "the analyst is still thinking".
    4. ``event: error`` if the analyst raises. ``data`` carries
       ``{"message": "...", "code": "..."}`` and the stream closes
       immediately after.
    5. ``event: done`` — always last, even on failure. The ``data``
       payload includes ``{"sections": <count>}`` on success and
       ``{"reason": "error"}`` on failure so the client can dispatch
       on which terminal state was reached.

    A 5-minute absolute timeout guards against pathological hangs.
    """
    from ..streaming.broadcast import (
        DONE_SENTINEL,
        ERROR_SENTINEL,
        get_broadcast,
        subscribe_with_heartbeat,
    )

    broadcast = get_broadcast()
    channel = f"memo:{deal_id}"

    async def event_stream() -> AsyncIterator[bytes]:
        # 1. Always start with a sentinel so the client gets bytes
        #    immediately and any intermediate proxy flushes its first
        #    chunk. Without this the connection looks dead for the full
        #    duration of the first section's LLM call.
        start_payload = json.dumps(
            {
                "data": {"deal_id": deal_id},
                "metadata": {"channel": channel},
            }
        )
        yield f"event: start\ndata: {start_payload}\n\n".encode()

        terminal_reason = "done"
        try:
            async for event in subscribe_with_heartbeat(
                broadcast,
                channel,
                heartbeat_seconds=_SSE_HEARTBEAT_SECONDS,
                total_timeout_seconds=_SSE_TOTAL_TIMEOUT_SECONDS,
            ):
                event_name = event.get("event", "section")

                if event_name == "ping":
                    ping_payload = json.dumps(
                        {
                            "data": event.get("data", {}),
                            "metadata": {"channel": channel},
                        }
                    )
                    yield f"event: ping\ndata: {ping_payload}\n\n".encode()
                    continue

                if event_name == ERROR_SENTINEL:
                    err_payload = json.dumps(
                        {
                            "data": event.get("data", {}),
                            "metadata": event.get("metadata", {}),
                        }
                    )
                    yield f"event: error\ndata: {err_payload}\n\n".encode()
                    terminal_reason = "error"
                    break

                if event_name == DONE_SENTINEL:
                    done_payload = json.dumps(
                        {
                            "data": event.get("data", {}),
                            "metadata": event.get("metadata", {}),
                        }
                    )
                    yield f"event: done\ndata: {done_payload}\n\n".encode()
                    return

                # Default: a real section. Pass through the data +
                # metadata exactly as published.
                section_payload = json.dumps(
                    {
                        "data": event.get("data", {}),
                        "metadata": event.get("metadata", {}),
                    }
                )
                yield f"event: section\ndata: {section_payload}\n\n".encode()

        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning("memo/stream: subscriber loop failed (%s)", exc)
            err = json.dumps(
                {
                    "data": {
                        "message": f"stream loop failed: {type(exc).__name__}: {exc}",
                        "code": "stream_loop_failed",
                    },
                    "metadata": {"channel": channel},
                }
            )
            yield f"event: error\ndata: {err}\n\n".encode()
            terminal_reason = "error"

        # 5. Always close with ``event: done`` so the client knows the
        #    stream ended deliberately (vs. socket reset).
        final = json.dumps(
            {
                "data": {"reason": terminal_reason},
                "metadata": {"channel": channel},
            }
        )
        yield f"event: done\ndata: {final}\n\n".encode()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


# ─────────────────────── memo edit history ───────────────────────


class MemoEditBody(BaseModel):
    """Request body for ``POST /deals/{deal_id}/memo/{section_id}/edits``.

    The client submits ``original_body`` (the section text it was
    looking at) so the server can record the full pre/post diff. We
    don't attempt optimistic-concurrency conflict detection — concurrent
    editors are a future problem; today the audit trail is the source
    of truth.
    """

    model_config = ConfigDict(extra="forbid")

    new_body: str = Field(min_length=1)
    original_body: str = Field(default="")
    comment: str | None = None


class MemoEditRecord(BaseModel):
    """One memo-edit row as returned by the history endpoint."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tenant_id: str
    deal_id: str
    section_id: str
    actor_id: str
    original_body: str
    new_body: str
    comment: str | None = None
    created_at: str


@router.post(
    "/{deal_id}/memo/{section_id}/edits",
    response_model=MemoEditRecord,
    status_code=status.HTTP_201_CREATED,
)
async def post_memo_edit(
    deal_id: UUID,
    section_id: str,
    body: MemoEditBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> MemoEditRecord:
    """Record an append-only memo-section edit.

    Both the edit row and the matching ``audit_log`` entry land in the
    same transaction — if either insert fails the other rolls back, so
    a half-recorded change never reaches the IT-review trail.
    """
    tenant_id_str = str(tenant_id)
    actor_id = "system"  # TODO: thread through Clerk user once auth lands

    edit_id = await record_edit(
        session,
        tenant_id=tenant_id_str,
        deal_id=str(deal_id),
        section_id=section_id,
        actor_id=actor_id,
        original_body=body.original_body,
        new_body=body.new_body,
        comment=body.comment,
    )

    await log_audit(
        session,
        tenant_id=tenant_id_str,
        actor_id=actor_id,
        action="memo.edited",
        resource_type="memo",
        resource_id=str(deal_id),
        input_payload={
            "section_id": section_id,
            "original_body": body.original_body,
        },
        output_payload={
            "section_id": section_id,
            "new_body": body.new_body,
            "comment": body.comment,
        },
        metadata={"edit_id": str(edit_id)},
    )
    await session.commit()

    # Read the row back so the response contains the canonical
    # created_at the DB stamped (avoids drift between client + server
    # clocks the audit trail would later flag).
    history = await list_edits(
        session, deal_id=str(deal_id), section_id=section_id
    )
    matching = next((h for h in history if h["id"] == str(edit_id)), None)
    if matching is None:  # pragma: no cover - defensive; we just inserted it
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="memo edit not visible after commit",
        )
    logger.info(
        "memo.edited: deal=%s section=%s edit=%s", deal_id, section_id, edit_id
    )
    return MemoEditRecord(**matching)


@router.get(
    "/{deal_id}/memo/edits",
    response_model=list[MemoEditRecord],
)
async def get_memo_edits(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    section_id: str | None = None,
) -> list[MemoEditRecord]:
    """Return the chronological edit history for a deal's memo.

    Pass ``section_id`` to scope to a single section; omit it to get
    every edit across the deal (newest first).
    """
    rows = await list_edits(
        session, deal_id=str(deal_id), section_id=section_id
    )
    return [MemoEditRecord(**r) for r in rows]
