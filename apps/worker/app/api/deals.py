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
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, status
from fastapi.responses import Response, StreamingResponse
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


# UUIDv5 namespace for Clerk organization IDs. Generated once with
# ``uuid.uuid5(uuid.NAMESPACE_URL, "fondok.app/clerk/org")`` — pinned
# here so a namespace change is a deliberate DB migration, not a
# silent bug that reshuffles every tenant's data. Never rotate this
# without a plan to migrate `tenant_id` columns across the schema.
_CLERK_ORG_UUID_NAMESPACE = UUID("6c7bd9b0-3c8a-5a24-9b2a-3c4d4d0f8e9a")


def _coerce_tenant_id(raw: str) -> UUID | None:
    """Map an X-Tenant-Id header value to a UUID.

    Sam QA 2026-07-02: the frontend passes Clerk's ``org_XXXXX...``
    organization id verbatim, but the worker's tenant column is UUID.
    Prior behavior rejected the header and fell back to
    ``DEFAULT_TENANT_ID``, so every real user's data landed on the
    catch-all default tenant — the extraction cache never hit
    (tenant-scoped), the /admin/cost aggregation was misleading, and
    cross-tenant isolation was effectively unenforced.

    Accepted formats (in priority order):
      1. Already a valid UUID string → parsed as-is.
      2. Clerk-style ``org_...`` (or ``user_...``) prefix → hashed
         to a deterministic UUIDv5 via ``_CLERK_ORG_UUID_NAMESPACE``.
         Same Clerk org id always produces the same UUID, so cached
         extractions + audit trails stay linked to the tenant across
         requests without any DB lookup.
      3. Anything else → ``None`` (caller falls back to default).
    """
    if not raw:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        return UUID(stripped)
    except ValueError:
        pass
    if stripped.startswith(("org_", "user_", "acc_")):
        return uuid5(_CLERK_ORG_UUID_NAMESPACE, stripped)
    return None


