"""FON-41 E-013 / E-017 — editable P&L workbooks (export + validated re-import).

Pure openpyxl builders and parsers. No DB access here: the API layer
(``app/api/pl_roundtrip.py``) gathers the CURRENT state (extractions, engine
outputs, assumptions) and hands it in, so every diff below is computed against
what the deal holds right now — never against what the file claims was there.

Historical P&L (``fondok.historicals.v1``)
------------------------------------------
Sheet ``Historical P&L``: rows are USALI line items, one column PAIR per
historical statement — the visible value column, then a hidden ``fondok_id``
column whose cell holds the stable id ``<document_id>::<field_name>`` of the
extracted line that value came from. Row 1 is the period header, row 2 names
the source document (the id column carries its ``document_id``), and the
hidden column A carries the line's concept id. A cell with no id is a line the
statement never published — there is nothing at source to correct.

Future P&L (``fondok.projections.v1``)
--------------------------------------
Sheet ``Future P&L``: engine-computed projection lines (read-only — a changed
value is reported as "computed — edit the assumption instead"), hidden column A
holds the engine path. Sheet ``Assumptions``: every analyst-editable Future P&L
assumption with its stable ``field_overrides`` key, current value, source,
method, whether a note is required, and a Note column for the justification.
Only the Assumptions sheet is importable.

Both workbooks carry a ``README`` sheet whose ``format`` / ``deal_id`` rows the
importer checks first, so a file for another deal (or another format) is
refused outright instead of being half-applied.
"""

from __future__ import annotations

import io
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HIST_FORMAT = "fondok.historicals.v1"
PROJ_FORMAT = "fondok.projections.v1"
HIST_SHEET = "Historical P&L"
PROJ_SHEET = "Future P&L"
ASSUMPTION_SHEET = "Assumptions"
README_SHEET = "README"
ID_HEADER = "fondok_id"
LINE_HEADER = "fondok_line"
PATH_HEADER = "fondok_path"

_HEADER_FILL = PatternFill("solid", fgColor="1A2233")
_HEADER_FONT = Font(bold=True, color="FFFFFF")
_SUB_FONT = Font(italic=True, color="6B6F76")
_READONLY_FILL = PatternFill("solid", fgColor="F3F2EE")

# ─────────────────────────────── line models ───────────────────────────────

#: USALI line items of the Historical P&L, in statement order. Concept ids
#: from ``app/ontology/concepts.yaml`` — the resolver that picks each
#: statement's line is the registry's, so the export names the SAME extracted
#: field the worksheet and the engines read.
HIST_LINES: tuple[tuple[str, str], ...] = (
    ("occupancy", "Occupancy"),
    ("adr", "ADR"),
    ("revpar", "RevPAR"),
    ("rooms_revenue", "Rooms Revenue"),
    ("fb_revenue", "Food & Beverage Revenue"),
    ("other_revenue", "Other Operated Departments Revenue"),
    ("misc_revenue", "Miscellaneous Income"),
    ("resort_fees", "Resort Fees"),
    ("total_revenue", "Total Operating Revenue"),
    ("rooms_dept_expense", "Rooms Department Expense"),
    ("fb_dept_expense", "Food & Beverage Department Expense"),
    ("other_dept_expense", "Other Operated Departments Expense"),
    ("dept_expenses", "Total Departmental Expenses"),
    ("dept_profit", "Total Departmental Profit"),
    ("administrative_general", "Administrative & General"),
    ("information_telecom", "Information & Telecommunications"),
    ("sales_marketing", "Sales & Marketing"),
    ("property_operations", "Property Operations & Maintenance"),
    ("utilities", "Utilities"),
    ("undistributed_expenses", "Total Undistributed Expenses"),
    ("gop", "Gross Operating Profit"),
    ("mgmt_fee", "Management Fee"),
    ("franchise_royalty_fee", "Franchise Royalty Fee"),
    ("property_taxes", "Property Taxes"),
    ("insurance", "Insurance"),
    ("rent_expense", "Rent (ground lease)"),
    ("ffe_reserve", "FF&E Reserve"),
    ("noi", "Net Operating Income"),
)

