"""Engine orchestration — runs the 8 deterministic underwriting engines.

The Run Model button in the web app posts to ``/deals/{id}/engines/run``
or ``/deals/{id}/engines/{name}/run``; the FastAPI handler delegates to
the helpers here. Each engine output is persisted as a row in
``engine_outputs`` so the UI can poll for completion and read back the
last result without re-running the math.

Engine dependency order (used for the run-all chain):

    revenue → fb → expense → capital → debt → returns
                                                  ├─→ sensitivity
                                                  └─→ partnership

When an engine fails its row lands with ``status='failed'`` and a
``error`` blob; downstream dependents that need its output are also
marked failed (with a "skipped: <upstream>" error) so the UI can show a
clear failure path. Independent engines (e.g. ``capital`` does not depend
on ``revenue``) keep running.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fondok_schemas.financial import ModelAssumptions
from fondok_schemas.partnership import WaterfallTier

from ..engines import (
    CapitalEngine,
    CapitalEngineInput,
    DebtEngine,
    DebtEngineInputExt,
    ExpenseEngine,
    ExpenseEngineInput,
    FBRevenueEngine,
    FBRevenueInput,
    PartnershipEngine,
    PartnershipInputExt,
    ReturnsEngine,
    ReturnsEngineInputExt,
    RevenueEngine,
    SensitivityEngine,
    SensitivityInput,
)
from fondok_schemas.underwriting import RevenueEngineInput

logger = logging.getLogger(__name__)


# Canonical engine identifiers — match what the web app posts.
ENGINE_NAMES: tuple[str, ...] = (
    "revenue",
    "fb",
    "expense",
    "capital",
    "debt",
    "returns",
    "sensitivity",
    "partnership",
)


ENGINE_REGISTRY: dict[str, type] = {
    "revenue": RevenueEngine,
    "fb": FBRevenueEngine,
    "expense": ExpenseEngine,
    "capital": CapitalEngine,
    "debt": DebtEngine,
    "returns": ReturnsEngine,
    "sensitivity": SensitivityEngine,
    "partnership": PartnershipEngine,
}


# Each engine declares the upstream outputs it needs. When any
# dependency failed we mark this engine ``skipped`` rather than running
# it with stale inputs.
ENGINE_DEPS: dict[str, list[str]] = {
    "revenue": [],
    "fb": ["revenue"],
    "expense": ["revenue", "fb"],
    "capital": [],
    "debt": ["expense", "capital"],
    "returns": ["expense", "debt", "capital"],
    "sensitivity": ["returns"],
    "partnership": ["returns", "capital"],
}


# ──────────────────────────── Kimpton fallback ────────────────────────


def _kimpton_assumptions() -> dict[str, Any]:
    """Default underwriting assumptions matching the Kimpton fixture.

    Mirrors ``apps/worker/app/export/fixtures.py`` so a single Run Model
    click on the demo deal reproduces the headline numbers shown in the
    seeded UI (~$4.7M Y1 NOI, ~23% levered IRR).
    """
    return {
        "keys": 132,
        "purchase_price": 36_400_000,
        "starting_occupancy": 0.762,
        "starting_adr": 385.0,
        "occupancy_growth": 0.008,
        "adr_growth": 0.04,
        "fb_revenue_per_occupied_room": 88.0,
        "other_revenue_pct_of_rooms": 0.065,
        "hold_years": 5,
        "hotel_type": "lifestyle",
        "fb_ratio": 0.29,
        "other_ratio": 0.06,
        "mgmt_fee_pct": 0.03,
        "ffe_reserve_pct": 0.04,
        "expense_growth": 0.035,
        "grow_opex_independently": True,
        "renovation_budget": 5_280_000,
        "soft_costs": 528_000,
        "contingency": 528_000,
        "working_capital": 500_000,
        "closing_costs_pct": 0.02,
        "loan_costs_pct": 0.015,
        "ltv": 0.65,
        "interest_rate": 0.068,
        "amortization_years": 30,
        "term_years": 5,
        "interest_only_years": 0,
        "exit_cap_rate": 0.07,
        "revpar_growth": 0.045,
        "selling_costs_pct": 0.02,
        "gp_equity_pct": 0.10,
        "lp_equity_pct": 0.90,
        "pref_rate": 0.08,
    }


# ──────────────────────────── Loading inputs ──────────────────────────


async def _load_engine_inputs(
    session: AsyncSession,
    deal_id: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the underwriting assumptions for ``deal_id``.

    Strategy:
        1. Load the deal row (purchase_price, keys) when present.
        2. Layer in caller overrides from the API request body.
        3. Fall back to the Kimpton fixture for everything missing.

    The web app's demo deal id (legacy int 7) does not parse as a UUID
    and never lands in the deals table; that path uses the pure Kimpton
    defaults.
    """
    base = _kimpton_assumptions()
    try:
        # Only try DB lookup when the id is a valid UUID. The Kimpton
        # demo card uses an int-string id which is intentionally
        # outside the deals table.
        UUID(deal_id)
    except (ValueError, TypeError):
        if overrides:
            base.update(overrides)
        return base

    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT keys, purchase_price
                      FROM deals
                     WHERE id = :id
                    """
                ),
                {"id": deal_id},
            )
        ).first()
    except Exception:
        # The migrations may not have been applied for the test DB;
        # fall through to defaults silently.
        row = None

    if row is not None:
        mapping = row._mapping
        if mapping.get("keys"):
            base["keys"] = int(mapping["keys"])
        if mapping.get("purchase_price"):
            try:
                base["purchase_price"] = float(mapping["purchase_price"])
            except (TypeError, ValueError):
                pass

    # Pull Year-1 T-12 expense actuals from the deal's extraction results
    # so the expense engine can ground synthesis on real numbers (Sam QA
    # #1: synthesized expenses ($457K Insurance vs actual $1.16M;
    # $905K Utilities vs actual $288K) cascaded into wrong DSCR / returns
    # / per-key metrics). Best-effort — partial extraction degrades to
    # ratio synthesis line-by-line.
    base["t12_expense_actuals"] = await _load_t12_expense_actuals(
        session, deal_id=deal_id
    )

    if overrides:
        base.update(overrides)
    return base


# Map extracted T-12 field paths onto the canonical expense-line keys
# the expense engine recognizes. Both the dotted ``p_and_l_usali.*``
# paths the Extractor agent emits and the bare lowercase aliases the
# legacy normalizer uses are accepted.
_T12_EXPENSE_FIELD_ALIASES: dict[str, str] = {
    # Departmental
    "p_and_l_usali.departmental_expenses.rooms": "rooms_dept_expense",
    "rooms_dept_expense": "rooms_dept_expense",
    "rooms_departmental_expenses": "rooms_dept_expense",
    "p_and_l_usali.departmental_expenses.food_beverage": "fb_dept_expense",
    "fb_dept_expense": "fb_dept_expense",
    "food_beverage_departmental_expenses": "fb_dept_expense",
    "p_and_l_usali.departmental_expenses.other_operated": "other_dept_expense",
    "other_dept_expense": "other_dept_expense",
    # Undistributed
    "p_and_l_usali.undistributed.administrative_general": "administrative_general",
    "administrative_general": "administrative_general",
    "admin_general": "administrative_general",
    "p_and_l_usali.undistributed.information_telecom": "information_telecom",
    "information_telecom": "information_telecom",
    "p_and_l_usali.undistributed.sales_marketing": "sales_marketing",
    "sales_marketing": "sales_marketing",
    "p_and_l_usali.undistributed.property_operations": "property_operations",
    "property_operations": "property_operations",
    "repairs_maintenance": "property_operations",
    "p_and_l_usali.undistributed.utilities": "utilities",
    "utilities": "utilities",
    # Fees & reserves
    "p_and_l_usali.fees_and_reserves.mgmt_fee": "mgmt_fee",
    "mgmt_fee": "mgmt_fee",
    "management_fee": "mgmt_fee",
    "p_and_l_usali.fees_and_reserves.ffe_reserve": "ffe_reserve",
    "ffe_reserve": "ffe_reserve",
    # Fixed charges
    "p_and_l_usali.fixed_charges.property_taxes": "property_taxes",
    "property_taxes": "property_taxes",
    "p_and_l_usali.fixed_charges.insurance": "insurance",
    "insurance": "insurance",
}


async def _load_t12_expense_actuals(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, float]:
    """Read Year-1 expense actuals off the deal's most recent T-12 extraction.

    Returns ``{}`` (no overrides — engine falls back to USALI ratios) when
    no T-12 has been extracted, when the deal id isn't a UUID, or when
    the migrations haven't been applied to the test DB.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal AND d.doc_type IN ('T12','PNL')
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return {}

    actuals: dict[str, float] = {}
    for r in rows.fetchall():
        raw_fields = r._mapping["fields"]
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw_fields, list):
            continue
        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not name or not isinstance(value, (int, float)):
                continue
            canonical = _T12_EXPENSE_FIELD_ALIASES.get(name)
            if canonical is None:
                # Try the last segment of a dotted path as a fallback.
                tail = name.rsplit(".", 1)[-1] if "." in name else name
                canonical = _T12_EXPENSE_FIELD_ALIASES.get(tail)
            if canonical and canonical not in actuals:
                actuals[canonical] = float(value)
    return actuals


