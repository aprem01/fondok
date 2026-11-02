"""F&B revenue per occupied room inflates with the deal's ADR growth (Sam,
Kimpton Angler finding 2b, 2026-11).

The F&B $ per occupied room used to be held flat for the whole hold, so F&B
revenue grew only with occupancy (~0.8%/yr) while rooms ADR grew 4%/yr. The
documented driver is the deal's ADR growth path — the scalar ``adr_growth``,
or the per-year ``adr_growth_by_year`` list (a year with no growth figure is
held flat, never seeded). Year 1 is the stated figure, and a renovation
displaces occupied rooms, never the F&B price.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.revenue import RevenueEngine
from fondok_schemas.underwriting import RevenueEngineInput


def _input(**kw) -> RevenueEngineInput:
    base = {
        "deal_id": uuid4(),
        "keys": 120,
        "starting_occupancy": 0.72,
        "starting_adr": 210.0,
        "occupancy_growth": 0.01,
        "adr_growth": 0.04,
        "fb_revenue_per_occupied_room": 50.0,
        "other_revenue_pct_of_rooms": 0.05,
        "hold_years": 5,
    }
    base.update(kw)
    return RevenueEngineInput(**base)


def _occupied(y) -> float:
    return 120 * 365 * y.occupancy


def test_fb_per_occupied_room_inflates_at_adr_growth() -> None:
    out = RevenueEngine().run(_input())
    for i, y in enumerate(out.years):
        assert y.fb_revenue == pytest.approx(_occupied(y) * 50.0 * 1.04**i)
    # Year 1 is exactly the stated figure.
    assert out.years[0].fb_revenue == _occupied(out.years[0]) * 50.0


def test_fb_follows_the_per_year_adr_growth_list() -> None:
    out = RevenueEngine().run(_input(adr_growth_by_year=[None, 0.05, None, 0.02, 0.03]))
    index = [1.0, 1.05, 1.05, 1.05 * 1.02, 1.05 * 1.02 * 1.03]
    for y, ix in zip(out.years, index):
        assert y.fb_revenue == pytest.approx(_occupied(y) * 50.0 * ix)


def test_displacement_moves_occupied_rooms_not_the_fb_price() -> None:
    out = RevenueEngine().run(_input(y1_occupancy_displacement_pct=0.125))
    y1 = out.years[0]
    assert y1.occupancy == pytest.approx(0.72 * 0.875)
    assert y1.fb_revenue == pytest.approx(_occupied(y1) * 50.0)
    y2 = out.years[1]
    assert y2.fb_revenue == pytest.approx(_occupied(y2) * 50.0 * 1.04)


def test_fb_trace_names_the_adr_growth() -> None:
    out = RevenueEngine().run(_input())
    tr = out.provenance["years[2].fb_revenue"]
    assert tr.value == pytest.approx(out.years[2].fb_revenue)
    assert "adr_growth" in (tr.formula or "")
    names = {i.name for i in tr.inputs}
    assert {"occupied_rooms", "fb_revenue_per_occupied_room", "fb_price_index"} <= names
    idx = next(i for i in tr.inputs if i.name == "fb_price_index")
    assert idx.value == pytest.approx(1.04**2)
