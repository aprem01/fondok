"""FON-41 E-013 / E-017 — P&L Excel round-trip (export → edit → validated re-import).

Routes (mounted under ``/deals``; every one tenant-scoped through
``get_current_auth`` + ``_assert_deal_belongs_to_tenant``):

* ``GET  /{deal_id}/exports/historicals.xlsx`` — Historical P&L, stable ids.
* ``POST /{deal_id}/imports/historicals`` — upload → preview (never writes).
* ``POST /{deal_id}/imports/historicals/apply`` — apply previewed changes.
  Each change goes through :func:`app.api.documents.review_extraction_field`
  with ``action="edit"`` — the SAME function a manual cell correction calls —
  so it is marked ``reviewed="edited"``, re-scores confidence and writes the
  ``extraction_field.edit`` audit row exactly as the Data Room review does.
* ``GET  /{deal_id}/exports/projections.xlsx`` — Future P&L + Assumptions.
* ``POST /{deal_id}/imports/projections`` — upload → preview (never writes).
* ``POST /{deal_id}/imports/projections/apply`` — apply previewed assumption
  changes through :func:`app.api.deals.update_deal` (``PATCH /deals/{id}``,
  the existing ``field_overrides`` save path) so the FON-74 note gate and the
  ``override.set`` audit diff apply unchanged.

Preview and Apply are separate calls on purpose: nothing an upload says is
written until the analyst has seen the diff. Apply re-checks every change
against the CURRENT value and skips (reports) any cell that moved since the
preview rather than overwriting someone else's correction.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import log_audit
from ..auth import AuthContext, get_current_auth
from ..database import get_session
from ..export.pl_workbooks import (
    ASSUMPTION_BY_KEY,
    ASSUMPTIONS,
    HIST_LINES,
    assumption_spec,
    by_year_target_assumptions,
    split_by_year_target_key,
    SCHEDULE_KEY,
    schedule_assumptions,
    split_schedule_period_key,
    HistCell,
    HistColumn,
    HistCurrent,
    ProjectionExtras,
    WorkbookFormatError,
    benchmark_column_values,
    build_historicals_workbook,
    build_projections_workbook,
    hist_cell_id,
    parse_historicals_import,
    parse_projections_import,
    split_projection_method_key,
    projection_extras,
    projection_values,
    same_number,
    split_hist_cell_id,
    to_number,
)
from .deals import _assert_deal_belongs_to_tenant
from .documents import load_market_data

logger = logging.getLogger(__name__)
router = APIRouter()

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


# ─────────────────────────────── shared ───────────────────────────────


async def _deal_name(session: AsyncSession, deal_id: UUID, tenant: str) -> str | None:
    row = (
        await session.execute(
            text("SELECT name FROM deals WHERE id = :id AND tenant_id = :tenant"),
            {"id": str(deal_id), "tenant": tenant},
        )
    ).first()
    return row._mapping["name"] if row is not None else None


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "workbook is over 5 MB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty upload")
    return data


def _xlsx_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _format_error(exc: WorkbookFormatError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": "workbook_format", "message": str(exc)},
    )


# ─────────────────────────── historical state ───────────────────────────


def _is_pnl_doc_type(doc_type: str | None) -> bool:
    """Mirror of the web's ``isPnlDoc`` (``lib/hooks/useHistoricals.ts``)."""
    dt = (doc_type or "").upper()
    return "T12" in dt or dt in ("T-12", "PNL", "P&L") or "PROFIT" in dt


