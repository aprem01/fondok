"""AI analysis endpoints (memo drafting, variance, deal-level Q&A).

The variance endpoint is the only one wired to real data right now —
``POST /analysis/{deal_id}/analyze`` is intentionally a stub that
returns a queued ``job_id`` so existing clients don't break, but the
underlying free-form analysis flow is future work and there is no
backing job runner yet (TODO(analysis-job-runner)).

``GET /analysis/{deal_id}/variance`` runs the **deterministic** part
of the Variance agent on read — it pulls the latest extraction
results for the deal, splits them into broker-proforma vs T-12
buckets the same way the Critic does, and emits the rule-based
``VarianceFlag`` set (severity from the USALI rule catalog). The LLM
narrative pass is intentionally skipped here so this endpoint is
free + idempotent on every poll. A persisted ``variance_reports``
table is on the roadmap; until then this on-the-fly compute is
adequate for the web app's variance panel.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


class AnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str | None = None
    section: str | None = None


class AnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    job_id: str
    status: str = "queued"


class VarianceFlagOut(BaseModel):
    """One variance flag — broker proforma vs T-12 actual on a single field."""

    model_config = ConfigDict(extra="forbid")

    field: str
    rule_id: str | None = None
    severity: str
    actual: float | None = None
    broker: float | None = None
    delta: float | None = None
    delta_pct: float | None = None
    source_page: int | None = None
    note: str | None = None


class VarianceReportResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    flags: list[VarianceFlagOut] = Field(default_factory=list)
    critical_count: int = 0
    warn_count: int = 0
    info_count: int = 0
    note: str | None = None


@router.post("/{deal_id}/analyze", response_model=AnalysisResponse)
async def analyze(deal_id: UUID, body: AnalysisRequest) -> AnalysisResponse:
    """Stub: kick off an Analyst run for a section or freeform prompt.

    TODO(analysis-job-runner): no backing job runner yet — the returned
    ``job_id`` is a placeholder. The web app should treat this as
    fire-and-forget until the deal-scoped Q&A flow is wired (will
    likely reuse the memo streaming broadcast under a different
    channel key).
    """
    logger.info("analysis(stub): deal=%s section=%s", deal_id, body.section)
    return AnalysisResponse(deal_id=deal_id, job_id="stub-job")


@router.get("/{deal_id}/variance", response_model=VarianceReportResponse)
async def get_variance(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> VarianceReportResponse:
    """Compute deterministic variance flags from the latest extraction.

    Pulls broker-proforma + T-12 buckets the same way the Critic does
    (see ``api/documents.py::_load_critic_inputs``) and runs the rule-
    based portion of the Variance agent. The LLM narrative pass is
    skipped on this read path; the resulting flag list and severity
    counts are still accurate per the USALI rule catalog.

    Returns an empty flag list (with a ``note`` explaining why) when
    either side of the comparison is missing — common until both an
    OM/broker proforma and a T-12 have been extracted on the deal.
    """
    # We reuse the documents module's loader so the broker / actual
    # mapping stays consistent with the Critic's input shape.
    from .documents import _load_critic_inputs  # local import: avoids cycle

    broker, actuals, _market_context, _keys = await _load_critic_inputs(
        session, deal_id=str(deal_id)
    )

    if actuals is None or broker is None:
        # Surface a structured "nothing to compare yet" response rather
        # than 404 — the web app calls this on every status poll and
        # an empty flag list is the right zero-value.
        missing = []
        if actuals is None:
            missing.append("T-12 actuals")
        if broker is None:
            missing.append("broker proforma")
        note = (
            "no flags computed: missing " + ", ".join(missing)
            + ". Upload + extract both an OM/broker-proforma and a T-12 "
            "to populate variance."
        )
        return VarianceReportResponse(deal_id=deal_id, flags=[], note=note)

    # We have both sides. Run the deterministic flag builder via the
    # public agent entrypoint — but skip persistence and the LLM pass.
    # ``run_variance`` already short-circuits to the deterministic-only
    # path when narration fails, but we want explicit control here, so
    # we call the underlying flag builder directly. It's stable API
    # within the worker — exported by the variance module for tests.
    from ..agents.variance import (
        _broker_fields_from_extraction,
        _build_flags,
    )

    try:
        from fondok_schemas import ExtractionField
    except ImportError as exc:  # pragma: no cover - schemas always present
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="fondok_schemas unavailable",
        ) from exc

    # Pull every extracted field on the deal so we can rebuild the
    # broker-proforma input set the same way the agent does.
    rows = await session.execute(
        text(
            """
            SELECT er.fields
              FROM extraction_results er
             WHERE er.deal_id = :deal AND er.tenant_id = :tenant
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )
    all_fields: list[Any] = []
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
                except Exception:
                    continue

    broker_fields = _broker_fields_from_extraction(all_fields)
    if not broker_fields:
        return VarianceReportResponse(
            deal_id=deal_id,
            flags=[],
            note=(
                "no broker proforma fields detected in extractions yet — "
                "upload + extract an OM/broker proforma to populate variance"
            ),
        )

    flags = _build_flags(
        deal_uuid=deal_id, actuals=actuals, broker_fields=broker_fields
    )

    out_flags: list[VarianceFlagOut] = []
    crit = warn = info = 0
    for f in flags:
        sev = f.severity.value if hasattr(f.severity, "value") else str(f.severity)
        if sev == "critical":
            crit += 1
        elif sev == "warn":
            warn += 1
        else:
            info += 1
        out_flags.append(
            VarianceFlagOut(
                field=f.field,
                rule_id=f.rule_id,
                severity=sev,
                actual=f.actual,
                broker=f.broker,
                delta=f.delta,
                delta_pct=f.delta_pct,
                source_page=f.source_page,
                # Deterministic stand-in for the LLM-generated note.
                # Skipping LLM keeps this endpoint free + idempotent.
                note=(
                    f"{f.field}: broker={f.broker:,.2f} vs actual={f.actual:,.2f} "
                    f"({(f.delta_pct or 0):.1%}); rule {f.rule_id}"
                ),
            )
        )

    return VarianceReportResponse(
        deal_id=deal_id,
        flags=out_flags,
        critical_count=crit,
        warn_count=warn,
        info_count=info,
    )
