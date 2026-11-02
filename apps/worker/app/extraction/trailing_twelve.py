"""Deterministic trailing-twelve classification for P&L-family workbooks.

W5 (2026-11). The Angler's "<Month> 2025 Financials" workbooks carry two
statements: a ``P&L`` sheet (the month and year-to-date) and a ``T12`` sheet
whose columns are twelve DATED month-ends and a TOTAL — a trailing twelve
months. They were typed (or analyst-tagged) ``PNL_MONTHLY`` and carried no
period label, so the ranking put them in the monthly tier and the T-12 loader
had to recognise them from the size of their monthly block. Two extraction
shapes exist on prod:

* the sibling-template read of the ``T12`` sheet — full-period lines equal to
  the TOTAL column plus a twelve-cell ``monthly`` block, no period fields;
* the LLM read — the month in ``p_and_l_usali.<month>_periodic.*``, the year
  to date in ``p_and_l_usali.ytd_<month>.*``, and only a few ``T12`` lines
  under ``ttm_performance``; no statement-level full-period line at all.

This pass proves the statement from the workbook itself and states it the way
every reader already reads a period:

1. **A trailing-twelve sheet.** A visible sheet named ``T12`` / ``T-12`` /
   ``TTM`` / ``Trailing Twelve`` whose header row carries at least twelve
   date cells, the last twelve consecutive months, followed by a ``TOTAL``
   column. A ``FOR THE PERIOD ENDING <date>`` title on the sheet must name
   the same last month-end. Forecast / budget / adjusted sheets are never it.
2. **The sheet adds up.** Its rooms-revenue row's twelve dated cells sum to
   its TOTAL cell, to the half-units the figures were read at
   (``services.statement_lines.half_unit``).
3. **The extraction is that statement, or holds no other full year.** The
   sheet is read by USALI label (``usali_summary_reconcile.parse_summary_grid``
   — the Summary-sheet reader). When the extraction has a statement-level
   full-period line that disagrees with the sheet by more than the
   reconciler's 5% (``reconcile_fields`` reports a change) the extraction is a
   different statement and nothing is touched. Otherwise every sheet total
   the extraction has no statement-level field for is ADDED at its canonical
   path (the reconciler's add rule, ``reviewed = "reconciled"``), and so are
   the sheet's occupancy / ADR / RevPAR when the extraction has no statement-
   level KPI.

Then ``p_and_l_usali.period_type = "trailing_twelve"`` and
``p_and_l_usali.period_ending = <last month-end>`` are put FIRST in the field
list (every reader takes the first ``*.period_type`` / ``*.period_ending``),
with ``reviewed = "reconciled"``, ``reconciled_from = {"rule":
"trailing_twelve", …}`` and a one-sentence ``note``. A partial label
(``monthly`` / ``ytd``) already on the extraction is replaced, its old value
kept in ``reconciled_from``.

Never applied: a statement already labelled full-year; a stated
``period_ending`` that differs from the sheet; a ``PNL`` (annual) whose twelve
months end in December (a calendar-year annual stays an annual); a workbook
with no dated trailing-twelve sheet (a genuine monthly statement).

``api.documents._refine_pnl_doc_type`` then narrows the type to ``T12`` from
the label; an analyst's other P&L tag is kept but raises the
misclassification banner (``refine_doc_type_post_extraction``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

TRAILING_TWELVE_RULE = "trailing_twelve"
LABELLED_CONFIDENCE = 0.98

PNL_FAMILY: frozenset[str] = frozenset({"T12", "PNL", "PNL_MONTHLY", "PNL_YTD"})

#: Period labels that already say "a full operating year".
FULL_YEAR_LABELS: frozenset[str] = frozenset(
    {"annual", "fiscal_year", "full_year", "trailing_twelve", "ttm", "t12", "rolling_twelve"}
)

#: Sheet names (lower-cased, separators collapsed) that hold a trailing twelve.
TRAILING_TWELVE_SHEET_NAMES: frozenset[str] = frozenset(
    {"t12", "t 12", "ttm", "trailing 12", "trailing twelve", "trailing twelve months", "t12 p&l"}
)

_DATE_CELL = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T]00:00:00)?$")
_PERIOD_TITLE = re.compile(
    r"period\s+ending\s+([a-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.IGNORECASE
)
_MONTH_NAMES = {
    m: i + 1
    for i, m in enumerate(
        (
            "january", "february", "march", "april", "may", "june", "july",
            "august", "september", "october", "november", "december",
        )
    )
}
#: KPI rows of the sheet → the loader's revenue canonical they ground.
_KPI_LABELS: dict[str, str] = {
    "occupancy": "occupancy",
    "occupancy %": "occupancy",
    "average daily rate": "adr",
    "adr": "adr",
    "revenue per available room": "revpar",
    "revpar": "revpar",
}
_KPI_UNITS = {"occupancy": "pct", "adr": "USD", "revpar": "USD"}


@dataclass
class TrailingTwelveResult:
    fields: list[dict[str, Any]]
    confidence: dict[str, Any]
    applied: bool = False
    skipped_reason: str | None = None
    period_end: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Sheet:
    page: Mapping[str, Any]
    page_num: int
    sheet_name: str
    grid: list[list[Any]]
    header_row: int
    month_cols: tuple[int, ...]
    total_col: int
    months: tuple[date, ...]


def _canonical_doc_type(value: str | None) -> str:
    v = (value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return "T12" if v in {"T_12", "T12"} else v


def _norm(s: Any) -> str:
    return re.sub(r"[\s_\-]+", " ", str(s or "").strip().lower())


def _first(fields: Sequence[Mapping[str, Any]], suffix: str) -> tuple[int, Mapping[str, Any]] | None:
    for i, f in enumerate(fields):
        if str(f.get("field_name") or "").strip().lower().endswith(suffix):
            v = f.get("value")
            if isinstance(v, str) and v.strip():
                return i, f
    return None


def _month_index(d: date) -> int:
    return d.year * 12 + d.month - 1


def _title_period_end(grid: Sequence[Sequence[Any]]) -> date | None:
    for row in grid[:8]:
        for cell in row:
            m = _PERIOD_TITLE.search(str(cell or ""))
            if not m:
                continue
            month = _MONTH_NAMES.get(m[1].lower())
            if month is None:
                continue
            try:
                return date(int(m[3]), month, int(m[2]))
            except ValueError:
                return None
    return None


def _sheet_header(grid: Sequence[Sequence[Any]]) -> tuple[int, tuple[int, ...], int, tuple[date, ...]] | None:
    """``(row, month columns, total column, month-ends)`` of the first header
    row with twelve consecutive dated months followed by a TOTAL column."""
    for ri, row in enumerate(grid[:20]):
        dated: list[tuple[int, date]] = []
        for ci, cell in enumerate(row):
            m = _DATE_CELL.match(str(cell or "").strip())
            if m:
                try:
                    dated.append((ci, date(int(m[1]), int(m[2]), int(m[3]))))
                except ValueError:
                    continue
        if len(dated) < 12:
            continue
        last = dated[-12:]
        idx = [_month_index(d) for _c, d in last]
        if not all(b - a == 1 for a, b in zip(idx, idx[1:], strict=False)):
            continue
        after = [ci for ci in range(last[-1][0] + 1, len(row)) if _norm(row[ci]) == "total"]
        if not after:
            continue
        return ri, tuple(c for c, _d in last), after[0], tuple(d for _c, d in last)
    return None


def find_trailing_twelve_sheet(pages: Sequence[Any] | None) -> _Sheet | None:
    """The workbook's dated trailing-twelve sheet, if it has one (see module doc)."""
    for i, p in enumerate(pages or []):
        if not isinstance(p, Mapping):
            continue
        meta = p.get("metadata") or {}
        if str(meta.get("sheet_state") or "visible").lower() != "visible":
            continue
        name = str(meta.get("sheet_name") or "")
        if _norm(name) not in TRAILING_TWELVE_SHEET_NAMES:
            continue
        tables = p.get("tables") or []
        grid = tables[0] if tables and isinstance(tables[0], list) else None
        if not grid:
            continue
        header = _sheet_header(grid)
        if header is None:
            continue
        ri, cols, total_col, months = header
        title_end = _title_period_end(grid)
        if title_end is not None and title_end != months[-1]:
            continue
        try:
            page_num = int(p.get("page_num") or i + 1)
        except (TypeError, ValueError):
            page_num = i + 1
        return _Sheet(p, page_num, name, grid, ri, cols, total_col, months)
    return None


