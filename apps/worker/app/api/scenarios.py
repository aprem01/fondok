"""Named scenarios — save/load/diff what-if scenarios per deal.

Wave 3 W3.2. Every IC committee asks "what's the upside? what's the
downside?" Today Fondok models a single point estimate; analysts copy
the deal, mess with assumptions, screenshot the outputs, and paste
them into PowerPoint. A *scenario* is a named layer of overrides on
top of the deal's persisted ``field_overrides``; the engine runs with
the scenario applied without disturbing the base deal.

Every deal has exactly one ``is_base=true`` scenario (auto-created on
deal insert via :func:`create_base_scenario_for_deal`) plus any number
of analyst-defined scenarios. The base scenario carries an empty
override list — running the engine against it is byte-identical to
running without a ``scenario_id`` at all.

The router is mounted at ``/deals/{deal_id}/scenarios``. Every route
is tenant-scoped via :func:`get_tenant_id`.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..database import get_session
from ..services.engine_runner import (
    ENGINE_NAMES,
    get_latest_outputs,
    run_all_engines,
)
from .deals import _assert_deal_belongs_to_tenant, get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── shapes ───────────────────────────


class ScenarioOverrideBody(BaseModel):
    """Per-field override inside a scenario.

    Mirrors :class:`fondok_schemas.scenario.ScenarioOverride`. ``value``
    is intentionally typed ``Any`` so we can carry scalars + the PIP
    monthly-schedule list through the same shape the engine_runner
    already understands.
    """

    model_config = ConfigDict(extra="forbid")

    field_path: Annotated[str, Field(min_length=1, max_length=200)]
    value: Any
    source: Annotated[str, Field(min_length=1, max_length=40)] = "analyst_override"


class ScenarioRecord(BaseModel):
    """Full scenario row returned by every CRUD endpoint."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    deal_id: UUID
    tenant_id: UUID
    name: str
    description: str | None = None
    is_base: bool = False
    overrides: list[ScenarioOverrideBody] = Field(default_factory=list)
    last_run_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class CreateScenarioBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None = None
    overrides: list[ScenarioOverrideBody] = Field(default_factory=list)


class UpdateScenarioBody(BaseModel):
    """Partial scenario update.

    ``overrides``, when present, REPLACES the existing override list
    (not append-on). This matches what the side-panel editor wants —
    the analyst sees the current list, edits in place, hits save.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    overrides: list[ScenarioOverrideBody] | None = None


class ScenarioRunResponse(BaseModel):
    """Returned from POST /scenarios/{id}/run.

    Carries the engine output map keyed by engine name + the
    ``run_id`` we stamped into ``scenarios.last_run_id``.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID
    deal_id: UUID
    run_id: UUID
    started_at: datetime
    engines: dict[str, dict[str, Any]]


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_ids: Annotated[list[UUID], Field(min_length=1, max_length=4)]


class CompareCell(BaseModel):
    """One scenario column inside the side-by-side compare response."""

    model_config = ConfigDict(extra="forbid")

    scenario_id: UUID
    scenario_name: str
    is_base: bool
    last_run_id: UUID | None = None
    engines: dict[str, dict[str, Any]]


class CompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: UUID
    base_scenario_id: UUID | None = None
    scenarios: list[CompareCell]


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


