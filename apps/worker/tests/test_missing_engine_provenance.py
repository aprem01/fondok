"""Provenance-state fan-out for the four engines that shipped without a
per-value sidecar (FON-65 gap-fill): capital, partnership, fb, sensitivity.

Companion to test_engine_provenance.py (expense / debt / returns). These lock
the *mechanism* on the remaining engines so the tabs' Data Key dots become
truly source-derived, not fallback:

  * each engine emits a non-empty ``provenance`` sidecar with ``state`` set on
    every trace;
  * every trace's value equals the actual output field;
  * the states are the ones the classifier should derive (calculated for the
    formula-computed figures, assumption for analyst inputs, linked for the
    F&B lines pulled straight from the Revenue engine);
  * a full-chain run persists those sidecars and GET /provenance surfaces them.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Per-file SQLite DB, set BEFORE any app module imports so the cached Settings
# pick up the right DSN (mirrors test_fon73_run_scoped_readers.py).
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-missing-prov.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from fondok_schemas.financial import ModelAssumptions  # noqa: E402
from fondok_schemas.partnership import WaterfallTier  # noqa: E402
from fondok_schemas.underwriting import (  # noqa: E402
    RevenueEngineOutput,
    RevenueProjectionYear,
)

from app.engines.capital import CapitalEngine, CapitalEngineInput  # noqa: E402
from app.engines.fb_revenue import FBRevenueEngine, FBRevenueInput  # noqa: E402
from app.engines.partnership import (  # noqa: E402
    PartnershipEngine,
    PartnershipInputExt,
)
from app.engines.returns import ReturnsEngineInputExt  # noqa: E402
from app.engines.sensitivity import SensitivityEngine, SensitivityInput  # noqa: E402

TOL = 0.5  # dollars — large-magnitude values

# A ``traces_to`` whose first dotted segment is a known engine name is a
# legitimate CROSS-engine link (e.g. "revenue.years[0].fb_revenue"); it need
# not resolve inside the same engine's map.
_ENGINES = {
    "revenue", "fb", "expense", "capital", "debt", "returns",
    "sensitivity", "partnership", "cash_flow",
}


def _assert_no_dangling(prov: dict) -> None:
    """Same-engine ``traces_to`` edges must resolve; cross-engine ones are OK."""
    keys = set(prov)
    for trace in prov.values():
        for inp in trace.inputs:
            ref = inp.traces_to
            if ref is None:
                continue
            head = ref.split(".", 1)[0].split("[", 1)[0]
            if head in _ENGINES:
                continue  # cross-engine link — resolves in another map
            assert ref in keys, f"dangling traces_to: {ref}"


def _all_states_set(prov: dict) -> None:
    assert prov, "engine emitted no provenance"
    for key, trace in prov.items():
        assert trace.state is not None, f"{key} has no state"


# ─────────────────────────────── Capital ─────────────────────────────────


def _capital_input() -> CapitalEngineInput:
    return CapitalEngineInput(
        deal_id=uuid4(),
        purchase_price=36_436_802,
        keys=132,
        ltv=0.65,
        renovation_budget=5_000_000,
        working_capital=1_000_000,
    )


def test_capital_tags_state_and_reconciles() -> None:
    out = CapitalEngine().run(_capital_input())
    prov = out.provenance
    _all_states_set(prov)
    _assert_no_dangling(prov)

    # Headline sized/derived figures read as calculated and reconcile.
    for key, field in (
        ("debt_amount", out.debt_amount),
        ("equity_amount", out.equity_amount),
        ("ltc", out.ltc),
        ("total_capital", out.total_capital),
        ("price_per_key", out.price_per_key),
        ("property_uses_usd", out.property_uses_usd),
        ("senior_loan_fee_usd", out.senior_loan_fee_usd),
    ):
        assert key in prov, f"missing {key}"
        assert abs(prov[key].value - field) < TOL
        assert prov[key].state == "calculated", (key, prov[key].state)

    # Renovation breakdown split is calculated.
    assert prov["renovation_breakdown.hard"].state == "calculated"
    assert abs(prov["renovation_breakdown.hard"].value - out.renovation_breakdown.hard) < TOL

    # The purchase price (an analyst / OM input) reads as an assumption.
    assert prov["purchase_price"].state == "assumption"
    assert abs(prov["purchase_price"].value - out.uses[0].amount) < TOL

    # equity = total_uses − debt reconciles through the declared inputs.
    eq = {i.name: i.value for i in prov["equity_amount"].inputs}
    assert abs(eq["total_uses"] - eq["debt_amount"] - prov["equity_amount"].value) < TOL


def test_capital_explicit_senior_loan_is_assumption() -> None:
    """An analyst-confirmed senior loan (FON-67 reconciliation) is a sourced
    assumption, not a formula-sized figure."""
    out = CapitalEngine().run(
        CapitalEngineInput(
            deal_id=uuid4(), purchase_price=36_436_802, keys=132,
            senior_loan_amount=23_187_000,
        )
    )
    assert out.provenance["debt_amount"].state == "assumption"
    assert out.provenance["debt_amount"].value == 23_187_000


# ─────────────────────────────────── F&B ─────────────────────────────────


def _fb_input() -> FBRevenueInput:
    rev = RevenueEngineOutput(
        deal_id=uuid4(),
        total_revenue_cagr=0.05,
        years=[
            # Y1 grounded (T-12 anchored F&B / other present).
            RevenueProjectionYear(
                year=1, occupancy=0.70, adr=200, revpar=140,
                rooms_revenue=10_000_000, fb_revenue=2_400_000,
                resort_fees=500_000, other_revenue=600_000,
                total_revenue=13_500_000,
            ),
            # Y2 ungrounded (no F&B / other actual) → ratio estimate.
            RevenueProjectionYear(
                year=2, occupancy=0.72, adr=206, revpar=148,
                rooms_revenue=10_600_000, fb_revenue=0.0,
                resort_fees=0.0, other_revenue=0.0, total_revenue=10_600_000,
            ),
        ],
    )
    return FBRevenueInput(deal_id=uuid4(), revenue=rev, hotel_type="full")


def test_fb_tags_linked_and_calculated() -> None:
    out = FBRevenueEngine().run(_fb_input())
    prov = out.provenance
    _all_states_set(prov)
    _assert_no_dangling(prov)

    # Grounded pass-throughs from the Revenue engine read as ``linked``.
    assert prov["years[0].fb_revenue"].state == "linked"
    assert prov["years[0].other_revenue"].state == "linked"
    assert prov["years[0].rooms_revenue"].state == "linked"
    assert prov["years[0].resort_fees"].state == "linked"

    # Ungrounded ratio estimates read as ``calculated``.
    assert prov["years[1].fb_revenue"].state == "calculated"
    assert prov["years[1].other_revenue"].state == "calculated"

    # The year total is composed by this engine → calculated, and reconciles.
    for i, yr in enumerate(out.years):
        tr = prov[f"years[{i}].total_revenue"]
        assert tr.state == "calculated"
        assert abs(tr.value - yr.total_revenue) < TOL
        parts = {inp.name: inp.value for inp in tr.inputs}
        assert abs(
            parts["rooms_revenue"] + parts["fb_revenue"]
            + parts["resort_fees"] + parts["other_revenue"] - tr.value
        ) < TOL

    # The linked lines carry a cross-engine edge back to the Revenue map.
    fb0 = prov["years[0].fb_revenue"]
    assert fb0.inputs[0].traces_to == "revenue.years[0].fb_revenue"


# ────────────────────────────── Partnership ──────────────────────────────


_WF = [
    WaterfallTier(label="Pref 8%", hurdle_rate=0.08, gp_split=0.0, lp_split=1.0),
    WaterfallTier(label="Promote 20%", hurdle_rate=0.15, gp_split=0.2, lp_split=0.8),
]


def _partnership_annual() -> PartnershipInputExt:
    return PartnershipInputExt(
        deal_id=uuid4(), total_equity=11_000_000,
        gp_equity_pct=0.10, lp_equity_pct=0.90, waterfall=_WF,
        cash_flows=[1_000_000, 1_200_000, 1_400_000, 1_600_000, 20_000_000],
    )


def _partnership_monthly() -> PartnershipInputExt:
    return PartnershipInputExt(
        deal_id=uuid4(), total_equity=11_000_000,
        gp_equity_pct=0.10, lp_equity_pct=0.90, waterfall=_WF,
        cash_flows=[1],  # unused on the monthly path
        period="monthly",
        cash_flows_monthly=[-11_000_000] + [100_000] * 51 + [20_000_000],
        close_date="2026-01-31",
    )


def _assert_partnership_prov(out) -> None:
    prov = out.provenance
    _all_states_set(prov)
    _assert_no_dangling(prov)
    # Every partnership figure is computed → calculated.
    assert all(t.state == "calculated" for t in prov.values())

    for key, field in (
        ("gp.irr", out.gp.irr),
        ("lp.irr", out.lp.irr),
        ("gp.equity_multiple", out.gp.equity_multiple),
        ("lp.equity_multiple", out.lp.equity_multiple),
        ("gp.contributed_equity", out.gp.contributed_equity),
        ("lp.contributed_equity", out.lp.contributed_equity),
        ("gp.distributions", out.gp.distributions),
        ("lp.distributions", out.lp.distributions),
        ("promote_earned", out.promote_earned),
        ("total_distributable", out.total_distributable),
    ):
        assert key in prov, f"missing {key}"
        assert abs(prov[key].value - field) < TOL, key

    # Every dollar-waterfall tier row is traced.
    for i, tier in enumerate(out.tier_allocations):
        assert abs(prov[f"tier_allocations[{i}].total_amount"].value - tier.total_amount) < TOL

    # equity_multiple chains to distributions ÷ contributed within the map.
    em = prov["gp.equity_multiple"]
    names = {inp.name: inp.traces_to for inp in em.inputs}
    assert names["gp_distributions"] == "gp.distributions"
    assert names["gp_contributed_equity"] == "gp.contributed_equity"


def test_partnership_annual_tags_state() -> None:
    _assert_partnership_prov(PartnershipEngine().run(_partnership_annual()))


def test_partnership_monthly_tags_state() -> None:
    out = PartnershipEngine().run(_partnership_monthly())
    _assert_partnership_prov(out)
    # The monthly IRR trace lists the full monthly flow stream it solved over.
    assert len(out.provenance["gp.irr"].inputs) == len(out.gp_cash_flows)


# ────────────────────────────── Sensitivity ──────────────────────────────


def _sensitivity_input() -> SensitivityInput:
    base = ReturnsEngineInputExt(
        deal_id=uuid4(),
        assumptions=ModelAssumptions(
            purchase_price=30_000_000, ltv=0.65, interest_rate=0.06,
            amortization_years=30, loan_term_years=5, hold_years=5,
            exit_cap_rate=0.075, revpar_growth=0.03, expense_growth=0.03,
            selling_costs_pct=0.02, closing_costs_pct=0.02,
        ),
        year_one_noi=2_400_000, annual_debt_service=1_500_000,
        loan_amount=19_500_000, loan_balance_at_exit=18_000_000,
        equity=11_000_000,
    )
    return SensitivityInput(
        deal_id=uuid4(), base_returns_input=base,
        row_variable="exit_cap_rate", row_values=[0.07, 0.075, 0.08],
        col_variable="revpar_growth", col_values=[0.02, 0.03, 0.04],
        metric="levered_irr",
    )


def test_sensitivity_grid_cells_tagged_calculated() -> None:
    out = SensitivityEngine().run(_sensitivity_input())
    prov = out.provenance
    _all_states_set(prov)
    _assert_no_dangling(prov)
    # Every cell is a Returns-engine re-run → calculated.
    assert all(t.state == "calculated" for t in prov.values())

    # Primary grid cells are traced and reconcile.
    for j, cell in enumerate(out.cells):
        tr = prov[f"cells[{j}].value"]
        assert abs(tr.value - cell.value) < 1e-9
        vals = {inp.name: inp.value for inp in tr.inputs}
        assert abs(vals["exit_cap_rate"] - cell.row_value) < 1e-9
        assert abs(vals["revpar_growth"] - cell.col_value) < 1e-9

    # Every named matrix's cells are traced too.
    for m, matrix in enumerate(out.matrices):
        for j, cell in enumerate(matrix.cells):
            assert abs(prov[f"matrices[{m}].cells[{j}].value"].value - cell.value) < 1e-9


# ─────────────────── End-to-end: GET /provenance surfaces them ────────────


async def _seed_deal(session, *, deal_id: str, tenant_id: str) -> None:
    from sqlalchemy import text

    await session.execute(
        text(
            "INSERT INTO deals (id, tenant_id, name, field_overrides) "
            "VALUES (:id, :tenant, :name, :fo)"
        ),
        {"id": deal_id, "tenant": tenant_id, "name": "Prov Test", "fo": "{}"},
    )


@pytest.mark.asyncio
async def test_provenance_endpoint_surfaces_four_engines_with_state() -> None:
    """A full-chain run persists the four new sidecars, and the provenance
    endpoint surfaces them with ``state`` set on every trace."""
    from sqlalchemy import text

    from app.api.deals import get_deal_provenance
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.services.engine_runner import run_all_engines

    await run_startup_migrations()
    deal_uuid = uuid4()
    tenant_id = uuid4()

    factory = get_session_factory()
    async with factory() as session:
        for tbl in ("engine_outputs", "scenarios", "deals"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()

        await _seed_deal(session, deal_id=str(deal_uuid), tenant_id=str(tenant_id))
        await run_all_engines(
            session, deal_id=str(deal_uuid), tenant_id=str(tenant_id),
            run_id=str(uuid4()),
        )
        await session.commit()

        resp = await get_deal_provenance(
            deal_id=deal_uuid, session=session, tenant_id=tenant_id
        )

    engines = resp.engines
    # One representative key per newly-instrumented engine, and every trace in
    # each engine's map must carry a state.
    expected = {
        "capital": "debt_amount",
        "partnership": "gp.irr",
        "fb": "years[0].total_revenue",
        "sensitivity": "cells[0].value",
    }
    for engine, key in expected.items():
        assert engine in engines, f"{engine} provenance not surfaced by endpoint"
        prov = engines[engine]
        assert key in prov, f"{engine} missing {key}"
        for path, trace in prov.items():
            state = trace.state if hasattr(trace, "state") else trace["state"]
            assert state is not None, f"{engine}.{path} has no state"
