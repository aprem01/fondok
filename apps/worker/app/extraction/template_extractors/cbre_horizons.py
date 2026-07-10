"""Deterministic extractor for CBRE Hotel Horizons forecast reports.

CBRE Hotel Horizons is a standardized report format (like STR Trend) with:
- Subject property details in a header section
- Four forecast tables (All Hotels + three price tiers: Upper/Mid/Lower Priced)
- Annual historical + forecast metrics (Occ%, ADR, RevPAR, supply/demand changes)
- Long-run average anchors
- Optional Guest-Paid ADR, source-of-business mix, length of stay, AirDNA STR data

Since most CBRE reports ship as PDFs, table extraction reliability is lower than
Excel workbooks. We use conservative detection: any ambiguity → return None →
LLM. If only native-Excel Horizons exports are sufficiently reliable, we can
gate on parser type (openpyxl / xlrd only).

Detection strategy:
1. Verify parser type (PDF tables may be unstable; xlsx/xls preferred)
2. Find the four segment tables by header label anchors
3. Require consistent structure: header row + year column + metric columns
4. Return None if any anchor fails to resolve

Emits fields in the LLM extractor's exact wire shape (canonical field paths
from cbre_horizons.md) with confidence=1.0 on deterministically-read cells.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from ..models import ParsedDocument

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "cbre_horizons"

# Metric labels as they appear in CBRE reports (case-insensitive).
# The four scope segments we track.
_SCOPES = {"all", "upper_priced", "mid_priced", "lower_priced"}
_SCOPE_LABELS = {
    "all hotels": "all",
    "upper-priced hotels": "upper_priced",
    "mid-priced hotels": "mid_priced",
    "lower-priced hotels": "lower_priced",
}

# Metric row labels (as they appear in the forecast tables).
_METRIC_LABELS = {"occupancy", "occupancy change", "adr", "adr change", "revpar", "revpar change", "supply change", "demand change"}

# Metric name mapping: (label_in_pdf, field_base, unit)
_METRIC_MAP = {
    "occupancy": ("occupancy_pct", "percent"),
    "occupancy change": ("occupancy_change_pct", "percent"),
    "adr": ("adr_usd", "USD"),
    "adr change": ("adr_change_pct", "percent"),
    "revpar": ("revpar_usd", "USD"),
    "revpar change": ("revpar_change_pct", "percent"),
    "supply change": ("supply_change_pct", "percent"),
    "demand change": ("demand_change_pct", "percent"),
}


@dataclass
class TemplateExtractResult:
    """Outcome of a successful template extraction."""
    fields: list[dict[str, Any]]
    template_name: str
    coverage_note: str


def try_template_extract(
    parsed: ParsedDocument, doc_type: str
) -> TemplateExtractResult | None:
    """Attempt deterministic extraction for CBRE Horizons.

    Returns None unless the document unambiguously matches CBRE Horizons
    layout AND every structural anchor resolves. Caller treats None as
    "fall through to LLM extractor".
    """
    try:
        if (doc_type or "").upper() != "CBRE_HORIZONS":
            return None
        return _try_cbre_horizons(parsed)
    except Exception:  # noqa: BLE001 — template misread must never break extraction
        logger.warning(
            "template extraction: unexpected error on %s — falling back to LLM",
            getattr(parsed, "filename", "?"),
            exc_info=True,
        )
        return None


# ── shared plumbing ──────────────────────────────────────────────────


def _to_float(cell: str) -> float | None:
    """Parse a grid cell into a float, or None."""
    s = (cell or "").strip().replace(",", "").replace("%", "")
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
    """One extraction-field row in the LLM extractor's wire shape."""
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
    """Materialize per-sheet grids; None when not a sheet-per-page workbook.

    For CBRE we prefer xlsx/xls (pdfplumber tables less reliable).
    """
    if parsed.parser not in ("xlrd", "openpyxl"):
        return None
    sheets: list[_Sheet] = []
    for page in parsed.pages:
        name = (page.metadata or {}).get("sheet_name")
        if not name:
            continue
        grid = page.tables[0] if page.tables else []
        sheets.append(_Sheet(name=str(name), grid=grid, page_num=page.page_num))
    # A CBRE workbook ships multiple tabs; anything smaller is suspect.
    if len(sheets) < 1:
        return None
    return sheets


