"""Cash Flow Statement engine — NOI-override reconciliation (live-bug regression).

A deal can pin its operating NOI to a source-model schedule via
``noi_override_by_year``. The engine_runner forwards that override to the
returns engine (as ``noi_by_year`` → ``noi_series``), so the returns engine's
canonical ``cash_flows_unlevered`` is built from the OVERRIDE NOI — which
differs from the expense engine's own ``years[].noi``.

The cash_flow view used to compose its NOI row from ``expense.years[y-1].noi``.
On any override deal that row could not tie out to the returns engine, so the
reconciliation guard raised and the whole cash_flow model failed ("The
cash_flow model didn't finish"; Cash Flow tab rendered "—" everywhere).

The fix: the returns engine now publishes the exact NOI series it used
(``ReturnsEngineOutputExt.noi_by_year``), and the cash_flow view composes its
NOI row from that when present — falling back to expense NOI (byte-identical to
the prior behavior) when it is not.

These are pure unit tests over ``CashFlowStatementEngine.run`` — no DB, no LLM.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.cash_flow import CashFlowStatementEngine, CashFlowStatementInput
from app.engines.capital import CapitalEngineOutput
from app.engines.debt import DebtEngineOutputExt
from app.engines.expense import ExpenseEngineOutput, ExpenseYear
from app.engines.partnership import PartnershipOutputExt
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt
from fondok_schemas.financial import (
    DepartmentalExpenses,
    FixedCharges,
    ModelAssumptions,
    UndistributedExpenses,
)
from fondok_schemas.partnership import PartnerReturn
from fondok_schemas.underwriting import SourceUseLine

_CENT = 0.01
_HOLD = 3
_EQUITY = 30_000_000.0  # all-equity deal (loan = 0) → levered == unlevered
_FFE = [180_000.0, 185_000.0, 190_000.0]

# The live-deal shape: the returns engine runs on the OVERRIDE NOI, which is
# materially larger than the expense engine's own NOI (period 1 ≈ 4.05M override
# vs ≈ 1.63M expense — the exact pattern that tripped the guard on Kimpton).
_OVERRIDE_NOI = [4_052_182.84, 4_100_000.0, 4_200_000.0]
_EXPENSE_NOI = [1_631_849.69, 1_650_000.0, 1_700_000.0]


def _assumptions() -> ModelAssumptions:
    return ModelAssumptions(
        purchase_price=_EQUITY,
        ltv=0.0,
        interest_rate=0.0,
        amortization_years=30,
        loan_term_years=5,
        hold_years=_HOLD,
        exit_cap_rate=0.07,
        revpar_growth=0.03,
        expense_growth=0.03,
        selling_costs_pct=0.03,
        transfer_tax_pct=0.0,
        closing_costs_pct=0.0,
    )


def _run_returns(noi_series: list[float]):
    """Run the real returns engine on ``noi_series`` (the override path)."""
    return ReturnsEngine().run(
        ReturnsEngineInputExt(
            deal_id=uuid4(),
            assumptions=_assumptions(),
            year_one_noi=noi_series[0],
            noi_by_year=list(noi_series),
            annual_debt_service=0.0,
            loan_amount=0.0,
            loan_balance_at_exit=0.0,
            equity=_EQUITY,
        )
    )


def _capital(deal_id) -> CapitalEngineOutput:
    return CapitalEngineOutput(
        deal_id=deal_id,
        total_capital=_EQUITY,
        price_per_key=1.0,
        sources=[SourceUseLine(label="Equity", amount=_EQUITY, is_total=False)],
        uses=[SourceUseLine(label="Total Uses", amount=_EQUITY, is_total=True)],
        debt_amount=0.0,
        equity_amount=_EQUITY,
        ltc=0.0,
    )


def _expense(deal_id, noi: list[float]) -> ExpenseEngineOutput:
    years = [
        ExpenseYear(
            year=y + 1,
            total_revenue=noi[y] + _FFE[y],
            dept_expenses=DepartmentalExpenses(),
            undistributed=UndistributedExpenses(),
            mgmt_fee=0.0,
            ffe_reserve=_FFE[y],
            fixed_charges=FixedCharges(),
            gop=noi[y] + _FFE[y],
            noi=noi[y],
            noi_institutional=noi[y] + _FFE[y],
        )
        for y in range(_HOLD)
    ]
    return ExpenseEngineOutput(deal_id=deal_id, years=years, noi_cagr=0.0)


def _debt(deal_id) -> DebtEngineOutputExt:
    # No debt: schedule empty, nothing to service, nothing to pay off.
    return DebtEngineOutputExt(
        deal_id=deal_id,
        annual_debt_service=0.0,
        schedule=[],
        debt_service_by_year=[],
        refi_cash_out=0.0,
        balance_at_exit=0.0,
    )


def _partnership(deal_id) -> PartnershipOutputExt:
    leg = lambda p: PartnerReturn(  # noqa: E731
        partner=p, contributed_equity=_EQUITY / 2, distributions=_EQUITY, irr=0.1,
        equity_multiple=1.5,
    )
    return PartnershipOutputExt(
        deal_id=deal_id,
        gp=leg("GP"),
        lp=leg("LP"),
        gp_cash_flows=[0.0] * (_HOLD + 1),
        lp_cash_flows=[0.0] * (_HOLD + 1),
        promote_amount=0.0,
    )


def _build_input(*, returns, expense_noi: list[float]) -> CashFlowStatementInput:
    deal_id = uuid4()
    return CashFlowStatementInput(
        deal_id=deal_id,
        capital=_capital(deal_id),
        expense=_expense(deal_id, expense_noi),
        debt=_debt(deal_id),
        returns=returns,
        partnership=_partnership(deal_id),
    )


def _noi_row(out):
    return next(r for r in out.unlevered if r.label == "Net Operating Income")


# ───────────────────────── (a) override path reconciles ─────────────────────


def test_override_noi_reconciles_and_row_reflects_override() -> None:
    # Returns ran on the override NOI; expense NOI differs (the live-deal shape).
    ret = _run_returns(_OVERRIDE_NOI)
    assert ret.noi_by_year == pytest.approx(_OVERRIDE_NOI, abs=_CENT), (
        "returns engine must publish the exact override NOI series it used"
    )
    payload = _build_input(returns=ret, expense_noi=_EXPENSE_NOI)

    # Before the fix this raised inside _reconcile; now it composes cleanly.
    out = CashFlowStatementEngine().run(payload)

    unlev_canon = [float(x) for x in ret.cash_flows_unlevered]
    # Served bottom line IS the canonical returns series …
    assert out.unlevered_cash_flow == pytest.approx(unlev_canon, abs=_CENT)
    # … and the composed component rows sum back to it to the cent.
    n = len(unlev_canon)
    composed = [0.0] * n
    for line in out.unlevered:
        if line.kind != "linked":
            continue
        for i, v in enumerate(line.values):
            if v is not None and i < n:
                composed[i] += float(v)
    assert composed == pytest.approx(unlev_canon, abs=_CENT)

    # The NOI row reflects the OVERRIDE (before FF&E reserve), NOT expense NOI.
    noi_row = _noi_row(out)
    for y in range(1, _HOLD + 1):
        assert noi_row.values[y] == pytest.approx(
            _OVERRIDE_NOI[y - 1] + _FFE[y - 1], abs=_CENT
        )
        # Sanity: it is genuinely the override, not the expense figure.
        assert abs(noi_row.values[y] - (_EXPENSE_NOI[y - 1] + _FFE[y - 1])) > 1.0


def test_pre_fix_behavior_would_have_raised() -> None:
    # Prove the fix is load-bearing: with the returns series cleared (the
    # pre-fix world where cash_flow composed from expense NOI), the very same
    # override deal fails the reconciliation guard.
    ret = _run_returns(_OVERRIDE_NOI)
    ret_no_series = ret.model_copy(update={"noi_by_year": []})
    payload = _build_input(returns=ret_no_series, expense_noi=_EXPENSE_NOI)
    with pytest.raises(ValueError, match="reconciliation failed"):
        CashFlowStatementEngine().run(payload)


# ─────────────────── (b) empty series → fallback unchanged ───────────────────


def test_empty_returns_series_falls_back_to_expense_noi() -> None:
    # When the analyst sets no override, the returns engine ran on the expense
    # NOI, so its canonical stream ties to expense NOI. Simulate a returns
    # output that did not populate noi_by_year (empty) → cash_flow must fall
    # back to expense NOI and still reconcile.
    ret = _run_returns(_EXPENSE_NOI)
    ret_no_series = ret.model_copy(update={"noi_by_year": []})
    payload = _build_input(returns=ret_no_series, expense_noi=_EXPENSE_NOI)

    out = CashFlowStatementEngine().run(payload)

    unlev_canon = [float(x) for x in ret.cash_flows_unlevered]
    assert out.unlevered_cash_flow == pytest.approx(unlev_canon, abs=_CENT)

    # NOI row uses the expense NOI (fallback path), before FF&E reserve.
    noi_row = _noi_row(out)
    for y in range(1, _HOLD + 1):
        assert noi_row.values[y] == pytest.approx(
            _EXPENSE_NOI[y - 1] + _FFE[y - 1], abs=_CENT
        )


def test_populated_series_equal_to_expense_is_byte_identical_to_fallback() -> None:
    # On a non-override deal the returns series equals the expense NOI, so the
    # new (populated) path and the old (fallback) path must produce an
    # identical NOI row — the guarantee that non-override deals are unchanged.
    ret = _run_returns(_EXPENSE_NOI)

    populated = CashFlowStatementEngine().run(
        _build_input(returns=ret, expense_noi=_EXPENSE_NOI)
    )
    fallback = CashFlowStatementEngine().run(
        _build_input(
            returns=ret.model_copy(update={"noi_by_year": []}),
            expense_noi=_EXPENSE_NOI,
        )
    )
    assert _noi_row(populated).values == _noi_row(fallback).values
    assert populated.unlevered_cash_flow == fallback.unlevered_cash_flow
    assert populated.levered_cash_flow == fallback.levered_cash_flow
