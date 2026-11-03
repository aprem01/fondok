"""FON-41 #2 / FON-121 — the projection grid has a calendar, and it is never guessed.

``RevenueProjectionYear.year`` is an ORDINAL (1..hold_years). Before this the
statement printed the ordinal underneath its own column header, producing
Sam's *"Base Year 1, Year 1 2, Year 2 3"*. The revenue engine now emits the
calendar alongside the ordinals, anchored on the acquisition close date — the
same date the timeline engine is built on. FON-121 (domain lead, Eshan
confirmed): each model year is a DEAL year — twelve months from the close —
so a 2025-09-30 close puts Year 1 on Oct 2025–Sep 2026.

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
    exit_noi_deal_year,
    projection_deal_years,
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


def test_projection_deal_years_anchor_on_close_date() -> None:
    """A 2025-09-30 close projects Oct 2025–Sep 2026 … Oct 2029–Sep 2030."""
    out = RevenueEngine().run(_payload("2025-09-30"))

    assert [d.label for d in out.projection_deal_years] == [
        "Oct 2025–Sep 2026", "Oct 2026–Sep 2027", "Oct 2027–Sep 2028",
        "Oct 2028–Sep 2029", "Oct 2029–Sep 2030",
    ]
    assert out.projection_deal_years[2].start == "2027-10-01"
    assert out.projection_deal_years[2].end == "2028-09-30"
    # The exit-NOI year is the one after the hold.
    assert out.exit_noi_deal_year is not None
    assert out.exit_noi_deal_year.label == "Oct 2030–Sep 2031"
    # The ordinals are untouched — the calendar rides ALONGSIDE them.
    assert [y.year for y in out.years] == [1, 2, 3, 4, 5]


def test_a_december_close_starts_the_projection_in_the_following_year() -> None:
    """Operating Year 1 starts the month after the close month."""
    assert [d.label for d in projection_deal_years("2025-12-15", 3)] == [
        "Jan–Dec 2026", "Jan–Dec 2027", "Jan–Dec 2028",
    ]
    assert projection_deal_years("2025-11-30", 1)[0].label == "Dec 2025–Nov 2026"
    assert exit_noi_deal_year("2025-12-15", 3).label == "Jan–Dec 2029"  # type: ignore[union-attr]


def test_no_close_date_emits_no_year_at_all() -> None:
    """No anchor → no calendar. Never the wall clock, never a guess."""
    out = RevenueEngine().run(_payload(None))

    assert out.projection_deal_years == []
    assert out.exit_noi_deal_year is None
    # The rows still carry their ordinals, so the statement can show "Year 1"
    # with no year underneath it.
    assert [y.year for y in out.years] == [1, 2, 3, 4, 5]


def test_an_unparseable_close_date_emits_no_year_either() -> None:
    out = RevenueEngine().run(_payload("not-a-date"))
    assert out.projection_deal_years == []
    assert out.exit_noi_deal_year is None


def test_the_calendar_has_exactly_one_year_per_projection_row() -> None:
    for hold in (1, 3, 7, 10):
        out = RevenueEngine().run(_payload("2026-03-31", hold_years=hold))
        assert len(out.projection_deal_years) == len(out.years) == hold
        assert out.projection_deal_years[0].label == "Apr 2026–Mar 2027"


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