def _row_by_label(sheet: _Sheet, labels: Mapping[str, str]) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {}
    for row in sheet.grid[sheet.header_row + 1 :]:
        texts = [c for c in row[: min(sheet.month_cols)] if str(c or "").strip()]
        if not texts:
            continue
        key = labels.get(_norm(texts[-1]))
        if key and key not in out:
            out[key] = list(row)
    return out


def _cell(row: Sequence[Any], j: int) -> float | None:
    from .numeric import coerce_cell_number

    return coerce_cell_number(row[j]) if j < len(row) else None


def _sheet_adds_up(sheet: _Sheet) -> tuple[float, float, float] | None:
    """``(total, Σ months, tolerance)`` for the Rooms revenue row, when its
    twelve dated cells sum to its TOTAL cell."""
    from ..services.statement_lines import half_unit

    rows = _row_by_label(sheet, {"rooms": "rooms", "rooms revenue": "rooms"})
    row = rows.get("rooms")
    if row is None:
        return None
    months = [_cell(row, j) for j in sheet.month_cols]
    total = _cell(row, sheet.total_col)
    if total is None or any(m is None for m in months):
        return None
    msum = sum(m for m in months if m is not None)
    tol = half_unit(total, str(row[sheet.total_col])) + sum(
        half_unit(m, str(row[j])) for m, j in zip(months, sheet.month_cols, strict=False) if m
    )
    if abs(total - msum) > tol:
        return None
    return total, msum, tol