def _coerce_overrides(value: Any) -> list[dict[str, Any]]:
    """Normalize the JSONB/TEXT column to ``list[dict]``.

    Postgres hands us a parsed list; SQLite hands us a JSON string. We
    accept both. Anything malformed silently falls back to ``[]`` so a
    bad row never blows up the API.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [v for v in parsed if isinstance(v, dict)]
    return []


def _row_to_record(row: Any) -> ScenarioRecord:
    m = row._mapping
    return ScenarioRecord(
        id=UUID(str(m["id"])),
        deal_id=UUID(str(m["deal_id"])),
        tenant_id=UUID(str(m["tenant_id"])),
        name=m["name"],
        description=m.get("description"),
        is_base=bool(m.get("is_base")),
        overrides=[ScenarioOverrideBody(**o) for o in _coerce_overrides(m.get("overrides"))],
        last_run_id=UUID(str(m["last_run_id"])) if m.get("last_run_id") else None,
        created_at=_coerce_dt(m.get("created_at")),
        updated_at=_coerce_dt(m.get("updated_at")),
    )


_SCENARIO_COLUMNS = (
    "id, deal_id, tenant_id, name, description, is_base, overrides, "
    "last_run_id, created_at, updated_at"
)


def _serialize_overrides_for_db(
    overrides: list[ScenarioOverrideBody],
    *,
    is_sqlite: bool,
) -> tuple[str, str]:
    """Return ``(sql_fragment, json_string)``.

    Postgres needs an explicit ``CAST(... AS JSONB)`` because we always
    bind the value as a string (the driver doesn't auto-serialize a
    list-of-dicts). SQLite accepts the string directly.
    """
    payload = json.dumps([o.model_dump() for o in overrides])
    if is_sqlite:
        return ":overrides", payload
    return "CAST(:overrides AS JSONB)", payload


def _is_sqlite_session(session: AsyncSession) -> bool:
    return (
        session.bind is not None
        and session.bind.dialect.name == "sqlite"
    )


async def create_base_scenario_for_deal(
    session: AsyncSession,
    *,
    deal_id: str | UUID,
    tenant_id: str | UUID,
) -> UUID:
    """Insert the ``is_base=true`` scenario for a freshly-created deal.

    Idempotent: if a base scenario already exists for the deal we
    return its id without inserting a duplicate. The caller (deals
    create endpoint) does NOT commit — it owns the transaction.
    """
    deal_id_str = str(deal_id)
    tenant_id_str = str(tenant_id)
    existing = (
        await session.execute(
            text(
                """
                SELECT id FROM scenarios
                 WHERE deal_id = :deal AND is_base = :is_base
                 LIMIT 1
                """
            ),
            {"deal": deal_id_str, "is_base": True},
        )
    ).first()
    if existing is not None:
        return UUID(str(existing._mapping["id"]))

    scenario_id = uuid4()
    now = _now()
    is_sqlite = _is_sqlite_session(session)
    overrides_sql, overrides_payload = _serialize_overrides_for_db(
        [], is_sqlite=is_sqlite
    )
    await session.execute(
        text(
            f"""
            INSERT INTO scenarios (
                id, deal_id, tenant_id, name, description, is_base,
                overrides, created_at, updated_at
            ) VALUES (
                :id, :deal, :tenant, :name, :description, :is_base,
                {overrides_sql}, :created_at, :updated_at
            )
            """
        ),
        {
            "id": str(scenario_id),
            "deal": deal_id_str,
            "tenant": tenant_id_str,
            "name": "Base",
            "description": "Base case (no overrides on top of deal defaults).",
            "is_base": True,
            "overrides": overrides_payload,
            "created_at": now,
            "updated_at": now,
        },
    )
    logger.info(
        "scenarios.base_created: deal=%s scenario=%s tenant=%s",
        deal_id_str, scenario_id, tenant_id_str,
    )
    return scenario_id


async def _load_scenario(
    session: AsyncSession,
    *,
    scenario_id: UUID,
    deal_id: UUID,
    tenant_id: UUID,
) -> Any:
    """Fetch a scenario row scoped to (deal_id, tenant_id) or 404."""
    row = (
        await session.execute(
            text(
                f"""
                SELECT {_SCENARIO_COLUMNS}
                  FROM scenarios
                 WHERE id = :id
                   AND deal_id = :deal
                   AND tenant_id = :tenant
                """
            ),
            {
                "id": str(scenario_id),
                "deal": str(deal_id),
                "tenant": str(tenant_id),
            },
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"scenario {scenario_id} not found",
        )
    return row


async def load_scenario_overrides(
    session: AsyncSession,
    *,
    scenario_id: str | UUID,
) -> dict[str, Any]:
    """Read the scenario's overrides as a ``{field_path: value}`` dict.

    Returns ``{}`` for non-UUID ids, missing rows, or empty overrides.
    The engine_runner uses this to merge a scenario's overrides on top
    of the deal's persisted ``field_overrides`` at run time. We collapse
    to the same flat ``{path: value}`` shape ``_load_deal_overrides``
    returns so the engine input loader's existing override-routing loop
    sees identical input regardless of source.
    """
    try:
        UUID(str(scenario_id))
    except (ValueError, TypeError):
        return {}
    try:
        row = (
            await session.execute(
                text("SELECT overrides FROM scenarios WHERE id = :id"),
                {"id": str(scenario_id)},
            )
        ).first()
    except Exception:
        return {}
    if row is None:
        return {}
    raw = _coerce_overrides(row._mapping.get("overrides"))
    out: dict[str, Any] = {}
    for entry in raw:
        path = entry.get("field_path")
        if not isinstance(path, str) or not path:
            continue
        out[path] = entry.get("value")
    return out


# ─────────────────────────── routes ───────────────────────────


@router.get(
    "/{deal_id}/scenarios",
    response_model=list[ScenarioRecord],
)
async def list_scenarios(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> list[ScenarioRecord]:
    """Return every scenario for the deal, base first then newest."""
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    rows = await session.execute(
        text(
            f"""
            SELECT {_SCENARIO_COLUMNS}
              FROM scenarios
             WHERE deal_id = :deal AND tenant_id = :tenant
             ORDER BY is_base DESC, created_at ASC
            """
        ),
        {"deal": str(deal_id), "tenant": str(tenant_id)},
    )
    return [_row_to_record(r) for r in rows.fetchall()]


@router.post(
    "/{deal_id}/scenarios",
    response_model=ScenarioRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_scenario(
    deal_id: UUID,
    body: CreateScenarioBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> ScenarioRecord:
    """Create a new named scenario on this deal.

    Returns 409 when the (deal_id, name) pair already exists — the
    analyst can rename or PATCH the existing row instead.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    # Reserved name guard — the auto-created base owns "Base" + the
    # ``is_base=true`` flag and shouldn't be shadowed by a sibling row.
    existing = (
        await session.execute(
            text(
                """
                SELECT 1 FROM scenarios
                 WHERE deal_id = :deal AND name = :name
                """
            ),
            {"deal": str(deal_id), "name": body.name},
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"scenario name {body.name!r} already exists on this deal",
        )

    scenario_id = uuid4()
    now = _now()
    is_sqlite = _is_sqlite_session(session)
    overrides_sql, overrides_payload = _serialize_overrides_for_db(
        body.overrides, is_sqlite=is_sqlite
    )
    await session.execute(
        text(
            f"""
            INSERT INTO scenarios (
                id, deal_id, tenant_id, name, description, is_base,
                overrides, created_at, updated_at
            ) VALUES (
                :id, :deal, :tenant, :name, :description, :is_base,
                {overrides_sql}, :created_at, :updated_at
            )
            """
        ),
        {
            "id": str(scenario_id),
            "deal": str(deal_id),
            "tenant": str(tenant_id),
            "name": body.name,
            "description": body.description,
            "is_base": False,
            "overrides": overrides_payload,
            "created_at": now,
            "updated_at": now,
        },
    )
    # Wave 4 W4.3 — audit emit BEFORE commit so the row + audit entry
    # land in the same transaction. log_audit only flushes (commit
    # ownership stays with this endpoint).
    after_snapshot = {
        "name": body.name,
        "description": body.description,
        "overrides": [o.model_dump() for o in body.overrides],
    }
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        action="scenario.created",
        resource_type="scenario",
        resource_id=str(scenario_id),
        output_payload=after_snapshot,
        after=after_snapshot,
        diff_summary=(
            f"created scenario {body.name!r} "
            f"with {len(body.overrides)} override(s)"
        ),
        tags=["scenario", "wave3"],
        metadata={"deal_id": str(deal_id)},
    )
    await session.commit()
    row = await _load_scenario(
        session, scenario_id=scenario_id, deal_id=deal_id, tenant_id=tenant_id
    )
    logger.info(
        "scenarios.create: deal=%s scenario=%s name=%r overrides=%d",
        deal_id, scenario_id, body.name, len(body.overrides),
    )
    return _row_to_record(row)


