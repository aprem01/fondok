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
    # FON-63 — the line is named for the assumption the Debt tab owns.
    assert _line(out, "Senior Loan Origination Fee").amount == 463_740
    assert _line(out, "Senior Loan Fee") is None
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


# ─── FON-63 / FON-44 — the senior loan fee is the DEBT TAB's origination fee ───
# Sam, 2026-09-11: "Overview Sources & Uses shows Senior Loan Fee = $354,900 …
# However Debt Overview shows Origination Fee = 0.00% / $0 … If the loan fee is
# 1.50%, Debt should surface 1.50% / $354,900." The fee now has ONE owner; the
# capital engine reads the resolved tranche fee. The pin below is what stops a
# default flip from silently re-cutting Sam's reconciled Total Uses — the tests
# above all pass ``loan_costs_pct`` explicitly, so they would not catch it.


def _sam_input(**kw) -> CapitalEngineInput:
    """Sam MVP Test 2 (FON-44 / FON-67): $36.4M at 65% LTV, 10% renovation
    contingency — the deal whose Total Uses Sam reconciled at $43,658,900."""
    base = dict(
        deal_id=uuid4(), purchase_price=36_400_000, keys=132,
        closing_costs_pct=0.02, renovation_budget=5_280_000,
        renovation_contingency_pct=0.10, working_capital=500_000,
        ltv=0.65, loan_costs_pct=0.015,
    )
    base.update(kw)
    return CapitalEngineInput(**base)


def test_kimpton_su_foots_to_43_658_900_with_the_tranche_fee():
    """The byte-identity pin: surfacing the fee on Debt moves NO number.

    1.50% of the $23,660,000 senior = $354,900, and Total Uses stays exactly
    where Sam reconciled it. A default flip to 0% would drop this to
    $43,304,000 and invalidate FON-44 and FON-67.
    """
    out = CapitalEngine().run(_sam_input())
    assert out.debt_amount == pytest.approx(23_660_000)
    assert out.senior_loan_fee_usd == pytest.approx(354_900)
    assert _line(out, "Senior Loan Origination Fee").amount == pytest.approx(354_900)
    assert out.total_capital == pytest.approx(43_658_900)
    assert out.equity_amount == pytest.approx(19_998_900)
    # The S&U table foots: every line but the total sums to the total.
    line_total = sum(u.amount for u in out.uses if not u.is_total)
    assert line_total == pytest.approx(43_658_900)


def test_editing_the_debt_origination_fee_to_zero_removes_the_line():
    """A 0% fee on Debt removes the S&U line and drops Total Uses by exactly
    the prior fee — nothing else moves."""
    base = CapitalEngine().run(_sam_input())
    zero = CapitalEngine().run(_sam_input(loan_costs_pct=0.0))

    assert _line(zero, "Senior Loan Origination Fee") is None
    assert zero.senior_loan_fee_usd == 0.0
    assert zero.total_capital == pytest.approx(
        base.total_capital - base.senior_loan_fee_usd
    )
    assert zero.total_capital == pytest.approx(43_304_000)
    # The fee funds out of equity, so equity drops by the same amount and the
    # senior loan (and therefore the property uses) is untouched.
    assert zero.debt_amount == pytest.approx(base.debt_amount)
    assert zero.property_uses_usd == pytest.approx(base.property_uses_usd)
    assert zero.equity_amount == pytest.approx(
        base.equity_amount - base.senior_loan_fee_usd
    )


def test_senior_loan_fee_provenance_names_the_debt_tranche_fee():
    """The trace points at the field an analyst can actually edit, in that
    field's own 0..10 percent units — not at the platform constant."""
    out = CapitalEngine().run(_sam_input())
    trace = out.provenance["senior_loan_fee_usd"]
    pct = next(i for i in trace.inputs if i.name == "senior_origination_fee_pct")
    assert pct.assumption_key == "debt_stack.tranches.0.upfront_fee_pct"
    assert pct.value == pytest.approx(1.50)
    assert "loan_costs_pct" not in (trace.formula or "")
