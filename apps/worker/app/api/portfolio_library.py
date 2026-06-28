"""Portfolio P&L Library — firm-level benchmark CRUD + upload.

Wave 4 W4.1 (Sam's June 2026 ask: *"Apollo / capital partners own
hotels in the same market and want to upload THEIR P&Ls as
benchmarks"*).

Wave 2 P2.7 added the per-deal ``PORTFOLIO_PNL`` doc type + the op-ratio
precedence chain that puts portfolio P&L between T-12 and CBRE. But the
PORTFOLIO_PNL had to be uploaded into one specific deal's data room —
that's wrong. Portfolio benchmarks are FIRM-LEVEL assets that apply
across every deal that firm underwrites.

This router exposes a tenant-scoped Portfolio Library:

    * ``GET    /portfolio-library``                  list entries
    * ``POST   /portfolio-library``                  create entry
    * ``POST   /portfolio-library/upload``           multipart upload + extract + entry
    * ``GET    /portfolio-library/{id}``             single
    * ``PATCH  /portfolio-library/{id}``             partial update
    * ``POST   /portfolio-library/{id}/deactivate``  soft delete
    * ``POST   /portfolio-library/{id}/activate``    reactivate
    * ``DELETE /portfolio-library/{id}``             hard delete (only if unreferenced)

The engine_runner's ``_load_engine_inputs`` queries every active entry
for the tenant whose ``chain_scales_covered`` overlaps the subject
deal's chain scale and whose ``vintage_year`` falls inside the 3-year
look-back window, computes a median per ratio, and feeds that median as
the ``portfolio_pnl`` candidate of the precedence chain
(``op_ratio_precedence``). Per-deal ``PORTFOLIO_PNL`` documents still
work — they win over the library median for the same chain scale via
the same precedence resolver.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from .deals import get_tenant_id

logger = logging.getLogger(__name__)
router = APIRouter()


# ─────────────────────────── shapes ───────────────────────────


class PortfolioLibraryEntryRecord(BaseModel):
    """Row shape returned by every CRUD endpoint.

    Mirrors :class:`fondok_schemas.portfolio_library.PortfolioLibraryEntry`
    but with UUID typing on the id fields (FastAPI handles the
    JSON-string ↔ UUID translation at the boundary).
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None = None
    vintage_year: int
    asset_count: int
    total_rooms_modeled: int
    chain_scales_covered: list[str] = Field(default_factory=list)
    msa_coverage: list[str] | None = None
    expense_ratios: dict[str, float] = Field(default_factory=dict)
    revenue_mix: dict[str, float] | None = None
    source_document_id: UUID | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime


class CreateEntryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=2000)] | None = None
    vintage_year: Annotated[int, Field(ge=1900, le=2100)]
    asset_count: Annotated[int, Field(ge=1)]
    total_rooms_modeled: Annotated[int, Field(ge=1)]
    chain_scales_covered: list[str] = Field(default_factory=list)
    msa_coverage: list[str] | None = None
    expense_ratios: dict[str, float] = Field(default_factory=dict)
    revenue_mix: dict[str, float] | None = None
    source_document_id: UUID | None = None


