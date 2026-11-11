"""Sam, 2026-11-04 — a new or reopened hotel earns nothing before it opens. Revenue
starts at the opening date, the opening year is prorated by months open, the ramp's
Year 1 is the first 12 months after opening, and the closed months carry no operating
costs (pre-opening costs are the analyst's input). Sam, 2026-11-05: fixed charges keep
running except on a new build, and displacement months inside the closure are dropped.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from datetime import date
from uuid import uuid4

import pytest
from fondok_schemas.underwriting import PIPDisplacement, RenovationDisplacementPeriod, RevenueEngineInput

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from app.engines.fb_revenue import FBRevenueEngine, FBRevenueInput
from app.engines.revenue import (
    RevenueEngine,
    _y1_revenue_factor_and_adr_drag,
    default_ramp_by_year,
    months_open_shares,
)
from app.engines.timeline import whole_months_between
from app.export.live_payload import _build_investment, t12_out_of_date
from app.export.pl_workbooks import proj_cell_key, projection_values
from app.services.engine_runner import _displacement_status
from app.services.topline import (
    _displacement_on_top,
    closed_months_for,
    opening_offset_for,
)


def _revenue(**kw: object):
    return RevenueEngine().run(RevenueEngineInput(
        deal_id=uuid4(), keys=100, starting_occupancy=0.7, starting_adr=300.0, hold_years=5,
        acquisition_close_date="2025-09-30", occupancy_growth=0.0, adr_growth=0.03,
        fb_revenue_per_occupied_room=40.0, starting_resort_fees=120000.0, **kw,
    ))


@pytest.mark.parametrize(
    "offset,expected",
    [
        (0, [0.85, 0.95]),  # opens at the first month: the plain steps
        (12, [0.85, 0.85, 0.95]),  # opens at a year start, exact steps
        (6, [0.85, 0.9, 0.975]),  # half a year in: Year 2 blends 85 and 95
        (-6, [0.9, 0.975]),  # opened six months before the close
    ],
)
def test_the_ramp_counts_from_the_opening_month(offset: int, expected: list[float]) -> None:
    assert default_ramp_by_year(len(expected) + 1, 1, offset) == expected


def test_a_month_opened_partway_through_does_not_count() -> None:
    close = "2025-09-30"  # first operating month Oct 2025
    assert closed_months_for({"acquisition_close_date": close, "reopening_date": "2026-10-01"}) == 12
    assert closed_months_for({"acquisition_close_date": close, "reopening_date": "2026-10-02"}) == 13
    assert closed_months_for({"acquisition_close_date": close, "reopening_date": "2027-03-31"}) == 18


def test_no_date_or_an_open_hotel_closes_nothing() -> None:
    close = "2025-09-30"
    assert closed_months_for({"acquisition_close_date": close}) == 0
    assert closed_months_for({"acquisition_close_date": close, "deal_type": "development"}) == 0
    assert closed_months_for({"reopening_date": "2027-03-31"}) == 0  # can't be placed
    # Finished its ramp before the projection: an operating hotel.
    assert opening_offset_for({"acquisition_close_date": close, "reopening_date": "2020-01-01"}) is None


def test_closed_months_earn_nothing_and_the_opening_year_is_prorated() -> None:
    full = _revenue().years
    closed = _revenue(closed_months=18).years
    assert closed[0].total_revenue == 0.0 and closed[0].occupancy == 0.0
    assert closed[1].rooms_revenue == pytest.approx(full[1].rooms_revenue * 0.5)
    assert closed[1].fb_revenue == pytest.approx(full[1].fb_revenue * 0.5)
    assert closed[1].resort_fees == pytest.approx(full[1].resort_fees * 0.5)
    assert closed[1].total_revenue == pytest.approx(full[1].total_revenue * 0.5)
    # Nothing cascades: the later years are the open hotel's.
    for y in (2, 3, 4):
        assert closed[y].total_revenue == pytest.approx(full[y].total_revenue)
    assert closed[1].occupancy_displacement_factor == pytest.approx(0.5)


def test_every_occupancy_trace_still_reconciles() -> None:
    out = _revenue(closed_months=18)
    for i, year in enumerate(out.years):
        assert out.provenance[f"years[{i}].occupancy"].value == pytest.approx(year.occupancy)
        assert out.provenance[f"years[{i}].resort_fees"].value == pytest.approx(year.resort_fees)
    assert "open_share" in out.provenance["years[1].occupancy"].formula


def _sched(start: int, end: int, offline: float = 0.0, discount: float = 0.0) -> RenovationDisplacementPeriod:
    return RenovationDisplacementPeriod(
        start_month=start, end_month=end, rooms_offline_pct=offline, rate_discount_pct=discount
    )


def test_a_renovation_period_inside_the_closure_is_not_counted_twice() -> None:
    closed = _revenue(closed_months=18).years
    both = _revenue(closed_months=18, renovation_displacement_schedule=[_sched(13, 18, offline=1.0)]).years
    assert both[1].total_revenue == pytest.approx(closed[1].total_revenue)


def test_the_schedule_applies_to_the_open_months_only() -> None:
    closed = _revenue(closed_months=18).years
    # Half offline Jul-Dec of an opening year closed Jan-Jun: 3 of 12 months' worth open.
    half = _revenue(closed_months=18, renovation_displacement_schedule=[_sched(19, 24, offline=0.5)]).years
    assert half[1].rooms_revenue == pytest.approx(closed[1].rooms_revenue * 0.5)
    # A discount through every open month: the year's ADR is that discount, no more.
    off = _revenue(closed_months=18, renovation_displacement_schedule=[_sched(19, 24, discount=0.2)]).years
    assert off[1].adr == pytest.approx(closed[1].adr * 0.8)


def _expense(as_open, actual=None, **kw):
    def fb(out):
        return FBRevenueEngine().run(FBRevenueInput(deal_id=uuid4(), revenue=out))

    return ExpenseEngine().run(ExpenseEngineInput(
        deal_id=uuid4(), revenue=fb(as_open), mgmt_fee_pct=0.03,
        ffe_reserve_pct=0.04, expense_growth=0.03, grow_opex_independently=True,
        open_shares_by_year=months_open_shares(18, 5) if actual is not None else {},
        actual_revenue=fb(actual) if actual is not None else None,
        **kw,
    ))


def test_the_closed_months_carry_no_costs_and_later_years_keep_theirs() -> None:
    open_hotel = _revenue()
    baseline = _expense(open_hotel)
    closed = _expense(open_hotel, _revenue(closed_months=18))
    y1_fixed, y2_fixed = (baseline.years[i].fixed_charges.total for i in (0, 1))
    assert y1_fixed > 0.0
    assert closed.years[0].undistributed.total == 0.0
    # Taxes and insurance don't stop when a hotel closes.
    assert closed.years[0].fixed_charges.total == pytest.approx(y1_fixed)
    assert closed.years[0].noi == pytest.approx(-y1_fixed)
    assert closed.years[1].fixed_charges.total == pytest.approx(y2_fixed)
    assert closed.years[1].noi == pytest.approx((baseline.years[1].noi + y2_fixed) * 0.5 - y2_fixed)
    # A closed Year 1 must not zero the out-years it seeds.
    assert closed.years[2].undistributed.total == pytest.approx(baseline.years[2].undistributed.total)
    assert closed.years[2].noi == pytest.approx(baseline.years[2].noi)
    assert closed.provenance["years[1].noi"].value == pytest.approx(closed.years[1].noi)
    assert closed.noi_cagr == baseline.noi_cagr


async def _model(**extra: object) -> dict:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import ENGINE_REGISTRY, _build_input_for, _load_engine_inputs
    from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed

    await run_startup_migrations()
    seed = {**kimpton_seed(), "acquisition_close_date": "2025-09-30", **extra}
    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, KIMPTON_FIXTURE_DEAL_ID, tenant_id="t", assumptions=seed)
    acc: dict = {}
    for name in ("revenue", "fb", "expense", "capital", "debt", "returns"):
        payload = _build_input_for(name, str(KIMPTON_FIXTURE_DEAL_ID), base, acc)
        acc[name] = ENGINE_REGISTRY[name]().run(payload)
        acc[f"{name}_input"] = payload
    return acc


@pytest.mark.asyncio
async def test_the_whole_model_runs_closed_and_the_sale_prices_a_full_year() -> None:
    open_hotel = await _model()
    closed = await _model(reopening_date="2027-03-31")
    assert closed["revenue"].years[0].total_revenue == 0.0
    y1_fixed = closed["expense"].years[0].fixed_charges.total
    assert y1_fixed > 0.0 and closed["expense"].years[0].noi == pytest.approx(-y1_fixed)
    assert closed["returns"].noi_by_year[0] == pytest.approx(-y1_fixed)
    assert closed["returns"].levered_irr < open_hotel["returns"].levered_irr
    # Opening in the sale year: the buyer still caps a full year.
    last = await _model(reopening_date="2030-03-31")
    last_fixed = open_hotel["expense"].years[-1].fixed_charges.total
    assert last["expense"].years[-1].noi == pytest.approx((open_hotel["expense"].years[-1].noi + last_fixed) * 0.5 - last_fixed)
    assert last["returns"].terminal_noi == pytest.approx(open_hotel["returns"].terminal_noi)


@pytest.mark.asyncio
async def test_a_hotel_still_closed_at_the_end_of_the_hold_has_no_exit_value() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="terminal_noi"):
        await _model(reopening_date="2031-03-31")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "displacement",
    [
        {"renovation_displacement_schedule": [
            {"start_month": 1, "end_month": 18, "rooms_offline_pct": 1.0, "rate_discount_pct": 0.0}
        ]},
        {"renovation_displacement_schedule": [
            {"start_month": 1, "end_month": 6, "rooms_offline_pct": 1.0, "rate_discount_pct": 0.0}
        ]},
        {"y1_occupancy_displacement_pct": 0.5},
    ],
)
async def test_displacement_beside_a_closure_cannot_lift_the_out_year_noi(displacement: dict) -> None:
    closed = await _model(reopening_date="2027-03-31")
    both = await _model(reopening_date="2027-03-31", **displacement)
    for a, b in zip(closed["expense"].years[2:], both["expense"].years[2:], strict=True):
        assert b.noi == pytest.approx(a.noi)
    assert both["returns"].terminal_noi == pytest.approx(closed["returns"].terminal_noi)


@pytest.mark.asyncio
async def test_a_refinance_in_the_opening_year_sizes_on_a_full_year() -> None:
    open_hotel = await _model()
    closed = await _model(reopening_date="2027-03-31")
    sizing = closed["debt_input"].refi_noi_by_year
    assert sizing[0] < 0.0  # fully closed: nothing to lend on
    assert sizing[1] == pytest.approx(open_hotel["expense"].years[1].noi)
    assert open_hotel["debt_input"].refi_noi_by_year == []


@pytest.mark.asyncio
async def test_years_the_closure_does_not_touch_keep_the_deals_own_displacement() -> None:
    # Half offline in Year 3, with the hotel open by then: the year matches the same
    # deal with no date, including the costs that follow its revenue.
    period = {"start_month": 25, "end_month": 36, "rooms_offline_pct": 0.5, "rate_discount_pct": 0.0}
    plain = await _model(renovation_displacement_schedule=[period], grow_opex_independently=False)
    closed = await _model(
        reopening_date="2027-03-31", renovation_displacement_schedule=[period], grow_opex_independently=False
    )
    assert closed["expense"].years[2].noi == pytest.approx(plain["expense"].years[2].noi)
    assert closed["expense"].years[3].noi == pytest.approx(plain["expense"].years[3].noi)


def test_an_analysts_year_1_fee_prorates_by_months_open() -> None:
    def fb(out):
        return FBRevenueEngine().run(FBRevenueInput(deal_id=uuid4(), revenue=out))

    out = ExpenseEngine().run(ExpenseEngineInput(
        deal_id=uuid4(), revenue=fb(_revenue()), mgmt_fee_pct=0.03, ffe_reserve_pct=0.04,
        expense_growth=0.03, grow_opex_independently=True, mgmt_fee_y1_override=1_000_000.0,
        open_shares_by_year=months_open_shares(6, 5), actual_revenue=fb(_revenue(closed_months=6)),
    ))
    assert out.years[0].mgmt_fee == pytest.approx(500_000.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "displacement",
    [
        {"y1_occupancy_displacement_pct": 0.5},
        {"y1_adr_displacement_pct": 0.3},
        {"renovation_displacement_schedule": [
            {"start_month": 1, "end_month": 12, "rooms_offline_pct": 1.0, "rate_discount_pct": 0.0}
        ]},
        {"renovation_displacement_schedule": [
            {"start_month": 1, "end_month": 6, "rooms_offline_pct": 0.5, "rate_discount_pct": 0.1}
        ]},
    ],
)
async def test_a_displaced_year_1_does_not_lower_later_years_costs(displacement: dict) -> None:
    plain = await _model()
    displaced = await _model(**displacement)
    for a, b in zip(plain["expense"].years[1:], displaced["expense"].years[1:], strict=True):
        assert b.undistributed.total == pytest.approx(a.undistributed.total)
        assert b.fixed_charges.total == pytest.approx(a.fixed_charges.total)
        assert b.noi == pytest.approx(a.noi)
    assert displaced["returns"].terminal_noi == pytest.approx(plain["returns"].terminal_noi)
    assert displaced["returns"].levered_irr < plain["returns"].levered_irr


@pytest.mark.asyncio
async def test_no_displacement_passes_no_seed() -> None:
    assert (await _model())["expense_input"].seed_year_one is None


def _pip(*offline: float, strategy: str = "rolling") -> PIPDisplacement:
    return PIPDisplacement(closure_strategy=strategy, pct_rooms_offline_by_month=list(offline))


def test_pip_months_inside_the_closure_are_dropped() -> None:
    # All six offline months are inside the closure: nothing is left to displace.
    inside = _revenue(closed_months=6, pip_displacement=_pip(*[1.0] * 6))
    plain = _revenue(closed_months=6)
    assert inside.years[0].occupancy == pytest.approx(plain.years[0].occupancy)
    assert inside.years[1].occupancy == pytest.approx(plain.years[1].occupancy)
    # Three of the six open months at half the rooms: 25% off the open months only.
    rest = _revenue(closed_months=6, pip_displacement=_pip(*[1.0] * 6, 0.5, 0.5, 0.5))
    assert rest.years[0].occupancy == pytest.approx(plain.years[0].occupancy * 0.75)


def test_no_closure_leaves_the_pip_factor_alone() -> None:
    assert _y1_revenue_factor_and_adr_drag(_pip(1.0, 1.0, 1.0)) == pytest.approx((0.75, 1.0))
    assert _y1_revenue_factor_and_adr_drag(_pip(0.8, strategy="wing_by_wing")) == pytest.approx((1 - 0.5 / 12, 0.95))
    # Wing work wholly inside the closure takes its ADR drag with it.
    assert _y1_revenue_factor_and_adr_drag(_pip(0.8, strategy="wing_by_wing"), 1) == (1.0, 1.0)


@pytest.mark.parametrize(
    "extra,flagged",
    [
        ({}, False),
        ({"renovation_displacement_schedule": [
            {"start_month": 1, "end_month": 6, "rooms_offline_pct": 1.0, "rate_discount_pct": 0.0}]}, False),
        ({"renovation_displacement_schedule": [
            {"start_month": 1, "end_month": 24, "rooms_offline_pct": 0.5, "rate_discount_pct": 0.0}]}, True),
        ({"y1_occupancy_displacement_pct": 0.1}, True),
    ],
)
def test_only_displacement_left_after_the_closure_is_flagged(extra: dict, flagged: bool) -> None:
    base = {"reopening_date": "2026-06-30", "acquisition_close_date": "2025-09-30", **extra}
    assert closed_months_for(base) == 9
    assert _displacement_on_top(base) is flagged


def test_a_flat_year_1_percent_has_nothing_to_flag_when_year_1_is_closed() -> None:
    base = {"reopening_date": "2027-03-31", "acquisition_close_date": "2025-09-30",
            "y1_occupancy_displacement_pct": 0.1}
    assert closed_months_for(base) >= 12
    assert _displacement_on_top(base) is False


def test_a_schedule_past_the_hold_or_doing_nothing_is_not_flagged() -> None:
    base = {"reopening_date": "2026-06-30", "acquisition_close_date": "2025-09-30", "hold_years": 5}
    past = {"start_month": 70, "end_month": 72, "rooms_offline_pct": 1.0, "rate_discount_pct": 0.0}
    nothing = {"start_month": 13, "end_month": 15, "rooms_offline_pct": 0.0, "rate_discount_pct": 0.0}
    assert _displacement_on_top({**base, "renovation_displacement_schedule": [past]}) is False
    assert _displacement_on_top({**base, "renovation_displacement_schedule": [nothing]}) is False


def test_the_renovation_status_says_when_the_closure_covers_the_displacement() -> None:
    base = {"reopening_date": "2026-06-30", "acquisition_close_date": "2025-09-30", "hold_years": 5, "renovation_budget": 0}
    inside = _displacement_status({**base, "renovation_displacement_schedule": [
        {"start_month": 1, "end_month": 6, "rooms_offline_pct": 1.0, "rate_discount_pct": 0.0}]}, {}, source_fields={}, analyst_override_paths=set())
    assert inside["active"] is False and inside["schedule"] == []
    assert "beyond the closure" in inside["detail"] and "Taxes, insurance" in inside["detail"]
    rest = _displacement_status({**base, "renovation_displacement_schedule": [
        {"start_month": 6, "end_month": 12, "rooms_offline_pct": 0.5, "rate_discount_pct": 0.0}]}, {}, source_fields={}, analyst_override_paths=set())
    assert rest["active"] is True and rest["schedule"][0]["start_month"] == closed_months_for(base) + 1


_CAPITAL = {"uses": [{"label": "Purchase Price", "amount": 50_000_000.0}], "total_capital": 60_000_000.0}
_NO_CAP_KEYS = {"entry_cap_rate_year1_uw", "entry_cap_rate_run_rate", "entry_cap_not_yet_open", "entry_cap_t12_out_of_date"}


def test_exports_quote_a_closed_hotels_cap_on_its_run_rate_noi() -> None:
    """FON-122 (Sam, 2026-11-05): the same normalized entry NOI an open hotel quotes —
    Year 1 priced without the closure — never the closed Year 1 (-500k here)."""
    on_run_rate = _build_investment(_CAPITAL, 100, -500_000.0, True, 4_000_000.0, "2025-06-30", 3)
    assert on_run_rate["entry_cap_rate_run_rate"] == pytest.approx(0.08)
    assert _NO_CAP_KEYS & on_run_rate.keys() == {"entry_cap_rate_run_rate"}
    # An undated current T-12, or no close date: nothing says it is stale, so the cap reads.
    undated = _build_investment(_CAPITAL, 100, -500_000.0, True, 4_000_000.0)
    assert undated["entry_cap_rate_run_rate"] == pytest.approx(0.08)
    assert t12_out_of_date(None, None) is None and t12_out_of_date("2025-03-31", None) is None


def test_exports_say_not_yet_open_only_when_no_entry_noi_can_be_built() -> None:
    closed = _build_investment(_CAPITAL, 100, -500_000.0, True)
    assert closed["entry_cap_not_yet_open"] is True
    assert _NO_CAP_KEYS & closed.keys() == {"entry_cap_not_yet_open"}
    # Closed with a run-rate NOI but no purchase price: still nothing to strike it on.
    assert _build_investment({"uses": []}, 100, -500_000.0, True, 4_000_000.0)["entry_cap_not_yet_open"] is True
    # An open hotel is untouched: Year-1 UW NOI ÷ price.
    assert _build_investment(_CAPITAL, 100, 500_000.0)["entry_cap_rate_year1_uw"] == pytest.approx(0.01)


def test_a_t12_older_than_a_year_at_the_close_prints_a_dated_flag_instead_of_a_cap() -> None:
    stale = _build_investment(_CAPITAL, 100, -500_000.0, True, 4_000_000.0, "2025-03-31", 14)
    assert stale["entry_cap_t12_out_of_date"] == "T-12 ends Mar 2025, 14 months before close"
    assert _NO_CAP_KEYS & stale.keys() == {"entry_cap_t12_out_of_date"}
    assert t12_out_of_date("2025-03-31", 13) == "T-12 ends Mar 2025, 13 months before close"
    # The flag names the month the loader has; a T-12 whose end is unreadable still says its age.
    assert t12_out_of_date("not a date", 13) == "T-12 ends, 13 months before close"


def test_a_t12_exactly_twelve_months_before_the_close_still_strikes_the_cap() -> None:
    """Boundary, decided: the rule is "MORE than twelve months". A June 2026 close on a
    T-12 to June 2025 holds the newest full year a seller can hand over at that close,
    so twelve is current and thirteen is out of date."""
    assert t12_out_of_date("2025-06-30", 12) is None
    on_time = _build_investment(_CAPITAL, 100, -500_000.0, True, 4_000_000.0, "2025-06-30", 12)
    assert on_time["entry_cap_rate_run_rate"] == pytest.approx(0.08)
    assert t12_out_of_date("2025-06-30", 13) == "T-12 ends Jun 2025, 13 months before close"
    # Whole months: a month counts once its day is reached; a month-end to a shorter
    # month's end is a full month.
    assert whole_months_between(date(2025, 6, 30), date(2026, 6, 30)) == 12
    assert whole_months_between(date(2025, 6, 30), date(2026, 7, 1)) == 12
    assert whole_months_between(date(2025, 6, 30), date(2026, 7, 30)) == 13
    assert whole_months_between(date(2025, 3, 31), date(2026, 5, 31)) == 14
    assert whole_months_between(date(2025, 3, 31), date(2026, 4, 30)) == 13
    assert whole_months_between(date(2025, 3, 31), date(2026, 5, 15)) == 13
    assert whole_months_between(date(2025, 3, 31), date(2025, 1, 31)) == -2


def test_the_expense_engine_publishes_the_open_hotels_year_1_noi_for_a_closed_year_1() -> None:
    """FON-122: the run-rate NOI the cap is struck on is the open hotel's Year 1 — the
    figure the closed Year 1 was priced from before it was brought to its months open."""
    open_hotel = _revenue()
    baseline = _expense(open_hotel)
    closed = _expense(open_hotel, _revenue(closed_months=18))
    assert closed.run_rate_noi_at_entry == pytest.approx(baseline.years[0].noi_institutional)
    assert closed.years[0].noi_institutional != pytest.approx(closed.run_rate_noi_at_entry)
    assert baseline.run_rate_noi_at_entry is None
    assert closed.t12_period_end is None and closed.t12_months_before_close is None
    dated = _expense(open_hotel, _revenue(closed_months=18), t12_period_end="2025-03-31", t12_months_before_close=14)
    assert dated.t12_period_end == "2025-03-31" and dated.t12_months_before_close == 14


def test_the_loader_dates_the_t12_and_the_runner_measures_it_against_the_close() -> None:
    from app.services.engine_runner import _t12_months_before_close, year_one_actuals_from_shims

    assert _t12_months_before_close("2025-03-31", "2026-05-31") == 14
    assert _t12_months_before_close("2025-03-31", date(2026, 3, 31)) == 12
    assert _t12_months_before_close("2025-03-31", None) is None
    assert _t12_months_before_close(None, "2026-05-31") is None
    fy = {
        "document_id": "fy", "doc_type": "PNL", "filename": "FY2024.xlsx",
        "fields": [{"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"},
                   {"field_name": "noi_usd", "value": 4_000_000.0}],
    }
    out = year_one_actuals_from_shims([fy], keys=100)
    assert out is not None and out.twelve and out.period_end == "2024-12-31"
    ytd = {**fy, "document_id": "ytd", "doc_type": "PNL_YTD", "fields": [
        {"field_name": "p_and_l_usali.period_type", "value": "ytd"},
        {"field_name": "p_and_l_usali.period_ending", "value": "2025-06-30"},
    ]}
    out = year_one_actuals_from_shims([ytd], keys=100)
    assert out is not None and not out.twelve and out.period_end == "2025-06-30"


def test_a_closed_years_adr_is_not_exported() -> None:
    years = [{"occupancy": 0.0, "adr": 264.0, "revpar": 0.0}, {"occupancy": 0.4, "adr": 270.0, "revpar": 108.0}]
    _, values = projection_values({"revenue": {"years": years}})
    assert values[proj_cell_key("revenue", "adr", 0)] is None
    assert values[proj_cell_key("revenue", "adr", 1)] == 270.0


@pytest.mark.asyncio
@pytest.mark.parametrize("deal_type", ["development", "adaptive_reuse", "acquisition"])
async def test_fixed_charges_run_on_every_deal_type(deal_type: str) -> None:
    closed = await _model(reopening_date="2027-03-31", deal_type=deal_type)
    y1 = closed["expense"].years[0]
    assert y1.fixed_charges.total > 0.0 and y1.noi == pytest.approx(-y1.fixed_charges.total)
