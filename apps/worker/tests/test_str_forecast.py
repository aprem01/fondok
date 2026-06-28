"""Engine + endpoint tests for ``apps/worker/app/engines/str_forecast.py``.

Pins the Wave 3 W3.3 contract:

* Coverage tiers — high (>=24 history), medium (12-23), low (<12).
* Three default scenarios always emitted.
* Downside RevPAR < Base RevPAR < Upside RevPAR (monotonic ordering).
* Subject RevPAR Index interpolates linearly to the scenario target.
* Occupancy + ADR floors honoured even when the math wants lower.
* Forecast horizon is exactly 24 months.
* Comp-set RevPAR grows at the scenario CAGR off the trailing-12 avg.
* Subject RevPAR == comp_set × index per row.
* Tenant-scoped endpoint (cross-tenant deal → 404).
* Revenue-engine seed flag pulls Month-12 base RevPAR into starting_*.

The pure-function tests cover the deterministic math; the endpoint
test spins up the FastAPI app + a real (SQLite) DB the same way the
historical-baseline / pricing-sensitivity test files do.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings / engine pick up the right DSN. Same pattern test_historical_baseline.py uses.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-str-forecast.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ["DOCUMENT_STORAGE_ROOT"] = str(
    Path(tempfile.gettempdir()) / "fondok-tests-str-forecast-storage"
)
os.environ.setdefault("EVALS_MOCK", "true")

_STORAGE_ROOT = Path(os.environ["DOCUMENT_STORAGE_ROOT"])
if _STORAGE_ROOT.exists():
    shutil.rmtree(_STORAGE_ROOT)


from app.engines.str_forecast import (  # noqa: E402
    build_str_forecast,
    default_scenarios,
)
from fondok_schemas.str_forecast import (  # noqa: E402
    STRForecastScenario,
    STRMonth,
)


TENANT_A = "11111111-1111-1111-1111-11111111aaaa"
TENANT_B = "22222222-2222-2222-2222-22222222bbbb"


# ────────────────────────── helpers ──────────────────────────


def _month_seq(start_year: int, start_month: int, count: int) -> list[str]:
    """Generate ``count`` ascending YYYY-MM strings starting at start."""
    out: list[str] = []
    y, m = start_year, start_month
    for _ in range(count):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _history(
    *,
    months: int = 24,
    occupancy: float = 0.70,
    adr: float = 220.0,
    comp_revpar: float = 150.0,
    revpar_index: float = 1.02,
) -> list[STRMonth]:
    """Build a synthetic STR Trend monthly history.

    Defaults produce ~$154 RevPAR (0.70 × 220), comp $150, index 1.02 —
    the worked example called out in the task spec.
    """
    revpar = occupancy * adr
    periods = _month_seq(2024, 1, months)
    return [
        STRMonth(
            period=p,
            occupancy=occupancy,
            adr=adr,
            revpar=revpar,
            comp_set_revpar=comp_revpar,
            revpar_index=revpar_index,
            is_historical=True,
        )
        for p in periods
    ]


# ────────────────────────── pure-function tests ──────────────────────────


def test_empty_historical_returns_low_coverage() -> None:
    """No history → coverage='low' and empty forecast lists per scenario."""
    res = build_str_forecast(deal_id="deal-1", historical_months=[])
    assert res.coverage_quality == "low"
    assert res.historical_months == []
    # 3 scenarios always keyed even when forecast lists are empty.
    assert set(res.forecast_months.keys()) == {"downside", "base", "upside"}
    for name in ("downside", "base", "upside"):
        assert res.forecast_months[name] == []
    assert len(res.scenario_settings) == 3


def test_12_months_history_yields_medium_coverage() -> None:
    """12-23 months → 'medium' coverage; forecast still runs."""
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(months=18)
    )
    assert res.coverage_quality == "medium"
    assert len(res.historical_months) == 18
    assert len(res.forecast_months["base"]) == 24


def test_24_months_history_yields_high_coverage() -> None:
    """24+ months → 'high' coverage; engine clips to most-recent 24."""
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(months=30)
    )
    assert res.coverage_quality == "high"
    # Engine clips historical to most-recent 24 even when more is supplied.
    assert len(res.historical_months) == 24


def test_three_default_scenarios_emitted() -> None:
    """When no overrides are supplied the engine emits exactly 3 named scenarios."""
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(months=24)
    )
    names = [s.name for s in res.scenario_settings]
    assert names == ["downside", "base", "upside"]
    # Default knobs match the task spec.
    defaults = {s.name: s for s in res.scenario_settings}
    assert defaults["downside"].revpar_cagr_pct == pytest.approx(-0.02)
    assert defaults["downside"].revpar_index_target == pytest.approx(0.92)
    assert defaults["base"].revpar_cagr_pct == pytest.approx(0.025)
    assert defaults["base"].revpar_index_target == pytest.approx(1.0)
    assert defaults["upside"].revpar_cagr_pct == pytest.approx(0.05)
    assert defaults["upside"].revpar_index_target == pytest.approx(1.06)


def test_downside_revpar_below_base() -> None:
    """Month-24 downside RevPAR < Month-24 base RevPAR."""
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(months=24)
    )
    downside_24 = res.forecast_months["downside"][-1].revpar
    base_24 = res.forecast_months["base"][-1].revpar
    assert downside_24 < base_24


def test_upside_revpar_above_base() -> None:
    """Month-24 upside RevPAR > Month-24 base RevPAR."""
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(months=24)
    )
    base_24 = res.forecast_months["base"][-1].revpar
    upside_24 = res.forecast_months["upside"][-1].revpar
    assert upside_24 > base_24


def test_revpar_index_interpolates_linearly_to_target() -> None:
    """Subject index at month 24 hits the scenario's target; month 12
    is roughly halfway between start and target.

    Start index ~1.02 (trailing-12 avg) → base target 1.00. So
    month 24 should be ~1.00; month 12 should be ~1.01.
    """
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(revpar_index=1.02)
    )
    base = res.forecast_months["base"]
    # Month 24 lands ~exactly at the target. Small rounding tolerance.
    assert base[-1].revpar_index == pytest.approx(1.00, abs=0.01)
    # Month 12 sits halfway between start (1.02) and target (1.00) →
    # ~1.01.
    assert base[11].revpar_index == pytest.approx(1.01, abs=0.01)


def test_occupancy_floor_respected() -> None:
    """Synthetic floor-stress: history at 0.45 occ → engine pins to
    BASE scenario's 0.60 floor when the math would predict lower."""
    # Subject history at 0.45 occ × $220 ADR = $99 RevPAR. Even with
    # CAGR + index migration the decomposed occupancy without a floor
    # would land near 0.45; the 0.60 floor should kick in.
    hist = _history(months=24, occupancy=0.45, adr=220.0, comp_revpar=100.0,
                    revpar_index=0.99)
    res = build_str_forecast(deal_id="deal-1", historical_months=hist)
    for m in res.forecast_months["base"]:
        assert m.occupancy >= 0.60 - 1e-9, (
            f"month {m.period} occupancy {m.occupancy} below floor 0.60"
        )