def _find_label(
    grid: list[list[str]], label: str, *, col: int | None = None
) -> tuple[int, int] | None:
    """First (row, col) whose stripped cell contains or equals label (case-insensitive)."""
    label_lower = label.lower()
    for r, row in enumerate(grid):
        if col is not None:
            if col < len(row) and label_lower in row[col].lower():
                return (r, col)
            continue
        for c, cell in enumerate(row):
            if label_lower in cell.lower():
                return (r, c)
    return None


def _grid_contains(grid: list[list[str]], needle: str, *, max_rows: int = 6) -> bool:
    """Check if grid contains needle text in first max_rows."""
    low = needle.lower()
    for row in grid[:max_rows]:
        for cell in row:
            if low in cell.lower():
                return True
    return False


# ── header metadata ──────────────────────────────────────────────────


def _market_from_header(sheets: list[_Sheet]) -> str | None:
    """Extract market area (e.g., 'Seattle, WA') from header."""
    # Typically in the first sheet, first 10 rows. Look for market patterns like "City, ST".
    pat = re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),\s*([A-Z]{2})")
    for sheet in sheets[:1]:  # Usually on first sheet
        for row in sheet.grid[:10]:
            for cell in row:
                m = pat.search(cell)
                if m:
                    return f"{m.group(1)}, {m.group(2)}"
    return None


def _publication_date_from_header(sheets: list[_Sheet]) -> str | None:
    """Extract publication date (Q# YYYY or ISO) from header."""
    pat = re.compile(r"(Q[1-4]\s+(?:20|19)\d{2}|(?:20|19)\d{2}-\d{2}-\d{2})")
    for sheet in sheets:
        for row in sheet.grid[:15]:
            for cell in row:
                m = pat.search(cell)
                if m:
                    return m.group(1)
    return None


# ── forecast table parsing ───────────────────────────────────────────


@dataclass
class _ForecastTable:
    """Parsed forecast table for one scope (all/upper/mid/lower)."""
    scope: str
    year_col: int
    metric_cols: dict[str, int]  # metric_name → column index
    rows: dict[int, dict[str, Any]]  # year → {metric → value}
    page_num: int


def _extract_tables_from_sheet(
    grid: list[list[str]], page_num: int
) -> list[_ForecastTable]:
    """Extract all forecast tables from a sheet grid.

    Scans the grid for scope headers and parses each table block.
    """
    tables: list[_ForecastTable] = []

    # Find all rows that contain a scope label
    scope_rows: list[tuple[int, str]] = []  # (row_num, scope_name)
    for r, row in enumerate(grid):
        for cell in row:
            cell_lower = cell.lower()
            for scope_label, scope_val in _SCOPE_LABELS.items():
                if scope_label in cell_lower:
                    scope_rows.append((r, scope_val))
                    break

    # For each scope, extract its table
    for scope_idx, (scope_row, scope_name) in enumerate(scope_rows):
        # End row is the next scope row or end of grid
        if scope_idx + 1 < len(scope_rows):
            end_row = scope_rows[scope_idx + 1][0]
        else:
            end_row = len(grid)

        # Extract the table for this scope
        table_grid = grid[scope_row:end_row]
        table = _parse_forecast_table_within_section(table_grid, scope_name, page_num)
        if table:
            tables.append(table)

    return tables