async def _load_historical_docs(
    session: AsyncSession, deal_id: UUID, tenant: str
) -> list[dict[str, Any]]:
    """Latest extraction of every EXTRACTED P&L / T-12 statement on the deal."""
    from ..extraction.terse_schema import read_extraction_fields

    rows = await session.execute(
        text(
            """
            SELECT d.id AS document_id, d.filename, d.doc_type, d.fiscal_year,
                   d.uploaded_at, er.fields, er.catalog_version, er.created_at
              FROM documents d
              JOIN extraction_results er ON er.document_id = d.id
             WHERE d.deal_id = :deal
               AND d.tenant_id = :tenant
               AND er.deal_id = :deal
               AND er.tenant_id = :tenant
               AND UPPER(COALESCE(d.status, '')) = 'EXTRACTED'
             ORDER BY er.created_at DESC
            """
        ),
        {"deal": str(deal_id), "tenant": tenant},
    )
    seen: set[str] = set()
    docs: list[dict[str, Any]] = []
    for r in rows.fetchall():
        m = r._mapping
        doc_id = str(m["document_id"])
        if doc_id in seen:
            continue  # older extraction of the same statement
        seen.add(doc_id)
        if not _is_pnl_doc_type(m["doc_type"]):
            continue
        raw = m["fields"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = []
        fields = read_extraction_fields(raw or [], m["catalog_version"])
        docs.append({
            "document_id": doc_id,
            "filename": m["filename"],
            "doc_type": m["doc_type"],
            "fiscal_year": m["fiscal_year"],
            "uploaded_at": str(m["uploaded_at"] or ""),
            "fields": [f for f in fields if isinstance(f, dict) and f.get("field_name")],
        })
    docs.sort(key=lambda d: (
        d["fiscal_year"] if isinstance(d["fiscal_year"], int) else 10_000,
        d["uploaded_at"],
    ))
    return docs


def _period_label(doc: Mapping[str, Any]) -> str:
    dt = (doc.get("doc_type") or "").upper()
    fy = doc.get("fiscal_year")
    kind = "T-12" if "T12" in dt or dt == "T-12" else ("YTD" if "YTD" in dt else "FY")
    return f"{kind} {fy}" if isinstance(fy, int) else f"{kind} · {doc.get('filename') or 'statement'}"


def _hist_state(docs: list[dict[str, Any]]) -> tuple[
    list[HistColumn], dict[tuple[str, str], HistCell], HistCurrent
]:
    from ..ontology.registry import get_registry, resolve

    known = set(get_registry().concepts)
    columns: list[HistColumn] = []
    cells: dict[tuple[str, str], HistCell] = {}
    current = HistCurrent()
    used: set[str] = set()
    for doc in docs:
        doc_id = doc["document_id"]
        label = _period_label(doc)
        base, n = label, 2
        while label in used:
            label, n = f"{base} ({n})", n + 1
        used.add(label)
        columns.append(HistColumn(document_id=doc_id, label=label, filename=doc.get("filename")))
        current.documents.add(doc_id)
        for f in doc["fields"]:
            cid = hist_cell_id(doc_id, f["field_name"])
            current.raw[cid] = f.get("value")
            current.values[cid] = to_number(f.get("value"))
        for line_id, _label in HIST_LINES:
            if line_id not in known:
                continue
            try:
                res = resolve(doc["fields"], line_id, doc_type=doc.get("doc_type"), want="unknown")
            except Exception:
                continue
            if res.field_name is None:
                continue
            # Key the cell on the stored field_name exactly (the review path
            # matches it verbatim); the resolver strips whitespace.
            fname = next(
                (f["field_name"] for f in doc["fields"]
                 if str(f["field_name"]).strip() == res.field_name),
                res.field_name,
            )
            cells[(line_id, doc_id)] = HistCell(
                field_name=fname,
                value=current.values.get(hist_cell_id(doc_id, fname)),
            )
    return columns, cells, current


# ─────────────────────────── historical routes ───────────────────────────


@router.get("/{deal_id}/exports/historicals.xlsx")
async def export_historicals(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> Response:
    """Multi-year Historical P&L workbook with stable per-cell field ids."""
    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    docs = await _load_historical_docs(session, deal_id, tenant)
    columns, cells, _current = _hist_state(docs)
    data = build_historicals_workbook(
        deal_id=str(deal_id),
        deal_name=await _deal_name(session, deal_id, tenant),
        columns=columns,
        cells=cells,
    )
    await log_audit(
        session,
        tenant_id=tenant,
        actor_id=auth.user_id,
        actor_email=auth.email,
        action="export.historicals_downloaded",
        resource_type="export",
        resource_id=str(deal_id),
        output_payload={"statements": len(columns), "size_bytes": len(data)},
        tags=["export", "download", "fon-41"],
        deal_id=str(deal_id),
    )
    await session.commit()
    return _xlsx_response(data, f"fondok-historical-pl-{deal_id}.xlsx")


@router.post("/{deal_id}/imports/historicals")
async def preview_historicals_import(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    file: Annotated[UploadFile, File(...)],
) -> dict[str, Any]:
    """Validate an edited Historical P&L workbook and return the diff. Writes nothing."""
    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    data = await _read_upload(file)
    docs = await _load_historical_docs(session, deal_id, tenant)
    _cols, _cells, current = _hist_state(docs)
    filenames = {d["document_id"]: d.get("filename") for d in docs}
    try:
        preview = parse_historicals_import(data, deal_id=str(deal_id), current=current)
    except WorkbookFormatError as exc:
        raise _format_error(exc) from exc
    for c in preview["changes"]:
        c["filename"] = filenames.get(c["document_id"])
    return preview


class HistApplyChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    cell_id: Annotated[str, Field(min_length=3, max_length=500)]
    new_value: float
    #: FON-74 — the analyst's justification; a historical cell corrected from
    #: the workbook is the same edit as "Correct this value" on the grid, so
    #: it carries the same note. Blank → the cell is skipped (``note_required``).
    note: Annotated[str, Field(max_length=2000)] = ""
    #: The value the preview showed as "old" — Apply skips the cell if the
    #: current value no longer matches (someone corrected it in between).
    old_value: Any = None


class HistApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: Annotated[list[HistApplyChange], Field(min_length=1, max_length=2000)]


@router.post("/{deal_id}/imports/historicals/apply")
async def apply_historicals_import(
    deal_id: UUID,
    body: HistApplyBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> dict[str, Any]:
    """Apply previewed changes, one ``review_extraction_field(edit)`` per cell."""
    from .documents import FieldReviewBody, review_extraction_field

    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    docs = await _load_historical_docs(session, deal_id, tenant)
    _cols, _cells, current = _hist_state(docs)

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for ch in body.changes:
        parts = split_hist_cell_id(ch.cell_id)
        if parts is None or ch.cell_id not in current.raw or parts[0] not in current.documents:
            skipped.append({"cell_id": ch.cell_id, "reason": "unknown_id"})
            continue
        now_val = current.values.get(ch.cell_id)
        if ch.old_value is not None:
            prev = to_number(ch.old_value)
            if not same_number(prev, now_val) and ch.old_value != current.raw.get(ch.cell_id):
                skipped.append({"cell_id": ch.cell_id, "reason": "stale",
                                "current_value": now_val})
                continue
        if same_number(now_val, ch.new_value):
            skipped.append({"cell_id": ch.cell_id, "reason": "unchanged"})
            continue
        doc_id, fname = parts
        if not (ch.note or "").strip():
            skipped.append({"cell_id": ch.cell_id, "reason": "note_required"})
            continue
        try:
            await review_extraction_field(
                deal_id=deal_id,
                doc_id=UUID(doc_id),
                body=FieldReviewBody(
                    field_name=fname, action="edit", value=ch.new_value, note=ch.note.strip()
                ),
                session=session,
                auth=auth,
            )
        except HTTPException as exc:
            skipped.append({"cell_id": ch.cell_id, "reason": "review_failed",
                            "detail": exc.detail})
            continue
        except ValueError:
            skipped.append({"cell_id": ch.cell_id, "reason": "unknown_id"})
            continue
        applied.append({"cell_id": ch.cell_id, "old_value": now_val, "new_value": ch.new_value})

    if applied:
        await log_audit(
            session,
            tenant_id=tenant,
            actor_id=auth.user_id,
            actor_email=auth.email,
            action="import.historicals_applied",
            resource_type="import",
            resource_id=str(deal_id),
            output_payload={"applied": len(applied), "skipped": len(skipped)},
            diff_summary=f"historical P&L import: {len(applied)} cell(s) corrected at source",
            tags=["import", "fon-41"],
            deal_id=str(deal_id),
        )
        await session.commit()
    return {"applied": applied, "skipped": skipped}


# ─────────────────────────── projection state ───────────────────────────


async def _projection_state(
    session: AsyncSession, deal_id: UUID, tenant: str
) -> tuple[list[str], dict[str, float | None], ProjectionExtras]:
    from ..services.engine_runner import get_run_scoped_outputs

    envelopes = await get_run_scoped_outputs(
        session, deal_id=str(deal_id), tenant_id=tenant
    )
    outputs: dict[str, Any] = {}
    for name, env in envelopes.items():
        out = env.get("outputs") if isinstance(env, dict) else None
        if isinstance(out, dict):
            outputs[name] = out
    n, values = projection_values(outputs)
    cal = (outputs.get("revenue") or {}).get("projection_calendar_years")
    headers: list[str] = []
    for i in range(n):
        label = "Base year (Year 1)" if i == 0 else f"Year {i + 1}"
        cy = cal[i] if isinstance(cal, list) and i < len(cal) else None
        headers.append(f"{label} · {cy}" if isinstance(cy, int) else label)
    return headers, values, projection_extras(outputs, n)


async def _assumption_state(
    session: AsyncSession, deal_id: UUID, tenant: str
) -> tuple[dict[str, Any], dict[str, str], dict[str, Any], tuple[Any, ...]]:
    """``(current values, sources, raw field_overrides, specs)`` for the editable keys.

    Values come from the engine-input loader (the same resolution
    ``GET /deals/{id}/assumption_sources`` serves), so an override already on
    the deal shows as the current value with source ``analyst_override``. A
    key the loader does not carry is left blank — never a UI default.
    ``specs`` is ``ASSUMPTIONS`` plus the FON-95 by-year penetration target
    rows for this deal's ``hold_years``.
    """
    from ..services.engine_runner import _load_engine_inputs
    from ..services.index_methodology import coerce_target_list
    from .deals import _coerce_overrides

    base = await _load_engine_inputs(session, str(deal_id), tenant_id=tenant)
    sources = base.get("__sources__") or {}
    row = (
        await session.execute(
            text("SELECT field_overrides FROM deals WHERE id = :id AND tenant_id = :tenant"),
            {"id": str(deal_id), "tenant": tenant},
        )
    ).first()
    overrides = _coerce_overrides(row._mapping["field_overrides"]) if row is not None else {}
    line_methods = await _active_line_methods(session, deal_id, tenant)
    values: dict[str, Any] = {}
    for spec in ASSUMPTIONS:
        pm = split_projection_method_key(spec.key)
        if pm is not None:
            # E-016 — the method/value the engine actually ran (override or
            # its default), so the sheet shows the live driver, never a guess.
            ov = overrides.get(spec.key)
            ov = ov.get("value") if isinstance(ov, dict) else ov
            active = line_methods.get(pm[0]) or {}
            if pm[1] == "method":
                m = ov if isinstance(ov, str) and ov else active.get("method")
                values[spec.key] = str(m) if isinstance(m, str) and m else None
            else:
                v = to_number(ov)
                values[spec.key] = v if v is not None else to_number(active.get("value"))
            continue
        v = base.get(spec.key)
        if v is None and spec.key in overrides:
            ov = overrides[spec.key]
            v = ov.get("value") if isinstance(ov, dict) else ov
        values[spec.key] = to_number(v)
    srcs = {
        k: (
            "analyst_override" if k in overrides
            else ("engine default" if split_projection_method_key(k) and values.get(k) is not None
                  else str(sources.get(k) or ""))
        )
        for k in values
    }
    # FON-95 part 2 — one row per metric per model year. The value is the
    # by-year entry only (never the scalar shorthand, which is its own key);
    # blank = no by-year target for that year.
    try:
        hold = int(float(base.get("hold_years") or 5))
    except (TypeError, ValueError):
        hold = 5
    by_year_specs = by_year_target_assumptions(hold)
    for spec in by_year_specs:
        list_key, year = split_by_year_target_key(spec.key)  # type: ignore[misc]
        ov = overrides.get(list_key)
        lst = coerce_target_list(ov.get("value") if isinstance(ov, dict) else ov)
        v = lst[year - 1] if year - 1 < len(lst) else None
        values[spec.key] = v
        srcs[spec.key] = "analyst_override" if v is not None else ""
    # Sam, 2026-10-15 — the renovation displacement schedule, one row per
    # period per field, plus one blank period. The value is the saved
    # period's own field; blank = not set.
    saved_periods = _schedule_periods(overrides)
    schedule_specs = schedule_assumptions(len(saved_periods))
    for spec in schedule_specs:
        period, field = split_schedule_period_key(spec.key)  # type: ignore[misc]
        entry = saved_periods[period - 1] if period - 1 < len(saved_periods) else {}
        v = to_number(entry.get(field)) if isinstance(entry, dict) else None
        values[spec.key] = v
        srcs[spec.key] = "analyst_override" if v is not None else ""
    return values, srcs, overrides, ASSUMPTIONS + by_year_specs + schedule_specs


def _schedule_periods(overrides: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The saved ``renovation_displacement_schedule`` periods as dicts (``[]``
    when absent or malformed) — the same list the runner reads."""
    ov = overrides.get(SCHEDULE_KEY)
    raw = ov.get("value") if isinstance(ov, dict) else ov
    if not isinstance(raw, list):
        return []
    return [dict(e) for e in raw if isinstance(e, dict)]


async def _active_line_methods(
    session: AsyncSession, deal_id: UUID, tenant: str
) -> dict[str, dict[str, Any]]:
    """E-016 — the expense engine's ``line_methods`` from the latest run ({} if none)."""
    from ..services.engine_runner import get_run_scoped_outputs

    envelopes = await get_run_scoped_outputs(
        session, deal_id=str(deal_id), tenant_id=tenant
    )
    env = envelopes.get("expense")
    out = env.get("outputs") if isinstance(env, dict) else None
    lm = out.get("line_methods") if isinstance(out, dict) else None
    return {k: v for k, v in lm.items() if isinstance(v, dict)} if isinstance(lm, dict) else {}


def _needs_note(key: str) -> bool:
    from ..services.engine_runner import _OVERRIDE_NON_ENGINE_KEYS
    from .deals import _override_needs_note

    return _override_needs_note(key, _OVERRIDE_NON_ENGINE_KEYS)


# ───────────────────── by-year target notes (FON-95 part 2) ─────────────────────
#
# An ``index_*_target_by_year`` override is ONE list with ONE note, but the
# Excel apply edits it a year at a time, each row with its own justification.
# The note therefore carries every year's reason as ``Y<n>: …`` segments
# joined by " · ". A segment that does not start with ``Y<n>:`` is a legacy
# whole-list note and is kept in front, so no justification is ever lost.

_YEAR_NOTE_SEP = " · "
#: ``prefix`` — ``"Y"`` for the by-year target lists (``Y2: …``), ``"P"`` for
#: the renovation displacement schedule's periods (``P1: …``).
_NOTE_PREFIX_RE = {
    "Y": re.compile(r"^Y(\d{1,2}):\s*(.*)$", re.DOTALL),
    "P": re.compile(r"^P(\d{1,2}):\s*(.*)$", re.DOTALL),
}


def split_by_year_notes(
    note: str | None, *, prefix: str = "Y"
) -> tuple[str | None, dict[int, str]]:
    """``"old · Y2: a · Y3: b"`` → ``("old", {2: "a", 3: "b"})``."""
    general: list[str] = []
    by_year: dict[int, str] = {}
    rx = _NOTE_PREFIX_RE[prefix]
    for seg in (note or "").split(_YEAR_NOTE_SEP):
        seg = seg.strip()
        if not seg:
            continue
        m = rx.match(seg)
        if m:
            by_year[int(m.group(1))] = m.group(2).strip()
        else:
            general.append(seg)
    return (_YEAR_NOTE_SEP.join(general) or None), by_year


def merge_by_year_notes(
    existing: str | None, new_by_year: Mapping[int, str], *, prefix: str = "Y"
) -> str:
    """Fold this apply's per-year notes into the list's existing note.

    Years edited now replace their own segment; every other year's segment
    — and any legacy whole-list note — survives untouched. The result is
    ``"<legacy> · Y2: … · Y3: …"`` in year order ("" when there is nothing).
    With ``prefix="P"`` the segments are per renovation period instead.
    """
    general, by_year = split_by_year_notes(existing, prefix=prefix)
    for year, text_ in new_by_year.items():
        cleaned = (text_ or "").strip()
        if cleaned:
            by_year[int(year)] = cleaned
    segments = ([general] if general else []) + [
        f"{prefix}{y}: {by_year[y]}" for y in sorted(by_year)
    ]
    return _YEAR_NOTE_SEP.join(segments)


# ─────────────────────────── projection routes ───────────────────────────


@router.get("/{deal_id}/exports/projections.xlsx")
async def export_projections(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> Response:
    """Future P&L (engine projection) + Assumptions workbook."""
    from .deals import _override_note

    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    headers, values, extras = await _projection_state(session, deal_id, tenant)
    cur, srcs, overrides, specs = await _assumption_state(session, deal_id, tenant)

    saved_periods = _schedule_periods(overrides)

    def _note_for(key: str) -> str | None:
        sp = split_schedule_period_key(key)
        if sp is not None:
            period = saved_periods[sp[0] - 1] if sp[0] - 1 < len(saved_periods) else None
            own = period.get("note") if isinstance(period, dict) else None
            if isinstance(own, str) and own.strip():
                return own.strip()
            return _override_note(overrides.get(SCHEDULE_KEY)) if period is not None else None
        by = split_by_year_target_key(key)
        return _override_note(overrides.get(by[0] if by is not None else key))

    rows = [
        {
            "key": spec.key,
            "label": spec.label,
            "value": cur.get(spec.key),
            "unit": spec.unit,
            "source": srcs.get(spec.key) or ("not set" if cur.get(spec.key) is None else ""),
            "method": spec.method,
            "note_required": _needs_note(spec.key),
            "current_note": _note_for(spec.key),
        }
        for spec in specs
    ]
    # FON-97 — the same market-data block the web's Benchmark column reads;
    # None (no column) when the deal carries no P&L benchmark. The column is
    # a convenience, so a market-data aggregation failure drops the column
    # and logs — it never fails the export of the analyst's own projections.
    benchmark = None
    try:
        market = await load_market_data(session, deal_id=deal_id, tenant_id=UUID(tenant))
    except Exception:  # noqa: BLE001
        logger.exception(
            "export_projections: market data unavailable for deal %s — "
            "Benchmark column omitted",
            deal_id,
        )
    else:
        if market.pnl_benchmark is not None and market.pnl_benchmark.categories:
            benchmark = benchmark_column_values(market.pnl_benchmark.categories)
    data = build_projections_workbook(
        deal_id=str(deal_id),
        deal_name=await _deal_name(session, deal_id, tenant),
        year_headers=headers,
        values=values,
        assumptions=rows,
        benchmark=benchmark,
        extras=extras,
    )
    await log_audit(
        session,
        tenant_id=tenant,
        actor_id=auth.user_id,
        actor_email=auth.email,
        action="export.projections_downloaded",
        resource_type="export",
        resource_id=str(deal_id),
        output_payload={"years": len(headers), "size_bytes": len(data)},
        tags=["export", "download", "fon-41"],
        deal_id=str(deal_id),
    )
    await session.commit()
    return _xlsx_response(data, f"fondok-future-pl-{deal_id}.xlsx")


@router.post("/{deal_id}/imports/projections")
async def preview_projections_import(
    deal_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    file: Annotated[UploadFile, File(...)],
) -> dict[str, Any]:
    """Validate an edited Future P&L workbook and return the diff. Writes nothing."""
    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    data = await _read_upload(file)
    _headers, values, _extras = await _projection_state(session, deal_id, tenant)
    cur, _srcs, _ov, _specs = await _assumption_state(session, deal_id, tenant)
    try:
        return parse_projections_import(
            data,
            deal_id=str(deal_id),
            current_values=values,
            current_assumptions=cur,
            needs_note=_needs_note,
        )
    except WorkbookFormatError as exc:
        raise _format_error(exc) from exc


class ProjApplyChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: Annotated[str, Field(min_length=1, max_length=120)]
    new_value: float | str
    note: Annotated[str, Field(max_length=2000)] = ""
    old_value: float | str | None = None


class ProjApplyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: Annotated[list[ProjApplyChange], Field(min_length=1, max_length=200)]


@router.post("/{deal_id}/imports/projections/apply")
async def apply_projections_import(
    deal_id: UUID,
    body: ProjApplyBody,
    session: Annotated[AsyncSession, Depends(get_session)],
    auth: Annotated[AuthContext, Depends(get_current_auth)],
) -> dict[str, Any]:
    """Write previewed assumption changes as ``field_overrides`` via ``update_deal``.

    One PATCH carries every accepted change (the same merged-blob shape the
    Future P&L's own Save sends), so the FON-74 note gate inside
    ``update_deal`` sees each key — a row needing a note without one is
    refused there too, not just in the preview. The caller re-runs the model
    afterwards, exactly as after an Assumptions-panel save.
    """
    from .deals import UpdateDealBody, update_deal

    tenant = str(auth.tenant_id)
    await _assert_deal_belongs_to_tenant(session, deal_id=deal_id, tenant_id=tenant)
    cur, _srcs, overrides, _specs = await _assumption_state(session, deal_id, tenant)

    merged: dict[str, Any] = dict(overrides)
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    method_changed_lines: set[str] = set()
    value_changed_lines: dict[str, str] = {}
    # FON-95 part 2 — by-year target rows fold into their JSON list override;
    # each edited year keeps its own note (``merge_by_year_notes``).
    by_year_changes: dict[str, dict[int, float]] = {}
    by_year_notes: dict[str, dict[int, str]] = {}
    # Sam, 2026-10-15 — renovation-schedule rows fold into their JSON list
    # override, period by period; each edited period keeps its own note.
    schedule_changes: dict[int, dict[str, float | int]] = {}
    schedule_notes: dict[int, str] = {}
    schedule_rows: dict[int, list[dict[str, Any]]] = {}
    batch_keys = {ch.key for ch in body.changes}
    for ch in body.changes:
        spec = assumption_spec(ch.key)
        if spec is None:
            skipped.append({"key": ch.key, "reason": "unknown_key"})
            continue
        if spec.choices:
            choice = str(ch.new_value).strip().lower()
            if choice not in spec.choices:
                skipped.append({"key": ch.key, "reason": "invalid_choice"})
                continue
            now_c = cur.get(ch.key)
            if ch.old_value is not None and str(ch.old_value).strip().lower() != (now_c or ""):
                skipped.append({"key": ch.key, "reason": "stale", "current_value": now_c})
                continue
            if choice == now_c:
                skipped.append({"key": ch.key, "reason": "unchanged"})
                continue
            note = ch.note.strip()
            if not note and _needs_note(ch.key):
                skipped.append({"key": ch.key, "reason": "note_required"})
                continue
            merged[ch.key] = {"value": choice, "note": note} if note else choice
            method_changed_lines.add(split_projection_method_key(ch.key)[0])
            applied.append({"key": ch.key, "old_value": now_c, "new_value": choice, "note": note})
            continue
        if isinstance(ch.new_value, str):
            skipped.append({"key": ch.key, "reason": "non_numeric"})
            continue
        value: float | int = ch.new_value
        if spec.integer:
            if not float(value).is_integer():
                skipped.append({"key": ch.key, "reason": "non_numeric"})
                continue
            value = int(value)
        now_val = cur.get(ch.key)
        if isinstance(ch.old_value, str):
            skipped.append({"key": ch.key, "reason": "stale", "current_value": now_val})
            continue
        if ch.old_value is not None and not same_number(ch.old_value, now_val):
            skipped.append({"key": ch.key, "reason": "stale", "current_value": now_val})
            continue
        if same_number(now_val, float(value)):
            skipped.append({"key": ch.key, "reason": "unchanged"})
            continue
        note = ch.note.strip()
        if not note and _needs_note(ch.key):
            skipped.append({"key": ch.key, "reason": "note_required"})
            continue
        by = split_by_year_target_key(ch.key)
        if by is not None:
            list_key, year = by
            from ..services.index_methodology import validate_index_target

            if float(value) <= 0:
                skipped.append({"key": ch.key, "reason": "non_numeric"})
                continue
            points, why = validate_index_target(value)
            if points is None:
                # "5" is neither 5 index points nor a 5.0× ratio — refuse
                # it with the reason rather than drive occupancy to 3.8%.
                skipped.append({"key": ch.key, "reason": "out_of_range", "detail": why})
                continue
            by_year_changes.setdefault(list_key, {})[year] = float(value)
            if note:
                by_year_notes.setdefault(list_key, {})[year] = note
            applied.append({"key": ch.key, "old_value": now_val, "new_value": value, "note": note})
            continue
        sp = split_schedule_period_key(ch.key)
        if sp is not None:
            period, field = sp
            schedule_changes.setdefault(period, {})[field] = value
            if note:
                schedule_notes[period] = note
            schedule_rows.setdefault(period, []).append(
                {"key": ch.key, "old_value": now_val, "new_value": value, "note": note}
            )
            continue
        pm = split_projection_method_key(ch.key)
        if pm is not None:
            mkey = f"projection_methods.{pm[0]}.method"
            if mkey not in overrides and mkey not in batch_keys and not cur.get(mkey):
                # No method to pair it with (no model run yet) — the engine
                # would ignore a bare value, so refuse rather than store it.
                skipped.append({"key": ch.key, "reason": "method_required"})
                continue
            value_changed_lines[pm[0]] = note
        merged[ch.key] = {"value": value, "note": note} if note else value
        applied.append({"key": ch.key, "old_value": now_val, "new_value": value, "note": note})

    # E-016 coherence. The engine ignores a value with no method, so a value
    # edited on a line still on its default pins that line's ACTIVE method
    # with it. A method switched without a new value drops the old value
    # (its units belong to the old method) — the line then holds its own
    # Year-1 ratio, exactly as the method chip does.
    from ..export.pl_workbooks import projection_method_key

    for line, note in value_changed_lines.items():
        mkey = projection_method_key(line, "method")
        if line not in method_changed_lines and mkey not in overrides:
            active = cur.get(mkey)
            if active:
                merged[mkey] = {"value": active, "note": note} if note else active
    for line in method_changed_lines - set(value_changed_lines):
        merged.pop(projection_method_key(line, "value"), None)

    # FON-95 part 2 — fold the per-year rows into the list the runner reads
    # (index y-1 = model year y; untouched years keep their current entry
    # AND their note — see ``merge_by_year_notes``).
    if by_year_changes:
        from ..services.index_methodology import coerce_target_list, override_note

        for list_key, per_year in by_year_changes.items():
            existing = overrides.get(list_key)
            current = coerce_target_list(existing.get("value") if isinstance(existing, dict) else existing)
            size = max(len(current), max(per_year))
            new_list: list[float | None] = list(current) + [None] * (size - len(current))
            for year, v in per_year.items():
                new_list[year - 1] = v
            note = merge_by_year_notes(
                override_note(overrides, list_key), by_year_notes.get(list_key, {})
            )
            merged[list_key] = {"value": new_list, "note": note} if note else new_list

    # Sam, 2026-10-15 — fold the per-period rows into the schedule list the
    # runner reads. A NEW period (index past the saved list) needs both
    # months in this batch — the engine cannot place a period with one end
    # — and every period is validated with the loader's own validator before
    # anything is stored; a refused period's rows are skipped with the reason.
    if schedule_changes:
        from ..services.engine_runner import renovation_schedule_problems

        periods = _schedule_periods(overrides)
        applied_periods: set[int] = set()
        for period in sorted(schedule_changes):
            edits = schedule_changes[period]
            idx = period - 1
            if idx > len(periods):
                for r in schedule_rows[period]:
                    skipped.append({"key": r["key"], "reason": "period_out_of_order",
                                    "detail": f"Fill period {len(periods) + 1} first."})
                continue
            if idx == len(periods):
                if "start_month" not in edits or "end_month" not in edits:
                    for r in schedule_rows[period]:
                        skipped.append({"key": r["key"], "reason": "period_incomplete",
                                        "detail": "A new period needs both First month and Last month."})
                    continue
                periods.append({})
            entry = dict(periods[idx])
            entry.update(edits)
            entry.setdefault("rooms_offline_pct", 0.0)
            entry.setdefault("rate_discount_pct", 0.0)
            if schedule_notes.get(period):
                entry["note"] = schedule_notes[period]
            problems = renovation_schedule_problems([entry])
            if problems:
                for r in schedule_rows[period]:
                    skipped.append({"key": r["key"], "reason": "out_of_range",
                                    "detail": "; ".join(problems)})
                if idx == len(periods) - 1 and not periods[idx]:
                    periods.pop()
                continue
            periods[idx] = entry
            applied.extend(schedule_rows[period])
            applied_periods.add(period)
        if applied_periods:
            from ..services.index_methodology import override_note

            # Only the periods that were actually stored contribute a note —
            # a refused period's justification never lands on the list.
            note = merge_by_year_notes(
                override_note(overrides, SCHEDULE_KEY),
                {p: n for p, n in schedule_notes.items() if p in applied_periods},
                prefix="P",
            )
            merged[SCHEDULE_KEY] = {"value": periods, "note": note} if note else periods

    if applied:
        await update_deal(
            deal_id=deal_id,
            body=UpdateDealBody(field_overrides=merged),
            session=session,
            tenant_id=auth.tenant_id,
        )
    return {"applied": applied, "skipped": skipped, "rerun_required": bool(applied)}


__all__ = ["router"]
