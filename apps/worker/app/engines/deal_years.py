"""FON-121 — the model calendar: deal years run 12 months from the close.

THE one mechanism that places model years on the calendar (it replaces the
FON-41 rule "Year 1 = the calendar year of the first operating month").

* The first operating month is the month after the close month (the FON-41
  close + 1 month rule): a 2025-09-30 close starts operations on 1 Oct 2025;
  a 2025-12-31 close on 1 Jan 2026. A close ON the 1st starts that month. A
  mid-month close's stub is not a period of its own.
* Year ``y`` is the twelve months starting ``y − 1`` years after that month:
  the Kimpton close puts Year 1 on Oct 2025–Sep 2026 and Year 3 on
  Oct 2027–Sep 2028.
* Each deal year names the calendar years it spans and how many of its months
  fall in each (``calendar_months``): market forecasts are published by
  calendar year, and the growth INTO a deal year is the month-weighted blend
  of those calendar years' growth (``services.topline_growth``).

No close date → no deal years (the statement shows the ordinal alone; never
the wall clock).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fondok_schemas.underwriting import DealYearSpan

from .timeline import parse_iso_date

MONTH_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _add_months(d: date, n: int) -> date:
    """The first day of the month ``n`` months after ``d``'s month."""
    k = d.year * 12 + (d.month - 1) + n
    return date(k // 12, k % 12 + 1, 1)


def first_operating_month(close: date) -> date:
    """First day of the first operating month: the month after the close
    month — or, for a close ON the 1st, that same month (review M4: a
    2025-10-01 close owns October)."""
    return close if close.day == 1 else _add_months(close, 1)


@dataclass(frozen=True)
class DealYear:
    """One model year: twelve months from ``start`` (first day) to ``end``
    (last day)."""

    year: int
    start: date

    @property
    def end(self) -> date:
        return _add_months(self.start, 12) - timedelta(days=1)

    @property
    def label(self) -> str:
        s, e = self.start, self.end
        if s.year == e.year:
            return f"{MONTH_ABBR[s.month - 1]}–{MONTH_ABBR[e.month - 1]} {s.year}"
        return f"{MONTH_ABBR[s.month - 1]} {s.year}–{MONTH_ABBR[e.month - 1]} {e.year}"

    @property
    def calendar_months(self) -> tuple[tuple[int, int], ...]:
        """``((calendar year, months of this deal year in it), …)``, oldest first."""
        first = 13 - self.start.month
        if first >= 12:
            return ((self.start.year, 12),)
        return ((self.start.year, first), (self.start.year + 1, 12 - first))

    @property
    def weights(self) -> dict[int, float]:
        return {cy: m / 12.0 for cy, m in self.calendar_months}

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def span(self) -> DealYearSpan:
        """The published record (``fondok_schemas.underwriting.DealYearSpan``)."""
        return DealYearSpan.model_validate(self.as_dict())

    def as_dict(self) -> dict[str, Any]:
        return {
            "year": self.year,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "label": self.label,
            "calendar_months": [{"calendar_year": cy, "months": m} for cy, m in self.calendar_months],
            "days": self.days,
        }


def deal_years(close: date | str | None, n: int) -> list[DealYear]:
    """Deal years ``1..n`` from the close date; ``[]`` with no (parseable) date."""
    d = close if isinstance(close, date) else parse_iso_date(close)
    if d is None or n <= 0:
        return []
    start = first_operating_month(d)
    return [DealYear(year=i + 1, start=_add_months(start, 12 * i)) for i in range(n)]


def deal_year_spans(close: date | str | None, n: int) -> list[DealYearSpan]:
    return [y.span() for y in deal_years(close, n)]


def deal_year_after(years: list[DealYear]) -> DealYear | None:
    """The deal year after the last listed one (the exit-NOI year)."""
    if not years:
        return None
    last = years[-1]
    return DealYear(year=last.year + 1, start=_add_months(last.start, 12))


def first_full_deal_year_index(years: list[DealYear], when: date) -> int | None:
    """0-based index of the first deal year that starts on or after ``when``,
    on the deal calendar of ``years`` — not clamped: past the listed years it
    keeps counting, and before Year 1 it is negative (deal year 0 = the twelve
    months before Year 1). ``None`` without deal years."""
    if not years:
        return None
    start = years[0].start
    months = (when.year - start.year) * 12 + (when.month - start.month) + (1 if when.day > 1 else 0)
    return -(-months // 12)


__all__ = [
    "DealYear",
    "deal_year_after",
    "deal_year_spans",
    "deal_years",
    "first_full_deal_year_index",
    "first_operating_month",
]