def test_adr_floor_respected() -> None:
    """ADR floor is a multiplier on trailing-12 ADR. Force a scenario
    that would otherwise drop ADR way below trailing-12 and confirm
    the floor binds."""
    # Build a custom scenario that hammers ADR via an extreme downside
    # CAGR. The default downside (-2%) already keeps ADR reasonable;
    # we push CAGR to -25% so the math wants a much lower ADR.
    hist = _history(months=24, occupancy=0.70, adr=200.0, comp_revpar=140.0,
                    revpar_index=1.0)
    override = STRForecastScenario(
        name="downside",
        revpar_cagr_pct=-0.25,
        revpar_index_target=0.95,
        occupancy_floor=0.55,
        adr_floor=0.85,  # 85% of trailing-12 ADR = $170 floor
        notes=[],
    )
    res = build_str_forecast(
        deal_id="deal-1",
        historical_months=hist,
        scenario_overrides=[override],
    )
    trailing_adr = 200.0
    expected_floor = 0.85 * trailing_adr
    for m in res.forecast_months["downside"]:
        assert m.adr >= expected_floor - 1e-6, (
            f"month {m.period} ADR {m.adr} below floor {expected_floor}"
        )


def test_forecast_month_count_is_24() -> None:
    """Every scenario emits exactly 24 forward months."""
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(months=24)
    )
    for name in ("downside", "base", "upside"):
        assert len(res.forecast_months[name]) == 24


