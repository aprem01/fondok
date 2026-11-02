"""The management agreement's fee in lieu is its own management-expense line
(review R3-I-1, 2026-11).

The Angler HMA reads "Base Fee: 3.25% of Hotel Gross Revenues and $76,000 in
Lieu Fee". The $76,000 was dropped silently. It is now a sourced line every
year, deducted after GOP with the base fee; flat unless the agreement states
an escalation, and the trace says which.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.expense import ExpenseEngine, ExpenseEngineInput
from tests.test_mgmt_fee_contractual import _revenue


def _run(**kw):
    payload = {
        "deal_id": uuid4(),
        "revenue": _revenue(),
        "hotel_type": "full",
        "mgmt_fee_pct": 0.0325,
        "grow_opex_independently": True,
        **kw,
    }
    return ExpenseEngine().run(ExpenseEngineInput(**payload))


def test_no_fee_in_lieu_leaves_every_figure_unchanged() -> None:
    base = _run()
    for y in base.years:
        assert y.mgmt_fee_in_lieu is None
        assert y.noi == pytest.approx(y.gop - y.mgmt_fee - y.ffe_reserve - y.fixed_charges.total)
    assert "years[0].mgmt_fee_in_lieu" not in base.provenance


def test_flat_fee_in_lieu_is_deducted_every_year_and_says_flat() -> None:
    base = _run()
    out = _run(mgmt_fee_in_lieu_usd=76_000.0)
    for b, y in zip(base.years, out.years):
        assert y.mgmt_fee_in_lieu == pytest.approx(76_000.0)
        assert y.noi == pytest.approx(b.noi - 76_000.0)
        assert y.noi_institutional == pytest.approx(b.noi_institutional - 76_000.0)
    tr = out.provenance["years[3].mgmt_fee_in_lieu"]
    assert tr.assumption_key == "mgmt_fee_in_lieu_usd"
    assert "flat" in (tr.note or "").lower()
    names = [i.name for i in out.provenance["years[0].noi"].inputs]
    assert "management_fee_in_lieu" in names


def test_stated_escalation_grows_the_fee_in_lieu() -> None:
    out = _run(mgmt_fee_in_lieu_usd=76_000.0, mgmt_fee_in_lieu_escalation=0.03)
    for i, y in enumerate(out.years):
        assert y.mgmt_fee_in_lieu == pytest.approx(76_000.0 * 1.03**i)
    assert "3.00%" in (out.provenance["years[2].mgmt_fee_in_lieu"].note or "")


def test_incentive_base_is_after_the_whole_base_fee() -> None:
    out = _run(mgmt_fee_in_lieu_usd=76_000.0, incentive_mgmt_fee_pct_of_gop=0.1)
    y = out.years[0]
    assert y.incentive_mgmt_fee == pytest.approx(0.1 * (y.gop - y.mgmt_fee - 76_000.0))


def test_noi_trace_names_both_fees_when_both_are_set() -> None:
    out = _run(mgmt_fee_in_lieu_usd=76_000.0, incentive_mgmt_fee_pct_of_gop=0.1)
    tr = out.provenance["years[0].noi"]
    assert tr.formula == (
        "noi = gop − management_fee − management_fee_in_lieu − "
        "incentive_management_fee − ffe_reserve − fixed_charges"
    )
    assert [i.name for i in tr.inputs][:4] == [
        "gop", "management_fee", "management_fee_in_lieu", "incentive_management_fee",
    ]


def test_exported_proforma_carries_the_fee_in_lieu_row_only_when_stated() -> None:
    from app.export.live_payload import _build_proforma_and_cf

    def years(in_lieu):
        rev = {"years": [{"year": 1, "rooms_revenue": 7e6, "fb_revenue": 2e6, "other_revenue": 1e6,
                          "resort_fees": 0.0, "total_revenue": 1e7}]}
        e = {"year": 1, "total_revenue": 1e7, "dept_expenses": {"total": 3e6},
             "undistributed": {"total": 2e6}, "fixed_charges": {"total": 8e5},
             "mgmt_fee": 3.25e5, "ffe_reserve": 4e5, "gop": 5e6,
             "noi": 5e6 - 3.25e5 - (in_lieu or 0) - 8e5 - 4e5,
             "noi_institutional": 5e6 - 3.25e5 - (in_lieu or 0) - 8e5}
        if in_lieu is not None:
            e["mgmt_fee_in_lieu"] = in_lieu
        return rev, {"years": [e]}

    rev, exp = years(None)
    labels = [r["label"] for r in _build_proforma_and_cf(rev, exp, {"annual_debt_service": 1e6}, horizon=1)[0]["lines"]]
    assert "Management Fee in Lieu" not in labels
    rev, exp = years(76_000.0)
    lines = _build_proforma_and_cf(rev, exp, {"annual_debt_service": 1e6}, horizon=1)[0]["lines"]
    labels = [r["label"] for r in lines]
    assert labels.index("Management Fee in Lieu") == labels.index("Management Fee") + 1
    assert next(r for r in lines if r["label"] == "Management Fee in Lieu")["y1"] == 76


def test_workbook_path_is_optional() -> None:
    from app.export.pl_workbooks import OPTIONAL_PROJ_PATHS, PROJ_LINES

    assert "expense.mgmt_fee_in_lieu" in OPTIONAL_PROJ_PATHS
    assert ("expense", "mgmt_fee_in_lieu", "Management Fee in Lieu") in PROJ_LINES