# ─────────────────────────── Per-engine input ─────────────────────────


def _build_input_for(
    engine_name: str,
    deal_id: str,
    base: dict[str, Any],
    accumulated: dict[str, BaseModel],
) -> BaseModel:
    """Materialize the typed Pydantic input for ``engine_name``."""
    deal_uuid = _coerce_uuid(deal_id)

    if engine_name == "revenue":
        return RevenueEngineInput(
            deal_id=deal_uuid,
            keys=base["keys"],
            starting_occupancy=base["starting_occupancy"],
            starting_adr=base["starting_adr"],
            occupancy_growth=base["occupancy_growth"],
            adr_growth=base["adr_growth"],
            fb_revenue_per_occupied_room=base["fb_revenue_per_occupied_room"],
            other_revenue_pct_of_rooms=base["other_revenue_pct_of_rooms"],
            hold_years=base["hold_years"],
        )

    if engine_name == "fb":
        return FBRevenueInput(
            deal_id=deal_uuid,
            revenue=accumulated["revenue"],
            hotel_type=base.get("hotel_type", "full"),
            fb_ratio=base.get("fb_ratio"),
            other_ratio=base.get("other_ratio"),
        )

    if engine_name == "expense":
        return ExpenseEngineInput(
            deal_id=deal_uuid,
            revenue=accumulated["fb"],
            hotel_type=base.get("hotel_type", "full"),
            mgmt_fee_pct=base["mgmt_fee_pct"],
            ffe_reserve_pct=base["ffe_reserve_pct"],
            expense_growth=base["expense_growth"],
            grow_opex_independently=base["grow_opex_independently"],
            # When the deal has an extracted T-12, the engine prefers
            # actuals over USALI benchmark ratios for Year 1. Loaded by
            # ``_load_engine_inputs`` below; absent on demo deals.
            t12_actuals=base.get("t12_expense_actuals", {}) or {},
        )

    if engine_name == "capital":
        return CapitalEngineInput(
            deal_id=deal_uuid,
            purchase_price=base["purchase_price"],
            keys=base["keys"],
            renovation_budget=base.get("renovation_budget", 0.0),
            soft_costs=base.get("soft_costs", 0.0),
            contingency=base.get("contingency", 0.0),
            working_capital=base.get("working_capital", 0.0),
            closing_costs_pct=base.get("closing_costs_pct", 0.02),
            loan_costs_pct=base.get("loan_costs_pct", 0.015),
            ltv=base["ltv"],
            debt_basis="purchase",
        )

    if engine_name == "debt":
        capital_out = accumulated["capital"]
        expense_out = accumulated["expense"]
        noi_by_year = [yr.noi for yr in expense_out.years]
        return DebtEngineInputExt(
            deal_id=deal_uuid,
            loan_amount=capital_out.debt_amount,
            ltv=base["ltv"],
            interest_rate=base["interest_rate"],
            term_years=base["term_years"],
            amortization_years=base["amortization_years"],
            interest_only_years=base.get("interest_only_years", 0),
            noi_by_year=noi_by_year,
        )

    if engine_name == "returns":
        capital_out = accumulated["capital"]
        debt_out = accumulated["debt"]
        expense_out = accumulated["expense"]
        noi_by_year = [yr.noi for yr in expense_out.years]
        assumptions = ModelAssumptions(
            purchase_price=base["purchase_price"],
            ltv=base["ltv"],
            interest_rate=base["interest_rate"],
            amortization_years=base["amortization_years"],
            loan_term_years=base["term_years"],
            hold_years=base["hold_years"],
            exit_cap_rate=base["exit_cap_rate"],
            revpar_growth=base["revpar_growth"],
            expense_growth=base["expense_growth"],
            selling_costs_pct=base["selling_costs_pct"],
            closing_costs_pct=base["closing_costs_pct"],
        )
        return ReturnsEngineInputExt(
            deal_id=deal_uuid,
            assumptions=assumptions,
            year_one_noi=noi_by_year[0],
            noi_by_year=noi_by_year,
            annual_debt_service=debt_out.annual_debt_service,
            loan_amount=capital_out.debt_amount,
            loan_balance_at_exit=(
                debt_out.schedule[-1].ending_balance
                if debt_out.schedule
                else capital_out.debt_amount
            ),
            equity=capital_out.equity_amount,
        )

    if engine_name == "sensitivity":
        # Reuse the returns engine input as the base; flex exit cap × revpar.
        returns_input = _build_input_for(
            "returns", deal_id, base, accumulated
        )
        # mypy: returns_input is ReturnsEngineInputExt by construction
        assert isinstance(returns_input, ReturnsEngineInputExt)
        ec = base["exit_cap_rate"]
        rp = base["revpar_growth"]
        row_values = [round(ec - 0.01, 4), round(ec - 0.005, 4), ec,
                      round(ec + 0.005, 4), round(ec + 0.01, 4)]
        col_values = [round(rp - 0.02, 4), round(rp - 0.01, 4), rp,
                      round(rp + 0.01, 4), round(rp + 0.02, 4)]
        # Clamp to engine bounds (exit_cap > 0).
        row_values = [max(0.001, v) for v in row_values]
        col_values = [max(-0.49, min(0.49, v)) for v in col_values]
        return SensitivityInput(
            deal_id=deal_uuid,
            base_returns_input=returns_input,
            row_variable="exit_cap_rate",
            row_values=row_values,
            col_variable="revpar_growth",
            col_values=col_values,
            metric="levered_irr",
        )

    if engine_name == "partnership":
        capital_out = accumulated["capital"]
        returns_out = accumulated["returns"]
        # The returns engine emits the levered cash-flow series; strip
        # the Year 0 (-equity) entry so we feed annual project cash.
        flows = returns_out.cash_flows[1:] if returns_out.cash_flows else []
        if not flows:
            # Defensive fallback: synthesize a flat annual cash flow.
            flows = [returns_out.equity_multiple * capital_out.equity_amount / max(1, base["hold_years"])]
        waterfall = [
            WaterfallTier(label="Pref", hurdle_rate=0.08, gp_split=0.10, lp_split=0.90),
            WaterfallTier(label="Tier 1", hurdle_rate=0.12, gp_split=0.20, lp_split=0.80),
            WaterfallTier(label="Tier 2", hurdle_rate=0.18, gp_split=0.30, lp_split=0.70),
        ]
        return PartnershipInputExt(
            deal_id=deal_uuid,
            total_equity=capital_out.equity_amount,
            gp_equity_pct=base["gp_equity_pct"],
            lp_equity_pct=base["lp_equity_pct"],
            pref_rate=base["pref_rate"],
            waterfall=waterfall,
            cash_flows=flows,
            catch_up=False,
        )

    raise ValueError(f"unknown engine: {engine_name!r}")


