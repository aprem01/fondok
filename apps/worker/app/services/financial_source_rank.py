"""One ranking key for "which financial statement is the primary source".

Two places decide which of a deal's financial statements (T-12 / annual
P&L / YTD / monthly) is the base year the model is grounded on:

* the engine loaders in ``services.engine_runner`` (``_rank_pnl_shims`` →
  ``_load_t12_revenue_actuals`` / ``_load_t12_expense_actuals``), which drive
  Year-1 revenue and expense actuals, and
* the Data Room's "Primary source" badge
  (``api.documents._mark_primary_financial``).

Until 2026-10 they used two DIFFERENT keys. The engines ranked purely on the
extracted ``*.period_type`` / ``*.period_ending`` fields, so a T-12 whose
extraction emitted neither (the sibling-template path does not) fell to the
"unknown period" tier — BELOW a monthly statement — and recency 0, while the
Data Room ranked on ``doc_type`` + ``fiscal_year``. On two external testers'
deals the Data Room badged the T-12 as primary while the engines grounded
Year-1 on a 2024 detailed P&L whose extractor had mapped one restaurant
concession to the F&B totals: total revenue $9.6M instead of $13.8M, levered
IRR -100%.

:func:`financial_source_sort_key` is the single, pure key both callers now
sort by (ascending — lower tuple = preferred). Its members, in order:

1. **tier** — full-year sources first. From the extracted ``period_type``
   when it is a recognised label (``annual`` / ``ttm`` / … → 0; ``ytd`` 5;
   ``quarterly`` 7; ``monthly`` 9, per ``field_catalog.PERIOD_TYPE_RANK``);
   otherwise from ``doc_type`` — ``T12`` and ``PNL`` are full-year (0),
   ``PNL_YTD`` / ``PNL_MONTHLY`` take the partial rank of the period they
   name, anything else is 50. A T-12 is by definition trailing-twelve; it
   never falls below a monthly statement because its extraction omitted a
   label.
2. **-recency** — the most recent period wins. ``YYYYMMDD`` from the first
   usable of: extracted ``period_ending`` → ``documents.report_as_of`` →
   ``extracted_period_year`` → ``fiscal_year`` (year-only values compare as
   ``YYYY-12-31``). An undated **T12** is the CURRENT period
   (:data:`CURRENT_PERIOD_SORTKEY`) — an analyst uploads a T-12 as the
   latest statement — while an undated PNL keeps recency 0 (oldest).
3. **doc-type preference** — ``T12`` (0) before ``PNL`` (1) before anything
   else (2) on an equal period: a trailing-twelve is the more current base
   than a calendar year ending in the same year.
4. **-completeness** — the caller's detail score (the engines'
   ``_pnl_completeness_score``, the Data Room's ``structural_pnl_score``);
   a Detailed P&L beats a Summary of the same period (FON-22).
5. **upload_order** — the caller's newest-first index; the newest upload
   is the final tiebreaker.

Pure and import-light on purpose: it must be usable from the API layer and
the engine layer without either importing the other.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ..extraction.field_catalog import PERIOD_TYPE_RANK

__all__ = [
    "CURRENT_PERIOD_SORTKEY",
    "FULL_YEAR_DOC_TYPES",
    "FULL_YEAR_PERIOD_TYPES",
    "UNKNOWN_PERIOD_RANK",
    "date_sortkey",
    "doc_type_preference",
    "financial_source_sort_key",
    "period_recency",
    "period_tier",
    "year_sortkey",
]

#: Full-year P&L period types. A trailing-twelve (TTM) and a calendar annual
#: are BOTH complete operating years — neither is a partial period — so they
#: share the top tier and are separated by RECENCY of period, not by label.
FULL_YEAR_PERIOD_TYPES: frozenset[str] = frozenset(
    {
        "annual",
        "fiscal_year",
        "full_year",
        "trailing_twelve",
        "ttm",
        "t12",
        "rolling_twelve",
    }
)

#: doc_types that are full-year statements by definition (used when the
#: extraction carries no recognised ``period_type`` label).
FULL_YEAR_DOC_TYPES: frozenset[str] = frozenset({"T12", "PNL"})

#: Partial-period doc_types → the ``PERIOD_TYPE_RANK`` label they stand for.
_PARTIAL_DOC_TYPE_PERIOD: dict[str, str] = {
    "PNL_YTD": "ytd",
    "PNL_MONTHLY": "monthly",
}

#: Tier for a source whose period cannot be classified at all.
UNKNOWN_PERIOD_RANK = 50

#: Recency of an undated T-12 — sorts as the most recent period possible.
CURRENT_PERIOD_SORTKEY = 9999_12_31

#: Tie-break between document kinds on an equal period (lower = preferred).
_DOC_TYPE_PREFERENCE: dict[str, int] = {"T12": 0, "PNL": 1}
_OTHER_DOC_TYPE_PREFERENCE = 2

#: Non-ISO ``period_ending`` shapes the Extractor has been seen to emit.
_DATE_FORMATS: tuple[str, ...] = ("%m/%d/%Y", "%Y/%m/%d", "%m-%d-%Y")


def _norm_doc_type(doc_type: Any) -> str:
    return str(doc_type or "").strip().upper()


def _norm_period_type(period_type: Any) -> str:
    return str(period_type or "").strip().lower()


def period_tier(period_type: Any, doc_type: Any) -> int:
    """Member 1 of the key — see the module docstring.

    A recognised ``period_type`` label wins; an absent OR unrecognised label
    falls back to what the ``doc_type`` says the document is.
    """
    pt = _norm_period_type(period_type)
    if pt:
        if pt in FULL_YEAR_PERIOD_TYPES:
            return 0
        rank = PERIOD_TYPE_RANK.get(pt)
        if rank is not None:
            return rank
    dt = _norm_doc_type(doc_type)
    if dt in FULL_YEAR_DOC_TYPES:
        return 0
    partial = _PARTIAL_DOC_TYPE_PERIOD.get(dt)
    if partial is not None:
        return PERIOD_TYPE_RANK.get(partial, UNKNOWN_PERIOD_RANK)
    return UNKNOWN_PERIOD_RANK


def date_sortkey(value: Any) -> int:
    """``YYYYMMDD`` as an int for a date / datetime / date-ish string; ``0``
    when absent or unparseable (so a dated period always beats an undated
    one and the caller can fall through to the next source)."""
    if value is None:
        return 0
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.year * 10_000 + value.month * 100 + value.day
    if not isinstance(value, str):
        return 0
    s = value.strip()
    if not s:
        return 0
    parsed: date | None = None
    try:
        parsed = date.fromisoformat(s[:10])
    except ValueError:
        for fmt in _DATE_FORMATS:
            try:
                parsed = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
    if parsed is not None:
        return parsed.year * 10_000 + parsed.month * 100 + parsed.day
    # Last resort — the pre-2026-10 engine behaviour: the first eight digits.
    digits = "".join(ch for ch in s if ch.isdigit())[:8]
    return int(digits) if len(digits) == 8 else 0


def year_sortkey(value: Any) -> int:
    """A bare year (``2025`` / ``2025.0`` / ``"2025"``) → ``20251231``; ``0``
    for anything missing, non-integral, or outside 1900..2100."""
    if value is None or isinstance(value, bool):
        return 0
    try:
        if isinstance(value, float) and not value.is_integer():
            return 0
        year = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return 0
    if 1900 <= year <= 2100:
        return year * 10_000 + 12_31
    return 0


def period_recency(
    *,
    doc_type: Any,
    period_ending: Any = None,
    report_as_of: Any = None,
    extracted_period_year: Any = None,
    fiscal_year: Any = None,
) -> int:
    """Member 2 of the key (before negation) — see the module docstring."""
    for candidate in (
        date_sortkey(period_ending),
        date_sortkey(report_as_of),
        year_sortkey(extracted_period_year),
        year_sortkey(fiscal_year),
    ):
        if candidate:
            return candidate
    if _norm_doc_type(doc_type) == "T12":
        return CURRENT_PERIOD_SORTKEY
    return 0


def doc_type_preference(doc_type: Any) -> int:
    """Member 3 of the key — ``T12`` 0, ``PNL`` 1, anything else 2."""
    return _DOC_TYPE_PREFERENCE.get(_norm_doc_type(doc_type), _OTHER_DOC_TYPE_PREFERENCE)


def financial_source_sort_key(
    *,
    doc_type: Any,
    period_type: Any = None,
    period_ending: Any = None,
    report_as_of: Any = None,
    extracted_period_year: Any = None,
    fiscal_year: Any = None,
    completeness: float = 0.0,
    upload_order: int = 0,
) -> tuple[int, int, int, float, int]:
    """The shared primary-financial-source sort key (ascending = preferred).

    ``(tier, -recency, doc_type_preference, -completeness, upload_order)``

    ``doc_type`` is the ``documents.doc_type`` (``T12`` / ``PNL`` /
    ``PNL_YTD`` / ``PNL_MONTHLY``). ``period_type`` / ``period_ending`` are
    the extracted fields (pass ``None`` when the caller has no extraction in
    hand — the Data Room). ``report_as_of`` / ``extracted_period_year`` /
    ``fiscal_year`` are the ``documents`` columns of the same names.
    ``completeness`` is the caller's detail score (higher = more detailed);
    ``upload_order`` is the caller's newest-first index (0 = newest).
    """
    return (
        period_tier(period_type, doc_type),
        -period_recency(
            doc_type=doc_type,
            period_ending=period_ending,
            report_as_of=report_as_of,
            extracted_period_year=extracted_period_year,
            fiscal_year=fiscal_year,
        ),
        doc_type_preference(doc_type),
        -float(completeness or 0.0),
        int(upload_order),
    )
