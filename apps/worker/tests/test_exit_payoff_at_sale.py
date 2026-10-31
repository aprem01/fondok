"""The exit loan payoff is the balance at the SALE month, not at loan maturity.

Defect (found in review): the debt engine built its schedule over the loan
term and published ``balance_at_exit = schedule[-1].ending_balance`` — the
balance at MATURITY — and the headline (selected) exit paid off that scalar.
A 10-year loan on a 5-year hold therefore understated the payoff by ~$3.5M on
a $40M loan and overstated the IRR. The interim exit-year rows already priced
their payoff at their own month; these tests pin the headline to the same rule
and every consumer of the payoff (debt scalar, sensitivity financing grid,
Cash Flow statement, Investment Bridge) to the figure the returns engine used.

Reference loan: $40M, 6.5%, 30-year amortization, no IO, 5-year hold.
  * month-60 balance           = 37,444,391
  * 10-year maturity balance   = 33,910,451  (what the headline used to pay)
  * 3-year maturity balance    = 38,566,897  (loan matures before the sale)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-exit-payoff-at-sale.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.debt import DebtEngine, DebtEngineInputExt  # noqa: E402
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


def _debt_input(term: int, loan: float = LOAN, rate: float = RATE, **kw: object) -> DebtEngineInputExt:
    base: dict[str, object] = dict(
        deal_id=uuid4(),
        loan_amount=loan,
        ltv=0.6,
        interest_rate=rate,
        term_years=term,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=list(NOI),
    )
    base.update(kw)
    return DebtEngineInputExt(**base)  # type: ignore[arg-type]


def _debt(term: int, loan: float = LOAN, rate: float = RATE, **kw: object):
    return DebtEngine().run(_debt_input(term, loan, rate, **kw))


def _returns_input(debt_out, *, loan: float = LOAN, scalar: float | None = None, **kw: object):
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
        annual_debt_service=debt_out.annual_debt_service,
        loan_amount=loan,
        # What the runner forwarded before the fix: the maturity balance.
        loan_balance_at_exit=(
            scalar if scalar is not None else debt_out.schedule[-1].ending_balance
        ),
        loan_balance_by_month=[m.ending_balance for m in debt_out.monthly_schedule],
        equity=EQUITY,
    )
    base.update(kw)
    return ReturnsEngineInputExt(**base)  # type: ignore[arg-type]


def _selected(out):
    rows = [r for r in out.exit_schedule if r.is_selected]
    assert len(rows) == 1
    return rows[0]


# ─────────────── returns engine — the selected exit ───────────────


def test_headline_pays_off_the_sale_month_balance_when_the_term_outlasts_the_hold() -> None:
    debt = _debt(10)
    m60 = debt.monthly_schedule[59].ending_balance
    maturity = debt.schedule[-1].ending_balance
    assert round(m60) == 37_444_391 and round(maturity) == 33_910_451
    out = ReturnsEngine().run(_returns_input(debt))
    sel = _selected(out)
    assert sel.exit_year == 5 and sel.exit_month == 60
    assert sel.loan_payoff == m60
    expected_net = out.gross_sale_price - out.selling_costs - m60
    assert out.net_proceeds == pytest.approx(expected_net, abs=1e-6)
    # The hold-year row IS the headline.
    assert sel.net_proceeds == out.net_proceeds
    assert sel.levered_irr == out.levered_irr
    # The maturity balance would have overstated proceeds by ~$3.5M.
    assert out.net_proceeds < out.gross_sale_price - out.selling_costs - maturity - 3_000_000


def test_off_cycle_exit_pays_off_the_balance_at_that_month() -> None:
    debt = _debt(10)
    out = ReturnsEngine().run(_returns_input(debt, exit_month_override=52))
    sel = _selected(out)
    assert sel.exit_month == 52
    assert sel.loan_payoff == debt.monthly_schedule[51].ending_balance
    assert out.net_proceeds == sel.net_proceeds


def test_maturity_before_sale_carries_the_last_balance() -> None:
    debt = _debt(3)
    assert len(debt.monthly_schedule) == 36
    out = ReturnsEngine().run(_returns_input(debt))
    sel = _selected(out)
    assert sel.loan_payoff == debt.monthly_schedule[-1].ending_balance
    assert round(sel.loan_payoff) == 38_566_897


def test_refi_deal_headline_still_pays_the_refi_balance() -> None:
    debt = _debt(10)
    out = ReturnsEngine().run(
        _returns_input(
            debt,
            scalar=55_000_000.0,
            refi_balance=55_000_000.0,
            refi_cash_out=5_000_000.0,
            refi_year=3,
        )
    )
    sel = _selected(out)
    assert sel.refi_included is True
    assert sel.loan_payoff == 55_000_000.0


def test_cash_in_refi_sale_repays_the_refi_balance() -> None:
    # A refinance that raises LESS than the senior balance (cash-out clamped
    # to 0) still replaces the senior loan: the sale repays the refi balance.
    # The paydown equity would fund is NOT modelled — the debt engine's
    # ``refi_warning`` discloses it (see the debt tests below).
    debt = _debt(10)
    out = ReturnsEngine().run(
        _returns_input(
            debt,
            scalar=36_000_000.0,
            refi_balance=36_000_000.0,
            refi_cash_out=0.0,
            refi_year=3,
        )
    )
    sel = _selected(out)
    assert sel.loan_payoff == 36_000_000.0
    # The interim rows follow the same rule once the refi year has passed.
    rows = {r.exit_year: r for r in out.exit_schedule if r.exit_year}
    assert rows[2].loan_payoff == debt.monthly_schedule[23].ending_balance
    assert rows[4].loan_payoff == 36_000_000.0


def test_true_cash_neutral_refi_sale_repays_the_refi_balance() -> None:
    # Proceeds exactly cover the senior balance + fee: cash-out is 0 only
    # because of the max(0, …) clamp, and the sale repays the refi balance.
    debt = _debt(10)
    senior_at_refi = debt.monthly_schedule[35].ending_balance
    fee_pct = 0.01
    proceeds = senior_at_refi / (1.0 - fee_pct)
    assert proceeds - senior_at_refi - proceeds * fee_pct == pytest.approx(0.0, abs=1e-6)
    out = ReturnsEngine().run(
        _returns_input(
            debt,
            scalar=proceeds,
            refi_balance=proceeds,
            refi_cash_out=0.0,
            refi_year=3,
        )
    )
    assert _selected(out).loan_payoff == proceeds
    rows = {r.exit_year: r for r in out.exit_schedule if r.exit_year}
    assert rows[2].loan_payoff == debt.monthly_schedule[23].ending_balance
    assert rows[3].loan_payoff == proceeds and rows[4].loan_payoff == proceeds


# ─────────────── debt engine — the published scalar ───────────────


@pytest.mark.parametrize(
    ("term", "expected", "warns"),
    [(5, 37_444_391, False), (10, 37_444_391, False), (3, 38_566_897, True)],
)
def test_balance_at_exit_is_the_hold_horizon_balance(term: int, expected: int, warns: bool) -> None:
    out = _debt(term)
    assert out.balance_at_exit is not None
    assert round(out.balance_at_exit) == expected
    if warns:
        assert out.maturity_warning == (
            "The loan matures in Year 3, before the Year 5 sale; the model carries "
            "the $38,566,897 maturity balance to the sale and keeps the same debt "
            "service. Model a refinance or set the loan term to cover the hold."
        )
    else:
        assert out.maturity_warning is None


def test_balance_at_exit_reads_the_hold_not_the_noi_pin_length() -> None:
    # A 10-year NOI pin on a 5-year hold: the sale is month 60.
    ten = [5_000_000.0] * 10
    out = _debt(10, noi_by_year=ten, hold_years=5)
    assert out.balance_at_exit == out.monthly_schedule[59].ending_balance
    assert round(out.balance_at_exit) == 37_444_391
    assert out.maturity_warning is None
    # Term 5 on that pin: the loan matures AT the Year-5 sale — no warning.
    assert _debt(5, noi_by_year=ten, hold_years=5).maturity_warning is None
    # A 3-year pin on a 5-year hold: still the month-60 balance.
    short = _debt(10, noi_by_year=[5_000_000.0] * 3, hold_years=5)
    assert short.balance_at_exit == short.monthly_schedule[59].ending_balance


def test_balance_at_exit_reads_the_selected_exit_month() -> None:
    out = _debt(10, hold_years=5, exit_month=52)
    assert out.balance_at_exit == out.monthly_schedule[51].ending_balance
    assert out.maturity_warning is None


def test_maturity_warning_compares_maturity_to_the_sale() -> None:
    out = _debt(3, hold_years=5)
    assert out.maturity_warning == (
        "The loan matures in Year 3, before the Year 5 sale; the model carries "
        "the $38,566,897 maturity balance to the sale and keeps the same debt "
        "service. Model a refinance or set the loan term to cover the hold."
    )
    assert _debt(5, hold_years=5).maturity_warning is None


def test_balance_at_horizon_zero_is_none() -> None:
    from app.engines.debt import balance_at_horizon

    sched = _debt(5).schedule
    assert balance_at_horizon(sched, 0) is None
    assert balance_at_horizon([], 5) is None
    assert balance_at_horizon(sched, 9) == sched[-1].ending_balance


_CASH_IN_REFI = {"refi_test_year": 3, "refi_market_ltv_pct": 0.5, "refi_stabilized_value": 60e6}
_CASH_OUT_REFI = {"refi_test_year": 3, "refi_market_ltv_pct": 0.75, "refi_stabilized_value": 60e6}


def test_refi_warning_discloses_a_shortfall_the_model_does_not_charge() -> None:
    out = _debt(10, hold_years=5, debt_stack_overrides=dict(_CASH_IN_REFI))
    assert out.refi_year == 3 and out.refi_cash_out == 0.0
    assert round(out.schedule[2].ending_balance) == 38_566_897
    assert out.refi_warning == (
        "The Year 3 refinance raises $30,000,000 against a $38,566,897 balance; "
        "the $8,566,897 shortfall (plus fees) that equity would fund is not modelled."
    )


def test_no_refi_warning_when_the_refi_covers_the_balance() -> None:
    assert _debt(10, hold_years=5, debt_stack_overrides=dict(_CASH_OUT_REFI)).refi_warning is None
    assert _debt(10, hold_years=5).refi_warning is None


def test_refi_after_maturity_retires_the_carried_maturity_balance() -> None:
    out = _debt(
        3,
        hold_years=5,
        debt_stack_overrides={
            "refi_test_year": 4, "refi_market_ltv_pct": 0.75, "refi_stabilized_value": 60e6,
        },
    )
    maturity_balance = out.schedule[-1].ending_balance
    assert round(maturity_balance) == 38_566_897
    assert out.refi_year == 4
    assert out.refi_existing_balance_repaid == maturity_balance
    assert out.refi_cash_out == pytest.approx(45_000_000.0 - maturity_balance)
    assert out.maturity_warning == (
        "The loan matures in Year 3, before the Year 4 refinance; the model carries "
        "the $38,566,897 maturity balance to the refinance and keeps the same debt "
        "service until then. Set the loan term to reach the refinance."
    )


# ─────────────── sensitivity financing grid ───────────────


def test_financing_grid_cell_prices_its_own_loan_balance() -> None:
    base_debt_in = DebtEngineInputExt(
        deal_id=uuid4(),
        loan_amount=LOAN,
        ltv=0.6,
        interest_rate=RATE,
        term_years=10,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=list(NOI),
    )
    base_debt = DebtEngine().run(base_debt_in)
    total_capital = LOAN + EQUITY
    base_in = _returns_input(
        base_debt, scalar=base_debt.monthly_schedule[59].ending_balance
    )
    other_loan = 30_000_000.0
    spec = SensitivitySpec(
        key="irr_loan_rate",
        label="IRR — Loan × Rate",
        row_variable="loan_amount",
        row_values=[LOAN, other_loan],
        col_variable="interest_rate",
        col_values=[RATE, 0.07],
        metric="levered_irr",
        mode="financing",
    )
    out = SensitivityEngine().run(
        SensitivityInput(
            deal_id=base_in.deal_id,
            base_returns_input=base_in,
            row_variable="exit_cap_rate",
            row_values=[0.07, 0.075],
            col_variable="revpar_growth",
            col_values=[0.02, 0.03],
            metric="levered_irr",
            specs=[spec],
            base_debt_input=base_debt_in,
            total_capital=total_capital,
        )
    )
    grid = next(m for m in out.matrices if m.key == "irr_loan_rate")
    cell = next(
        c for c in grid.cells
        if abs(c.row_value - other_loan) < 1.0 and abs(c.col_value - RATE) < 1e-12
    )

    own = _debt(10, loan=other_loan)
    own_m60 = own.monthly_schedule[59].ending_balance

    def _irr(by_month: list[float], scalar: float) -> float:
        trial = base_in.model_copy(
            update={
                "loan_amount": other_loan,
                "annual_debt_service": own.annual_debt_service,
                "debt_service_by_year": [],
                "loan_balance_at_exit": scalar,
                "loan_balance_by_month": by_month,
                "refi_balance": None,
                "equity": total_capital - other_loan,
                "exit_schedule": False,
            }
        )
        return ReturnsEngine().run(trial).levered_irr

    expected = _irr([m.ending_balance for m in own.monthly_schedule], own_m60)
    stale = _irr(list(base_in.loan_balance_by_month), base_in.loan_balance_at_exit or 0.0)
    assert abs(expected - stale) > 1e-4  # a stale base balance is a different answer
    assert cell.value == pytest.approx(expected, abs=1e-12)


def test_financing_grid_base_cell_reproduces_the_headline_on_a_refi_deal() -> None:
    from app.engines.debt import returns_debt_inputs

    din = _debt_input(10, hold_years=5, debt_stack_overrides=dict(_CASH_OUT_REFI))
    d = DebtEngine().run(din)
    assert d.refi_year == 3 and d.refi_cash_out > 0
    base_in = _returns_input(d).model_copy(
        update=returns_debt_inputs(d, fallback_loan=LOAN)
    )
    headline = ReturnsEngine().run(base_in).levered_irr
    big = 45_000_000.0
    total_capital = LOAN + EQUITY
    spec = SensitivitySpec(
        key="g", label="g", row_variable="loan_amount", row_values=[LOAN, big],
        col_variable="interest_rate", col_values=[RATE, 0.07],
        metric="levered_irr", mode="financing",
    )
    out = SensitivityEngine().run(
        SensitivityInput(
            deal_id=base_in.deal_id, base_returns_input=base_in,
            row_variable="exit_cap_rate", row_values=[0.07, 0.075],
            col_variable="revpar_growth", col_values=[0.02, 0.03],
            metric="levered_irr", specs=[spec],
            base_debt_input=din, total_capital=total_capital,
        )
    )
    grid = next(m for m in out.matrices if m.key == "g")
    base_cell = next(c for c in grid.cells if c.is_base)
    assert base_cell.value == pytest.approx(headline, abs=1e-9)
    # The $45M cell carries ITS refi cash-out, not the base loan's.
    d_big = DebtEngine().run(din.model_copy(update={"loan_amount": big}))
    assert d_big.refi_cash_out != pytest.approx(d.refi_cash_out)
    own = base_in.model_copy(
        update={
            **returns_debt_inputs(d_big, fallback_loan=big),
            "loan_amount": big,
            "equity": total_capital - big,
            "exit_schedule": False,
        }
    )
    big_cell = next(
        c for c in grid.cells if abs(c.row_value - big) < 1.0 and abs(c.col_value - RATE) < 1e-12
    )
    assert big_cell.value == pytest.approx(ReturnsEngine().run(own).levered_irr, abs=1e-12)


# ─────────────── runner-level: Cash Flow statement + Investment Bridge ───────────────


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


def _exit_debt_payoff(rows: dict) -> list:
    cf = rows["cash_flow"]["outputs"]
    line = next(r for r in cf["levered"] if r["label"] == "Exit Debt Payoff")
    return line["values"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("overrides", "month"), [({"term_years": 10}, 60), ({"term_years": 10, "exit_month": 52}, 52)])
async def test_cash_flow_and_bridge_use_the_returns_payoff(overrides: dict, month: int) -> None:
    from app.services.investment_bridge import build_investment_bridge

    rows = await _run_kimpton(**overrides)
    assert rows["cash_flow"]["status"] == "complete", rows["cash_flow"]
    debt = rows["debt"]["outputs"]
    ret = rows["returns"]["outputs"]
    sel = next(r for r in ret["exit_schedule"] if r["is_selected"])
    sale_balance = debt["monthly_schedule"][month - 1]["ending_balance"]
    assert sel["loan_payoff"] == sale_balance
    # The Debt tab's Balance at Exit IS the payoff Returns deducted.
    assert debt["balance_at_exit"] == sel["loan_payoff"]
    assert debt.get("maturity_warning") is None

    values = _exit_debt_payoff(rows)
    hold = ret["hold_years"]
    assert values[hold] == pytest.approx(-sale_balance, abs=0.01)

    bridge = build_investment_bridge(rows)
    fin = next(leg for leg in bridge["legs"] if leg["key"] == "financing")
    payoff = next(c for c in fin["components"] if c["label"] == "Loan payoff at exit")
    assert payoff["value"] == pytest.approx(-sale_balance, abs=0.01)


def test_sale_before_the_refinance_repays_the_senior_and_later_rows_owe_the_refi() -> None:
    from app.engines.debt import returns_debt_inputs

    d = _debt(10, hold_years=5, exit_month=30, debt_stack_overrides=dict(_CASH_OUT_REFI))
    assert d.refi_year == 3
    senior_m30 = d.monthly_schedule[29].ending_balance
    assert d.balance_at_exit == senior_m30
    fields = returns_debt_inputs(d, fallback_loan=LOAN)
    assert fields["refi_balance"] == d.refi_new_loan_proceeds == 45_000_000.0
    out = ReturnsEngine().run(
        _returns_input(d).model_copy(update={**fields, "exit_month_override": 30})
    )
    assert _selected(out).loan_payoff == senior_m30 == d.balance_at_exit


# ─────────────── round 3 ───────────────


def test_non_numeric_exit_month_fails_closed() -> None:
    from app.services.engine_runner import _exit_month_override, _selected_exit_month

    with pytest.raises(ValueError, match="exit_month"):
        _exit_month_override({"exit_month": "52 months"})
    with pytest.raises(ValueError, match="exit_month"):
        _selected_exit_month({"hold_years": 5, "exit_month": "soon"})
    assert _exit_month_override({"exit_month": None}) is None
    assert _exit_month_override({"exit_month": ""}) is None
    assert _exit_month_override({}) is None
    assert _exit_month_override({"exit_month": "52"}) == 52
    assert _selected_exit_month({"hold_years": 5, "exit_month": 999}) == 60


def test_refi_at_or_after_the_sale_is_disclosed() -> None:
    out = _debt(
        10,
        hold_years=5,
        debt_stack_overrides={**_CASH_OUT_REFI, "refi_test_year": 5},
    )
    assert out.refi_year is None
    assert out.refi_warning == (
        "The Year 5 refinance is at or after the Year 5 sale and is not modelled."
    )


def test_sale_before_the_refi_keeps_the_cash_out_out_of_the_annual_stream() -> None:
    from app.engines.debt import returns_debt_inputs

    d = _debt(10, hold_years=5, exit_month=30, debt_stack_overrides=dict(_CASH_OUT_REFI))
    assert d.refi_cash_out > 0
    inp = _returns_input(d).model_copy(
        update={**returns_debt_inputs(d, fallback_loan=LOAN), "exit_month_override": 30}
    )
    out = ReturnsEngine().run(inp)
    assert _selected(out).refi_included is False
    # Year 3 of the display stream: NOI − debt service, no refinance cash-out.
    ds = inp.debt_service_by_year
    assert out.cash_flows[3] == pytest.approx(NOI[2] - ds[2], abs=1e-6)


def test_maturity_warning_names_the_sale_when_it_precedes_the_refi() -> None:
    out = _debt(
        2,
        hold_years=5,
        exit_month=30,
        debt_stack_overrides={**_CASH_OUT_REFI, "refi_test_year": 4},
    )
    assert out.refi_year == 4
    bal = out.schedule[-1].ending_balance
    assert out.maturity_warning == (
        f"The loan matures in Year 2, before the month 30 sale; the model carries "
        f"the ${round(bal):,} maturity balance to the sale and keeps the same debt "
        "service. Model a refinance or set the loan term to cover the hold."
    )


@pytest.mark.asyncio
async def test_cash_flow_statement_omits_the_refi_when_the_sale_precedes_it() -> None:
    rows = await _run_kimpton(
        term_years=10,
        exit_month=30,
        debt_stack_overrides={
            "refi_test_year": 3, "refi_market_ltv_pct": 0.75, "refi_stabilized_value": 60e6,
        },
    )
    assert rows["debt"]["outputs"]["refi_year"] == 3
    assert rows["cash_flow"]["status"] == "complete", rows["cash_flow"]
    labels = [r["label"] for r in rows["cash_flow"]["outputs"]["levered"]]
    assert "Net Refinance Cash-Out" not in labels
    assert "Refinance Proceeds" not in labels
    # …and the Investment Bridge still foots to the same stream.
    from app.services.investment_bridge import build_investment_bridge

    bridge = build_investment_bridge(rows)
    assert bridge["reconciles"] is True, bridge["residual"]
