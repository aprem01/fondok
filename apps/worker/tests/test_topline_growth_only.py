"""FON-116 staged rollout — the GROWTH-ONLY topline (``TOPLINE_BENCHMARK_ENABLED``
off, the deployed default).

Year 1 occupancy / ADR come from the subject's own basis exactly as before
topline v2 (the analyst's value — including a rate the retired "Use STR
rates" button wrote, badged STR comp set; else the T-12 actual; else the STR
subject TTM when the legacy switch is on; else CBRE Hotel Horizons' first
forecast year's ADR), never benchmark × penetration × ramp. Every out-year's
growth is the per-year resolver's (analyst > CBRE > CoStar > post-forecast
convention > unavailable), occupancy flat after stabilization (decision 1b).
No Year-1 basis at all refuses the revenue chain (no 76.2% / $385 seed).
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-topline-growth-only.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from tests.fixtures.kimpton_docs_fixture import seed_kimpton_docs_deal  # noqa: E402
from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed  # noqa: E402

#: The real T-12 on the documents fixture (to May 2025).
T12_OCC, T12_ADR = 0.828892, 233.446
#: CoStar Miami Beach Luxury & Upper Upscale, forecast years (p.26).
LUU = {2026: (-0.012, 0.018), 2027: (0.0, -0.006), 2028: (-0.012, 0.038), 2029: (0.03, 0.028)}
#: Past the series: CoStar's PUBLISHED forecast average (overview p.4, +2.5%;
#: FON-121 decision 2) — the computed 2025–2029 mean would be +2.44%.
LUU_ADR_AVG = 0.025


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations
    from app.tenants import reset_tenant_cache

    await run_startup_migrations()
    reset_tenant_cache()
    async with get_session_factory()() as session:
        for tbl in ("engine_outputs", "extraction_results", "documents", "deals", "tenants"):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001
                pass
        await session.commit()
    yield


async def _docs_deal(drop: tuple[str, ...] = ("occupancy_growth_by_year", "adr_growth_by_year"), **patch) -> tuple[str, str]:
    """The documents fixture (STR + CoStar + T-12, close 2025-12-31) with the
    reference tab's growth keys dropped by default (document growth only)."""
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        deal_id, tenant_id = await seed_kimpton_docs_deal(s)
        row = (await s.execute(text("SELECT field_overrides FROM deals WHERE id = :id"), {"id": deal_id})).first()
        ov = json.loads(row[0])
        for k in drop:
            ov.pop(k, None)
        ov.update(patch)
        await s.execute(text("UPDATE deals SET field_overrides = :ov WHERE id = :id"), {"ov": json.dumps(ov), "id": deal_id})
        await s.commit()
    return deal_id, tenant_id


async def _load(deal_id: str, tenant_id: str, overrides: dict | None = None, **kw) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    async with get_session_factory()() as s:
        return await _load_engine_inputs(s, deal_id, overrides, tenant_id=tenant_id, **kw)


def _revenue(deal_id: str, base: dict):
    from app.engines.revenue import RevenueEngine
    from app.services.engine_runner import _build_input_for

    return RevenueEngine().run(_build_input_for("revenue", deal_id, base, {}))


