"""FON-68 — return targets live on the deal; the Pricing endpoints read them.

API-level contract:

* ``POST/PATCH/GET /deals`` round-trip ``target_irr`` + ``target_moic``
  (null = unset).
* ``POST /analysis/{id}/pricing/max-price`` with an empty body reads the
  deal's targets; explicit body values still win; with neither → 422
  carrying the canonical "No return target set — …" copy, never 15%/1.8x.
* ``POST /analysis/{id}/pricing/max-price-grid`` returns ≤ 25 cells, each
  the lower of the IRR-/MOIC-solved prices with the binding constraint,
  and 422s on an unset target or an oversized grid.

The engine chain is monkeypatched to a deterministic returns input so
these tests pin the endpoint contract, not the chain's seed assumptions.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import UUID, uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-pricing-targets.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.engines.price_solver import NO_TARGET_MESSAGE  # noqa: E402
from app.engines.returns import ReturnsEngineInputExt  # noqa: E402
from fondok_schemas.financial import ModelAssumptions  # noqa: E402


def _base_input() -> ReturnsEngineInputExt:
    purchase_price, loan_amount, y1, hold = 40_000_000.0, 24_000_000.0, 3_500_000.0, 5
    assumptions = ModelAssumptions(
        purchase_price=purchase_price,
        ltv=loan_amount / purchase_price,
        interest_rate=0.06,
        amortization_years=30,
        loan_term_years=5,
        hold_years=hold,
        exit_cap_rate=0.075,
        revpar_growth=0.03,
        expense_growth=0.03,
        selling_costs_pct=0.02,
        closing_costs_pct=0.02,
    )
    return ReturnsEngineInputExt(
        deal_id=UUID("44444444-4444-4444-4444-444444444444"),
        assumptions=assumptions,
        year_one_noi=y1,
        noi_by_year=[y1 * (1.03**i) for i in range(hold)],
        annual_debt_service=1_460_000.0,
        loan_amount=loan_amount,
        loan_balance_at_exit=loan_amount,
        equity=16_000_000.0,
    )


@pytest.fixture(autouse=True)
async def _migrated() -> None:
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


@pytest.fixture
def _fixed_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the pricing endpoints' returns input to a deterministic fixture."""
    import app.api.analysis as analysis

    async def _fake(session, *, deal_id, tenant_id):  # noqa: ANN001
        return _base_input()

    monkeypatch.setattr(analysis, "_build_returns_input_for_deal", _fake)


