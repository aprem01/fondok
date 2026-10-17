"""Engine + scenario endpoints (the underwriting model side).

Two router groups live in this module:

    * ``router`` — legacy ``/model/...`` stubs the original UI used.
      Kept for backwards-compatibility with the existing shape.

    * ``engines_router`` — the new deal-scoped engine surface
      ``/deals/{id}/engines/...`` that the Run Model button hits. Runs
      the deterministic engine chain server-side, persists outputs to
      ``engine_outputs`` and exposes polling endpoints so the UI can
      reflect ``running → complete`` without re-running the math.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from fondok_schemas.reasons import ReasonCode
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..auth.context import current_tenant_id
from ..database import get_session, get_session_factory
from ..engines.timeline import TimelineEvent, build_timeline, parse_iso_date
from ..services.engine_runner import (
    ENGINE_NAMES,
    ENGINE_REGISTRY,
    _load_deal_overrides,
    get_latest_output,
    get_run_scoped_outputs,
    get_run_status,
    run_all_engines,
    run_returns_preview,
    run_single_engine,
)
from ..services.run_freshness import stale_engines
from .deals import _assert_deal_belongs_to_tenant, get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()
engines_router = APIRouter()


# ─────────────────────── legacy /model stubs ──────────────────────────


class EngineRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: str = Field(
        description=(
            "revenue | fb | expense | capital | debt | returns | "
            "sensitivity | partnership"
        )
    )
    inputs: dict[str, Any] = Field(default_factory=dict)


class EngineRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    engine: str
    outputs: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    overrides: dict[str, Any] = Field(default_factory=dict)


class ScenarioResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    scenario: str
    status: str = "queued"


@router.post("/{deal_id}/engines/run", response_model=EngineRunResponse)
async def run_engine_legacy(
    deal_id: UUID,
    body: EngineRunRequest,
    _tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> EngineRunResponse:
    """Legacy stub — kept so existing clients don't 404.

    FON-93 (2026-10-15): resolves the caller like every other route so an
    unauthenticated request is 401, not a 200 placeholder.

    TODO(remove-legacy-model-route): the web app moved to the
    deal-scoped ``/deals/{id}/engines/{name}/run`` route months ago;
    this entry-point can be deleted once the OpenAPI client gen drops
    the corresponding helper. Returning an empty ``outputs`` keeps
    legacy callers from misinterpreting the response as real.
    """
    logger.info("model(stub-legacy): run engine=%s deal=%s", body.engine, deal_id)
    return EngineRunResponse(deal_id=deal_id, engine=body.engine)


@router.post("/{deal_id}/scenarios", response_model=ScenarioResponse)
async def create_scenario(
    deal_id: UUID,
    body: ScenarioRequest,
    _tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> ScenarioResponse:
    """Stub: spawns a what-if scenario derived from the base case.

    FON-93 (2026-10-15): resolves the caller (401 without a session).

    TODO(scenario-engine): no real scenario runner yet. The plan is
    to overlay ``ScenarioRequest.overrides`` on the base assumptions
    and call the engine chain again with a distinct ``run_id``. For
    now the response is a placeholder so existing clients don't 404.
    """
    return ScenarioResponse(deal_id=deal_id, scenario=body.name)


# ──────────────────── /deals/{id}/engines/... ─────────────────────────


class EngineRunBody(BaseModel):
    """Optional body accepted by both run-all and run-one.

    ``assumptions`` is a free-form dict (purchase_price, ltv, etc.) that
    overlays the deal's defaults. Empty body == use defaults.
    """

    model_config = ConfigDict(extra="forbid")

    assumptions: dict[str, Any] | None = None


class EngineSlotStatus(BaseModel):
    """Single engine's status inside the run-all kickoff response."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: str  # 'queued' | 'running' | 'complete' | 'failed'


