"""Tests for the Debt engine's output shape.

Pins Sam QA #4: ``DebtEngineOutputExt`` MUST echo ``loan_amount`` from
the input so the web app's Debt tab can gate its body render on
``wLoan != null``. Without this echo the tab dropped to the empty-
state placeholder even when the engine had clearly run (DSCR was
present in the badge).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.debt import DebtEngine, DebtEngineInputExt, DebtEngineOutputExt


def _input(*, loan: float = 25_000_000.0, noi: float = 2_500_000.0) -> DebtEngineInputExt:
    return DebtEngineInputExt(
        deal_id=uuid4(),
        loan_amount=loan,
        ltv=0.65,
        interest_rate=0.068,
        term_years=5,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=[noi, noi * 1.03, noi * 1.06, noi * 1.09, noi * 1.12],
    )


def test_output_echoes_loan_amount() -> None:
    """DebtEngine output must surface loan_amount so web reads it."""
    out = DebtEngine().run(_input(loan=23_683_922.0))
    assert isinstance(out, DebtEngineOutputExt)
    assert out.loan_amount == pytest.approx(23_683_922.0)


def test_output_carries_dscr_and_debt_yield() -> None:
    """Year-1 DSCR and debt yield are the headline KPIs the Debt tab
    badges. They must come back populated on a successful run."""
    out = DebtEngine().run(_input(loan=20_000_000.0, noi=2_400_000.0))
    assert out.year_one_dscr is not None and out.year_one_dscr > 0
    assert out.year_one_debt_yield is not None and out.year_one_debt_yield > 0
    # Debt yield is NOI / loan — sanity check.
    assert out.year_one_debt_yield == pytest.approx(2_400_000.0 / 20_000_000.0, rel=1e-3)


def test_output_monthly_schedule_populated_for_amortization() -> None:
    """Monthly schedule must round-trip through the response so the
    Debt Schedule table on the web tab can render it."""
    out = DebtEngine().run(_input())
    # 5-year term → 60 monthly entries.
    assert len(out.monthly_schedule) == 60
    # Each row must have a positive payment.
    assert all(m.payment > 0 for m in out.monthly_schedule)
    # Final ending balance must be lower than the initial loan amount.
    assert out.monthly_schedule[-1].ending_balance < 25_000_000.0


# ─────────────────────── FON-72 fees + covenants ─────────────────────


def _input_with_basis(**kw):
    base = _input(**kw)
    return base.model_copy(
        update={
            "purchase_price_usd": 40_000_000.0,
            "total_capital_usd": 45_000_000.0,
        }
    )


def test_output_exposes_fee_fields_default_zero() -> None:
    """Fee fields are always present; a default stack carries 0.0, not None."""
    out = DebtEngine().run(_input())
    assert out.origination_fee_pct == pytest.approx(0.0)
    assert out.exit_fee_pct == pytest.approx(0.0)
    assert out.origination_fee_usd == pytest.approx(0.0)
    assert out.exit_fee_usd == pytest.approx(0.0)


def test_output_surfaces_senior_fees_from_overrides() -> None:
    """Analyst upfront/exit fees on the senior tranche surface on the output,
    with USD aggregated using the percent convention (fee_pct/100 × principal)."""
    inp = _input(loan=25_000_000.0).model_copy(
        update={
            "debt_stack_overrides": {
                "tranches": {0: {"upfront_fee_pct": 1.0, "exit_fee_pct": 0.5}}
            }
        }
    )
    out = DebtEngine().run(inp)
    assert out.origination_fee_pct == pytest.approx(1.0)
    assert out.exit_fee_pct == pytest.approx(0.5)
    assert out.origination_fee_usd == pytest.approx(25_000_000.0 * 0.01)
    assert out.exit_fee_usd == pytest.approx(25_000_000.0 * 0.005)


def test_output_exposes_covenant_current_and_headroom() -> None:
    """Debt output carries LTV/LTC/DSCR/debt-yield covenants, each with a live
    Current reading and signed Headroom vs the threshold."""
    out = DebtEngine().run(_input_with_basis(loan=25_000_000.0, noi=2_500_000.0))
    by = {c.name: c for c in out.covenants}
    assert set(by) == {"ltv", "ltc", "dscr", "debt_yield"}

    ltv = by["ltv"]
    assert ltv.kind == "max"
    assert ltv.current == pytest.approx(25_000_000.0 / 40_000_000.0)  # 0.625
    assert ltv.threshold == pytest.approx(0.65)
    # Ceiling headroom = threshold − current (positive = under the cap).
    assert ltv.headroom == pytest.approx(0.65 - 0.625)
    assert ltv.passes is True

    dscr = by["dscr"]
    assert dscr.kind == "min"
    assert dscr.current is not None and dscr.current > 0
    # Floor headroom = current − threshold.
    assert dscr.headroom == pytest.approx(dscr.current - dscr.threshold)


def test_covenant_thresholds_are_override_driven() -> None:
    """A tighter analyst LTV covenant flips the pass/fail + headroom sign."""
    inp = _input_with_basis(loan=25_000_000.0).model_copy(
        update={"debt_stack_overrides": {"covenant_max_ltv": 0.60}}
    )
    out = DebtEngine().run(inp)
    ltv = next(c for c in out.covenants if c.name == "ltv")
    assert ltv.threshold == pytest.approx(0.60)
    # current 0.625 > 0.60 → breach.
    assert ltv.passes is False
    assert ltv.headroom == pytest.approx(0.60 - 0.625)  # negative
