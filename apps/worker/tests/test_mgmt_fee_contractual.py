"""The base management fee is contractual: % × projected total revenue in
every year, Year 1 included (Sam, Kimpton Angler finding 4, 2026-11).

The engine used to carry the T-12 dollar fee into Year 1 (4.84% / 3.87% of
the displaced Year-1 revenue on the two Angler deals). The T-12 dollar is a
historical figure; the fee is a % of revenue. The only Year-1 dollar figure
that wins is one an analyst entered explicitly (with its note) — never the
extracted T-12 line.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from app.engines.fb_revenue import FBRevenueOutput, FBRevenueYear


def _revenue(y1_factor: float = 1.0) -> FBRevenueOutput:
    years = []
    for y in range(1, 6):
        f = y1_factor if y == 1 else 1.0
        rooms, fb, other = 9_000_000.0 * f * 1.04 ** (y - 1), 2_000_000.0 * f, 500_000.0 * f
        years.append(
            FBRevenueYear(
                year=y, rooms_revenue=rooms, fb_revenue=fb, other_revenue=other,
                total_revenue=rooms + fb + other,
            )
        )
    return FBRevenueOutput(deal_id=uuid4(), years=years, fb_ratio_used=0.2, other_ratio_used=0.05)


def _run(**kw):
    payload = {
        "deal_id": uuid4(),
        "revenue": _revenue(),
        "hotel_type": "full",
        "mgmt_fee_pct": 0.03,
        "grow_opex_independently": True,
        **kw,
    }
    return ExpenseEngine().run(ExpenseEngineInput(**payload))


def test_t12_dollar_fee_never_drives_year_one() -> None:
    out = _run(t12_actuals={"mgmt_fee": 619_834.0, "insurance": 400_000.0})
    for y in out.years:
        assert y.mgmt_fee == pytest.approx(0.03 * y.total_revenue)
    tr = out.provenance["years[0].noi"]
    fee_in = next(i for i in tr.inputs if i.name == "management_fee")
    assert fee_in.assumption_key == "mgmt_fee_pct"
    # The T-12 line did not ground anything, so it is not badged "from T-12".
    assert "mgmt_fee" not in out.sourced_from_t12
    assert "insurance" in out.sourced_from_t12


def test_displaced_year_one_fee_is_displaced_with_revenue() -> None:
    out = _run(revenue=_revenue(0.875), t12_actuals={"mgmt_fee": 467_917.0})
    y1 = out.years[0]
    assert y1.mgmt_fee == pytest.approx(0.03 * y1.total_revenue)


def test_analyst_year_one_fee_wins_with_its_provenance() -> None:
    out = _run(t12_actuals={"mgmt_fee": 619_834.0}, mgmt_fee_y1_override=410_000.0)
    assert out.years[0].mgmt_fee == pytest.approx(410_000.0)
    assert out.years[1].mgmt_fee == pytest.approx(0.03 * out.years[1].total_revenue)
    tr = out.provenance["years[0].noi"]
    fee_in = next(i for i in tr.inputs if i.name == "management_fee")
    assert fee_in.assumption_key == "mgmt_fee"


def test_incentive_fee_base_uses_the_contractual_fee() -> None:
    out = _run(t12_actuals={"mgmt_fee": 619_834.0}, incentive_mgmt_fee_pct_of_gop=0.1)
    y1 = out.years[0]
    assert y1.incentive_mgmt_fee == pytest.approx(0.1 * (y1.gop - 0.03 * y1.total_revenue))
