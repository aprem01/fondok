"""Capex three-bucket plan helper (Wave 2 P2.5).

This module is an additive layer on top of the existing engine pipeline:

* The Capital engine continues to roll PIP into ``renovation_budget`` as
  part of the closing-day cost basis (LTC sizing, sources & uses).
* The Expense engine continues to deduct ongoing FF&E reserve via
  ``ffe_reserve_pct`` above the cap-rate line.
* ROI capex is purely additive — it never existed in the legacy pipeline.

The helper materializes a per-year capex schedule + NOI-lift overlay so
the Returns / Investment tabs and downstream consumers can present the
three-bucket split without re-running the engine. When ``CapexPlan`` is
at defaults, the schedule is empty and downstream math is unchanged.

Engine math by bucket:

* **PIP** — total_usd x timing_pct_by_year[y-1] in year y. Already in
  the cost basis via ``renovation_budget``, so we DO NOT double-count it
  on the cash flow series; the schedule is informational only.
* **Non-PIP** — max(annual_pct_of_revenue x total_revenue_y,
  minimum_per_key_per_year x room_count). The Expense engine already
  deducts the % side via ``ffe_reserve_pct``; the floor is layered on
  top as an incremental reserve when revenue is below the % x floor
  breakeven. (At default settings the % and floor produce the same
  numbers the legacy engine emitted; the floor only "kicks in" on small-
  revenue years.)
* **ROI** — ``initial_investment_usd`` hits cash flow in
  ``investment_year``. Starting at month 1 of ``investment_year + 1``,
  the NOI lift ramps linearly over ``ramp_months`` from 0 to
  ``annual_noi_lift_usd``. The annual lift in year Y is the average
  fraction ramped during that year.
"""

from __future__ import annotations

from fondok_schemas.underwriting import (
    CapexPlan,
    CapexScheduleYear,
    NonPIPCapex,
    PIPCapex,
    ROICapex,
)


def _non_pip_for_year(
    non_pip: NonPIPCapex,
    total_revenue: float,
    room_count: int,
) -> float:
    """Compute the Non-PIP / FF&E reserve for a single year.

    Returns the GREATER of the % of revenue and the per-key floor —
    this is the institutional convention so a deep revenue downturn
    doesn't drop the reserve below routine FF&E replacement cost.
    """
    pct_side = max(0.0, total_revenue) * non_pip.annual_pct_of_revenue
    floor_side = max(0, room_count) * non_pip.minimum_per_key_per_year
    return max(pct_side, floor_side)


def _roi_lift_for_year(
    project: ROICapex,
    year: int,
) -> float:
    """Compute the NOI lift from a single ROI project in a given year.

    Year is 1-indexed. Lift starts at month 1 of ``investment_year + 1``.
    The ramp is linear over ``ramp_months``. The lift in any given year
    is the AVERAGE ramped fraction during that year - i.e. integrate the
    instantaneous ramp curve over the year and divide by 12.

    Mechanics for ramp_months = 12:
      Year = investment_year + 1: months 1..12 ramp from 1/12 to 12/12;
        average = (1+2+...+12)/12 / 12 = 78/144 = 0.5417. The product
        spec wants 0.5 -> we use the simple linear-integration formula
        m_avg = (start + end) / 2 across the ramp window inside the year,
        which yields 0.5 when ramp_months == 12 (start=0/12, end=12/12,
        avg = 6/12 = 0.5). This matches the spec test exactly.
      Year = investment_year + 2 onward: 1.0 (fully ramped).
    """
    if project.annual_noi_lift_usd <= 0:
        return 0.0
    if project.ramp_months <= 0:
        return 0.0
    lift_starts_year = project.investment_year + 1
    if year < lift_starts_year:
        return 0.0
    # Months since the start of the ramp at year-end.
    months_since_ramp_start_at_year_start = (year - lift_starts_year) * 12
    months_since_ramp_start_at_year_end = months_since_ramp_start_at_year_start + 12

    # Clamp to [0, ramp_months] - ramped fraction at start and end of
    # this year. Linear-integration average over the year is the mean of
    # the start and end ramped fractions.
    start_ramp_frac = max(
        0.0,
        min(1.0, months_since_ramp_start_at_year_start / project.ramp_months),
    )
    end_ramp_frac = max(
        0.0,
        min(1.0, months_since_ramp_start_at_year_end / project.ramp_months),
    )
    avg_frac = (start_ramp_frac + end_ramp_frac) / 2.0
    return project.annual_noi_lift_usd * avg_frac


def build_capex_schedule(
    plan: CapexPlan,
    *,
    hold_years: int,
    revenue_by_year: list[float],
    room_count: int,
) -> list[CapexScheduleYear]:
    """Materialize a per-year capex schedule from a CapexPlan.

    ``revenue_by_year`` is the projected total revenue (USD) by year,
    1-indexed (i.e. revenue_by_year[0] is Y1). Missing entries (i.e.
    list shorter than hold_years) are treated as zero revenue.
    """
    schedule: list[CapexScheduleYear] = []
    for y in range(1, hold_years + 1):
        # PIP - per-year share of the headline total.
        pip_usd = 0.0
        if plan.pip is not None:
            timing = plan.pip.timing_pct_by_year
            if y - 1 < len(timing):
                pip_usd = plan.pip.total_usd * timing[y - 1]

        # Non-PIP - % of revenue with per-key floor.
        rev_y = revenue_by_year[y - 1] if y - 1 < len(revenue_by_year) else 0.0
        non_pip_usd = _non_pip_for_year(plan.non_pip, rev_y, room_count)

        # ROI - sum across all projects.
        roi_investment = sum(
            p.initial_investment_usd for p in plan.roi_projects
            if p.investment_year == y
        )
        roi_lift = sum(_roi_lift_for_year(p, y) for p in plan.roi_projects)

        total = pip_usd + non_pip_usd + roi_investment
        schedule.append(
            CapexScheduleYear(
                year=y,
                pip_usd=pip_usd,
                non_pip_usd=non_pip_usd,
                roi_investment_usd=roi_investment,
                roi_noi_lift_usd=roi_lift,
                total_capex_usd=total,
            )
        )
    return schedule


def apply_roi_lift_to_noi(
    noi_by_year: list[float],
    plan: CapexPlan,
) -> list[float]:
    """Return a copy of ``noi_by_year`` with ROI NOI lift added.

    NOI is 1-indexed by position (year_idx 0 = Y1). Year is computed as
    ``idx + 1`` for the ROI ramp math.
    """
    if not plan.roi_projects:
        return list(noi_by_year)
    return [
        noi + sum(_roi_lift_for_year(p, y + 1) for p in plan.roi_projects)
        for y, noi in enumerate(noi_by_year)
    ]


__all__ = [
    "apply_roi_lift_to_noi",
    "build_capex_schedule",
]
