"""Provenance sidecar fan-out (FON-25) — Expense (P&L), Debt, Returns.

Companion to test_revenue_provenance.py. These lock the *mechanism* on the
three engines Sam most wants explainable, not the underlying math:

  * every trace's value equals the actual output field;
  * declared inputs reconcile to the value via the stated formula;
  * every ``traces_to`` edge resolves to another key in the same map.
"""

from __future__ import annotations

import os
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.debt import DebtEngine, DebtEngineInputExt  # noqa: E402
from app.engines.expense import ExpenseEngine, ExpenseEngineInput  # noqa: E402
from app.engines.fb_revenue import FBRevenueOutput, FBRevenueYear  # noqa: E402
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402

TOL = 0.5  # dollars — these are large-magnitude values


def _assert_no_dangling(prov: dict) -> None:
    keys = set(prov)
    for trace in prov.values():
        for inp in trace.inputs:
            if inp.traces_to is not None:
                assert inp.traces_to in keys, f"dangling traces_to: {inp.traces_to}"


# ─────────────────────────── Expense / P&L ───────────────────────────


def _expense_input() -> ExpenseEngineInput:
    total = 13_600_000.0
    rooms, fb = total * 0.74, total * 0.22
    other = total - rooms - fb
    rev = FBRevenueOutput(
        deal_id=uuid4(),
        years=[
            FBRevenueYear(
                year=y,
                rooms_revenue=rooms,
                fb_revenue=fb,
                other_revenue=other,
                total_revenue=total,
            )
            for y in range(1, 6)
        ],
        fb_ratio_used=0.22,
        other_ratio_used=0.04,
    )
    return ExpenseEngineInput(
        deal_id=uuid4(),
        revenue=rev,
        hotel_type="full",
        mgmt_fee_pct=0.03,
        ffe_reserve_pct=0.04,
        expense_growth=0.035,
        grow_opex_independently=True,
        t12_actuals={},
    )


def test_expense_traces_gop_and_noi_reconcile() -> None:
    out = ExpenseEngine().run(_expense_input())
    assert out.provenance
    for i, yr in enumerate(out.years):
        gop_tr = out.provenance[f"years[{i}].gop"]
        noi_tr = out.provenance[f"years[{i}].noi"]
        assert abs(gop_tr.value - yr.gop) < TOL
        assert abs(noi_tr.value - yr.noi) < TOL
        # gop = revenue − dept − undistributed
        g = {inp.name: inp.value for inp in gop_tr.inputs}
        assert abs(
            g["total_revenue"]
            - g["departmental_expenses"]
            - g["undistributed_expenses"]
            - gop_tr.value
        ) < TOL
        # noi = gop − mgmt_fee − ffe − fixed
        n = {inp.name: inp.value for inp in noi_tr.inputs}
        assert abs(
            n["gop"]
            - n["management_fee"]
            - n["ffe_reserve"]
            - n["fixed_charges"]
            - noi_tr.value
        ) < TOL
    _assert_no_dangling(out.provenance)


def test_expense_noi_chains_to_gop() -> None:
    out = ExpenseEngine().run(_expense_input())
    for i in range(len(out.years)):
        noi_tr = out.provenance[f"years[{i}].noi"]
        gop_inp = next(inp for inp in noi_tr.inputs if inp.name == "gop")
        assert gop_inp.traces_to == f"years[{i}].gop"


# ─────────────────────────────── Debt ────────────────────────────────


def _debt_input() -> DebtEngineInputExt:
    noi = 2_500_000.0
    return DebtEngineInputExt(
        deal_id=uuid4(),
        loan_amount=25_000_000.0,
        ltv=0.65,
        interest_rate=0.068,
        term_years=5,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=[noi * (1.03**k) for k in range(5)],
    )


def test_debt_traces_debt_service_and_dscr() -> None:
    out = DebtEngine().run(_debt_input())
    assert out.provenance
    for i, yr in enumerate(out.schedule):
        ds_tr = out.provenance[f"schedule[{i}].debt_service"]
        assert abs(ds_tr.value - yr.debt_service) < TOL
        d = {inp.name: inp.value for inp in ds_tr.inputs}
        assert abs(d["interest"] + d["principal"] - ds_tr.value) < TOL
        if yr.dscr is not None:
            dscr_tr = out.provenance[f"schedule[{i}].dscr"]
            assert abs(dscr_tr.value - yr.dscr) < 1e-6
            dd = {inp.name: inp.value for inp in dscr_tr.inputs}
            assert abs(dd["noi"] / dd["debt_service"] - dscr_tr.value) < 1e-6
    _assert_no_dangling(out.provenance)


def test_debt_dscr_chains_to_debt_service() -> None:
    out = DebtEngine().run(_debt_input())
    for i, yr in enumerate(out.schedule):
        if yr.dscr is None:
            continue
        dscr_tr = out.provenance[f"schedule[{i}].dscr"]
        ds_inp = next(inp for inp in dscr_tr.inputs if inp.name == "debt_service")
        assert ds_inp.traces_to == f"schedule[{i}].debt_service"


# ────────────────────────────── Returns ──────────────────────────────


def _returns_input() -> ReturnsEngineInputExt:
    purchase = 30_000_000.0
    return ReturnsEngineInputExt(
        deal_id=uuid4(),
        assumptions=ModelAssumptions(
            purchase_price=purchase,
            ltv=0.65,
            interest_rate=0.06,
            amortization_years=30,
            loan_term_years=5,
            hold_years=5,
            exit_cap_rate=0.075,
            revpar_growth=0.03,
            expense_growth=0.03,
            selling_costs_pct=0.02,
            closing_costs_pct=0.02,
        ),
        year_one_noi=2_400_000.0,
        annual_debt_service=1_500_000.0,
        loan_amount=19_500_000.0,
        loan_balance_at_exit=18_000_000.0,
        equity=11_000_000.0,
    )


def test_returns_exit_value_chain_reconciles() -> None:
    out = ReturnsEngine().run(_returns_input())
    prov = out.provenance
    assert {"gross_sale_price", "net_proceeds", "equity_multiple"} <= set(prov)

    assert abs(prov["gross_sale_price"].value - out.gross_sale_price) < TOL
    assert abs(prov["net_proceeds"].value - out.net_proceeds) < TOL
    assert abs(prov["equity_multiple"].value - out.equity_multiple) < 1e-6

    # net_proceeds = gross_sale − selling_costs − loan_balance
    n = {inp.name: inp.value for inp in prov["net_proceeds"].inputs}
    assert abs(
        n["gross_sale_price"]
        - n["selling_costs"]
        - n["loan_balance_at_exit"]
        - prov["net_proceeds"].value
    ) < TOL
    _assert_no_dangling(prov)


def test_returns_net_proceeds_chains_to_gross_sale() -> None:
    out = ReturnsEngine().run(_returns_input())
    np_tr = out.provenance["net_proceeds"]
    gs_inp = next(inp for inp in np_tr.inputs if inp.name == "gross_sale_price")
    assert gs_inp.traces_to == "gross_sale_price"