@pytest.mark.asyncio
async def test_growth_only_year1_is_the_t12_and_growth_is_the_resolver() -> None:
    deal_id, tenant_id = await _docs_deal()
    base = await _load(deal_id, tenant_id)
    src = base["__sources__"]
    status = base["__topline_status__"]
    assert status["topline_mode"] == "growth_only"
    assert status["methodology_version"] == 3
    # Year 1: the subject's own T-12 — NOT the comp set × penetration × ramp.
    assert base["starting_occupancy"] == pytest.approx(T12_OCC, rel=1e-6)
    assert base["starting_adr"] == pytest.approx(T12_ADR, rel=1e-6)
    assert src["starting_occupancy"] == src["starting_adr"] == "t12_actual"
    assert base.get("topline") is None
    # Growth: Year 1 is not grown (the T-12 is Year 1 as reported); Years 2–4
    # = CoStar 2027–2029 on the close-2025-12-31 calendar (Year 1 = 2026).
    rows = status["growth_by_year"]
    assert [r["deal_year"]["label"] for r in rows] == [f"Jan–Dec {c}" for c in (2026, 2027, 2028, 2029, 2030)]
    assert [r["applied"] for r in rows] == [False, True, True, True, True]
    assert base["adr_growth_by_year"] == pytest.approx([None, -0.006, 0.038, 0.028, LUU_ADR_AVG])
    # Occupancy: CoStar through the Year-3 stabilization (2028), held after (1b).
    assert base["occupancy_growth_by_year"] == pytest.approx([None, 0.0, -0.012, 0.0, 0.0])
    assert [r["occupancy"]["source"] for r in rows[1:]] == [
        "costar_submarket_forecast", "costar_submarket_forecast", "held_at_stabilization", "held_at_stabilization",
    ]
    assert rows[4]["adr"]["source"] == "carried_forward"
    assert "CoStar forecast average (as published) +2.5%" in rows[4]["adr"]["label"]
    assert rows[4]["adr"]["chip"] == "CoStar p.4"
    assert status["growth_state"] == "sourced"
    # Benchmark × penetration × ramp is not in use and says so.
    assert status["benchmark_level"] == "none"
    assert status["benchmark_by_year"] == []
    assert status["penetration_occ"] is None and status["penetration_adr"] is None
    assert status["ramp_by_year"] == [] and status["ramp_source"] == "not_applied_growth_only"
    assert status["stabilization_year"] == 3
    assert status["year1_sources"] == {"occupancy": "t12_actual", "adr": "t12_actual"}
    # The stored penetration settings (the reference tab's) are kept, not applied.
    assert any("penetration" in n.lower() and "not applied" in n.lower() for n in status["notes"])
    # Exit growth = Year 5 (2030) ADR past the series: the forecast average (1a).
    assert base["revpar_growth"] == pytest.approx(LUU_ADR_AVG)