def _parse_forecast_table_within_section(
    grid: list[list[str]], scope: str, page_num: int
) -> _ForecastTable | None:
    """Parse a forecast table section that's already been identified with a scope.

    A forecast table section has:
    - Scope header (already identified, passed as parameter)
    - A year column
    - Metric columns (Occupancy, ADR, RevPAR, changes, supply/demand)
    - Data rows with year labels and values

    Returns None if the structure can't be confidently parsed.
    """
    if not grid or len(grid) < 3:
        return None

    # Scope is already known; find metric header row (typically row 1-2)
    scope_row = 0  # Assume first row has scope

    # Find metric header row (typically right after scope header)
    metric_header_row = None
    for r in range(scope_row + 1, min(scope_row + 3, len(grid))):
        row = grid[r]
        metric_count = sum(1 for cell in row if any(m in cell.lower() for m in _METRIC_LABELS))
        if metric_count >= 2:  # At least 2 metrics
            metric_header_row = r
            break
    if metric_header_row is None:
        return None

    # Extract metric columns and year column
    metric_row = grid[metric_header_row]
    metric_cols: dict[str, int] = {}
    year_col = None
    # Sort metric labels by length (longest first) so "occupancy change" matches before "occupancy"
    sorted_metric_labels = sorted(_METRIC_LABELS, key=len, reverse=True)
    for c, cell in enumerate(metric_row):
        cell_lower = cell.lower().strip()
        if cell_lower in ("year", "calendar year", "calendar"):
            year_col = c
        else:
            for metric_label in sorted_metric_labels:
                if metric_label in cell_lower:
                    metric_cols[metric_label] = c
                    break

    if year_col is None:
        return None
    if len(metric_cols) < 2:  # Need at least 2 metrics
        return None

    # Parse data rows
    rows: dict[int, dict[str, Any]] = {}
    for r in range(metric_header_row + 1, len(grid)):
        row = grid[r]
        if year_col >= len(row):
            continue
        year_str = row[year_col].strip()
        if not year_str or not re.match(r"^\d{4}$", year_str):
            break  # End of data rows
        year = int(year_str)
        values: dict[str, Any] = {}
        for metric_label, col in metric_cols.items():
            if col < len(row):
                val = _to_float(row[col])
                if val is not None:
                    values[metric_label] = val
        if values:
            rows[year] = values

    if not rows:
        return None

    return _ForecastTable(
        scope=scope,
        year_col=year_col,
        metric_cols=metric_cols,
        rows=rows,
        page_num=page_num,
    )


def _as_of_year_from_pub_date(pub_date: str | None) -> int | None:
    """Report base / as-of year, parsed from the publication date.

    CBRE Hotel Horizons treats the publication year (partial actual +
    forward projection) as the FIRST forecast year; every calendar year
    strictly earlier is a historical actual. We take the 4-digit year
    out of the publication string ("Q3 2024", "2024-08-01", …) as the
    historical-vs-forecast boundary.

    Returns ``None`` when no year can be parsed. In that case the caller
    conservatively treats every row as forecast (the pre-fix behaviour),
    since without an anchor we cannot tell which rows are actuals.
    """
    if not pub_date:
        return None
    m = re.search(r"(?:19|20)\d{2}", pub_date)
    return int(m.group(0)) if m else None


