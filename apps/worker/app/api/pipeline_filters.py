"""Saved pipeline views + scheduled digests API (Wave 4 W4.5).

Two parallel CRUD surfaces, both tenant-scoped via the shared
:func:`apps.worker.app.api.deals.get_tenant_id` resolver.

``/pipeline-views`` — named filter presets the analyst recalls from
the pipeline page. ``POST .../set-default`` pins as the actor's
landing filter; the API unpins the previous default in the same
transaction so only one default exists per (tenant, actor) at a
time.

``/pipeline-digests`` — recurring Slack / email pipeline summaries.
``POST .../run-now`` builds + dispatches the digest immediately for
testing; the in-process scheduler
(:mod:`apps.worker.app.services.digest_scheduler`) drives the
periodic firing path.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..services.pipeline_digest import (
    build_digest_payload,
    compute_next_run_at,
    dispatch_digest,
)
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── shapes ───────────────────────────


class PipelineFilterBody(BaseModel):
    """Mirror of :class:`fondok_schemas.PipelineFilter` for the API."""

    model_config = ConfigDict(extra="forbid")

    state: list[str] | None = None
    min_irr: float | None = None
    max_irr: float | None = None
    min_per_key: float | None = None
    max_per_key: float | None = None
    max_cap_rate: float | None = None
    chain_scales: list[str] | None = None
    sort: str = "last_activity_desc"


class SavedViewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None = None
    filter: PipelineFilterBody
    is_owner_default: bool = False
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class CreateSavedViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)]
    description: Annotated[str, Field(max_length=2000)] | None = None
    filter: PipelineFilterBody
    is_owner_default: bool = False
    created_by: str | None = None


class UpdateSavedViewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    filter: PipelineFilterBody | None = None
    is_owner_default: bool | None = None


class DigestScheduleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    saved_view_id: UUID | None = None
    cadence: str
    weekday: int | None = None
    hour_utc: int
    delivery: str
    slack_webhook_url: str | None = None  # opaque URL — already a secret
    email_recipients: list[str] = Field(default_factory=list)
    include_kpi_summary: bool = True
    include_recently_mutated: bool = True
    include_deals_meeting_target: bool = True
    include_full_table: bool = False
    is_active: bool = True
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


def _validate_emails(value: list[str] | None) -> list[str] | None:
    """Light shape check — full RFC 5322 validation is overkill here."""
    if value is None:
        return value
    for v in value:
        if "@" not in v or "." not in v.split("@", 1)[1]:
            raise ValueError(f"invalid email address: {v!r}")
    return value


class CreateDigestScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)]
    saved_view_id: UUID | None = None
    cadence: str = "daily"
    weekday: int | None = None
    hour_utc: int = 13
    delivery: str = "slack"
    slack_webhook_url: SecretStr | None = None
    email_recipients: list[str] = Field(default_factory=list)
    include_kpi_summary: bool = True
    include_recently_mutated: bool = True
    include_deals_meeting_target: bool = True
    include_full_table: bool = False
    is_active: bool = True

    @field_validator("email_recipients")
    @classmethod
    def _v_emails(cls, value: list[str]) -> list[str]:
        return _validate_emails(value) or []


class UpdateDigestScheduleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    saved_view_id: UUID | None = None
    cadence: str | None = None
    weekday: int | None = None
    hour_utc: int | None = None
    delivery: str | None = None
    slack_webhook_url: SecretStr | None = None
    email_recipients: list[str] | None = None
    include_kpi_summary: bool | None = None
    include_recently_mutated: bool | None = None
    include_deals_meeting_target: bool | None = None
    include_full_table: bool | None = None
    is_active: bool | None = None

    @field_validator("email_recipients")
    @classmethod
    def _v_emails(cls, value: list[str] | None) -> list[str] | None:
        return _validate_emails(value)


class RunNowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: UUID
    dispatched_at: datetime
    slack_attempted: bool
    slack_succeeded: bool
    slack_error: str | None = None
    email_attempted: bool
    email_succeeded: bool
    email_error: str | None = None
    no_op_reason: str | None = None
    deal_count: int


# ─────────────────────────── helpers ───────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _is_sqlite(session: AsyncSession) -> bool:
    return (
        session.bind is not None and session.bind.dialect.name == "sqlite"
    )


def _filter_sql(is_sqlite: bool) -> tuple[str, str]:
    """Return (sql_fragment, payload_label) for inserting the JSON blob."""
    if is_sqlite:
        return ":filter", "filter"
    return "CAST(:filter AS JSONB)", "filter"


def _email_sql(is_sqlite: bool) -> str:
    if is_sqlite:
        return ":email_recipients"
    return "CAST(:email_recipients AS JSONB)"


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            return _now()
    return _now()


def _coerce_optional_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return _coerce_dt(value)


def _decode_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default


def _view_row_to_record(row: Any) -> SavedViewRecord:
    m = row._mapping
    return SavedViewRecord(
        id=UUID(str(m["id"])),
        tenant_id=UUID(str(m["tenant_id"])),
        name=m["name"],
        description=m.get("description"),
        filter=PipelineFilterBody(**_decode_json(m.get("filter"), {})),
        is_owner_default=bool(m.get("is_owner_default")),
        created_by=m.get("created_by"),
        created_at=_coerce_dt(m.get("created_at")),
        updated_at=_coerce_dt(m.get("updated_at")),
    )


def _schedule_row_to_record(row: Any) -> DigestScheduleRecord:
    m = row._mapping
    return DigestScheduleRecord(
        id=UUID(str(m["id"])),
        tenant_id=UUID(str(m["tenant_id"])),
        name=m["name"],
        saved_view_id=(
            UUID(str(m["saved_view_id"])) if m.get("saved_view_id") else None
        ),
        cadence=m.get("cadence") or "daily",
        weekday=m.get("weekday"),
        hour_utc=int(m.get("hour_utc") or 13),
        delivery=m.get("delivery") or "slack",
        slack_webhook_url=m.get("slack_webhook_url") or None,
        email_recipients=_decode_json(m.get("email_recipients"), []),
        include_kpi_summary=bool(m.get("include_kpi_summary")),
        include_recently_mutated=bool(m.get("include_recently_mutated")),
        include_deals_meeting_target=bool(m.get("include_deals_meeting_target")),
        include_full_table=bool(m.get("include_full_table")),
        is_active=bool(m.get("is_active")),
        last_run_at=_coerce_optional_dt(m.get("last_run_at")),
        next_run_at=_coerce_optional_dt(m.get("next_run_at")),
        created_at=_coerce_dt(m.get("created_at")),
        updated_at=_coerce_dt(m.get("updated_at")),
    )


def _schedule_to_dict(record: DigestScheduleRecord) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "tenant_id": str(record.tenant_id),
        "name": record.name,
        "saved_view_id": (
            str(record.saved_view_id) if record.saved_view_id else None
        ),
        "cadence": record.cadence,
        "weekday": record.weekday,
        "hour_utc": record.hour_utc,
        "delivery": record.delivery,
        "slack_webhook_url": record.slack_webhook_url,
        "email_recipients": list(record.email_recipients),
        "include_kpi_summary": record.include_kpi_summary,
        "include_recently_mutated": record.include_recently_mutated,
        "include_deals_meeting_target": record.include_deals_meeting_target,
        "include_full_table": record.include_full_table,
    }


_VIEW_COLUMNS = (
    "id, tenant_id, name, description, filter, is_owner_default, "
    "created_by, created_at, updated_at"
)

_SCHEDULE_COLUMNS = (
    "id, tenant_id, name, saved_view_id, cadence, weekday, hour_utc, "
    "delivery, slack_webhook_url, email_recipients, include_kpi_summary, "
    "include_recently_mutated, include_deals_meeting_target, "
    "include_full_table, is_active, last_run_at, next_run_at, "
    "created_at, updated_at"
)


# ─────────────────────────── saved views ───────────────────────────


@router.get("/pipeline-views", response_model=list[SavedViewRecord])
async def list_views(
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[SavedViewRecord]:
    rows = (
        await session.execute(
            text(
                f"""
                SELECT {_VIEW_COLUMNS}
                  FROM saved_pipeline_views
                 WHERE tenant_id = :tenant
                 ORDER BY is_owner_default DESC, name ASC
                """
            ),
            {"tenant": str(tenant_id)},
        )
    ).fetchall()
    return [_view_row_to_record(r) for r in rows]


@router.post(
    "/pipeline-views",
    response_model=SavedViewRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_view(
    body: CreateSavedViewBody,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedViewRecord:
    existing = (
        await session.execute(
            text(
                """
                SELECT id FROM saved_pipeline_views
                 WHERE tenant_id = :tenant AND name = :name
                """
            ),
            {"tenant": str(tenant_id), "name": body.name},
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"saved view named {body.name!r} already exists",
        )

    is_sqlite = _is_sqlite(session)
    filter_sql, _ = _filter_sql(is_sqlite)
    now = _now()
    view_id = uuid4()
    actor = body.created_by or "system"

    if body.is_owner_default:
        await _unpin_actor_default(session, tenant_id=tenant_id, actor=actor)

    await session.execute(
        text(
            f"""
            INSERT INTO saved_pipeline_views (
                id, tenant_id, name, description, filter,
                is_owner_default, created_by, created_at, updated_at
            ) VALUES (
                :id, :tenant, :name, :description, {filter_sql},
                :is_default, :created_by, :created_at, :updated_at
            )
            """
        ),
        {
            "id": str(view_id),
            "tenant": str(tenant_id),
            "name": body.name,
            "description": body.description,
            "filter": json.dumps(body.filter.model_dump()),
            "is_default": bool(body.is_owner_default),
            "created_by": actor,
            "created_at": now,
            "updated_at": now,
        },
    )
    await session.commit()

    return await _load_view(session, tenant_id=tenant_id, view_id=view_id)


@router.get("/pipeline-views/{view_id}", response_model=SavedViewRecord)
async def get_view(
    view_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedViewRecord:
    return await _load_view(session, tenant_id=tenant_id, view_id=view_id)


@router.patch("/pipeline-views/{view_id}", response_model=SavedViewRecord)
async def update_view(
    view_id: UUID,
    body: UpdateSavedViewBody,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedViewRecord:
    existing = await _load_view(
        session, tenant_id=tenant_id, view_id=view_id
    )
    sets: list[str] = []
    params: dict[str, Any] = {
        "id": str(view_id),
        "tenant": str(tenant_id),
        "updated_at": _now(),
    }
    if body.name is not None and body.name != existing.name:
        clash = (
            await session.execute(
                text(
                    """
                    SELECT id FROM saved_pipeline_views
                     WHERE tenant_id = :tenant AND name = :name AND id != :id
                    """
                ),
                {
                    "tenant": str(tenant_id),
                    "name": body.name,
                    "id": str(view_id),
                },
            )
        ).first()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"saved view named {body.name!r} already exists",
            )
        sets.append("name = :name")
        params["name"] = body.name
    if body.description is not None:
        sets.append("description = :description")
        params["description"] = body.description
    if body.filter is not None:
        is_sqlite = _is_sqlite(session)
        filter_sql, _ = _filter_sql(is_sqlite)
        sets.append(f"filter = {filter_sql}")
        params["filter"] = json.dumps(body.filter.model_dump())
    if body.is_owner_default is True:
        actor = existing.created_by or "system"
        await _unpin_actor_default(
            session, tenant_id=tenant_id, actor=actor
        )
        sets.append("is_owner_default = :is_default")
        params["is_default"] = True
    elif body.is_owner_default is False:
        sets.append("is_owner_default = :is_default")
        params["is_default"] = False
    sets.append("updated_at = :updated_at")

    await session.execute(
        text(
            f"""
            UPDATE saved_pipeline_views
               SET {', '.join(sets)}
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        params,
    )
    await session.commit()
    return await _load_view(session, tenant_id=tenant_id, view_id=view_id)