@pytest.mark.asyncio
async def test_growth_only_engine_run_compounds_the_t12_by_the_resolved_growth() -> None:
    deal_id, tenant_id = await _docs_deal()
    base = await _load(deal_id, tenant_id)
    out = _revenue(deal_id, base)
    assert out.topline_methodology_version == 3
    assert out.topline_mode == "growth_only"
    occ = [y.occupancy for y in out.years]
    adr = [y.adr for y in out.years]
    assert occ[0] == pytest.approx(T12_OCC, rel=1e-6)
    assert occ[1] == pytest.approx(T12_OCC * 1.0, rel=1e-9)
    assert occ[2] == pytest.approx(T12_OCC * 0.988, rel=1e-6)
    assert occ[3] == occ[2] and occ[4] == occ[2]  # flat after stabilization
    assert adr[1] == pytest.approx(T12_ADR * 0.994, rel=1e-6)
    assert adr[4] == pytest.approx(adr[3] * (1 + LUU_ADR_AVG), rel=1e-12)
    rec = out.topline_by_year
    assert rec[3].occupancy_growth_source == "held_at_stabilization"
    assert rec[4].adr_growth_source == "carried_forward"
    assert "forecast average" in (rec[4].growth_label or "")
    assert all(r.method != "benchmark_penetration_ramp" for r in rec)
    trace = out.provenance["years[3].occupancy"]
    assert "flat after stabilization" in json.dumps(trace.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_benchmark_mode_on_the_same_deal_runs_the_full_topline(topline_benchmark: None) -> None:
    deal_id, tenant_id = await _docs_deal()
    base = await _load(deal_id, tenant_id)
    status = base["__topline_status__"]
    assert status["topline_mode"] == "benchmark"
    assert status["benchmark_level"] == "comp_set"
    assert base["__sources__"]["starting_occupancy"] == "topline_benchmark"
    assert base["topline"] is not None
    out = _revenue(deal_id, base)
    assert out.topline_mode == "benchmark"


@pytest.mark.asyncio
async def test_growth_only_keeps_an_analyst_year1_and_an_str_noted_rate() -> None:
    deal_id, tenant_id = await _docs_deal(
        starting_occupancy={"value": 0.78, "note": "STR comp-set market rates (Market tab)"},
        starting_adr={"value": 250.0, "note": "Broker underwriting"},
    )
    base = await _load(deal_id, tenant_id)
    src = base["__sources__"]
    assert base["starting_occupancy"] == pytest.approx(0.78)
    assert src["starting_occupancy"] == "str_comp_set"  # as before topline v2
    assert base["starting_adr"] == pytest.approx(250.0)
    assert src["starting_adr"] == "analyst_override"
    flags = base["__topline_status__"]["legacy_flags"]
    assert all(f["key"] != "starting_occupancy" for f in flags)


@pytest.mark.asyncio
async def test_growth_only_never_seeds_a_year1_rate() -> None:
    from app.services.engine_runner import _load_engine_inputs  # noqa: F401

    base = await _load(
        KIMPTON_FIXTURE_DEAL_ID, str(uuid4()),
        assumptions={k: v for k, v in kimpton_seed().items() if k not in ("starting_occupancy", "starting_adr")},
    )
    assert "starting_occupancy" not in base and "starting_adr" not in base
    assert base["__reasons__"]["starting_occupancy"]["code"].value == "no_revenue_basis"
    assert base["__topline_status__"]["revenue_basis"]["state"] == "missing"
    assert base["__topline_status__"]["topline_mode"] == "growth_only"


@pytest.mark.asyncio
async def test_growth_only_no_documents_is_flat_with_the_labelled_suggestion() -> None:
    base = await _load(KIMPTON_FIXTURE_DEAL_ID, str(uuid4()), assumptions=kimpton_seed())
    status = base["__topline_status__"]
    assert status["growth_state"] == "unavailable"
    assert base["occupancy_growth_by_year"] == [None, None, None, 0.0, 0.0]
    assert base["adr_growth_by_year"] == [None] * 5
    assert base["revpar_growth"] == 0.0
    assert base["__sources__"]["revpar_growth"] == "market_forecast_unavailable"
    assert status["suggestions"][0]["value"] == 0.045


async def _drop_t12_add_cbre(deal_id: str, tenant_id: str) -> None:
    """Remove the T-12 and add a CBRE Hotel Horizons extraction whose All
    Hotels curve runs 2022–2027 (first forecast year 2025, ADR $300)."""
    from datetime import UTC, datetime

    from app.database import get_session_factory

    fields: list[dict] = []
    for year, adr in {2022: 270.0, 2023: 280.0, 2024: 290.0, 2025: 300.0, 2026: 309.0, 2027: 318.0}.items():
        fields.append({"field_name": f"cbre_horizons.segment_all.{year}.adr_usd", "value": adr, "source_page": 3})
    async with get_session_factory()() as s:
        await s.execute(text("DELETE FROM extraction_results WHERE document_id = '5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a23'"))
        await s.execute(text("DELETE FROM documents WHERE id = '5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a23'"))
        now = datetime(2025, 12, 31, 12, 0, tzinfo=UTC)
        doc = "5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a24"
        await s.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, uploaded_at) "
                "VALUES (:id, :deal, :t, 'Horizons.xlsx', 'CBRE_HORIZONS', 'EXTRACTED', :now)"
            ),
            {"id": doc, "deal": deal_id, "t": tenant_id, "now": now},
        )
        await s.execute(
            text(
                "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields, confidence_report, "
                "agent_version, created_at) VALUES (:id, :doc, :deal, :t, :f, '{}', 'fixture', :now)"
            ),
            {"id": doc[:-2] + "c4", "doc": doc, "deal": deal_id, "t": tenant_id, "f": json.dumps(fields), "now": now},
        )
        await s.commit()


