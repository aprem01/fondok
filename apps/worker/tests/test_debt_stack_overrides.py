"""FON-63 — multi-tranche debt stack + editable-tranche override wiring.

The debt engine now seeds a deal-agnostic institutional stack (the deal's own
senior loan + a PACE placeholder) and lets an analyst edit tranches from the
Debt tab via indexed overrides (``debt_stack.tranches.<idx>.<field>``). These
tests pin the two invariants that matter most:

  * a deal with NO debt overrides is byte-for-byte the legacy single-senior
    model (PACE placeholder excluded), so nothing regresses; and
  * edits + an activated PACE tranche flow through to the headline metrics the
    Returns engine consumes.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.engines.debt import (
    DebtEngine,
    DebtEngineInputExt,
    _apply_tranche_overrides,
    _build_default_tranches,
)


def _input(**overrides) -> DebtEngineInputExt:
    base = dict(
        deal_id=uuid4(),
        loan_amount=23_700_000,
        ltv=0.65,
        interest_rate=0.068,
        term_years=5,
        amortization_years=30,
        interest_only_years=0,
        noi_by_year=[1_300_000, 1_400_000, 1_500_000, 1_600_000, 1_700_000],
        purchase_price_usd=36_400_000,
        total_capital_usd=44_300_000,
    )
    base.update(overrides)
    return DebtEngineInputExt(**base)


def test_default_stack_is_senior_only_and_matches_legacy() -> None:
    out = DebtEngine().run(_input())
    # PACE placeholder ($0 / pending) is excluded — leverage unchanged.
    assert len(out.debt_stack.tranches) == 1
    assert out.debt_stack.tranches[0].kind == "senior"
    assert out.loan_amount == pytest.approx(23_700_000)
    assert out.debt_stack.total_debt == pytest.approx(23_700_000)
    # Headline DS comes from the monthly senior schedule (no drift).
    assert out.annual_debt_service == pytest.approx(out.schedule[0].debt_service)


def test_default_seed_has_senior_and_pending_pace() -> None:
    tranches = _build_default_tranches(_input())
    assert [t.kind for t in tranches] == ["senior", "pace"]
    assert tranches[1].terms_pending is True
    assert tranches[1].loan_amount == 0.0


def test_senior_rate_override_raises_debt_service() -> None:
    base = DebtEngine().run(_input())
    hi = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"rate_pct": 0.075}}})
    )
    assert hi.annual_debt_service > base.annual_debt_service
    assert hi.year_one_dscr < base.year_one_dscr


def test_activated_pace_adds_debt_and_service() -> None:
    base = DebtEngine().run(_input())
    p = DebtEngine().run(
        _input(
            debt_stack_overrides={
                "tranches": {1: {"principal_usd": 5_000_000, "rate_pct": 0.06}}
            }
        )
    )
    assert len(p.debt_stack.tranches) == 2
    assert p.debt_stack.total_debt == pytest.approx(28_700_000)
    # Senior DS unchanged + PACE interest ($5M x 6% = $300k).
    assert p.annual_debt_service == pytest.approx(
        base.annual_debt_service + 300_000, rel=1e-3
    )


def test_unpriced_pace_stays_pending_but_counts_in_leverage() -> None:
    base = DebtEngine().run(_input())
    p = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {1: {"principal_usd": 5_000_000}}})
    )
    pace = next(t for t in p.debt_stack.tranches if t.kind == "pace")
    # No invented rate — excluded from debt service...
    assert pace.terms_pending is True
    assert pace.all_in_rate is None
    assert p.annual_debt_service == pytest.approx(base.annual_debt_service)
    # ...but still counts toward total debt / LTV / debt yield.
    assert p.debt_stack.total_debt == pytest.approx(28_700_000)


def test_string_keyed_override_indexes_apply() -> None:
    # JSONB round-trips can key the tranche index as a string.
    p = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {"0": {"rate_pct": 0.09}}})
    )
    assert p.debt_stack.tranches[0].all_in_rate == pytest.approx(0.09)


def test_apply_overrides_is_noop_without_overrides() -> None:
    seed = _build_default_tranches(_input())
    assert _apply_tranche_overrides(seed, None) is seed
    assert _apply_tranche_overrides(seed, {}) is seed
    assert _apply_tranche_overrides(seed, {"tranches": {}}) is seed


def test_amortization_override_switches_off_interest_only() -> None:
    resolved = _apply_tranche_overrides(
        _build_default_tranches(_input(amortization_years=0)),  # IO senior
        {"tranches": {0: {"amortization_months": 300}}},
    )
    assert resolved[0].interest_only is False
    assert resolved[0].amortization_years == 25


def test_zero_amortization_override_means_interest_only() -> None:
    # A single Amort control: 0 years -> interest-only.
    resolved = _apply_tranche_overrides(
        _build_default_tranches(_input(amortization_years=30)),  # amortizing senior
        {"tranches": {0: {"amortization_months": 0}}},
    )
    assert resolved[0].interest_only is True
    assert resolved[0].amortization_years is None


def test_zero_amortization_lowers_debt_service() -> None:
    # Switching the senior to IO reduces its debt service (no principal).
    amort = DebtEngine().run(_input())  # default = amortizing 30yr
    io = DebtEngine().run(
        _input(debt_stack_overrides={"tranches": {0: {"amortization_months": 0}}})
    )
    assert io.annual_debt_service < amort.annual_debt_service