def test_compset_growth_applied_to_trailing_12_avg() -> None:
    """Comp-set RevPAR at month-12 ≈ trailing-12 avg × (1 + CAGR)^(12/12)."""
    hist = _history(months=24, comp_revpar=150.0)
    res = build_str_forecast(deal_id="deal-1", historical_months=hist)
    base = res.forecast_months["base"]
    # Trailing-12 avg comp RevPAR is 150 (flat synthetic series).
    # Base CAGR 2.5%. Month 12 should be ~150 * 1.025 = 153.75.
    assert base[11].comp_set_revpar == pytest.approx(150 * 1.025, abs=0.5)
    # Month 24 should be ~150 * 1.025^2 = 157.59.
    assert base[-1].comp_set_revpar == pytest.approx(150 * (1.025 ** 2), abs=0.5)


def test_subject_revpar_equals_compset_times_index() -> None:
    """Per-row identity: subject.revpar ≈ comp_set_revpar × revpar_index.

    Holds exactly when no floor binds; the engine re-derives the
    index after flooring so the identity stays true regardless.
    """
    res = build_str_forecast(
        deal_id="deal-1", historical_months=_history(months=24)
    )
    for name in ("downside", "base", "upside"):
        for m in res.forecast_months[name]:
            implied = m.comp_set_revpar * m.revpar_index
            assert m.revpar == pytest.approx(implied, abs=0.05), (
                f"{name}/{m.period}: revpar {m.revpar} != comp({m.comp_set_revpar})*index({m.revpar_index})"
            )


# ────────────────────────── endpoint tests ──────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Truncate + re-migrate between tests so each starts deterministic."""
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "broker_questions",
            "audit_log",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:
                pass
        await session.commit()
    yield


