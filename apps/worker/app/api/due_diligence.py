"""Due Diligence API — broker questions generated from extracted state.

Endpoints
---------
POST   /deals/{deal_id}/due-diligence/generate   — run agent + persist
GET    /deals/{deal_id}/due-diligence            — list latest questions
PATCH  /deals/{deal_id}/due-diligence/{qid}      — update status (pending/sent/answered)

The Due Diligence sub-tab on the P&L page renders the response of GET.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── response shapes ───────────────────────────


class DueDiligenceQuestionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    question: str
    narrative: str
    priority: Literal["high", "medium", "low"]
    category: Literal["revenue", "expenses", "operations", "market", "capex"]
    source: str
    supporting_metric_key: str | None = None
    supporting_metric_value: str | None = None
    status: Literal["pending", "sent", "answered"] = "pending"
    created_at: datetime
    sent_at: datetime | None = None


class DueDiligencePacketResponse(BaseModel):
    """Latest packet for a deal, plus a per-status counter the UI uses
    for the four KPI cards (total / high priority / pending / answered).
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    questions: list[DueDiligenceQuestionOut] = Field(default_factory=list)
    total: int = 0
    high_priority: int = 0
    pending: int = 0
    answered: int = 0
    note: str | None = None


class DueDiligenceGenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    generated: int
    error: str | None = None


class DueDiligenceStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["pending", "sent", "answered"]


# ─────────────────────── helpers ───────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_out(row_mapping: Any) -> DueDiligenceQuestionOut:
    return DueDiligenceQuestionOut(
        id=UUID(str(row_mapping["id"])),
        deal_id=UUID(str(row_mapping["deal_id"])),
        question=row_mapping["question"],
        narrative=row_mapping["narrative"],
        priority=row_mapping["priority"],
        category=row_mapping["category"],
        source=row_mapping["source"],
        supporting_metric_key=row_mapping.get("supporting_metric_key"),
        supporting_metric_value=row_mapping.get("supporting_metric_value"),
        status=row_mapping["status"],
        created_at=_coerce_dt(row_mapping["created_at"]),
        sent_at=_coerce_dt(row_mapping.get("sent_at")) if row_mapping.get("sent_at") else None,
    )


def _coerce_dt(value: Any) -> datetime:
    if value is None:
        return _now()
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


# ─────────────────────── GET — list questions ───────────────────────