#: Future P&L lines: (engine, dotted path inside ``years[i]``, label). Exactly
#: what the engines publish — nothing is derived here.
PROJ_LINES: tuple[tuple[str, str, str], ...] = (
    ("revenue", "occupancy", "Occupancy"),
    ("revenue", "adr", "ADR"),
    ("revenue", "revpar", "RevPAR"),
    ("revenue", "rooms_revenue", "Rooms Revenue"),
    ("fb", "fb_revenue", "Food & Beverage Revenue"),
    ("fb", "other_revenue", "Other Operated Departments Revenue"),
    ("fb", "resort_fees", "Resort Fees"),
    ("expense", "total_revenue", "Total Revenue"),
    ("expense", "dept_expenses.rooms", "Rooms Department Expense"),
    ("expense", "dept_expenses.food_beverage", "F&B Department Expense"),
    ("expense", "dept_expenses.other_operated", "Other Operated Departments Expense"),
    ("expense", "dept_expenses.total", "Total Departmental Expenses"),
    ("expense", "undistributed.administrative_general", "Administrative & General"),
    ("expense", "undistributed.information_telecom", "Information & Telecommunications"),
    ("expense", "undistributed.sales_marketing", "Sales & Marketing"),
    ("expense", "undistributed.property_operations", "Property Operations & Maintenance"),
    ("expense", "undistributed.utilities", "Utilities"),
    ("expense", "undistributed.total", "Total Undistributed Expenses"),
    ("expense", "gop", "Gross Operating Profit"),
    ("expense", "mgmt_fee", "Management Fee"),
    ("expense", "fixed_charges.property_taxes", "Property Taxes"),
    ("expense", "fixed_charges.insurance", "Insurance"),
    ("expense", "fixed_charges.rent", "Rent"),
    ("expense", "fixed_charges.other_fixed", "Other Fixed Charges"),
    ("expense", "fixed_charges.total", "Total Fixed Charges"),
    ("expense", "noi_institutional", "NOI (before FF&E reserve)"),
    ("expense", "ffe_reserve", "FF&E Reserve"),
)


@dataclass(frozen=True)
class AssumptionSpec:
    """One analyst-editable Future P&L assumption (a ``field_overrides`` key)."""

    key: str
    label: str
    unit: str
    method: str
    integer: bool = False


#: Every assumption the Future P&L lets an analyst edit (driver cells, the
#: Assumptions panel and the projection period). The keys are the SAME
#: ``field_overrides`` keys ``ProjectionsSection.tsx`` writes; the method text
#: restates how the projection applies each one, from that panel's own copy.
ASSUMPTIONS: tuple[AssumptionSpec, ...] = (
    AssumptionSpec(
        "starting_occupancy", "Base-year occupancy", "fraction (0.762 = 76.2%)",
        "Year-1 starting point; later years follow the occupancy path",
    ),
    AssumptionSpec(
        "starting_adr", "Base-year ADR", "USD",
        "Year-1 starting point; later years grow from it",
    ),
    AssumptionSpec(
        "revpar_growth", "RevPAR growth", "fraction per year (0.045 = 4.5%)",
        "Compounds annually; drives ADR growth with the occupancy path held",
    ),
    AssumptionSpec(
        "expense_growth", "Dept. expense inflation", "fraction per year",
        "Compounds annually on departmental expenses",
    ),
    AssumptionSpec(
        "other_expense_growth", "Other expense inflation", "fraction per year",
        "Compounds annually on undistributed expenses; blank = follows dept. inflation",
    ),
    AssumptionSpec(
        "resort_fee_per_night", "Resort fee", "USD per night",
        "Charged per night, scaled by the capture rates below",
    ),
    AssumptionSpec(
        "resort_fee_capture_y1", "Resort fee capture — Base year (Year 1)", "fraction",
        "Applies to the Base year (Year 1) column",
    ),
    AssumptionSpec(
        "resort_fee_capture_y2", "Resort fee capture — Year 2", "fraction",
        "Applies to the Year 2 column",
    ),
    AssumptionSpec(
        "resort_fee_capture_y3", "Resort fee capture — Year 3+", "fraction",
        "Applies to Year 3 and every later year",
    ),
    AssumptionSpec(
        "mgmt_fee_pct", "Management fee", "fraction of total revenue",
        "Applied to each year's total revenue",
    ),
    AssumptionSpec(
        "hold_years", "Projection period (hold)", "years",
        "Number of modelled years", integer=True,
    ),
    AssumptionSpec(
        "stabilization_year", "Stabilization year", "model year (1-based)",
        "Selects which projection year the stabilized figures are read from; moves no return",
        integer=True,
    ),
)
ASSUMPTION_BY_KEY: dict[str, AssumptionSpec] = {a.key: a for a in ASSUMPTIONS}