async def _seed_str_trend(
    *,
    deal_id: str,
    tenant_id: str,
    months: int = 24,
    occupancy_pct: float = 70.0,
    adr_usd: float = 220.0,
    compset_revpar: float = 150.0,
) -> None:
    """Seed one STR_TREND extraction with ``months`` of subject monthly
    data + a single comp-set row (the engine averages compset rows).

    Mirrors the schema used by the live STR Trend extractor — see
    ``apps/worker/app/agents/extraction_schemas/str_trend.md``.
    """
    from sqlalchemy import text

    from app.database import get_session_factory

    fields: list[dict[str, object]] = [
        # One comp-set row — engine averages multiple but one is fine.
        {"field_name": "ttm_performance.compset.1.name",
         "value": "Comp Hotel A"},
        {"field_name": "ttm_performance.compset.1.revpar_usd",
         "value": compset_revpar},
        # Subject indices (trailing-12 summary on the report).
        {"field_name": "ttm_performance.indices.rgi_revpar_index",
         "value": 1.02},
    ]
    # Subject monthly rows.
    revpar = (occupancy_pct / 100.0) * adr_usd
    for p in _month_seq(2024, 1, months):
        year, month = p.split("-")
        key = f"{year}_{month}"
        fields.append({
            "field_name": f"ttm_performance.subject.monthly.{key}.occupancy_pct",
            "value": occupancy_pct,
        })
        fields.append({
            "field_name": f"ttm_performance.subject.monthly.{key}.adr_usd",
            "value": adr_usd,
        })
        fields.append({
            "field_name": f"ttm_performance.subject.monthly.{key}.revpar_usd",
            "value": revpar,
        })

    doc_id = str(uuid4())
    extr_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO documents (
                    id, deal_id, tenant_id, filename, doc_type, status
                ) VALUES (
                    :id, :deal, :tenant, :fname, :dtype, :stat
                )
                """
            ),
            {
                "id": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fname": "str-trend.xlsx",
                "dtype": "STR_TREND",
                "stat": "Extracted",
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO extraction_results (
                    id, document_id, deal_id, tenant_id, fields
                ) VALUES (
                    :id, :doc, :deal, :tenant, :fields
                )
                """
            ),
            {
                "id": extr_id,
                "doc": doc_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "fields": json.dumps(fields),
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_endpoint_tenant_scoped() -> None:
    """Seed a deal under TENANT_A; request as TENANT_B → 404 (deal-belongs
    gate fires before the forecast read). No cross-tenant leakage.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Create deal under TENANT_A.
        r = await client.post(
            "/deals",
            json={"name": "STR Hotel A", "city": "Tampa, FL"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        await _seed_str_trend(deal_id=deal_id, tenant_id=TENANT_A)

        # GET as TENANT_B → 404.
        r = await client.get(
            f"/deals/{deal_id}/str-forecast",
            headers={"X-Tenant-Id": TENANT_B},
        )
        assert r.status_code == 404

        # GET as TENANT_A → 200 + populated forecast.
        r = await client.get(
            f"/deals/{deal_id}/str-forecast",
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["coverage_quality"] == "high"
        assert len(body["historical_months"]) == 24
        assert len(body["forecast_months"]["base"]) == 24


@pytest.mark.asyncio
async def test_seed_revenue_from_forecast_month_12_when_flag_set() -> None:
    """When ``revenue_seed_from_str_forecast=True`` the engine_runner
    seeds (starting_occupancy, starting_adr) from the BASE scenario's
    Month-12 forecast point and tags both with SOURCE_STR_FORECAST.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.services.engine_runner import (
        SOURCE_STR_FORECAST,
        _load_engine_inputs,
    )
    from app.database import get_session_factory

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        r = await client.post(
            "/deals",
            json={"name": "STR Seed Hotel", "city": "Tampa, FL"},
            headers={"X-Tenant-Id": TENANT_A},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

    await _seed_str_trend(deal_id=deal_id, tenant_id=TENANT_A)

    factory = get_session_factory()
    async with factory() as session:
        # Flag OFF (default) — sources should NOT be SOURCE_STR_FORECAST.
        baseline = await _load_engine_inputs(session, deal_id=deal_id)
        assert baseline["__sources__"].get("starting_occupancy") != SOURCE_STR_FORECAST
        baseline_occ = baseline["starting_occupancy"]
        baseline_adr = baseline["starting_adr"]

        # Flag ON — sources should flip to SOURCE_STR_FORECAST and the
        # values should differ from the default (unless the forecast
        # happens to land EXACTLY on the default — vanishingly unlikely
        # with the synthetic seed which uses occ=0.70, adr=220 vs
        # Kimpton defaults).
        seeded = await _load_engine_inputs(
            session,
            deal_id=deal_id,
            overrides={"revenue_seed_from_str_forecast": True},
        )
        assert seeded["__sources__"].get("starting_occupancy") == SOURCE_STR_FORECAST
        assert seeded["__sources__"].get("starting_adr") == SOURCE_STR_FORECAST
        # Sanity: the seeded numbers come from the forecast and are
        # NOT identical to the no-flag baseline (the seed actually fired).
        assert (
            seeded["starting_occupancy"] != baseline_occ
            or seeded["starting_adr"] != baseline_adr
        )