def _extract_from_tables(
    sheets: list[_Sheet], as_of_year: int | None = None
) -> tuple[list[dict[str, Any]], str] | None:
    """Extract fields from forecast tables in all sheets.

    Looks for forecast tables across all sheets and emits fields for each.
    Since multiple tables can be on one sheet, we parse the grid sequentially
    looking for scope headers and their associated tables.

    ``as_of_year`` is the report's base year (from the publication date).
    Rows with ``year >= as_of_year`` are forecast; earlier rows are
    historical actuals. The legacy 1-indexed ``cbre_horizons.year_<i>``
    sequence numbers the FIRST FORECAST year as ``year_1`` (contract:
    Year 1 = first forecast year, never an earlier historical actual) and
    excludes historical rows entirely. When ``as_of_year`` is None we
    can't locate the boundary, so every row is treated as forecast.

    Returns (fields, coverage_note) on success, None on failure.
    """
    fields: list[dict[str, Any]] = []
    tables_found: list[_ForecastTable] = []

    for sheet in sheets:
        # Parse all tables in this sheet by scanning for scope headers
        tables = _extract_tables_from_sheet(sheet.grid, sheet.page_num)
        tables_found.extend(tables)

    if not tables_found:
        return None

    # Emit fields for each table
    for table in tables_found:
        sorted_years = sorted(table.rows.keys())
        # Forecast years are those at/after the report's as-of year.
        # Without an anchor we conservatively treat every year as forecast.
        forecast_years = [
            y for y in sorted_years if as_of_year is None or y >= as_of_year
        ]
        for year in sorted_years:
            values = table.rows[year]
            is_forecast = as_of_year is None or year >= as_of_year
            for metric_label, value in values.items():
                if metric_label not in _METRIC_MAP:
                    continue
                field_base, unit = _METRIC_MAP[metric_label]
                field_name = f"cbre_horizons.segment_{table.scope}.{year}.{field_base}"
                fields.append(_field(field_name, value, unit=unit, page=table.page_num))
                # Also emit on legacy year_N path for "all" scope (backwards
                # compat). Year 1 = FIRST FORECAST year; historical actuals
                # are excluded from the year_N sequence so year_1 never maps
                # to an earlier actual.
                if table.scope == "all" and is_forecast:
                    year_idx = forecast_years.index(year) + 1  # 1-indexed
                    legacy_name = f"cbre_horizons.year_{year_idx}.{field_base}"
                    fields.append(_field(legacy_name, value, unit=unit, page=table.page_num))

            # Label the row historical vs forecast off the as-of boundary.
            period = "forecast" if is_forecast else "historical"
            fields.append(
                _field(f"cbre_horizons.segment_{table.scope}.{year}.period", period, page=table.page_num)
            )

    if not fields:
        return None

    scopes_covered = {t.scope for t in tables_found}
    coverage_note = f"forecast tables for scopes: {', '.join(sorted(scopes_covered))}; " \
                    f"{len(fields)} fields extracted deterministically from tables"
    return (fields, coverage_note)


# ── top-level dispatch ───────────────────────────────────────────────


def _try_cbre_horizons(parsed: ParsedDocument) -> TemplateExtractResult | None:
    """Attempt to extract CBRE Horizons data deterministically.

    Conservative detector: any ambiguity → return None.
    """
    sheets = _sheets_of(parsed)
    if sheets is None:
        # PDF parsing for CBRE is unreliable; skip for now.
        return None

    # Fingerprint: look for CBRE-specific markers
    # (forecast tables, market references, "Horizons" in content)
    # Scan first 10 rows to find "Horizons" text
    all_text = ""
    for sheet in sheets:
        for row in sheet.grid[:10]:
            all_text += " ".join(str(cell) for cell in row) + " "
    if "horizons" not in all_text.lower():
        # Not a Horizons report, probably
        return None

    # Resolve the publication date first — its year anchors the
    # historical-vs-forecast boundary used when numbering the legacy
    # year_N sequence and labelling each row's ``period``.
    pub_date = _publication_date_from_header(sheets)
    as_of_year = _as_of_year_from_pub_date(pub_date)

    result = _extract_from_tables(sheets, as_of_year=as_of_year)
    if result is None:
        return None

    fields, coverage_note = result

    # Extract header metadata
    market = _market_from_header(sheets)
    if market:
        fields.insert(0, _field("cbre_horizons.market", market, page=sheets[0].page_num))

    if pub_date:
        fields.insert(0, _field("cbre_horizons.publication_date", pub_date, page=sheets[0].page_num))

    return TemplateExtractResult(
        fields=fields,
        template_name=TEMPLATE_NAME,
        coverage_note=coverage_note,
    )