@router.get(
    "/{deal_id}/due-diligence", response_model=DueDiligencePacketResponse
)
async def get_due_diligence(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DueDiligencePacketResponse:
    """Return the latest persisted broker-question packet for ``deal_id``.

    Returns an empty packet (with a structured ``note``) when the agent
    hasn't run yet — the UI renders the "Generate Due Diligence
    Questions" empty state in that case.
    """
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, question, narrative, priority,
                   category, source, supporting_metric_key,
                   supporting_metric_value, status, created_at, sent_at
              FROM due_diligence_questions
             WHERE deal_id = :deal
             ORDER BY
                 CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                 created_at DESC
            """
        ),
        {"deal": str(deal_id)},
    )
    fetched = rows.fetchall()
    if not fetched:
        return DueDiligencePacketResponse(
            deal_id=deal_id,
            questions=[],
            note=(
                "No broker questions generated yet. "
                "Run extraction on the OM + T-12 first, then "
                "POST /deals/{id}/due-diligence/generate."
            ),
        )

    questions = [_row_to_out(r._mapping) for r in fetched]
    high = sum(1 for q in questions if q.priority == "high")
    pending = sum(1 for q in questions if q.status == "pending")
    answered = sum(1 for q in questions if q.status == "answered")

    return DueDiligencePacketResponse(
        deal_id=deal_id,
        questions=questions,
        total=len(questions),
        high_priority=high,
        pending=pending,
        answered=answered,
    )


# ─────────────────────── POST — generate packet ───────────────────────


@router.post(
    "/{deal_id}/due-diligence/generate",
    response_model=DueDiligenceGenerateResponse,
)
async def generate_due_diligence(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DueDiligenceGenerateResponse:
    """Run the Due Diligence agent and persist the new question set.

    Replaces any prior packet for this deal — older runs stay in
    audit_log if needed but the live API surface only ever shows the
    most recent generation. This is the right contract for a "regenerate"
    button: deterministic single state, no merge logic to reason about.
    """
    from ..agents.due_diligence import (
        DueDiligenceInput,
        run_due_diligence,
    )

    payload = await _build_due_diligence_input(
        session, deal_id=str(deal_id), tenant_id=str(tenant_id)
    )

    out = await run_due_diligence(payload)
    if not out.success:
        return DueDiligenceGenerateResponse(
            deal_id=deal_id,
            generated=0,
            error=out.error or "due diligence agent failed",
        )

    # Replace prior questions with the fresh set.
    try:
        await session.execute(
            text("DELETE FROM due_diligence_questions WHERE deal_id = :deal"),
            {"deal": str(deal_id)},
        )
        for q in out.questions:
            await session.execute(
                text(
                    """
                    INSERT INTO due_diligence_questions (
                        id, deal_id, tenant_id, question, narrative,
                        priority, category, source,
                        supporting_metric_key, supporting_metric_value,
                        status, created_at
                    ) VALUES (
                        :id, :deal, :tenant, :question, :narrative,
                        :priority, :category, :source,
                        :smk, :smv,
                        'pending', :created_at
                    )
                    """
                ),
                {
                    "id": str(q.id),
                    "deal": str(deal_id),
                    "tenant": str(tenant_id),
                    "question": q.question,
                    "narrative": q.narrative,
                    "priority": q.priority,
                    "category": q.category,
                    "source": q.source,
                    "smk": q.supporting_metric_key,
                    "smv": q.supporting_metric_value,
                    "created_at": q.created_at,
                },
            )
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "due_diligence: persistence failed for deal=%s — %s", deal_id, exc
        )
        return DueDiligenceGenerateResponse(
            deal_id=deal_id,
            generated=0,
            error=f"persistence: {type(exc).__name__}: {exc}",
        )

    return DueDiligenceGenerateResponse(
        deal_id=deal_id,
        generated=len(out.questions),
    )


# ─────────────────────── PATCH — status update ───────────────────────


@router.patch(
    "/{deal_id}/due-diligence/{question_id}",
    response_model=DueDiligenceQuestionOut,
)
async def update_due_diligence_status(
    deal_id: UUID,
    question_id: UUID,
    body: DueDiligenceStatusUpdate,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DueDiligenceQuestionOut:
    """Move a question through the lifecycle (pending → sent → answered).

    Used by the UI's "Mark as Sent" batch action and the per-question
    answered toggle. Stamps ``sent_at`` when status flips to ``sent``.
    """
    sent_at = _now() if body.status == "sent" else None
    result = await session.execute(
        text(
            """
            UPDATE due_diligence_questions
               SET status = :status,
                   sent_at = COALESCE(:sent_at, sent_at)
             WHERE id = :id AND deal_id = :deal
            """
        ),
        {
            "status": body.status,
            "sent_at": sent_at,
            "id": str(question_id),
            "deal": str(deal_id),
        },
    )
    if result.rowcount == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"question {question_id} not found on deal {deal_id}",
        )
    await session.commit()

    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, question, narrative, priority,
                       category, source, supporting_metric_key,
                       supporting_metric_value, status, created_at, sent_at
                  FROM due_diligence_questions
                 WHERE id = :id
                """
            ),
            {"id": str(question_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=500, detail="row vanished after update")
    return _row_to_out(row._mapping)


# ─────────────────────── input builder ───────────────────────


async def _build_due_diligence_input(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> Any:
    """Build the agent's input from the deal's persisted state.

    Pulls a compact summary of every layer the LLM keys off:
    - deal metadata (deals row)
    - per-doc-type extracted field summary (extraction_results)
    - engine output headlines (engine_outputs)
    - variance flags (computed inline from the variance helper)
    - market data (CBRE / STR / benchmark)
    """
    from ..agents.due_diligence import DueDiligenceInput

    deal_data = await _load_deal_metadata(session, deal_id=deal_id)
    extracted_summary = await _summarize_extractions(session, deal_id=deal_id)
    engine_outputs = await _summarize_engine_outputs(session, deal_id=deal_id)
    variance_flags = await _summarize_variance(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    market_data = await _summarize_market_data(session, deal_id=deal_id)

    return DueDiligenceInput(
        tenant_id=tenant_id,
        deal_id=deal_id,
        deal_data=deal_data,
        extracted_summary=extracted_summary,
        engine_outputs=engine_outputs,
        variance_flags=variance_flags,
        market_data=market_data,
    )


async def _load_deal_metadata(
    session: AsyncSession, *, deal_id: str
) -> dict[str, Any]:
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT name, city, keys, brand, service, deal_stage,
                           return_profile
                      FROM deals
                     WHERE id = :id
                    """
                ),
                {"id": deal_id},
            )
        ).first()
    except Exception:  # noqa: BLE001
        return {}
    if row is None:
        return {}
    return {k: v for k, v in dict(row._mapping).items() if v is not None}


async def _summarize_extractions(
    session: AsyncSession, *, deal_id: str
) -> dict[str, Any]:
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:  # noqa: BLE001
        return {}

    by_type: dict[str, dict[str, Any]] = {}
    for r in rows.fetchall():
        m = r._mapping
        doc_type = (m.get("doc_type") or "UNKNOWN").upper()
        raw = m["fields"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw, list):
            continue
        bucket = by_type.setdefault(doc_type, {})
        for f in raw:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip()
            value = f.get("value")
            if not name or value is None:
                continue
            bucket.setdefault(name, value)
        # Cap each doc_type's field count to keep the prompt budget sane.
        if len(bucket) > 60:
            keep = dict(list(bucket.items())[:60])
            keep["__truncated__"] = f"{len(bucket) - 60} fields elided"
            by_type[doc_type] = keep
    return by_type


async def _summarize_engine_outputs(
    session: AsyncSession, *, deal_id: str
) -> dict[str, Any]:
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT engine_name, summary, outputs
                  FROM engine_outputs
                 WHERE deal_id = :deal AND status = 'complete'
                """
            ),
            {"deal": deal_id},
        )
    except Exception:  # noqa: BLE001
        return {}

    out: dict[str, Any] = {}
    for r in rows.fetchall():
        m = r._mapping
        name = m["engine_name"]
        summary = m.get("summary")
        if name in out:
            continue
        bucket: dict[str, Any] = {}
        if summary:
            bucket["summary"] = summary
        outputs_blob = m.get("outputs")
        if isinstance(outputs_blob, str):
            try:
                outputs_blob = json.loads(outputs_blob) if outputs_blob else None
            except (json.JSONDecodeError, TypeError):
                outputs_blob = None
        if isinstance(outputs_blob, dict):
            # Only top-level scalar fields; skip nested structures.
            for k, v in outputs_blob.items():
                if isinstance(v, (int, float, str, bool)) and not isinstance(v, bool):
                    bucket[k] = v
        out[name] = bucket
    return out


async def _summarize_variance(
    session: AsyncSession, *, deal_id: str, tenant_id: str
) -> list[dict[str, Any]]:
    """Reuse the variance endpoint's flag computation, return summary rows."""
    try:
        from .analysis import _broker_vs_market_flags  # local
        from .documents import _load_critic_inputs
        from ..agents.variance import _broker_fields_from_extraction, _build_flags
        from fondok_schemas import ExtractionField
    except Exception:  # noqa: BLE001
        return []

    try:
        broker, actuals, _market_context, _keys = await _load_critic_inputs(
            session, deal_id=deal_id
        )
    except Exception:  # noqa: BLE001
        return []
    if broker is None or actuals is None:
        return []

    # Pull every extraction field for broker-fields builder.
    try:
        rows = await session.execute(
            text(
                "SELECT fields FROM extraction_results WHERE deal_id = :deal"
            ),
            {"deal": deal_id},
        )
    except Exception:  # noqa: BLE001
        return []
    all_fields = []
    for r in rows.fetchall():
        raw = r._mapping["fields"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if isinstance(raw, list):
            for f in raw:
                if not isinstance(f, dict):
                    continue
                try:
                    all_fields.append(ExtractionField.model_validate(f))
                except Exception:  # noqa: BLE001
                    continue

    broker_fields = _broker_fields_from_extraction(all_fields)
    flags_summary: list[dict[str, Any]] = []
    if broker_fields:
        try:
            flags = _build_flags(
                deal_uuid=UUID(deal_id) if _is_uuid(deal_id) else uuid4(),
                actuals=actuals,
                broker_fields=broker_fields,
            )
        except Exception:  # noqa: BLE001
            flags = []
        for f in flags:
            sev = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
            flags_summary.append(
                {
                    "field": f.field,
                    "rule_id": f.rule_id,
                    "severity": sev,
                    "actual": f.actual,
                    "broker": f.broker,
                    "delta_pct": f.delta_pct,
                }
            )

    # Broker vs market flags (CBRE comparison) — already exposed by analysis.
    try:
        market_flags = await _broker_vs_market_flags(
            session, deal_id=deal_id, broker_proforma=broker, actuals=actuals
        )
        for mf in market_flags:
            flags_summary.append(
                {
                    "field": mf.field,
                    "rule_id": mf.rule_id,
                    "severity": mf.severity,
                    "actual": mf.actual,
                    "broker": mf.broker,
                    "delta_pct": mf.delta_pct,
                }
            )
    except Exception:  # noqa: BLE001
        pass

    return flags_summary


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
        return True
    except (TypeError, ValueError):
        return False


async def _summarize_market_data(
    session: AsyncSession, *, deal_id: str
) -> dict[str, Any]:
    """Pull the deal's STR / CBRE / benchmark blocks via the existing endpoint logic."""
    try:
        from .documents import (
            _aggregate_market_data,  # type: ignore[attr-defined]
        )
    except ImportError:
        return {}
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields,
                       er.document_id,
                       d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND UPPER(COALESCE(d.doc_type, '')) IN (
                       'STR_TREND', 'CBRE_HORIZONS', 'PNL_BENCHMARK'
                   )
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:  # noqa: BLE001
        return {}

    materialized = [dict(r._mapping) for r in rows.fetchall()]
    try:
        envelope = _aggregate_market_data(materialized, UUID(deal_id))
    except Exception:  # noqa: BLE001
        return {}

    out: dict[str, Any] = {}
    if hasattr(envelope, "str_trend") and envelope.str_trend is not None:
        out["str_trend"] = envelope.str_trend.model_dump(exclude_none=True)
    if hasattr(envelope, "cbre_horizons") and envelope.cbre_horizons is not None:
        out["cbre_horizons"] = envelope.cbre_horizons.model_dump(exclude_none=True)
    if hasattr(envelope, "pnl_benchmark") and envelope.pnl_benchmark is not None:
        out["pnl_benchmark"] = envelope.pnl_benchmark.model_dump(exclude_none=True)
    return out


__all__ = ["router"]
