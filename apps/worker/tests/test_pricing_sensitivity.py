"""Tests for the pricing sensitivity grid (Wave 2 P2.8).

Covers the 5x5 grid + breakeven sweep + custom-axis hooks + the
DSCR-floor flag. The endpoint test mounts a temporary FastAPI app
through ASGI so the tenant-scope check + Depends(get_tenant_id) wiring
is exercised without standing up the full worker.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

# Per-test SQLite database BEFORE app modules import.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-pricing-sensitivity.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"


from app.engines.pricing_sensitivity import (  # noqa: E402
    DEFAULT_CAP_DELTAS,
    DEFAULT_NOI_MULTIPLIERS,
    SensitivityGrid,
    run_sensitivity_grid,
)
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402
from fondok_schemas.financial import ModelAssumptions  # noqa: E402


def _base_input(
    *,
    purchase_price: float = 40_000_000.0,
    exit_cap: float = 0.075,
    y1_noi: float = 3_500_000.0,
    hold_years: int = 5,
    annual_debt_service: float = 1_460_000.0,  # ~60% LTV @ 6% on $24M
    equity: float = 16_000_000.0,
    loan_amount: float = 24_000_000.0,
) -> ReturnsEngineInputExt:
    """Build a clean 200-key returns input for grid tests."""
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
        deal_id=UUID("11111111-1111-1111-1111-111111111111"),
        assumptions=assumptions,
        year_one_noi=y1_noi,
        noi_by_year=[y1_noi * (1.03 ** i) for i in range(hold_years)],
        annual_debt_service=annual_debt_service,
        loan_amount=loan_amount,
        loan_balance_at_exit=loan_amount,  # IO assumption simplifies the test
        equity=equity,
    )


# ─────────────────────────── unit tests ─────────────────────────────


def test_5x5_grid_has_25_cells() -> None:
    """Default grid is exactly 5x5 = 25 cells."""
    grid = run_sensitivity_grid(_base_input())
    assert isinstance(grid, SensitivityGrid)
    assert len(grid.cells) == 25
    # And the axis lengths confirm the 5x5 layout via cell counts.
    unique_caps = {c.exit_cap_pct for c in grid.cells}
    unique_nois = {c.noi_multiplier for c in grid.cells}
    assert len(unique_caps) == 5
    assert len(unique_nois) == 5


def test_base_cell_matches_unmutated_returns_engine() -> None:
    """Center cell (base exit cap, NOI multiplier 1.0) reproduces base IRR/EM."""
    base = _base_input()
    grid = run_sensitivity_grid(base)
    base_cell = next(
        c for c in grid.cells
        if abs(c.exit_cap_pct - base.assumptions.exit_cap_rate) < 1e-9
        and abs(c.noi_multiplier - 1.0) < 1e-9
    )
    direct = ReturnsEngine().run(base)
    assert abs(base_cell.levered_irr - direct.levered_irr) < 1e-6
    assert abs(base_cell.equity_multiple - direct.equity_multiple) < 1e-6


def test_higher_cap_rate_lowers_irr() -> None:
    """At fixed NOI multiplier, IRR is monotone-decreasing in exit cap."""
    grid = run_sensitivity_grid(_base_input())
    at_base_noi = sorted(
        [c for c in grid.cells if abs(c.noi_multiplier - 1.0) < 1e-9],
        key=lambda c: c.exit_cap_pct,
    )
    irrs = [c.levered_irr for c in at_base_noi]
    assert irrs == sorted(irrs, reverse=True), (
        f"IRR did not monotonically fall as exit cap rose: {irrs}"
    )


def test_higher_noi_multiplier_raises_irr() -> None:
    """At fixed exit cap, IRR rises with NOI multiplier."""
    grid = run_sensitivity_grid(_base_input())
    base_cap = grid.base_exit_cap_pct
    same_cap = sorted(
        [c for c in grid.cells if abs(c.exit_cap_pct - base_cap) < 1e-9],
        key=lambda c: c.noi_multiplier,
    )
    irrs = [c.levered_irr for c in same_cap]
    assert irrs == sorted(irrs), (
        f"IRR did not monotonically rise with NOI multiplier: {irrs}"
    )


def test_breakeven_cap_found_when_in_range() -> None:
    """Target IRR within the swept window → breakeven cap is returned.

    At base NOI multiplier (1.0) the IRR sweep across exit cap
    ±100bp covers a wide IRR band for the test deal — picking a
    target inside that band (~22% — middle of the band) guarantees
    the linear interpolation lands.
    """
    base = _base_input()
    grid = run_sensitivity_grid(base, target_irr=0.22)
    assert grid.breakeven_exit_cap_pct is not None
    # And the breakeven should land within the swept window.
    assert grid.base_exit_cap_pct - 0.015 < grid.breakeven_exit_cap_pct < (
        grid.base_exit_cap_pct + 0.015
    )


def test_breakeven_cap_none_when_target_unreachable() -> None:
    """Astronomical target IRR → breakeven not findable, returns None."""
    grid = run_sensitivity_grid(_base_input(), target_irr=10.0)
    assert grid.breakeven_exit_cap_pct is None
    assert grid.breakeven_noi_multiplier is None


def test_custom_axes_accepted() -> None:
    """Caller can pass a custom 3-cell cap axis and 4-cell NOI axis."""
    grid = run_sensitivity_grid(
        _base_input(),
        cap_axis=[0.060, 0.065, 0.070],
        noi_axis=[0.9, 1.0, 1.1, 1.2],
    )
    assert len(grid.cells) == 3 * 4
    unique_caps = sorted({c.exit_cap_pct for c in grid.cells})
    assert unique_caps == [0.060, 0.065, 0.070]


def test_grid_is_deterministic() -> None:
    """Same inputs produce the same outputs — no Monte Carlo noise."""
    base = _base_input()
    g1 = run_sensitivity_grid(base)
    g2 = run_sensitivity_grid(base)
    assert len(g1.cells) == len(g2.cells)
    for c1, c2 in zip(g1.cells, g2.cells):
        assert c1.levered_irr == c2.levered_irr
        assert c1.equity_multiple == c2.equity_multiple
        assert c1.exit_cap_pct == c2.exit_cap_pct


def test_grid_handles_dscr_under_1_gracefully() -> None:
    """Cells where DSCR<1 still produce IRR/EM but flag breaches_dscr_floor."""
    # Heavy debt — annual debt service exceeds Y1 NOI even at NOI mult 1.0.
    heavy = _base_input(annual_debt_service=4_000_000.0)
    grid = run_sensitivity_grid(heavy)
    # At least one cell must trip the floor (debt service > Y1 NOI).
    assert any(c.breaches_dscr_floor for c in grid.cells), (
        "expected some cells to breach DSCR floor under heavy debt"
    )
    # And the breaching cells still report numeric IRR / EM (not NaN/None).
    breachers = [c for c in grid.cells if c.breaches_dscr_floor]
    for c in breachers:
        assert isinstance(c.levered_irr, float)
        assert isinstance(c.equity_multiple, float)


@pytest.mark.asyncio
async def test_tenant_scoped_endpoint() -> None:
    """POST /analysis/{deal_id}/pricing/sensitivity rejects cross-tenant deals.

    Also asserts the endpoint actually exists by issuing a request with
    the matching tenant and expecting NOT-404 — a naive test could
    spuriously pass by hitting a non-existent route.
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.main import app
    from app.migrations import run_startup_migrations

    await run_startup_migrations()

    tenant_a = uuid4()
    tenant_b = uuid4()
    deal_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, city, keys, purchase_price, "
                "service, status, deal_stage, risk, ai_confidence, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Test', 'NYC', 200, 40000000, "
                "'Full Service', 'Draft', 'Teaser', 'Medium', 0.8, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(deal_id), "tenant": str(tenant_a)},
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Tenant B tries to read tenant A's deal → 404.
        r_cross = await client.post(
            f"/analysis/{deal_id}/pricing/sensitivity",
            json={"target_irr": 0.15},
            headers={"X-Tenant-Id": str(tenant_b)},
        )
        assert r_cross.status_code == 404, (
            f"expected 404 for cross-tenant request, got {r_cross.status_code} "
            f"body={r_cross.text}"
        )
        # Tenant A on its own deal: must NOT be 404. Returning the grid
        # depends on the engine chain succeeding on the seed assumptions
        # — that's tested elsewhere — so we just rule out a 404 here.
        r_same = await client.post(
            f"/analysis/{deal_id}/pricing/sensitivity",
            json={"target_irr": 0.15},
            headers={"X-Tenant-Id": str(tenant_a)},
        )
        assert r_same.status_code != 404, (
            f"endpoint must not 404 on the owning tenant: status="
            f"{r_same.status_code} body={r_same.text[:300]}"
        )