class EngineRunKickoff(BaseModel):
    """Returned immediately when a run is scheduled to a background task."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    run_id: str
    started_at: datetime
    engines: list[EngineSlotStatus]


class EngineOutputResponse(BaseModel):
    """Latest persisted row for one engine."""

    model_config = ConfigDict(extra="ignore")

    deal_id: str
    engine: str
    status: str
    summary: str = ""
    outputs: dict[str, Any] | None = None
    inputs: dict[str, Any] | None = None
    error: str | None = None
    runtime_ms: int | None = None
    started_at: str | None = None
    completed_at: str | None = None
    run_id: str | None = None


class StaleRun(BaseModel):
    """FON-75 — this snapshot predates part of the model that produced it.

    ``missing_blocks`` maps an engine name to the dotted paths of output
    fields the engine now publishes but this persisted run does not carry —
    i.e. the run is older than the field. A field the run carries with value
    ``null`` is NOT listed: the engine ran with it and honestly resolved no
    value, which is a refusal, not staleness. See
    ``app/services/run_freshness.py``.

    The ``reason`` is the existing ``stale_run`` ReasonCode, so the UI prints
    one vocabulary rather than inventing banner-specific copy.
    """

    model_config = ConfigDict(extra="forbid")

    reason: Literal["stale_run"] = ReasonCode.STALE_RUN.value
    missing_blocks: dict[str, list[str]]


class EngineOutputsResponse(BaseModel):
    """Map of engine name → latest output for a deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    engines: dict[str, EngineOutputResponse]
    # FON-75 — ``None`` whenever the snapshot is current, which is the
    # overwhelmingly common case: a healthy deal's payload gains nothing but
    # an explicit ``"stale_run": null``, and every existing field is untouched.
    stale_run: StaleRun | None = None


class EngineRunStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    run_id: str
    engines: list[EngineOutputResponse]


def _validate_engine_name(name: str) -> None:
    if name not in ENGINE_REGISTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"unknown engine {name!r}; expected one of "
                f"{sorted(ENGINE_REGISTRY)}"
            ),
        )


async def _background_run_all(
    deal_id: str,
    tenant_id: str,
    run_id: str,
    overrides: dict[str, Any] | None,
) -> None:
    """Background-task wrapper that opens its own session and runs the
    full chain. We open a fresh session here because the request-scoped
    session would already be closed by the time the task fires."""
    # FON-93: the task runs outside the request's auth dependency, so the
    # RLS GUC hook (``database._tenant_guc_after_begin``) needs the tenant
    # published here from the argument.
    current_tenant_id.set(str(tenant_id))
    factory = get_session_factory()
    async with factory() as session:
        try:
            await run_all_engines(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                run_id=run_id,
                overrides=overrides,
            )
        except Exception:  # pragma: no cover - logged for ops visibility
            logger.exception(
                "engines/run_all background task failed deal=%s run=%s",
                deal_id, run_id,
            )


async def _background_run_one(
    deal_id: str,
    tenant_id: str,
    engine_name: str,
    run_id: str,
    overrides: dict[str, Any] | None,
) -> None:
    current_tenant_id.set(str(tenant_id))  # FON-93 — see _background_run_all
    factory = get_session_factory()
    async with factory() as session:
        try:
            await run_single_engine(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                engine_name=engine_name,
                run_id=run_id,
                overrides=overrides,
            )
        except Exception:  # pragma: no cover
            logger.exception(
                "engines/run background task failed deal=%s engine=%s run=%s",
                deal_id, engine_name, run_id,
            )


