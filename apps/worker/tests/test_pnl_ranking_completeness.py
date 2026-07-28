"""FON-22 — when multiple P&Ls of the same period are uploaded, the more
DETAILED one must drive the engines (Detailed > Summary), while period_type
still dominates (an annual Summary still beats a monthly Detailed).

Locks the ranking contract in `_rank_pnl_rows` / `_pnl_completeness_score`,
independent of the DB/extraction plumbing.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.services.engine_runner import (  # noqa: E402
    _pnl_completeness_score,
    _rank_pnl_rows,
)


def _field(name: str, value):
    return {"field_name": name, "value": value}


def _summary_pnl(period_type: str = "annual") -> list[dict]:
    """A handful of aggregated totals — the low-detail shape."""
    return [
        _field("p_and_l_usali.period_type", period_type),
        _field("p_and_l_usali.property_name", "The Anglers"),  # text, not counted
        _field("p_and_l_usali.total_revenue", "12,400,000"),
        _field("p_and_l_usali.gross_operating_profit", "4,100,000"),
        _field("p_and_l_usali.noi", "3,200,000"),
    ]


def _detailed_pnl(period_type: str = "annual") -> list[dict]:
    """Broken-out USALI line items — the high-detail shape."""
    return [
        _field("p_and_l_usali.period_type", period_type),
        _field("p_and_l_usali.property_name", "The Anglers"),  # text, not counted
        _field("p_and_l_usali.rooms_revenue", "9,100,000"),
        _field("p_and_l_usali.fb_revenue", "2,300,000"),
        _field("p_and_l_usali.other_revenue", "1,000,000"),
        _field("p_and_l_usali.total_revenue", "12,400,000"),
        _field("p_and_l_usali.rooms_expense", "2,050,000"),
        _field("p_and_l_usali.fb_expense", "1,700,000"),
        _field("p_and_l_usali.admin_general", "620,000"),
        _field("p_and_l_usali.sales_marketing", "540,000"),
        _field("p_and_l_usali.utilities", "410,000"),
        _field("p_and_l_usali.repairs_maintenance", "380,000"),
        _field("p_and_l_usali.property_taxes", "300,000"),
        _field("p_and_l_usali.insurance", "180,000"),
        _field("p_and_l_usali.noi", "3,200,000"),
    ]


def test_completeness_score_detailed_beats_summary() -> None:
    assert _pnl_completeness_score(_detailed_pnl()) > _pnl_completeness_score(
        _summary_pnl()
    )


def test_completeness_excludes_text_and_period_type() -> None:
    # property_name (text) and period_type must not inflate the score.
    score = _pnl_completeness_score(
        [
            _field("p_and_l_usali.period_type", "annual"),
            _field("p_and_l_usali.property_name", "The Anglers"),
            _field("p_and_l_usali.noi", "3,200,000"),
        ]
    )
    assert score == 1  # only NOI is a numeric line item


def test_detailed_pnl_wins_within_same_period() -> None:
    """Summary uploaded LATER (idx 0) must still lose to the Detailed doc
    of the same period — completeness outranks recency."""
    rows = [
        {"fields": _summary_pnl("annual"), "doc_type": "PNL"},   # newer
        {"fields": _detailed_pnl("annual"), "doc_type": "PNL"},  # older
    ]
    ranked = _rank_pnl_rows(rows)
    primary_fields, _ = ranked[0]
    # The winner is the detailed doc → it carries the broken-out lines.
    names = {f["field_name"] for f in primary_fields}
    assert "p_and_l_usali.sales_marketing" in names
    assert "p_and_l_usali.utilities" in names


def _dated(period_type: str, period_ending: str, adr: float) -> list[dict]:
    return [
        _field("p_and_l_usali.period_type", period_type),
        _field("p_and_l_usali.period_ending", period_ending),
        _field("p_and_l_usali.operational_kpis.occupancy_pct", 0.80),
        _field("p_and_l_usali.operational_kpis.adr_usd", adr),
        _field("p_and_l_usali.operating_revenue.rooms_revenue_usd", "9,000,000"),
    ]


def test_current_ttm_beats_stale_annual() -> None:
    """Harbor Palms retest: a current TTM (period_ending 2025-06-30) must
    drive the base year over a 2-year-old calendar annual (2023-12-31) —
    both are full-year, so the most RECENT period wins, not the label."""
    rows = [
        {"fields": _dated("annual", "2023-12-31", 265.0), "doc_type": "T12"},
        {"fields": _dated("trailing_twelve", "2025-06-30", 283.96), "doc_type": "T12"},
    ]
    ranked = _rank_pnl_rows(rows)
    primary_fields, _ = ranked[0]
    adr = next(
        f["value"] for f in primary_fields if f["field_name"].endswith("adr_usd")
    )
    assert adr == 283.96, "the current TTM must win over the stale annual"


def test_full_year_still_beats_partial_period() -> None:
    """A recent MONTHLY must NOT beat an older full-year annual (Eshan's QA:
    a May-YTD/monthly was clobbering the annual T-12 base)."""
    rows = [
        {"fields": _dated("monthly", "2025-05-31", 300.0), "doc_type": "PNL_MONTHLY"},
        {"fields": _dated("annual", "2024-12-31", 265.0), "doc_type": "T12"},
    ]
    ranked = _rank_pnl_rows(rows)
    primary_fields, _ = ranked[0]
    adr = next(
        f["value"] for f in primary_fields if f["field_name"].endswith("adr_usd")
    )
    assert adr == 265.0, "full-year annual must beat a recent monthly"


def test_period_type_still_dominates_completeness() -> None:
    """An annual SUMMARY must still beat a monthly DETAILED — a richer
    monthly must not clobber the true annual baseline (Eshan's QA)."""
    rows = [
        {"fields": _detailed_pnl("monthly"), "doc_type": "PNL_MONTHLY"},
        {"fields": _summary_pnl("annual"), "doc_type": "T12"},
    ]
    ranked = _rank_pnl_rows(rows)
    primary_fields, doc_type = ranked[0]
    pt = next(
        f["value"]
        for f in primary_fields
        if f["field_name"].endswith("period_type")
    )
    assert pt == "annual", "annual must win over monthly regardless of detail"