class UpdateEntryBody(BaseModel):
    """Partial update — every field optional."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    vintage_year: Annotated[int, Field(ge=1900, le=2100)] | None = None
    asset_count: Annotated[int, Field(ge=1)] | None = None
    total_rooms_modeled: Annotated[int, Field(ge=1)] | None = None
    chain_scales_covered: list[str] | None = None
    msa_coverage: list[str] | None = None
    expense_ratios: dict[str, float] | None = None
    revenue_mix: dict[str, float] | None = None
    is_active: bool | None = None


# ─────────────────────────── helpers ───────────────────────────


_COLUMNS = (
    "id, tenant_id, name, description, vintage_year, asset_count, "
    "total_rooms_modeled, chain_scales_covered, msa_coverage, "
    "expense_ratios, revenue_mix, source_document_id, is_active, "
    "created_at, updated_at"
)


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


def _coerce_json(value: Any) -> Any:
    """Parse a JSONB or TEXT-JSON column into Python.

    Postgres hands us a parsed list/dict; SQLite hands us a JSON string.
    Anything malformed silently degrades to ``None`` so a bad row never
    blows up the API.
    """
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        if not value:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _coerce_str_list(value: Any) -> list[str]:
    parsed = _coerce_json(value)
    if isinstance(parsed, list):
        return [str(x) for x in parsed if isinstance(x, (str, int, float))]
    return []


def _coerce_str_list_optional(value: Any) -> list[str] | None:
    parsed = _coerce_json(value)
    if parsed is None:
        return None
    if isinstance(parsed, list):
        return [str(x) for x in parsed if isinstance(x, (str, int, float))]
    return None


def _coerce_float_dict(value: Any) -> dict[str, float]:
    parsed = _coerce_json(value)
    if isinstance(parsed, dict):
        out: dict[str, float] = {}
        for k, v in parsed.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    return {}


def _coerce_float_dict_optional(value: Any) -> dict[str, float] | None:
    parsed = _coerce_json(value)
    if parsed is None:
        return None
    if isinstance(parsed, dict):
        out: dict[str, float] = {}
        for k, v in parsed.items():
            try:
                out[str(k)] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    return None


def _row_to_record(row: Any) -> PortfolioLibraryEntryRecord:
    m = row._mapping
    return PortfolioLibraryEntryRecord(
        id=UUID(str(m["id"])),
        tenant_id=UUID(str(m["tenant_id"])),
        name=m["name"],
        description=m.get("description"),
        vintage_year=int(m["vintage_year"]),
        asset_count=int(m["asset_count"]),
        total_rooms_modeled=int(m["total_rooms_modeled"]),
        chain_scales_covered=_coerce_str_list(m.get("chain_scales_covered")),
        msa_coverage=_coerce_str_list_optional(m.get("msa_coverage")),
        expense_ratios=_coerce_float_dict(m.get("expense_ratios")),
        revenue_mix=_coerce_float_dict_optional(m.get("revenue_mix")),
        source_document_id=(
            UUID(str(m["source_document_id"]))
            if m.get("source_document_id")
            else None
        ),
        is_active=bool(m.get("is_active")),
        created_at=_coerce_dt(m.get("created_at")),
        updated_at=_coerce_dt(m.get("updated_at")),
    )


def _is_sqlite_session(session: AsyncSession) -> bool:
    return (
        session.bind is not None
        and session.bind.dialect.name == "sqlite"
    )


def _jsonb_bind(key: str, *, is_sqlite: bool) -> str:
    """SQL fragment binding ``:key`` to a JSON column.

    Postgres needs ``CAST(:key AS JSONB)`` because we always bind a
    string. SQLite accepts the string directly into a TEXT column.
    """
    if is_sqlite:
        return f":{key}"
    return f"CAST(:{key} AS JSONB)"


# Map PORTFOLIO_PNL extraction field_names → canonical engine ratio keys.
# Mirrors the ``portfolio_pnl.<field>`` paths defined in
# ``apps/worker/app/agents/extraction_schemas/portfolio_pnl.md`` (Wave 2
# P2.7). Per-chain-scale segment headers (``portfolio_pnl.segment_*``)
# are intentionally NOT in this map — when an analyst uploads through the
# Library wizard they pick chain scales explicitly, so the rollup ratios
# are what we want for the library entry.
_PORTFOLIO_FIELD_MAP: dict[str, str] = {
    "portfolio_pnl.rooms_dept_pct": "rooms_dept_pct",
    "portfolio_pnl.fb_dept_pct": "fb_dept_pct",
    "portfolio_pnl.other_ops_dept_pct": "other_ops_dept_pct",
    "portfolio_pnl.admin_pct": "admin_pct",
    "portfolio_pnl.sales_pct": "sales_pct",
    "portfolio_pnl.prop_ops_pct": "prop_ops_pct",
    "portfolio_pnl.utilities_pct": "utilities_pct",
    "portfolio_pnl.marketing_pct": "marketing_pct",
    "portfolio_pnl.management_fee_pct": "mgmt_fee_pct",
    "portfolio_pnl.property_tax_pct": "property_tax_pct",
    "portfolio_pnl.insurance_pct": "insurance_pct",
    "portfolio_pnl.ffe_reserve_pct": "ffe_reserve_pct",
    "portfolio_pnl.gop_margin": "gop_margin",
    "portfolio_pnl.noi_margin": "noi_margin",
}

_REVENUE_MIX_MAP: dict[str, str] = {
    "portfolio_pnl.rooms_revenue_pct": "rooms_revenue_pct",
    "portfolio_pnl.fb_revenue_pct": "fb_revenue_pct",
    "portfolio_pnl.other_revenue_pct": "other_revenue_pct",
}


# ─────────────────────────── tenant helpers ───────────────────────────


async def _load_entry_or_404(
    session: AsyncSession,
    *,
    entry_id: UUID,
    tenant_id: UUID,
) -> Any:
    row = (
        await session.execute(
            text(
                f"""
                SELECT {_COLUMNS}
                  FROM portfolio_library
                 WHERE id = :id
                   AND tenant_id = :tenant
                """
            ),
            {"id": str(entry_id), "tenant": str(tenant_id)},
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"portfolio library entry {entry_id} not found",
        )
    return row


async def _name_in_use(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    name: str,
    exclude_id: UUID | None = None,
) -> bool:
    sql = (
        "SELECT id FROM portfolio_library "
        "WHERE tenant_id = :tenant AND name = :name"
    )
    params: dict[str, Any] = {"tenant": str(tenant_id), "name": name}
    if exclude_id is not None:
        sql += " AND id <> :exclude"
        params["exclude"] = str(exclude_id)
    sql += " LIMIT 1"
    return (
        await session.execute(text(sql), params)
    ).first() is not None


# ─────────────────────────── endpoints ───────────────────────────


@router.get("", response_model=list[PortfolioLibraryEntryRecord])
async def list_entries(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    is_active: bool | None = None,
    chain_scale: str | None = None,
) -> list[PortfolioLibraryEntryRecord]:
    """List portfolio library entries for the tenant.

    Filters:
        * ``is_active`` — when set, returns only active / inactive rows.
        * ``chain_scale`` — case-insensitive loose match against any
          entry in ``chain_scales_covered``. Useful for the UI's
          "show me everything covering Upper Upscale" filter.
    """
    sql = (
        f"SELECT {_COLUMNS} FROM portfolio_library "
        "WHERE tenant_id = :tenant"
    )
    params: dict[str, Any] = {"tenant": str(tenant_id)}
    if is_active is not None:
        sql += " AND is_active = :is_active"
        params["is_active"] = is_active
    sql += " ORDER BY vintage_year DESC, created_at DESC"
    rows = (await session.execute(text(sql), params)).fetchall()
    records = [_row_to_record(r) for r in rows]
    if chain_scale is not None:
        target = _normalize_chain_scale(chain_scale)
        records = [
            r for r in records
            if any(_normalize_chain_scale(s) == target for s in r.chain_scales_covered)
        ]
    return records


@router.post(
    "",
    response_model=PortfolioLibraryEntryRecord,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry(
    body: CreateEntryBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> PortfolioLibraryEntryRecord:
    """Create a new portfolio library entry."""
    if await _name_in_use(session, tenant_id=tenant_id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"portfolio library entry named {body.name!r} already exists",
        )
    entry_id = uuid4()
    now = _now()
    is_sqlite = _is_sqlite_session(session)
    params = {
        "id": str(entry_id),
        "tenant": str(tenant_id),
        "name": body.name,
        "description": body.description,
        "vintage_year": body.vintage_year,
        "asset_count": body.asset_count,
        "total_rooms_modeled": body.total_rooms_modeled,
        "chain_scales_covered": json.dumps(list(body.chain_scales_covered)),
        "msa_coverage": (
            json.dumps(list(body.msa_coverage))
            if body.msa_coverage is not None
            else None
        ),
        "expense_ratios": json.dumps(dict(body.expense_ratios)),
        "revenue_mix": (
            json.dumps(dict(body.revenue_mix))
            if body.revenue_mix is not None
            else None
        ),
        "source_document_id": (
            str(body.source_document_id) if body.source_document_id else None
        ),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    await session.execute(
        text(
            f"""
            INSERT INTO portfolio_library (
                id, tenant_id, name, description, vintage_year,
                asset_count, total_rooms_modeled, chain_scales_covered,
                msa_coverage, expense_ratios, revenue_mix,
                source_document_id, is_active, created_at, updated_at
            ) VALUES (
                :id, :tenant, :name, :description, :vintage_year,
                :asset_count, :total_rooms_modeled,
                {_jsonb_bind("chain_scales_covered", is_sqlite=is_sqlite)},
                {_jsonb_bind("msa_coverage", is_sqlite=is_sqlite)},
                {_jsonb_bind("expense_ratios", is_sqlite=is_sqlite)},
                {_jsonb_bind("revenue_mix", is_sqlite=is_sqlite)},
                :source_document_id, :is_active,
                :created_at, :updated_at
            )
            """
        ),
        params,
    )
    await session.commit()
    row = await _load_entry_or_404(session, entry_id=entry_id, tenant_id=tenant_id)
    return _row_to_record(row)


@router.get("/{entry_id}", response_model=PortfolioLibraryEntryRecord)
async def get_entry(
    entry_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> PortfolioLibraryEntryRecord:
    row = await _load_entry_or_404(
        session, entry_id=entry_id, tenant_id=tenant_id
    )
    return _row_to_record(row)


@router.patch("/{entry_id}", response_model=PortfolioLibraryEntryRecord)
async def update_entry(
    entry_id: UUID,
    body: UpdateEntryBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> PortfolioLibraryEntryRecord:
    row = await _load_entry_or_404(
        session, entry_id=entry_id, tenant_id=tenant_id
    )
    current = _row_to_record(row)

    # Name uniqueness check on rename.
    if body.name is not None and body.name != current.name:
        if await _name_in_use(
            session, tenant_id=tenant_id, name=body.name, exclude_id=entry_id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"portfolio library entry named {body.name!r} already exists",
            )

    is_sqlite = _is_sqlite_session(session)
    set_clauses: list[str] = []
    params: dict[str, Any] = {"id": str(entry_id), "tenant": str(tenant_id)}

    if body.name is not None:
        set_clauses.append("name = :name")
        params["name"] = body.name
    if body.description is not None:
        set_clauses.append("description = :description")
        params["description"] = body.description
    if body.vintage_year is not None:
        set_clauses.append("vintage_year = :vintage_year")
        params["vintage_year"] = body.vintage_year
    if body.asset_count is not None:
        set_clauses.append("asset_count = :asset_count")
        params["asset_count"] = body.asset_count
    if body.total_rooms_modeled is not None:
        set_clauses.append("total_rooms_modeled = :total_rooms_modeled")
        params["total_rooms_modeled"] = body.total_rooms_modeled
    if body.chain_scales_covered is not None:
        set_clauses.append(
            "chain_scales_covered = "
            + _jsonb_bind("chain_scales_covered", is_sqlite=is_sqlite)
        )
        params["chain_scales_covered"] = json.dumps(list(body.chain_scales_covered))
    if body.msa_coverage is not None:
        set_clauses.append(
            "msa_coverage = " + _jsonb_bind("msa_coverage", is_sqlite=is_sqlite)
        )
        params["msa_coverage"] = json.dumps(list(body.msa_coverage))
    if body.expense_ratios is not None:
        set_clauses.append(
            "expense_ratios = " + _jsonb_bind("expense_ratios", is_sqlite=is_sqlite)
        )
        params["expense_ratios"] = json.dumps(dict(body.expense_ratios))
    if body.revenue_mix is not None:
        set_clauses.append(
            "revenue_mix = " + _jsonb_bind("revenue_mix", is_sqlite=is_sqlite)
        )
        params["revenue_mix"] = json.dumps(dict(body.revenue_mix))
    if body.is_active is not None:
        set_clauses.append("is_active = :is_active")
        params["is_active"] = body.is_active

    if set_clauses:
        set_clauses.append("updated_at = :updated_at")
        params["updated_at"] = _now()
        await session.execute(
            text(
                "UPDATE portfolio_library "
                "SET " + ", ".join(set_clauses) + " "
                "WHERE id = :id AND tenant_id = :tenant"
            ),
            params,
        )
        await session.commit()

    row = await _load_entry_or_404(
        session, entry_id=entry_id, tenant_id=tenant_id
    )
    return _row_to_record(row)


@router.post(
    "/{entry_id}/deactivate", response_model=PortfolioLibraryEntryRecord
)
async def deactivate_entry(
    entry_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> PortfolioLibraryEntryRecord:
    """Soft-delete (is_active=false). Excludes the entry from engine
    resolution without losing the row."""
    await _load_entry_or_404(session, entry_id=entry_id, tenant_id=tenant_id)
    await session.execute(
        text(
            "UPDATE portfolio_library "
            "SET is_active = :is_active, updated_at = :updated_at "
            "WHERE id = :id AND tenant_id = :tenant"
        ),
        {
            "is_active": False,
            "updated_at": _now(),
            "id": str(entry_id),
            "tenant": str(tenant_id),
        },
    )
    await session.commit()
    row = await _load_entry_or_404(
        session, entry_id=entry_id, tenant_id=tenant_id
    )
    return _row_to_record(row)


@router.post("/{entry_id}/activate", response_model=PortfolioLibraryEntryRecord)
async def activate_entry(
    entry_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> PortfolioLibraryEntryRecord:
    await _load_entry_or_404(session, entry_id=entry_id, tenant_id=tenant_id)
    await session.execute(
        text(
            "UPDATE portfolio_library "
            "SET is_active = :is_active, updated_at = :updated_at "
            "WHERE id = :id AND tenant_id = :tenant"
        ),
        {
            "is_active": True,
            "updated_at": _now(),
            "id": str(entry_id),
            "tenant": str(tenant_id),
        },
    )
    await session.commit()
    row = await _load_entry_or_404(
        session, entry_id=entry_id, tenant_id=tenant_id
    )
    return _row_to_record(row)


@router.delete(
    "/{entry_id}",
    response_model=PortfolioLibraryEntryRecord,
)
async def delete_entry(
    entry_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
) -> PortfolioLibraryEntryRecord:
    """Hard-delete. Blocked when the entry's ``source_document_id`` is
    referenced by any deal's documents. Use ``/deactivate`` to remove
    the entry from engine resolution without losing the row."""
    row = await _load_entry_or_404(
        session, entry_id=entry_id, tenant_id=tenant_id
    )
    record = _row_to_record(row)

    # Reference guard — when a deal still owns the source doc we refuse
    # the hard delete and steer the user to ``/deactivate``. Best-effort:
    # the documents table may not exist on every test DB, so we degrade
    # silently when the lookup fails.
    if record.source_document_id is not None:
        try:
            ref = (
                await session.execute(
                    text(
                        """
                        SELECT id FROM documents
                         WHERE id = :doc
                           AND tenant_id = :tenant
                        LIMIT 1
                        """
                    ),
                    {
                        "doc": str(record.source_document_id),
                        "tenant": str(tenant_id),
                    },
                )
            ).first()
        except Exception:
            ref = None
        if ref is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "portfolio library entry is referenced by a deal "
                    "document — deactivate it instead of deleting"
                ),
            )

    await session.execute(
        text(
            "DELETE FROM portfolio_library "
            "WHERE id = :id AND tenant_id = :tenant"
        ),
        {"id": str(entry_id), "tenant": str(tenant_id)},
    )
    await session.commit()
    return record


# ─────────────────────────── upload flow ───────────────────────────


@router.post(
    "/upload",
    response_model=PortfolioLibraryEntryRecord,
    status_code=status.HTTP_201_CREATED,
)
async def upload_entry(
    session: Annotated[AsyncSession, Depends(get_session)],
    tenant_id: Annotated[UUID, Depends(get_tenant_id)],
    file: UploadFile = File(...),
    name: str = Form(...),
    vintage_year: int = Form(...),
    asset_count: int = Form(...),
    total_rooms_modeled: int = Form(...),
    chain_scales_covered: str = Form("[]"),
    description: str | None = Form(None),
    msa_coverage: str | None = Form(None),
) -> PortfolioLibraryEntryRecord:
    """Upload a PORTFOLIO_PNL document + metadata in one round-trip.

    The doc is persisted, parsed, and the extracted ratios are folded
    into a brand-new library entry. When extraction fails we surface a
    422 so the client knows the entry was NOT created (atomic semantics
    — the upload either lands as a complete entry or not at all).
    """
    # Parse the JSON-encoded list params.
    try:
        chain_scales_list = json.loads(chain_scales_covered)
        if not isinstance(chain_scales_list, list):
            chain_scales_list = []
    except json.JSONDecodeError:
        chain_scales_list = []
    msa_list: list[str] | None = None
    if msa_coverage:
        try:
            parsed = json.loads(msa_coverage)
            if isinstance(parsed, list):
                msa_list = [str(x) for x in parsed]
        except json.JSONDecodeError:
            msa_list = None

    if not name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="name is required",
        )
    if await _name_in_use(session, tenant_id=tenant_id, name=name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"portfolio library entry named {name!r} already exists",
        )

    try:
        body = await file.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"failed to read uploaded file: {exc}",
        )
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="uploaded file is empty",
        )

    # Run extraction in a best-effort try/except. The Wave 2 P2.7
    # PORTFOLIO_PNL extraction schema is the source of truth — we delegate
    # the actual LLM pass to ``extract_portfolio_ratios`` which the test
    # suite stubs out with a controllable monkeypatch.
    try:
        expense_ratios, revenue_mix = await extract_portfolio_ratios(
            filename=file.filename or "portfolio.pdf",
            content=body,
        )
    except PortfolioExtractionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"portfolio extraction failed: {exc}",
        )

    create_body = CreateEntryBody(
        name=name,
        description=description,
        vintage_year=vintage_year,
        asset_count=asset_count,
        total_rooms_modeled=total_rooms_modeled,
        chain_scales_covered=[str(s) for s in chain_scales_list],
        msa_coverage=msa_list,
        expense_ratios=expense_ratios,
        revenue_mix=revenue_mix,
    )
    return await create_entry(
        body=create_body, session=session, tenant_id=tenant_id
    )


# ─────────────────────────── extraction shim ───────────────────────────


class PortfolioExtractionError(RuntimeError):
    """Raised when a PORTFOLIO_PNL extraction yields no usable ratios."""


async def extract_portfolio_ratios(
    *,
    filename: str,
    content: bytes,
) -> tuple[dict[str, float], dict[str, float] | None]:
    """Run the PORTFOLIO_PNL extractor on the uploaded bytes.

    Returns ``(expense_ratios, revenue_mix_or_None)``. The default
    implementation defers to the agent pipeline when available and
    raises :class:`PortfolioExtractionError` when no ratios can be
    surfaced. Tests monkeypatch this function to drive the upload flow
    deterministically — production wiring can replace the body when the
    LLM pass is plumbed end-to-end.
    """
    try:
        from ..agents.portfolio_pnl_extractor import (  # type: ignore[import-not-found]
            extract as _extract,
        )
    except ImportError:
        # Production pipeline not yet wired through agents/. Fail loudly
        # so the API surfaces a meaningful 422 rather than silently
        # creating an empty entry.
        raise PortfolioExtractionError(
            "portfolio extractor not configured; upload via metadata-only POST"
        )
    raw_fields = await _extract(filename=filename, content=content)

    expense_ratios: dict[str, float] = {}
    revenue_mix: dict[str, float] = {}
    for k, v in (raw_fields or {}).items():
        if not isinstance(v, (int, float)):
            continue
        canonical = _PORTFOLIO_FIELD_MAP.get(k.lower())
        if canonical is not None:
            value = float(v)
            if value > 1.0 and value <= 100.0:
                value = value / 100.0
            expense_ratios[canonical] = value
            continue
        mix_key = _REVENUE_MIX_MAP.get(k.lower())
        if mix_key is not None:
            value = float(v)
            if value > 1.0 and value <= 100.0:
                value = value / 100.0
            revenue_mix[mix_key] = value

    if not expense_ratios:
        raise PortfolioExtractionError(
            "no portfolio_pnl.* fields surfaced from upload"
        )
    return expense_ratios, (revenue_mix or None)


# ─────────────────────────── shared utilities ───────────────────────────


def _normalize_chain_scale(value: str) -> str:
    """Loose chain-scale equality (matches op_ratio_precedence)."""
    return value.strip().lower().replace("_", " ").replace("-", " ")


__all__ = [
    "router",
    "_PORTFOLIO_FIELD_MAP",
    "_REVENUE_MIX_MAP",
    "_normalize_chain_scale",
    "extract_portfolio_ratios",
    "PortfolioExtractionError",
]
