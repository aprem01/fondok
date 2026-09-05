"""Capital engine — renovation split (FON-71) and FON-67 reconciliation."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.capital import CapitalEngine, CapitalEngineInput


def _line(out, label):
    return next((u for u in out.uses if u.label == label), None)


def test_explicit_senior_loan_overrides_ltv():
    e = CapitalEngine()
    # LTV would give 0.65 * 36,436,802 = 23,683,921; explicit senior wins.
    out = e.run(
        CapitalEngineInput(
            deal_id=uuid4(), purchase_price=36_436_802, keys=132,
            senior_loan_amount=23_187_000,
        )
    )
    assert out.debt_amount == 23_187_000


def test_ltv_sizing_when_no_explicit_senior():
    e = CapitalEngine()
    out = e.run(
        CapitalEngineInput(
            deal_id=uuid4(), purchase_price=36_436_802, keys=132, ltv=0.65,
        )
    )
    assert round(out.debt_amount) == round(0.65 * 36_436_802)


def test_kimpton_source_reconciliation():
    """Reproduce Sam's Kimpton capital uses exactly (FON-67)."""
    e = CapitalEngine()
    out = e.run(
        CapitalEngineInput(
            deal_id=uuid4(), purchase_price=36_436_802, keys=132,
            closing_costs=728_736, renovation_budget=4_943_400,
            working_capital=284_041, insurance_reserve=299_057,
            senior_loan_amount=23_187_000, loan_costs_pct=0.02,
        )
    )
    # Property uses = purchase + closing + reno + WC + insurance (Sam: $42.692M).
    assert round(out.property_uses_usd) == 42_692_036
    # Senior fee = 2% of the senior loan (Sam: $463,740).
    assert round(out.senior_loan_fee_usd) == 463_740
    # Total capitalization = property uses + financing fee.
    assert round(out.total_capital) == 43_155_776
    # Financing cost is a separate line, not folded into the property uses.
    assert _line(out, "Insurance Reserve").amount == 299_057
    assert _line(out, "Senior Loan Fee").amount == 463_740
    assert _line(out, "Loan Costs") is None


def test_financing_fee_separation_is_irr_neutral():
    """Moving the fee out of property uses leaves equity unchanged: equity
    still funds the property gap plus the fee."""
    e = CapitalEngine()
    out = e.run(
        CapitalEngineInput(
            deal_id=uuid4(), purchase_price=36_436_802, keys=132,
            closing_costs=728_736, renovation_budget=4_943_400,
            working_capital=284_041, insurance_reserve=299_057,
            senior_loan_amount=23_187_000, loan_costs_pct=0.02,
        )
    )
    # equity = total_uses - debt = (property + fee) - senior
    assert round(out.equity_amount) == round(
        out.property_uses_usd + out.senior_loan_fee_usd - out.debt_amount
    )


def test_insurance_reserve_omitted_when_zero():
    e = CapitalEngine()
    out = e.run(
        CapitalEngineInput(deal_id=uuid4(), purchase_price=30_000_000, keys=100)
    )
    assert _line(out, "Insurance Reserve") is None
    assert out.property_uses_usd > 0


# ─────────── FON-71 follow-up: renovation contingency fold-in ──────────


def _reno_input(**kw) -> CapitalEngineInput:
    return CapitalEngineInput(
        deal_id=uuid4(), purchase_price=36_436_802, keys=132,
        closing_costs=728_736, renovation_budget=4_943_400,
        working_capital=284_041, insurance_reserve=299_057,
        senior_loan_amount=23_187_000, loan_costs_pct=0.02,
        **kw,
    )


def test_no_contingency_is_byte_identical():
    """Absent the contingency pct, every existing figure is byte-identical and
    the new fields report the base with a zero contingency."""
    e = CapitalEngine()
    base = e.run(_reno_input())
    withpct0 = e.run(_reno_input(renovation_contingency_pct=0.0))

    assert withpct0.total_capital == base.total_capital
    assert withpct0.property_uses_usd == base.property_uses_usd
    assert withpct0.equity_amount == base.equity_amount
    assert withpct0.ltc == base.ltc
    assert _line(withpct0, "Renovation").amount == _line(base, "Renovation").amount
    # Renovation use line still equals the base budget.
    assert _line(base, "Renovation").amount == 4_943_400
    assert base.renovation_contingency_usd == 0.0
    assert base.renovation_base_usd == 4_943_400
    assert base.renovation_total_usd == 4_943_400


def test_contingency_folds_into_reno_total_and_su_foots():
    """A 10% contingency (on hard costs) folds into the renovation total; the
    total cost rises by exactly the contingency and Sources & Uses still foots."""
    e = CapitalEngine()
    base = e.run(_reno_input())
    out = e.run(_reno_input(renovation_contingency_pct=0.10))

    hard = 4_943_400 * 0.75           # renovation_hard_pct default
    contingency = 0.10 * hard         # = 370,755
    # Reno total = base + contingency; the "Renovation" use line carries it.
    assert out.renovation_contingency_usd == pytest.approx(contingency)
    assert out.renovation_base_usd == pytest.approx(4_943_400)
    assert out.renovation_total_usd == pytest.approx(4_943_400 + contingency)
    assert _line(out, "Renovation").amount == pytest.approx(4_943_400 + contingency)

    # Total cost / equity rise by exactly the contingency (debt is the explicit
    # senior, unchanged); property uses rise by the contingency too.
    assert out.total_capital == pytest.approx(base.total_capital + contingency)
    assert out.property_uses_usd == pytest.approx(base.property_uses_usd + contingency)
    assert out.equity_amount == pytest.approx(base.equity_amount + contingency)
    assert out.debt_amount == pytest.approx(base.debt_amount)  # explicit senior

    # Sources & Uses still foots: total uses == total sources == total_capital.
    uses_total = _line(out, "Total Uses").amount
    sources_total = next(s.amount for s in out.sources if s.is_total)
    assert uses_total == pytest.approx(out.total_capital)
    assert sources_total == pytest.approx(out.total_capital)
    assert uses_total == pytest.approx(sources_total)
    # LTC recomputes on the larger basis.
    assert out.ltc == pytest.approx(out.debt_amount / out.total_capital)


def test_contingency_leaves_hard_soft_fees_breakdown_on_base():
    """The hard/soft/fees split stays computed on the base budget (contingency
    sits on top, not inside the split)."""
    e = CapitalEngine()
    out = e.run(_reno_input(renovation_contingency_pct=0.10))
    assert out.renovation_breakdown is not None
    assert out.renovation_breakdown.hard == pytest.approx(4_943_400 * 0.75)
    assert out.renovation_breakdown.soft == pytest.approx(4_943_400 * 0.15)
    assert out.renovation_breakdown.fees == pytest.approx(4_943_400 * 0.10)
