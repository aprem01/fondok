"""FON-68 — max-price grid engine + solver hurdle semantics.

Pins the Pricing sub-tab's contract:

* the solver has NO default hurdle (both targets ``None`` raises),
* a single hurdle solves a single constraint,
* an unreachable / always-cleared hurdle is reported via ``*_status`` and a
  ``None`` headline — never as the bracket endpoint dressed as a price,
* the grid is exit cap × NOI growth, ≤ 25 cells, every cell = lower of the
  independently solved IRR / MOIC prices with the binding constraint
  named, and the base cell reproduces the headline solve exactly.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-max-price-grid.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.engines.max_price_grid import (  # noqa: E402
    MAX_CELLS,
    TOO_MANY_CELLS_MESSAGE,
    flex_returns_input,
    run_max_price_grid,
)
from app.engines.price_solver import (  # noqa: E402
    NO_TARGET_MESSAGE,
    PRICE_FLOOR_MULTIPLIER,
    solve_max_price,
)
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402
from fondok_schemas.financial import ModelAssumptions  # noqa: E402


def _base_input(
    *,
    purchase_price: float = 40_000_000.0,
    y1_noi: float = 3_500_000.0,
    exit_cap: float = 0.075,
    growth: float = 0.03,
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
        revpar_growth=growth,
        expense_growth=0.03,
        selling_costs_pct=0.02,
        closing_costs_pct=0.02,
    )
    return ReturnsEngineInputExt(
        deal_id=UUID("33333333-3333-3333-3333-333333333333"),
        assumptions=assumptions,
        year_one_noi=y1_noi,
        noi_by_year=[y1_noi * ((1 + growth) ** i) for i in range(hold_years)],
        annual_debt_service=annual_debt_service,
        loan_amount=loan_amount,
        loan_balance_at_exit=loan_amount,
        equity=equity,
    )


# ─────────────────────────── solver hurdle semantics ─────────────────


def test_solver_refuses_to_invent_a_hurdle() -> None:
    with pytest.raises(ValueError) as exc:
        solve_max_price(_base_input(), target_irr=None, target_em=None)
    assert str(exc.value) == NO_TARGET_MESSAGE


def test_solver_single_irr_target() -> None:
    res = solve_max_price(_base_input(), target_irr=0.12, target_em=None, rooms=200)
    assert res.em_status == "not_requested"
    assert res.max_price_for_em is None
    assert res.irr_status == "converged"
    assert res.binding_constraint == "irr"
    assert res.max_price == res.max_price_for_irr
    assert res.max_price is not None and res.max_price > 0


def test_solver_single_em_target() -> None:
    res = solve_max_price(_base_input(), target_irr=None, target_em=1.5, rooms=200)
    assert res.irr_status == "not_requested"
    assert res.max_price_for_irr is None
    assert res.binding_constraint == "em"
    assert res.max_price == res.max_price_for_em


def test_solver_headline_is_lower_of_both() -> None:
    res = solve_max_price(_base_input(), target_irr=0.12, target_em=1.5, rooms=200)
    assert res.irr_status == "converged" and res.em_status == "converged"
    assert res.max_price_for_irr is not None and res.max_price_for_em is not None
    if res.binding_constraint == "both":
        assert res.max_price == pytest.approx(
            (res.max_price_for_irr + res.max_price_for_em) / 2
        )
    else:
        assert res.max_price == min(res.max_price_for_irr, res.max_price_for_em)
        expected = "irr" if res.max_price_for_irr < res.max_price_for_em else "em"
        assert res.binding_constraint == expected


def test_unreachable_hurdle_has_no_headline_price() -> None:
    """A 100× IRR hurdle can't be cleared at any price ≥ the 50% floor."""
    base = _base_input()
    res = solve_max_price(base, target_irr=100.0, target_em=1.5, rooms=200)
    assert res.irr_status == "unreachable"
    assert res.binding_constraint == "irr"
    assert res.max_price is None
    # The floor-clamped raw price is still inside the bracket (legacy contract).
    floor = base.assumptions.purchase_price * PRICE_FLOOR_MULTIPLIER
    assert res.max_price_for_irr == pytest.approx(floor)


def test_hurdle_cleared_even_at_ceiling_is_above_ceiling() -> None:
    """A negative-IRR hurdle clears at 2× the basis → status above_ceiling."""
    res = solve_max_price(_base_input(), target_irr=-0.4, target_em=None, rooms=200)
    assert res.irr_status == "above_ceiling"
    assert res.max_price is None


# ─────────────────────────── grid ─────────────────────────────────────


