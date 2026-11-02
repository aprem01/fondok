"""The FF&E reserve is a % of total revenue in every year, Year 1 included
(review I-3, 2026-11 — the same defect class as Sam finding 4).

Year 1 used to carry the T-12 reserve dollars (3.98% / 4.21% of the displaced
Year-1 revenue on the two Angler deals) while every later year ran 4.0% ×
revenue. The reserve is contractual (HMA / lender); the T-12 dollars are
history. An analyst's explicit Year-1 reserve wins, as a fixed dollar figure.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from tests.test_mgmt_fee_contractual import _revenue


def _run(**kw):
    payload = {
        "deal_id": uuid4(),
        "revenue": _revenue(0.875),
        "hotel_type": "full",
        "ffe_reserve_pct": 0.04,
        "grow_opex_independently": True,
        **kw,
    }
    return ExpenseEngine().run(ExpenseEngineInput(**payload))


def test_t12_reserve_dollars_never_drive_year_one() -> None:
    out = _run(t12_actuals={"ffe_reserve": 480_135.0, "insurance": 300_000.0})
    for y in out.years:
        assert y.ffe_reserve == pytest.approx(0.04 * y.total_revenue)
    assert "ffe_reserve" not in out.sourced_from_t12
    tr = out.provenance["years[0].noi"]
    ffe_in = next(i for i in tr.inputs if i.name == "ffe_reserve")
    assert ffe_in.assumption_key == "ffe_reserve_pct"


def test_analyst_year_one_reserve_is_a_fixed_dollar_figure() -> None:
    out = _run(t12_actuals={"ffe_reserve": 480_135.0}, ffe_reserve_y1_override=500_000.0)
    assert out.years[0].ffe_reserve == pytest.approx(500_000.0)
    assert out.years[1].ffe_reserve == pytest.approx(0.04 * out.years[1].total_revenue)
    tr = out.provenance["years[0].noi"]
    ffe_in = next(i for i in tr.inputs if i.name == "ffe_reserve")
    assert ffe_in.assumption_key == "ffe_reserve"


def test_year_one_overrides_say_they_are_fixed_dollars() -> None:
    out = _run(mgmt_fee_y1_override=410_000.0, ffe_reserve_y1_override=500_000.0)
    for path, key in (("years[0].mgmt_fee", "mgmt_fee"), ("years[0].ffe_reserve", "ffe_reserve")):
        tr = out.provenance[path]
        assert tr.assumption_key == key
        assert "fixed" in (tr.note or "") and "displacement" in (tr.note or "")
    assert "years[1].mgmt_fee" not in out.provenance