@engines_router.post(
    "/{deal_id}/engines/run",
    response_model=EngineRunKickoff,
    status_code=status.HTTP_202_ACCEPTED,
)
async def kickoff_run_all(
    deal_id: str,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    body: EngineRunBody | None = None,
) -> EngineRunKickoff:
    """Run the full engine chain in the background.

    Returns immediately with a ``run_id`` the client can poll via
    ``GET /deals/{id}/engines/run/{run_id}``.

    Tenant-scoped: cross-tenant access returns 404 before the
    background task is scheduled — otherwise an attacker could
    enqueue compute against another tenant's deal.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    run_id = str(uuid4())
    started_at = datetime.now(UTC)
    overrides = body.assumptions if body else None

    background_tasks.add_task(
        _background_run_all,
        deal_id, str(tenant_id), run_id, overrides,
    )

    # Wave 4 W4.3 — engine_run audit fires at kickoff so the Activity
    # Feed surfaces the scheduled run even if the background task later
    # fails. Pairs with the scenario.run audit emitted by run_scenario.
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        action="engine_run.kicked_off",
        resource_type="engine_run",
        resource_id=run_id,
        output_payload={
            "engine_count": len(ENGINE_NAMES),
            "had_overrides": overrides is not None,
        },
        diff_summary=f"kicked off run of {len(ENGINE_NAMES)} engines",
        tags=["engine_run", "kickoff"],
        metadata={"deal_id": deal_id},
    )
    await session.commit()

    logger.info(
        "engines/run_all scheduled deal=%s run=%s tenant=%s",
        deal_id, run_id, tenant_id,
    )
    return EngineRunKickoff(
        deal_id=deal_id,
        run_id=run_id,
        started_at=started_at,
        engines=[EngineSlotStatus(name=n, status="queued") for n in ENGINE_NAMES],
    )


@engines_router.post(
    "/{deal_id}/engines/{name}/run",
    response_model=EngineOutputResponse,
)
async def kickoff_run_one(
    deal_id: str,
    name: str,
    background_tasks: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    body: EngineRunBody | None = None,
) -> EngineOutputResponse:
    """Run a single engine.

    Engines are pure-Python and deterministic; total chain runs in well
    under a second on the Kimpton fixture, so we run synchronously in
    the request and return the persisted row directly. The
    ``BackgroundTasks`` parameter is kept on the signature so future
    long-running engines can flip a single line to defer.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    _validate_engine_name(name)
    overrides = body.assumptions if body else None
    run_id = str(uuid4())

    result = await run_single_engine(
        session,
        deal_id=deal_id,
        tenant_id=tenant_id and str(tenant_id),
        engine_name=name,
        run_id=run_id,
        overrides=overrides,
    )

    # Re-fetch the persisted row so the response contains the canonical
    # started_at / completed_at the DB recorded.
    row = await get_latest_output(
        session, deal_id=deal_id, engine_name=name, tenant_id=str(tenant_id)
    )
    if row is None:  # pragma: no cover - defensive; we just inserted it
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="engine row missing after run",
        )
    # Ensure background_tasks is referenced so FastAPI keeps the dep
    # resolved (cheap no-op).
    _ = background_tasks
    _ = result
    return EngineOutputResponse(**row)


class ReturnsPreviewBody(BaseModel):
    """Ephemeral sandbox overrides for the Returns preview (FON-68 step 3).

    ``overrides`` carries the Live-Assumptions slider values (exit_cap_rate,
    revpar_growth, hold_years, ltv, interest_rate). Only those recognized keys
    are applied server-side; everything else is ignored. Empty body == the
    deal's canonical base case.
    """

    model_config = ConfigDict(extra="forbid")

    overrides: dict[str, Any] | None = None
    include_sensitivity: bool = False


class ReturnsPreviewResponse(BaseModel):
    """Sandbox returns headline — computed in-memory, persisted NOWHERE."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    levered_irr: float | None = None
    unlevered_irr: float | None = None
    equity_multiple: float | None = None
    year_one_coc: float | None = None
    exit_value: float | None = None
    net_proceeds: float | None = None
    dscr_y1: float | None = None
    hold_years: int | None = None
    exit_cap_rate: float | None = None
    # FON-68 §3 — the loan the sandbox ran on. ``loan_amount`` is what the
    # capital engine sized (what the returns engine levered); ``total_debt`` is
    # the debt engine's whole stack. Equal on a senior-only deal.
    loan_amount: float | None = None
    total_debt: float | None = None
    # FON-68 §1 — the rest of the Returns Summary headline, so a dirty sandbox
    # can render its own hero tiles (avg CoC, yield on cost, equity profit)
    # rather than leaving them on the canonical case.
    avg_coc: float | None = None
    total_capital: float | None = None
    noi_by_year: list[float] | None = None
    cash_flows: list[float] | None = None
    # Present only when ``include_sensitivity`` was requested — the serialized
    # sensitivity engine output (row/col axes + flat cell list), matching the
    # shape the web ``matrixFromWorker`` helper already consumes.
    sensitivity: dict[str, Any] | None = None


@engines_router.post(
    "/{deal_id}/engines/returns/preview",
    response_model=ReturnsPreviewResponse,
)
async def preview_returns(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    body: ReturnsPreviewBody | None = None,
) -> ReturnsPreviewResponse:
    """Compute a non-persisting Returns preview under sandbox overrides.

    Powers the Returns tab's ephemeral "Live Assumptions" sensitivity sandbox
    (FON-68 step 3). Walks the engine chain in memory
    (revenue → fb → expense → capital → debt → returns, optionally
    → sensitivity), flexes the recognized slider keys on top of the deal's
    canonical inputs, and returns the resulting IRR / equity multiple /
    year-one CoC / exit value / DSCR (+ optional sensitivity grid).

    Read-only guarantee: this NEVER writes to ``engine_outputs``, never mints
    a ``run_id``, and never advances the deal's canonical run — dragging a
    sandbox slider cannot mutate the deal. Tenant-scoped like the sibling
    engine routes (cross-tenant deal ids 404 before any compute).
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    body = body or ReturnsPreviewBody()
    result = await run_returns_preview(
        session,
        deal_id=deal_id,
        tenant_id=str(tenant_id),
        overrides=body.overrides,
        include_sensitivity=body.include_sensitivity,
    )
    return ReturnsPreviewResponse(deal_id=deal_id, **result)


