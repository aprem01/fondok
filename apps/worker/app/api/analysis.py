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
