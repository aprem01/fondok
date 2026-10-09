"""R-051 — Asking vs Bidding price.

* The bid is the EXISTING max-price solver's IRR-constrained price: at the bid
  the returns engine's levered IRR lands on the deal's Target LIRR.
* The asking scenario at the modeled price IS the base case (same IRR as a
  straight engine run) and says so (``asking_is_model_price``).
* No target → ``no_target`` with every bidding number ``None`` — never a
  default hurdle.
* The endpoint reads the deal's ``target_irr``, labels the asking-price
  source, and 200s without a target.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-asking-bid.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.financial import ModelAssumptions  # noqa: E402

from app.engines.asking_bid import NO_TARGET_COPY, solve_asking_and_bid  # noqa: E402
from app.engines.price_solver import IRR_TOLERANCE_PCT, solve_max_price  # noqa: E402
from app.engines.returns import ReturnsEngine, ReturnsEngineInputExt  # noqa: E402


def _base_input() -> ReturnsEngineInputExt:
    price, loan, y1, hold = 40_000_000.0, 24_000_000.0, 3_500_000.0, 5
    return ReturnsEngineInputExt(
        deal_id=UUID("55555555-5555-5555-5555-555555555555"),
        assumptions=ModelAssumptions(
            purchase_price=price,
            ltv=loan / price,
            interest_rate=0.06,
            amortization_years=30,
            loan_term_years=5,
            hold_years=hold,
            exit_cap_rate=0.075,
            revpar_growth=0.03,
            expense_growth=0.03,
            selling_costs_pct=0.02,
            closing_costs_pct=0.02,
        ),
        year_one_noi=y1,
        noi_by_year=[y1 * (1.03**i) for i in range(hold)],
        annual_debt_service=1_460_000.0,
        loan_amount=loan,
        loan_balance_at_exit=loan,
        equity=17_000_000.0,
    )


def test_asking_at_model_price_is_the_base_case() -> None:
    base = _base_input()
    res = solve_asking_and_bid(base, asking_price=40_000_000.0, target_irr=None, rooms=200)
    direct = ReturnsEngine().run(base)
    assert res.asking_is_model_price is True
    assert res.asking.levered_irr == pytest.approx(direct.levered_irr)
    assert res.asking.unlevered_irr == pytest.approx(direct.unlevered_irr)
    assert res.asking.total_capitalization == pytest.approx(base.equity + base.loan_amount)
    assert res.asking.price_per_key == pytest.approx(200_000.0)


def test_no_target_leaves_bidding_empty() -> None:
    res = solve_asking_and_bid(_base_input(), asking_price=40_000_000.0, target_irr=None)
    assert res.bid_status == "no_target"
    assert res.bid_message == NO_TARGET_COPY
    b = res.bidding
    assert all(
        v is None
        for v in (
            b.purchase_price, b.total_capitalization, b.equity,
            b.levered_irr, b.unlevered_irr, b.equity_multiple,
        )
    )
    assert res.bid_vs_asking is None


def test_bid_is_the_solver_price_and_hits_target_irr() -> None:
    base = _base_input()
    target = 0.15
    res = solve_asking_and_bid(base, asking_price=40_000_000.0, target_irr=target)
    solver = solve_max_price(base, target_irr=target, target_em=None)
    assert res.bid_status == "converged"
    assert res.bidding.purchase_price == pytest.approx(solver.max_price_for_irr)
    # Solved backward: at the bid the engine's levered IRR is the target.
    assert res.bidding.levered_irr == pytest.approx(target, abs=2 * IRR_TOLERANCE_PCT + 0.002)
    # Capitalization identity of the solver: loan fixed, equity absorbs Δprice.
    delta = res.bidding.purchase_price - 40_000_000.0
    assert res.bidding.total_capitalization == pytest.approx(
        base.equity + base.loan_amount + delta
    )
    assert res.bid_vs_asking == pytest.approx(delta)


def test_higher_target_means_lower_bid() -> None:
    base = _base_input()
    lo = solve_asking_and_bid(base, asking_price=40e6, target_irr=0.12)
    hi = solve_asking_and_bid(base, asking_price=40e6, target_irr=0.18)
    assert lo.bid_status == hi.bid_status == "converged"
    assert hi.bidding.purchase_price < lo.bidding.purchase_price


def test_out_of_bracket_target_reports_status_not_a_price() -> None:
    # A hurdle that clears even at 2x the modeled price: the solver's bracket
    # end must never be dressed up as a bid.
    res = solve_asking_and_bid(_base_input(), asking_price=40e6, target_irr=-0.4)
    assert res.bid_status == "above_ceiling"
    assert res.bidding.purchase_price is None
    assert res.bid_message


def test_asking_off_model_price_is_repriced() -> None:
    base = _base_input()
    res = solve_asking_and_bid(base, asking_price=42_000_000.0, target_irr=None)
    assert res.asking_is_model_price is False
    assert res.asking.purchase_price == 42_000_000.0
    assert res.asking.levered_irr < ReturnsEngine().run(base).levered_irr


# ─────────────────────────── endpoint ────────────────────────────────


@pytest.fixture(autouse=True)
async def _migrated() -> None:
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create(client, tenant: str, **extra) -> str:
    r = await client.post(
        "/deals",
        json={"name": "Asking", "city": "Miami", "keys": 132, **extra},
        headers={"X-Tenant-Id": tenant},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_endpoint_solves_bid_from_deal_target() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal_id = await _create(client, tenant, target_irr=0.15, purchase_price=36_400_000)
        r = await client.get(
            f"/analysis/{deal_id}/pricing/asking-vs-bid", headers={"X-Tenant-Id": tenant}
        )
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["target_irr"] == 0.15
    # No OM extracted → the asking column is the modeled price, labelled.
    assert body["om_asking_price"] is None
    assert body["asking_price_source"] == "model_purchase_price"
    assert body["asking_price_label"] == "Deal record purchase price"
    assert body["asking_is_model_price"] is True
    assert body["asking"]["purchase_price"] == pytest.approx(36_400_000)
    assert body["asking"]["renovation"] is not None
    assert body["bid_status"] in {"converged", "unreachable", "above_ceiling"}
    if body["bid_status"] == "converged":
        assert body["bidding"]["levered_irr"] == pytest.approx(0.15, abs=0.003)
        assert body["bidding"]["renovation"] == body["asking"]["renovation"]


async def test_endpoint_without_target_returns_no_target() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal_id = await _create(client, tenant)
        r = await client.get(
            f"/analysis/{deal_id}/pricing/asking-vs-bid", headers={"X-Tenant-Id": tenant}
        )
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["target_irr"] is None
    assert body["bid_status"] == "no_target"
    assert body["bid_message"] == NO_TARGET_COPY
    assert body["bidding"]["purchase_price"] is None
    assert body["bidding"]["levered_irr"] is None
    assert body["bidding"]["renovation"] is None
    assert body["asking"]["levered_irr"] is not None