def _coerce_uuid(value: str) -> UUID:
    """Best-effort UUID coercion — fall back to a deterministic UUID5
    for legacy int-string ids (e.g. the Kimpton demo deal '7')."""
    try:
        return UUID(value)
    except (ValueError, TypeError):
        # Stable derivation so repeat calls produce the same UUID.
        from uuid import NAMESPACE_URL, uuid5

        return uuid5(NAMESPACE_URL, f"fondok://deal/{value}")


# ──────────────────────────── Persistence ─────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, default=str)


async def _persist_status(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    engine_name: str,
    run_id: str,
    inputs: BaseModel | dict[str, Any] | None,
    started_at: datetime,
) -> str:
    """Insert a ``running`` row; return the row id."""
    row_id = str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO engine_outputs (
                id, deal_id, tenant_id, run_id, engine_name,
                status, inputs, outputs, error,
                started_at, completed_at, runtime_ms
            ) VALUES (
                :id, :deal, :tenant, :run, :engine,
                'running', :inputs, NULL, NULL,
                :started_at, NULL, NULL
            )
            """
        ),
        {
            "id": row_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "run": run_id,
            "engine": engine_name,
            "inputs": _json_dumps(inputs),
            "started_at": started_at,
        },
    )
    await session.commit()
    return row_id


async def _persist_complete(
    session: AsyncSession,
    *,
    row_id: str,
    output: BaseModel,
    inputs: BaseModel | dict[str, Any] | None,
    runtime_ms: int,
) -> None:
    await session.execute(
        text(
            """
            UPDATE engine_outputs
               SET status = 'complete',
                   inputs = :inputs,
                   outputs = :outputs,
                   completed_at = :ts,
                   runtime_ms = :runtime_ms
             WHERE id = :id
            """
        ),
        {
            "id": row_id,
            "inputs": _json_dumps(inputs),
            "outputs": _json_dumps(output),
            "ts": _now(),
            "runtime_ms": runtime_ms,
        },
    )
    await session.commit()


async def _persist_failed(
    session: AsyncSession,
    *,
    row_id: str,
    error: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE engine_outputs
               SET status = 'failed',
                   error = :error,
                   completed_at = :ts
             WHERE id = :id
            """
        ),
        {"id": row_id, "error": error, "ts": _now()},
    )
    await session.commit()


