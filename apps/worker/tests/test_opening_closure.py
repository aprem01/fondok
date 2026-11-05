"""Sam, 2026-11-04 — a new or reopened hotel earns nothing before it opens. Revenue
starts at the opening date, the opening year is prorated by months open, the ramp's
Year 1 is the first 12 months after opening, and the closed months carry no operating
costs (pre-opening costs are the analyst's input).
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from uuid import uuid4

import pytest
from fondok_schemas.underwriting import RenovationDisplacementPeriod, RevenueEngineInput

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from app.engines.fb_revenue import FBRevenueEngine, FBRevenueInput
from app.engines.revenue import RevenueEngine, default_ramp_by_year, months_open_shares
from app.services.topline import closed_months_for, opening_offset_for


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


def _expense(as_open, actual=None):
    def fb(out):
        return FBRevenueEngine().run(FBRevenueInput(deal_id=uuid4(), revenue=out))

    return ExpenseEngine().run(ExpenseEngineInput(
        deal_id=uuid4(), revenue=fb(as_open), mgmt_fee_pct=0.03,
        ffe_reserve_pct=0.04, expense_growth=0.03, grow_opex_independently=True,
        open_shares_by_year=months_open_shares(18, 5) if actual is not None else {},
        actual_revenue=fb(actual) if actual is not None else None,
    ))


def test_the_closed_months_carry_no_costs_and_later_years_keep_theirs() -> None:
    open_hotel = _revenue()
    baseline = _expense(open_hotel)
    closed = _expense(open_hotel, _revenue(closed_months=18))
    assert closed.years[0].noi == pytest.approx(0.0) and closed.years[0].undistributed.total == 0.0
    assert closed.years[0].fixed_charges.total == 0.0
    assert closed.years[1].noi == pytest.approx(baseline.years[1].noi * 0.5)
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
    assert closed["expense"].years[0].noi == 0.0
    assert closed["expense"].years[1].noi == pytest.approx(open_hotel["expense"].years[1].noi * 0.5)
    assert closed["returns"].noi_by_year[0] == 0.0
    assert closed["returns"].levered_irr < open_hotel["returns"].levered_irr
    # Opening in the sale year: the buyer still caps a full year.
    last = await _model(reopening_date="2030-03-31")
    assert last["expense"].years[-1].noi == pytest.approx(open_hotel["expense"].years[-1].noi * 0.5)
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
    assert sizing[0] == pytest.approx(0.0)  # fully closed: nothing to lend on
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
