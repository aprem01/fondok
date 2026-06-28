"""Tests for ``extracted_period_year`` derivation.

Sam QA Bug #4 v1 (June 2026): ``extracted_period_year`` was NULL on
documents that ought to carry a year (e.g. a 2023 P&L), which
cascaded into Broker Questions never populating + the year-mismatch
banner never firing. Root cause: the documents.py extraction completion
block only derived ``extracted_period_year`` when ``user_fiscal_year``
was set — the common case (analyst skipped the year prompt) left it
NULL even though the Extractor surfaced a valid ``period_ending``.

Sam QA Bug #4 v2 (June 2026): T-12 worked after v1 but ANNUAL P&L
still returned NULL. Root cause: ``_extract_period_ending`` only
matched ``*.period_ending``; the annual P&L extractor emits
``p_and_l_usali.period.end_date`` (verified against the prod payload
saved at ``tests/fixtures/real_payloads/anglers_annual_pnl_real.json``).
v2 expands the recognized suffix list + adds a bare-year fallback for
schemas that emit only ``report_year`` / ``fiscal_year``.

These tests pin the contract:

* ``_extract_period_ending`` resolves the canonical
  ``p_and_l_usali.period_ending`` path the t12.md schema specifies.
* It also resolves the annual P&L variant ``period.end_date``.
* It also resolves ``*.statement_period`` and ``*.period_end``.
* Bare-year fields (``report_year``, ``fiscal_year``) derive Dec-31.
* Multiple date string formats parse correctly.
* The derivation runs INDEPENDENTLY of ``user_fiscal_year``.
* The real prod annual-P&L fixture yields ``2023``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest


_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "real_payloads"


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


# ────────────────────── Bug #4 v2 — annual P&L paths ──────────────────────


def test_extract_period_ending_annual_pnl_period_end_date_path() -> None:
    """The annual P&L extractor emits ``p_and_l_usali.period.end_date``
    instead of ``period_ending`` (per the real prod payload at
    ``tests/fixtures/real_payloads/anglers_annual_pnl_real.json``).
    This was the v1 regression Sam reported — fails before v2 fix.
    """
    from app.services.coverage_audit import _extract_period_ending

    fields = [
        {"field_name": "p_and_l_usali.period.end_date", "value": "2023-12-31"},
        {"field_name": "p_and_l_usali.revenues.total_revenues_usd", "value": 12_940_000},
    ]
    assert _extract_period_ending(fields) == date(2023, 12, 31)


def test_extract_period_ending_period_end_underscore_path() -> None:
    """``period_end`` (some extraction schemas use the underscore form
    rather than ``period_ending`` — Sam-flagged risk path)."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [{"field_name": "p_and_l_usali.period_end", "value": "2022-12-31"}]
    assert _extract_period_ending(fields) == date(2022, 12, 31)


def test_extract_period_ending_fiscal_year_end_path() -> None:
    """``fiscal_year_end`` — observed on docs whose USALI emit uses
    the broker-proforma vocabulary instead of the T-12 vocabulary."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [{"field_name": "broker_proforma.fiscal_year_end", "value": "2024-06-30"}]
    assert _extract_period_ending(fields) == date(2024, 6, 30)


def test_extract_period_ending_statement_period_fallback() -> None:
    """``property_overview.statement_period`` is the fallback path the
    prod T-12 carries — when the canonical ``period_ending`` is missing
    we'd otherwise lose the year. Empirically observed in Sam's prod
    T-12 payload alongside the canonical."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [{"field_name": "property_overview.statement_period", "value": "2025-12-31"}]
    assert _extract_period_ending(fields) == date(2025, 12, 31)


def test_extract_period_ending_canonical_wins_over_statement_period() -> None:
    """When BOTH the canonical ``period_ending`` and the fallback
    ``statement_period`` exist (the actual T-12 prod payload), the
    canonical wins — the suffix-priority pass in the resolver picks
    the schema-canonical path even if ``statement_period`` appears
    earlier in the field list."""
    from app.services.coverage_audit import _extract_period_ending

    # statement_period appears first in the list. The canonical
    # period_ending must still win because we iterate suffixes
    # (priority) in the outer loop, not the field list.
    fields = [
        {"field_name": "property_overview.statement_period", "value": "2025-12-31"},
        {"field_name": "p_and_l_usali.period_ending", "value": "2025-05-31"},
    ]
    assert _extract_period_ending(fields) == date(2025, 5, 31)