ASSUMPTION_COLUMNS: tuple[str, ...] = (
    "Key", "Assumption", "Value", "Unit", "Source", "Method",
    "Note required", "Current note", "Note (your justification)",
)


# ─────────────────────────────── helpers ───────────────────────────────


class WorkbookFormatError(ValueError):
    """The upload is not a Fondok workbook of the expected format / deal."""


def to_number(v: Any) -> float | None:
    """A finite number from a stored extraction / engine value, else None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def parse_cell_number(raw: Any) -> tuple[float | None, bool]:
    """``(value, ok)`` for an UPLOADED cell. Blank → ``(None, True)``.

    Accepts numbers and plain numeric text (thousands separators and a leading
    ``$`` are tolerated). A ``%`` suffix, words, or anything else is refused
    (``ok=False``) — values round-trip in the units the export wrote, so the
    importer never guesses whether "73.8%" meant 0.738 or 73.8.
    """
    if raw is None:
        return None, True
    if isinstance(raw, bool):
        return None, False
    if isinstance(raw, (int, float)):
        f = float(raw)
        return (f, True) if math.isfinite(f) else (None, False)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, True
        s2 = s.replace(",", "").replace("$", "").strip()
        try:
            f = float(s2)
        except ValueError:
            return None, False
        return (f, True) if math.isfinite(f) else (None, False)
    return None, False


def same_number(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= 1e-9 * max(1.0, abs(a), abs(b))


def hist_cell_id(document_id: str, field_name: str) -> str:
    return f"{document_id}::{field_name}"


def split_hist_cell_id(cell_id: str) -> tuple[str, str] | None:
    if not isinstance(cell_id, str) or "::" not in cell_id:
        return None
    doc, fname = cell_id.split("::", 1)
    doc, fname = doc.strip(), fname.strip()
    if not doc or not fname:
        return None
    return doc, fname


def proj_cell_key(engine: str, path: str, year_index: int) -> str:
    """The Future P&L cell's stable key — also its comment ``cell_key`` suffix."""
    return f"{engine}.years[{year_index}].{path}"


