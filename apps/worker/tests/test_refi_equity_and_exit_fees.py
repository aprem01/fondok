"""A cash-in refinance is charged to equity; lender exit fees are paid.

Domain lead (Sam), verbatim: "Yes, please charge equity for the shortfall at
the refinance date, plus refinance costs, as a negative equity cash flow so
it flows through IRR and equity multiple."

Reference case (review probe rv_probe2): $40M senior, 6.5%, 30-year
amortization, 10-year term, 5-year hold; a Year-3 refinance sized at
50% LTV × $60M = $30,000,000 against the $38,566,897 Year-3 senior balance.

  * net refinance cash flow = proceeds − balance retired − refinance costs
  * positive → cash-out to equity (unchanged); negative → an equity
    contribution at the refinance month, through IRR, the multiple, the
    exit-year rows, Cash Flow, the Bridge, the financing grid and the
    partnership waterfall.

Lender exit fees (tranche ``exit_fee_pct``, published as ``exit_fee_usd``)
are deducted from the sale proceeds of every tranche still outstanding at the
sale; a tranche retired by a refinance pays its fee at the refinance instead.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-refi-equity-exit-fees.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.debt import (  # noqa: E402
    DebtEngine,
    DebtEngineInputExt,
    returns_debt_inputs,
)
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402
from app.engines.sensitivity import (  # noqa: E402
    SensitivityEngine,
    SensitivityInput,
    SensitivitySpec,
)
from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed  # noqa: E402

LOAN = 40_000_000.0
RATE = 0.065
NOI = [5_000_000.0] * 5
EQUITY = 22_000_000.0
BALANCE_Y3 = 38_566_897  # senior balance at the end of Year 3 (rounded)

CASH_IN = {"refi_test_year": 3, "refi_market_ltv_pct": 0.5, "refi_stabilized_value": 60e6}
CASH_OUT = {"refi_test_year": 3, "refi_market_ltv_pct": 0.75, "refi_stabilized_value": 60e6}
SENIOR_EXIT_FEE_1PCT = {"tranches": {0: {"exit_fee_pct": 1.0}}}


def _debt_input(loan: float = LOAN, rate: float = RATE, **kw: object) -> DebtEngineInputExt:
    base: dict[str, object] = dict(
        deal_id=uuid4(),
        loan_amount=loan,
        ltv=0.6,
        interest_rate=rate,
        term_years=10,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=list(NOI),
        hold_years=5,
    )
    base.update(kw)
    return DebtEngineInputExt(**base)  # type: ignore[arg-type]


def _debt(**kw: object):
    return DebtEngine().run(_debt_input(**kw))


def _returns_input(debt_out, *, loan: float = LOAN, **kw: object) -> ReturnsEngineInputExt:
    base: dict[str, object] = dict(
        deal_id=uuid4(),
        assumptions=ModelAssumptions(
            purchase_price=60_000_000.0,
            ltv=0.6,
            interest_rate=RATE,
            amortization_years=30,
            loan_term_years=10,
            hold_years=5,
            exit_cap_rate=0.075,
            revpar_growth=0.03,
            expense_growth=0.03,
            selling_costs_pct=0.02,
            closing_costs_pct=0.02,
        ),
        year_one_noi=NOI[0],
        noi_by_year=list(NOI),
        loan_amount=loan,
        equity=EQUITY,
        **returns_debt_inputs(debt_out, fallback_loan=loan),
    )
    base.update(kw)
    return ReturnsEngineInputExt(**base)  # type: ignore[arg-type]


def _rows(out):
    return {r.exit_year: r for r in out.exit_schedule if r.exit_year}


def _selected(out):
    rows = [r for r in out.exit_schedule if r.is_selected]
    assert len(rows) == 1
    return rows[0]


# ─────────────── debt engine — the net refinance cash flow ───────────────


def test_cash_in_refi_charges_the_shortfall_to_equity() -> None:
    d = _debt(debt_stack_overrides=dict(CASH_IN))
    balance = d.schedule[2].ending_balance
    assert round(balance) == BALANCE_Y3
    assert d.refi_year == 3
    assert d.refi_cash_out == 0.0
    assert d.refi_equity_contribution == pytest.approx(balance - 30_000_000.0, abs=1e-6)
    # Modelled now — a fact on the Debt tab, not a "not modelled" warning.
    assert d.refi_warning is None
    assert d.refi_equity_note == (
        "The Year 3 refinance needs $8,566,897 of equity to retire the "
        "$38,566,897 balance (new loan $30,000,000, refinance costs $0); "
        "charged to equity in Year 3."
    )


def test_cash_in_refi_contribution_includes_refinance_costs() -> None:
    d = _debt(debt_stack_overrides={**CASH_IN, "refi_fee_pct": 0.01})
    balance = d.schedule[2].ending_balance
    assert d.refi_financing_costs == pytest.approx(300_000.0)
    assert d.refi_equity_contribution == pytest.approx(
        balance + 300_000.0 - 30_000_000.0, abs=1e-6
    )
    assert d.refi_cash_out == 0.0


def test_proceeds_that_clear_the_balance_but_not_the_fee_call_the_fee() -> None:
    d0 = _debt()
    balance = d0.schedule[2].ending_balance
    proceeds = balance + 100_000.0
    d = _debt(
        debt_stack_overrides={
            "refi_test_year": 3,
            "refi_market_ltv_pct": proceeds / 60e6,
            "refi_stabilized_value": 60e6,
            "refi_fee_pct": 0.01,
        }
    )
    fee = proceeds * 0.01
    assert d.refi_cash_out == 0.0
    assert d.refi_equity_contribution == pytest.approx(fee - 100_000.0, abs=1e-6)


def test_cash_out_refi_is_unchanged_and_calls_no_equity() -> None:
    d = _debt(debt_stack_overrides=dict(CASH_OUT))
    assert d.refi_cash_out == pytest.approx(45_000_000.0 - d.schedule[2].ending_balance)
    assert d.refi_equity_contribution == 0.0
    assert d.refi_equity_note is None
    assert d.refi_warning is None


def test_sale_before_the_cash_in_refi_says_the_selected_sale_does_not_draw_it() -> None:
    d = _debt(exit_month=30, debt_stack_overrides=dict(CASH_IN))
    assert d.refi_equity_contribution > 0
    assert d.refi_warning is None
    assert d.refi_equity_note == (
        "The Year 3 refinance needs $8,566,897 of equity to retire the "
        "$38,566,897 balance (new loan $30,000,000, refinance costs $0); "
        "the month 30 sale comes first, so the selected exit does not draw it "
        "(later exit-year rows do)."
    )


def test_refi_month_override_names_the_month_in_the_note() -> None:
    d = _debt(debt_stack_overrides={**CASH_IN, "refi_month": 30})
    assert d.refi_equity_note is not None
    assert d.refi_equity_note.endswith("charged to equity at month 30.")


def test_returns_mapping_carries_the_contribution_and_the_exit_fees() -> None:
    d = _debt(debt_stack_overrides={**CASH_IN, **SENIOR_EXIT_FEE_1PCT})
    fields = returns_debt_inputs(d, fallback_loan=LOAN)
    assert fields["refi_equity_contribution"] == d.refi_equity_contribution
    assert fields["loan_exit_fee"] == pytest.approx(400_000.0)
    assert fields["refi_retired_exit_fee"] == pytest.approx(400_000.0)


# ─────────────── debt engine — lender exit fees ───────────────


def test_senior_exit_fee_is_paid_at_the_refi_as_a_refinance_cost() -> None:
    plain = _debt(debt_stack_overrides=dict(CASH_OUT))
    d = _debt(debt_stack_overrides={**CASH_OUT, **SENIOR_EXIT_FEE_1PCT})
    assert d.exit_fee_usd == pytest.approx(400_000.0)
    assert d.refi_exit_fee_usd == pytest.approx(400_000.0)
    assert d.refi_cash_out == pytest.approx(plain.refi_cash_out - 400_000.0)
    cash_in = _debt(debt_stack_overrides={**CASH_IN, **SENIOR_EXIT_FEE_1PCT})
    assert cash_in.refi_equity_contribution == pytest.approx(
        cash_in.schedule[2].ending_balance + 400_000.0 - 30_000_000.0, abs=1e-6
    )


def test_no_refi_no_refi_exit_fee() -> None:
    d = _debt(debt_stack_overrides=dict(SENIOR_EXIT_FEE_1PCT))
    assert d.refi_exit_fee_usd is None
    assert d.refi_equity_contribution == 0.0
    fields = returns_debt_inputs(d, fallback_loan=LOAN)
    assert fields["loan_exit_fee"] == pytest.approx(400_000.0)
    assert fields["refi_retired_exit_fee"] == 0.0


# ─────────────── returns engine — the contribution in IRR / EM ───────────────


def test_cash_in_refi_is_a_negative_equity_flow_at_the_refi_month() -> None:
    d = _debt(debt_stack_overrides=dict(CASH_IN))
    call = d.refi_equity_contribution
    inp = _returns_input(d)
    out = ReturnsEngine().run(inp)
    # Before: the call was never charged. Same deal with the call zeroed.
    free = ReturnsEngine().run(inp.model_copy(update={"refi_equity_contribution": 0.0}))
    assert out.levered_irr < free.levered_irr - 0.005
    # Month 36 (the Year-3 refinance) carries −call on top of its operating cash.
    op_m36 = free.levered_monthly[36]
    assert out.levered_monthly[36] == pytest.approx(op_m36 - call, abs=1e-6)
    assert out.levered_monthly[35] == pytest.approx(free.levered_monthly[35], abs=1e-9)
    # Annual display stream: Year 3 = NOI − debt service − call.
    ds = inp.debt_service_by_year
    assert out.cash_flows[3] == pytest.approx(NOI[2] - ds[2] - call, abs=1e-6)
    # EM convention: the call is additional equity invested (denominator).
    sel = _selected(out)
    op_dist = sum(NOI[i] - ds[i] for i in range(5))
    assert out.equity_multiple == pytest.approx(
        (op_dist + sel.net_proceeds) / (EQUITY + call), rel=1e-12
    )
    # The sale repays the refinanced $30M.
    assert sel.loan_payoff == 30_000_000.0 and sel.refi_included is True


def test_exit_year_rows_draw_the_call_only_after_the_refi() -> None:
    d = _debt(debt_stack_overrides=dict(CASH_IN))
    call = d.refi_equity_contribution
    inp = _returns_input(d)
    out = ReturnsEngine().run(inp)
    free = ReturnsEngine().run(inp.model_copy(update={"refi_equity_contribution": 0.0}))
    rows, free_rows = _rows(out), _rows(free)
    # Year 2: the refi has not happened — identical to a deal with no call.
    assert rows[2].levered_irr == free_rows[2].levered_irr
    assert rows[2].equity_multiple == free_rows[2].equity_multiple
    assert rows[2].refi_included is False
    # Year 4: after the refi — the call lowers the row's IRR and multiple.
    assert rows[4].refi_included is True
    assert rows[4].levered_irr < free_rows[4].levered_irr
    ds = inp.debt_service_by_year
    op4 = sum(NOI[i] - ds[i] for i in range(4))
    assert rows[4].equity_multiple == pytest.approx(
        (op4 + rows[4].net_proceeds) / (EQUITY + call), rel=1e-12
    )


def test_selected_sale_before_the_refi_draws_no_call() -> None:
    d = _debt(exit_month=30, debt_stack_overrides=dict(CASH_IN))
    inp = _returns_input(d, exit_month_override=30)
    out = ReturnsEngine().run(inp)
    sel = _selected(out)
    assert sel.refi_included is False
    assert all(cf > -1.0 for cf in out.levered_monthly[1:])
    free = ReturnsEngine().run(inp.model_copy(update={"refi_equity_contribution": 0.0}))
    assert out.levered_irr == free.levered_irr
    assert out.equity_multiple == free.equity_multiple
    assert out.cash_flows == free.cash_flows


def test_off_cycle_exit_after_the_refi_counts_the_call_as_equity_in_the_multiple() -> None:
    d = _debt(exit_month=52, debt_stack_overrides=dict(CASH_IN))
    call = d.refi_equity_contribution
    out = ReturnsEngine().run(_returns_input(d, exit_month_override=52))
    lev = out.levered_monthly
    # The refi month's distribution is its operating cash (the call is equity in).
    dist = sum(cf for cf in lev[1:] if cf > 0) + (lev[36] + call if lev[36] + call > 0 else 0.0)
    assert out.equity_multiple == pytest.approx(dist / (EQUITY + call), rel=1e-12)


def test_refi_month_override_places_the_call_at_that_month() -> None:
    d = _debt(debt_stack_overrides={**CASH_IN, "refi_month": 30})
    call = d.refi_equity_contribution
    inp = _returns_input(d, refi_time_years=2.5)
    out = ReturnsEngine().run(inp)
    free = ReturnsEngine().run(inp.model_copy(update={"refi_equity_contribution": 0.0}))
    assert out.levered_monthly[30] < free.levered_monthly[30] - call + 1.0


# ─────────────── returns engine — lender exit fees at the sale ───────────────


def test_exit_fee_is_deducted_from_every_sale_without_a_refi() -> None:
    d = _debt(debt_stack_overrides=dict(SENIOR_EXIT_FEE_1PCT))
    inp = _returns_input(d)
    out = ReturnsEngine().run(inp)
    nofee = ReturnsEngine().run(inp.model_copy(update={"loan_exit_fee": 0.0}))
    for k, row in _rows(out).items():
        assert row.loan_exit_fee == pytest.approx(400_000.0)
        assert row.net_proceeds == pytest.approx(_rows(nofee)[k].net_proceeds - 400_000.0)
    assert out.net_proceeds == pytest.approx(nofee.net_proceeds - 400_000.0)
    assert out.levered_irr < nofee.levered_irr
    assert _selected(nofee).loan_exit_fee == 0.0


def test_a_tranche_retired_by_the_refi_pays_its_fee_there_not_at_the_sale() -> None:
    d = _debt(debt_stack_overrides={**CASH_OUT, **SENIOR_EXIT_FEE_1PCT})
    out = ReturnsEngine().run(_returns_input(d))
    rows = _rows(out)
    # Before the refi the senior is still outstanding: its fee at the sale.
    assert rows[2].loan_exit_fee == pytest.approx(400_000.0)
    # After it the senior is gone (fee paid inside the refi cash flow).
    assert rows[4].loan_exit_fee == 0.0
    assert _selected(out).loan_exit_fee == 0.0


def test_headline_net_proceeds_provenance_names_the_exit_fee() -> None:
    d = _debt(debt_stack_overrides=dict(SENIOR_EXIT_FEE_1PCT))
    out = ReturnsEngine().run(_returns_input(d))
    tr = out.provenance["net_proceeds"]
    assert "loan_exit_fee" in (tr.formula or "")
    row_tr = out.provenance["exit_schedule[4].net_proceeds"]
    assert "loan_exit_fee" in (row_tr.formula or "")


# ─────────────── sensitivity financing grid ───────────────


@pytest.mark.parametrize("ovr", [CASH_IN, {**CASH_IN, **SENIOR_EXIT_FEE_1PCT}])
def test_financing_grid_base_cell_is_the_headline_on_a_cash_in_refi(ovr: dict) -> None:
    din = _debt_input(debt_stack_overrides=dict(ovr))
    d = DebtEngine().run(din)
    assert d.refi_equity_contribution > 0
    base_in = _returns_input(d)
    headline = ReturnsEngine().run(base_in).levered_irr
    spec = SensitivitySpec(
        key="g", label="g", row_variable="loan_amount", row_values=[LOAN, 35_000_000.0],
        col_variable="interest_rate", col_values=[RATE, 0.07],
        metric="levered_irr", mode="financing",
    )
    out = SensitivityEngine().run(
        SensitivityInput(
            deal_id=base_in.deal_id, base_returns_input=base_in,
            row_variable="exit_cap_rate", row_values=[0.07, 0.075],
            col_variable="revpar_growth", col_values=[0.02, 0.03],
            metric="levered_irr", specs=[spec],
            base_debt_input=din, total_capital=LOAN + EQUITY,
        )
    )
    grid = next(m for m in out.matrices if m.key == "g")
    base_cell = next(c for c in grid.cells if c.is_base)
    assert base_cell.value == pytest.approx(headline, abs=1e-12)


# ─────────────── runner-level: Cash Flow, Bridge, partnership ───────────────


async def _run_kimpton(**overrides: object) -> dict:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import get_run_scoped_outputs, run_all_engines

    await run_startup_migrations()
    seed = kimpton_seed()
    seed.update(overrides)
    tenant_id = str(uuid4())
    async with get_session_factory()() as session:
        await run_all_engines(
            session,
            deal_id=KIMPTON_FIXTURE_DEAL_ID,
            tenant_id=tenant_id,
            run_id=str(uuid4()),
            assumptions=seed,
        )
        return await get_run_scoped_outputs(
            session, deal_id=KIMPTON_FIXTURE_DEAL_ID, tenant_id=tenant_id
        )


def _line(rows: dict, label: str) -> list | None:
    for r in rows["cash_flow"]["outputs"]["levered"]:
        if r["label"] == label:
            return r["values"]
    return None


_KIMPTON_CASH_IN = {
    "refi_test_year": 3, "refi_market_ltv_pct": 0.2, "refi_stabilized_value": 60e6,
    "refi_fee_pct": 0.01,
}


@pytest.mark.asyncio
async def test_kimpton_cash_in_refi_flows_through_cash_flow_bridge_and_waterfall() -> None:
    from app.services.investment_bridge import build_investment_bridge

    rows = await _run_kimpton(term_years=10, debt_stack_overrides=dict(_KIMPTON_CASH_IN))
    debt = rows["debt"]["outputs"]
    ret = rows["returns"]["outputs"]
    call = debt["refi_equity_contribution"]
    assert debt["refi_year"] == 3 and debt["refi_cash_out"] == 0.0
    assert call == pytest.approx(
        debt["refi_existing_balance_repaid"] + debt["refi_financing_costs"]
        - debt["refi_new_loan_proceeds"],
        abs=1e-6,
    )
    assert call > 1_000_000
    # Cash Flow: its own line, and the statement reconciles to the cent.
    assert rows["cash_flow"]["status"] == "complete", rows["cash_flow"]
    contrib = _line(rows, "Refinance Equity Contribution")
    assert contrib is not None and contrib[3] == pytest.approx(-call, abs=0.01)
    assert _line(rows, "Net Refinance Cash-Out") is None
    # Returns: the Year-3 stream carries the call.
    assert ret["cash_flows"][3] < 0
    # Bridge: its own component, and it foots.
    bridge = build_investment_bridge(rows)
    assert bridge["reconciles"] is True, bridge["residual"]
    fin = next(leg for leg in bridge["legs"] if leg["key"] == "financing")
    comp = next(c for c in fin["components"] if c["label"].startswith("Refinance equity contribution"))
    assert comp["value"] == pytest.approx(-call, abs=0.01)
    # Partnership: a mid-hold capital call by the GP/LP split.
    part = rows["partnership"]["outputs"]
    lev = ret["levered_monthly"]
    refi_m = 36
    net_call = -lev[refi_m]
    assert net_call > 0
    gp_pct = part.get("gp_equity_pct") or rows["partnership"]["inputs"]["gp_equity_pct"]
    lp_pct = part.get("lp_equity_pct") or rows["partnership"]["inputs"]["lp_equity_pct"]
    assert part["gp_additional_contributions"] == pytest.approx(net_call * gp_pct, rel=1e-9)
    assert part["lp_additional_contributions"] == pytest.approx(net_call * lp_pct, rel=1e-9)


@pytest.mark.asyncio
async def test_kimpton_exit_fee_is_its_own_line_and_the_statement_reconciles() -> None:
    from app.services.investment_bridge import build_investment_bridge

    base = await _run_kimpton()
    rows = await _run_kimpton(debt_stack_overrides=dict(SENIOR_EXIT_FEE_1PCT))
    debt = rows["debt"]["outputs"]
    fee = debt["exit_fee_usd"]
    assert fee == pytest.approx(debt["loan_amount"] * 0.01)
    sel = next(r for r in rows["returns"]["outputs"]["exit_schedule"] if r["is_selected"])
    sel0 = next(r for r in base["returns"]["outputs"]["exit_schedule"] if r["is_selected"])
    assert sel["loan_exit_fee"] == pytest.approx(fee)
    assert sel["net_proceeds"] == pytest.approx(sel0["net_proceeds"] - fee, abs=1e-6)
    assert rows["cash_flow"]["status"] == "complete", rows["cash_flow"]
    hold = rows["returns"]["outputs"]["hold_years"]
    line = _line(rows, "Lender Exit Fees")
    assert line is not None and line[hold] == pytest.approx(-fee, abs=0.01)
    # No fee → no line (the Kimpton golden statement is unchanged).
    assert _line(base, "Lender Exit Fees") is None
    bridge = build_investment_bridge(rows)
    assert bridge["reconciles"] is True, bridge["residual"]
    fin = next(leg for leg in bridge["legs"] if leg["key"] == "financing")
    comp = next(c for c in fin["components"] if c["label"] == "Lender exit fees at sale")
    assert comp["value"] == pytest.approx(-fee, abs=0.01)