async def get_tenant_id(
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> UUID:
    """Resolve the tenant for this request.

    Reads the ``X-Tenant-Id`` header set by the web app's `lib/api.ts`
    (mirrors the active Clerk Organization id). Uses ``_coerce_tenant_id``
    to accept either a raw UUID (server-side callers, tests, curl) or
    a Clerk ``org_...`` id (real logged-in browser traffic). When the
    header is missing or in an unrecognized shape we fall back to
    ``settings.DEFAULT_TENANT_ID`` so the unauthenticated demo persona
    keeps working end-to-end.
    """
    settings = get_settings()
    if x_tenant_id:
        coerced = _coerce_tenant_id(x_tenant_id)
        if coerced is not None:
            return coerced
        logger.warning(
            "get_tenant_id: unrecognized X-Tenant-Id header %r — using default",
            x_tenant_id,
        )
    return UUID(settings.DEFAULT_TENANT_ID)


# ─────────────────────────── request bodies ───────────────────────────


class FieldOverrideRecord(BaseModel):
    """Structured analyst override with a mandatory justification note.

    Roadmap item #6 (June 2026 call) — Eshan's exact ask: "you should
    have a note when you hard-code something." The note is required at
    the API layer so the IC review trail always has the reason behind
    every analyst override.

    Backward compatibility: legacy ``field_overrides`` rows in the DB
    use the flat shape ``{path: value}``. The engine_runner loader
    auto-migrates them on read (value preserved, note set to "",
    overridden_by stamped as "legacy"). Going forward, the API accepts
    only the structured shape so new overrides cannot skip the note.
    """

    model_config = ConfigDict(extra="forbid")

    value: float | str | int | bool
    note: str = Field(min_length=1, max_length=2000)
    overridden_by: str | None = None  # stamped server-side from auth
    overridden_at: datetime | None = None  # stamped server-side


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
    # Sourcing channel for pipeline analytics (Sam's v2 ask):
    # broker / lender / franchisor / operator / capital_partner / direct.
    sourcing_channel: str | None = Field(default=None, max_length=40)
    # Wave 3 W3.5 — analyst-declared target levered IRR. The Pipeline
    # view uses this to badge each deal as meeting / missing target.
    # NULL means "no opinion yet" — UI shows a dash and the summary
    # aggregates skip the deal in the meets-target tally.
    target_irr: float | None = Field(default=None, ge=-0.5, le=2.0)


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
    sourcing_channel: str | None = Field(default=None, max_length=40)
    # Wave 3 W3.5 — patch the per-deal target levered IRR. Pass null to
    # clear it ("no opinion") or a fraction in [-0.5, 2.0] (e.g. 0.18 =
    # 18% IRR threshold).
    target_irr: float | None = Field(default=None, ge=-0.5, le=2.0)
    # Per-field analyst overrides (canonical extractor field path →
    # primitive value). When present, this dict REPLACES the deal's
    # current overrides — clients send the full merged map. Engines pick
    # these up via the OM-actuals loader on next run.
    field_overrides: dict[str, Any] | None = None


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
    sourcing_channel: str | None = None
    # Wave 3 W3.5 — analyst-declared target levered IRR (fraction).
    # NULL when the analyst hasn't set a threshold yet. The Pipeline
    # view's "deals meeting target IRR" KPI ignores deals with no
    # target rather than counting them as misses.
    target_irr: float | None = None
    # Per-field analyst overrides — keyed by extractor field path (e.g.
    # ``property_overview.year_built``) → either a scalar (legacy) or
    # a ``FieldOverrideRecord``-shaped dict ``{value, note, overridden_by,
    # overridden_at}``. New overrides land in the structured shape; the
    # engine_runner reads either via ``_normalize_override_shape``.
    field_overrides: dict[str, Any] = Field(default_factory=dict)
    # Deal lifecycle state for the Onboarding → Validation separation
    # Eshan asked for. ONBOARDING (default) → VALIDATING → READY.
    state: str = "ONBOARDING"
    validation_started_at: datetime | None = None
    validation_complete_at: datetime | None = None
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


class AssumptionSourcesResponse(BaseModel):
    """Per-assumption provenance map.

    Maps each canonical assumption key (``starting_adr``,
    ``exit_cap_rate``, ``revpar_growth`` etc.) to the source label
    that produced the current value: ``seed`` (Kimpton default),
    ``deal_row`` (deals table), ``t12_actual`` (extracted T-12),
    ``cbre_horizons``, ``pnl_benchmark``, ``om_comps``, or
    ``analyst_override``. The web app surfaces these as small badges
    on the Investment / Returns / Overview tabs so reviewers can see
    which numbers are grounded vs which are still defaults.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    sources: dict[str, str] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)
    # Per-assumption source document ids (Sam P3 doc-to-engine
    # traceability). Maps each canonical key whose source label points
    # at an uploaded doc (t12_actual / cbre_horizons / pnl_benchmark /
    # om_comps / om_broker) to the document_id that most likely
    # contributed the value. Seed / deal_row / analyst_override keys
    # are omitted. The web UI uses these for "click NOI → jump to the
    # T-12 row" deep links.
    source_documents: dict[str, str] = Field(default_factory=dict)


class GateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    gate: str
    accepted: bool = True
    next_state: str | None = None


# ─────────────────────────── pipeline view ───────────────────────────
# Wave 3 W3.5 — multi-deal Pipeline view. One row per deal, enriched
# with the deal's LATEST engine output snapshot (returns / debt /
# capital). Analysts open this view dozens of times a day, so the
# endpoint pre-computes summary KPIs and supports server-side
# sort + filter + pagination.


class PipelineDealRow(BaseModel):
    """Single row in the pipeline table.

    Numbers carry their natural units: prices in USD, IRRs and cap
    rates as fractions (0.18 = 18%), equity multiples as raw ratios.
    Any field that requires an engine run we don't yet have is NULL —
    the UI dashes those cells rather than rendering zeroes.
    """

    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    name: str
    state: str  # ONBOARDING / VALIDATING / READY
    status: str  # Draft / Active / Archived (legacy status column)
    city: str | None = None
    brand: str | None = None
    deal_stage: str | None = None
    keys: int | None = None
    purchase_price: float | None = None
    price_per_key: float | None = None
    noi_y1: float | None = None
    noi_stabilized: float | None = None
    exit_cap_rate: float | None = None
    levered_irr: float | None = None
    equity_multiple: float | None = None
    dscr_y1: float | None = None
    document_count: int = 0
    last_engine_run_at: datetime | None = None
    last_activity_at: datetime
    pip_total_usd: float | None = None  # convenience pull from capex_plan
    target_irr: float | None = None
    target_irr_met: bool | None = None  # NULL when no target on the deal


class PipelineSummary(BaseModel):
    """Portfolio-level rollup over the rows in this response.

    All percentiles are computed over the deals that have a usable
    ``levered_irr`` (post-engine-run). ``deals_meeting_target_irr``
    counts only deals whose ``target_irr`` is set AND whose
    ``levered_irr`` meets/exceeds the threshold — deals with no target
    are excluded from the tally entirely so a sparse pipeline doesn't
    inflate the miss rate.
    """

    model_config = ConfigDict(extra="forbid")

    deal_count: int
    median_irr: float | None = None
    p25_irr: float | None = None
    p75_irr: float | None = None
    median_em: float | None = None
    median_per_key: float | None = None
    median_cap_rate: float | None = None
    deals_meeting_target_irr: int = 0
    deals_with_target_irr: int = 0
    deals_by_state: dict[str, int] = Field(default_factory=dict)


class PipelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deals: list[PipelineDealRow] = Field(default_factory=list)
    summary: PipelineSummary
    total_count: int  # deals matching filters before pagination
    limit: int
    offset: int


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


def _coerce_overrides(value: Any) -> dict[str, Any]:
    """Normalize JSONB column reads — Postgres may hand us a parsed
    dict or a raw JSON string depending on driver/version. Anything
    unexpected falls back to an empty map so the API never blows up
    on a malformed row.
    """
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _coerce_dt_optional(value: Any) -> datetime | None:
    """Like ``_coerce_dt`` but returns None for null/missing values."""
    if value is None:
        return None
    try:
        return _coerce_dt(value)
    except Exception:
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
        sourcing_channel=row.get("sourcing_channel"),
        target_irr=_coerce_float(row.get("target_irr")),
        field_overrides=_coerce_overrides(row.get("field_overrides")),
        state=row.get("state") or "ONBOARDING",
        validation_started_at=_coerce_dt_optional(row.get("validation_started_at")),
        validation_complete_at=_coerce_dt_optional(row.get("validation_complete_at")),
        created_at=_coerce_dt(row.get("created_at")),
        updated_at=_coerce_dt(row.get("updated_at")),
    )


_DEAL_COLUMNS = (
    "id, tenant_id, name, city, keys, service, status, deal_stage, "
    "risk, ai_confidence, return_profile, brand, positioning, "
    "purchase_price, sourcing_channel, target_irr, field_overrides, "
    "state, validation_started_at, validation_complete_at, "
    "created_at, updated_at"
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


async def _assert_deal_belongs_to_tenant(
    session: AsyncSession,
    *,
    deal_id: str | UUID,
    tenant_id: str | UUID,
) -> None:
    """Raise 404 if ``deal_id`` does not belong to ``tenant_id``.

    Defense-in-depth check used by endpoints whose primary read path
    doesn't already filter by tenant (in-process caches, SSE streams,
    cost rollups). Returning 404 (not 403) is intentional — leaks no
    information about whether the deal exists on another tenant.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT 1
                  FROM deals
                 WHERE id = :id AND tenant_id = :tenant
                 LIMIT 1
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
        "sourcing_channel": body.sourcing_channel,
        "target_irr": body.target_irr,
        "created_at": now,
        "updated_at": now,
    }

    await session.execute(
        text(
            """
            INSERT INTO deals (
                id, tenant_id, name, city, keys, service, status,
                deal_stage, risk, ai_confidence, return_profile,
                brand, positioning, purchase_price, sourcing_channel,
                target_irr, created_at, updated_at
            ) VALUES (
                :id, :tenant, :name, :city, :keys, :service, :status,
                :deal_stage, :risk, :ai_confidence, :return_profile,
                :brand, :positioning, :purchase_price, :sourcing_channel,
                :target_irr, :created_at, :updated_at
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
    # Wave 3 W3.2 — every deal gets a Base scenario at create time so
    # the UI's scenario selector always has a default pill to highlight
    # and engine runs without a scenario_id stay byte-identical to a
    # run against the base.
    try:
        from .scenarios import create_base_scenario_for_deal

        await create_base_scenario_for_deal(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id_str,
        )
    except Exception as exc:  # noqa: BLE001 — never block deal create
        logger.warning(
            "deals.create: base-scenario insert failed for deal=%s: %s",
            deal_id, exc,
        )
    await session.commit()
    # Wave 3 W3.5 — invalidate the pipeline-view cache so a freshly
    # created deal shows up immediately on the analyst's next visit.
    from ..services.pipeline import invalidate as _pipeline_invalidate
    _pipeline_invalidate(tenant_id)

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
        sourcing_channel=body.sourcing_channel,
        target_irr=body.target_irr,
        created_at=now,
        updated_at=now,
    )


@router.get("/pipeline", response_model=PipelineResponse)
async def get_pipeline(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    sort: str = "last_activity_desc",
    state: str | None = None,
    deal_stage: str | None = None,
    min_irr: float | None = None,
    max_irr: float | None = None,
    min_per_key: float | None = None,
    max_per_key: float | None = None,
    target_met: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> PipelineResponse:
    """Multi-deal pipeline view (Wave 3 W3.5).

    Returns one row per non-archived deal in the current tenant
    enriched with the LATEST engine output snapshot per engine
    (returns / debt / capital / expense). The endpoint is the data
    backing for the ``/pipeline`` page in the web app — analysts open
    this view dozens of times a day, so the per-tenant snapshot is
    cached for 60 seconds and invalidated by any engine run or deal
    mutation (see ``services.pipeline.invalidate``).

    Filtering & sorting both happen in Python after a single tenant-
    scoped pull (deals + window-function-latest engine rows + grouped
    document counts). Pagination caps at 200 rows so an unbounded
    fetch can never overrun the response budget.
    """
    from ..services.pipeline import (
        DEFAULT_SORT,
        SORT_KEYS,
        apply_filters,
        apply_sort,
        build_pipeline_snapshot,
        build_summary,
    )

    # Clamp pagination so a forgotten limit query-arg can't bring back
    # the entire firm's portfolio in one call.
    limit = max(1, min(int(limit), 200))
    offset = max(0, int(offset))
    # Reject unknown sort tokens loudly — silently downgrading to the
    # default would confuse the analyst (the column header looks sorted
    # but the rows aren't).
    if sort not in SORT_KEYS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unknown sort {sort!r} — expected one of "
                f"{sorted(SORT_KEYS.keys())} or '{DEFAULT_SORT}'"
            ),
        )

    snapshot = await build_pipeline_snapshot(session, tenant_id=tenant_id)
    filtered = apply_filters(
        snapshot,
        state=state,
        min_irr=min_irr,
        max_irr=max_irr,
        min_per_key=min_per_key,
        max_per_key=max_per_key,
        deal_stage=deal_stage,
        target_met=target_met,
    )
    summary_dict = build_summary(filtered)
    sorted_rows = apply_sort(filtered, sort)
    page = sorted_rows[offset : offset + limit]

    deal_rows: list[PipelineDealRow] = []
    for row in page:
        deal_rows.append(
            PipelineDealRow(
                deal_id=UUID(row["deal_id"]),
                name=row["name"],
                state=row["state"],
                status=row["status"],
                city=row.get("city"),
                brand=row.get("brand"),
                deal_stage=row.get("deal_stage"),
                keys=row.get("keys"),
                purchase_price=row.get("purchase_price"),
                price_per_key=row.get("price_per_key"),
                noi_y1=row.get("noi_y1"),
                noi_stabilized=row.get("noi_stabilized"),
                exit_cap_rate=row.get("exit_cap_rate"),
                levered_irr=row.get("levered_irr"),
                equity_multiple=row.get("equity_multiple"),
                dscr_y1=row.get("dscr_y1"),
                document_count=row.get("document_count", 0),
                last_engine_run_at=row.get("last_engine_run_at"),
                last_activity_at=row["last_activity_at"],
                pip_total_usd=row.get("pip_total_usd"),
                target_irr=row.get("target_irr"),
                target_irr_met=row.get("target_irr_met"),
            )
        )

    return PipelineResponse(
        deals=deal_rows,
        summary=PipelineSummary(**summary_dict),
        total_count=len(filtered),
        limit=limit,
        offset=offset,
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
    # Postgres stores `field_overrides` as JSONB and needs an explicit
    # cast; SQLite (dev/test) stores it as TEXT and accepts a JSON
    # string directly. Detect the dialect off the bound session.
    is_sqlite = (session.bind is not None
                 and session.bind.dialect.name == "sqlite")
    for field, value in changes.items():
        if field == "field_overrides":
            # Drivers vary on whether they auto-serialize dicts. Force
            # json.dumps so we never end up with a stringified Python repr.
            if is_sqlite:
                set_clauses.append(f"{field} = :{field}")
            else:
                set_clauses.append(f"{field} = CAST(:{field} AS JSONB)")
            params[field] = json.dumps(value or {})
        else:
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

    # Wave 4 W4.3 — when ``field_overrides`` is in the changeset, audit
    # as an ``override.set`` event with a one-line diff per changed field
    # so the Activity Feed renders "exit_cap_rate: 0.07 → 0.075" instead
    # of an opaque "deal.updated". Other patches keep the legacy shape.
    prior_overrides = _coerce_overrides(existing._mapping.get("field_overrides"))
    if "field_overrides" in changes:
        new_overrides = changes.get("field_overrides") or {}
        # Both sides flattened to ``{path: value}`` for a clean diff.
        before_flat = {
            k: (v.get("value") if isinstance(v, dict) else v)
            for k, v in prior_overrides.items()
        }
        after_flat = {
            k: (v.get("value") if isinstance(v, dict) else v)
            for k, v in new_overrides.items()
        }
        await log_audit(
            session,
            tenant_id=tenant_id_str,
            actor_id="system",
            action="override.set",
            resource_type="override",
            resource_id=str(deal_id),
            input_payload={"changes": changes},
            output_payload={"field_overrides": new_overrides},
            before=before_flat,
            after=after_flat,
            tags=["override", "wave1"],
            metadata={"deal_id": str(deal_id)},
        )
        # Still emit the legacy deal.updated trail so existing dashboards
        # that filter on action='deal.updated' keep firing.
        await _write_audit(
            session,
            tenant_id=tenant_id_str,
            deal_id=str(deal_id),
            actor_id="system",
            action="deal.updated",
            payload={"changes": list(changes.keys())},
        )
    else:
        await _write_audit(
            session,
            tenant_id=tenant_id_str,
            deal_id=str(deal_id),
            actor_id="system",
            action="deal.updated",
            payload={"changes": changes},
        )
    await session.commit()
    # Wave 3 W3.5 — pipeline-view cache invalidation.
    from ..services.pipeline import invalidate as _pipeline_invalidate
    _pipeline_invalidate(tenant_id)

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


@router.delete(
    "/{deal_id}/hard",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def hard_delete_deal(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> Response:
    """Hard-delete a deal and every related row. Irreversible.

    Cascade order (FK-safest order — children before parents):
      * broker_questions / broker_qa_pairs
      * scenarios
      * engine_outputs
      * extraction_results (via documents)
      * documents
      * audit_log entries scoped to this deal
      * deals row itself

    Tenant-isolated: cross-tenant guesses 404. Audit-logged at the
    tenant level (not deal — the deal_id stops existing mid-call)
    so a future review can find "tenant X deleted deal Y at T".
    """
    tenant_id_str = str(tenant_id)
    deal_id_str = str(deal_id)
    existing = (
        await session.execute(
            text(
                f"SELECT {_DEAL_COLUMNS} FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": deal_id_str, "tenant": tenant_id_str},
        )
    ).first()
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )
    deal_name = existing._mapping.get("name")

    # Cascade. Each table either has FK ON DELETE CASCADE OR we
    # delete explicitly. Doing it explicitly is safer — Postgres +
    # SQLite agree on order without needing FK plumbing to be perfect.
    cascade_sql = [
        # Children of documents (extraction_results FK -> documents.id).
        (
            "DELETE FROM extraction_results "
            "WHERE document_id IN ("
            "  SELECT id FROM documents WHERE deal_id = :deal AND tenant_id = :tenant"
            ")",
            "extraction_results",
        ),
        # Direct-children of deal.
        ("DELETE FROM broker_questions WHERE deal_id = :deal AND tenant_id = :tenant", "broker_questions"),
        ("DELETE FROM broker_qa_pairs WHERE deal_id = :deal AND tenant_id = :tenant", "broker_qa_pairs"),
        ("DELETE FROM scenarios WHERE deal_id = :deal AND tenant_id = :tenant", "scenarios"),
        ("DELETE FROM engine_outputs WHERE deal_id = :deal AND tenant_id = :tenant", "engine_outputs"),
        ("DELETE FROM documents WHERE deal_id = :deal AND tenant_id = :tenant", "documents"),
        # audit_log: scoped narrowly to this deal so we don't delete
        # tenant-wide audit history. Keep these even after deal delete
        # for compliance forensics — but the original Wave 1 audit
        # schema doesn't carry deal_id on every row, so this is a
        # best-effort match on the deal_id column when present.
        # NB: we KEEP audit entries that mention the deal_id in
        # metadata/payload — those are usually scoped to actor/system
        # actions and survive deletion for compliance.
    ]

    deleted_counts: dict[str, int] = {}
    bind = {"deal": deal_id_str, "tenant": tenant_id_str}
    for sql, label in cascade_sql:
        result = await session.execute(text(sql), bind)
        deleted_counts[label] = result.rowcount or 0

    # The deal itself.
    await session.execute(
        text("DELETE FROM deals WHERE id = :id AND tenant_id = :tenant"),
        {"id": deal_id_str, "tenant": tenant_id_str},
    )
    deleted_counts["deals"] = 1

    # Audit BEFORE commit so a partial cascade still leaves a trail.
    try:
        from ..audit import log_audit

        await log_audit(
            session,
            tenant_id=tenant_id_str,
            deal_id=deal_id_str,
            actor_id=None,
            action="deal.hard_deleted",
            resource_type="deal",
            resource_id=deal_id_str,
            severity="warning",
            metadata={
                "name": deal_name,
                "cascade_counts": deleted_counts,
            },
        )
    except Exception:
        logger.exception(
            "hard_delete_deal: audit log failed for deal=%s", deal_id
        )

    await session.commit()

    from ..services.pipeline import invalidate as _pipeline_invalidate
    _pipeline_invalidate(tenant_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


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
    # Wave 3 W3.5 — pipeline-view cache invalidation.
    from ..services.pipeline import invalidate as _pipeline_invalidate
    _pipeline_invalidate(tenant_id)

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


@router.get("/{deal_id}/assumption_sources", response_model=AssumptionSourcesResponse)
async def get_assumption_sources(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> AssumptionSourcesResponse:
    """Return the provenance map of underwriting assumptions.

    Calls into ``engine_runner._load_engine_inputs`` to resolve the
    current sources (seed / deal_row / t12_actual / cbre_horizons /
    pnl_benchmark / om_comps / analyst_override) for each key, plus
    the resolved value for each.

    The web app uses this on Investment / Returns / Overview to
    badge each headline number with its source so reviewers can see
    at a glance which assumptions are grounded in extracted data vs
    falling back to Kimpton seeds.
    """
    # Verify deal exists + tenant authorization.
    row = (
        await session.execute(
            text(
                "SELECT id FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )

    # Lazy import to avoid a top-level circular: engine_runner pulls
    # in models from this module via the dossier path.
    from ..services.engine_runner import _load_engine_inputs

    base = await _load_engine_inputs(session, str(deal_id))
    sources = base.pop("__sources__", {})
    # Strip the t12_*_actuals dicts and other non-scalar fields from
    # `values` — they're internal plumbing, not assumptions the UI
    # would badge directly.
    values: dict[str, Any] = {}
    for k, v in base.items():
        if k.startswith("__"):
            continue
        if isinstance(v, (int, float, str, bool)) or v is None:
            values[k] = v
    # Only return entries that have a tracked source AND a value the
    # UI can read.
    sources_filtered = {k: s for k, s in sources.items() if k in values}

    # Per-document provenance lookup. Best-effort — failures degrade
    # to an empty map (the badge UI just doesn't render the link icon).
    from ..services.engine_runner import _load_source_documents
    try:
        source_documents = await _load_source_documents(
            session, deal_id=str(deal_id), sources=sources_filtered,
        )
    except Exception:
        source_documents = {}

    return AssumptionSourcesResponse(
        id=deal_id,
        sources=sources_filtered,
        values=values,
        source_documents=source_documents,
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
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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


class TransitionBody(BaseModel):
    """POST body for /{deal_id}/transition.

    Drives the deal lifecycle: ONBOARDING → VALIDATING → READY. Notes
    land in the audit log so the IC review trail captures who advanced
    the deal at each gate and why.
    """

    model_config = ConfigDict(extra="forbid")

    next_state: Literal["ONBOARDING", "VALIDATING", "READY"]
    notes: str | None = None


_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "ONBOARDING": {"VALIDATING"},
    "VALIDATING": {"READY", "ONBOARDING"},  # can roll back to add more docs
    "READY": {"VALIDATING"},  # can re-open for revisions
}


@router.post("/{deal_id}/transition", response_model=DealRecord)
async def transition_deal_state(
    deal_id: UUID,
    body: TransitionBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> DealRecord:
    """Advance a deal through the lifecycle state machine.

    Roadmap item #2 (June 2026 call). Eshan asked for strict separation
    between Onboarding (data collection) and Validation (gap/anomaly
    review). This endpoint is how the deal moves between phases.

    The state machine:

        ONBOARDING (default) → VALIDATING → READY
                            ←------------ ←-----

    Backward transitions are allowed (e.g., ``VALIDATING → ONBOARDING``
    when the analyst needs to add more documents) so the UI can offer
    a "back to onboarding" CTA without minting a new deal.

    Pre-condition gating (e.g., "must have all checklist items green
    before VALIDATING → READY") is deferred — Sam needs to confirm the
    exact criteria. For now this endpoint enforces only the topological
    transition rules above and stamps the timestamp + audit trail.
    """
    current = (
        await session.execute(
            text(
                """
                SELECT state, validation_started_at, validation_complete_at
                  FROM deals
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if current is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )
    current_state = current._mapping.get("state") or "ONBOARDING"
    if body.next_state not in _ALLOWED_TRANSITIONS.get(current_state, set()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"transition {current_state} → {body.next_state} not allowed"
            ),
        )

    now = _now()
    # Stamp timestamps only on the forward transitions; backward moves
    # leave the prior timestamps intact so the audit trail captures the
    # original validation window.
    started_at_clause = ""
    complete_at_clause = ""
    params: dict[str, Any] = {
        "id": str(deal_id),
        "tenant": str(tenant_id),
        "next_state": body.next_state,
        "now": now,
    }
    if body.next_state == "VALIDATING" and current_state == "ONBOARDING":
        started_at_clause = ", validation_started_at = :now"
    if body.next_state == "READY":
        complete_at_clause = ", validation_complete_at = :now"

    await session.execute(
        text(
            f"""
            UPDATE deals
               SET state = :next_state,
                   updated_at = :now
                   {started_at_clause}
                   {complete_at_clause}
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        params,
    )
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        actor_id="system",
        action="deal.transition",
        resource_type="deal",
        resource_id=str(deal_id),
        output_payload={
            "from_state": current_state,
            "to_state": body.next_state,
            "notes": body.notes,
        },
    )
    await session.commit()
    logger.info(
        "deal.transition: deal=%s %s → %s",
        deal_id,
        current_state,
        body.next_state,
    )

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
    assert row is not None  # we just UPDATEd it
    return _row_to_record(dict(row._mapping))


@router.get("/{deal_id}/memo", response_model=MemoEnvelope)
async def get_memo(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> MemoEnvelope:
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
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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
async def get_deal_costs(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> Any:
    """Aggregated LLM cost dashboard for ``deal_id``.

    Reads ``ModelCall`` rows from the ``model_calls`` table (when
    populated) and rolls them up by agent and model bucket. Returns a
    well-formed zeroed report when there's no activity yet so the UI
    can render the empty state without a separate code path.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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
            # Document is EXTRACTED on the worker but the parser cache
            # didn't yield usable page text (image-heavy PDF, async parse
            # still in flight, parse failure pre-LlamaParse-fix). Emit
            # the document anyway with a placeholder excerpt so the
            # Analyst can cite the document by id + filename — better
            # than dropping the row and falling back to fixture
            # filenames the deal never uploaded (Sam re-test #2).
            excerpts = {
                1: f"[no parsed text available for {m['filename']!s} — "
                "the file uploaded successfully but the parser produced "
                "no extractable page text. Cite by filename only.]"
            }

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
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
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
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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
async def stream_memo(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> StreamingResponse:
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
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    section_id: str | None = None,
) -> list[MemoEditRecord]:
    """Return the chronological edit history for a deal's memo.

    Pass ``section_id`` to scope to a single section; omit it to get
    every edit across the deal (newest first).
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    rows = await list_edits(
        session, deal_id=str(deal_id), section_id=section_id
    )
    return [MemoEditRecord(**r) for r in rows]


# ════════════════════════════════════════════════════════════════════
# Wave 3 W3.1 — Comparable Sales engine
# ════════════════════════════════════════════════════════════════════
#
# Two endpoints:
#   GET  /deals/{id}/comp-sales            → full CompSalesSet for the deal
#   POST /deals/{id}/comp-sales/exclude    → pin a comp as excluded
#
# Both are tenant-scoped via Depends(get_tenant_id). The GET is the read
# path the Investment tab's "Comps" sub-panel calls; the POST persists
# an analyst's per-row "this comp doesn't reflect the deal" decision
# onto the deal's field_overrides JSONB column under the
# ``comp_sales.exclude_transaction_ids`` key.


class _CompTransactionOut(BaseModel):
    """One row of the OM's Comparable Sales table — API shape."""

    model_config = ConfigDict(extra="forbid")

    property_name: str | None = None
    city: str | None = None
    state: str | None = None
    sale_date: str | None = None
    keys: int | None = None
    sale_price_usd: float | None = None
    sale_price_per_key_usd: float | None = None
    noi_usd: float | None = None
    cap_rate_pct: float | None = None
    chain_scale: str | None = None
    brand_family: str | None = None
    flag: str | None = None
    source_document_id: str
    source_page_number: int | None = None
    note: str | None = None
    transaction_id: str | None = None


class _CompSalesSetOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    transactions: list[_CompTransactionOut] = Field(default_factory=list)
    total_count: int = 0
    derived_cap_rate_median: float | None = None
    derived_cap_rate_weighted: float | None = None
    derived_cap_rate_method: Literal["median", "weighted", "none"] = "none"
    weighting_notes: list[str] = Field(default_factory=list)
    coverage_quality: Literal["high", "medium", "low"] = "low"
    subject_market: str | None = None
    subject_chain_scale: str | None = None
    lookback_years: int = 5


class _CompSalesExcludeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(..., min_length=1, max_length=200)


def _comp_set_to_out(deal_id: UUID, comp_set: Any) -> _CompSalesSetOut:
    """Wrap a CompSalesSet for the API. ``sale_date`` → ISO string."""
    rows: list[_CompTransactionOut] = []
    for t in comp_set.transactions:
        rows.append(
            _CompTransactionOut(
                property_name=t.property_name,
                city=t.city,
                state=t.state,
                sale_date=t.sale_date.isoformat() if t.sale_date else None,
                keys=t.keys,
                sale_price_usd=t.sale_price_usd,
                sale_price_per_key_usd=t.sale_price_per_key_usd,
                noi_usd=t.noi_usd,
                cap_rate_pct=t.cap_rate_pct,
                chain_scale=t.chain_scale,
                brand_family=t.brand_family,
                flag=t.flag,
                source_document_id=t.source_document_id,
                source_page_number=t.source_page_number,
                note=t.note,
                transaction_id=t.transaction_id,
            )
        )
    return _CompSalesSetOut(
        deal_id=deal_id,
        transactions=rows,
        total_count=comp_set.total_count,
        derived_cap_rate_median=comp_set.derived_cap_rate_median,
        derived_cap_rate_weighted=comp_set.derived_cap_rate_weighted,
        derived_cap_rate_method=comp_set.derived_cap_rate_method,
        weighting_notes=list(comp_set.weighting_notes),
        coverage_quality=comp_set.coverage_quality,
        subject_market=comp_set.subject_market,
        subject_chain_scale=comp_set.subject_chain_scale,
        lookback_years=comp_set.lookback_years,
    )


async def _load_subject_market_and_chain(
    session: AsyncSession,
    *,
    deal_id: UUID,
    tenant_id: UUID,
) -> tuple[str | None, str | None]:
    """Pull the subject's ``"City, ST"`` market + chain-scale label.

    Best-effort: the deals row carries ``city`` and (when present)
    ``service`` we map onto a chain-scale bucket. Missing
    fields are returned as ``None`` — the comp engine handles that by
    falling back to recency-only weighting + reporting method=median.
    """
    row = (
        await session.execute(
            text(
                "SELECT city, service FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        return (None, None)
    m = row._mapping
    city = m.get("city") or None
    # No state column on the deals row today; the API expects
    # ``"City, ST"`` so we hand back just the city.
    subject_market = str(city) if city else None
    # Map ``service`` (Full Service / Select Service / Luxury / Resort)
    # onto a chain-scale bucket. Approximate but enough to enable the
    # chain-match weight; the analyst can refine via overrides.
    service = (m.get("service") or "").strip().lower()
    if service in ("luxury", "ultra luxury"):
        chain_scale: str | None = "luxury"
    elif service in ("full service", "full-service"):
        chain_scale = "upper-upscale"
    elif service in ("select service", "select-service"):
        chain_scale = "upper-midscale"
    elif service in ("limited service", "limited-service"):
        chain_scale = "midscale"
    elif service == "resort":
        chain_scale = "upper-upscale"
    else:
        chain_scale = None
    return (subject_market, chain_scale)


@router.get(
    "/{deal_id}/comp-sales",
    response_model=_CompSalesSetOut,
)
async def get_comp_sales(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> _CompSalesSetOut:
    """Return the deal's full CompSalesSet (transactions + derivation).

    Read-only. Reads OM extraction results, layers analyst overrides
    (exclude list), runs the deterministic Comparable Sales engine
    in-memory, and hands back the structured set. Tenant-scoped 404
    on cross-tenant deal ids.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    from ..services.engine_runner import _build_comp_sales_set

    subject_market, subject_chain_scale = await _load_subject_market_and_chain(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    comp_set = await _build_comp_sales_set(
        session,
        deal_id=str(deal_id),
        subject_market=subject_market,
        subject_chain_scale=subject_chain_scale,
    )
    return _comp_set_to_out(deal_id, comp_set)


@router.post(
    "/{deal_id}/comp-sales/exclude",
    response_model=_CompSalesSetOut,
)
async def exclude_comp_transaction(
    deal_id: UUID,
    body: _CompSalesExcludeRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> _CompSalesSetOut:
    """Pin a comp as excluded from the derived cap rate.

    Persists the transaction_id under the deal's ``field_overrides``
    JSONB column at key ``comp_sales.exclude_transaction_ids``. The
    value is the full sorted JSON array (idempotent — re-posting the
    same transaction_id is a no-op).

    Returns the refreshed CompSalesSet so the UI can render the new
    derivation without a second round-trip.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    # Read the current field_overrides blob, layer this exclude on top,
    # write back. We do this on the raw ``field_overrides`` column
    # rather than going through PATCH /deals so the audit entry is
    # scoped to the comp-sales action specifically.
    row = (
        await session.execute(
            text(
                "SELECT field_overrides FROM deals "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"deal {deal_id} not found",
        )
    raw = row._mapping.get("field_overrides")
    if isinstance(raw, str):
        try:
            overrides = json.loads(raw) or {}
        except json.JSONDecodeError:
            overrides = {}
    elif isinstance(raw, dict):
        overrides = dict(raw)
    else:
        overrides = {}

    # Parse existing exclude list. May be a real list, a JSON-array
    # string, or absent.
    existing = overrides.get("comp_sales.exclude_transaction_ids")
    current_ids: list[str] = []
    if isinstance(existing, list):
        current_ids = [str(x) for x in existing if x is not None]
    elif isinstance(existing, str):
        try:
            parsed = json.loads(existing)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            current_ids = [str(x) for x in parsed if x is not None]

    if body.transaction_id not in current_ids:
        current_ids.append(body.transaction_id)
    # JSONB-friendly: write the list as JSON-string under the path so
    # the override loader's coercion handles either shape uniformly.
    overrides["comp_sales.exclude_transaction_ids"] = json.dumps(
        sorted(current_ids)
    )

    is_sqlite = (
        session.bind is not None
        and session.bind.dialect.name == "sqlite"
    )
    if is_sqlite:
        sql = (
            "UPDATE deals SET field_overrides = :fo, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = :id AND tenant_id = :tenant"
        )
    else:
        sql = (
            "UPDATE deals SET field_overrides = CAST(:fo AS JSONB), "
            "updated_at = NOW() "
            "WHERE id = :id AND tenant_id = :tenant"
        )
    await session.execute(
        text(sql),
        {
            "fo": json.dumps(overrides),
            "id": str(deal_id),
            "tenant": str(tenant_id),
        },
    )
    # Wave 4 W4.3 — comp-exclude is a manual override on the IRR derivation
    # path. Surface it in the Activity Feed with the transaction id + the
    # before/after exclude list so the IT-review can trace why a deal's
    # cap rate moved.
    before_ids = sorted(
        x for x in (existing if isinstance(existing, list) else [])
        if x is not None
    )
    after_ids = sorted(current_ids)
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        action="comp_transaction.excluded",
        resource_type="comp_transaction",
        resource_id=body.transaction_id,
        output_payload={
            "transaction_id": body.transaction_id,
            "exclude_count": len(after_ids),
        },
        before={"exclude_transaction_ids": before_ids},
        after={"exclude_transaction_ids": after_ids},
        diff_summary=(
            f"excluded comp transaction {body.transaction_id} "
            f"({len(before_ids)} → {len(after_ids)} total)"
        ),
        tags=["comp_sales", "override"],
        metadata={"deal_id": str(deal_id)},
    )
    await session.commit()

    # Re-derive and return.
    from ..services.engine_runner import _build_comp_sales_set

    subject_market, subject_chain_scale = await _load_subject_market_and_chain(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    comp_set = await _build_comp_sales_set(
        session,
        deal_id=str(deal_id),
        subject_market=subject_market,
        subject_chain_scale=subject_chain_scale,
    )
    return _comp_set_to_out(deal_id, comp_set)