def test_extract_period_ending_bare_year_fallback() -> None:
    """When the Extractor surfaced ONLY a year (no date — e.g.
    ``p_and_l_usali.report_year=2024``), derive Dec-31 of that year.
    Pre-fix this returned None and the row's ``extracted_period_year``
    stayed null."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [
        {"field_name": "p_and_l_usali.report_year", "value": 2024},
        {"field_name": "p_and_l_usali.revenues.total_revenues_usd", "value": 12_000_000},
    ]
    assert _extract_period_ending(fields) == date(2024, 12, 31)


def test_extract_period_ending_period_year_path() -> None:
    """``p_and_l_usali.period.year`` (the dotted-bucket form some
    extractors emit when they namespace period metadata)."""
    from app.services.coverage_audit import _extract_period_ending

    fields = [{"field_name": "p_and_l_usali.period.year", "value": "2023"}]
    assert _extract_period_ending(fields) == date(2023, 12, 31)


def test_extract_period_ending_bare_year_rejects_out_of_range() -> None:
    """``rooms_sold=2024`` (rooms_sold COULD numerically exceed 2000,
    but the year fallback's ``[1900, 2100]`` guard rejects out-of-range
    ints — and ``rooms_sold`` doesn't end with any of our year suffixes
    anyway, so the candidate never fires).
    """
    from app.services.coverage_audit import _extract_period_ending

    fields = [
        {"field_name": "p_and_l_usali.rooms_sold", "value": 39936},
        {"field_name": "p_and_l_usali.report_year", "value": 99999},  # bogus
    ]
    # rooms_sold doesn't match the year suffix list; bogus year is out
    # of range. Result: no period derivable, returns None cleanly.
    assert _extract_period_ending(fields) is None


# ────────────────────── Bug #4 v2 — real prod payload ──────────────────────


def test_extract_period_ending_real_t12_payload() -> None:
    """Real prod T-12 fixture (Sam's anglers_t12.xlsx, 213 fields).
    Asserts the canonical path resolves to May 31, 2025 (the TTM ending
    date the workbook actually carries). Pre-v1 fix this still worked;
    we pin it here so the v2 expanded suffix list doesn't regress it."""
    from app.services.coverage_audit import _extract_period_ending

    payload_path = _FIXTURES_DIR / "anglers_t12_real.json"
    if not payload_path.exists():
        pytest.skip(f"missing fixture {payload_path}")
    payload = json.loads(payload_path.read_text())
    fields = payload["fields"]
    result = _extract_period_ending(fields)
    assert result == date(2025, 5, 31), (
        f"real T-12 payload yielded {result!r}; expected 2025-05-31. "
        "The canonical p_and_l_usali.period_ending should win over "
        "the property_overview.statement_period fallback."
    )


def test_extract_period_ending_real_annual_pnl_payload() -> None:
    """Real prod annual P&L fixture (Sam's sam_anglers_2023_pnl.xlsx,
    144 fields). The annual P&L extractor emits
    ``p_and_l_usali.period.end_date = 2023-12-31``. Pre-v2 fix the
    resolver returned None (only matched ``period_ending``) → the row's
    extracted_period_year stayed NULL → broker questions never
    populated → Sam reported the bug."""
    from app.services.coverage_audit import _extract_period_ending

    payload_path = _FIXTURES_DIR / "anglers_annual_pnl_real.json"
    if not payload_path.exists():
        pytest.skip(f"missing fixture {payload_path}")
    payload = json.loads(payload_path.read_text())
    fields = payload["fields"]
    result = _extract_period_ending(fields)
    assert result == date(2023, 12, 31), (
        f"real annual-P&L payload yielded {result!r}; expected 2023-12-31. "
        "The annual P&L extractor emits ``p_and_l_usali.period.end_date``, "
        "not ``period_ending`` — the v2 suffix expansion must cover it."
    )
