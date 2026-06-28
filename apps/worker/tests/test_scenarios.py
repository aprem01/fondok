"""Wave 3 W3.2 — Named scenarios (save/load/diff/run).

Every IC committee asks for upside/downside. These tests pin the
scenario contract end-to-end:

* Every freshly-created deal gets exactly one ``is_base=true`` scenario.
* Analyst CRUD over ``/deals/{id}/scenarios`` is tenant-scoped.
* Running a scenario flows its overrides through the SAME engine_runner
  routing the deal's persisted ``field_overrides`` use — PIP /
  segment / capex routes are wired identically.
* Compare returns side-by-side engine outputs.
* The base scenario is undeletable.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

# Force a per-test SQLite DB BEFORE app modules import so the cached
# Settings/engine pick up the right DSN.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-scenarios.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")


# ─────────────────────────── fixtures ───────────────────────────


@pytest.fixture(autouse=True)
async def _reset_db() -> None:
    """Recreate the schema + truncate before each test."""
    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    factory = get_session_factory()
    async with factory() as session:
        for tbl in (
            "scenarios",
            "audit_log",
            "engine_outputs",
            "extraction_results",
            "documents",
            "deals",
        ):
            try:
                await session.execute(text(f"DELETE FROM {tbl}"))
            except Exception:  # noqa: BLE001 — table may not exist yet
                pass
        await session.commit()
    yield


async def _create_deal_via_api(client, *, name: str = "Scenario Hotel", **kw) -> str:
    body = {"name": name, "city": "Denver", "keys": 120}
    body.update(kw)
    r = await client.post("/deals", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ─────────────────────────── tests ───────────────────────────


@pytest.mark.asyncio
async def test_deal_creation_auto_creates_base_scenario() -> None:
    """POST /deals creates exactly one ``is_base=true`` scenario row."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client)

        r = await client.get(f"/deals/{deal_id}/scenarios")
        assert r.status_code == 200, r.text
        scenarios = r.json()
        assert len(scenarios) == 1
        base = scenarios[0]
        assert base["is_base"] is True
        assert base["overrides"] == []
        assert base["name"]  # whatever the label is, non-empty


@pytest.mark.asyncio
async def test_base_scenario_unique_per_deal() -> None:
    """Idempotent helper: calling create_base_scenario_for_deal twice
    on the same deal returns the same row (no UNIQUE-constraint blow-up)."""
    from app.api.scenarios import create_base_scenario_for_deal
    from app.database import get_session_factory
    from datetime import UTC, datetime

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, :name, 'Draft', :now, :now)
                """
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "name": "Unique Base",
                "now": datetime.now(UTC),
            },
        )
        first = await create_base_scenario_for_deal(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        second = await create_base_scenario_for_deal(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        await session.commit()
        assert first == second
        # Exactly one base row exists.
        rows = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM scenarios "
                    "WHERE deal_id = :d AND is_base = :b"
                ),
                {"d": deal_id, "b": True},
            )
        ).first()
        assert rows[0] == 1


@pytest.mark.asyncio
async def test_create_named_scenario_persists_overrides() -> None:
    """POST /deals/{id}/scenarios with overrides round-trips through GET."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client)

        body = {
            "name": "downside",
            "description": "IC stress test — soft RevPAR",
            "overrides": [
                {"field_path": "starting_occupancy", "value": 0.62},
                {"field_path": "exit_cap_rate", "value": 0.085},
            ],
        }
        r = await client.post(f"/deals/{deal_id}/scenarios", json=body)
        assert r.status_code == 201, r.text
        created = r.json()
        assert created["name"] == "downside"
        assert created["is_base"] is False
        assert len(created["overrides"]) == 2

        sid = created["id"]
        r = await client.get(f"/deals/{deal_id}/scenarios/{sid}")
        assert r.status_code == 200
        fetched = r.json()
        paths = {o["field_path"]: o["value"] for o in fetched["overrides"]}
        assert paths["starting_occupancy"] == 0.62
        assert paths["exit_cap_rate"] == 0.085