def _kpi_additions(
    fields: Sequence[Mapping[str, Any]], sheet: _Sheet, end_iso: str
) -> list[dict[str, Any]]:
    from .field_catalog import ALIAS_LISTS_BY_NAMESPACE

    lists = ALIAS_LISTS_BY_NAMESPACE["t12_revenue"]
    names = {str(f.get("field_name") or "").strip().lower() for f in fields}
    rows = _row_by_label(sheet, _KPI_LABELS)
    out: list[dict[str, Any]] = []
    for key in ("occupancy", "adr", "revpar"):
        aliases = lists.get(key) or []
        if not aliases or names & set(aliases) or key not in rows:
            continue
        row = rows[key]
        v = _cell(row, sheet.total_col)
        if v is None:
            continue
        label = str([c for c in row[: min(sheet.month_cols)] if str(c or "").strip()][-1]).strip()
        out.append(
            {
                "field_name": aliases[0],
                "value": v,
                "unit": _KPI_UNITS[key],
                "source_page": sheet.page_num,
                "confidence": LABELLED_CONFIDENCE,
                "raw_text": f"{sheet.sheet_name}: {label} … {row[sheet.total_col]} (TOTAL)",
                "reviewed": "reconciled",
                "reconciled_from": {
                    "added": True,
                    "rule": TRAILING_TWELVE_RULE,
                    "sheet": sheet.sheet_name,
                    "label": label,
                },
                "note": (
                    f"Added from the {sheet.sheet_name} sheet's '{label}' TOTAL column: the "
                    f"trailing twelve months ending {end_iso}."
                ),
            }
        )
    return out