@pytest.mark.asyncio
async def test_growth_only_cbre_first_forecast_year_adr_is_year1_without_a_t12() -> None:
    deal_id, tenant_id = await _docs_deal(starting_occupancy={"value": 0.8, "note": "Broker underwriting"})
    await _drop_t12_add_cbre(deal_id, tenant_id)
    base = await _load(deal_id, tenant_id)
    assert base["starting_adr"] == pytest.approx(300.0)
    assert base["__sources__"]["starting_adr"] == "cbre_horizons"
    assert base["starting_occupancy"] == pytest.approx(0.8)
    assert base["__topline_status__"]["year1_sources"] == {"occupancy": "analyst_override", "adr": "cbre_horizons"}


@pytest.mark.asyncio
async def test_benchmark_mode_never_anchors_year1_on_cbre(topline_benchmark: None) -> None:
    deal_id, tenant_id = await _docs_deal(starting_occupancy={"value": 0.8, "note": "Broker underwriting"})
    await _drop_t12_add_cbre(deal_id, tenant_id)
    base = await _load(deal_id, tenant_id)
    assert base["__sources__"]["starting_adr"] == "topline_benchmark"


@pytest.mark.asyncio
async def test_growth_only_honours_the_retired_str_switch_with_the_subject_ttm() -> None:
    deal_id, tenant_id = await _docs_deal(revenue_seed_from_str_forecast={"value": True, "note": "STR market rates enabled from the Market tab"})
    base = await _load(deal_id, tenant_id)
    src = base["__sources__"]
    assert src["starting_occupancy"] == src["starting_adr"] == "str_subject_ttm"
    assert src["revenue_seed_from_str_forecast"] == "str_subject_ttm"
    # The STR TTM to May 2025: comp set × MPI / ARI (STR's own subject row).
    assert base["starting_occupancy"] == pytest.approx(0.828892, abs=5e-4)
    assert "starting_occupancy" not in base["__source_fields__"]
    # Its control is retired: listed (clearable), and honoured until cleared.
    flags = {f["key"]: f["treatment"] for f in base["__topline_status__"]["legacy_flags"]}
    assert flags["revenue_seed_from_str_forecast"] == "applied_growth_only"


@pytest.mark.asyncio
async def test_growth_only_str_switch_with_no_str_report_is_never_silent() -> None:
    from app.database import get_session_factory

    deal_id, tenant_id = await _docs_deal(revenue_seed_from_str_forecast=True)
    async with get_session_factory()() as s:
        await s.execute(text("DELETE FROM extraction_results WHERE document_id = '5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a21'"))
        await s.execute(text("DELETE FROM documents WHERE id = '5b0c1f0e-6d43-5c1b-9a7e-2f6b1d0c4a21'"))
        await s.commit()
    base = await _load(deal_id, tenant_id)
    src = base["__sources__"]
    assert src["revenue_seed_from_str_forecast"] == "str_forecast_unavailable"
    assert base["__reasons__"]["revenue_seed_from_str_forecast"]["code"].value == "str_unavailable"
    # No synthetic STR forecast point: Year 1 stays on the T-12.
    assert src["starting_occupancy"] == "t12_actual"


@pytest.mark.asyncio
async def test_benchmark_mode_lists_the_retired_str_switch_as_legacy(topline_benchmark: None) -> None:
    deal_id, tenant_id = await _docs_deal(revenue_seed_from_str_forecast=True)
    base = await _load(deal_id, tenant_id)
    flags = {f["key"]: f["treatment"] for f in base["__topline_status__"]["legacy_flags"]}
    assert flags["revenue_seed_from_str_forecast"] == "ignored"
    assert base["__sources__"]["starting_occupancy"] == "topline_benchmark"