# ────────────────────────────── Runners ───────────────────────────────


def _summary_for(engine_name: str, output: BaseModel) -> str:
    """Compact one-line headline shown next to the Run button."""
    try:
        if engine_name == "returns":
            return (
                f"IRR {output.levered_irr * 100:.1f}% "
                f"· Multiple {output.equity_multiple:.2f}x"
            )
        if engine_name == "expense":
            y1 = output.years[0].noi if output.years else 0.0
            return f"Y1 NOI ${y1 / 1e6:.2f}M"
        if engine_name == "revenue":
            cagr = getattr(output, "total_revenue_cagr", 0.0)
            return f"Revenue CAGR {cagr * 100:.1f}%"
        if engine_name == "fb":
            ratio = getattr(output, "fb_ratio_used", 0.0)
            return f"F&B {ratio * 100:.0f}% of rooms"
        if engine_name == "capital":
            return (
                f"Equity ${output.equity_amount / 1e6:.2f}M "
                f"· LTC {output.ltc * 100:.1f}%"
            )
        if engine_name == "debt":
            dscr = getattr(output, "year_one_dscr", None) or 0.0
            return f"DSCR {dscr:.2f}x"
        if engine_name == "sensitivity":
            return f"{len(output.cells)} cells"
        if engine_name == "partnership":
            return (
                f"LP IRR {output.lp.irr * 100:.1f}% "
                f"· GP IRR {output.gp.irr * 100:.1f}%"
            )
    except Exception:
        return ""
    return ""