@router.get(
    "/{deal_id}/scenarios/{scenario_id}",
    response_model=ScenarioRecord,
)
async def get_scenario(
    deal_id: UUID,
    scenario_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> ScenarioRecord:
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    row = await _load_scenario(
        session, scenario_id=scenario_id, deal_id=deal_id, tenant_id=tenant_id
    )
    return _row_to_record(row)


@router.patch(
    "/{deal_id}/scenarios/{scenario_id}",
    response_model=ScenarioRecord,
)
async def update_scenario(
    deal_id: UUID,
    scenario_id: UUID,
    body: UpdateScenarioBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> ScenarioRecord:
    """Patch name / description / overrides.

    ``overrides`` REPLACES the existing list (not append-on). When the
    target name collides with another scenario on the same deal we
    return 409 — same dedupe contract as create.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    # 404 guard up front so we don't half-write.
    existing_row = await _load_scenario(
        session, scenario_id=scenario_id, deal_id=deal_id, tenant_id=tenant_id
    )
    existing_record = _row_to_record(existing_row)
    before_snapshot = {
        "name": existing_record.name,
        "description": existing_record.description,
        "overrides": [o.model_dump() for o in existing_record.overrides],
    }

    changes = body.model_dump(exclude_unset=True)
    if not changes:
        row = await _load_scenario(
            session, scenario_id=scenario_id, deal_id=deal_id, tenant_id=tenant_id
        )
        return _row_to_record(row)

    if "name" in changes:
        clash = (
            await session.execute(
                text(
                    """
                    SELECT 1 FROM scenarios
                     WHERE deal_id = :deal
                       AND name = :name
                       AND id != :id
                    """
                ),
                {
                    "deal": str(deal_id),
                    "name": changes["name"],
                    "id": str(scenario_id),
                },
            )
        ).first()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"scenario name {changes['name']!r} already exists "
                    "on this deal"
                ),
            )

    is_sqlite = _is_sqlite_session(session)
    set_clauses: list[str] = []
    params: dict[str, Any] = {
        "id": str(scenario_id),
        "deal": str(deal_id),
        "tenant": str(tenant_id),
    }
    if "name" in changes:
        set_clauses.append("name = :name")
        params["name"] = changes["name"]
    if "description" in changes:
        set_clauses.append("description = :description")
        params["description"] = changes["description"]
    if "overrides" in changes:
        # ``changes['overrides']`` is the dumped list of dicts at this
        # point; rebuild the typed list so we get strict validation on
        # the input shape before serializing.
        typed = [ScenarioOverrideBody(**o) for o in changes["overrides"]]
        overrides_sql, overrides_payload = _serialize_overrides_for_db(
            typed, is_sqlite=is_sqlite
        )
        set_clauses.append(f"overrides = {overrides_sql}")
        params["overrides"] = overrides_payload
    now = _now()
    set_clauses.append("updated_at = :updated_at")
    params["updated_at"] = now

    await session.execute(
        text(
            f"""
            UPDATE scenarios
               SET {", ".join(set_clauses)}
             WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant
            """
        ),
        params,
    )
    # Audit the diff BEFORE commit so it lands in the same txn.
    updated_record = _row_to_record(
        await _load_scenario(
            session,
            scenario_id=scenario_id,
            deal_id=deal_id,
            tenant_id=tenant_id,
        )
    )
    after_snapshot = {
        "name": updated_record.name,
        "description": updated_record.description,
        "overrides": [o.model_dump() for o in updated_record.overrides],
    }
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        action="scenario.updated",
        resource_type="scenario",
        resource_id=str(scenario_id),
        input_payload={"changes": sorted(changes.keys())},
        output_payload=after_snapshot,
        before=before_snapshot,
        after=after_snapshot,
        tags=["scenario", "wave3"],
        metadata={"deal_id": str(deal_id)},
    )
    await session.commit()
    row = await _load_scenario(
        session, scenario_id=scenario_id, deal_id=deal_id, tenant_id=tenant_id
    )
    logger.info(
        "scenarios.update: deal=%s scenario=%s fields=%s",
        deal_id, scenario_id, sorted(changes.keys()),
    )
    return _row_to_record(row)


@router.delete(
    "/{deal_id}/scenarios/{scenario_id}",
    response_model=ScenarioRecord,
)
async def delete_scenario(
    deal_id: UUID,
    scenario_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> ScenarioRecord:
    """Delete a scenario. The base scenario cannot be deleted (409)."""
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    row = await _load_scenario(
        session, scenario_id=scenario_id, deal_id=deal_id, tenant_id=tenant_id
    )
    record = _row_to_record(row)
    if record.is_base:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot delete the base scenario",
        )
    await session.execute(
        text(
            "DELETE FROM scenarios "
            "WHERE id = :id AND deal_id = :deal AND tenant_id = :tenant"
        ),
        {
            "id": str(scenario_id),
            "deal": str(deal_id),
            "tenant": str(tenant_id),
        },
    )
    deleted_snapshot = {
        "name": record.name,
        "description": record.description,
        "overrides": [o.model_dump() for o in record.overrides],
    }
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        action="scenario.deleted",
        resource_type="scenario",
        resource_id=str(scenario_id),
        output_payload=deleted_snapshot,
        before=deleted_snapshot,
        diff_summary=f"deleted scenario {record.name!r}",
        tags=["scenario", "wave3"],
        metadata={"deal_id": str(deal_id)},
    )
    await session.commit()
    logger.info("scenarios.delete: deal=%s scenario=%s", deal_id, scenario_id)
    return record


@router.post(
    "/{deal_id}/scenarios/{scenario_id}/run",
    response_model=ScenarioRunResponse,
)
async def run_scenario(
    deal_id: UUID,
    scenario_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> ScenarioRunResponse:
    """Run the engine chain with this scenario's overrides applied.

    Runs synchronously (engines are pure-Python and complete in well
    under a second on the Kimpton fixture). Stamps the run_id back into
    ``scenarios.last_run_id`` so the UI can deep-link without re-running.
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    await _load_scenario(
        session, scenario_id=scenario_id, deal_id=deal_id, tenant_id=tenant_id
    )

    run_id = uuid4()
    started_at = _now()
    results = await run_all_engines(
        session,
        deal_id=str(deal_id),
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        scenario_id=str(scenario_id),
    )

    await session.execute(
        # tenant_id predicate keeps tenant_middleware / Sentry quiet — see
        # apps/worker/app/tenant_middleware.py.
        text(
            """
            UPDATE scenarios
               SET last_run_id = :run_id, updated_at = :updated_at
             WHERE id = :id
               AND tenant_id = :tenant
            """
        ),
        {
            "run_id": str(run_id),
            "id": str(scenario_id),
            "tenant": str(tenant_id),
            "updated_at": _now(),
        },
    )
    # Wave 4 W4.3 — engine_run audit so the Activity Feed surfaces every
    # what-if run alongside the override edits that drove it.
    await log_audit(
        session,
        tenant_id=str(tenant_id),
        action="engine_run.ran_with_scenario",
        resource_type="engine_run",
        resource_id=str(run_id),
        output_payload={
            "engines": sorted(results.keys()),
            "engine_count": len(results),
        },
        diff_summary=(
            f"ran {len(results)} engine(s) "
            f"with scenario {scenario_id}"
        ),
        tags=["engine_run", "scenario"],
        metadata={
            "deal_id": str(deal_id),
            "scenario_id": str(scenario_id),
        },
    )
    await session.commit()
    logger.info(
        "scenarios.run: deal=%s scenario=%s run=%s",
        deal_id, scenario_id, run_id,
    )
    return ScenarioRunResponse(
        scenario_id=scenario_id,
        deal_id=deal_id,
        run_id=run_id,
        started_at=started_at,
        engines=results,
    )


@router.post(
    "/{deal_id}/scenarios/compare",
    response_model=CompareResponse,
)
async def compare_scenarios(
    deal_id: UUID,
    body: CompareRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> CompareResponse:
    """Side-by-side compare of up to 4 scenarios.

    Returns the most-recent persisted engine outputs per scenario
    (``engine_outputs`` rows filtered by each scenario's ``last_run_id``).
    Scenarios that haven't been run yet are auto-run inline so the UI
    never has to render an empty column.

    Tenant-scope guard: every scenario id MUST belong to the requested
    deal AND to the caller's tenant. Cross-tenant ids are rejected with
    404 (never 403 — leaks no information about whether the scenario
    exists on another tenant).
    """
    await _assert_deal_belongs_to_tenant(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    base_row = (
        await session.execute(
            text(
                """
                SELECT id FROM scenarios
                 WHERE deal_id = :deal AND tenant_id = :tenant
                   AND is_base = :is_base
                 LIMIT 1
                """
            ),
            {
                "deal": str(deal_id),
                "tenant": str(tenant_id),
                "is_base": True,
            },
        )
    ).first()
    base_id = UUID(str(base_row._mapping["id"])) if base_row else None

    cells: list[CompareCell] = []
    for sid in body.scenario_ids:
        row = await _load_scenario(
            session, scenario_id=sid, deal_id=deal_id, tenant_id=tenant_id
        )
        record = _row_to_record(row)
        # Auto-run on first compare so columns don't render empty.
        if record.last_run_id is None:
            run_id = uuid4()
            await run_all_engines(
                session,
                deal_id=str(deal_id),
                tenant_id=str(tenant_id),
                run_id=str(run_id),
                scenario_id=str(sid),
            )
            await session.execute(
                # tenant_id predicate keeps tenant_middleware / Sentry quiet
                # — see apps/worker/app/tenant_middleware.py.
                text(
                    """
                    UPDATE scenarios
                       SET last_run_id = :run_id, updated_at = :updated_at
                     WHERE id = :id
                       AND tenant_id = :tenant
                    """
                ),
                {
                    "run_id": str(run_id),
                    "id": str(sid),
                    "tenant": str(tenant_id),
                    "updated_at": _now(),
                },
            )
            await session.commit()
            record = record.model_copy(update={"last_run_id": run_id})

        # Read the persisted engine outputs back out so every column
        # reflects what the DB carries (the source of truth the UI is
        # already polling for elsewhere).
        engines = await get_latest_outputs(
            session, deal_id=str(deal_id), tenant_id=str(tenant_id)
        )
        # Filter to the rows whose run_id matches this scenario's
        # ``last_run_id`` — defends against compare picking up a stale
        # row from a different scenario's older run.
        scoped: dict[str, dict[str, Any]] = {}
        for name in ENGINE_NAMES:
            entry = engines.get(name)
            if entry is None:
                continue
            if (
                record.last_run_id is not None
                and entry.get("run_id")
                and str(entry["run_id"]) != str(record.last_run_id)
            ):
                # The latest persisted row belongs to a different run.
                # Skip rather than render mixed-scenario data.
                continue
            scoped[name] = entry
        cells.append(
            CompareCell(
                scenario_id=record.id,
                scenario_name=record.name,
                is_base=record.is_base,
                last_run_id=record.last_run_id,
                engines=scoped,
            )
        )

    return CompareResponse(
        deal_id=deal_id,
        base_scenario_id=base_id,
        scenarios=cells,
    )
