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
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    sections: list[dict[str, Any]] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)


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
async def gate1(deal_id: UUID, body: Gate1Body) -> GateResponse:
    """HITL Gate 1: accept / reject / edit the normalized spread."""
    logger.info("gate1(stub): deal=%s decision=%s", deal_id, body.decision)
    return GateResponse(
        id=deal_id,
        gate="gate1",
        accepted=body.decision == "approve",
        next_state="run_engines",
    )


@router.post("/{deal_id}/gate2", response_model=GateResponse)
async def gate2(deal_id: UUID, body: Gate2Body) -> GateResponse:
    """HITL Gate 2: final recommendation on the IC memo."""
    logger.info("gate2(stub): deal=%s recommendation=%s", deal_id, body.recommendation)
    return GateResponse(
        id=deal_id, gate="gate2", accepted=True, next_state="finalize"
    )


@router.get("/{deal_id}/memo", response_model=MemoEnvelope)
async def get_memo(deal_id: UUID) -> MemoEnvelope:
    """Final IC memo (JSON envelope). Stub returns empty sections."""
    return MemoEnvelope(deal_id=deal_id)


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


async def _load_deal_payload(deal_id: str) -> Any:
    """Build an ``AnalystInput`` from the persisted deal + engine state.

    Until the live deal flow is fully wired through the database we fall
    back to the Kimpton Angler fixture so the streaming endpoint demos
    end-to-end. Once ``apps/worker/app/storage`` lands a deal-fetch helper
    this function should prefer real DB rows when available.
    """
    from ..agents.analyst import AnalystInput, AnalystSourceDocument
    from ..export.fixtures import kimpton_deal, kimpton_memo, kimpton_model

    settings = get_settings()
    deal = kimpton_deal()
    deal["id"] = deal_id
    model = kimpton_model()
    memo = kimpton_memo()

    # Build a lightweight set of source documents from the memo's
    # appendix so the Analyst has something to cite. Each filename
    # maps to a deterministic synthetic document_id; the Analyst
    # tolerates citations that reference any of these ids.
    docs: list[AnalystSourceDocument] = []
    for idx, fname in enumerate(memo.get("appendix", {}).get("documents_reviewed", []), start=1):
        docs.append(
            AnalystSourceDocument(
                document_id=f"doc-{idx:02d}",
                filename=fname,
                doc_type="reference",
                page_count=1,
                excerpts_by_page={1: f"Reference excerpt for {fname}."},
            )
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


@router.post("/{deal_id}/memo/generate")
async def trigger_memo_generation(
    deal_id: str, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """Kick off the streaming Opus memo draft. Returns immediately.

    The Analyst publishes one section at a time to the in-process
    ``MemoBroadcast`` keyed by ``memo:{deal_id}``; clients should
    immediately open ``GET /deals/{deal_id}/memo/stream`` to receive
    the sections via SSE.
    """
    from ..agents.analyst import run_analyst_streaming

    payload = await _load_deal_payload(deal_id)
    background_tasks.add_task(run_analyst_streaming, payload)
    logger.info("memo/generate: scheduled streaming draft for deal=%s", deal_id)
    return {"status": "started", "deal_id": deal_id}


@router.get("/{deal_id}/memo/stream")
async def stream_memo(deal_id: str) -> StreamingResponse:
    """SSE stream of memo sections as the Analyst writes them.

    Subscribes to the in-process ``MemoBroadcast`` channel
    ``memo:{deal_id}``. Each completed section is emitted as a
    ``section`` SSE event; a final ``done`` event closes the stream.
    """
    from ..streaming import DONE_SENTINEL, get_broadcast

    broadcast = get_broadcast()
    channel = f"memo:{deal_id}"

    async def event_stream() -> AsyncIterator[bytes]:
        try:
            async for event in broadcast.subscribe(channel):
                event_name = event.get("event", "section")
                if event_name == DONE_SENTINEL:
                    payload = json.dumps(
                        {
                            "data": event.get("data", {}),
                            "metadata": event.get("metadata", {}),
                        }
                    )
                    yield f"event: done\ndata: {payload}\n\n".encode()
                    break
                payload = json.dumps(
                    {
                        "data": event.get("data", {}),
                        "metadata": event.get("metadata", {}),
                    }
                )
                yield f"event: section\ndata: {payload}\n\n".encode()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("memo/stream: subscriber loop failed (%s)", exc)
            err = json.dumps({"error": str(exc)})
            yield f"event: error\ndata: {err}\n\n".encode()

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