def _client():
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _create_deal(client, tenant: str, **extra):  # noqa: ANN001
    r = await client.post(
        "/deals",
        json={"name": "Targets", "city": "Miami", "keys": 200, **extra},
        headers={"X-Tenant-Id": tenant},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ─────────────────────────── deal targets round-trip ─────────────────


async def test_deal_targets_round_trip() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        created = await _create_deal(client, tenant, target_irr=0.15, target_moic=1.8)
        assert created["target_irr"] == 0.15
        assert created["target_moic"] == 1.8

        r = await client.get(f"/deals/{created['id']}", headers={"X-Tenant-Id": tenant})
        assert r.status_code == 200, r.text
        assert r.json()["target_irr"] == 0.15
        assert r.json()["target_moic"] == 1.8

        # PATCH one, clear the other (null = unset).
        r = await client.patch(
            f"/deals/{created['id']}",
            json={"target_irr": 0.17, "target_moic": None},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        assert r.json()["target_irr"] == 0.17
        assert r.json()["target_moic"] is None

        r = await client.get(f"/deals/{created['id']}", headers={"X-Tenant-Id": tenant})
        assert r.json()["target_irr"] == 0.17
        assert r.json()["target_moic"] is None

        # A deal created without targets reports both unset.
        bare = await _create_deal(client, tenant)
        assert bare["target_irr"] is None and bare["target_moic"] is None


async def test_deal_target_moic_is_range_checked() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        created = await _create_deal(client, tenant)
        r = await client.patch(
            f"/deals/{created['id']}",
            json={"target_moic": -1.0},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 422


# ─────────────────────────── max-price target resolution ─────────────


@pytest.mark.usefixtures("_fixed_chain")
async def test_max_price_reads_deal_targets_when_body_omits_them() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal = await _create_deal(client, tenant, target_irr=0.12, target_moic=1.5)
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price",
            json={},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_irr"] == 0.12
        assert body["target_em"] == 1.5
        assert body["target_source"] == "deal"
        assert body["irr_status"] == "converged" and body["em_status"] == "converged"
        assert body["max_price"] == min(body["max_price_for_irr"], body["max_price_for_em"]) or (
            body["binding_constraint"] == "both"
        )
        assert body["binding_constraint"] in ("irr", "em", "both")
        assert body["base_purchase_price"] == 40_000_000.0
        assert body["rooms"] == 200
        assert body["exit_cap_rate"] == 0.075
        assert body["ltv"] == pytest.approx(0.6)
        assert body["interest_rate"] == 0.06
        assert body["hold_years"] == 5.0
        assert body["final_price_per_key"] == pytest.approx(body["max_price"] / 200)


@pytest.mark.usefixtures("_fixed_chain")
async def test_max_price_explicit_body_wins_over_deal() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal = await _create_deal(client, tenant, target_irr=0.12, target_moic=1.5)
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price",
            json={"target_irr": 0.18, "target_em": 1.2},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_irr"] == 0.18 and body["target_em"] == 1.2
        assert body["target_source"] == "request"
        # 18% IRR is the tighter hurdle vs a 1.2x multiple.
        assert body["binding_constraint"] == "irr"

        # One from the body, one from the deal.
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price",
            json={"target_irr": 0.10},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        assert r.json()["target_source"] == "mixed"
        assert r.json()["target_em"] == 1.5


@pytest.mark.usefixtures("_fixed_chain")
async def test_max_price_single_deal_target_solves_one_constraint() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal = await _create_deal(client, tenant, target_irr=0.12)
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price",
            json={},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_em"] is None
        assert body["em_status"] == "not_requested"
        assert body["max_price_for_em"] is None
        assert body["binding_constraint"] == "irr"
        assert body["max_price"] == body["max_price_for_irr"]


@pytest.mark.usefixtures("_fixed_chain")
async def test_max_price_422_when_no_target_anywhere() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal = await _create_deal(client, tenant)
        for path in ("pricing/max-price", "pricing/max-price-grid"):
            r = await client.post(
                f"/analysis/{deal['id']}/{path}",
                json={},
                headers={"X-Tenant-Id": tenant},
            )
            assert r.status_code == 422, r.text
            assert r.json()["detail"] == NO_TARGET_MESSAGE
            assert "max_price" not in r.text  # no numbers ride along


@pytest.mark.usefixtures("_fixed_chain")
async def test_max_price_is_tenant_scoped() -> None:
    tenant, other = str(uuid4()), str(uuid4())
    async with _client() as client:
        deal = await _create_deal(client, tenant, target_irr=0.12, target_moic=1.5)
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price",
            json={},
            headers={"X-Tenant-Id": other},
        )
        assert r.status_code == 404


# ─────────────────────────── max-price grid ───────────────────────────


@pytest.mark.usefixtures("_fixed_chain")
async def test_grid_shape_lower_of_and_binding() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal = await _create_deal(client, tenant, target_irr=0.12, target_moic=1.5)
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price-grid",
            json={},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["target_source"] == "deal"
        assert body["target_irr"] == 0.12 and body["target_em"] == 1.5
        assert body["cap_axis"] == pytest.approx([0.065, 0.07, 0.075, 0.08, 0.085])
        assert body["noi_growth_axis"] == pytest.approx([0.01, 0.02, 0.03, 0.04, 0.05])
        assert body["base_exit_cap_pct"] == 0.075
        assert body["base_noi_growth_pct"] == 0.03
        assert body["rooms"] == 200
        cells = body["cells"]
        assert len(cells) == 25
        assert sum(1 for c in cells if c["is_base"]) == 1
        for c in cells:
            assert c["irr_status"] == "converged" and c["em_status"] == "converged"
            if c["binding_constraint"] == "both":
                assert abs(c["max_price_for_irr"] - c["max_price_for_em"]) < 50_000
            else:
                assert c["max_price"] == min(c["max_price_for_irr"], c["max_price_for_em"])
                expected = "irr" if c["max_price_for_irr"] < c["max_price_for_em"] else "em"
                assert c["binding_constraint"] == expected
            assert c["price_per_key"] == pytest.approx(c["max_price"] / 200)

        # The base cell matches the headline solve from /pricing/max-price.
        head = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price",
            json={},
            headers={"X-Tenant-Id": tenant},
        )
        base_cell = next(c for c in cells if c["is_base"])
        assert base_cell["max_price"] == head.json()["max_price"]
        assert base_cell["binding_constraint"] == head.json()["binding_constraint"]


@pytest.mark.usefixtures("_fixed_chain")
async def test_grid_explicit_axes_and_cell_cap() -> None:
    tenant = str(uuid4())
    async with _client() as client:
        deal = await _create_deal(client, tenant, target_irr=0.12, target_moic=1.5)
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price-grid",
            json={"cap_axis": [0.07, 0.08], "noi_growth_axis": [0.02, 0.03, 0.04]},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        assert len(r.json()["cells"]) == 6
        # More than 5 on an axis is rejected before any solve runs.
        r = await client.post(
            f"/analysis/{deal['id']}/pricing/max-price-grid",
            json={"cap_axis": [0.06, 0.065, 0.07, 0.075, 0.08, 0.085]},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 422
