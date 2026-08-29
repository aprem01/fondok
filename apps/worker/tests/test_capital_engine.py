"""Capital engine — renovation split (FON-71) and FON-67 reconciliation."""

from __future__ import annotations

from uuid import uuid4

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
