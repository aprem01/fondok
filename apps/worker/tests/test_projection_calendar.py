"""FON-41 #2 — the projection grid has a calendar, and it is never guessed.

``RevenueProjectionYear.year`` is an ORDINAL (1..hold_years). Before this the
statement printed the ordinal underneath its own column header, producing
Sam's *"Base Year 1, Year 1 2, Year 2 3"*. The revenue engine now emits the
calendar alongside the ordinals, anchored on the acquisition close date — the
same date the timeline engine is built on.

The hard rule pinned here: with NO close date the engine emits NO calendar.
Not the wall-clock year, not the extraction year, not year 1 — nothing, so the
UI renders the ordinal alone rather than a fabricated date.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fondok_schemas.underwriting import RevenueEngineInput

from app.engines.revenue import (
    RevenueEngine,
    projection_calendar_years,
    projection_start_year,
)


def _payload(close_date: str | None, hold_years: int = 5) -> RevenueEngineInput:
    return RevenueEngineInput(
        deal_id=uuid4(),
        keys=132,
        starting_occupancy=0.762,
        starting_adr=385.0,
        occupancy_growth=0.008,
        adr_growth=0.04,
        fb_revenue_per_occupied_room=88.0,
        other_revenue_pct_of_rooms=0.065,
        hold_years=hold_years,
        acquisition_close_date=close_date,
    )


def test_projection_calendar_years_anchor_on_close_date() -> None:
    """A 2025-09-30 close projects 2025 → 2029 over a five-year hold."""
    out = RevenueEngine().run(_payload("2025-09-30"))

    assert out.projection_start_year == 2025
    assert out.projection_calendar_years == [2025, 2026, 2027, 2028, 2029]
    # The ordinals are untouched — the calendar rides ALONGSIDE them.
    assert [y.year for y in out.years] == [1, 2, 3, 4, 5]


def test_a_december_close_starts_the_projection_in_the_following_year() -> None:
    """Operating Year 1 is the calendar year of the first operating month
    (close + 1 month), matching the timeline engine's schedule."""
    assert projection_start_year("2025-12-15") == 2026
    assert projection_calendar_years("2025-12-15", 3) == [2026, 2027, 2028]
    # …and a November close still starts in the close year.
    assert projection_start_year("2025-11-30") == 2025


def test_no_close_date_emits_no_year_at_all() -> None:
    """No anchor → no calendar. Never the wall clock, never a guess."""
    out = RevenueEngine().run(_payload(None))

    assert out.projection_start_year is None
    assert out.projection_calendar_years == []
    # The rows still carry their ordinals, so the statement can show "Year 1"
    # with no year underneath it.
    assert [y.year for y in out.years] == [1, 2, 3, 4, 5]


def test_an_unparseable_close_date_emits_no_year_either() -> None:
    out = RevenueEngine().run(_payload("not-a-date"))
    assert out.projection_start_year is None
    assert out.projection_calendar_years == []


def test_the_calendar_has_exactly_one_year_per_projection_row() -> None:
    for hold in (1, 3, 7, 10):
        out = RevenueEngine().run(_payload("2026-03-31", hold_years=hold))
        assert len(out.projection_calendar_years) == len(out.years) == hold
        assert out.projection_calendar_years[0] == 2026


def test_the_calendar_moves_no_revenue_number() -> None:
    """Display-only: the same deal with and without a close date projects
    byte-identical revenue."""
    anchored = RevenueEngine().run(_payload("2025-09-30"))
    bare = RevenueEngine().run(_payload(None))

    for a, b in zip(anchored.years, bare.years, strict=True):
        assert a.model_dump() == b.model_dump()
    assert anchored.total_revenue_cagr == pytest.approx(
        bare.total_revenue_cagr, rel=0, abs=0
    )