@pytest.mark.asyncio
async def test_list_scenarios_tenant_scoped() -> None:
    """Scenarios from another tenant don't leak into the list."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant_a = str(uuid4())
    tenant_b = str(uuid4())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Tenant A owns the deal.
        r = await client.post(
            "/deals",
            json={"name": "Tenant A", "city": "NYC", "keys": 100},
            headers={"X-Tenant-Id": tenant_a},
        )
        assert r.status_code == 201
        deal_id = r.json()["id"]

        # Tenant B trying to read tenant A's scenarios sees a 404 (we
        # never leak the deal's existence cross-tenant).
        r = await client.get(
            f"/deals/{deal_id}/scenarios",
            headers={"X-Tenant-Id": tenant_b},
        )
        assert r.status_code == 404, r.text

        # Tenant A sees their own base scenario.
        r = await client.get(
            f"/deals/{deal_id}/scenarios",
            headers={"X-Tenant-Id": tenant_a},
        )
        assert r.status_code == 200
        assert len(r.json()) == 1


@pytest.mark.asyncio
async def test_cannot_delete_base_scenario() -> None:
    """DELETE on the base scenario returns 409."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client)
        scenarios = (await client.get(f"/deals/{deal_id}/scenarios")).json()
        base = next(s for s in scenarios if s["is_base"])
        r = await client.delete(f"/deals/{deal_id}/scenarios/{base['id']}")
        assert r.status_code == 409, r.text
        assert "base" in r.json()["detail"].lower()

        # And it's still there.
        scenarios = (await client.get(f"/deals/{deal_id}/scenarios")).json()
        assert any(s["is_base"] for s in scenarios)


@pytest.mark.asyncio
async def test_patch_overrides_replaces_not_appends() -> None:
    """PATCH with a new override list REPLACES, doesn't append."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client)
        body = {
            "name": "upside",
            "overrides": [
                {"field_path": "starting_occupancy", "value": 0.85},
                {"field_path": "starting_adr", "value": 410.0},
                {"field_path": "exit_cap_rate", "value": 0.065},
            ],
        }
        r = await client.post(f"/deals/{deal_id}/scenarios", json=body)
        assert r.status_code == 201
        sid = r.json()["id"]

        # Replace with a single override.
        r = await client.patch(
            f"/deals/{deal_id}/scenarios/{sid}",
            json={
                "overrides": [
                    {"field_path": "exit_cap_rate", "value": 0.06},
                ]
            },
        )
        assert r.status_code == 200, r.text
        got = r.json()
        assert len(got["overrides"]) == 1
        assert got["overrides"][0]["field_path"] == "exit_cap_rate"
        assert got["overrides"][0]["value"] == 0.06


@pytest.mark.asyncio
async def test_run_scenario_applies_overrides_to_engine() -> None:
    """POST /scenarios/{id}/run produces engine output reflecting overrides."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client, keys=132)

        # Baseline run — no scenario overrides — for the comparison.
        base_scenarios = (await client.get(f"/deals/{deal_id}/scenarios")).json()
        base_sid = next(s["id"] for s in base_scenarios if s["is_base"])
        r = await client.post(f"/deals/{deal_id}/scenarios/{base_sid}/run")
        assert r.status_code == 200, r.text
        base_irr = r.json()["engines"]["returns"]["outputs"]["levered_irr"]
        base_noi_y1 = r.json()["engines"]["expense"]["outputs"]["years"][0]["noi"]

        body = {
            "name": "downside",
            "overrides": [
                # Crush occupancy + push cap — Y1 occupancy 0.55 (down
                # from Kimpton 0.762), exit cap 0.090 (up from 0.07).
                {"field_path": "starting_occupancy", "value": 0.55},
                {"field_path": "exit_cap_rate", "value": 0.090},
            ],
        }
        r = await client.post(f"/deals/{deal_id}/scenarios", json=body)
        assert r.status_code == 201
        sid = r.json()["id"]

        r = await client.post(f"/deals/{deal_id}/scenarios/{sid}/run")
        assert r.status_code == 200, r.text
        run = r.json()
        assert run["scenario_id"] == sid
        assert run["run_id"]

        # Every engine ran.
        engines = run["engines"]
        for name in (
            "revenue", "fb", "expense", "capital",
            "debt", "returns", "sensitivity", "partnership",
        ):
            assert engines[name]["status"] == "complete", (
                f"engine {name} did not complete: {engines[name]}"
            )

        # Downside flexes BOTH NOI Y1 down AND IRR down vs. the base.
        scen_irr = engines["returns"]["outputs"]["levered_irr"]
        scen_noi_y1 = engines["expense"]["outputs"]["years"][0]["noi"]
        assert scen_noi_y1 < base_noi_y1, (
            f"downside NOI not lower than base: {scen_noi_y1} vs {base_noi_y1}"
        )
        assert scen_irr < base_irr, (
            f"downside IRR not lower than base: {scen_irr} vs {base_irr}"
        )

        # Compare-by-engine_runner-inputs: the loader saw the override
        # (loader run separately confirms the source label flips).
        from app.database import get_session_factory
        from app.services.engine_runner import (
            SOURCE_ANALYST_OVERRIDE,
            _load_engine_inputs,
        )

        factory = get_session_factory()
        async with factory() as session:
            base = await _load_engine_inputs(
                session, deal_id, scenario_id=sid
            )
        assert base["starting_occupancy"] == 0.55
        assert base["exit_cap_rate"] == 0.090
        assert base["__sources__"]["starting_occupancy"] == SOURCE_ANALYST_OVERRIDE
        assert base["__sources__"]["exit_cap_rate"] == SOURCE_ANALYST_OVERRIDE