def _style_header(ws: Any, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _readme(wb: Workbook, *, fmt: str, deal_id: str, deal_name: str | None,
            lines: Sequence[str]) -> None:
    ws = wb.create_sheet(README_SHEET)
    ws["A1"] = "Fondok editable workbook"
    ws["A1"].font = Font(bold=True, size=13)
    meta = (
        ("format", fmt),
        ("deal_id", deal_id),
        ("deal", deal_name or ""),
        ("exported_at", datetime.now(UTC).isoformat(timespec="seconds")),
    )
    for i, (k, v) in enumerate(meta, start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    start = 3 + len(meta) + 1
    for i, line in enumerate(lines):
        ws.cell(row=start + i, column=1, value=line)
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 60


def _check_readme(wb: Any, *, fmt: str, deal_id: str) -> None:
    if README_SHEET not in wb.sheetnames:
        raise WorkbookFormatError(
            "This is not a Fondok export (no README sheet). Export the workbook "
            "from Fondok, edit it, and import that file."
        )
    ws = wb[README_SHEET]
    meta: dict[str, str] = {}
    for row in ws.iter_rows(min_row=1, max_row=40, max_col=2, values_only=True):
        if row and isinstance(row[0], str) and row[1] is not None:
            meta.setdefault(row[0].strip(), str(row[1]).strip())
    if meta.get("format") != fmt:
        raise WorkbookFormatError(
            f"Wrong workbook format {meta.get('format')!r} — expected {fmt!r}."
        )
    if meta.get("deal_id", "").lower() != str(deal_id).lower():
        raise WorkbookFormatError(
            "This workbook was exported from a different deal — import refused."
        )


def _load(data: bytes) -> Any:
    try:
        return load_workbook(io.BytesIO(data), data_only=True)
    except Exception as exc:
        raise WorkbookFormatError("The file could not be read as an .xlsx workbook.") from exc


# ─────────────────────────── historical P&L ───────────────────────────


@dataclass(frozen=True)
class HistColumn:
    document_id: str
    label: str
    filename: str | None = None


@dataclass(frozen=True)
class HistCell:
    field_name: str
    value: float | None


def build_historicals_workbook(
    *,
    deal_id: str,
    deal_name: str | None,
    columns: Sequence[HistColumn],
    cells: Mapping[tuple[str, str], HistCell],
    lines: Sequence[tuple[str, str]] = HIST_LINES,
) -> bytes:
    """Render the Historical P&L workbook. ``cells`` is keyed ``(line_id, document_id)``."""
    wb = Workbook()
    ws = wb.active
    ws.title = HIST_SHEET
    ws.cell(row=1, column=1, value=LINE_HEADER)
    ws.cell(row=1, column=2, value="Line item")
    ws.cell(row=2, column=1, value="")
    ws.cell(row=2, column=2, value="Source document")
    for j, col in enumerate(columns):
        vc = 3 + 2 * j
        ws.cell(row=1, column=vc, value=col.label)
        ws.cell(row=1, column=vc + 1, value=ID_HEADER)
        ws.cell(row=2, column=vc, value=col.filename or "")
        ws.cell(row=2, column=vc + 1, value=col.document_id)
    ncols = 2 + 2 * len(columns)
    _style_header(ws, 1, ncols)
    for c in range(1, ncols + 1):
        ws.cell(row=2, column=c).font = _SUB_FONT
    for i, (line_id, label) in enumerate(lines):
        r = 3 + i
        ws.cell(row=r, column=1, value=line_id)
        ws.cell(row=r, column=2, value=label)
        for j, col in enumerate(columns):
            vc = 3 + 2 * j
            cell = cells.get((line_id, col.document_id))
            if cell is None:
                continue
            if cell.value is not None:
                ws.cell(row=r, column=vc, value=cell.value).number_format = "#,##0.####"
            ws.cell(row=r, column=vc + 1, value=hist_cell_id(col.document_id, cell.field_name))
    ws.column_dimensions["A"].hidden = True
    ws.column_dimensions["B"].width = 38
    for j in range(len(columns)):
        vc = 3 + 2 * j
        ws.column_dimensions[get_column_letter(vc)].width = 16
        ws.column_dimensions[get_column_letter(vc + 1)].hidden = True
    ws.freeze_panes = "C3"
    _readme(
        wb,
        fmt=HIST_FORMAT,
        deal_id=deal_id,
        deal_name=deal_name,
        lines=(
            "Historical P&L — one column per extracted statement, rows are USALI line items.",
            "Edit values in the visible year columns, save as .xlsx, then use Import on the "
            "Historical P&L.",
            "Each value has a hidden 'fondok_id' column beside it holding the stable id of the "
            "extracted line (document_id::field_name). Do not edit, move or delete hidden columns.",
            "Values are exported exactly as extracted (no unit conversion) — type plain numbers in "
            "the same units; '%' and text are rejected.",
            "A blank cell with no id means the statement has no such line: there is nothing at "
            "source to correct, so a value typed there is reported as a mapping error.",
            "Import shows a preview (changed values old → new, mapping errors, non-numeric cells). "
            "Nothing changes until you Apply; each change is saved as an analyst correction at "
            "source, exactly like editing the cell in the Data Room review.",
            "OM-embedded prior years are not included — correct those in the Data Room.",
        ),
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@dataclass
class HistCurrent:
    """What the deal holds right now, for import validation."""

    #: ``cell_id → current numeric value`` (None for a non-numeric / empty value)
    values: dict[str, float | None] = field(default_factory=dict)
    #: ``cell_id → raw stored value`` (shown as the old value when non-numeric)
    raw: dict[str, Any] = field(default_factory=dict)
    #: document ids on the deal that the export may reference
    documents: set[str] = field(default_factory=set)


def parse_historicals_import(
    data: bytes, *, deal_id: str, current: HistCurrent
) -> dict[str, Any]:
    """Diff an uploaded Historical P&L workbook against ``current``.

    Returns ``{changes, mapping_errors, non_numeric, unchanged}``. Never writes.
    """
    wb = _load(data)
    _check_readme(wb, fmt=HIST_FORMAT, deal_id=deal_id)
    if HIST_SHEET not in wb.sheetnames:
        raise WorkbookFormatError(f"Sheet {HIST_SHEET!r} is missing.")
    ws = wb[HIST_SHEET]
    max_col = ws.max_column
    header = [ws.cell(row=1, column=c).value for c in range(1, max_col + 1)]
    pairs: list[tuple[int, int, str]] = []  # (value col, id col, period label)
    for c in range(2, max_col + 1):
        if header[c - 1] == ID_HEADER:
            label = header[c - 2]
            pairs.append((c - 1, c, str(label) if label is not None else f"column {c - 1}"))
    if not pairs:
        raise WorkbookFormatError(
            "No 'fondok_id' columns found — the hidden id columns were removed or renamed."
        )

    changes: list[dict[str, Any]] = []
    mapping_errors: list[dict[str, Any]] = []
    non_numeric: list[dict[str, Any]] = []
    unchanged = 0
    seen: dict[str, float | None] = {}

    for r in range(3, ws.max_row + 1):
        line_id = ws.cell(row=r, column=1).value
        label = ws.cell(row=r, column=2).value
        line_label = str(label) if label is not None else str(line_id or f"row {r}")
        for vc, ic, period in pairs:
            ref = f"{get_column_letter(vc)}{r}"
            raw_val = ws.cell(row=r, column=vc).value
            raw_id = ws.cell(row=r, column=ic).value
            base = {"cell_ref": ref, "line_id": line_id, "line_label": line_label,
                    "period_label": period}
            id_txt = str(raw_id).strip() if raw_id is not None else ""
            if not id_txt:
                if raw_val not in (None, ""):
                    mapping_errors.append({
                        **base, "cell_id": None, "reason": "no_source_line",
                        "detail": "This statement has no extracted line here, so there is "
                                  "nothing at source to correct.",
                    })
                continue
            parts = split_hist_cell_id(id_txt)
            if parts is None or parts[0] not in current.documents or id_txt not in current.raw:
                mapping_errors.append({
                    **base, "cell_id": id_txt, "reason": "unknown_id",
                    "detail": "The hidden id does not match an extracted line on this deal "
                              "(edited id, removed document or re-extracted statement).",
                })
                continue
            new_val, ok = parse_cell_number(raw_val)
            if not ok:
                non_numeric.append({**base, "cell_id": id_txt, "raw": str(raw_val)})
                continue
            old_val = current.values.get(id_txt)
            if new_val is None:
                if old_val is None:
                    unchanged += 1
                else:
                    mapping_errors.append({
                        **base, "cell_id": id_txt, "reason": "cleared",
                        "detail": "A cleared cell cannot be imported — reject the field in "
                                  "the Data Room review instead.",
                    })
                continue
            if id_txt in seen:
                if not same_number(seen[id_txt], new_val):
                    mapping_errors.append({
                        **base, "cell_id": id_txt, "reason": "duplicate_id",
                        "detail": "The same extracted line appears twice with different values.",
                    })
                continue
            seen[id_txt] = new_val
            if same_number(old_val, new_val):
                unchanged += 1
                continue
            doc_id, fname = parts
            changes.append({
                **base,
                "cell_id": id_txt,
                "document_id": doc_id,
                "field_name": fname,
                "old_value": old_val if old_val is not None else current.raw.get(id_txt),
                "new_value": new_val,
            })
    # A later duplicate can invalidate an earlier change of the same id.
    dup_ids = {e["cell_id"] for e in mapping_errors if e["reason"] == "duplicate_id"}
    changes = [c for c in changes if c["cell_id"] not in dup_ids]
    return {
        "format": HIST_FORMAT,
        "changes": changes,
        "mapping_errors": mapping_errors,
        "non_numeric": non_numeric,
        "unchanged": unchanged,
    }


# ─────────────────────────── projections ───────────────────────────


def _get_path(d: Any, path: str) -> Any:
    cur = d
    for seg in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(seg)
    return cur


def projection_values(
    engine_outputs: Mapping[str, Any],
) -> tuple[int, dict[str, float | None]]:
    """``(year_count, {proj_cell_key: value})`` from ``{engine: outputs-dict}``."""
    years_by_engine: dict[str, list[Any]] = {}
    for eng in {e for e, _p, _l in PROJ_LINES}:
        out = engine_outputs.get(eng)
        yrs = out.get("years") if isinstance(out, dict) else None
        years_by_engine[eng] = yrs if isinstance(yrs, list) else []
    n = len(years_by_engine.get("revenue") or []) or max(
        (len(v) for v in years_by_engine.values()), default=0
    )
    values: dict[str, float | None] = {}
    for eng, path, _label in PROJ_LINES:
        yrs = years_by_engine.get(eng) or []
        for i in range(n):
            y = yrs[i] if i < len(yrs) else None
            values[proj_cell_key(eng, path, i)] = to_number(_get_path(y, path))
    return n, values


def build_projections_workbook(
    *,
    deal_id: str,
    deal_name: str | None,
    year_headers: Sequence[str],
    values: Mapping[str, float | None],
    assumptions: Sequence[Mapping[str, Any]],
) -> bytes:
    """Render the Future P&L + Assumptions workbook.

    ``assumptions`` rows carry ``key, label, value, unit, source, method,
    note_required, current_note`` — already resolved by the caller.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = PROJ_SHEET
    ws.cell(row=1, column=1, value=PATH_HEADER)
    ws.cell(row=1, column=2, value="Line item (computed — read-only)")
    for i, h in enumerate(year_headers):
        ws.cell(row=1, column=3 + i, value=h)
    _style_header(ws, 1, 2 + len(year_headers))
    for r_i, (eng, path, label) in enumerate(PROJ_LINES):
        r = 2 + r_i
        ws.cell(row=r, column=1, value=f"{eng}.{path}")
        ws.cell(row=r, column=2, value=label)
        for i in range(len(year_headers)):
            v = values.get(proj_cell_key(eng, path, i))
            c = ws.cell(row=r, column=3 + i, value=v)
            c.fill = _READONLY_FILL
            if v is not None:
                c.number_format = "#,##0.####"
    if not year_headers:
        ws.cell(row=2 + len(PROJ_LINES) + 1, column=2,
                value="No model run yet — run the model to populate the projection.")
    ws.column_dimensions["A"].hidden = True
    ws.column_dimensions["B"].width = 40
    for i in range(len(year_headers)):
        ws.column_dimensions[get_column_letter(3 + i)].width = 16
    ws.freeze_panes = "C2"

    wa = wb.create_sheet(ASSUMPTION_SHEET)
    for c, h in enumerate(ASSUMPTION_COLUMNS, start=1):
        wa.cell(row=1, column=c, value=h)
    _style_header(wa, 1, len(ASSUMPTION_COLUMNS))
    for r_i, a in enumerate(assumptions):
        r = 2 + r_i
        wa.cell(row=r, column=1, value=a["key"])
        wa.cell(row=r, column=2, value=a.get("label"))
        wa.cell(row=r, column=3, value=a.get("value"))
        wa.cell(row=r, column=4, value=a.get("unit"))
        wa.cell(row=r, column=5, value=a.get("source") or "—")
        wa.cell(row=r, column=6, value=a.get("method"))
        wa.cell(row=r, column=7, value="Yes" if a.get("note_required") else "No")
        wa.cell(row=r, column=8, value=a.get("current_note") or "")
        for c in (1, 2, 4, 5, 6, 7, 8):
            wa.cell(row=r, column=c).fill = _READONLY_FILL
    for c, w in zip(range(1, 10), (26, 36, 14, 26, 22, 52, 12, 30, 40), strict=True):
        wa.column_dimensions[get_column_letter(c)].width = w
    wa.freeze_panes = "C2"
    _readme(
        wb,
        fmt=PROJ_FORMAT,
        deal_id=deal_id,
        deal_name=deal_name,
        lines=(
            "Future P&L — the engine projection by model year, plus every analyst-editable "
            "assumption that drives it.",
            "Only the Assumptions sheet is importable: edit the Value column, add a Note for every "
            "row whose 'Note required' is Yes, save as .xlsx, then use Import on the Future P&L.",
            "Key is the stable field_overrides key — do not edit it.",
            "Values use the units in the Unit column (fractions, not percents: 0.045 = 4.5%).",
            "Future P&L cells are computed by the engines; a change there is reported as "
            "'computed — edit the assumption instead' and is never applied.",
            "Import shows a preview of changed assumptions (old → new) and any rejected rows. "
            "Nothing changes until you Apply; applied values are saved as analyst overrides, "
            "with your note, through the same save path as the Assumptions panel.",
        ),
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def parse_projections_import(
    data: bytes,
    *,
    deal_id: str,
    current_values: Mapping[str, float | None],
    current_assumptions: Mapping[str, Any],
    needs_note: Callable[[str], bool],
) -> dict[str, Any]:
    """Diff an uploaded Future P&L workbook. Never writes.

    Returns ``{changes, rejected, mapping_errors, non_numeric, computed_edits,
    unchanged}``; only ``changes`` are eligible for Apply.
    """
    wb = _load(data)
    _check_readme(wb, fmt=PROJ_FORMAT, deal_id=deal_id)
    if ASSUMPTION_SHEET not in wb.sheetnames:
        raise WorkbookFormatError(f"Sheet {ASSUMPTION_SHEET!r} is missing.")

    changes: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    mapping_errors: list[dict[str, Any]] = []
    non_numeric: list[dict[str, Any]] = []
    computed_edits: list[dict[str, Any]] = []
    unchanged = 0

    wa = wb[ASSUMPTION_SHEET]
    header = [wa.cell(row=1, column=c).value for c in range(1, wa.max_column + 1)]

    def _col(name: str, fallback: int) -> int:
        for i, h in enumerate(header, start=1):
            if isinstance(h, str) and h.strip().lower().startswith(name.lower()):
                return i
        return fallback

    c_key, c_val, c_note = _col("Key", 1), _col("Value", 3), _col("Note (", 9)
    seen: set[str] = set()
    for r in range(2, wa.max_row + 1):
        raw_key = wa.cell(row=r, column=c_key).value
        key = str(raw_key).strip() if raw_key is not None else ""
        raw_val = wa.cell(row=r, column=c_val).value
        raw_note = wa.cell(row=r, column=c_note).value
        note = str(raw_note).strip() if raw_note is not None else ""
        ref = f"{get_column_letter(c_val)}{r}"
        if not key:
            if raw_val not in (None, ""):
                mapping_errors.append({"cell_ref": ref, "key": None, "reason": "missing_key",
                                       "detail": "A value with no Key cannot be mapped."})
            continue
        spec = ASSUMPTION_BY_KEY.get(key)
        if spec is None:
            mapping_errors.append({
                "cell_ref": ref, "key": key, "reason": "unknown_key",
                "detail": "Not an editable Future P&L assumption key.",
            })
            continue
        if key in seen:
            mapping_errors.append({"cell_ref": ref, "key": key, "reason": "duplicate_key",
                                   "detail": "The key appears more than once."})
            continue
        seen.add(key)
        new_val, ok = parse_cell_number(raw_val)
        if ok and new_val is not None and spec.integer and not float(new_val).is_integer():
            ok = False
        if not ok:
            non_numeric.append({"cell_ref": ref, "key": key, "label": spec.label,
                                "raw": str(raw_val),
                                "detail": "Must be a whole number." if spec.integer else None})
            continue
        old_val = to_number(current_assumptions.get(key))
        if new_val is None:
            if old_val is None:
                unchanged += 1
            else:
                rejected.append({
                    "cell_ref": ref, "key": key, "label": spec.label, "old_value": old_val,
                    "new_value": None, "reason": "cleared",
                    "detail": "Clearing is not imported — use Reset on the Future P&L.",
                })
            continue
        if spec.integer:
            new_val = float(int(new_val))
        if same_number(old_val, new_val):
            unchanged += 1
            continue
        required = needs_note(key)
        row = {"cell_ref": ref, "key": key, "label": spec.label, "old_value": old_val,
               "new_value": int(new_val) if spec.integer else new_val,
               "note": note, "note_required": required}
        if required and not note:
            rejected.append({**row, "reason": "note_required",
                             "detail": "This assumption moves the model — add a Note."})
            continue
        changes.append(row)

    if PROJ_SHEET in wb.sheetnames:
        wp = wb[PROJ_SHEET]
        headers = [wp.cell(row=1, column=c).value for c in range(3, wp.max_column + 1)]
        for r in range(2, wp.max_row + 1):
            path = wp.cell(row=r, column=1).value
            if not isinstance(path, str) or "." not in path:
                continue
            eng, sub = path.split(".", 1)
            label = wp.cell(row=r, column=2).value
            for i in range(len(headers)):
                ck = proj_cell_key(eng, sub, i)
                if ck not in current_values:
                    continue
                raw = wp.cell(row=r, column=3 + i).value
                new_val, ok = parse_cell_number(raw)
                old_val = current_values.get(ck)
                if ok and same_number(old_val, new_val):
                    continue
                computed_edits.append({
                    "cell_ref": f"{get_column_letter(3 + i)}{r}",
                    "cell_key": ck, "line_label": label, "year_index": i,
                    "period_label": headers[i], "old_value": old_val,
                    "new_value": new_val if ok else str(raw),
                    "reason": "computed",
                    "detail": "Computed by the engines — edit the assumption instead.",
                })

    return {
        "format": PROJ_FORMAT,
        "changes": changes,
        "rejected": rejected,
        "mapping_errors": mapping_errors,
        "non_numeric": non_numeric,
        "computed_edits": computed_edits,
        "unchanged": unchanged,
    }


__all__ = [
    "ASSUMPTIONS",
    "ASSUMPTION_BY_KEY",
    "HIST_FORMAT",
    "HIST_LINES",
    "PROJ_FORMAT",
    "PROJ_LINES",
    "HistCell",
    "HistColumn",
    "HistCurrent",
    "WorkbookFormatError",
    "build_historicals_workbook",
    "build_projections_workbook",
    "hist_cell_id",
    "parse_historicals_import",
    "parse_projections_import",
    "proj_cell_key",
    "projection_values",
    "split_hist_cell_id",
    "to_number",
]
