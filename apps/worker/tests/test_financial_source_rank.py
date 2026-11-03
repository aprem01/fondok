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


def _shim(doc_id: str, doc_type: str, fields: list[dict], **cols) -> dict:
    return {"fields": fields, "doc_type": doc_type, "document_id": doc_id, "filename": doc_id, **cols}


def _record(doc_id: str, doc_type: str, *, idx: int, structural_pnl_score: float, **cols):
    from app.api.documents import DocumentRecord

    return DocumentRecord(
        id=uuid4(),
        deal_id=uuid4(),
        tenant_id=uuid4(),
        filename=doc_id,
        doc_type=doc_type,
        status="EXTRACTED",
        uploaded_at=datetime(2026, 10, 20 - idx, tzinfo=UTC),
        structural_pnl_score=structural_pnl_score,
        **cols,
    )


def _numeric(prefix: str, n: int) -> list[dict]:
    return [{"field_name": f"p_and_l_usali.{prefix}.line_{i:04d}", "value": float(i + 1)} for i in range(n)]


def test_data_room_badge_and_engine_choice_agree_on_the_same_rows() -> None:
    """(7) Both sides computed from the SAME rows, with REAL, non-uniform
    completeness. b67bfdee: the Summary CSV (17 numeric lines, states
    ``annual``, newer upload) and the Detailed P&L (1,031 lines, older) both
    score ``structural_pnl_score`` 1.0 — the measure the old badge ranked on,
    which tied and handed the badge to the newer CSV. The badge must equal
    the engine's per-period choice; it is built from that choice, never from
    a second ranking."""
    from app.api.documents import _mark_statement_roles
    from app.services.engine_runner import (
        _choose_period_statements,
        _pnl_completeness_score,
        _rank_pnl_shims,
        statement_roles_from_shims,
    )

    rooms = {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue"}
    csv_fields = [
        {"field_name": "p_and_l_usali.period_type", "value": "annual"},
        {"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
        {**rooms, "value": 8_900_000.0},
        *_numeric("summary", 16),
    ]
    detailed_fields = [{**rooms, "value": 9_496_407.22}, *_numeric("detail", 1_030)]
    records = [
        _record("csv", "PNL", idx=0, structural_pnl_score=1.0,
                report_as_of=date(2024, 12, 31), extracted_period_year=2024),
        _record("detailed", "PNL", idx=1, structural_pnl_score=1.0, extracted_period_year=2024),
        _record("fy2023", "PNL", idx=2, structural_pnl_score=1.0, extracted_period_year=2023),
    ]
    by_name = {r.filename: r for r in records}
    ids = {name: str(r.id) for name, r in by_name.items()}
    # Newest-first, as both the list endpoint and the loader read them.
    shims = [
        _shim(ids["csv"], "PNL", csv_fields, report_as_of="2024-12-31", extracted_period_year=2024),
        _shim(ids["detailed"], "PNL", detailed_fields, extracted_period_year=2024),
        _shim(ids["fy2023"], "PNL", [{**rooms, "value": 8_000_000.0}], extracted_period_year=2023),
    ]
    assert _pnl_completeness_score(csv_fields) == 17
    assert _pnl_completeness_score(detailed_fields) == 1_031
    _mark_statement_roles(records, statement_roles_from_shims(shims))

    engine_primary = _choose_period_statements(_rank_pnl_shims(shims))[0][0].shim["document_id"]
    assert engine_primary == ids["detailed"]
    assert [str(r.id) for r in records if r.primary_financial_source] == [engine_primary]
    assert by_name["csv"].statement_role == "supplemental"
    assert by_name["fy2023"].statement_role == "period_primary"

    # The legacy Data Room key (``structural_pnl_score`` as completeness) on
    # the same records ranks the CSV first — the divergence this pins.
    legacy = min(
        enumerate(records),
        key=lambda t: financial_source_sort_key(
            doc_type=t[1].doc_type, report_as_of=t[1].report_as_of,
            extracted_period_year=t[1].extracted_period_year,
            completeness=t[1].structural_pnl_score or 0.0, upload_order=t[0],
        ),
    )[1]
    assert legacy.filename == "csv"

    # Tester 2's variant still holds on the key itself: an undated T-12 is the
    # current period on both sides.
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