def test_default_grid_is_5x5_over_exit_cap_and_growth() -> None:
    base = _base_input()
    grid = run_max_price_grid(base, target_irr=0.12, target_em=1.5, rooms=200)
    assert len(grid.cells) == 25 == MAX_CELLS
    assert grid.cap_axis == pytest.approx([0.065, 0.07, 0.075, 0.08, 0.085])
    assert grid.noi_growth_axis == pytest.approx([0.01, 0.02, 0.03, 0.04, 0.05])
    assert grid.base_exit_cap_pct == 0.075
    assert grid.base_noi_growth_pct == 0.03
    assert grid.base_purchase_price == 40_000_000.0
    # Row-major: outer exit cap ascending, inner growth ascending.
    assert [c.exit_cap_pct for c in grid.cells[:5]] == pytest.approx([0.065] * 5)
    assert [c.noi_growth_pct for c in grid.cells[:5]] == pytest.approx(
        [0.01, 0.02, 0.03, 0.04, 0.05]
    )
    assert sum(1 for c in grid.cells if c.is_base) == 1


def test_every_cell_is_lower_of_with_binding_named() -> None:
    grid = run_max_price_grid(_base_input(), target_irr=0.12, target_em=1.5, rooms=200)
    for c in grid.cells:
        assert c.irr_status == "converged" and c.em_status == "converged"
        assert c.max_price_for_irr is not None and c.max_price_for_em is not None
        if c.binding_constraint == "both":
            assert abs(c.max_price_for_irr - c.max_price_for_em) < 50_000
        else:
            assert c.max_price == min(c.max_price_for_irr, c.max_price_for_em)
            expected = "irr" if c.max_price_for_irr < c.max_price_for_em else "em"
            assert c.binding_constraint == expected
        assert c.price_per_key == pytest.approx(c.max_price / 200)


def test_base_cell_reproduces_headline_solve() -> None:
    base = _base_input()
    headline = solve_max_price(base, target_irr=0.12, target_em=1.5, rooms=200)
    grid = run_max_price_grid(base, target_irr=0.12, target_em=1.5, rooms=200)
    base_cell = next(c for c in grid.cells if c.is_base)
    assert base_cell.max_price_for_irr == headline.max_price_for_irr
    assert base_cell.max_price_for_em == headline.max_price_for_em
    assert base_cell.max_price == headline.max_price
    assert base_cell.binding_constraint == headline.binding_constraint


def test_higher_exit_cap_lowers_max_price_and_higher_growth_raises_it() -> None:
    grid = run_max_price_grid(_base_input(), target_irr=0.12, target_em=1.5, rooms=200)
    by = {(round(c.exit_cap_pct, 4), round(c.noi_growth_pct, 4)): c for c in grid.cells}
    # Along the base growth column, price falls as the exit cap rises.
    col = [by[(cap, 0.03)].max_price for cap in (0.065, 0.07, 0.075, 0.08, 0.085)]
    assert all(a > b for a, b in zip(col, col[1:])), col
    # Along the base cap row, price rises with NOI growth.
    row = [by[(0.075, g)].max_price for g in (0.01, 0.02, 0.03, 0.04, 0.05)]
    assert all(a < b for a, b in zip(row, row[1:])), row


def test_growth_flex_leaves_year_one_untouched_and_retilts_series() -> None:
    base = _base_input(growth=0.03)
    flexed = flex_returns_input(base, exit_cap_pct=0.07, noi_growth_pct=0.05)
    assert flexed.noi_by_year[0] == base.noi_by_year[0]
    assert flexed.assumptions.exit_cap_rate == 0.07
    assert flexed.assumptions.revpar_growth == 0.05
    ratio = 1.05 / 1.03
    for i, (a, b) in enumerate(zip(base.noi_by_year, flexed.noi_by_year)):
        assert b == pytest.approx(a * ratio**i)
    # The base combination is an identity flex.
    same = flex_returns_input(base, exit_cap_pct=0.075, noi_growth_pct=0.03)
    assert same.noi_by_year == pytest.approx(base.noi_by_year)
    assert ReturnsEngine().run(same).levered_irr == pytest.approx(
        ReturnsEngine().run(base).levered_irr
    )


def test_grid_caps_at_25_cells() -> None:
    with pytest.raises(ValueError) as exc:
        run_max_price_grid(
            _base_input(),
            target_irr=0.12,
            target_em=1.5,
            cap_axis=[0.06, 0.065, 0.07, 0.075, 0.08, 0.085],
            noi_growth_axis=[0.01, 0.02, 0.03, 0.04, 0.05],
        )
    assert str(exc.value) == TOO_MANY_CELLS_MESSAGE


def test_grid_refuses_without_a_target() -> None:
    with pytest.raises(ValueError) as exc:
        run_max_price_grid(_base_input(), target_irr=None, target_em=None)
    assert str(exc.value) == NO_TARGET_MESSAGE


def test_grid_single_hurdle_marks_other_not_requested() -> None:
    grid = run_max_price_grid(
        _base_input(), target_irr=0.12, target_em=None,
        cap_axis=[0.07, 0.075], noi_growth_axis=[0.03],
    )
    assert len(grid.cells) == 2
    for c in grid.cells:
        assert c.em_status == "not_requested"
        assert c.max_price_for_em is None
        assert c.binding_constraint == "irr"
        assert c.max_price == c.max_price_for_irr