@engines_router.get(
    "/{deal_id}/engines",
    response_model=EngineOutputsResponse,
)
async def list_engine_outputs(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> EngineOutputsResponse:
    """Return one internally-consistent run-scoped snapshot for ``deal_id``.

    FON-73 — serve the deal's *canonical* run (the most-recent full
    ``run_all_engines`` chain that isn't a non-base scenario) so every
    engine here comes from the SAME ``run_id``. This replaces the old
    latest-row-per-engine read, under which a single-engine re-run could
    silently de-sync the deal-wide snapshot (Debt from run A, Returns from
    run B) — the root cause of the cross-tab "two different Base" drift
    (FON-54 / FON-69). Falls back to the legacy per-engine latest only
    when the deal has never completed a full chain, so mid-migration /
    never-run deals still render instead of 404-ing or showing empty.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    rows = await get_run_scoped_outputs(
        session, deal_id=deal_id, tenant_id=str(tenant_id)
    )
    engines = {name: EngineOutputResponse(**row) for name, row in rows.items()}
    # FON-75 — staleness is a property of an OLD run read against a NEW
    # engine, so it is derived here at read time rather than stamped at
    # write time: a run written last month becomes stale the moment a new
    # output field deploys, and nothing rewrites that row. Read-only — no
    # engine output is computed, altered or persisted by this call.
    missing = stale_engines(rows)
    return EngineOutputsResponse(
        deal_id=deal_id,
        engines=engines,
        stale_run=StaleRun(missing_blocks=missing) if missing else None,
    )


class TimelineResponse(BaseModel):
    """Dated transaction timeline for the Investment tab (FON-71)."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    # The acquisition close date drives every other date; None until the
    # analyst enters it (dates then read as "pending" in the UI).
    close_date: str | None = None
    # Convenience anchors surfaced on the Exit Valuation panel.
    exit_date: str | None = None
    stabilization_date: str | None = None  # FTM (forward-twelve-month) date
    events: list[TimelineEvent]


def _timeline_int(value: object) -> int | None:
    """Coerce a JSON scalar to int, or None when absent / non-numeric."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _reno_budget_from_capital(capital_out: dict[str, Any]) -> float:
    """Renovation budget from the capital output's 'Renovation' uses line."""
    for line in capital_out.get("uses") or []:
        if isinstance(line, dict) and "renovat" in str(line.get("label", "")).lower():
            try:
                return float(line.get("amount") or 0.0)
            except (TypeError, ValueError):
                return 0.0
    return 0.0


@engines_router.get("/{deal_id}/engines/timeline", response_model=TimelineResponse)
async def get_deal_timeline(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> TimelineResponse:
    """Project the transaction schedule onto calendar dates.

    Reads the acquisition close date from ``field_overrides`` and the timing
    already modeled by the returns (hold) and debt (term / interest-only)
    engines, then maps those month-offsets onto dates. No re-run, no writes.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    overrides = await _load_deal_overrides(
        session, deal_id=deal_id, tenant_id=str(tenant_id)
    )
    # FON-73 — read the canonical run so the dated timeline is derived from
    # the SAME run as every other deal-wide tab (falls back to latest-per-
    # engine when no full chain has completed).
    rows = await get_run_scoped_outputs(
        session, deal_id=deal_id, tenant_id=str(tenant_id)
    )
    returns_out = (rows.get("returns") or {}).get("outputs") or {}
    debt_out = (rows.get("debt") or {}).get("outputs") or {}
    capital_out = (rows.get("capital") or {}).get("outputs") or {}

    close_date = parse_iso_date(overrides.get("acquisition_close_date"))

    io_years = _timeline_int(debt_out.get("interest_only_years"))
    io_months = (
        io_years * 12
        if io_years
        else _timeline_int(debt_out.get("io_period_months"))
    )
    dso = overrides.get("debt_stack_overrides")
    refi_month = (
        _timeline_int(dso.get("refi_month")) if isinstance(dso, dict) else None
    )

    events = build_timeline(
        close_date=close_date,
        hold_years=_timeline_int(returns_out.get("hold_years")),
        term_years=_timeline_int(debt_out.get("term_years")),
        interest_only_months=io_months,
        refi_month=refi_month,
        renovation_budget=_reno_budget_from_capital(capital_out),
        # Per-deal-overridable renovation / ramp windows (FON-71); each falls
        # back to the engine default when the analyst hasn't set it.
        renovation_start_offset_months=_timeline_int(
            overrides.get("renovation_start_offset_months")
        ),
        renovation_duration_months=_timeline_int(
            overrides.get("renovation_duration_months")
        ),
        ramp_months=_timeline_int(overrides.get("ramp_months")),
    )

    exit_ev = next((e for e in events if e.event.startswith("Disposition")), None)
    stab_ev = next((e for e in events if e.event.startswith("Stabilized")), None)
    return TimelineResponse(
        deal_id=deal_id,
        close_date=close_date.isoformat() if close_date else None,
        exit_date=exit_ev.finish if exit_ev else None,
        stabilization_date=stab_ev.finish if stab_ev else None,
        events=events,
    )


class InvestmentBridgeComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    value: float
    # The engine field this figure is read from (``capital.uses[...]``,
    # ``returns.noi_by_year[i]``, ``returns.inputs.loan_balance_at_exit`` …).
    source: str


class InvestmentBridgeLeg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal["acquisition", "renovation", "operations", "financing", "exit"]
    label: str
    # None when the leg is unavailable (``status="unavailable"``) or the deal
    # has no such item (``status="none"``) — never a fabricated zero.
    value: float | None
    status: Literal["ok", "none", "unavailable"]
    formula: str
    components: list[InvestmentBridgeComponent]
    reason: str | None = None


class InvestmentBridgeResponse(BaseModel):
    """R-073 — equity invested → equity returned, attributed by leg."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    available: bool
    reason: str | None = None
    equity_invested: float | None
    equity_invested_source: str
    equity_returned: float | None
    equity_returned_source: str
    equity_profit: float | None
    hold_years: int | None
    legs: list[InvestmentBridgeLeg]
    unavailable: list[str]
    computed_equity_returned: float | None
    residual: float | None
    reconciles: bool


@engines_router.get(
    "/{deal_id}/engines/investment-bridge",
    response_model=InvestmentBridgeResponse,
)
async def get_investment_bridge(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> InvestmentBridgeResponse:
    """R-073 — the Investment Bridge waterfall, derived from the canonical run.

    Read-only: reads the SAME run-scoped snapshot every deal-wide tab reads
    (FON-73) and attributes equity invested → equity returned across
    acquisition, renovation, operations, financing and exit. No engine is
    re-run and nothing is persisted.
    """
    from ..services.investment_bridge import build_investment_bridge

    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    rows = await get_run_scoped_outputs(
        session, deal_id=deal_id, tenant_id=str(tenant_id)
    )
    return InvestmentBridgeResponse(deal_id=deal_id, **build_investment_bridge(rows))


# ─────────────── Regression baselines: list + compare runs ───────────────
#
# Sam (2026-10-16): preserve the Kimpton model outputs so expected changes can
# be told apart from unintended calculation drift. ``engine_outputs`` is
# append-only (one INSERT per engine per run; only deal deletion removes rows),
# so every earlier run already IS a preserved baseline. These read-only routes
# list those runs and diff the headline outputs of any two. Registered BEFORE
# ``/{deal_id}/engines/{name}`` so "runs" / "compare" are not read as an
# engine name.


class EngineRunScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    is_base: bool


class EngineRunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: str | None = None
    completed_at: str | None = None
    engines: list[str]
    status: Literal["complete", "partial", "failed", "running"]
    # Has every base engine (the FON-73 canonical gate).
    is_full_chain: bool
    # Is the run the deal-wide canonical snapshot the tabs serve right now.
    is_canonical: bool
    # Set when a scenario's ``last_run_id`` points at this run. Only the
    # scenario's latest run is recorded, so older scenario runs read as
    # plain runs (documented in docs/REGRESSION_BASELINE.md).
    scenario: EngineRunScenario | None = None


class EngineRunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    canonical_run_id: str | None = None
    # When ``?before=`` was passed: the newest complete, full-chain,
    # non-scenario run completed before that instant (the default baseline).
    baseline_before: str | None = None
    baseline_run_id: str | None = None
    runs: list[EngineRunSummary]


class RunCompareSide(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: str | None = None
    completed_at: str | None = None
    engines: dict[str, str] = Field(
        default_factory=dict, description="engine → status in this run"
    )


class RunCompareField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    engine: str
    group: str
    label: str
    unit: str
    status: Literal["changed", "added", "removed", "unchanged"]
    base_value: float | int | str | None = None
    head_value: float | int | str | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None
    delta_reason: str | None = None
    base_provenance: dict[str, Any] | None = None
    head_provenance: dict[str, Any] | None = None


class RunCompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    base: RunCompareSide
    head: RunCompareSide
    include_unchanged: bool
    fields_compared: int
    fields_changed: int
    tolerance: dict[str, float]
    fields: list[RunCompareField]


def _parse_before(value: str | None) -> datetime | None:
    from ..services.run_compare import parse_ts

    if value is None:
        return None
    dt = parse_ts(value)
    if dt is None:
        raise HTTPException(
            status_code=422,
            detail=f"before={value!r} is not an ISO-8601 timestamp",
        )
    return dt


async def _deal_runs(
    session: AsyncSession, *, deal_id: str, tenant_id: UUID
) -> tuple[str, str | None, list[dict[str, Any]]]:
    from ..services.engine_runner import (
        CANONICAL_ENGINES,
        _coerce_uuid,
        get_canonical_run_id,
    )
    from ..services.run_compare import list_runs

    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    deal_uuid = str(_coerce_uuid(deal_id))
    canonical = await get_canonical_run_id(
        session, deal_id=deal_id, tenant_id=str(tenant_id)
    )
    runs = await list_runs(
        session,
        deal_uuid=deal_uuid,
        tenant_id=str(tenant_id),
        canonical_engines=CANONICAL_ENGINES,
        canonical_run_id=canonical,
    )
    return deal_uuid, canonical, runs


@engines_router.get(
    "/{deal_id}/engines/runs",
    response_model=EngineRunListResponse,
)
async def list_engine_runs(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    before: str | None = None,
) -> EngineRunListResponse:
    """Every persisted run on the deal (newest first) — read-only."""
    from ..services.run_compare import pick_baseline

    before_dt = _parse_before(before)
    _, canonical, runs = await _deal_runs(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    baseline = pick_baseline(runs, before_dt) if before_dt else None
    return EngineRunListResponse(
        deal_id=deal_id,
        canonical_run_id=canonical,
        baseline_before=before_dt.isoformat() if before_dt else None,
        baseline_run_id=baseline["run_id"] if baseline else None,
        runs=[EngineRunSummary(**r) for r in runs],
    )


async def _compare(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: UUID,
    base: str,
    head: str,
    include_unchanged: bool,
) -> dict[str, Any]:
    from ..services.run_compare import (
        ABS_TOL,
        REL_TOL,
        diff_fields,
        extract_headline_fields,
        load_run_engines,
        outputs_only,
    )

    deal_uuid, canonical, runs = await _deal_runs(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    known = {r["run_id"] for r in runs}

    def _resolve(ref: str, label: str) -> str:
        if ref in ("latest", "canonical"):
            if canonical:
                return canonical
            if not runs:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"deal {deal_id} has no engine runs",
                )
            # Runs exist but none completed the full chain, so there is no
            # canonical run. ``latest`` promises the canonical run (the one
            # every tab serves; the panel labels it "Latest (canonical)"),
            # so say so rather than silently diffing against the newest
            # partial / failed run and reporting its gaps as changes.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"deal {deal_id} has no canonical run: none of its "
                    f"{len(runs)} run(s) completed the full engine chain. "
                    f"Pass an explicit run id as {label} to compare a "
                    "partial run."
                ),
            )
        if ref not in known:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{label} run {ref} not found on deal {deal_id}",
            )
        return ref

    base_id = _resolve(base, "base")
    head_id = _resolve(head, "head")
    base_rows = await load_run_engines(
        session, deal_uuid=deal_uuid, tenant_id=str(tenant_id), run_id=base_id
    )
    head_rows = await load_run_engines(
        session, deal_uuid=deal_uuid, tenant_id=str(tenant_id), run_id=head_id
    )
    base_fields = extract_headline_fields(outputs_only(base_rows))
    head_fields = extract_headline_fields(outputs_only(head_rows))
    rows = diff_fields(base_fields, head_fields, include_unchanged=include_unchanged)
    compared = len({f["key"] for f in base_fields} | {f["key"] for f in head_fields})
    changed = sum(1 for r in rows if r["status"] != "unchanged")

    def _side(run_id: str, engines: dict[str, dict[str, Any]]) -> dict[str, Any]:
        meta = next((r for r in runs if r["run_id"] == run_id), {})
        return {
            "run_id": run_id,
            "started_at": meta.get("started_at"),
            "completed_at": meta.get("completed_at"),
            "engines": {name: row["status"] for name, row in engines.items()},
        }

    return {
        "deal_id": deal_id,
        "base": _side(base_id, base_rows),
        "head": _side(head_id, head_rows),
        "include_unchanged": include_unchanged,
        "fields_compared": compared,
        "fields_changed": changed,
        "tolerance": {"rel": REL_TOL, "abs": ABS_TOL},
        "fields": rows,
    }


@engines_router.get(
    "/{deal_id}/engines/compare",
    response_model=RunCompareResponse,
)
async def compare_engine_runs(
    deal_id: str,
    base: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    head: str = "latest",
    all: int = 0,  # noqa: A002 — the public query param is ``?all=1``
) -> RunCompareResponse:
    """Field-level diff of two runs' headline outputs — read-only.

    ``head`` defaults to ``latest`` (the deal's canonical run). A deal with
    runs but no canonical run — none completed the full chain — gets 404
    for ``latest``; name a run id to compare a partial run. Unchanged
    fields are omitted unless ``?all=1``.
    """
    result = await _compare(
        session,
        deal_id=deal_id,
        tenant_id=tenant_id,
        base=base,
        head=head,
        include_unchanged=bool(all),
    )
    return RunCompareResponse(**result)


@engines_router.get("/{deal_id}/engines/compare/xlsx")
async def export_engine_run_compare(
    deal_id: str,
    base: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    head: str = "latest",
    all: int = 0,  # noqa: A002
) -> Response:
    """The same comparison as an .xlsx workbook (Summary + Changed fields)."""
    from ..services.run_compare import build_compare_workbook

    result = await _compare(
        session,
        deal_id=deal_id,
        tenant_id=tenant_id,
        base=base,
        head=head,
        include_unchanged=bool(all),
    )
    payload = build_compare_workbook(result)
    fname = (
        f"fondok-run-compare-{result['base']['run_id'][:8]}"
        f"-vs-{result['head']['run_id'][:8]}.xlsx"
    )
    return Response(
        content=payload,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@engines_router.get(
    "/{deal_id}/engines/run/{run_id}",
    response_model=EngineRunStatusResponse,
)
async def get_engine_run_status(
    deal_id: str,
    run_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> EngineRunStatusResponse:
    """Return every engine row associated with ``run_id``."""
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    rows = await get_run_status(
        session, deal_id=deal_id, run_id=run_id, tenant_id=str(tenant_id)
    )
    return EngineRunStatusResponse(
        deal_id=deal_id,
        run_id=run_id,
        engines=[EngineOutputResponse(**r) for r in rows],
    )


@engines_router.get(
    "/{deal_id}/engines/{name}",
    response_model=EngineOutputResponse,
)
async def get_engine_output(
    deal_id: str,
    name: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> EngineOutputResponse:
    """Return the latest persisted row for one engine on ``deal_id``."""
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    _validate_engine_name(name)
    row = await get_latest_output(
        session, deal_id=deal_id, engine_name=name, tenant_id=str(tenant_id)
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no output found for engine {name!r} on deal {deal_id}",
        )
    return EngineOutputResponse(**row)