async def run_single_engine(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    engine_name: str,
    run_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    accumulated: dict[str, BaseModel] | None = None,
    base_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single engine and persist the output.

    Returns a serializable dict suitable for the API response.
    """
    if engine_name not in ENGINE_REGISTRY:
        raise ValueError(
            f"unknown engine {engine_name!r}; "
            f"expected one of {sorted(ENGINE_REGISTRY)}"
        )

    run_id = run_id or str(uuid4())
    accumulated = accumulated if accumulated is not None else {}
    base_inputs = base_inputs or await _load_engine_inputs(
        session, deal_id, overrides
    )

    # Some engines need upstream outputs. When called in single-engine
    # mode we transparently run prerequisites first so the user can hit
    # any one Run button without manually orchestrating the chain.
    for dep in ENGINE_DEPS[engine_name]:
        if dep not in accumulated:
            await run_single_engine(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                engine_name=dep,
                run_id=run_id,
                overrides=overrides,
                accumulated=accumulated,
                base_inputs=base_inputs,
            )

    started_at = _now()
    # Persist the running row first so a build-time failure (e.g. a
    # Pydantic ValidationError on a bad override) still surfaces to
    # the UI as a normal failed row instead of bubbling up to FastAPI
    # as a 500.
    row_id = await _persist_status(
        session,
        deal_id=str(_coerce_uuid(deal_id)),
        tenant_id=tenant_id,
        engine_name=engine_name,
        run_id=run_id,
        inputs=None,
        started_at=started_at,
    )

    t0 = time.monotonic()
    try:
        engine_input = _build_input_for(
            engine_name, deal_id, base_inputs, accumulated
        )
        engine = ENGINE_REGISTRY[engine_name]()
        output = engine.run(engine_input)
    except Exception as exc:
        runtime_ms = int((time.monotonic() - t0) * 1000)
        logger.exception(
            "engine %s failed deal=%s runtime=%dms", engine_name, deal_id, runtime_ms
        )
        await _persist_failed(session, row_id=row_id, error=str(exc))
        return {
            "engine": engine_name,
            "status": "failed",
            "error": str(exc),
            "runtime_ms": runtime_ms,
        }

    runtime_ms = int((time.monotonic() - t0) * 1000)
    accumulated[engine_name] = output
    await _persist_complete(
        session,
        row_id=row_id,
        output=output,
        inputs=engine_input,
        runtime_ms=runtime_ms,
    )
    return {
        "engine": engine_name,
        "status": "complete",
        "outputs": json.loads(output.model_dump_json()),
        "summary": _summary_for(engine_name, output),
        "runtime_ms": runtime_ms,
    }


async def run_all_engines(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    run_id: str,
    overrides: dict[str, Any] | None = None,
    on_complete: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the full 8-engine chain; persist each output as it lands.

    Returns a dict keyed by engine name with the per-engine result
    dicts (matching ``run_single_engine``'s return shape).

    Failure policy: when an engine fails we mark its row failed AND
    skip downstream engines that need its output (their rows land with
    ``status='failed'`` and a ``skipped: <upstream>`` error). Engines
    independent of the failure keep running so the user sees partial
    progress instead of a blank page.
    """
    base_inputs = await _load_engine_inputs(session, deal_id, overrides)
    accumulated: dict[str, BaseModel] = {}
    results: dict[str, dict[str, Any]] = {}

    for name in ENGINE_NAMES:
        deps = ENGINE_DEPS[name]
        missing = [d for d in deps if d not in accumulated]
        if missing:
            # Upstream failed — record a skipped row and move on.
            started_at = _now()
            row_id = await _persist_status(
                session,
                deal_id=str(_coerce_uuid(deal_id)),
                tenant_id=tenant_id,
                engine_name=name,
                run_id=run_id,
                inputs=None,
                started_at=started_at,
            )
            err = f"skipped: upstream {', '.join(missing)} did not complete"
            await _persist_failed(session, row_id=row_id, error=err)
            results[name] = {
                "engine": name,
                "status": "failed",
                "error": err,
                "runtime_ms": 0,
            }
            if on_complete:
                on_complete(name, results[name])
            continue

        result = await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            engine_name=name,
            run_id=run_id,
            overrides=overrides,
            accumulated=accumulated,
            base_inputs=base_inputs,
        )
        results[name] = result
        if on_complete:
            on_complete(name, result)

    return results


