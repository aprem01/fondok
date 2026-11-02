"""An interest-only-then-amortizing senior is charged its actual schedule
(refi review P-2, 2026-11).

Without a refinance or a junior tranche the debt engine published no per-year
series, so Returns charged the Year-1 (interest-only) payment for the whole
hold while the payoff at sale used the AMORTIZED balance: principal paydown
credited without its payments (+104 bp levered IRR in the probe). The debt
engine now always publishes ``debt_service_by_year`` from the senior schedule
(plus juniors), and every consumer reads that one series.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.debt import DebtEngine, DebtEngineInputExt, returns_debt_inputs
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt
from fondok_schemas.financial import ModelAssumptions

LOAN = 40e6


def _debt(**over):
    kw = dict(
        deal_id=uuid4(), loan_amount=LOAN, ltv=0.6, interest_rate=0.065,
        term_years=10, amortization_years=30, interest_only_years=2,
        noi_by_year=[5e6] * 5, hold_years=5,
    )
    kw.update(over)
    return DebtEngine().run(DebtEngineInputExt(**kw))


def _returns(fields):
    a = ModelAssumptions(
        purchase_price=60e6, ltv=0.6, interest_rate=0.065, amortization_years=30,
        loan_term_years=10, hold_years=5, exit_cap_rate=0.075, revpar_growth=0.03,
        expense_growth=0.03, selling_costs_pct=0.02, closing_costs_pct=0.02,
    )
    return ReturnsEngine().run(
        ReturnsEngineInputExt(
            deal_id=uuid4(), assumptions=a, year_one_noi=5e6, noi_by_year=[5e6] * 5,
            loan_amount=LOAN, equity=22e6, **fields,
        )
    )


def test_io_stub_publishes_the_schedule_series() -> None:
    d = _debt()
    sched = [y.debt_service for y in d.schedule[:5]]
    assert sched[0] == pytest.approx(LOAN * 0.065)  # IO years
    assert sched[2] > sched[1] * 1.1  # amortizing from Year 3
    assert d.debt_service_by_year == sched
    assert returns_debt_inputs(d, fallback_loan=LOAN)["debt_service_by_year"] == sched


def test_returns_charge_the_amortizing_payments() -> None:
    d = _debt()
    r = _returns(returns_debt_inputs(d, fallback_loan=LOAN))
    io_m = LOAN * 0.065 / 12
    amort_m = d.schedule[2].debt_service / 12
    # Month 6 (IO) and month 30 (amortizing) carry different debt service.
    assert r.levered_monthly[6] == pytest.approx(5e6 / 12 - io_m, rel=1e-6)
    assert r.levered_monthly[30] == pytest.approx(5e6 / 12 - amort_m, rel=1e-6)
    # The annual levered flows net the schedule's payments.
    for i in range(4):
        assert r.cash_flows[i + 1] == pytest.approx(5e6 - d.schedule[i].debt_service, rel=1e-9)
    # The probe: 15.09% on the schedule, not 16.13% on flat IO payments.
    assert r.levered_irr == pytest.approx(0.1509, abs=5e-4)


def test_level_payment_loan_series_is_the_level_payment() -> None:
    d = _debt(interest_only_years=0)
    assert len(d.debt_service_by_year) == 5
    for ds in d.debt_service_by_year:
        assert ds == pytest.approx(d.annual_debt_service, rel=1e-12)


def test_series_past_maturity_keeps_the_last_payment() -> None:
    # A 3-year loan, 2 years IO, sold in Year 5: the matured loan is carried
    # at its last scheduled payment, not its Year-1 interest-only payment.
    d = _debt(term_years=3)
    assert len(d.schedule) == 3
    assert d.debt_service_by_year[3:] == pytest.approx([d.schedule[2].debt_service] * 2)
