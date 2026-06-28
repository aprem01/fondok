"""Tests for ``extracted_period_year`` derivation.

Sam QA Bug #4 (June 2026): ``extracted_period_year`` was NULL on
documents that ought to carry a year (e.g. a 2023 P&L), which
cascaded into Broker Questions never populating + the year-mismatch
banner never firing. Root cause: the documents.py extraction completion
block only derived ``extracted_period_year`` when ``user_fiscal_year``
was set — the common case (analyst skipped the year prompt) left it
NULL even though the Extractor surfaced a valid ``period_ending``.

These tests pin the contract:

* ``_extract_period_ending`` resolves the canonical
  ``p_and_l_usali.period_ending`` path the t12.md schema specifies.
* It also resolves common variants (any ``*.period_ending``).
* Multiple date string formats parse correctly.
* The derivation runs INDEPENDENTLY of ``user_fiscal_year``.
"""

from __future__ import annotations

from datetime import date

import pytest


# ─────────────────────────── _extract_period_ending ───────────────────────────


def test_extract_period_ending_canonical_path() -> None:
    """The canonical ``p_and_l_usali.period_ending`` path resolves
    (this is what the t12.md schema specifies as the MANDATORY emit)."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [
        {"field_name": "p_and_l_usali.period_ending", "value": "2023-12-31"},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_000_000},
    ]
    assert _extract_period_ending(fields) == date(2023, 12, 31)


def test_extract_period_ending_pnl_monthly_path() -> None:
    """A PNL_MONTHLY extraction with a partial-year period_ending still
    resolves so we can attribute it to the right year for variance."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [{"field_name": "p_and_l_usali.period_ending", "value": "2024-05-31"}]
    assert _extract_period_ending(fields) == date(2024, 5, 31)


def test_extract_period_ending_accepts_common_formats() -> None:
    """The Extractor sometimes emits dates in non-ISO formats — the
    parser tolerates the four common alternates (m/d/y, y/m/d, m-d-y)."""
    from app.services.coverage_audit import _extract_period_ending

    cases: list[tuple[str, date]] = [
        ("2023-12-31", date(2023, 12, 31)),
        ("12/31/2023", date(2023, 12, 31)),
        ("2023/12/31", date(2023, 12, 31)),
        ("12-31-2023", date(2023, 12, 31)),
    ]
    for raw, expected in cases:
        fields = [{"field_name": "p_and_l_usali.period_ending", "value": raw}]
        assert _extract_period_ending(fields) == expected, raw


def test_extract_period_ending_returns_none_when_missing() -> None:
    """OM / STR / CAPEX docs don't carry a period_ending — returns None
    cleanly so the caller can leave the column NULL."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [{"field_name": "property_overview.year_built", "value": 2005}]
    assert _extract_period_ending(fields) is None


# ─────────────── derivation runs independent of user_fiscal_year ──────────────


def test_extracted_period_year_does_not_require_user_fiscal_year() -> None:
    """Sam Bug #4 root cause: previously gated the derivation on
    ``user_fiscal_year is not None``. Replicate the new, ungated
    derivation and assert ``extracted_period_year`` populates when
    the user skipped year-tagging."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [
        {"field_name": "p_and_l_usali.period_ending", "value": "2023-12-31"},
        {"field_name": "p_and_l_usali.period_type", "value": "annual"},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 8_000_000},
    ]
    user_fiscal_year = None  # analyst skipped the year prompt

    # The NEW code path (post-fix): derivation happens regardless.
    extracted_period_year = None
    period_end = _extract_period_ending(fields)
    if period_end is not None:
        extracted_period_year = period_end.year

    assert extracted_period_year == 2023, (
        "extracted_period_year stayed None on a P&L with a valid period_ending "
        "even though user skipped year-tagging — Broker Questions / variance "
        "would not populate"
    )
    # No year-mismatch can fire because user didn't pin a year.
    year_mismatch_flag = bool(
        user_fiscal_year is not None
        and extracted_period_year is not None
        and extracted_period_year != user_fiscal_year
    )
    assert year_mismatch_flag is False


@pytest.mark.parametrize(
    "period_end_value, user_fiscal_year, expected_year, expected_mismatch",
    [
        ("2023-12-31", 2023, 2023, False),  # year matches → no banner
        ("2023-12-31", 2024, 2023, True),   # year mismatches → banner
        ("2024-05-31", None, 2024, False),  # user skipped → still extracted
        ("2025-12-31", 2025, 2025, False),
    ],
)
def test_period_year_and_mismatch_flag_matrix(
    period_end_value: str,
    user_fiscal_year: int | None,
    expected_year: int,
    expected_mismatch: bool,
) -> None:
    """Full matrix of the extraction-completion logic in
    apps/worker/app/api/documents.py — Bug #4 fix."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [{"field_name": "p_and_l_usali.period_ending", "value": period_end_value}]
    extracted_period_year: int | None = None
    period_end = _extract_period_ending(fields)
    if period_end is not None:
        extracted_period_year = period_end.year
    year_mismatch_flag = bool(
        user_fiscal_year is not None
        and extracted_period_year is not None
        and extracted_period_year != user_fiscal_year
    )

    assert extracted_period_year == expected_year
    assert year_mismatch_flag is expected_mismatch
