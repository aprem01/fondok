"""Deterministic extractor for STR / CoStar Trend report workbooks.

STR ships three workbook layouts we recognize (all standardized —
same tabs, same labels, every report):

1. **Monthly STAR** (modern .xlsx, e.g. ``ANG-20241200-USD-E.xlsx``):
   ``Table of Contents`` + per-comp-set groups ``Glance_N`` /
   ``Summary_N`` / ``Comp_N`` / ``Response_N`` + ``Help``. The
   ``Glance_1`` tab ("Tab 2 - Monthly Performance at a Glance") carries
   the subject's Running-12-Month Occ/ADR/RevPAR and the MPI/ARI/RGI
   penetration indices; ``Comp_1`` carries the 18-month subject
   monthly series; ``Response_1`` carries the authoritative comp-set
   roster (STR# / Name / Rooms).

2. **Weekly STAR** (modern .xlsx, e.g. ``56387-20250525-USD-E.xlsx``):
   same family but "For the Week of:" — daily/weekly data only, no
   trailing-twelve rollup exists in the file. We extract the roster
   (the ground truth downstream Available-Rooms math needs) and leave
   TTM metrics unset rather than mislabel weekly numbers as TTM.

3. **Legacy Custom Trend** (.xls parsed via xlrd, e.g. the golden-set
   ``sample_str_trend.xls``): numbered tabs ``2) By Measure`` …
   ``10) Response``. ``By Measure`` carries the monthly Occ/ADR/RevPAR
   matrix (years × months) + Total Year rollups; ``Response`` carries
   the roster (STR Code / Name of Establishment / Rooms + a "Total
   Properties" row). Custom Trend reports have no subject-vs-comp-set
   split, so no penetration indices are published — every roster row
   is a comp-set member.

Everything is label-anchored (find the row by its label text, find the
column by its header text) — never fixed coordinates — so small layout
shifts between report vintages don't silently misread cells. Any
anchor that fails to resolve makes the whole extraction return
``None`` (fall through to the LLM) rather than emit a partial guess.

Reads ``ParsedPage.tables`` (the lossless 2D cell grid produced by
``_xls_sheet_to_page``), never ``ParsedPage.text``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..models import ParsedDocument

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "str_trend"

# Month labels as they appear across variants.
_MONTH_ABBREV = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_FULL = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

_YEAR_RE = re.compile(r"^(19|20)\d{2}$")

# Roster header cell labels per variant (lowercased, exact match).
_ROSTER_ID_LABELS = {"str#", "str id", "str code"}
_ROSTER_NAME_LABELS = {"name", "name of establishment"}
_ROSTER_ROOMS_LABEL = "rooms"


@dataclass
class TemplateExtractResult:
    """Outcome of a successful template extraction.

    ``fields`` uses the exact shape the LLM extractor emits so the
    caller can persist it through the same ``extraction_results``
    insert path with no translation.
    """

    fields: list[dict[str, Any]]
    template_name: str
    coverage_note: str


def try_template_extract(
    parsed: ParsedDocument, doc_type: str
) -> TemplateExtractResult | None:
    """Attempt deterministic extraction for STR Trend.

    Returns ``None`` unless the document unambiguously matches a known
    STR Trend layout AND every structural anchor resolves. The caller
    treats ``None`` as "fall through to the LLM extractor".

    Note: This is called via the dispatcher in __init__.py, which first
    checks the doc_type matches ("STR" / "STR_TREND").
    """
    try:
        return _try_str_trend(parsed)
    except Exception:  # noqa: BLE001 — template misread must never break extraction
        logger.warning(
            "template extraction: unexpected error on %s — falling back to LLM",
            getattr(parsed, "filename", "?"),
            exc_info=True,
        )
        return None


# ── shared plumbing ──────────────────────────────────────────────────


def _to_float(cell: str) -> float | None:
    """Parse a grid cell into a float, or ``None``."""
    s = (cell or "").strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _to_int(cell: str) -> int | None:
    v = _to_float(cell)
    if v is None or not float(v).is_integer():
        return None
    return int(v)


def _field(
    name: str,
    value: Any,
    *,
    unit: str | None = None,
    page: int | None = None,
) -> dict[str, Any]:
    """One extraction-field row in the LLM extractor's wire shape.

    ``confidence=1.0`` — the value was read deterministically from a
    known cell of a standardized report, not inferred.
    """
    out: dict[str, Any] = {
        "field_name": name,
        "value": value,
        "confidence": 1.0,
        "unit": unit,
    }
    if page is not None:
        out["source_page"] = page
    return out


@dataclass
class _Sheet:
    name: str
    grid: list[list[str]]
    page_num: int

    def cell(self, r: int, c: int) -> str:
        try:
            return self.grid[r][c]
        except IndexError:
            return ""


def _sheets_of(parsed: ParsedDocument) -> list[_Sheet] | None:
    """Materialize per-sheet grids; ``None`` when this isn't a
    sheet-per-page workbook parse (pdf, docx, …)."""
    if parsed.parser not in ("xlrd", "openpyxl"):
        return None
    sheets: list[_Sheet] = []
    for page in parsed.pages:
        name = (page.metadata or {}).get("sheet_name")
        if not name:
            continue
        grid = page.tables[0] if page.tables else []
        sheets.append(_Sheet(name=str(name), grid=grid, page_num=page.page_num))
    # A trend workbook always ships several tabs; anything smaller is
    # not what we think it is.
    if len(sheets) < 3:
        return None
    return sheets


def _find_label(
    grid: list[list[str]], label: str, *, col: int | None = None
) -> tuple[int, int] | None:
    """First (row, col) whose stripped cell equals ``label``."""
    for r, row in enumerate(grid):
        if col is not None:
            if col < len(row) and row[col].strip() == label:
                return (r, col)
            continue
        for c, cell in enumerate(row):
            if cell.strip() == label:
                return (r, c)
    return None


def _grid_contains(grid: list[list[str]], needle: str, *, max_rows: int = 6) -> bool:
    low = needle.lower()
    for row in grid[:max_rows]:
        for cell in row:
            if low in cell.lower():
                return True
    return False


# ── roster (Response tab) ────────────────────────────────────────────


@dataclass
class _RosterRow:
    str_id: str
    name: str
    rooms: int | None


def _parse_roster(grid: list[list[str]]) -> list[_RosterRow]:
    """Pull the property roster out of a Response-tab grid.

    Anchored on the header row that carries an STR-id label
    (``STR#`` / ``STR ID`` / ``STR Code``), a name label and a
    ``Rooms`` label. Rows are consumed until the id column stops
    holding a numeric STR id (the totals / legend rows below the
    roster). Only the FIRST such block is read — the "Segmentation
    Data" block that follows on monthly reports repeats the same
    roster.
    """
    header: tuple[int, int, int, int] | None = None  # row, id_col, name_col, rooms_col
    for r, row in enumerate(grid):
        id_col = name_col = rooms_col = None
        for c, cell in enumerate(row):
            s = cell.strip().lower()
            if s in _ROSTER_ID_LABELS and id_col is None:
                id_col = c
            elif s in _ROSTER_NAME_LABELS and name_col is None:
                name_col = c
            elif s == _ROSTER_ROOMS_LABEL and rooms_col is None:
                rooms_col = c
        if id_col is not None and name_col is not None and rooms_col is not None:
            header = (r, id_col, name_col, rooms_col)
            break
    if header is None:
        return []

    r0, id_col, name_col, rooms_col = header
    rows: list[_RosterRow] = []
    for row in grid[r0 + 1:]:
        str_id = row[id_col].strip() if id_col < len(row) else ""
        if not str_id.isdigit():
            break
        name = row[name_col].strip() if name_col < len(row) else ""
        rooms = _to_int(row[rooms_col]) if rooms_col < len(row) else None
        if not name:
            break
        rows.append(_RosterRow(str_id=str_id, name=name, rooms=rooms))
    return rows


def _find_subject_id(sheets: list[_Sheet]) -> str | None:
    """Subject property's STR id from the standardized header block
    (``Property ID: 56387`` on monthly reports, ``STR # 56387`` on
    weekly ones). ``None`` on Custom Trend reports, which have no
    subject."""
    pat = re.compile(r"(?:Property ID|STR\s*#)\s*:?\s*(\d{3,})")
    for sheet in sheets:
        for row in sheet.grid[:6]:
            for cell in row:
                m = pat.search(cell)
                if m:
                    return m.group(1)
    return None


# ── header metadata ──────────────────────────────────────────────────


def _subject_name_from_header(grid: list[list[str]]) -> str | None:
    """Modern STAR reports put the subject on header line 2 as
    ``<name>        <address>…`` (runs of spaces separate segments)."""
    for row in grid[1:4]:
        for cell in row:
            s = cell.strip()
            if not s or s.lower().startswith(("property id", "str #", "for the", "tab ")):
                continue
            name = re.split(r"\s{2,}", s)[0].strip()
            if name:
                return name
        break  # only the first non-empty header line
    return None


def _report_year_from_headers(sheets: list[_Sheet]) -> int | None:
    """Year of the report's period-ending, from ``For the Month of:
    December 2024`` / ``For the Week of: … 2025`` header lines."""
    pat = re.compile(r"For the (?:Month|Week) of:.*?((?:19|20)\d{2})")
    for sheet in sheets:
        for row in sheet.grid[:6]:
            for cell in row:
                m = pat.search(cell)
                if m:
                    return int(m.group(1))
    return None


# ── variant A: Monthly STAR (.xlsx) ──────────────────────────────────


def _extract_monthly_glance(sheet: _Sheet) -> list[dict[str, Any]] | None:
    """Subject TTM Occ/ADR/RevPAR + MPI/ARI/RGI from the Monthly
    Performance at a Glance tab.

    Layout (label-anchored):

        …  Occupancy (%)  …            ADR  …            RevPAR  …
           My Prop | Comp Set | Index (MPI)   (per metric block)
        Current Month     | …
        Year To Date      | …
        Running 3 Month   | …
        Running 12 Month  | …   ← the TTM row we want

    The first ``Running 12 Month`` row is the level block (a %-change
    block with the same labels follows further down).
    """
    grid = sheet.grid
    # Metric header row: carries all three block titles.
    metric_row = metric_cols = None
    for r, row in enumerate(grid):
        cols: dict[str, int] = {}
        for c, cell in enumerate(row):
            s = cell.strip()
            if s == "Occupancy (%)":
                cols["occ"] = c
            elif s == "ADR":
                cols["adr"] = c
            elif s == "RevPAR":
                cols["revpar"] = c
        if len(cols) == 3:
            metric_row, metric_cols = r, cols
            break
    if metric_row is None or metric_cols is None:
        return None

    # Sub-header row (next row): My Prop / Comp Set / Index (XXX) per block.
    sub = grid[metric_row + 1] if metric_row + 1 < len(grid) else []
    ordered = sorted(metric_cols.items(), key=lambda kv: kv[1])
    bounds = {
        key: (start, ordered[i + 1][1] if i + 1 < len(ordered) else len(sub) + 64)
        for i, (key, start) in enumerate(ordered)
    }
    my_prop_col: dict[str, int] = {}
    index_col: dict[str, int] = {}
    for c, cell in enumerate(sub):
        s = cell.strip()
        for key, (lo, hi) in bounds.items():
            if lo <= c < hi:
                if s == "My Prop":
                    my_prop_col[key] = c
                elif s.startswith("Index ("):
                    index_col[key] = c
    if set(my_prop_col) != {"occ", "adr", "revpar"}:
        return None

    # First "Running 12 Month" row below the sub-header = the TTM levels.
    ttm = None
    for row in grid[metric_row + 2:]:
        if any(cell.strip() == "Running 12 Month" for cell in row):
            ttm = row
            break
    if ttm is None:
        return None

    def _at(col: int) -> float | None:
        return _to_float(ttm[col]) if col < len(ttm) else None

    occ = _at(my_prop_col["occ"])
    adr = _at(my_prop_col["adr"])
    revpar = _at(my_prop_col["revpar"])
    if occ is None or adr is None or revpar is None:
        return None
    if not (0 < occ <= 100 and adr > 0 and revpar > 0):
        return None

    page = sheet.page_num
    fields = [
        _field("ttm_performance.subject.occupancy_pct", occ, unit="percent", page=page),
        _field("ttm_performance.subject.adr_usd", adr, unit="USD", page=page),
        _field("ttm_performance.subject.revpar_usd", revpar, unit="USD", page=page),
    ]
    # Penetration indices: STR publishes on the 100-scale (100 =
    # parity); the canonical field namespace uses 1.00 = parity, so
    # divide (the LLM extractor is instructed the same way).
    for key, fname in (
        ("occ", "ttm_performance.indices.mpi_occupancy_index"),
        ("adr", "ttm_performance.indices.ari_adr_index"),
        ("revpar", "ttm_performance.indices.rgi_revpar_index"),
    ):
        col = index_col.get(key)
        val = _at(col) if col is not None else None
        if val is not None and val > 0:
            fields.append(_field(fname, val / 100.0, unit="ratio", page=page))
    return fields


def _extract_monthly_series_from_comp(sheet: _Sheet) -> list[dict[str, Any]]:
    """Subject monthly Occ/ADR/RevPAR from the Competitive-set tab.

    Layout per metric block::

        Occupancy (%) | 2023 …       | 2024 …          | Year to Date …
                      | Jul Aug … Dec | Jan … Dec       | 2022 2023 2024
        My Property   | v v v …

    Month columns are identified by month-abbreviation labels; the
    year each column belongs to comes from the most recent year label
    at or left of that column. Cells under the Year-to-Date / Running
    sections are year labels, not months, so they're skipped naturally.
    """
    grid = sheet.grid
    fields: list[dict[str, Any]] = []
    metric_names = {
        "Occupancy (%)": ("occupancy_pct", "percent"),
        "ADR": ("adr_usd", "USD"),
        "RevPAR": ("revpar_usd", "USD"),
    }
    for r, row in enumerate(grid):
        label = row[1].strip() if len(row) > 1 else ""
        if label not in metric_names:
            continue
        attr, unit = metric_names[label]
        # Year labels live on the metric row itself.
        year_at: list[tuple[int, int]] = [
            (c, int(cell.strip()))
            for c, cell in enumerate(row)
            if _YEAR_RE.match(cell.strip())
        ]
        if not year_at or r + 2 >= len(grid):
            continue
        month_row = grid[r + 1]
        # Column → (year, month) map for genuine month columns only.
        col_period: dict[int, tuple[int, int]] = {}
        for c, cell in enumerate(month_row):
            mnum = _MONTH_ABBREV.get(cell.strip().lower()[:3]) if cell.strip() else None
            if mnum is None or not cell.strip().isalpha():
                continue
            years_left = [y for (yc, y) in year_at if yc <= c]
            if not years_left:
                continue
            col_period[c] = (years_left[-1], mnum)
        if not col_period:
            continue
        # First "My Property" row after the month header, before % Chg.
        for vrow in grid[r + 2: r + 8]:
            head = vrow[1].strip() if len(vrow) > 1 else ""
            if head == "% Chg":
                break
            if head != "My Property":
                continue
            for c, (year, month) in sorted(col_period.items()):
                val = _to_float(vrow[c]) if c < len(vrow) else None
                if val is not None:
                    fields.append(
                        _field(
                            f"ttm_performance.subject.monthly.{year}_{month:02d}.{attr}",
                            val,
                            unit=unit,
                            page=sheet.page_num,
                        )
                    )
            break
    return fields


# ── variant C: legacy Custom Trend (.xls) ────────────────────────────


def _extract_by_measure(sheet: _Sheet) -> list[dict[str, Any]]:
    """Monthly + annual subject series from the ``By Measure`` tab.

    Layout per metric block::

        Occupancy (%)
             January February … December   [gap]  Total Year  <M> YTD
        2015  v v v …                              v
        …
        2023  v v                                  (blank until year ends)
        Avg   …

    Emits the most-recent 12 populated months (parity with what the
    LLM extractor is instructed to pull) plus every ``Total Year``
    annual rollup.
    """
    grid = sheet.grid
    metric_names = {
        "Occupancy (%)": ("occupancy_pct", "percent"),
        "ADR ($)": ("adr_usd", "USD"),
        "RevPAR ($)": ("revpar_usd", "USD"),
    }
    fields: list[dict[str, Any]] = []
    for r, row in enumerate(grid):
        label = row[1].strip() if len(row) > 1 else ""
        if label not in metric_names or r + 2 >= len(grid):
            continue
        attr, unit = metric_names[label]
        header = grid[r + 1]
        month_cols: dict[int, int] = {}
        total_year_col: int | None = None
        for c, cell in enumerate(header):
            s = cell.strip().lower()
            if s in _MONTH_FULL:
                month_cols[c] = _MONTH_FULL[s]
            elif s == "total year":
                total_year_col = c
        if not month_cols:
            continue
        # Year rows until the Avg row.
        monthly_points: list[tuple[int, int, float]] = []  # (year, month, value)
        for vrow in grid[r + 2:]:
            head = vrow[1].strip() if len(vrow) > 1 else ""
            if not _YEAR_RE.match(head):
                break
            year = int(head)
            for c, month in month_cols.items():
                val = _to_float(vrow[c]) if c < len(vrow) else None
                if val is not None:
                    monthly_points.append((year, month, val))
            if total_year_col is not None and total_year_col < len(vrow):
                annual = _to_float(vrow[total_year_col])
                if annual is not None:
                    fields.append(
                        _field(
                            f"ttm_performance.subject.annual.{year}.{attr}",
                            annual,
                            unit=unit,
                            page=sheet.page_num,
                        )
                    )
        # Most-recent 12 populated months.
        monthly_points.sort(key=lambda p: (p[0], p[1]))
        for year, month, val in monthly_points[-12:]:
            fields.append(
                _field(
                    f"ttm_performance.subject.monthly.{year}_{month:02d}.{attr}",
                    val,
                    unit=unit,
                    page=sheet.page_num,
                )
            )
    return fields


def _latest_monthly_year(fields: list[dict[str, Any]]) -> int | None:
    years = []
    for f in fields:
        m = re.match(
            r"ttm_performance\.subject\.monthly\.((?:19|20)\d{2})_\d{2}\.",
            f["field_name"],
        )
        if m:
            years.append(int(m.group(1)))
    return max(years) if years else None


# ── top-level dispatch ───────────────────────────────────────────────


def _try_str_trend(parsed: ParsedDocument) -> TemplateExtractResult | None:
    sheets = _sheets_of(parsed)
    if sheets is None:
        return None

    lower_names = [s.name.lower() for s in sheets]
    # Every STR trend workbook ships a Table of Contents, a Help tab
    # and at least one Response tab. Anything missing → not ours.
    if not any("table of contents" in n for n in lower_names):
        return None
    if not any(n == "help" or n.endswith(") help") for n in lower_names):
        return None

    # Primary Response tab = first sheet (report order) whose name
    # contains "response" (not "segmentation") and whose first header
    # line says "Response Report".
    response = next(
        (
            s
            for s in sheets
            if "response" in s.name.lower()
            and "segmentation" not in s.name.lower()
            and _grid_contains(s.grid, "response report", max_rows=2)
        ),
        None,
    )
    if response is None:
        return None

    roster = _parse_roster(response.grid)
    if not roster:
        return None

    subject_id = _find_subject_id(sheets)
    comps = [r for r in roster if r.str_id != subject_id]
    if not comps or any(r.rooms is None for r in comps):
        # A roster row without a parseable room count means the layout
        # drifted — don't guess, let the LLM look at it. (``rooms == 0``
        # is legitimate: STR reports closed comps as "Closed - <name>"
        # with 0 rooms.)
        return None

    fields: list[dict[str, Any]] = []
    notes: list[str] = []
    page = response.page_num
    for i, comp in enumerate(comps, start=1):
        fields.append(_field(f"ttm_performance.compset.{i}.name", comp.name, page=page))
        fields.append(
            _field(f"ttm_performance.compset.{i}.keys", comp.rooms, unit="rooms", page=page)
        )
    fields.append(_field("comp_set.comp_set_size", len(comps), page=page))
    fields.append(
        _field(
            "comp_set.total_keys",
            sum(r.rooms for r in comps if r.rooms),
            unit="rooms",
            page=page,
        )
    )

    is_custom_trend = any("by measure" in n for n in lower_names)
    is_weekly = _grid_contains(response.grid, "for the week of:", max_rows=6)

    if is_custom_trend:
        # Legacy .xls Custom Trend.
        by_measure = next(s for s in sheets if "by measure" in s.name.lower())
        name_match = None
        for row in by_measure.grid[:4]:
            for cell in row:
                m = re.match(r"Custom Trend:\s*(.+)", cell.strip())
                if m:
                    name_match = m.group(1)
                    break
        if name_match:
            subject_name = re.split(r"\s*\(", name_match)[0].strip()
            if subject_name:
                fields.append(
                    _field(
                        "ttm_performance.subject.name",
                        subject_name,
                        page=by_measure.page_num,
                    )
                )
        series = _extract_by_measure(by_measure)
        if not series:
            # By Measure tab present but unreadable → layout drift.
            return None
        fields.extend(series)
        year = _latest_monthly_year(series)
        if year is not None:
            fields.append(_field("str_trend.report_year", year))
        notes.append(
            "legacy Custom Trend (.xls): monthly + annual subject series "
            "and full roster extracted; Custom Trend reports publish no "
            "subject-vs-comp-set penetration indices and no TTM rollup "
            "row, so ttm_performance.subject.{occupancy_pct,adr_usd,"
            "revpar_usd} and MPI/ARI/RGI are not emitted"
        )
        variant = "custom_trend_xls"
    elif is_weekly:
        # Weekly STAR: no trailing-twelve data exists in the file.
        subject_name = _subject_name_from_header(response.grid)
        if subject_name:
            fields.append(_field("ttm_performance.subject.name", subject_name, page=page))
        year = _report_year_from_headers(sheets)
        if year is not None:
            fields.append(_field("str_trend.report_year", year))
        notes.append(
            "weekly STAR report: comp-set roster + rollups extracted; the "
            "file carries daily/weekly data only (no Running 12 Month "
            "block), so TTM subject metrics, penetration indices and the "
            "monthly series are not emitted"
        )
        variant = "weekly_star_xlsx"
    else:
        # Monthly STAR: Glance tab is mandatory for a template hit.
        glance = next(
            (
                s
                for s in sheets
                if _grid_contains(
                    s.grid, "monthly performance at a glance", max_rows=1
                )
            ),
            None,
        )
        if glance is None:
            return None
        glance_fields = _extract_monthly_glance(glance)
        if glance_fields is None:
            return None
        fields.extend(glance_fields)

        subject_name = _subject_name_from_header(glance.grid)
        if subject_name:
            fields.append(
                _field("ttm_performance.subject.name", subject_name, page=glance.page_num)
            )
        year = _report_year_from_headers(sheets)
        if year is not None:
            fields.append(_field("str_trend.report_year", year))

        comp_tab = next(
            (
                s
                for s in sheets
                if _grid_contains(s.grid, "competitive set report", max_rows=1)
                or _grid_contains(s.grid, "competitive set data", max_rows=1)
            ),
            None,
        )
        if comp_tab is not None:
            series = _extract_monthly_series_from_comp(comp_tab)
            fields.extend(series)
            if not series:
                notes.append("Comp tab present but monthly series not readable")
        else:
            notes.append("no Competitive Set Data tab — monthly series not emitted")
        notes.append(
            "monthly STAR report: subject TTM Occ/ADR/RevPAR, MPI/ARI/RGI "
            "(converted from 100-scale to 1.00=parity), comp-set roster + "
            "rollups and subject monthly series extracted; day-of-week and "
            "segmentation breakdowns left to future template versions"
        )
        variant = "monthly_star_xlsx"

    return TemplateExtractResult(
        fields=fields,
        template_name=TEMPLATE_NAME,
        coverage_note=f"variant={variant}; " + "; ".join(notes),
    )
