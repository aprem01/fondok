"""The ONE primary-financial-source ranking key
(``services.financial_source_rank.financial_source_sort_key``).

Live bug (2026-10, two external testers' deals): the engine loaders ranked
P&L extractions purely on the extracted ``period_type`` / ``period_ending``
fields, so a T-12 whose extraction emitted neither ("The Angler's - March
2025 Financials.xlsx", sibling-template path) fell to the unknown tier —
below a monthly — and a 2024 detailed P&L (period_ending 2024-12-31) whose
extractor had mapped one restaurant concession to the F&B totals ($96,528
vs the T-12's $3,216,620) drove Year-1: total revenue $9.6M instead of
$13.8M, levered IRR -100%. Meanwhile the Data Room's "Primary source" badge
ranked on doc_type + fiscal_year and sat on the T-12. Both now sort by this
key, and these tests pin its order of preference.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.services.financial_source_rank import (
    CURRENT_PERIOD_SORTKEY,
    date_sortkey,
    financial_source_sort_key,
    period_recency,
    period_tier,
    year_sortkey,
)


def _key(doc_type: str, **kw):
    return financial_source_sort_key(doc_type=doc_type, **kw)


def _ranked(*named: tuple[str, tuple]) -> list[str]:
    """``[(label, key), ...]`` → labels best-first."""
    return [label for label, _ in sorted(named, key=lambda t: t[1])]


# ─────────────────────────── the seven required pins ───────────────────────


def test_undated_t12_beats_dated_annual_pnl() -> None:
    """(1) A T-12 with NO period fields and NO document dates is the current
    period by definition — it must outrank a 2024-12-31 P&L."""
    order = _ranked(
        ("pnl_2024", _key("PNL", period_type="annual", period_ending="2024-12-31")),
        ("t12", _key("T12")),
    )
    assert order[0] == "t12"


def test_t12_dated_only_by_fiscal_year_beats_older_dated_pnl() -> None:
    """(2) Tester 1's deal: the T-12 extraction has no period fields but the
    analyst pinned fiscal_year 2025 → it is the 2025 full-year source and
    beats the 2024 P&L on recency (2025-12-31 > 2024-12-31)."""
    order = _ranked(
        ("pnl_2024", _key("PNL", period_type="annual", period_ending="2024-12-31")),
        ("t12_fy2025", _key("T12", fiscal_year=2025)),
    )
    assert order[0] == "t12_fy2025"


def test_older_dated_t12_loses_to_newer_pnl_on_recency() -> None:
    """(3) Recency still rules within full-year: a T-12 ending 2023-06-30 is
    STALE next to a 2024-12-31 annual, so the P&L wins."""
    order = _ranked(
        ("t12_2023", _key("T12", period_type="ttm", period_ending="2023-06-30")),
        ("pnl_2024", _key("PNL", period_type="annual", period_ending="2024-12-31")),
    )
    assert order[0] == "pnl_2024"


def test_monthly_never_beats_a_t12_or_pnl() -> None:
    """(4) A PNL_MONTHLY — labelled or not, however recent — never outranks
    a full-year source, including an UNLABELLED T-12 / PNL."""
    monthly_labelled = _key(
        "PNL_MONTHLY", period_type="monthly", period_ending="2025-08-31", completeness=40
    )
    monthly_unlabelled = _key("PNL_MONTHLY", period_ending="2025-08-31", completeness=40)
    for monthly in (monthly_labelled, monthly_unlabelled):
        assert _key("T12") < monthly
        assert _key("T12", fiscal_year=2019) < monthly
        assert _key("PNL") < monthly  # undated PNL: recency 0 but still tier 0
        assert _key("PNL", period_ending="2019-12-31") < monthly
        assert _key("T12", period_type="ttm", period_ending="2020-06-30") < monthly
    # The same holds for a YTD statement.
    assert _key("T12") < _key("PNL_YTD", period_ending="2025-09-30", completeness=40)


def test_t12_beats_pnl_on_an_equal_period() -> None:
    """(5) Same period end → the trailing-twelve is the more current base."""
    assert _key("T12", period_ending="2024-12-31") < _key("PNL", period_ending="2024-12-31")
    assert _key("T12", fiscal_year=2024) < _key("PNL", extracted_period_year=2024)
    # … even when the PNL is richer and newer; doc-type preference sits above
    # completeness and upload order.
    assert _key("T12", period_ending="2024-12-31", completeness=5, upload_order=3) < _key(
        "PNL", period_ending="2024-12-31", completeness=40, upload_order=0
    )


def test_two_pnls_same_period_more_complete_first() -> None:
    """(6) FON-22 — a Detailed P&L beats a Summary of the same period, even
    when the Summary was uploaded later."""
    order = _ranked(
        ("summary", _key("PNL", period_ending="2024-12-31", completeness=5, upload_order=0)),
        ("detailed", _key("PNL", period_ending="2024-12-31", completeness=15, upload_order=1)),
    )
    assert order == ["detailed", "summary"]
    # Equal completeness → the newer upload (lower index) wins.
    assert _key("PNL", period_ending="2024-12-31", completeness=5, upload_order=0) < _key(
        "PNL", period_ending="2024-12-31", completeness=5, upload_order=1
    )


def test_data_room_key_and_engine_key_agree_on_the_live_deal() -> None:
    """(7) The fixture mirrors tester 1's deal: T-12 (fiscal_year 2025, no
    extracted period fields), PNL 2024 (period_ending 2024-12-31), PNL 2023,
    PNL 2019. The engine ranks with the extracted fields; the Data Room has
    only the ``documents`` columns (``extracted_period_year`` derived from
    the same period_ending). Both must put the T-12 first and agree on the
    whole order."""
    docs = [
        # (label, doc_type, period_type, period_ending, extracted_period_year, fiscal_year)
        ("t12", "T12", None, None, None, 2025),
        ("pnl_2024", "PNL", "annual", "2024-12-31", 2024, None),
        ("pnl_2023", "PNL", "annual", "2023-12-31", 2023, None),
        ("pnl_2019", "PNL", "annual", "2019-12-31", 2019, None),
    ]
    engine = _ranked(
        *(
            (
                label,
                financial_source_sort_key(
                    doc_type=dt,
                    period_type=pt,
                    period_ending=pe,
                    fiscal_year=fy,
                    completeness=30,
                    upload_order=idx,
                ),
            )
            for idx, (label, dt, pt, pe, _epy, fy) in enumerate(docs)
        )
    )
    data_room = _ranked(
        *(
            (
                label,
                financial_source_sort_key(
                    doc_type=dt,
                    period_type=None,
                    period_ending=None,
                    extracted_period_year=epy,
                    fiscal_year=fy,
                    completeness=0.8,
                    upload_order=idx,
                ),
            )
            for idx, (label, dt, _pt, _pe, epy, fy) in enumerate(docs)
        )
    )
    assert engine[0] == "t12"
    assert engine == data_room == ["t12", "pnl_2024", "pnl_2023", "pnl_2019"]

    # Tester 2's variant: the T-12 row has fiscal_year NULL and
    # extracted_period_year NULL — still the current period on both sides.
    assert financial_source_sort_key(doc_type="T12") < financial_source_sort_key(
        doc_type="PNL", period_type="annual", period_ending="2024-12-31"
    )
    assert financial_source_sort_key(doc_type="T12") < financial_source_sort_key(
        doc_type="PNL", extracted_period_year=2024
    )


# ─────────────────────────── member-level behaviour ─────────────────────────


def test_tier_from_label_else_doc_type() -> None:
    assert period_tier("annual", "PNL_MONTHLY") == 0  # a recognised label wins
    assert period_tier("ttm", "PNL") == 0
    assert period_tier("monthly", "T12") == 9
    assert period_tier(None, "T12") == 0
    assert period_tier("", "PNL") == 0
    assert period_tier(None, "PNL_YTD") == 5
    assert period_tier(None, "PNL_MONTHLY") == 9
    assert period_tier("not-a-real-label", "T12") == 0  # unrecognised → doc_type
    assert period_tier("not-a-real-label", "OM") == 50
    assert period_tier(None, None) == 50


def test_recency_fall_through_and_undated_t12_sentinel() -> None:
    kw = {"report_as_of": date(2025, 3, 31), "extracted_period_year": 2024, "fiscal_year": 2023}
    assert period_recency(doc_type="PNL", period_ending="2025-06-30", **kw) == 2025_06_30
    assert period_recency(doc_type="PNL", **kw) == 2025_03_31
    assert period_recency(doc_type="PNL", extracted_period_year=2024, fiscal_year=2023) == 2024_12_31
    assert period_recency(doc_type="PNL", fiscal_year=2023) == 2023_12_31
    # An unparseable period_ending falls through rather than blocking.
    assert period_recency(doc_type="PNL", period_ending="FY two-thousand", fiscal_year=2023) == (
        2023_12_31
    )
    assert period_recency(doc_type="PNL") == 0
    assert period_recency(doc_type="T12") == CURRENT_PERIOD_SORTKEY
    assert period_recency(doc_type="t12") == CURRENT_PERIOD_SORTKEY
    assert period_recency(doc_type="PNL_MONTHLY") == 0


def test_date_and_year_sortkeys_accept_the_shapes_the_db_and_extractor_emit() -> None:
    assert date_sortkey("2025-06-30") == 2025_06_30
    assert date_sortkey("2025-06-30T00:00:00") == 2025_06_30
    assert date_sortkey(date(2025, 6, 30)) == 2025_06_30  # Postgres DATE
    assert date_sortkey(datetime(2025, 6, 30, 12, tzinfo=UTC)) == 2025_06_30
    assert date_sortkey("06/30/2025") == 2025_06_30
    assert date_sortkey("") == 0
    assert date_sortkey(None) == 0
    assert date_sortkey(uuid4()) == 0
    assert year_sortkey(2025) == 2025_12_31
    assert year_sortkey("2025") == 2025_12_31
    assert year_sortkey(2025.0) == 2025_12_31
    assert year_sortkey(2025.5) == 0
    assert year_sortkey(True) == 0
    assert year_sortkey(1800) == 0
    assert year_sortkey(None) == 0


def test_key_shape_and_order_of_members() -> None:
    key = financial_source_sort_key(
        doc_type="PNL",
        period_type="annual",
        period_ending="2024-12-31",
        completeness=12,
        upload_order=2,
    )
    assert key == (0, -2024_12_31, 1, -12.0, 2)
    assert financial_source_sort_key(doc_type="T12") == (0, -CURRENT_PERIOD_SORTKEY, 0, 0.0, 0)
