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

Broker-question endpoints (Wave 1, roadmap item #4) live in this
module too — see ``list_broker_questions`` / ``update_broker_question_state``
/ ``refresh_broker_questions``. Those are backed by the deterministic
``HistoricalVariance`` engine, NOT the LLM variance agent above.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from .deals import _assert_deal_belongs_to_tenant, get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


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


def _is_sqlite(session: AsyncSession) -> bool:
    return session.bind is not None and session.bind.dialect.name == "sqlite"


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
async def analyze(
    deal_id: UUID,
    body: AnalysisRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> AnalysisResponse:
    """Stub: kick off an Analyst run for a section or freeform prompt.

    TODO(analysis-job-runner): no backing job runner yet — the returned
    ``job_id`` is a placeholder. The web app should treat this as
    fire-and-forget until the deal-scoped Q&A flow is wired (will
    likely reuse the memo streaming broadcast under a different
    channel key).

    Tenant-scoped: a cross-tenant ``deal_id`` returns 404 even though
    the stub never reads any data — keeps the surface area uniform so
    a future implementation can't accidentally regress to leaking.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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

    Tenant-scoped: cross-tenant access returns 404 before the loader
    reads any extraction data.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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

    # Broker vs market forecast (May 7 scope): compare broker-projected
    # growth against CBRE Horizons' published submarket forecast and
    # flag any line where the broker is meaningfully above the
    # market's view. This is what makes Fondok an investment tool —
    # not just T-12 reconciliation but discipline against the broker
    # over-projecting forward growth.
    market_flags = await _broker_vs_market_flags(
        session, deal_id=str(deal_id), broker_proforma=broker, actuals=actuals
    )
    for mf in market_flags:
        if mf.severity == "Critical":
            crit += 1
        elif mf.severity == "Warn":
            warn += 1
        else:
            info += 1
        out_flags.append(mf)

    return VarianceReportResponse(
        deal_id=deal_id,
        flags=out_flags,
        critical_count=crit,
        warn_count=warn,
        info_count=info,
    )


async def _broker_vs_market_flags(
    session: AsyncSession,
    *,
    deal_id: str,
    broker_proforma: Any,
    actuals: Any,
) -> list[VarianceFlagOut]:
    """Compare broker-projected ADR / RevPAR growth vs CBRE Horizons.

    Reads the deal's CBRE Horizons extraction (when present) and
    derives the published submarket CAGR for ADR and RevPAR. Compares
    the broker's implied growth (broker Year-1 / T-12 - 1) against
    the market forecast. Flags severity:
      |delta| ≥ 5pts → Critical (broker over-projects market growth)
      |delta| ≥ 2pts → Warn
      otherwise     → Info

    Returns ``[]`` when the CBRE report hasn't been extracted, the
    deal id isn't a UUID, or the extraction doesn't carry enough
    years to compute a CAGR.
    """
    from ..services.engine_runner import _load_cbre_horizons_overrides

    market_overrides = await _load_cbre_horizons_overrides(
        session, deal_id=deal_id
    )
    if not market_overrides:
        return []

    flags: list[VarianceFlagOut] = []
    namespace_uuid = UUID("00000000-0000-0000-0000-000000000000")

    # ADR comparison: broker Year-1 ADR / T-12 ADR - 1.
    market_adr_growth = market_overrides.get("adr_growth")
    if (
        market_adr_growth is not None
        and getattr(broker_proforma, "adr", None)
        and getattr(actuals, "adr", None)
        and actuals.adr > 0
    ):
        broker_adr_growth = (broker_proforma.adr / actuals.adr) - 1.0
        delta = broker_adr_growth - market_adr_growth
        severity = (
            "Critical" if abs(delta) >= 0.05
            else "Warn" if abs(delta) >= 0.02
            else "Info"
        )
        flags.append(
            VarianceFlagOut(
                field="broker_adr_growth_vs_market",
                rule_id="BROKER_VS_CBRE_ADR_GROWTH",
                severity=severity,
                actual=market_adr_growth,
                broker=broker_adr_growth,
                delta=delta,
                delta_pct=delta,
                source_page=None,
                note=(
                    f"Broker projects {broker_adr_growth:.1%} Y1 ADR growth vs CBRE "
                    f"published submarket forecast of {market_adr_growth:.1%} ({delta:+.1%})"
                ),
            )
        )

    # RevPAR comparison: broker Year-1 RevPAR / T-12 RevPAR - 1.
    market_revpar_growth = market_overrides.get("revpar_growth")
    if (
        market_revpar_growth is not None
        and getattr(broker_proforma, "revpar", None)
        and getattr(actuals, "revpar", None)
        and actuals.revpar > 0
    ):
        broker_revpar_growth = (broker_proforma.revpar / actuals.revpar) - 1.0
        delta = broker_revpar_growth - market_revpar_growth
        severity = (
            "Critical" if abs(delta) >= 0.05
            else "Warn" if abs(delta) >= 0.02
            else "Info"
        )
        flags.append(
            VarianceFlagOut(
                field="broker_revpar_growth_vs_market",
                rule_id="BROKER_VS_CBRE_REVPAR_GROWTH",
                severity=severity,
                actual=market_revpar_growth,
                broker=broker_revpar_growth,
                delta=delta,
                delta_pct=delta,
                source_page=None,
                note=(
                    f"Broker projects {broker_revpar_growth:.1%} Y1 RevPAR growth vs "
                    f"CBRE submarket forecast {market_revpar_growth:.1%} ({delta:+.1%})"
                ),
            )
        )

    _ = namespace_uuid  # reserved for future deterministic flag-id stable hashing
    return flags


# ════════════════════════════════════════════════════════════════════
# Wave 1 — Broker questions (roadmap item #4)
#
# Deterministic YoY variance → copy-paste-ready broker questions.
# The engine lives in ``app/engines/historical_variance.py``; this
# section is the HTTP + persistence surface. See the engine module
# docstring for the threshold + severity ladder.
# ════════════════════════════════════════════════════════════════════


# ────────────────────────── response shapes ─────────────────────────


class BrokerQuestionOut(BaseModel):
    """One persisted broker question. Mirrors ``fondok_schemas.BrokerQuestion``
    but is locally declared so the API layer doesn't take a hard
    dependency on a fondok_schemas import path at request time (the
    schemas package is sometimes installed lazily in dev).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    line_item: str
    period_key: str
    variance_pct: float
    actual_prior: float | None = None
    actual_current: float | None = None
    threshold_pct: float
    severity: Literal["CRITICAL", "WARN", "INFO"]
    question_text: str
    state: Literal["pending", "dismissed", "sent", "answered"]
    dismissal_reason: str | None = None
    broker_response: str | None = None
    created_at: datetime
    updated_at: datetime


class UpdateStateBody(BaseModel):
    """PATCH body for ``update_broker_question_state``.

    State machine:
        ``pending``    → ``dismissed`` | ``sent``
        ``sent``       → ``answered``
        ``dismissed`` / ``answered`` are terminal.

    ``dismissal_reason`` is required when ``next_state == "dismissed"``
    (kept optional at the type level so an answered/sent transition
    doesn't have to send a null sentinel; runtime check below).
    ``broker_response`` is required when transitioning to ``answered``.
    """

    model_config = ConfigDict(extra="forbid")

    next_state: Literal["pending", "dismissed", "sent", "answered"]
    dismissal_reason: str | None = Field(default=None, max_length=2000)
    broker_response: str | None = Field(default=None, max_length=4000)


class TriggerBody(BaseModel):
    """Body for ``refresh_broker_questions``. Reserved for future
    knobs (e.g. ``force=True`` to wipe + regenerate). Kept as a sealed
    Pydantic model so callers can't smuggle ad-hoc fields past us.
    """

    model_config = ConfigDict(extra="forbid")


# ──────────────────────────── row helpers ────────────────────────────


def _row_to_broker_question(row_mapping: Any) -> BrokerQuestionOut:
    """SQL row → response shape. Tolerates SQLite (string UUIDs, naive
    datetimes) and Postgres (UUID/datetime objects) equally.
    """
    return BrokerQuestionOut(
        id=UUID(str(row_mapping["id"])),
        deal_id=UUID(str(row_mapping["deal_id"])),
        line_item=row_mapping["line_item"],
        period_key=row_mapping["period_key"],
        variance_pct=float(row_mapping["variance_pct"]),
        actual_prior=(
            float(row_mapping["actual_prior"])
            if row_mapping.get("actual_prior") is not None
            else None
        ),
        actual_current=(
            float(row_mapping["actual_current"])
            if row_mapping.get("actual_current") is not None
            else None
        ),
        threshold_pct=float(row_mapping["threshold_pct"]),
        severity=row_mapping["severity"],
        question_text=row_mapping["question_text"],
        state=row_mapping["state"],
        dismissal_reason=row_mapping.get("dismissal_reason"),
        broker_response=row_mapping.get("broker_response"),
        created_at=_coerce_dt(row_mapping["created_at"]),
        updated_at=_coerce_dt(row_mapping["updated_at"]),
    )


# State-transition catalog. The PATCH endpoint rejects any move not
# whitelisted here. Kept here (not in the engine) because it's an API-
# layer policy, not an engine concern.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"dismissed", "sent"},
    "sent": {"answered"},
    # Terminal states — no further transitions allowed.
    "dismissed": set(),
    "answered": set(),
}


_BROKER_QUESTION_COLUMNS = (
    "id, deal_id, tenant_id, line_item, period_key, variance_pct, "
    "actual_prior, actual_current, threshold_pct, severity, "
    "question_text, state, dismissal_reason, broker_response, "
    "created_at, updated_at"
)


async def _assert_deal_in_tenant(
    session: AsyncSession, *, deal_id: UUID, tenant_id: UUID
) -> None:
    """404 if the deal doesn't belong to this tenant — defense in depth.

    Returning 404 (not 403) is intentional for parity with the rest of
    the deal API: leaks no information about cross-tenant deal ids.
    """
    row = (
        await session.execute(
            text(
                "SELECT 1 FROM deals "
                "WHERE id = :id AND tenant_id = :tenant LIMIT 1"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )


# ────────────────────── historical P&L loader ───────────────────────


def _coerce_fields_blob(raw: Any) -> list[dict[str, Any]]:
    """Normalize the ``extraction_results.fields`` column.

    Postgres hands us a parsed list; SQLite hands us a JSON-encoded
    string. Either way we want ``list[dict]`` with string field_name
    and a numeric/None value.
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for f in raw:
        if isinstance(f, dict):
            out.append(f)
    return out


async def _load_historical_pnls(
    session: AsyncSession, *, deal_id: str, tenant_id: str
) -> list[dict[str, Any]]:
    """Pull every P&L extraction on the deal, project into per-year
    flat dicts the engine understands.

    Strategy
    --------

    1. Query ``extraction_results`` joined to ``documents`` for any
       PNL/T12 family doc.
    2. For each row, flatten the ``fields`` list into ``{field_name: value}``.
    3. Resolve the document's year — preferring ``documents.fiscal_year``
       (set explicitly by the wizard), falling back to the period
       metadata fields the extractor emits
       (``p_and_l_usali.period_ending`` / ``period_label``).
    4. When two docs land on the same year, the most-recent extraction
       wins (the SQL orders by ``created_at DESC`` and we keep the
       first row per year).
    """
    rows = await session.execute(
        text(
            """
            SELECT er.fields,
                   d.fiscal_year,
                   d.doc_type,
                   er.created_at
              FROM extraction_results er
              JOIN documents d ON d.id = er.document_id
             WHERE er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND UPPER(COALESCE(d.doc_type, '')) IN (
                   'T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD'
               )
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": deal_id, "tenant": tenant_id},
    )

    pnls_by_year: dict[int, dict[str, Any]] = {}
    for r in rows.fetchall():
        m = r._mapping
        fields = _coerce_fields_blob(m["fields"])
        if not fields:
            continue

        # Flatten the extracted field list into a single dict.
        flat: dict[str, Any] = {}
        for f in fields:
            name = f.get("field_name")
            if not isinstance(name, str):
                continue
            flat[name] = f.get("value")

        # Year resolution priority: explicit fiscal_year column →
        # ``p_and_l_usali.period_label`` → ``period_ending`` → bare
        # ``period_ending`` (no prefix, OM-style).
        year: int | None = None
        fiscal = m.get("fiscal_year")
        if isinstance(fiscal, int) and 1900 < fiscal < 2100:
            year = fiscal
        if year is None:
            for key in (
                "p_and_l_usali.period_label",
                "p_and_l_usali.period_ending",
                "p_and_l_usali.period_start",
                "period_label",
                "period_ending",
            ):
                v = flat.get(key)
                if isinstance(v, str):
                    for token in v.replace("-", " ").replace("/", " ").split():
                        if token.isdigit() and 1900 < int(token) < 2100:
                            year = int(token)
                            break
                if year is not None:
                    break

        if year is None:
            continue

        # First row wins (SQL is DESC by created_at) — most-recent
        # extraction is canonical when two docs cover the same year.
        if year in pnls_by_year:
            continue

        flat["year"] = year
        pnls_by_year[year] = flat

    # Engine expects ascending sort but the engine itself re-sorts; we
    # return a flat list either way.
    return [pnls_by_year[y] for y in sorted(pnls_by_year.keys())]


# ───────────────────────────── routes ────────────────────────────────


@router.get(
    "/{deal_id}/broker_questions",
    response_model=list[BrokerQuestionOut],
)
async def list_broker_questions(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    state: Annotated[
        str | None,
        Query(description="Filter to one of pending/dismissed/sent/answered"),
    ] = None,
) -> list[BrokerQuestionOut]:
    """List broker questions for a deal, newest first.

    Optional ``?state=`` narrows to one of ``pending`` / ``dismissed``
    / ``sent`` / ``answered``. An unknown state is treated as "no
    filter" — we'd rather return data than 422 on a typo'd UI query.
    """
    await _assert_deal_in_tenant(session, deal_id=deal_id, tenant_id=tenant_id)

    sql = f"""
        SELECT {_BROKER_QUESTION_COLUMNS}
          FROM broker_questions
         WHERE deal_id = :deal AND tenant_id = :tenant
    """
    params: dict[str, Any] = {
        "deal": str(deal_id),
        "tenant": str(tenant_id),
    }
    if state in {"pending", "dismissed", "sent", "answered"}:
        sql += " AND state = :state"
        params["state"] = state
    sql += " ORDER BY created_at DESC"

    rows = await session.execute(text(sql), params)
    return [_row_to_broker_question(r._mapping) for r in rows.fetchall()]


@router.patch(
    "/{deal_id}/broker_questions/{question_id}",
    response_model=BrokerQuestionOut,
)
async def update_broker_question_state(
    deal_id: UUID,
    question_id: UUID,
    body: UpdateStateBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> BrokerQuestionOut:
    """Move a question through its state machine.

    Validates ``current_state → body.next_state`` against
    ``_ALLOWED_TRANSITIONS``; rejects illegal moves with 409. Stamps
    ``updated_at`` and persists ``dismissal_reason`` / ``broker_response``
    when supplied.
    """
    await _assert_deal_in_tenant(session, deal_id=deal_id, tenant_id=tenant_id)

    row = (
        await session.execute(
            text(
                f"""
                SELECT {_BROKER_QUESTION_COLUMNS}
                  FROM broker_questions
                 WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant
                """
            ),
            {
                "id": str(question_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"broker question {question_id} not found",
        )

    current_state = row._mapping["state"]
    allowed = _ALLOWED_TRANSITIONS.get(current_state, set())
    if body.next_state == current_state:
        # No-op: PATCH to the same state. Return the row unchanged.
        return _row_to_broker_question(row._mapping)
    if body.next_state not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"illegal transition {current_state} → {body.next_state}; "
                f"allowed: {sorted(allowed) or '(terminal)'}"
            ),
        )
    if body.next_state == "dismissed" and not body.dismissal_reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="dismissal_reason is required when dismissing a question",
        )
    if body.next_state == "answered" and not body.broker_response:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="broker_response is required when marking a question answered",
        )

    now = _now()
    await session.execute(
        text(
            """
            UPDATE broker_questions
               SET state = :next_state,
                   dismissal_reason = COALESCE(:dismissal_reason, dismissal_reason),
                   broker_response = COALESCE(:broker_response, broker_response),
                   updated_at = :now
             WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant
            """
        ),
        {
            "next_state": body.next_state,
            "dismissal_reason": body.dismissal_reason,
            "broker_response": body.broker_response,
            "now": now,
            "id": str(question_id),
            "deal": str(deal_id),
            "tenant": str(tenant_id),
        },
    )
    await session.commit()

    refreshed = (
        await session.execute(
            text(
                f"""
                SELECT {_BROKER_QUESTION_COLUMNS}
                  FROM broker_questions
                 WHERE id = :id
                """
            ),
            {"id": str(question_id)},
        )
    ).first()
    assert refreshed is not None  # we just updated this row
    return _row_to_broker_question(refreshed._mapping)


@router.post(
    "/{deal_id}/broker_questions/refresh",
    response_model=list[BrokerQuestionOut],
)
async def refresh_broker_questions(
    deal_id: UUID,
    body: TriggerBody,  # sealed envelope; reserved for future knobs
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> list[BrokerQuestionOut]:
    """Re-run the engine against the deal's current historical P&L extractions.

    Behavior:

    * Loads every PNL/T12 extraction on the deal, normalizes to a
      per-year flat dict.
    * Runs the deterministic ``detect_yoy_variances`` engine.
    * For each finding, inserts a new ``broker_questions`` row UNLESS
      an open (``pending`` / ``sent``) row already exists for the same
      ``(deal, line_item, period_key)`` tuple. Dismissed / answered
      rows do NOT block re-emission — analysts who closed out a stale
      question deserve to see it pop again if the underlying data
      hasn't changed (and a new variance after a re-extraction is also
      surfaced this way).

    Returns the full set of open + just-created rows so the UI can
    refresh in one round-trip.
    """
    await _assert_deal_in_tenant(session, deal_id=deal_id, tenant_id=tenant_id)

    # Load + normalize historical P&Ls.
    historical = await _load_historical_pnls(
        session, deal_id=str(deal_id), tenant_id=str(tenant_id)
    )

    # Lazy import — keeps engine off the import-time critical path.
    from ..engines.historical_variance import detect_yoy_variances

    findings = detect_yoy_variances(historical)

    # Defensive guard against extraction-noise findings whose magnitudes
    # would overflow the broker_questions column constraints
    # (variance_pct NUMERIC(8,4) → ±9999.9999; actual_* NUMERIC(14,2)
    # → ±$999B). The engine can legitimately surface a 100,000% YoY
    # ratio when an extracted prior is near-zero ($0.01 → $1M), but
    # that's almost always a misparsed decimal, not a real signal.
    # Dropping the finding and logging keeps the endpoint working while
    # surfacing the data-quality issue for the analyst.
    VARIANCE_RATIO_CAP = 9000.0  # ratio, NOT percent — 900,000% YoY
    DOLLAR_AMOUNT_CAP = 999_000_000_000.0  # $999B headroom
    safe_findings: list[Any] = []
    for f in findings:
        if abs(f.variance_pct) > VARIANCE_RATIO_CAP:
            logger.warning(
                "broker_questions/refresh: dropping pathological variance "
                "on deal=%s line=%s period=%s ratio=%.2f prior=%.2f current=%.2f "
                "(likely extraction noise — column overflow guard)",
                deal_id, f.line_item, f.period_key,
                f.variance_pct, f.actual_prior, f.actual_current,
            )
            continue
        if abs(f.actual_prior) > DOLLAR_AMOUNT_CAP or abs(f.actual_current) > DOLLAR_AMOUNT_CAP:
            logger.warning(
                "broker_questions/refresh: dropping over-cap dollar values "
                "on deal=%s line=%s period=%s prior=%.2f current=%.2f",
                deal_id, f.line_item, f.period_key,
                f.actual_prior, f.actual_current,
            )
            continue
        safe_findings.append(f)
    findings = safe_findings

    if findings:
        # Pull existing open rows once so the dedupe check is a set
        # lookup, not N+1 SELECTs.
        existing_rows = await session.execute(
            text(
                """
                SELECT line_item, period_key
                  FROM broker_questions
                 WHERE deal_id = :deal AND tenant_id = :tenant
                   AND state IN ('pending', 'sent')
                """
            ),
            {"deal": str(deal_id), "tenant": str(tenant_id)},
        )
        open_keys: set[tuple[str, str]] = {
            (r._mapping["line_item"], r._mapping["period_key"])
            for r in existing_rows.fetchall()
        }

        now = _now()
        for finding in findings:
            key = (finding.line_item, finding.period_key)
            if key in open_keys:
                continue
            await session.execute(
                text(
                    """
                    INSERT INTO broker_questions (
                        id, deal_id, tenant_id, line_item, period_key,
                        variance_pct, actual_prior, actual_current,
                        threshold_pct, severity, question_text, state,
                        dismissal_reason, broker_response,
                        created_at, updated_at
                    ) VALUES (
                        :id, :deal, :tenant, :line_item, :period_key,
                        :variance_pct, :actual_prior, :actual_current,
                        :threshold_pct, :severity, :question_text, 'pending',
                        NULL, NULL,
                        :now, :now
                    )
                    """
                ),
                {
                    "id": str(uuid4()),
                    "deal": str(deal_id),
                    "tenant": str(tenant_id),
                    "line_item": finding.line_item,
                    "period_key": finding.period_key,
                    "variance_pct": finding.variance_pct,
                    "actual_prior": finding.actual_prior,
                    "actual_current": finding.actual_current,
                    "threshold_pct": finding.threshold_pct,
                    "severity": finding.severity,
                    "question_text": finding.question_text,
                    "now": now,
                },
            )
            open_keys.add(key)  # block intra-batch duplicates too
        await session.commit()

    # Return every question on this deal — open or terminal — newest first.
    rows = await session.execute(
        text(
            f"""
            SELECT {_BROKER_QUESTION_COLUMNS}
              FROM broker_questions
             WHERE deal_id = :deal AND tenant_id = :tenant
             ORDER BY created_at DESC
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )
    return [_row_to_broker_question(r._mapping) for r in rows.fetchall()]


# ════════════════════════════════════════════════════════════════════
#                   Q&A re-ingestion loop (Wave 1 #5)
# ════════════════════════════════════════════════════════════════════
#
# Three endpoints:
#
#   POST   /{deal_id}/broker_responses                — submit broker
#          reply + run the QA Resolver agent.
#   GET    /{deal_id}/qa_history                      — list QA pairs.
#   PATCH  /{deal_id}/broker_responses/{qa_pair_id}/apply
#                                                     — analyst confirms
#          which proposed overrides land in ``deals.field_overrides``.
#
# Trust model (Wave 1 decision — never deviate): the analyst confirms
# every override. The PATCH endpoint NEVER auto-triggers engine runs —
# the analyst hits "Run Model" themselves on the next page so the
# applied overrides flow through the existing engine pipeline.


# ────────────────────────── response shapes ─────────────────────────


class ProposedOverrideOut(BaseModel):
    """One proposed engine-input override emitted by the QA Resolver."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    value: float | str
    rationale: str
    confidence: Literal["high", "medium", "low"]


class BrokerQAPairOut(BaseModel):
    """Persisted Q&A round-trip row — the shape the UI renders + filters on."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    tenant_id: UUID
    broker_question_id: UUID
    analyst_question: str
    broker_response: str
    resolver_verdict: Literal[
        "resolved", "partially_resolved", "still_concerning"
    ] | None = None
    resolver_summary: str | None = None
    proposed_overrides: list[ProposedOverrideOut] = Field(default_factory=list)
    applied_overrides: list[ProposedOverrideOut] | None = None
    audit_note: str | None = None
    created_at: datetime
    updated_at: datetime


class SubmitBrokerResponseBody(BaseModel):
    """POST body for ``submit_broker_response``.

    ``broker_question_id`` must reference an open question on this deal.
    ``broker_response`` is the raw paste from the analyst — we never edit
    it; the agent reads it verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    broker_question_id: UUID
    broker_response: str = Field(min_length=1, max_length=8000)


class ApplyOverridesBody(BaseModel):
    """PATCH body for ``apply_proposed_overrides``.

    ``override_indexes_to_apply`` is the analyst's selection from the
    persisted ``proposed_overrides`` list (0-indexed). Sending an empty
    list is the explicit "skip all" choice — the row's
    ``applied_overrides`` flips from ``None`` (pending decision) to
    ``[]`` (analyst reviewed + skipped) so the UI shows the resolved
    state instead of an indefinite "needs decision" badge.
    """

    model_config = ConfigDict(extra="forbid")

    override_indexes_to_apply: list[int] = Field(default_factory=list)


# ──────────────────────────── row helpers ────────────────────────────


_BROKER_QA_COLUMNS = (
    "id, deal_id, tenant_id, broker_question_id, analyst_question, "
    "broker_response, resolver_verdict, resolver_summary, "
    "proposed_overrides, applied_overrides, audit_note, "
    "created_at, updated_at"
)


def _coerce_json_blob(raw: Any) -> Any:
    """Parse a JSON blob that may be a Python object (Postgres) or string (SQLite).

    Returns ``None`` on bad input — callers default to the empty case.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
    return None


def _coerce_overrides_list(raw: Any) -> list[ProposedOverrideOut]:
    """Normalize a stored proposed/applied overrides blob to the response shape."""
    parsed = _coerce_json_blob(raw)
    if not isinstance(parsed, list):
        return []
    out: list[ProposedOverrideOut] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ProposedOverrideOut.model_validate(item))
        except Exception:  # noqa: BLE001 - best-effort, defensive
            continue
    return out


def _row_to_qa_pair(row_mapping: Any) -> BrokerQAPairOut:
    """SQL row → response shape (SQLite + Postgres tolerant)."""
    applied_raw = row_mapping.get("applied_overrides")
    applied = (
        _coerce_overrides_list(applied_raw) if applied_raw is not None else None
    )
    return BrokerQAPairOut(
        id=UUID(str(row_mapping["id"])),
        deal_id=UUID(str(row_mapping["deal_id"])),
        tenant_id=UUID(str(row_mapping["tenant_id"])),
        broker_question_id=UUID(str(row_mapping["broker_question_id"])),
        analyst_question=row_mapping["analyst_question"],
        broker_response=row_mapping["broker_response"],
        resolver_verdict=row_mapping.get("resolver_verdict"),
        resolver_summary=row_mapping.get("resolver_summary"),
        proposed_overrides=_coerce_overrides_list(
            row_mapping.get("proposed_overrides")
        ),
        applied_overrides=applied,
        audit_note=row_mapping.get("audit_note"),
        created_at=_coerce_dt(row_mapping["created_at"]),
        updated_at=_coerce_dt(row_mapping["updated_at"]),
    )


def _serialize_overrides_for_db(
    overrides: list[Any], *, is_sqlite: bool
) -> Any:
    """Serialize an overrides list for the dialect.

    Postgres takes a parsed list directly when bound through the JSONB
    cast in the INSERT/UPDATE statement; SQLite stores TEXT, so we
    json.dumps. ``overrides`` may be ProposedOverride pydantic models or
    plain dicts.
    """
    payload = []
    for o in overrides:
        if hasattr(o, "model_dump"):
            payload.append(o.model_dump())
        elif isinstance(o, dict):
            payload.append(o)
    return json.dumps(payload) if is_sqlite else payload


# ─────────────────── endpoints ────────────────────


@router.post(
    "/{deal_id}/broker_responses",
    response_model=BrokerQAPairOut,
    status_code=status.HTTP_201_CREATED,
)
async def submit_broker_response(
    deal_id: UUID,
    body: SubmitBrokerResponseBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> BrokerQAPairOut:
    """Submit a broker reply + run the QA Resolver agent.

    Pipeline:

    1. Verify the question exists and is on this tenant + deal.
    2. Snapshot the question text + supporting variance data.
    3. Load the deal's CURRENT engine assumptions so the agent can
       avoid restating overrides that are already in place.
    4. Run the QA Resolver (Sonnet 4.6, allow-listed override paths,
       per-deal budget guard).
    5. Persist the resulting row with verdict + summary + proposed
       overrides + audit_note (``applied_overrides`` stays NULL).
    6. Flip the broker_question state to ``answered`` AND copy the
       raw reply onto the question row (mirrors the existing
       ``broker_questions PATCH`` semantics — keeps the question list
       single source of truth for "did the broker reply yet?").
    """
    await _assert_deal_in_tenant(session, deal_id=deal_id, tenant_id=tenant_id)

    # 1. Verify the question exists + belongs to this tenant + deal.
    qrow = (
        await session.execute(
            text(
                f"""
                SELECT {_BROKER_QUESTION_COLUMNS}
                  FROM broker_questions
                 WHERE id = :id
                   AND deal_id = :deal
                   AND tenant_id = :tenant
                """
            ),
            {
                "id": str(body.broker_question_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    ).first()
    if qrow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"broker question {body.broker_question_id} not found",
        )

    qmap = qrow._mapping
    current_state = qmap["state"]
    # 'pending' is also accepted because the analyst pasting a reply
    # implicitly marks the question as both sent + answered in one move.
    if current_state in {"dismissed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"cannot submit response: question state is "
                f"{current_state!r} (terminal)"
            ),
        )

    # 2. Snapshot the question + supporting data.
    analyst_question = qmap["question_text"]
    supporting_data = {
        "line_item": qmap["line_item"],
        "period_key": qmap["period_key"],
        "variance_pct": float(qmap["variance_pct"]),
        "actual_prior": (
            float(qmap["actual_prior"])
            if qmap.get("actual_prior") is not None
            else None
        ),
        "actual_current": (
            float(qmap["actual_current"])
            if qmap.get("actual_current") is not None
            else None
        ),
        "threshold_pct": float(qmap["threshold_pct"]),
    }

    # 3. Pull the deal's current engine assumptions for prompt context.
    from ..services.engine_runner import _load_engine_inputs

    current_assumptions: dict[str, float] = {}
    try:
        loaded = await _load_engine_inputs(session, str(deal_id))
        for k, v in loaded.items():
            if k.startswith("__"):
                continue
            if isinstance(v, (int, float)):
                current_assumptions[k] = float(v)
    except Exception as exc:  # noqa: BLE001 - best-effort context
        logger.debug(
            "submit_broker_response: _load_engine_inputs failed (%s) — "
            "proceeding with empty assumptions",
            exc,
        )

    # 4. Run the resolver agent. Budget exhaustion → 402.
    from ..agents.qa_resolver import QAResolverInput, run_qa_resolver
    from ..budget import BudgetExceededError

    try:
        result = await run_qa_resolver(
            QAResolverInput(
                deal_id=str(deal_id),
                tenant_id=str(tenant_id),
                broker_question_id=str(body.broker_question_id),
                analyst_question=analyst_question,
                broker_response=body.broker_response,
                supporting_data=supporting_data,
                current_assumptions=current_assumptions,
            )
        )
    except BudgetExceededError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"QA resolver: deal budget exhausted "
                f"(${exc.spent_usd:.2f} of ${exc.budget_usd:.2f}). "
                "Raise the budget in Settings or contact admin."
            ),
        ) from exc

    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"QA resolver failed: {result.error or 'unknown error'}",
        )

    # 5. Persist the row.
    is_sqlite = _is_sqlite(session)
    qa_id = uuid4()
    now = _now()
    proposed_payload = _serialize_overrides_for_db(
        result.proposed_overrides, is_sqlite=is_sqlite
    )

    if is_sqlite:
        await session.execute(
            text(
                """
                INSERT INTO broker_qa_pairs (
                    id, deal_id, tenant_id, broker_question_id,
                    analyst_question, broker_response,
                    resolver_verdict, resolver_summary,
                    proposed_overrides, applied_overrides, audit_note,
                    created_at, updated_at
                ) VALUES (
                    :id, :deal, :tenant, :question_id,
                    :analyst_question, :broker_response,
                    :verdict, :summary,
                    :proposed, NULL, :audit_note,
                    :now, :now
                )
                """
            ),
            {
                "id": str(qa_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
                "question_id": str(body.broker_question_id),
                "analyst_question": analyst_question,
                "broker_response": body.broker_response,
                "verdict": result.verdict,
                "summary": result.summary,
                "proposed": proposed_payload,
                "audit_note": result.audit_note,
                "now": now,
            },
        )
    else:
        await session.execute(
            text(
                """
                INSERT INTO broker_qa_pairs (
                    id, deal_id, tenant_id, broker_question_id,
                    analyst_question, broker_response,
                    resolver_verdict, resolver_summary,
                    proposed_overrides, applied_overrides, audit_note,
                    created_at, updated_at
                ) VALUES (
                    :id, :deal, :tenant, :question_id,
                    :analyst_question, :broker_response,
                    :verdict, :summary,
                    CAST(:proposed AS JSONB), NULL, :audit_note,
                    :now, :now
                )
                """
            ),
            {
                "id": str(qa_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
                "question_id": str(body.broker_question_id),
                "analyst_question": analyst_question,
                "broker_response": body.broker_response,
                "verdict": result.verdict,
                "summary": result.summary,
                "proposed": json.dumps(proposed_payload),
                "audit_note": result.audit_note,
                "now": now,
            },
        )

    # 6. Flip the broker_question to answered + copy the reply onto it.
    # We bypass the state-transition allow-list here intentionally:
    # this endpoint is the canonical "answered" path that knows the
    # reply exists and has been resolver-processed. The "answered"
    # state on the parent question is what the existing
    # ``broker_questions`` list / panel reads, so we keep it the single
    # source of truth.
    await session.execute(
        text(
            """
            UPDATE broker_questions
               SET state = 'answered',
                   broker_response = :response,
                   updated_at = :now
             WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant
            """
        ),
        {
            "response": body.broker_response,
            "now": now,
            "id": str(body.broker_question_id),
            "deal": str(deal_id),
            "tenant": str(tenant_id),
        },
    )

    await session.commit()

    refreshed = (
        await session.execute(
            text(
                f"""
                SELECT {_BROKER_QA_COLUMNS}
                  FROM broker_qa_pairs
                 WHERE id = :id
                """
            ),
            {"id": str(qa_id)},
        )
    ).first()
    assert refreshed is not None
    return _row_to_qa_pair(refreshed._mapping)


@router.get(
    "/{deal_id}/qa_history",
    response_model=list[BrokerQAPairOut],
)
async def get_qa_history(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    state: Annotated[
        str | None,
        Query(
            description=(
                "Optional verdict filter: resolved / partially_resolved / "
                "still_concerning"
            )
        ),
    ] = None,
) -> list[BrokerQAPairOut]:
    """List broker Q&A pairs for a deal, newest first.

    Optional ``?state=`` filters on ``resolver_verdict``. Unknown values
    are ignored (we return data instead of 422 on a typo'd UI query).
    """
    await _assert_deal_in_tenant(session, deal_id=deal_id, tenant_id=tenant_id)

    sql = f"""
        SELECT {_BROKER_QA_COLUMNS}
          FROM broker_qa_pairs
         WHERE deal_id = :deal AND tenant_id = :tenant
    """
    params: dict[str, Any] = {
        "deal": str(deal_id),
        "tenant": str(tenant_id),
    }
    if state in {"resolved", "partially_resolved", "still_concerning"}:
        sql += " AND resolver_verdict = :state"
        params["state"] = state
    sql += " ORDER BY created_at DESC"

    rows = await session.execute(text(sql), params)
    return [_row_to_qa_pair(r._mapping) for r in rows.fetchall()]


@router.patch(
    "/{deal_id}/broker_responses/{qa_pair_id}/apply",
    response_model=BrokerQAPairOut,
)
async def apply_proposed_overrides(
    deal_id: UUID,
    qa_pair_id: UUID,
    body: ApplyOverridesBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> BrokerQAPairOut:
    """Analyst confirms which proposed overrides should land in field_overrides.

    Behavior:

    * Reads the QA pair's stored ``proposed_overrides`` and subsets by
      the indexes the analyst chose.
    * For each chosen override, merges a structured ``FieldOverrideRecord``
      ``{value, note=rationale, overridden_by='broker_qa_resolver',
      overridden_at=<now>}`` into the deal's ``field_overrides`` JSONB.
    * Persists the chosen subset as ``applied_overrides`` on the QA row
      (an empty list is the explicit "skip all" choice — distinct from
      ``None`` which means "pending decision").
    * Does NOT auto-trigger engine runs — the analyst hits "Run Model"
      themselves so the overrides flow through the existing pipeline.

    Returns the updated QA pair.
    """
    await _assert_deal_in_tenant(session, deal_id=deal_id, tenant_id=tenant_id)

    is_sqlite = _is_sqlite(session)

    # Read the QA pair.
    qa_row = (
        await session.execute(
            text(
                f"""
                SELECT {_BROKER_QA_COLUMNS}
                  FROM broker_qa_pairs
                 WHERE id = :id
                   AND deal_id = :deal
                   AND tenant_id = :tenant
                """
            ),
            {
                "id": str(qa_pair_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    ).first()
    if qa_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"qa pair {qa_pair_id} not found",
        )

    proposed = _coerce_overrides_list(qa_row._mapping.get("proposed_overrides"))
    chosen: list[ProposedOverrideOut] = []
    for idx in body.override_indexes_to_apply:
        if 0 <= idx < len(proposed):
            chosen.append(proposed[idx])
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"override index {idx} out of range "
                    f"(0..{len(proposed) - 1})"
                ),
            )

    # Read the current deal.field_overrides and merge in the chosen rows.
    deal_row = (
        await session.execute(
            text(
                "SELECT field_overrides FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    assert deal_row is not None  # _assert_deal_in_tenant already checked
    current_overrides = _coerce_json_blob(deal_row._mapping.get("field_overrides")) or {}
    if not isinstance(current_overrides, dict):
        current_overrides = {}

    now = _now()
    now_iso = now.isoformat()
    for o in chosen:
        current_overrides[o.field_path] = {
            "value": o.value,
            "note": o.rationale,
            "overridden_by": "broker_qa_resolver",
            "overridden_at": now_iso,
        }

    if is_sqlite:
        await session.execute(
            text(
                """
                UPDATE deals
                   SET field_overrides = :overrides,
                       updated_at = :now
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {
                "overrides": json.dumps(current_overrides),
                "now": now,
                "id": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    else:
        await session.execute(
            text(
                """
                UPDATE deals
                   SET field_overrides = CAST(:overrides AS JSONB),
                       updated_at = :now
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {
                "overrides": json.dumps(current_overrides),
                "now": now,
                "id": str(deal_id),
                "tenant": str(tenant_id),
            },
        )

    # Persist the chosen subset on the QA pair. Empty list is the
    # explicit "skip all" choice — distinct from NULL ("pending").
    applied_payload_raw = [o.model_dump() for o in chosen]
    if is_sqlite:
        await session.execute(
            text(
                """
                UPDATE broker_qa_pairs
                   SET applied_overrides = :applied,
                       updated_at = :now
                 WHERE id = :id
                """
            ),
            {
                "applied": json.dumps(applied_payload_raw),
                "now": now,
                "id": str(qa_pair_id),
            },
        )
    else:
        await session.execute(
            text(
                """
                UPDATE broker_qa_pairs
                   SET applied_overrides = CAST(:applied AS JSONB),
                       updated_at = :now
                 WHERE id = :id
                """
            ),
            {
                "applied": json.dumps(applied_payload_raw),
                "now": now,
                "id": str(qa_pair_id),
            },
        )

    await session.commit()

    refreshed = (
        await session.execute(
            text(
                f"""
                SELECT {_BROKER_QA_COLUMNS}
                  FROM broker_qa_pairs
                 WHERE id = :id
                """
            ),
            {"id": str(qa_pair_id)},
        )
    ).first()
    assert refreshed is not None
    logger.info(
        "qa.apply: deal=%s qa_pair=%s applied=%d/%d",
        deal_id,
        qa_pair_id,
        len(chosen),
        len(proposed),
    )
    return _row_to_qa_pair(refreshed._mapping)


# ════════════════════════════════════════════════════════════════════
# Wave 2 P2.8 — Pricing sensitivity grid + max-price + LOI draft
# ════════════════════════════════════════════════════════════════════
#
# Three deterministic, side-effect-free endpoints. None of them touches
# the deal's persisted state — they run the existing engine chain in
# memory, flex the parameters, and return data. The LOI draft is a
# pure template fill; no LLM call, no document insert.
#
# Tenant-scoped via ``Depends(get_tenant_id)`` for parity with the rest
# of the file.


class _SensitivityRequest(BaseModel):
    """POST body for ``/pricing/sensitivity``."""

    model_config = ConfigDict(extra="forbid")

    target_irr: float = Field(default=0.15, ge=-0.5, le=2.0)
    target_em: float = Field(default=1.8, ge=0.0, le=20.0)
    cap_axis: list[float] | None = Field(
        default=None,
        description=(
            "Optional explicit exit-cap rates (absolute, e.g. [0.06, 0.07, "
            "0.08]). When omitted the default ±100bp window anchored at "
            "the deal's base exit cap is used."
        ),
    )
    noi_axis: list[float] | None = Field(
        default=None,
        description=(
            "Optional explicit NOI multipliers (e.g. [0.9, 1.0, 1.1]). "
            "When omitted the default 0.85–1.15 window is used."
        ),
    )


class _SensitivityCellOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exit_cap_pct: float
    noi_multiplier: float
    levered_irr: float
    equity_multiple: float
    going_in_cap_rate: float
    dscr_y1: float
    breaches_dscr_floor: bool


class _SensitivityGridOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    base_exit_cap_pct: float
    base_stabilized_noi: float
    cells: list[_SensitivityCellOut]
    breakeven_exit_cap_pct: float | None
    breakeven_noi_multiplier: float | None


class _MaxPriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_irr: float = Field(default=0.15, ge=-0.5, le=2.0)
    target_em: float = Field(default=1.8, ge=0.0, le=20.0)


class _MaxPriceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    target_irr: float
    target_em: float
    max_price_for_irr: float
    max_price_for_em: float
    binding_constraint: Literal["irr", "em", "both"]
    final_price_per_key: float
    iters: int


class _LOIRequest(BaseModel):
    """POST body for ``/pricing/loi``. Every field is optional; the
    engine fills sensible defaults so the analyst's one-click path
    produces a copy-paste-ready draft.
    """

    model_config = ConfigDict(extra="forbid")

    target_irr: float = Field(default=0.15, ge=-0.5, le=2.0)
    target_em: float = Field(default=1.8, ge=0.0, le=20.0)
    buyer: str | None = Field(default=None, max_length=200)
    seller: str | None = Field(default=None, max_length=200)
    earnest_money_pct: float = Field(default=0.01, ge=0.0, le=0.10)
    due_diligence_days: int = Field(default=30, ge=1, le=180)
    closing_days_from_pa: int = Field(default=60, ge=1, le=365)
    financing_contingency: str = Field(
        default="60 days from PA execution", max_length=200
    )
    exclusivity_days: int = Field(default=21, ge=0, le=180)
    representation: str | None = Field(default=None, max_length=200)
    valid_until: str = Field(
        default="10 business days from issuance", max_length=200
    )
    contingencies: list[str] | None = Field(default=None, max_length=20)
    proposed_price_override: float | None = Field(
        default=None,
        ge=0,
        description=(
            "When omitted, the draft uses ``max_price_for_irr``. Caller "
            "can pin a specific price (e.g. negotiation-room below the "
            "max) without re-solving."
        ),
    )


class _LOIOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    buyer: str
    seller: str
    asset_name: str
    asset_address: str
    rooms: int
    proposed_price: float
    proposed_price_per_key: float
    earnest_money_pct: float
    deposit_at_pa: float
    due_diligence_days: int
    closing_days_from_pa: int
    financing_contingency: str
    exclusivity_days: int
    representation: str
    valid_until: str
    contingencies: list[str]
    rendered_markdown: str


async def _build_returns_input_for_deal(
    session: AsyncSession,
    *,
    deal_id: UUID,
    tenant_id: UUID,
) -> Any:
    """Run the engine chain in-memory and return the materialized
    ``ReturnsEngineInputExt``.

    The pricing endpoints all hang off the *base* returns input — the
    sensitivity grid flexes it, the price solver bisects on it, the LOI
    consumes the solver's output. Rather than persist the run, we walk
    revenue → fb → expense → capital → debt and stop just before
    returns — exactly what ``_build_input_for("returns", ...)`` needs.

    No DB writes. No engine_outputs row. This is the safety guarantee
    we promise the UI: viewing the pricing tab cannot mutate the deal.
    """
    from ..services.engine_runner import (
        ENGINE_REGISTRY,
        _build_input_for,
        _load_engine_inputs,
    )

    base = await _load_engine_inputs(session, str(deal_id))
    accumulated: dict[str, Any] = {}
    # Walk the chain up through capital + debt so the returns input
    # builder has the loan amount + debt service + equity it needs.
    for engine_name in ("revenue", "fb", "expense", "capital", "debt"):
        engine_input = _build_input_for(
            engine_name, str(deal_id), base, accumulated
        )
        engine = ENGINE_REGISTRY[engine_name]()
        accumulated[engine_name] = engine.run(engine_input)

    return _build_input_for("returns", str(deal_id), base, accumulated)


async def _load_asset_facts(
    session: AsyncSession, *, deal_id: UUID, tenant_id: UUID
) -> tuple[str, str, int]:
    """Pull ``(asset_name, asset_address, rooms)`` for the LOI body.

    Falls back to placeholders when a deal row is incomplete (defensive
    — the LOI is meant to be edited anyway, and a missing field should
    show as ``[TBD]`` not 500).
    """
    row = (
        await session.execute(
            text(
                "SELECT name, city, keys FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        return ("[Asset TBD]", "[Address TBD]", 0)
    m = row._mapping
    name = (m.get("name") or "[Asset TBD]") + ""
    city = m.get("city") or "[City TBD]"
    address = f"{city}"  # full street address isn't on the deals row yet
    rooms = int(m.get("keys") or 0)
    return (name, address, rooms)


@router.post(
    "/{deal_id}/pricing/sensitivity",
    response_model=_SensitivityGridOut,
)
async def get_pricing_sensitivity(
    deal_id: UUID,
    body: _SensitivityRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> _SensitivityGridOut:
    """Compute the 5x5 exit-cap × NOI-multiplier sensitivity grid.

    Read-only: walks the engine chain in memory, flexes per cell, never
    touches ``engine_outputs`` or ``deals``. Tenant-scoped 404 on cross-
    tenant deal ids.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    from ..engines.pricing_sensitivity import run_sensitivity_grid

    base_input = await _build_returns_input_for_deal(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    grid = run_sensitivity_grid(
        base_input,
        target_irr=body.target_irr,
        cap_axis=body.cap_axis,
        noi_axis=body.noi_axis,
    )
    return _SensitivityGridOut(
        deal_id=deal_id,
        base_exit_cap_pct=grid.base_exit_cap_pct,
        base_stabilized_noi=grid.base_stabilized_noi,
        cells=[
            _SensitivityCellOut(
                exit_cap_pct=c.exit_cap_pct,
                noi_multiplier=c.noi_multiplier,
                levered_irr=c.levered_irr,
                equity_multiple=c.equity_multiple,
                going_in_cap_rate=c.going_in_cap_rate,
                dscr_y1=c.dscr_y1,
                breaches_dscr_floor=c.breaches_dscr_floor,
            )
            for c in grid.cells
        ],
        breakeven_exit_cap_pct=grid.breakeven_exit_cap_pct,
        breakeven_noi_multiplier=grid.breakeven_noi_multiplier,
    )


@router.post(
    "/{deal_id}/pricing/max-price",
    response_model=_MaxPriceOut,
)
async def get_pricing_max_price(
    deal_id: UUID,
    body: _MaxPriceRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> _MaxPriceOut:
    """Bisect-search the max purchase price hitting ``target_irr`` AND
    ``target_em``. Returns both prices + binding-constraint chip.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    from ..engines.price_solver import solve_max_price

    base_input = await _build_returns_input_for_deal(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    _name, _address, rooms = await _load_asset_facts(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    res = solve_max_price(
        base_input,
        target_irr=body.target_irr,
        target_em=body.target_em,
        rooms=rooms or None,
    )
    return _MaxPriceOut(
        deal_id=deal_id,
        target_irr=res.target_irr,
        target_em=res.target_em,
        max_price_for_irr=res.max_price_for_irr,
        max_price_for_em=res.max_price_for_em,
        binding_constraint=res.binding_constraint,
        final_price_per_key=res.final_price_per_key,
        iters=res.iters,
    )


@router.post(
    "/{deal_id}/pricing/loi",
    response_model=_LOIOut,
)
async def get_pricing_loi(
    deal_id: UUID,
    body: _LOIRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> _LOIOut:
    """Build an LOI draft using the max-price-for-IRR scalar.

    The draft is a copy-paste artifact — this endpoint NEVER inserts a
    document or persists any state; the analyst clicks Save in the UI
    to land it in the documents table.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    from ..engines.loi_generator import draft_loi
    from ..engines.price_solver import solve_max_price

    base_input = await _build_returns_input_for_deal(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    asset_name, asset_address, rooms = await _load_asset_facts(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    mpr = solve_max_price(
        base_input,
        target_irr=body.target_irr,
        target_em=body.target_em,
        rooms=rooms or None,
    )
    draft = draft_loi(
        asset_name=asset_name,
        asset_address=asset_address,
        rooms=rooms,
        max_price_result=mpr,
        buyer=body.buyer,
        seller=body.seller,
        earnest_money_pct=body.earnest_money_pct,
        due_diligence_days=body.due_diligence_days,
        closing_days_from_pa=body.closing_days_from_pa,
        financing_contingency=body.financing_contingency,
        exclusivity_days=body.exclusivity_days,
        representation=body.representation,
        valid_until=body.valid_until,
        contingencies=body.contingencies,
        proposed_price_override=body.proposed_price_override,
    )
    return _LOIOut(
        deal_id=deal_id,
        buyer=draft.buyer,
        seller=draft.seller,
        asset_name=draft.asset_name,
        asset_address=draft.asset_address,
        rooms=draft.rooms,
        proposed_price=draft.proposed_price,
        proposed_price_per_key=draft.proposed_price_per_key,
        earnest_money_pct=draft.earnest_money_pct,
        deposit_at_pa=draft.deposit_at_pa,
        due_diligence_days=draft.due_diligence_days,
        closing_days_from_pa=draft.closing_days_from_pa,
        financing_contingency=draft.financing_contingency,
        exclusivity_days=draft.exclusivity_days,
        representation=draft.representation,
        valid_until=draft.valid_until,
        contingencies=draft.contingencies,
        rendered_markdown=draft.rendered_markdown,
    )
