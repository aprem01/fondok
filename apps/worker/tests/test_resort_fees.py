"""End-to-end test that Resort Fees flow as a distinct revenue line.

Pins Sam QA #11: Resort Fees must NOT be folded into Misc / Other Income
anywhere in the pipeline. The schema, normalizer, revenue engine, FB
engine, and engine_runner all need to keep them on their own line.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.fb_revenue import FBRevenueEngine, FBRevenueInput
from app.engines.revenue import RevenueEngine
from fondok_schemas.financial import USALIFinancials
from fondok_schemas.underwriting import RevenueEngineInput


def test_revenue_engine_emits_resort_fees_per_year() -> None:
    """When ``starting_resort_fees`` is supplied, the revenue engine
    emits ``resort_fees`` on every projection year and includes them in
    ``total_revenue``."""
    out = RevenueEngine().run(
        RevenueEngineInput(
            deal_id=uuid4(),
            keys=200,
            starting_occupancy=0.75,
            starting_adr=300.0,
            occupancy_growth=0.0,
            adr_growth=0.04,
            fb_revenue_per_occupied_room=80.0,
            other_revenue_pct_of_rooms=0.06,
            starting_resort_fees=900_000.0,
            resort_fees_growth=0.04,
            hold_years=5,
        )
    )
    assert out.years[0].resort_fees == pytest.approx(900_000.0)
    # Out-years grow at resort_fees_growth.
    assert out.years[1].resort_fees == pytest.approx(900_000.0 * 1.04)
    assert out.years[4].resort_fees == pytest.approx(900_000.0 * (1.04 ** 4))
    # Resort fees are part of total_revenue, not double-counted in other.
    y1 = out.years[0]
    expected_total = (
        y1.rooms_revenue + y1.fb_revenue + y1.resort_fees + y1.other_revenue
    )
    assert y1.total_revenue == pytest.approx(expected_total)


def test_revenue_engine_zero_resort_fees_when_unsupplied() -> None:
    """Default behavior — no resort_fees anchor → 0 on every year."""
    out = RevenueEngine().run(
        RevenueEngineInput(
            deal_id=uuid4(),
            keys=132,
            starting_occupancy=0.76,
            starting_adr=385.0,
            adr_growth=0.04,
            fb_revenue_per_occupied_room=88.0,
            other_revenue_pct_of_rooms=0.065,
            hold_years=5,
        )
    )
    assert all(y.resort_fees == 0.0 for y in out.years)


def test_fb_engine_passes_resort_fees_through_unchanged() -> None:
    """FB engine doesn't synthesize resort_fees from a ratio — it passes
    through whatever the upstream revenue engine produced. Verifies the
    Operating Statement renders the same number the revenue engine
    computed, year by year.
    """
    rev_out = RevenueEngine().run(
        RevenueEngineInput(
            deal_id=uuid4(),
            keys=200,
            starting_occupancy=0.78,
            starting_adr=300.0,
            adr_growth=0.04,
            fb_revenue_per_occupied_room=80.0,
            other_revenue_pct_of_rooms=0.06,
            starting_resort_fees=600_000.0,
            resort_fees_growth=0.05,
            hold_years=5,
        )
    )
    fb_out = FBRevenueEngine().run(
        FBRevenueInput(deal_id=uuid4(), revenue=rev_out, hotel_type="full")
    )
    for rev_y, fb_y in zip(rev_out.years, fb_out.years, strict=True):
        assert fb_y.resort_fees == pytest.approx(rev_y.resort_fees)
        # And total_revenue includes resort_fees.
        assert fb_y.total_revenue == pytest.approx(
            fb_y.rooms_revenue + fb_y.fb_revenue + fb_y.resort_fees + fb_y.other_revenue
        )


def test_usali_financials_accepts_resort_fees_field() -> None:
    """Schema must accept resort_fees independent of other_revenue."""
    spread = USALIFinancials(
        period_label="TTM 2026-Q1",
        rooms_revenue=15_000_000.0,
        fb_revenue=4_000_000.0,
        resort_fees=900_000.0,
        other_revenue=600_000.0,
        total_revenue=20_500_000.0,
        gop=8_000_000.0,
        noi=6_000_000.0,
        opex_ratio=0.71,
    )
    assert spread.resort_fees == 900_000.0
    assert spread.other_revenue == 600_000.0
    # Sanity: total = sum of components.
    assert spread.total_revenue == pytest.approx(
        spread.rooms_revenue + spread.fb_revenue + spread.resort_fees + spread.other_revenue
    )
