"""Deterministic extractor for the Fondok Partnership / JV upload template.

FON-66 Slice B. A partnership/JV agreement's economics arrive either as prose
(a real operating agreement — that's the LLM path) or as a STRUCTURED upload
template: a workbook with the ownership split, preferred return, and a promote
waterfall laid out in a labeled grid. This extractor handles the structured
case at $0, with zero hallucination risk, emitting the SAME canonical fields
the Partnership engine already consumes (``partnership.gp_equity_pct``,
``partnership.lp_equity_pct``, ``partnership.pref_rate`` and per-tier
``partnership.waterfall.<idx>.{hurdle_rate,gp_split,lp_split}``).

Layout it recognizes (labels matched case-insensitively; positions are found by
anchor, not fixed coordinates):

    Sponsor / GP Ownership     10%
    LP Investor Ownership      90%
    Preferred Return           10%

    Tier            IRR Hurdle   GP Split   LP Split
    Preferred       10%          0%         100%
    Tier 2          15%          20%        80%
    ...

Detection biases HARD toward ``None`` (fall through to the LLM extractor): the
promote table must resolve with at least two tiers whose splits are coherent,
or nothing is emitted.
"""

from __future__ import annotations

import logging

from ..models import ParsedDocument
from ..numeric import coerce_cell_number
from ._common import TemplateExtractResult, _Sheet, _field, sheets_of

logger = logging.getLogger(__name__)

TEMPLATE_NAME = "partnership"


def try_template_extract(
    parsed: ParsedDocument, doc_type: str
) -> TemplateExtractResult | None:
    """Attempt deterministic extraction for a Partnership/JV upload template.

    Returns None unless the workbook unambiguously carries a promote-waterfall
    table; the caller then falls through to the LLM extractor.
    """
    try:
        if (doc_type or "").upper() != "PARTNERSHIP":
            return None
        return _extract(parsed)
    except Exception:  # noqa: BLE001 — a template misread must never break extraction
        logger.warning(
            "template extraction: unexpected error on %s — falling back to LLM",
            getattr(parsed, "filename", "?"),
            exc_info=True,
        )
        return None


def _pct(cell: str) -> float | None:
    """Normalize a percentage/ratio cell to a 0..1 fraction.

    ``coerce_cell_number`` returns the bare number (``10%`` → ``10``), so any
    value above 1 is read as a percentage and divided by 100; values at/below 1
    are already fractions.
    """
    v = coerce_cell_number(cell)
    if v is None:
        return None
    return v / 100.0 if v > 1.0 else float(v)


def _row_value(sheet: _Sheet, r: int, label_c: int) -> float | None:
    """First numeric cell to the right of a label in row ``r``."""
    row = sheet.grid[r] if r < len(sheet.grid) else []
    for c in range(label_c + 1, len(row)):
        v = _pct(row[c])
        if v is not None:
            return v
    return None


def _find_ownership(sheet: _Sheet, *needles: str) -> float | None:
    """Find a labeled ownership/return value anywhere in the sheet.

    Matches a row whose cell contains ALL of ``needles`` (case-insensitive) and
    returns the first numeric value to its right.
    """
    for r, row in enumerate(sheet.grid):
        for c, cell in enumerate(row):
            low = cell.lower()
            if all(n in low for n in needles):
                v = _row_value(sheet, r, c)
                if v is not None:
                    return v
    return None


def _find_waterfall_header(sheet: _Sheet) -> tuple[int, int, int, int] | None:
    """Locate the promote-table header row.

    Returns ``(row, hurdle_col, gp_col, lp_col)`` for a row that carries a
    hurdle/IRR column plus distinct GP and LP split columns; ``None`` otherwise.
    """
    for r, row in enumerate(sheet.grid):
        hurdle_c = gp_c = lp_c = None
        for c, cell in enumerate(row):
            low = cell.lower()
            if hurdle_c is None and ("hurdle" in low or "irr" in low):
                hurdle_c = c
            elif gp_c is None and "gp" in low:
                gp_c = c
            elif lp_c is None and "lp" in low:
                lp_c = c
        if hurdle_c is not None and gp_c is not None and lp_c is not None:
            return (r, hurdle_c, gp_c, lp_c)
    return None


def _extract(parsed: ParsedDocument) -> TemplateExtractResult | None:
    sheets = sheets_of(parsed, min_sheets=1)
    if not sheets:
        return None

    # Use the first sheet whose grid carries a promote-waterfall header.
    target: _Sheet | None = None
    header: tuple[int, int, int, int] | None = None
    for sheet in sheets:
        h = _find_waterfall_header(sheet)
        if h is not None:
            target, header = sheet, h
            break
    if target is None or header is None:
        return None

    header_row, hurdle_c, gp_c, lp_c = header
    # The tier label is the first column that isn't one of the numeric columns.
    label_c = next(
        (c for c in range(len(target.grid[header_row])) if c not in (hurdle_c, gp_c, lp_c)),
        0,
    )

    tiers: list[tuple[str, float, float, float]] = []
    for r in range(header_row + 1, len(target.grid)):
        row = target.grid[r]
        hurdle = _pct(row[hurdle_c]) if hurdle_c < len(row) else None
        gp = _pct(row[gp_c]) if gp_c < len(row) else None
        lp = _pct(row[lp_c]) if lp_c < len(row) else None
        if hurdle is None or gp is None:
            # End of the contiguous tier block.
            if tiers:
                break
            continue
        if lp is None:
            lp = round(1.0 - gp, 6)
        # Coherence: splits must sum to ~1.0, hurdle within 0..1.
        if not (0.0 <= hurdle <= 1.0) or abs((gp + lp) - 1.0) > 0.02:
            if tiers:
                break
            continue
        label = str(row[label_c]).strip() if label_c < len(row) else f"Tier {len(tiers) + 1}"
        tiers.append((label or f"Tier {len(tiers) + 1}", hurdle, gp, round(1.0 - gp, 6)))

    if len(tiers) < 2:
        return None  # not a real waterfall — fall through to the LLM path.

    fields: list[dict[str, object]] = []
    for idx, (label, hurdle, gp, lp) in enumerate(tiers):
        fields.append(_field(f"partnership.waterfall.{idx}.hurdle_rate", hurdle, unit="ratio"))
        fields.append(_field(f"partnership.waterfall.{idx}.gp_split", gp, unit="ratio"))
        fields.append(_field(f"partnership.waterfall.{idx}.lp_split", lp, unit="ratio"))

    # Ownership + preferred return (optional — emitted only when present).
    gp_own = _find_ownership(target, "gp") or _find_ownership(target, "sponsor")
    lp_own = _find_ownership(target, "lp", "investor") or _find_ownership(target, "lp", "ownership")
    pref = _find_ownership(target, "preferred") or _find_ownership(target, "pref")
    if gp_own is not None and 0.0 < gp_own < 1.0:
        fields.append(_field("partnership.gp_equity_pct", gp_own, unit="ratio"))
        if lp_own is None:
            lp_own = round(1.0 - gp_own, 6)
    if lp_own is not None and 0.0 < lp_own <= 1.0:
        fields.append(_field("partnership.lp_equity_pct", lp_own, unit="ratio"))
    if pref is not None and 0.0 <= pref <= 0.30:
        fields.append(_field("partnership.pref_rate", pref, unit="ratio"))

    return TemplateExtractResult(
        fields=fields,
        template_name=TEMPLATE_NAME,
        coverage_note=f"{len(tiers)} promote tiers + ownership/pref from the partnership template.",
    )
