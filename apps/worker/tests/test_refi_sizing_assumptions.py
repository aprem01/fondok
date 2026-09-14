"""FON-63 — the refinance SIZING assumptions are analyst input, end to end.

Sam's 2026-09-14 MVP QA, filed as a blocker:

    "Refinance assumptions (FON-63): Once refinance is enabled, the key sizing
    assumptions were not editable in my testing. If refinance is intended to be
    supported in this MVP, I consider that a functional gap. If refinance is
    explicitly out of scope, I'd rather clearly label/disable it than expose a
    workflow users cannot complete."

The engine has taken all of them as ``debt_stack.refi_*`` field_overrides since
FON-67; the Debt tab only ever offered the refinance YEAR, so a refinance ran on
invisible seeds (a 6.80% rate, a 10.00% debt yield, a 1.25x DSCR, a zero fee)
the analyst could not reach. These tests pin the outcome Sam asked for — every
sizing assumption is engine input, each one re-sizes the refinance, and the
derived figures move with it — plus the thing that must NOT change: with no
refinance override set, the debt engine's output is the single-phase model it
always was, and the five read-only echo fields added for the tab are all None.

See also ``test_refi.py`` (the FON-67 two-phase model) and
``apps/web/__tests__/debtTab.test.tsx`` (the browser half of the same contract:
the same keys, the same units, and no row that looks editable but is not).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.debt import DebtEngine, DebtEngineInputExt
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt
from fondok_schemas.underwriting import ModelAssumptions

# The EXACT `field_overrides` keys the Debt tab's Refinance section writes.
# Mirrors `REFI_KEYS` in `apps/web/src/components/project/DebtTab.tsx`; the
# first test below proves each one is engine input and demands a justification.
UI_REFI_KEYS: frozenset[str] = frozenset(
    {
        "debt_stack.refi_test_year",
        "debt_stack.refi_stabilized_value",
        "debt_stack.refi_market_ltv_pct",
        "debt_stack.refi_market_rate_pct",
        "debt_stack.refi_fee_pct",
        "debt_stack.refi_market_debt_yield_pct",
        "debt_stack.refi_market_dscr_min",
    }
)

# One refinance an analyst could actually complete: sized off LTV × value,
# priced at a quoted rate, with a 1.00% loan fee.
SIZED_REFI: dict[str, float] = {
    "refi_test_year": 3,
    "refi_market_ltv_pct": 0.60,
    "refi_stabilized_value": 50_000_000,
    "refi_market_rate_pct": 0.06,
    "refi_fee_pct": 0.01,
}


def _debt_input(**over) -> DebtEngineInputExt:
    base = dict(
        deal_id=uuid4(),
        loan_amount=23_700_000,
        ltv=0.65,
        interest_rate=0.068,
        term_years=5,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=[3_000_000, 3_300_000, 3_600_000, 4_000_000, 4_400_000],
        purchase_price_usd=36_400_000,
        total_capital_usd=44_300_000,
    )
    base.update(over)
    return DebtEngineInputExt(**base)


def _assumptions() -> ModelAssumptions:
    return ModelAssumptions(
        purchase_price=36_400_000, ltv=0.65, interest_rate=0.068,
        amortization_years=30, loan_term_years=5, hold_years=5,
        exit_cap_rate=0.07, revpar_growth=0.045, expense_growth=0.03,
        selling_costs_pct=0.02, closing_costs_pct=0.02,
    )


def _levered_irr(debt_overrides: dict[str, float] | None) -> float:
    """Run debt → returns the way the runner does, and report levered IRR."""
    debt_out = DebtEngine().run(_debt_input(debt_stack_overrides=debt_overrides))
    returns_out = ReturnsEngine().run(
        ReturnsEngineInputExt(
            deal_id=uuid4(),
            assumptions=_assumptions(),
            year_one_noi=3_000_000,
            noi_by_year=[3_000_000, 3_300_000, 3_600_000, 4_000_000, 4_400_000],
            annual_debt_service=debt_out.annual_debt_service,
            debt_service_by_year=debt_out.debt_service_by_year,
            loan_amount=23_700_000,
            loan_balance_at_exit=debt_out.balance_at_exit,
            equity=20_658_900,
            refi_cash_out=debt_out.refi_cash_out,
            refi_year=debt_out.refi_year,
        )
    )
    assert returns_out.levered_irr is not None
    return returns_out.levered_irr


# ── 1. Every assumption the tab offers is real engine input ────────────────


def test_every_ui_refi_key_routes_into_engine_input() -> None:
    """The keys the Refinance section writes are the keys the runner routes.

    A control that writes a key the runner drops is the failure this ticket
    exists to close, so the tab's key list is checked against the runner's own
    allow-list rather than trusted.
    """
    from app.services.engine_runner import _OVERRIDE_DEBT_KEYS

    assert UI_REFI_KEYS <= _OVERRIDE_DEBT_KEYS


def test_every_ui_refi_key_requires_a_justification() -> None:
    """FON-74 — each one moves a number, so the API refuses it without a note."""
    from app.api.deals import _override_needs_note
    from app.services.engine_runner import _OVERRIDE_NON_ENGINE_KEYS

    for key in sorted(UI_REFI_KEYS):
        assert _override_needs_note(key, _OVERRIDE_NON_ENGINE_KEYS), key


# ── 2. Each assumption re-sizes the refinance ──────────────────────────────


def test_ltv_resizes_the_refinance() -> None:
    base = DebtEngine().run(_debt_input(debt_stack_overrides=dict(SIZED_REFI)))
    raised = DebtEngine().run(
        _debt_input(debt_stack_overrides={**SIZED_REFI, "refi_market_ltv_pct": 0.65})
    )
    assert base.refi_new_loan_proceeds == pytest.approx(30_000_000)
    assert raised.refi_new_loan_proceeds == pytest.approx(32_500_000)
    # …and the derived figures move with it: a bigger loan pays a bigger fee,
    # returns more cash to equity, costs more to service and leaves more debt
    # at the sale.
    assert raised.refi_financing_costs > base.refi_financing_costs
    assert raised.refi_cash_out > base.refi_cash_out
    assert raised.debt_service_by_year[-1] > base.debt_service_by_year[-1]
    assert raised.balance_at_exit > base.balance_at_exit
    # The payoff is the senior's own schedule — unmoved by the new loan's size.
    assert raised.refi_existing_balance_repaid == pytest.approx(
        base.refi_existing_balance_repaid
    )


def test_value_at_refinance_resizes_the_refinance() -> None:
    base = DebtEngine().run(_debt_input(debt_stack_overrides=dict(SIZED_REFI)))
    richer = DebtEngine().run(
        _debt_input(
            debt_stack_overrides={**SIZED_REFI, "refi_stabilized_value": 60_000_000}
        )
    )
    assert richer.refi_value_at_refinance == pytest.approx(60_000_000)
    assert richer.refi_new_loan_proceeds == pytest.approx(0.60 * 60_000_000)
    assert richer.refi_cash_out > base.refi_cash_out


def test_new_interest_rate_reprices_the_refinance_without_resizing_it() -> None:
    base = DebtEngine().run(_debt_input(debt_stack_overrides=dict(SIZED_REFI)))
    dearer = DebtEngine().run(
        _debt_input(debt_stack_overrides={**SIZED_REFI, "refi_market_rate_pct": 0.075})
    )
    assert dearer.refi_new_interest_rate == pytest.approx(0.075)
    # Interest-only: post-refi debt service is proceeds × rate.
    assert dearer.debt_service_by_year[-1] == pytest.approx(30_000_000 * 0.075)
    # The loan is the same size, so the cash-out at the refinance is unchanged.
    assert dearer.refi_new_loan_proceeds == pytest.approx(base.refi_new_loan_proceeds)
    assert dearer.refi_cash_out == pytest.approx(base.refi_cash_out)


def test_loan_fee_moves_financing_costs_and_the_cash_out() -> None:
    base = DebtEngine().run(_debt_input(debt_stack_overrides=dict(SIZED_REFI)))
    assert base.refi_fee_pct == pytest.approx(0.01)
    assert base.refi_financing_costs == pytest.approx(30_000_000 * 0.01)
    dearer = DebtEngine().run(
        _debt_input(debt_stack_overrides={**SIZED_REFI, "refi_fee_pct": 0.02})
    )
    assert dearer.refi_financing_costs == pytest.approx(30_000_000 * 0.02)
    assert dearer.refi_cash_out == pytest.approx(base.refi_cash_out - 300_000)


def test_debt_yield_and_dscr_limits_size_the_loan_when_no_ltv_is_set() -> None:
    """With no LTV / value the two market limits ARE the sizing constraint —
    which is why they are editable rather than invisible seeds."""
    seeded = DebtEngine().run(_debt_input(debt_stack_overrides={"refi_test_year": 3}))
    assert seeded.refi_sizing_basis == "debt_yield_dscr"
    # The seeds the model falls back to, now reported so the tab can show them.
    assert seeded.refi_debt_yield_min == pytest.approx(0.10)
    assert seeded.refi_dscr_min == pytest.approx(1.25)
    noi_year_3 = 3_600_000
    assert seeded.refi_new_loan_proceeds == pytest.approx(
        min(noi_year_3 / 0.10, noi_year_3 / (1.25 * 0.068))
    )

    tighter_dy = DebtEngine().run(
        _debt_input(
            debt_stack_overrides={
                "refi_test_year": 3,
                "refi_market_debt_yield_pct": 0.12,
            }
        )
    )
    assert tighter_dy.refi_debt_yield_min == pytest.approx(0.12)
    assert tighter_dy.refi_new_loan_proceeds == pytest.approx(noi_year_3 / 0.12)

    tighter_dscr = DebtEngine().run(
        _debt_input(
            debt_stack_overrides={"refi_test_year": 3, "refi_market_dscr_min": 1.60}
        )
    )
    assert tighter_dscr.refi_dscr_min == pytest.approx(1.60)
    assert tighter_dscr.refi_new_loan_proceeds == pytest.approx(
        noi_year_3 / (1.60 * 0.068)
    )


def test_refi_year_moves_the_payoff_and_the_phasing() -> None:
    year_two = DebtEngine().run(
        _debt_input(debt_stack_overrides={**SIZED_REFI, "refi_test_year": 2})
    )
    year_three = DebtEngine().run(_debt_input(debt_stack_overrides=dict(SIZED_REFI)))
    assert year_two.refi_year == 2
    assert year_three.refi_year == 3
    # The senior amortizes, so an earlier refinance repays a LARGER balance and
    # the new (interest-only) payment starts a year sooner.
    assert year_two.refi_existing_balance_repaid > year_three.refi_existing_balance_repaid
    assert year_two.debt_service_by_year[2] == pytest.approx(30_000_000 * 0.06)
    assert year_three.debt_service_by_year[2] != pytest.approx(30_000_000 * 0.06)


# ── 3. …and it reaches Cash Flow and Returns ───────────────────────────────


def test_a_sizing_edit_flows_through_to_levered_returns() -> None:
    """The point of making these editable: an edit changes the answer."""
    base = _levered_irr(dict(SIZED_REFI))
    raised = _levered_irr({**SIZED_REFI, "refi_market_ltv_pct": 0.65})
    dearer = _levered_irr({**SIZED_REFI, "refi_market_rate_pct": 0.075})
    assert raised != pytest.approx(base)
    assert dearer != pytest.approx(base)
    # A larger cash-out earlier lifts the levered IRR; a dearer loan drags it.
    assert raised > base > dearer


# ── 4. The basis flags the tab reads, so it never states a method it guessed ──


def test_sizing_and_rate_basis_report_the_method_actually_used() -> None:
    ltv_sized = DebtEngine().run(_debt_input(debt_stack_overrides=dict(SIZED_REFI)))
    assert ltv_sized.refi_sizing_basis == "ltv"
    assert ltv_sized.refi_rate_basis == "input"
    assert ltv_sized.refi_ltv == pytest.approx(0.60)

    limit_sized = DebtEngine().run(
        _debt_input(debt_stack_overrides={"refi_test_year": 3})
    )
    assert limit_sized.refi_sizing_basis == "debt_yield_dscr"
    assert limit_sized.refi_rate_basis == "input"


def test_curve_priced_refi_reports_sofr_basis_and_ignores_the_flat_rate() -> None:
    """When a SOFR curve + refi spread price the loan, the flat rate is NOT
    what runs — so the tab is told, and shows the rate read-only instead of an
    editor whose value would be overwritten on the next run."""
    curve = [0.042] * 60
    out = DebtEngine().run(
        _debt_input(
            sofr_curve=curve,
            debt_stack_overrides={**SIZED_REFI, "refi_spread_pct": 0.035},
        )
    )
    assert out.refi_rate_basis == "sofr_curve"
    assert out.refi_new_interest_rate == pytest.approx(0.042 + 0.035)
    # The analyst's flat 6.00% is genuinely not used.
    assert out.refi_new_interest_rate != pytest.approx(0.06)


# ── 5. NOT ONE NUMBER MOVES with no refinance override set ─────────────────


def test_no_refi_deal_is_byte_for_byte_the_single_phase_model() -> None:
    """The guard on the whole change: the five FON-63 echo fields are None on a
    deal with no refinance, and every pre-existing output is what the
    single-phase model always produced (``test_refi.py`` pins the same claim
    from the FON-67 side; the monthly-waterfall and engine-runner goldens pin it
    for the full pipeline)."""
    out = DebtEngine().run(_debt_input())

    # The additions: all None, so nothing downstream sees a new value.
    assert out.refi_fee_pct is None
    assert out.refi_debt_yield_min is None
    assert out.refi_dscr_min is None
    assert out.refi_sizing_basis is None
    assert out.refi_rate_basis is None

    # The single-phase model, untouched.
    assert out.debt_service_by_year == []
    assert out.refi_cash_out == 0.0
    assert out.refi_year is None
    assert out.refi_value_at_refinance is None
    assert out.refi_ltv is None
    assert out.refi_new_loan_proceeds is None
    assert out.refi_existing_balance_repaid is None
    assert out.refi_new_interest_rate is None
    assert out.refi_financing_costs is None
    assert out.balance_at_exit == pytest.approx(out.schedule[-1].ending_balance)


def test_the_new_echo_fields_are_the_only_difference_on_a_refi_deal() -> None:
    """On a deal that DOES refinance, the five additions are the only new keys —
    every figure the refi model already produced is still exactly what the
    formulas say, computed here from the inputs rather than re-read from the
    engine."""
    out = DebtEngine().run(_debt_input(debt_stack_overrides=dict(SIZED_REFI)))
    proceeds = 0.60 * 50_000_000
    payoff = out.schedule[2].ending_balance
    fee = proceeds * 0.01
    assert out.refi_new_loan_proceeds == pytest.approx(proceeds)
    assert out.refi_existing_balance_repaid == pytest.approx(payoff)
    assert out.refi_financing_costs == pytest.approx(fee)
    assert out.refi_cash_out == pytest.approx(max(0.0, proceeds - payoff - fee))
    assert out.refi_value_at_refinance == pytest.approx(50_000_000)
    assert out.refi_new_interest_rate == pytest.approx(0.06)
    # Senior debt service through the refi year, the IO refi payment after it.
    assert out.debt_service_by_year[:3] == [
        pytest.approx(out.schedule[i].debt_service) for i in range(3)
    ]
    assert out.debt_service_by_year[3:] == [pytest.approx(proceeds * 0.06)] * 2
