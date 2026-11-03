"""FON-121 test helper — deal years that ARE calendar years (a 31 Dec close).

Most topline tests were written on calendar years (Year 1 = 2026). A deal
that closes on 31 December has deal years Jan–Dec, so those tests keep their
meaning by passing ``calendar_deal_years([2026, 2027, …])``.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.engines.deal_years import DealYear, deal_years


def calendar_deal_years(years: Sequence[int]) -> list[DealYear]:
    """Jan–Dec deal years for consecutive calendar ``years`` ([] for [])."""
    ys = [int(y) for y in years]
    if not ys:
        return []
    assert ys == list(range(ys[0], ys[0] + len(ys))), "consecutive calendar years only"
    return deal_years(f"{ys[0] - 1}-12-31", len(ys))
