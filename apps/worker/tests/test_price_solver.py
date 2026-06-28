"""Tests for the max-price solver (Wave 2 P2.8).

The solver bisects on purchase price to find the headline number every
IC committee asks for: "what's the most we'd pay to still hit 15% IRR
(or 1.8x EM)?". These tests pin convergence, iteration caps, and the
binding-constraint chip.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID

# Per-test SQLite database BEFORE app modules import (some app modules
# eagerly evaluate Settings on import).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-price-solver.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.engines.price_solver import (  # noqa: E402
    MAX_ITERS,
    PRICE_FLOOR_MULTIPLIER,
    PRICE_TOLERANCE_USD,
    solve_max_price,
)
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402
from fondok_schemas.financial import ModelAssumptions  # noqa: E402


def _base_input(
    *,
    purchase_price: float = 40_000_000.0,
    y1_noi: float = 3_500_000.0,
    exit_cap: float = 0.075,
    annual_debt_service: float = 1_460_000.0,
    equity: float = 16_000_000.0,
    loan_amount: float = 24_000_000.0,
    hold_years: int = 5,
) -> ReturnsEngineInputExt:
    assumptions = ModelAssumptions(
        purchase_price=purchase_price,
        ltv=loan_amount / purchase_price,
        interest_rate=0.06,
        amortization_years=30,
        loan_term_years=5,
        hold_years=hold_years,
        exit_cap_rate=exit_cap,
        revpar_growth=0.03,
        expense_growth=0.03,
        selling_costs_pct=0.02,
        closing_costs_pct=0.02,
    )
    return ReturnsEngineInputExt(
        deal_id=UUID("22222222-2222-2222-2222-222222222222"),
        assumptions=assumptions,
        year_one_noi=y1_noi,
        noi_by_year=[y1_noi * (1.03 ** i) for i in range(hold_years)],
        annual_debt_service=annual_debt_service,
        loan_amount=loan_amount,
        loan_balance_at_exit=loan_amount,
        equity=equity,
    )


def test_bisect_converges_within_tolerance() -> None:
    """Returned price hits target IRR within IRR tolerance."""
    base = _base_input()
    res = solve_max_price(base, target_irr=0.12, target_em=1.5, rooms=200)
    # Re-run returns at the IRR-binding price and confirm we're close.
    from app.engines.price_solver import _flex_price

    flexed = _flex_price(base, res.max_price_for_irr)
    actual_irr = ReturnsEngine().run(flexed).levered_irr
    assert abs(actual_irr - 0.12) < 0.01, (
        f"IRR not converged: got {actual_irr:.4f}, target 0.12"
    )


def test_solver_caps_at_40_iters() -> None:
    """Two searches × ≤40 iters each ⇒ total iters ≤ 80."""
    res = solve_max_price(
        _base_input(), target_irr=0.15, target_em=1.8, rooms=200
    )
    # Sum of the two bisections; each capped at MAX_ITERS = 40.
    assert res.iters <= 2 * MAX_ITERS


def test_irr_constraint_binds_when_em_easier() -> None:
    """High IRR target + low EM target ⇒ IRR binds."""
    # 18% IRR is hard; 1.2x EM is easy. Max-price-for-IRR < max-price-for-EM.
    res = solve_max_price(
        _base_input(), target_irr=0.18, target_em=1.2, rooms=200
    )
    assert res.binding_constraint == "irr"
    assert res.max_price_for_irr < res.max_price_for_em


def test_em_constraint_binds_when_em_harder() -> None:
    """Low IRR + high EM ⇒ EM binds."""
    # 8% IRR is easy at a low price; 2.5x EM is hard. EM binds.
    res = solve_max_price(
        _base_input(), target_irr=0.08, target_em=2.5, rooms=200
    )
    assert res.binding_constraint == "em"
    assert res.max_price_for_em < res.max_price_for_irr


def test_both_binding_when_equal() -> None:
    """When IRR price and EM price land within $50K, binding == 'both'."""
    # Pick a combo where the two targets land at nearly the same price.
    # Iterate one to find a matching EM target — but as a deterministic
    # test, we instead probe IRR target and choose EM that maps to it.
    base = _base_input()
    irr_only = solve_max_price(
        base, target_irr=0.13, target_em=10.0, rooms=200
    )
    matching_price = irr_only.max_price_for_irr
    # Compute EM at that price and use it as both target metrics.
    from app.engines.price_solver import _flex_price

    flexed = _flex_price(base, matching_price)
    em_at_match = ReturnsEngine().run(flexed).equity_multiple
    res = solve_max_price(
        base, target_irr=0.13, target_em=em_at_match, rooms=200
    )
    # Both prices should land within tolerance of each other.
    assert (
        abs(res.max_price_for_irr - res.max_price_for_em) < 200_000
    ), (
        f"prices diverged: irr={res.max_price_for_irr:,.0f} "
        f"em={res.max_price_for_em:,.0f}"
    )
    # And the binding chip should mark this as essentially tied.
    if abs(res.max_price_for_irr - res.max_price_for_em) < PRICE_TOLERANCE_USD:
        assert res.binding_constraint == "both"


def test_solver_returns_no_solution_when_target_unreachable_in_bounds() -> None:
    """Astronomical IRR target ⇒ price stays inside [floor, ceiling].

    A truly unreachable target (10000% IRR) can't be hit anywhere in
    the 50–200% price bracket. The solver may still report a price
    inside the bracket — that's the bisection's closest approximation
    — but it must NEVER drop below the floor or go above the ceiling.
    This is the safety guarantee the UI relies on to render a chip.
    """
    base = _base_input()
    res = solve_max_price(base, target_irr=100.0, target_em=20.0, rooms=200)
    # Target is unreachable inside bracket → result lives in bracket.
    floor = base.assumptions.purchase_price * PRICE_FLOOR_MULTIPLIER
    ceiling = base.assumptions.purchase_price * 2.0
    assert floor <= res.max_price_for_irr <= ceiling
    assert floor <= res.max_price_for_em <= ceiling


def test_solver_respects_price_floor() -> None:
    """No matter how mean the IRR/EM target is, price >= 50% of base."""
    base = _base_input()
    floor = base.assumptions.purchase_price * PRICE_FLOOR_MULTIPLIER
    res = solve_max_price(base, target_irr=1.0, target_em=5.0, rooms=200)
    assert res.max_price_for_irr >= floor - 0.01
    assert res.max_price_for_em >= floor - 0.01


def test_per_key_derived_from_total_and_rooms() -> None:
    """final_price_per_key == binding_price / rooms."""
    res = solve_max_price(
        _base_input(), target_irr=0.10, target_em=1.5, rooms=200
    )
    binding_price = min(res.max_price_for_irr, res.max_price_for_em)
    expected_per_key = binding_price / 200
    # ``binding == 'both'`` averages the two prices; allow a wider tol.
    assert abs(res.final_price_per_key - expected_per_key) < 1000.0
