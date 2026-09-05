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


# ─────────────── FON-72 follow-up: stabilized credit metrics ──────────


def test_stabilized_metrics_populate_and_entry_unchanged() -> None:
    """Stabilized DSCR / debt yield populate to sane values; the existing
    Year-1 (entry) metrics and schedule stay byte-identical.

    The golden-style ``_input`` is a stabilized acquisition (steady 3% NOI
    growth, no ramp, no occupancy signal) → the NOI-plateau fallback puts the
    stabilized year at Year 1, so stabilized == entry (a stabilized asset)."""
    inp = _input(loan=20_000_000.0, noi=2_400_000.0)
    baseline = DebtEngine().run(inp)  # what the tab shows today (stabilized None)

    out = DebtEngine().run(inp)
    # Existing numbers unchanged.
    assert out.year_one_dscr == pytest.approx(baseline.year_one_dscr)
    assert out.year_one_debt_yield == pytest.approx(baseline.year_one_debt_yield)
    assert out.entry_dscr == pytest.approx(baseline.entry_dscr)
    assert out.entry_debt_yield == pytest.approx(baseline.entry_debt_yield)
    assert out.annual_debt_service == pytest.approx(baseline.annual_debt_service)
    assert [y.debt_service for y in out.schedule] == pytest.approx(
        [y.debt_service for y in baseline.schedule]
    )
    # Stabilized now populates to sane values.
    assert out.stabilized_dscr is not None and out.stabilized_dscr > 0
    assert out.stabilized_debt_yield is not None and out.stabilized_debt_yield > 0
    # Stabilized == entry for a stabilized (no-ramp) acquisition.
    assert out.stabilized_dscr == pytest.approx(out.entry_dscr)
    assert out.stabilized_debt_yield == pytest.approx(out.entry_debt_yield)


def test_stabilized_year_from_occupancy_signal_when_ramping() -> None:
    """With an occupancy ramp + stabilized-occupancy assumption, the stabilized
    year is the first year occupancy reaches the assumption — and the stabilized
    metrics reflect THAT year's NOI, distinct from entry."""
    noi = [2_000_000.0, 2_500_000.0, 2_900_000.0, 3_100_000.0, 3_150_000.0]
    inp = _input(loan=25_000_000.0).model_copy(
        update={
            "noi_by_year": noi,
            # Ramps to the 0.76 stabilized assumption in Year 4 (index 3).
            "occupancy_by_year": [0.60, 0.68, 0.74, 0.76, 0.762],
            "stabilized_occupancy": 0.76,
        }
    )
    out = DebtEngine().run(inp)
    stab_noi = noi[3]
    # Debt yield uses the loan denominator (total_debt == senior on this stack).
    assert out.stabilized_debt_yield == pytest.approx(stab_noi / out.loan_amount)
    # DSCR uses the stabilized-year (Y4) debt service off the same schedule.
    stab_ds = out.schedule[3].debt_service
    assert out.stabilized_dscr == pytest.approx(stab_noi / stab_ds)
    # Clearly a stabilized-year (Y4) reading, not Year 1.
    assert out.stabilized_debt_yield > (out.entry_debt_yield or 0.0)
    # Entry metrics untouched (Year-1 NOI ÷ loan).
    assert out.entry_debt_yield == pytest.approx(noi[0] / out.loan_amount)


def test_stabilized_year_noi_plateau_fallback() -> None:
    """No occupancy signal → derive the stabilized year from the NOI plateau:
    the year after the last above-terminal growth step."""
    # Big early ramp (30%, 11.5%, 3.4%) settling to ~2% terminal → stabilizes Y4.
    noi = [100.0, 130.0, 145.0, 150.0, 153.0]
    inp = _input(loan=25_000_000.0).model_copy(update={"noi_by_year": noi})
    out = DebtEngine().run(inp)
    stab_noi = noi[3]  # Year 4
    assert out.stabilized_debt_yield == pytest.approx(stab_noi / out.loan_amount)
    assert out.stabilized_dscr == pytest.approx(
        stab_noi / out.schedule[3].debt_service
    )


def test_completion_guarantee_echoed_from_input() -> None:
    """The qualitative Completion Guarantee status round-trips to the output;
    absent it stays None (the tab renders "—")."""
    assert DebtEngine().run(_input()).completion_guarantee is None
    inp = _input().model_copy(update={"completion_guarantee": "in_place"})
    assert DebtEngine().run(inp).completion_guarantee == "in_place"
