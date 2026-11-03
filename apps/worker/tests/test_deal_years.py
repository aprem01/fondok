"""FON-121 — model years run 12 months from the acquisition close.

Domain lead (Sam, Eshan confirmed): a 2025-09-30 close puts Year 1 on
Oct 2025–Sep 2026 and Year 3 on Oct 2027–Sep 2028. Market forecasts are
published by CALENDAR year, so every deal year names the calendar months it
spans; the growth into a deal year is the month-weighted blend of the
calendar years it covers (``topline_growth``).
"""

from __future__ import annotations

from datetime import date

import pytest

from app.engines.deal_years import (
    deal_years,
    first_full_deal_year_index,
    first_operating_month,
)


def test_kimpton_close_puts_year_one_on_oct_2025_to_sep_2026() -> None:
    ys = deal_years("2025-09-30", 5)
    assert [y.year for y in ys] == [1, 2, 3, 4, 5]
    assert ys[0].start == date(2025, 10, 1)
    assert ys[0].end == date(2026, 9, 30)
    assert ys[0].label == "Oct 2025–Sep 2026"
    assert ys[2].label == "Oct 2027–Sep 2028"
    assert ys[0].calendar_months == ((2025, 3), (2026, 9))
    assert ys[0].weights == {2025: 0.25, 2026: 0.75}


def test_a_year_end_close_is_a_calendar_year() -> None:
    ys = deal_years("2025-12-31", 2)
    assert ys[0].label == "Jan–Dec 2026"
    assert ys[0].calendar_months == ((2026, 12),)
    assert ys[1].label == "Jan–Dec 2027"


def test_first_operating_month_is_the_month_after_the_close_month() -> None:
    # The FON-41 rule (close + 1 month) is kept; a mid-month close's stub is
    # not modelled as its own period.
    assert first_operating_month(date(2025, 9, 30)) == date(2025, 10, 1)
    assert first_operating_month(date(2025, 12, 15)) == date(2026, 1, 1)
    assert first_operating_month(date(2025, 1, 1)) == date(2025, 2, 1)


def test_days_count_the_leap_day_inside_the_span() -> None:
    ys = deal_years("2027-09-30", 2)  # Oct 2027–Sep 2028 holds 29 Feb 2028
    assert ys[0].days == 366
    assert ys[1].days == 365


def test_no_or_bad_close_date_has_no_deal_years() -> None:
    assert deal_years(None, 5) == []
    assert deal_years("not-a-date", 5) == []
    assert deal_years("2025-09-30", 0) == []


def test_as_dict_is_the_published_shape() -> None:
    d = deal_years(date(2025, 9, 30), 1)[0].as_dict()
    assert d == {
        "year": 1,
        "start": "2025-10-01",
        "end": "2026-09-30",
        "label": "Oct 2025–Sep 2026",
        "calendar_months": [{"calendar_year": 2025, "months": 3}, {"calendar_year": 2026, "months": 9}],
        "days": 365,
    }


@pytest.mark.parametrize(
    ("reopening", "expected"),
    [
        ("2025-10-01", 0),  # reopens on Year 1's first day → Year 1 is full
        ("2025-10-02", 1),  # any later day → the next deal year is the first full one
        ("2026-09-30", 1),
        ("2026-10-01", 1),
        ("2025-09-01", 0),
        ("2024-01-01", -1),  # before the close: deal year −1 (Oct 2023–Sep 2024 starts before it)
    ],
)
def test_first_full_deal_year_after_a_reopening(reopening: str, expected: int) -> None:
    ys = deal_years("2025-09-30", 5)
    assert first_full_deal_year_index(ys, date.fromisoformat(reopening)) == expected


def test_first_full_deal_year_counts_past_the_listed_years() -> None:
    # The index is on the deal calendar, not clamped to the hold (the
    # stabilization resolver clamps and says so).
    ys = deal_years("2025-09-30", 2)
    assert first_full_deal_year_index(ys, date(2030, 1, 1)) == 5
    assert first_full_deal_year_index([], date(2030, 1, 1)) is None