@pytest.mark.asyncio
async def test_run_scenario_without_overrides_matches_base() -> None:
    """The base scenario is byte-identical to a run with no scenario_id."""
    from app.database import get_session_factory
    from app.services.engine_runner import (
        run_all_engines,
    )
    from datetime import UTC, datetime

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, 'Base Match', 'Draft', :now, :now)
                """
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "now": datetime.now(UTC),
            },
        )
        await session.commit()

        from app.api.scenarios import create_base_scenario_for_deal

        async with factory() as session2:
            base_sid = await create_base_scenario_for_deal(
                session2, deal_id=deal_id, tenant_id=tenant_id
            )
            await session2.commit()

        async with factory() as session3:
            no_scenario = await run_all_engines(
                session3,
                deal_id=deal_id,
                tenant_id=tenant_id,
                run_id=str(uuid4()),
            )

        async with factory() as session4:
            with_base = await run_all_engines(
                session4,
                deal_id=deal_id,
                tenant_id=tenant_id,
                run_id=str(uuid4()),
                scenario_id=str(base_sid),
            )

    # Identical headline numbers.
    a = no_scenario["returns"]["outputs"]
    b = with_base["returns"]["outputs"]
    assert a["levered_irr"] == b["levered_irr"]
    assert a["equity_multiple"] == b["equity_multiple"]
    assert a["gross_sale_price"] == b["gross_sale_price"]


@pytest.mark.asyncio
async def test_compare_returns_side_by_side_outputs() -> None:
    """POST /scenarios/compare returns one column per scenario id."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client)
        base = (await client.get(f"/deals/{deal_id}/scenarios")).json()
        base_id = base[0]["id"]

        r = await client.post(
            f"/deals/{deal_id}/scenarios",
            json={
                "name": "downside",
                "overrides": [
                    {"field_path": "starting_occupancy", "value": 0.55},
                    {"field_path": "exit_cap_rate", "value": 0.085},
                ],
            },
        )
        downside_id = r.json()["id"]

        r = await client.post(
            f"/deals/{deal_id}/scenarios",
            json={
                "name": "upside",
                "overrides": [
                    {"field_path": "starting_occupancy", "value": 0.85},
                    {"field_path": "exit_cap_rate", "value": 0.06},
                ],
            },
        )
        upside_id = r.json()["id"]

        r = await client.post(
            f"/deals/{deal_id}/scenarios/compare",
            json={"scenario_ids": [base_id, downside_id, upside_id]},
        )
        assert r.status_code == 200, r.text
        payload = r.json()
        assert payload["base_scenario_id"] == base_id
        assert len(payload["scenarios"]) == 3

        # Engine output present on every column.
        for cell in payload["scenarios"]:
            assert "returns" in cell["engines"], (
                f"missing returns engine for {cell['scenario_name']}: "
                f"{list(cell['engines'].keys())}"
            )
            assert cell["engines"]["returns"]["status"] == "complete"

        # Downside IRR < base IRR < upside IRR (sanity).
        rows = {c["scenario_name"]: c["engines"]["returns"]["outputs"] for c in payload["scenarios"]}
        assert rows["downside"]["levered_irr"] < rows[base[0]["name"]]["levered_irr"]
        assert rows["upside"]["levered_irr"] > rows[base[0]["name"]]["levered_irr"]


