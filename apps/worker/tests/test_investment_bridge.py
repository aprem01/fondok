"""R-073 — Investment Bridge: equity invested → equity returned, by leg.

Pins the contract the web waterfall relies on:

* on the full Kimpton chain the five legs (acquisition, renovation,
  operations, financing, exit) plus equity invested sum EXACTLY to the
  equity returned (Σ returns.cash_flows[1:]) — the foot check;
* every leg's components trace to an engine field and sum to the leg;
* a leg whose inputs are missing is ``unavailable`` with a reason and a
  ``None`` value — never zero — and the bridge stops claiming it reconciles;
* the endpoint serves the canonical run and degrades cleanly on a deal that
  has never run.
"""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-investment-bridge.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from app.services.investment_bridge import build_investment_bridge  # noqa: E402

_LEG_ORDER = ["acquisition", "renovation", "operations", "financing", "exit"]


@pytest.fixture(autouse=True)
async def _migrated() -> None:
    from app.migrations import run_startup_migrations

    await run_startup_migrations()


async def _kimpton_rows() -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import get_run_scoped_outputs, run_all_engines

    tenant_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await run_all_engines(
            session,
            deal_id="kimpton-angler-2026",
            tenant_id=tenant_id,
            run_id=str(uuid4()),
        )
        return await get_run_scoped_outputs(
            session, deal_id="kimpton-angler-2026", tenant_id=tenant_id
        )


async def test_kimpton_bridge_bars_sum_to_equity_returned() -> None:
    rows = await _kimpton_rows()
    bridge = build_investment_bridge(rows)

    assert bridge["available"] is True
    assert [leg["key"] for leg in bridge["legs"]] == _LEG_ORDER
    assert all(leg["status"] == "ok" for leg in bridge["legs"]), bridge["legs"]
    assert bridge["unavailable"] == []

    flows = rows["returns"]["outputs"]["cash_flows"]
    equity_invested = -flows[0]
    equity_returned = sum(flows[1:])
    assert bridge["equity_invested"] == pytest.approx(equity_invested)
    assert bridge["equity_returned"] == pytest.approx(equity_returned)
    # Equity invested is the capital engine's equity cheque.
    assert equity_invested == pytest.approx(rows["capital"]["outputs"]["equity_amount"])

    # THE foot check: equity invested + Σ legs == equity returned.
    total = equity_invested + sum(leg["value"] for leg in bridge["legs"])
    assert total == pytest.approx(equity_returned, abs=1.0)
    assert bridge["reconciles"] is True
    assert abs(bridge["residual"]) < 1.0
    assert bridge["equity_profit"] == pytest.approx(equity_returned - equity_invested)


async def test_kimpton_legs_trace_to_engine_fields() -> None:
    rows = await _kimpton_rows()
    bridge = build_investment_bridge(rows)
    legs = {leg["key"]: leg for leg in bridge["legs"]}

    for leg in bridge["legs"]:
        assert leg["components"], leg["key"]
        assert sum(c["value"] for c in leg["components"]) == pytest.approx(leg["value"])
        assert all(c["source"] for c in leg["components"])

    cap = rows["capital"]["outputs"]
    uses = {u["label"]: u["amount"] for u in cap["uses"] if not u.get("is_total")}
    # Acquisition + renovation + loan fee == Total Uses (every line attributed once).
    fee = uses.get("Senior Loan Origination Fee", 0.0)
    assert -(legs["acquisition"]["value"] + legs["renovation"]["value"]) + fee == pytest.approx(
        cap["total_capital"]
    )
    assert legs["renovation"]["value"] == pytest.approx(-uses["Renovation"])

    ret = rows["returns"]["outputs"]
    assert legs["operations"]["value"] == pytest.approx(sum(ret["noi_by_year"]))
    # Exit (before the loan payoff) less the payoff == the engine's net_proceeds.
    payoff = rows["returns"]["inputs"]["loan_balance_at_exit"]
    assert legs["exit"]["value"] - payoff == pytest.approx(ret["net_proceeds"])


async def test_missing_capital_marks_legs_unavailable_never_zero() -> None:
    rows = copy.deepcopy(await _kimpton_rows())
    rows.pop("capital")
    bridge = build_investment_bridge(rows)
    legs = {leg["key"]: leg for leg in bridge["legs"]}
    for key in ("acquisition", "renovation"):
        assert legs[key]["status"] == "unavailable"
        assert legs[key]["value"] is None
        assert legs[key]["reason"]
    assert bridge["reconciles"] is False
    assert bridge["residual"] is None
    assert set(bridge["unavailable"]) == {"acquisition", "renovation"}


async def test_missing_returns_inputs_marks_financing_unavailable() -> None:
    rows = copy.deepcopy(await _kimpton_rows())
    rows["returns"]["inputs"] = None
    bridge = build_investment_bridge(rows)
    fin = next(leg for leg in bridge["legs"] if leg["key"] == "financing")
    assert fin["status"] == "unavailable"
    assert fin["value"] is None
    assert bridge["reconciles"] is False


def test_no_run_is_unavailable() -> None:
    bridge = build_investment_bridge({})
    assert bridge["available"] is False
    assert bridge["equity_invested"] is None
    assert bridge["reason"]
    assert all(leg["value"] is None for leg in bridge["legs"])
    assert bridge["reconciles"] is False


def test_no_renovation_line_is_none_not_zero() -> None:
    rows = {
        "capital": {
            "status": "complete",
            "outputs": {
                "uses": [
                    {"label": "Purchase Price", "amount": 100.0},
                    {"label": "Total Uses", "amount": 100.0, "is_total": True},
                ]
            },
        },
    }
    bridge = build_investment_bridge(rows)
    reno = next(leg for leg in bridge["legs"] if leg["key"] == "renovation")
    assert reno["status"] == "none"
    assert reno["value"] is None
    assert reno["reason"]


async def test_endpoint_degrades_on_a_never_run_deal() -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant = str(uuid4())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/deals",
            json={"name": "Bridge", "city": "Miami", "keys": 120},
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 201, r.text
        deal_id = r.json()["id"]
        r = await client.get(
            f"/deals/{deal_id}/engines/investment-bridge",
            headers={"X-Tenant-Id": tenant},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["available"] is False
        assert body["reconciles"] is False
        assert [leg["key"] for leg in body["legs"]] == _LEG_ORDER
