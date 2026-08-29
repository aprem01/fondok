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
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..database import get_session, get_session_factory
from ..engines.timeline import TimelineEvent, build_timeline, parse_iso_date
from ..services.engine_runner import (
    ENGINE_NAMES,
    ENGINE_REGISTRY,
    _load_deal_overrides,
    get_latest_output,
    get_latest_outputs,
    get_run_status,
    run_all_engines,
    run_single_engine,
)
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
async def run_engine_legacy(deal_id: UUID, body: EngineRunRequest) -> EngineRunResponse:
    """Legacy stub — kept so existing clients don't 404.

    TODO(remove-legacy-model-route): the web app moved to the
    deal-scoped ``/deals/{id}/engines/{name}/run`` route months ago;
    this entry-point can be deleted once the OpenAPI client gen drops
    the corresponding helper. Returning an empty ``outputs`` keeps
    legacy callers from misinterpreting the response as real.
    """
    logger.info("model(stub-legacy): run engine=%s deal=%s", body.engine, deal_id)
    return EngineRunResponse(deal_id=deal_id, engine=body.engine)


@router.post("/{deal_id}/scenarios", response_model=ScenarioResponse)
async def create_scenario(deal_id: UUID, body: ScenarioRequest) -> ScenarioResponse:
    """Stub: spawns a what-if scenario derived from the base case.

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


class EngineOutputsResponse(BaseModel):
    """Map of engine name → latest output for a deal."""

    model_config = ConfigDict(extra="forbid")

    deal_id: str
    engines: dict[str, EngineOutputResponse]


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


@engines_router.get(
    "/{deal_id}/engines",
    response_model=EngineOutputsResponse,
)
async def list_engine_outputs(
    deal_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> EngineOutputsResponse:
    """Return the latest persisted output per engine for ``deal_id``."""
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    rows = await get_latest_outputs(session, deal_id=deal_id, tenant_id=str(tenant_id))
    engines = {name: EngineOutputResponse(**row) for name, row in rows.items()}
    return EngineOutputsResponse(deal_id=deal_id, engines=engines)


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
    rows = await get_latest_outputs(
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