@pytest.mark.asyncio
async def test_compare_rejects_cross_tenant_scenario_ids() -> None:
    """Compare returns 404 when any scenario id belongs to another tenant."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    tenant_a = str(uuid4())
    tenant_b = str(uuid4())

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Tenant A creates a deal + scenario.
        r = await client.post(
            "/deals",
            json={"name": "Tenant A Deal", "city": "NYC", "keys": 100},
            headers={"X-Tenant-Id": tenant_a},
        )
        deal_a = r.json()["id"]
        r = await client.post(
            f"/deals/{deal_a}/scenarios",
            json={
                "name": "stress",
                "overrides": [{"field_path": "exit_cap_rate", "value": 0.09}],
            },
            headers={"X-Tenant-Id": tenant_a},
        )
        scenario_a = r.json()["id"]

        # Tenant B creates their own deal + tries to compare with
        # tenant A's scenario id mixed in.
        r = await client.post(
            "/deals",
            json={"name": "Tenant B Deal", "city": "Austin", "keys": 110},
            headers={"X-Tenant-Id": tenant_b},
        )
        deal_b = r.json()["id"]
        base_b = (
            await client.get(
                f"/deals/{deal_b}/scenarios",
                headers={"X-Tenant-Id": tenant_b},
            )
        ).json()[0]["id"]

        # Compare on deal B carrying scenario A's id → 404.
        r = await client.post(
            f"/deals/{deal_b}/scenarios/compare",
            json={"scenario_ids": [base_b, scenario_a]},
            headers={"X-Tenant-Id": tenant_b},
        )
        assert r.status_code == 404, r.text


@pytest.mark.asyncio
async def test_unique_name_per_deal_enforced() -> None:
    """Creating two scenarios with the same name on one deal returns 409."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client)
        r = await client.post(
            f"/deals/{deal_id}/scenarios",
            json={"name": "downside", "overrides": []},
        )
        assert r.status_code == 201
        r = await client.post(
            f"/deals/{deal_id}/scenarios",
            json={"name": "downside", "overrides": []},
        )
        assert r.status_code == 409, r.text


@pytest.mark.asyncio
async def test_pip_displacement_override_routes_correctly() -> None:
    """Scenario PIP override flows through the same routing deal overrides use."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from datetime import UTC, datetime

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, 'PIP Hotel', 'Draft', :now, :now)
                """
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "now": datetime.now(UTC),
            },
        )

        from app.api.scenarios import create_base_scenario_for_deal

        await create_base_scenario_for_deal(
            session, deal_id=deal_id, tenant_id=tenant_id
        )
        # Insert a non-base scenario with PIP overrides via raw SQL so
        # this test doesn't depend on the API.
        scenario_id = str(uuid4())
        overrides = [
            {
                "field_path": "pip_displacement.closure_strategy",
                "value": "rolling",
            },
            {
                "field_path": "pip_displacement.brand",
                "value": "kimpton",
            },
            {
                "field_path": "pip_displacement.revpar_index_post_reno",
                "value": 1.15,
            },
        ]
        await session.execute(
            text(
                """
                INSERT INTO scenarios (
                    id, deal_id, tenant_id, name, is_base, overrides,
                    created_at, updated_at
                ) VALUES (
                    :id, :deal, :tenant, :name, :is_base, :overrides, :now, :now
                )
                """
            ),
            {
                "id": scenario_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "name": "heavy_pip",
                "is_base": False,
                "overrides": json.dumps(overrides),
                "now": datetime.now(UTC),
            },
        )
        await session.commit()

        base = await _load_engine_inputs(
            session, deal_id, scenario_id=scenario_id
        )

    pip = base.get("pip_displacement_overrides") or {}
    assert pip.get("closure_strategy") == "rolling"
    assert pip.get("brand") == "kimpton"
    assert pip.get("revpar_index_post_reno") == 1.15