# ─────────────────────────── Reading back ─────────────────────────────


async def get_latest_output(
    session: AsyncSession,
    *,
    deal_id: str,
    engine_name: str,
) -> dict[str, Any] | None:
    """Return the latest persisted row for ``(deal_id, engine_name)``."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, run_id, engine_name,
                       status, inputs, outputs, error,
                       started_at, completed_at, runtime_ms
                  FROM engine_outputs
                 WHERE deal_id = :deal AND engine_name = :engine
                 ORDER BY started_at DESC
                 LIMIT 1
                """
            ),
            {"deal": str(_coerce_uuid(deal_id)), "engine": engine_name},
        )
    ).first()
    if row is None:
        return None
    return _row_to_dict(row._mapping)


async def get_latest_outputs(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, dict[str, Any]]:
    """Return the latest row per engine for ``deal_id``.

    Result is keyed by engine name; engines with no rows are omitted.
    """
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, run_id, engine_name,
                   status, inputs, outputs, error,
                   started_at, completed_at, runtime_ms
              FROM engine_outputs
             WHERE deal_id = :deal
             ORDER BY started_at DESC
            """
        ),
        {"deal": str(_coerce_uuid(deal_id))},
    )
    seen: dict[str, dict[str, Any]] = {}
    for r in rows.fetchall():
        mapping = r._mapping
        name = mapping["engine_name"]
        if name in seen:
            continue
        seen[name] = _row_to_dict(mapping)
    return seen


async def get_run_status(
    session: AsyncSession,
    *,
    deal_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return every engine row tagged with ``run_id`` for ``deal_id``."""
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, run_id, engine_name,
                   status, inputs, outputs, error,
                   started_at, completed_at, runtime_ms
              FROM engine_outputs
             WHERE deal_id = :deal AND run_id = :run
             ORDER BY started_at ASC
            """
        ),
        {"deal": str(_coerce_uuid(deal_id)), "run": run_id},
    )
    return [_row_to_dict(r._mapping) for r in rows.fetchall()]


def _row_to_dict(mapping: Any) -> dict[str, Any]:
    """Coerce a SQL row into the engine-output JSON envelope.

    JSONB columns come back as dicts on Postgres; on SQLite they're
    serialized strings (we did the encoding ourselves) so we decode
    here to keep the API response shape consistent across backends.
    """
    def _decode(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    name = mapping["engine_name"]
    outputs = _decode(mapping["outputs"])
    summary = ""
    if outputs and mapping["status"] == "complete":
        summary = _summary_from_dict(name, outputs)

    return {
        "id": str(mapping["id"]),
        "deal_id": str(mapping["deal_id"]),
        "tenant_id": str(mapping["tenant_id"]),
        "run_id": str(mapping["run_id"]) if mapping["run_id"] else None,
        "engine": name,
        "status": mapping["status"],
        "inputs": _decode(mapping["inputs"]),
        "outputs": outputs,
        "summary": summary,
        "error": mapping["error"],
        "started_at": _coerce_iso(mapping["started_at"]),
        "completed_at": _coerce_iso(mapping["completed_at"]),
        "runtime_ms": mapping["runtime_ms"],
    }


def _summary_from_dict(engine_name: str, output: dict[str, Any]) -> str:
    """Compute the headline summary directly from a JSON dict.

    Mirrors ``_summary_for`` but operates on the deserialized output so
    the GET endpoints don't have to re-instantiate the Pydantic model.
    """
    try:
        if engine_name == "returns":
            return (
                f"IRR {output.get('levered_irr', 0) * 100:.1f}% "
                f"· Multiple {output.get('equity_multiple', 0):.2f}x"
            )
        if engine_name == "expense":
            years = output.get("years") or []
            y1 = years[0]["noi"] if years else 0.0
            return f"Y1 NOI ${y1 / 1e6:.2f}M"
        if engine_name == "revenue":
            cagr = output.get("total_revenue_cagr", 0.0)
            return f"Revenue CAGR {cagr * 100:.1f}%"
        if engine_name == "fb":
            return f"F&B {output.get('fb_ratio_used', 0) * 100:.0f}% of rooms"
        if engine_name == "capital":
            return (
                f"Equity ${output.get('equity_amount', 0) / 1e6:.2f}M "
                f"· LTC {output.get('ltc', 0) * 100:.1f}%"
            )
        if engine_name == "debt":
            dscr = output.get("year_one_dscr") or 0.0
            return f"DSCR {dscr:.2f}x"
        if engine_name == "sensitivity":
            return f"{len(output.get('cells') or [])} cells"
        if engine_name == "partnership":
            lp = output.get("lp", {})
            gp = output.get("gp", {})
            return (
                f"LP IRR {lp.get('irr', 0) * 100:.1f}% "
                f"· GP IRR {gp.get('irr', 0) * 100:.1f}%"
            )
    except Exception:
        return ""
    return ""


def _coerce_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


__all__ = [
    "ENGINE_DEPS",
    "ENGINE_NAMES",
    "ENGINE_REGISTRY",
    "get_latest_output",
    "get_latest_outputs",
    "get_run_status",
    "run_all_engines",
    "run_single_engine",
]
