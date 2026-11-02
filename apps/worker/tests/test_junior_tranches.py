"""A funded junior tranche (PACE, tranche 1) is a real loan everywhere.

Pre-existing defect (review P-1): the Debt tab counted an activated PACE loan
in Total Debt / LTV / DSCR, but Returns neither funded the deal with it
(equity at close was overstated by its principal) nor repaid it at the sale
(net sale proceeds overstated by its balance); with a refinance its debt
service vanished from Returns altogether.

The rule now:
  * a PRICED junior tranche (principal + a rate) is a source of funds at close
    — equity = total uses − senior − junior — its origination fee is a use;
  * its debt service is in every debt-service series, before and after a
    refinance (the refinance retires the SENIOR loan only);
  * every sale repays its outstanding balance plus its exit fee;
  * a tranche whose terms are pending (no rate) stays out of the cash flows
    (sources, debt service, payoff, exit fee) — it is never free money.

Reference (review probe rv_junior_probe.py): $60M price, $40M senior 6.5% /
30-yr amortization, 10-yr term, NOI $5M flat, 5-yr hold, $5M PACE at 8% IO.
Correct model: equity $17M, payoff $42,444,391 → levered IRR ≈ 16.53%,
EM ≈ 1.922x (verified by hand: (5 × 1,566,073 + 24,848,942) ÷ 17M = 1.9223).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-junior-tranches.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.capital import CapitalEngine, CapitalEngineInput, JuniorDebtFunding  # noqa: E402
from app.engines.debt import (  # noqa: E402
    DebtEngine,
    DebtEngineInputExt,
    funded_junior_tranches,
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
PACE_AMT = 5_000_000.0
NOI = [5_000_000.0] * 5
PACE = {"tranches": {"1": {"principal_usd": PACE_AMT, "rate_pct": 0.08, "amortization_months": 0}}}
PACE_FEE = {
    "tranches": {
        "1": {"principal_usd": PACE_AMT, "rate_pct": 0.08, "amortization_months": 0, "exit_fee_pct": 1.0}
    }
}
PACE_PENDING = {"tranches": {"1": {"principal_usd": PACE_AMT, "exit_fee_pct": 1.0}}}
REFI = {"refi_test_year": 3, "refi_market_ltv_pct": 0.75, "refi_stabilized_value": 60e6}


def _debt_input(ov: dict | None = None, loan: float = LOAN, **kw: object) -> DebtEngineInputExt:
    return DebtEngineInputExt(
        deal_id=uuid4(), loan_amount=loan, ltv=0.6, interest_rate=0.065, term_years=10,
        amortization_years=30, interest_only_years=0, noi_by_year=list(NOI), hold_years=5,
        debt_stack_overrides=ov, **kw,  # type: ignore[arg-type]
    )


def _debt(ov: dict | None = None, **kw: object):
    return DebtEngine().run(_debt_input(ov, **kw))


def _junior_total(d) -> float:
    return sum(t.loan_amount for t in d.junior_tranches)


def _returns_input(d, **kw: object) -> ReturnsEngineInputExt:
    junior = _junior_total(d)
    base: dict[str, object] = dict(
        deal_id=uuid4(),
        assumptions=ModelAssumptions(
            purchase_price=60e6, ltv=0.6, interest_rate=0.065, amortization_years=30,
            loan_term_years=10, hold_years=5, exit_cap_rate=0.075, revpar_growth=0.03,
            expense_growth=0.03, selling_costs_pct=0.02, closing_costs_pct=0.02,
        ),
        year_one_noi=NOI[0], noi_by_year=list(NOI),
        loan_amount=LOAN + junior, equity=22e6 - junior,
        **returns_debt_inputs(d, fallback_loan=LOAN),
    )
    base.update(kw)
    return ReturnsEngineInputExt(**base)  # type: ignore[arg-type]


def _sel(out):
    return next(r for r in out.exit_schedule if r.is_selected)


# ─────────────── debt engine ───────────────


def test_priced_pace_is_a_junior_tranche_with_its_own_schedule() -> None:
    d = _debt(PACE)
    assert [t.label for t in d.junior_tranches] == ["PACE Loan"]
    j = d.junior_tranches[0]
    assert j.loan_amount == PACE_AMT and j.all_in_rate == pytest.approx(0.08)
    assert len(d.junior_balance_by_month) == 60
    assert all(b == PACE_AMT for b in d.junior_balance_by_month)
    senior_ds = d.schedule[0].debt_service
    # Every year carries the PACE interest — published even without a refi.
    assert d.debt_service_by_year == pytest.approx([senior_ds + 400_000.0] * 5)
    assert d.annual_debt_service == pytest.approx(senior_ds + 400_000.0)
    # Balance at Exit is everything the sale repays.
    assert d.balance_at_exit == pytest.approx(d.monthly_schedule[59].ending_balance + PACE_AMT)


def test_pending_pace_stays_out_of_the_cash_flows() -> None:
    d = _debt(PACE_PENDING)
    assert d.junior_tranches == []
    assert d.junior_balance_by_month == []
    assert d.debt_service_by_year == []
    f = returns_debt_inputs(d, fallback_loan=LOAN)
    assert f["loan_exit_fee"] == 0.0  # its exit fee is not charged either
    assert funded_junior_tranches(PACE_PENDING) == []


def test_refi_retires_the_senior_only_and_pace_debt_service_continues() -> None:
    d = _debt({**REFI, **PACE})
    refi_ds = 45_000_000.0 * 0.068
    senior_ds = d.schedule[0].debt_service
    assert d.debt_service_by_year == pytest.approx(
        [senior_ds + 400_000.0] * 3 + [refi_ds + 400_000.0] * 2
    )
    assert d.balance_at_exit == pytest.approx(45_000_000.0 + PACE_AMT)
    # The refinance retired the senior only: its cash-out is unchanged by PACE.
    assert d.refi_cash_out == pytest.approx(_debt(REFI).refi_cash_out)


def test_funded_junior_tranches_reads_the_overrides() -> None:
    js = funded_junior_tranches(PACE_FEE)
    assert [(t.label, t.loan_amount, t.exit_fee_pct) for t in js] == [("PACE Loan", PACE_AMT, 1.0)]
    assert funded_junior_tranches(None) == []


# ─────────────── capital engine ───────────────


def test_capital_counts_the_junior_principal_as_a_source() -> None:
    base = dict(deal_id=uuid4(), purchase_price=60e6, keys=100, ltv=0.6, loan_costs_pct=0.0)
    plain = CapitalEngine().run(CapitalEngineInput(**base))  # type: ignore[arg-type]
    out = CapitalEngine().run(
        CapitalEngineInput(
            **base,  # type: ignore[arg-type]
            junior_debt=[JuniorDebtFunding(label="PACE Loan", amount=PACE_AMT, origination_fee_pct=1.0)],
        )
    )
    assert out.junior_debt_amount == PACE_AMT
    assert out.debt_amount == plain.debt_amount  # the senior is unchanged
    fee = PACE_AMT * 0.01
    assert out.total_capital == pytest.approx(plain.total_capital + fee)
    assert out.equity_amount == pytest.approx(plain.equity_amount + fee - PACE_AMT)
    sources = {s.label: s.amount for s in out.sources}
    assert sources["PACE Loan"] == PACE_AMT
    uses = {u.label: u.amount for u in out.uses}
    assert uses["PACE Loan Origination Fee"] == pytest.approx(fee)
    assert sources["Total Sources"] == pytest.approx(uses["Total Uses"])


# ─────────────── returns engine ───────────────


def test_pace_deal_returns_match_the_independent_model() -> None:
    d = _debt(PACE)
    out = ReturnsEngine().run(_returns_input(d))
    sel = _sel(out)
    assert sel.loan_payoff == pytest.approx(42_444_391, abs=1.0)
    assert sel.net_proceeds == pytest.approx(24_848_942, abs=1.0)
    assert out.levered_irr == pytest.approx(0.165341, abs=5e-6)
    assert out.equity_multiple == pytest.approx(1.9223, abs=5e-4)
    # Every interim row repays the PACE too.
    for r in out.exit_schedule:
        assert r.loan_payoff == pytest.approx(
            d.monthly_schedule[r.exit_month - 1].ending_balance + PACE_AMT
        )


def test_pace_after_a_refi_is_still_repaid_and_still_serviced() -> None:
    d = _debt({**REFI, **PACE})
    out = ReturnsEngine().run(_returns_input(d))
    rows = {r.exit_year: r for r in out.exit_schedule}
    assert rows[5].loan_payoff == pytest.approx(45_000_000.0 + PACE_AMT)
    assert rows[2].loan_payoff == pytest.approx(d.monthly_schedule[23].ending_balance + PACE_AMT)
    no_pace = ReturnsEngine().run(_returns_input(_debt(REFI)))
    assert out.levered_irr != pytest.approx(no_pace.levered_irr, abs=1e-4)


def test_junior_exit_fee_is_paid_at_every_sale_and_not_retired_by_the_refi() -> None:
    d = _debt({**REFI, **PACE_FEE})
    out = ReturnsEngine().run(_returns_input(d))
    rows = {r.exit_year: r for r in out.exit_schedule}
    assert rows[2].loan_exit_fee == pytest.approx(50_000.0)
    assert rows[5].loan_exit_fee == pytest.approx(50_000.0)


def test_financing_grid_base_cell_is_the_headline_with_a_pace_tranche() -> None:
    din = _debt_input(PACE)
    d = DebtEngine().run(din)
    base_in = _returns_input(d)
    headline = ReturnsEngine().run(base_in).levered_irr
    spec = SensitivitySpec(
        key="g", label="g", row_variable="loan_amount", row_values=[LOAN, 35e6],
        col_variable="interest_rate", col_values=[0.065, 0.07],
        metric="levered_irr", mode="financing",
    )
    out = SensitivityEngine().run(
        SensitivityInput(
            deal_id=base_in.deal_id, base_returns_input=base_in,
            row_variable="exit_cap_rate", row_values=[0.07, 0.075],
            col_variable="revpar_growth", col_values=[0.02, 0.03],
            metric="levered_irr", specs=[spec],
            base_debt_input=din, total_capital=62e6,
        )
    )
    cell = next(c for c in next(m for m in out.matrices if m.key == "g").cells if c.is_base)
    assert cell.value == pytest.approx(headline, abs=1e-12)


# ─────────────── runner: every consumer agrees ───────────────


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
            session, deal_id=KIMPTON_FIXTURE_DEAL_ID, tenant_id=tenant_id,
            run_id=str(uuid4()), assumptions=seed,
        )
        return await get_run_scoped_outputs(
            session, deal_id=KIMPTON_FIXTURE_DEAL_ID, tenant_id=tenant_id
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ovr",
    [
        PACE_FEE,
        {**PACE_FEE, **REFI},
        {"tranches": {"1": {**PACE_FEE["tranches"]["1"], "upfront_fee_pct": 2.0}}},
    ],
)
async def test_kimpton_with_pace_agrees_across_every_consumer(ovr: dict) -> None:
    from app.services.investment_bridge import build_investment_bridge

    base = await _run_kimpton()
    rows = await _run_kimpton(debt_stack_overrides=dict(ovr))
    cap0, cap = base["capital"]["outputs"], rows["capital"]["outputs"]
    debt = rows["debt"]["outputs"]
    ret = rows["returns"]["outputs"]
    assert cap["junior_debt_amount"] == PACE_AMT
    # Equity falls by its principal, less any PACE origination fee it funds.
    pace_fee = PACE_AMT * float(ovr["tranches"]["1"].get("upfront_fee_pct", 0.0)) / 100.0
    assert cap["equity_amount"] == pytest.approx(
        cap0["equity_amount"] - PACE_AMT + pace_fee, abs=1e-6
    )
    assert rows["returns"]["inputs"]["equity"] == cap["equity_amount"]
    assert rows["returns"]["inputs"]["loan_amount"] == pytest.approx(cap["debt_amount"] + PACE_AMT)
    sel = next(r for r in ret["exit_schedule"] if r["is_selected"])
    assert sel["loan_payoff"] == pytest.approx(debt["balance_at_exit"])
    assert sel["loan_exit_fee"] == pytest.approx(50_000.0)
    assert rows["cash_flow"]["status"] == "complete", rows["cash_flow"]
    lev = {r["label"]: r["values"] for r in rows["cash_flow"]["outputs"]["levered"]}
    assert lev["PACE Loan Proceeds"][0] == pytest.approx(PACE_AMT)
    bridge = build_investment_bridge(rows)
    assert bridge["reconciles"] is True, bridge["residual"]
    part = rows["partnership"]["outputs"]
    assert rows["partnership"]["inputs"]["total_equity"] == cap["equity_amount"]
    assert part["gp_cash_flows"]


@pytest.mark.asyncio
async def test_kimpton_with_a_pending_pace_is_unchanged() -> None:
    base = await _run_kimpton()
    rows = await _run_kimpton(debt_stack_overrides=dict(PACE_PENDING))
    assert rows["capital"]["outputs"]["equity_amount"] == base["capital"]["outputs"]["equity_amount"]
    assert rows["returns"]["outputs"]["levered_irr"] == base["returns"]["outputs"]["levered_irr"]