@pytest.mark.asyncio
async def test_segment_override_in_scenario() -> None:
    """Scenario segment override lands in ``segments_overrides``."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from datetime import UTC, datetime

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, 'Seg Hotel', 'Draft', :now, :now)
                """
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "now": datetime.now(UTC),
            },
        )
        scenario_id = str(uuid4())
        overrides = [
            {"field_path": "segments.transient_ota.adr", "value": 295.0},
            {"field_path": "segments.transient_ota.channel_cost_pct", "value": 0.22},
        ]
        await session.execute(
            text(
                """
                INSERT INTO scenarios (
                    id, deal_id, tenant_id, name, is_base, overrides,
                    created_at, updated_at
                ) VALUES (
                    :id, :deal, :tenant, :name, 0, :overrides, :now, :now
                )
                """
            ),
            {
                "id": scenario_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "name": "ota_stress",
                "overrides": json.dumps(overrides),
                "now": datetime.now(UTC),
            },
        )
        await session.commit()

        base = await _load_engine_inputs(
            session, deal_id, scenario_id=scenario_id
        )

    seg = base.get("segments_overrides") or {}
    assert seg.get("transient_ota", {}).get("adr") == 295.0
    assert seg.get("transient_ota", {}).get("channel_cost_pct") == 0.22


@pytest.mark.asyncio
async def test_capex_override_in_scenario() -> None:
    """Scenario capex override lands in ``capex_plan_overrides``."""
    from app.database import get_session_factory
    from app.services.engine_runner import _load_engine_inputs
    from datetime import UTC, datetime

    deal_id = str(uuid4())
    tenant_id = str(uuid4())
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO deals (id, tenant_id, name, status, created_at, updated_at)
                VALUES (:id, :tenant, 'Capex Hotel', 'Draft', :now, :now)
                """
            ),
            {
                "id": deal_id,
                "tenant": tenant_id,
                "now": datetime.now(UTC),
            },
        )
        scenario_id = str(uuid4())
        overrides = [
            {"field_path": "capex_plan.pip.total_usd", "value": 8_500_000},
            {"field_path": "capex_plan.pip.per_key_usd", "value": 64_393.94},
        ]
        await session.execute(
            text(
                """
                INSERT INTO scenarios (
                    id, deal_id, tenant_id, name, is_base, overrides,
                    created_at, updated_at
                ) VALUES (
                    :id, :deal, :tenant, :name, 0, :overrides, :now, :now
                )
                """
            ),
            {
                "id": scenario_id,
                "deal": deal_id,
                "tenant": tenant_id,
                "name": "deep_pip",
                "overrides": json.dumps(overrides),
                "now": datetime.now(UTC),
            },
        )
        await session.commit()

        base = await _load_engine_inputs(
            session, deal_id, scenario_id=scenario_id
        )

    capex = base.get("capex_plan_overrides") or {}
    assert capex.get("pip", {}).get("total_usd") == 8_500_000
    assert capex.get("pip", {}).get("per_key_usd") == 64_393.94


@pytest.mark.asyncio
async def test_last_run_id_updates_on_run() -> None:
    """POST /scenarios/{id}/run stamps ``scenarios.last_run_id``."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        deal_id = await _create_deal_via_api(client)
        r = await client.post(
            f"/deals/{deal_id}/scenarios",
            json={
                "name": "run_tracker",
                "overrides": [
                    {"field_path": "starting_occupancy", "value": 0.70},
                ],
            },
        )
        sid = r.json()["id"]
        assert r.json()["last_run_id"] is None

        r = await client.post(f"/deals/{deal_id}/scenarios/{sid}/run")
        assert r.status_code == 200
        first_run = r.json()["run_id"]

        # Re-fetch the scenario record — last_run_id should equal the run id.
        r = await client.get(f"/deals/{deal_id}/scenarios/{sid}")
        assert r.status_code == 200
        assert r.json()["last_run_id"] == first_run

        # Running again advances last_run_id.
        r = await client.post(f"/deals/{deal_id}/scenarios/{sid}/run")
        second_run = r.json()["run_id"]
        assert second_run != first_run
        r = await client.get(f"/deals/{deal_id}/scenarios/{sid}")
        assert r.json()["last_run_id"] == second_run
