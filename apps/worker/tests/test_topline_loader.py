"""FON-116 / FON-117 — the loader switch to topline v2 (plan §2.5, §4.1, §6):
one unconditional step, the ``topline_status`` block, legacy translation, the
data policy reaching the resolvers, the no-growth-seed contract."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

#: FON-116 staged rollout — this module pins the BENCHMARK topline (FON-117,
#: ``TOPLINE_BENCHMARK_ENABLED`` on); the deployed growth-only default is
#: ``tests/test_topline_growth_only.py``.
pytestmark = pytest.mark.usefixtures("topline_benchmark")
from sqlalchemy import text

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-topline-loader.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

from tests.fixtures.kimpton_docs_fixture import (  # noqa: E402
    KIMPTON_DOCS_FIXTURE_TENANT_ID,
    seed_kimpton_docs_deal,
)
from tests.fixtures.kimpton_seed import KIMPTON_FIXTURE_DEAL_ID, kimpton_seed  # noqa: E402


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


async def _load(deal_id: str, tenant_id: str, overrides: dict | None = None) -> dict:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    async with get_session_factory()() as s:
        return await _load_engine_inputs(s, deal_id, overrides, tenant_id=tenant_id)


async def _docs_deal(**patch_overrides) -> tuple[str, str]:
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        deal_id, tenant_id = await seed_kimpton_docs_deal(s)
        if patch_overrides:
            row = (await s.execute(text("SELECT field_overrides FROM deals WHERE id = :id"), {"id": deal_id})).first()
            ov = json.loads(row[0])
            for k, v in patch_overrides.items():
                if v is None:
                    ov.pop(k, None)
                else:
                    ov[k] = v
            await s.execute(text("UPDATE deals SET field_overrides = :ov WHERE id = :id"), {"ov": json.dumps(ov), "id": deal_id})
            await s.commit()
    return deal_id, tenant_id


@pytest.mark.asyncio
async def test_documents_deal_runs_the_reference_path_through_the_loader() -> None:
    deal_id, tenant_id = await _docs_deal()
    base = await _load(deal_id, tenant_id)
    status = base["__topline_status__"]
    assert status["methodology_version"] == 3
    assert status["benchmark_level"] == "comp_set"
    assert status["benchmark"]["source_id"] == "str_comp_set"
    assert status["class_bucket"] == "luxury_upper_upscale"
    assert status["growth_state"] == "sourced"
    assert status["current_mpi"]["basis"] == "str_published"
    # Year 1 (2026): comp set 0.754959 × (1 + 0.0) × MPI 100; ADR 310.7753 × 0.98 × 0.97.
    assert base["starting_occupancy"] == pytest.approx(0.754959, abs=1e-6)
    assert base["starting_adr"] == pytest.approx(310.7753 * 0.98 * 0.97, rel=1e-6)
    assert base["__sources__"]["starting_occupancy"] == "topline_benchmark"
    # No growth seed: the scalars are absent; the per-year lists carry the growth.
    assert "occupancy_growth" not in base and "adr_growth" not in base
    assert base["adr_growth_by_year"][:3] == pytest.approx([-0.02, -0.006, 0.038])
    # Returns' terminal growth = Year 5 (2030) ADR growth 2.0% (override) — the
    # 0.5% occupancy step does not compound in perpetuity (review I-2).
    assert base["revpar_growth"] == pytest.approx(0.02)
    assert base["__sources__"]["revpar_growth"] == "analyst_override"


@pytest.mark.asyncio
async def test_defaults_on_the_documents_deal_ramp_and_carry_the_forecast() -> None:
    deal_id, tenant_id = await _docs_deal(
        occupancy_growth_by_year=None, adr_growth_by_year=None, index_mpi_target=None,
        index_ari_target=None, index_mpi_target_by_year=None, index_ari_target_by_year=None,
    )
    base = await _load(deal_id, tenant_id)
    status = base["__topline_status__"]
    assert status["penetration_occ_source"] == "default"
    assert status["ramp_by_year"] == [0.85, 0.95]
    assert base["starting_occupancy"] == pytest.approx(0.754959 * 0.988 * 0.85, rel=1e-5)
    assert [r["adr"]["source"] for r in status["growth_by_year"]] == ["costar_submarket_forecast"] * 4 + ["carried_forward"]
    # Terminal growth = 2030 ADR past the CoStar series: the report's
    # published forecast average (+2.5%, p.4 — FON-121 decision 2), not
    # 2029's +2.8%. Occupancy is
    # flat after the Year-3 stabilization (decision 1b): the 2029 +3.0% and
    # 2030 are held.
    assert base["revpar_growth"] == pytest.approx(0.025)
    occ = [r["occupancy"] for r in status["growth_by_year"]]
    assert [g["source"] for g in occ][3:] == ["held_at_stabilization"] * 2
    assert [g["value"] for g in occ][3:] == [0.0, 0.0]
    assert base["__sources__"]["revpar_growth"] == "growth_carried_forward"


@pytest.mark.asyncio
async def test_withhold_policy_reaches_the_resolvers() -> None:
    from app.tenants import TenantDataPolicies, set_tenant_data_policies, upsert_tenant

    deal_id, tenant_id = await _docs_deal(occupancy_growth_by_year=None, adr_growth_by_year=None)
    await upsert_tenant(
        tenant_id=KIMPTON_DOCS_FIXTURE_TENANT_ID, clerk_org_id="org_topline_test", name="Client",
        kind="external", status="active",
    )
    await set_tenant_data_policies(tenant_id, TenantDataPolicies(costar_derived_defaults="withhold"))
    base = await _load(deal_id, tenant_id)
    status = base["__topline_status__"]
    # The STR comp set is not CoStar: still the benchmark. The CoStar growth is withheld.
    assert status["benchmark_level"] == "comp_set"
    assert status["growth_state"] == "withheld"
    # 2026–2029 are the withheld CoStar years; 2030 is past the report and a
    # withheld year is never carried forward.
    assert [r["adr"]["source"] for r in status["growth_by_year"]] == ["withheld_by_policy"] * 4 + ["unavailable"]
    assert base["__reasons__"]["adr_growth"]["code"].value == "license_withheld"
    assert status["data_policy"]["costar_derived_defaults"] == "withhold"
    assert status["suggestions"] and status["suggestions"][0]["value"] == 0.045


@pytest.mark.asyncio
async def test_no_documents_no_growth_seed_flat_with_the_labelled_suggestion() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    async with get_session_factory()() as s:
        base = await _load_engine_inputs(s, KIMPTON_FIXTURE_DEAL_ID, tenant_id=str(uuid4()), assumptions=kimpton_seed())
    status = base["__topline_status__"]
    assert status["benchmark_level"] == "none"
    assert status["growth_state"] == "unavailable"
    # Years 2–3 have no figure (held flat, unavailable); Years 4–5 are past
    # the Year-3 stabilization: occupancy held by rule (decision 1b).
    assert base["occupancy_growth_by_year"] == [None, None, None, 0.0, 0.0]
    assert base["adr_growth_by_year"] == [None] * 5
    assert base["revpar_growth"] == 0.0
    assert base["__sources__"]["revpar_growth"] == "market_forecast_unavailable"
    assert base["__reasons__"]["revpar_growth"]["code"].value == "no_market_forecast"
    assert status["suggestions"][0]["note_prefill"] == "Platform suggestion 4.5% — no market forecast on file"
    # The seed's starting rates stay (no benchmark to replace them).
    assert base["starting_occupancy"] == 0.762


@pytest.mark.asyncio
async def test_analyst_scalar_growth_still_applies_on_the_seam() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs

    async with get_session_factory()() as s:
        base = await _load_engine_inputs(
            s, KIMPTON_FIXTURE_DEAL_ID, {"occupancy_growth": 0.008, "adr_growth": 0.04},
            tenant_id=str(uuid4()), assumptions=kimpton_seed(),
        )
    # The all-years occupancy figure applies through the Year-3 stabilization,
    # then occupancy is flat (decision 1b); ADR keeps its growth.
    assert base["occupancy_growth_by_year"] == [None, 0.008, 0.008, 0.0, 0.0]
    assert base["adr_growth_by_year"] == [None, 0.04, 0.04, 0.04, 0.04]
    assert base["__topline_status__"]["growth_state"] == "override"
    # Exit growth = the last year's ADR growth; occupancy held (review I-2).
    assert base["revpar_growth"] == pytest.approx(0.04)


@pytest.mark.asyncio
async def test_legacy_keys_are_translated_with_visible_reasons() -> None:
    deal_id, tenant_id = await _docs_deal(
        revenue_seed_from_str_forecast=True,
        index_market_adr_growth=0.03,
        adr_growth_by_year=None,
        starting_occupancy={"value": 0.8, "note": "STR comp-set market rates (Market tab)"},
    )
    base = await _load(deal_id, tenant_id)
    flags = {f["key"]: f for f in base["__topline_status__"]["legacy_flags"]}
    assert flags["revenue_seed_from_str_forecast"]["treatment"] == "ignored"
    assert flags["index_market_adr_growth"]["treatment"] == "read_as_all_years_growth"
    assert flags["starting_occupancy"]["treatment"] == "treated_as_legacy_seed"
    # The STR-noted rate is a legacy seed: the benchmark sets Year 1.
    assert base["__sources__"]["starting_occupancy"] == "topline_benchmark"
    assert base["adr_growth_by_year"][1:] == [0.03] * 4


@pytest.mark.asyncio
async def test_explicit_year_one_value_is_not_penetrated() -> None:
    deal_id, tenant_id = await _docs_deal(starting_adr={"value": 280.0, "note": "Budget rate"})
    base = await _load(deal_id, tenant_id)
    assert base["starting_adr"] == 280.0
    assert base["__sources__"]["starting_adr"] == "analyst_override"
    assert base["__topline_status__"]["explicit_metrics"] == ["adr"]


@pytest.mark.asyncio
async def test_full_chain_on_the_documents_deal_echoes_the_topline() -> None:
    from app.database import get_session_factory
    from app.services.engine_runner import run_all_engines

    deal_id, tenant_id = await _docs_deal()
    async with get_session_factory()() as s:
        results = await run_all_engines(s, deal_id=deal_id, tenant_id=tenant_id, run_id=str(uuid4()))
    rev = results["revenue"]["outputs"]
    rev = rev if isinstance(rev, dict) else rev.model_dump(mode="json")
    assert rev["topline_methodology_version"] == 3
    assert [d["label"] for d in rev["projection_deal_years"]] == [f"Jan–Dec {c}" for c in (2026, 2027, 2028, 2029, 2030)]
    recs = rev["topline_by_year"]
    assert [r["ari_target"] for r in recs] == pytest.approx([97, 98, 99, 100, 100])
    assert {r["occupancy_status"] for r in recs} == {"driven"}
    assert rev["index_drivers_by_year"] == []
    assert results["returns"]["status"] == "complete"


@pytest.mark.asyncio
async def test_stabilization_year_ends_the_ramp_on_a_benchmark_deal() -> None:
    """Topline v2: S is where the penetration reaches its stabilized value.
    Moving S from the Year-3 default to Year 2 removes the Year-2 ramp (95%)."""
    deal_id, tenant_id = await _docs_deal(
        index_mpi_target_by_year=None, index_ari_target_by_year=None,
    )
    s3 = await _load(deal_id, tenant_id)
    deal2, tenant2 = deal_id, tenant_id
    from app.database import get_session_factory

    async with get_session_factory()() as s:
        row = (await s.execute(text("SELECT field_overrides FROM deals WHERE id = :id"), {"id": deal2})).first()
        ov = json.loads(row[0])
        ov["stabilization_year"] = 2
        await s.execute(text("UPDATE deals SET field_overrides = :ov WHERE id = :id"), {"ov": json.dumps(ov), "id": deal2})
        await s.commit()
    s2 = await _load(deal2, tenant2)
    assert s3["__topline_status__"]["ramp_by_year"] == [0.85, 0.95]
    assert s2["__topline_status__"]["ramp_by_year"] == [0.85]
    assert s2["__topline_status__"]["stabilization_source"] == "analyst_override"
    # Year 1 is the same (85% either way); the drivers differ from Year 2.
    assert s2["starting_occupancy"] == pytest.approx(s3["starting_occupancy"])
    assert s2["topline"]["stabilization_year"] == 2 and s3["topline"]["stabilization_year"] == 3


@pytest.mark.asyncio
async def test_exit_growth_follows_the_post_forecast_rule_with_a_cbre_long_run_average_on_file() -> None:
    """FON-121 review M3 (supersedes review I-2's precedence) — the exit-NOI
    year uses its own resolved ADR growth like every year: on this deal the
    analyst's Year-5 ADR growth (+2.0%, the reference scenario) carries to
    the exit, not the CBRE long-run average (which applies only when that
    year has no figure — ``test_topline_terminal_growth.py``)."""
    from app.database import get_session_factory

    deal_id, tenant_id = await _docs_deal()
    doc_id, ext_id = str(uuid4()), str(uuid4())
    fields = [
        {"field_name": "cbre_horizons.long_run_avg.revpar_change_pct", "value": 3.1, "unit": "%"},
        {"field_name": "cbre_horizons.long_run_avg.adr_change_pct", "value": 2.6, "unit": "%"},
    ]
    async with get_session_factory()() as s:
        await s.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, uploaded_at) "
                "VALUES (:id, :deal, :tenant, 'Horizons.xlsx', 'CBRE_HORIZONS', 'EXTRACTED', CURRENT_TIMESTAMP)"
            ),
            {"id": doc_id, "deal": deal_id, "tenant": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO extraction_results (id, document_id, deal_id, tenant_id, fields, "
                "confidence_report, agent_version, created_at) "
                "VALUES (:id, :doc, :deal, :tenant, :f, '{}', 'fixture', CURRENT_TIMESTAMP)"
            ),
            {"id": ext_id, "doc": doc_id, "deal": deal_id, "tenant": tenant_id, "f": json.dumps(fields)},
        )
        await s.commit()
    base = await _load(deal_id, tenant_id)
    term = base["__topline_status__"]["terminal_revpar_growth"]
    assert term["basis"] == "exit_year_adr_growth"
    assert term["value"] == pytest.approx(0.02)
    assert base["revpar_growth"] == pytest.approx(0.02)
    assert base["__sources__"]["revpar_growth"] == "analyst_override"