def label_trailing_twelve(
    fields: Sequence[Mapping[str, Any]],
    confidence: Mapping[str, Any] | None,
    *,
    doc_type: str | None,
    extraction_data: Mapping[str, Any] | None,
    file_bytes: bytes | None = None,
) -> TrailingTwelveResult:
    """Type a workbook's trailing-twelve statement with its period (module doc)."""
    from .usali_summary_reconcile import (
        parse_summary_grid,
        reconcile_fields,
        summary_grid_from_workbook,
    )

    base = [dict(f) for f in fields if isinstance(f, Mapping)]
    conf = dict(confidence or {})

    def skip(reason: str) -> TrailingTwelveResult:
        return TrailingTwelveResult(fields=base, confidence=conf, skipped_reason=reason)

    dt = _canonical_doc_type(doc_type)
    if dt not in PNL_FAMILY:
        return skip("not_pnl_family")
    if not base:
        return skip("no_fields")
    label = _first(base, "period_type")
    if label is not None and str(label[1].get("value")).strip().lower() in FULL_YEAR_LABELS:
        return skip("labelled_full_year")

    pages = extraction_data.get("pages") if isinstance(extraction_data, Mapping) else None
    sheet = find_trailing_twelve_sheet(pages)
    if sheet is None:
        return skip("no_trailing_twelve_sheet")
    end = sheet.months[-1]
    end_iso = end.isoformat()
    stated = _first(base, "period_ending")
    if stated is not None and str(stated[1].get("value")).strip()[:10] != end_iso:
        return skip("stated_period_end_disagrees")
    if dt == "PNL" and end.month == 12:
        return skip("calendar_year_annual")
    proof = _sheet_adds_up(sheet)
    if proof is None:
        return skip("sheet_does_not_add_up")
    total, month_sum, tolerance = proof

    grid: Sequence[Sequence[Any]] = sheet.grid
    row_numbers = None
    source = "parser_cache"
    if file_bytes:
        wb = summary_grid_from_workbook(file_bytes, sheet.sheet_name)
        if wb is not None:
            grid, row_numbers = wb
            source = "workbook"
    table = parse_summary_grid(
        grid,
        sheet_name=sheet.sheet_name,
        page_index=sheet.page_num,
        row_numbers=row_numbers,
        source=source,
        doc_basis="T12",
    )
    if table is None or "rooms_revenue" not in table.rows:
        return skip("sheet_has_no_usali_lines")
    merged = reconcile_fields(base, conf, table, doc_type="T12")
    if merged.changes:
        return skip("statement_lines_are_another_period")

    out = list(merged.fields)
    out_conf = dict(merged.confidence)
    out += _kpi_additions(out, sheet, end_iso)

    start_iso = sheet.months[0].isoformat()
    evidence = (
        f"{sheet.sheet_name} sheet columns {start_iso} … {end_iso} + TOTAL; Rooms "
        f"{total:,.2f} = sum of the 12 months {month_sum:,.2f}"
    )
    provenance: dict[str, Any] = {
        "rule": TRAILING_TWELVE_RULE,
        "derived": True,
        "sheet": sheet.sheet_name,
        "period_start_month": start_iso,
        "period_end": end_iso,
        "months": 12,
        "rooms_total": total,
        "rooms_month_sum": month_sum,
        "tolerance": tolerance,
        "added": [a.field_name for a in merged.additions],
    }
    note = (
        f"Trailing twelve months ending {end_iso}: the {sheet.sheet_name} sheet's twelve "
        f"dated monthly columns ({start_iso[:7]} to {end_iso[:7]}) add up to its TOTAL."
    )
    if label is not None:
        i, old = label
        out = [f for f in out if f is not old and f.get("field_name") != old.get("field_name")]
        provenance = {**provenance, "field_name": old.get("field_name"), "old_value": old.get("value")}
    labelled: list[dict[str, Any]] = [
        {
            "field_name": "p_and_l_usali.period_type",
            "value": "trailing_twelve",
            "unit": None,
            "source_page": sheet.page_num,
            "confidence": LABELLED_CONFIDENCE,
            "raw_text": evidence,
            "reviewed": "reconciled",
            "reconciled_from": provenance,
            "note": note,
        }
    ]
    if stated is None:
        labelled.append(
            {
                "field_name": "p_and_l_usali.period_ending",
                "value": end_iso,
                "unit": "date",
                "source_page": sheet.page_num,
                "confidence": LABELLED_CONFIDENCE,
                "raw_text": evidence,
                "reviewed": "reconciled",
                "reconciled_from": {"rule": TRAILING_TWELVE_RULE, "derived": True, "period_end": end_iso},
                "note": note,
            }
        )
    out = labelled + out
    by_field = dict(out_conf.get("by_field") or {})
    for f in out:
        if f.get("reviewed") == "reconciled" and f.get("field_name"):
            by_field[f["field_name"]] = float(f.get("confidence") or LABELLED_CONFIDENCE)
    if by_field:
        out_conf["by_field"] = by_field
    out_conf["trailing_twelve"] = {k: v for k, v in provenance.items() if k != "derived"}
    return TrailingTwelveResult(
        fields=out, confidence=out_conf, applied=True, period_end=end_iso, detail=provenance
    )


def is_derived_trailing_twelve(fields: Sequence[Mapping[str, Any]]) -> bool:
    """True when the statement's period label was set by this pass."""
    hit = _first(fields, "period_type")
    if hit is None:
        return False
    prov = hit[1].get("reconciled_from")
    return isinstance(prov, Mapping) and prov.get("rule") == TRAILING_TWELVE_RULE


__all__ = [
    "FULL_YEAR_LABELS",
    "LABELLED_CONFIDENCE",
    "TRAILING_TWELVE_RULE",
    "TRAILING_TWELVE_SHEET_NAMES",
    "TrailingTwelveResult",
    "find_trailing_twelve_sheet",
    "is_derived_trailing_twelve",
    "label_trailing_twelve",
]