@router.delete(
    "/pipeline-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_view(
    view_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await _load_view(session, tenant_id=tenant_id, view_id=view_id)
    await session.execute(
        text(
            """
            DELETE FROM saved_pipeline_views
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        {"id": str(view_id), "tenant": str(tenant_id)},
    )
    # Detach this view from any schedule that referenced it — keep the
    # schedule alive (digest still runs against the unfiltered pipeline)
    # rather than silently nuke the analyst's recurring summary.
    await session.execute(
        text(
            """
            UPDATE pipeline_digest_schedules
               SET saved_view_id = NULL, updated_at = :now
             WHERE tenant_id = :tenant AND saved_view_id = :id
            """
        ),
        {"id": str(view_id), "tenant": str(tenant_id), "now": _now()},
    )
    await session.commit()


@router.post(
    "/pipeline-views/{view_id}/set-default",
    response_model=SavedViewRecord,
)
async def set_view_default(
    view_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SavedViewRecord:
    existing = await _load_view(
        session, tenant_id=tenant_id, view_id=view_id
    )
    actor = existing.created_by or "system"
    await _unpin_actor_default(session, tenant_id=tenant_id, actor=actor)
    await session.execute(
        text(
            """
            UPDATE saved_pipeline_views
               SET is_owner_default = :is_default, updated_at = :now
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        {
            "id": str(view_id),
            "tenant": str(tenant_id),
            "is_default": True,
            "now": _now(),
        },
    )
    await session.commit()
    return await _load_view(session, tenant_id=tenant_id, view_id=view_id)


async def _unpin_actor_default(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    actor: str,
) -> None:
    """Unset ``is_owner_default`` on every other view owned by ``actor``.

    Implemented in Python (not via a partial index) so the migration
    stays portable to SQLite. The race window is small — both the
    unpin and the new default set commit inside the same request
    txn — and a duplicate default is a UI nuisance, not a correctness
    bug.
    """
    await session.execute(
        text(
            """
            UPDATE saved_pipeline_views
               SET is_owner_default = :off, updated_at = :now
             WHERE tenant_id = :tenant
               AND COALESCE(created_by, 'system') = :actor
               AND is_owner_default = :on
            """
        ),
        {
            "tenant": str(tenant_id),
            "actor": actor,
            "off": False,
            "on": True,
            "now": _now(),
        },
    )


async def _load_view(
    session: AsyncSession, *, tenant_id: UUID, view_id: UUID
) -> SavedViewRecord:
    row = (
        await session.execute(
            text(
                f"""
                SELECT {_VIEW_COLUMNS}
                  FROM saved_pipeline_views
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(view_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"saved view {view_id} not found",
        )
    return _view_row_to_record(row)


# ─────────────────────────── digest schedules ───────────────────────────


@router.get("/pipeline-digests", response_model=list[DigestScheduleRecord])
async def list_schedules(
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[DigestScheduleRecord]:
    rows = (
        await session.execute(
            text(
                f"""
                SELECT {_SCHEDULE_COLUMNS}
                  FROM pipeline_digest_schedules
                 WHERE tenant_id = :tenant
                 ORDER BY name ASC
                """
            ),
            {"tenant": str(tenant_id)},
        )
    ).fetchall()
    return [_schedule_row_to_record(r) for r in rows]


@router.post(
    "/pipeline-digests",
    response_model=DigestScheduleRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    body: CreateDigestScheduleBody,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DigestScheduleRecord:
    if body.cadence not in ("daily", "weekly", "monthly"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown cadence {body.cadence!r}",
        )
    if body.delivery not in ("slack", "email", "both"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown delivery {body.delivery!r}",
        )

    existing = (
        await session.execute(
            text(
                """
                SELECT id FROM pipeline_digest_schedules
                 WHERE tenant_id = :tenant AND name = :name
                """
            ),
            {"tenant": str(tenant_id), "name": body.name},
        )
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"digest named {body.name!r} already exists",
        )

    is_sqlite = _is_sqlite(session)
    email_sql = _email_sql(is_sqlite)
    now = _now()
    schedule_id = uuid4()
    next_run = compute_next_run_at(
        cadence=body.cadence,
        hour_utc=body.hour_utc,
        weekday=body.weekday,
        now=now,
    )

    await session.execute(
        text(
            f"""
            INSERT INTO pipeline_digest_schedules (
                id, tenant_id, name, saved_view_id, cadence, weekday,
                hour_utc, delivery, slack_webhook_url, email_recipients,
                include_kpi_summary, include_recently_mutated,
                include_deals_meeting_target, include_full_table,
                is_active, next_run_at, created_at, updated_at
            ) VALUES (
                :id, :tenant, :name, :saved_view, :cadence, :weekday,
                :hour_utc, :delivery, :slack, {email_sql},
                :inc_kpi, :inc_recent, :inc_target, :inc_full,
                :is_active, :next_run, :created_at, :updated_at
            )
            """
        ),
        {
            "id": str(schedule_id),
            "tenant": str(tenant_id),
            "name": body.name,
            "saved_view": (
                str(body.saved_view_id) if body.saved_view_id else None
            ),
            "cadence": body.cadence,
            "weekday": body.weekday,
            "hour_utc": body.hour_utc,
            "delivery": body.delivery,
            "slack": (
                body.slack_webhook_url.get_secret_value()
                if body.slack_webhook_url
                else None
            ),
            "email_recipients": json.dumps(
                [str(e) for e in body.email_recipients]
            ),
            "inc_kpi": body.include_kpi_summary,
            "inc_recent": body.include_recently_mutated,
            "inc_target": body.include_deals_meeting_target,
            "inc_full": body.include_full_table,
            "is_active": body.is_active,
            "next_run": next_run,
            "created_at": now,
            "updated_at": now,
        },
    )
    await session.commit()
    return await _load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id
    )


@router.patch(
    "/pipeline-digests/{schedule_id}", response_model=DigestScheduleRecord
)
async def update_schedule(
    schedule_id: UUID,
    body: UpdateDigestScheduleBody,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DigestScheduleRecord:
    existing = await _load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id
    )
    sets: list[str] = []
    params: dict[str, Any] = {
        "id": str(schedule_id),
        "tenant": str(tenant_id),
        "updated_at": _now(),
    }
    new_cadence = body.cadence or existing.cadence
    new_hour = body.hour_utc if body.hour_utc is not None else existing.hour_utc
    new_weekday = (
        body.weekday if body.weekday is not None else existing.weekday
    )
    cadence_changed = (
        body.cadence is not None
        or body.hour_utc is not None
        or body.weekday is not None
    )
    if body.name is not None and body.name != existing.name:
        clash = (
            await session.execute(
                text(
                    """
                    SELECT id FROM pipeline_digest_schedules
                     WHERE tenant_id = :tenant AND name = :name AND id != :id
                    """
                ),
                {
                    "tenant": str(tenant_id),
                    "name": body.name,
                    "id": str(schedule_id),
                },
            )
        ).first()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"digest named {body.name!r} already exists",
            )
        sets.append("name = :name")
        params["name"] = body.name
    if body.saved_view_id is not None:
        sets.append("saved_view_id = :saved_view")
        params["saved_view"] = str(body.saved_view_id)
    if body.cadence is not None:
        sets.append("cadence = :cadence")
        params["cadence"] = body.cadence
    if body.weekday is not None:
        sets.append("weekday = :weekday")
        params["weekday"] = body.weekday
    if body.hour_utc is not None:
        sets.append("hour_utc = :hour_utc")
        params["hour_utc"] = body.hour_utc
    if body.delivery is not None:
        sets.append("delivery = :delivery")
        params["delivery"] = body.delivery
    if body.slack_webhook_url is not None:
        sets.append("slack_webhook_url = :slack")
        params["slack"] = body.slack_webhook_url.get_secret_value()
    if body.email_recipients is not None:
        is_sqlite = _is_sqlite(session)
        email_sql = _email_sql(is_sqlite)
        sets.append(f"email_recipients = {email_sql}")
        params["email_recipients"] = json.dumps(
            [str(e) for e in body.email_recipients]
        )
    for attr, col in (
        ("include_kpi_summary", "include_kpi_summary"),
        ("include_recently_mutated", "include_recently_mutated"),
        ("include_deals_meeting_target", "include_deals_meeting_target"),
        ("include_full_table", "include_full_table"),
        ("is_active", "is_active"),
    ):
        val = getattr(body, attr)
        if val is not None:
            sets.append(f"{col} = :{col}")
            params[col] = val
    if cadence_changed:
        sets.append("next_run_at = :next_run")
        params["next_run"] = compute_next_run_at(
            cadence=new_cadence,
            hour_utc=new_hour,
            weekday=new_weekday,
        )
    sets.append("updated_at = :updated_at")

    await session.execute(
        text(
            f"""
            UPDATE pipeline_digest_schedules
               SET {', '.join(sets)}
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        params,
    )
    await session.commit()
    return await _load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id
    )


@router.delete(
    "/pipeline-digests/{schedule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_schedule(
    schedule_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> None:
    await _load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id
    )
    await session.execute(
        text(
            """
            DELETE FROM pipeline_digest_schedules
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        {"id": str(schedule_id), "tenant": str(tenant_id)},
    )
    await session.commit()


@router.post(
    "/pipeline-digests/{schedule_id}/run-now", response_model=RunNowResponse
)
async def run_schedule_now(
    schedule_id: UUID,
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RunNowResponse:
    record = await _load_schedule(
        session, tenant_id=tenant_id, schedule_id=schedule_id
    )
    schedule_dict = _schedule_to_dict(record)
    payload = await build_digest_payload(
        session, tenant_id=tenant_id, schedule=schedule_dict
    )
    result = dispatch_digest(schedule_dict, payload)
    now = _now()
    await session.execute(
        text(
            """
            UPDATE pipeline_digest_schedules
               SET last_run_at = :now, updated_at = :now
             WHERE id = :id AND tenant_id = :tenant
            """
        ),
        {
            "id": str(schedule_id),
            "tenant": str(tenant_id),
            "now": now,
        },
    )
    await session.commit()
    return RunNowResponse(
        schedule_id=schedule_id,
        dispatched_at=now,
        slack_attempted=result.slack_attempted,
        slack_succeeded=result.slack_succeeded,
        slack_error=result.slack_error,
        email_attempted=result.email_attempted,
        email_succeeded=result.email_succeeded,
        email_error=result.email_error,
        no_op_reason=result.no_op_reason,
        deal_count=payload.deal_count,
    )


async def _load_schedule(
    session: AsyncSession, *, tenant_id: UUID, schedule_id: UUID
) -> DigestScheduleRecord:
    row = (
        await session.execute(
            text(
                f"""
                SELECT {_SCHEDULE_COLUMNS}
                  FROM pipeline_digest_schedules
                 WHERE id = :id AND tenant_id = :tenant
                """
            ),
            {"id": str(schedule_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"digest schedule {schedule_id} not found",
        )
    return _schedule_row_to_record(row)


__all__ = ["router"]
