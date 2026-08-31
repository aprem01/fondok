"""FON-29 — a calendar/fiscal ANNUAL P&L must classify as PNL, not T12, so it
can't clobber the real trailing-twelve in the Historicals column. Only a
genuine trailing-twelve maps to T12. Locks `_refine_pnl_doc_type` directly.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.api.documents import _refine_pnl_doc_type  # noqa: E402


def _pt(period_type: str) -> list[dict]:
    return [{"field_name": "p_and_l_usali.period_type", "value": period_type}]


def test_annual_pnl_classified_t12_is_refined_to_pnl():
    # The FON-29 clobber: an annual P&L the router landed in T12 must become
    # PNL so it doesn't overwrite the real trailing-twelve column.
    assert _refine_pnl_doc_type("T12", _pt("annual")) == "PNL"
    assert _refine_pnl_doc_type("T12", _pt("fiscal_year")) == "PNL"
    assert _refine_pnl_doc_type("T12", _pt("full_year")) == "PNL"


def test_genuine_trailing_twelve_stays_t12():
    assert _refine_pnl_doc_type("T12", _pt("trailing_twelve")) == "T12"
    assert _refine_pnl_doc_type("PNL", _pt("ttm")) == "T12"


def test_partial_periods_map_to_their_lanes():
    assert _refine_pnl_doc_type("T12", _pt("monthly")) == "PNL_MONTHLY"
    assert _refine_pnl_doc_type("T12", _pt("ytd")) == "PNL_YTD"


def test_metadata_less_pnl_is_left_unchanged():
    # No period_type extracted → no refinement (the frontend FY-filename
    # marker handles the metadata-less annual case separately).
    assert _refine_pnl_doc_type("T12", []) == "T12"


def test_non_pnl_doc_types_pass_through():
    assert _refine_pnl_doc_type("OM", _pt("annual")) == "OM"
    assert _refine_pnl_doc_type("STR_TREND", _pt("trailing_twelve")) == "STR_TREND"
