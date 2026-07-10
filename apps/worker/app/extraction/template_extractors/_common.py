"""Shared plumbing for the deterministic template extractors.

``str_trend`` and ``cbre_horizons`` are two implementations of the same
contract (see ``__init__.py``), so the result envelope, the field/int
helpers and the sheet-materialization iterator live here ONCE instead of
being copy-pasted into each extractor (where they had already started to
drift). Both extractors import from this module, so an ``isinstance``
check against the package-exported :class:`TemplateExtractResult`
matches results from either extractor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import ParsedDocument
from ..numeric import coerce_cell_number


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


def _to_int(cell: str) -> int | None:
    """Parse a grid cell into an int, or ``None`` when it isn't a whole
    number."""
    v = coerce_cell_number(cell)
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


def sheets_of(
    parsed: ParsedDocument, *, min_sheets: int
) -> list[_Sheet] | None:
    """Materialize per-sheet grids for a workbook parse, or ``None``.

    Returns ``None`` when this isn't a sheet-per-page workbook parse
    (pdf, docx, …) or when fewer than ``min_sheets`` usable sheets
    resolve.

    Only VISIBLE sheets are yielded — the same filter the sibling
    learner (``services/sibling_template._iter_visible_sheets``) uses,
    so the "same workbook" view is consistent across both paths and the
    ``veryHidden`` GUID-named macro sheets some exports carry don't leak
    in. Sheets whose ``sheet_state`` metadata is absent (older parses,
    the xlrd ``.xls`` path) are treated as visible so we never silently
    drop a sheet that simply has no state marker.
    """
    if parsed.parser not in ("xlrd", "openpyxl"):
        return None
    sheets: list[_Sheet] = []
    for page in parsed.pages:
        meta = page.metadata or {}
        if meta.get("sheet_state", "visible") != "visible":
            continue
        name = meta.get("sheet_name")
        if not name:
            continue
        grid = page.tables[0] if page.tables else []
        sheets.append(_Sheet(name=str(name), grid=grid, page_num=page.page_num))
    if len(sheets) < min_sheets:
        return None
    return sheets
