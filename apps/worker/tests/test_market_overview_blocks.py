"""FON-61 — ``GET /market/{deal_id}/overview`` Market-tab blocks, end to end.

Seeds a deal with an STR_TREND extraction (roster with one closed hotel,
subject TTM + indices) and a MARKET_STUDY extraction (inventory, pipeline,
demand trend) in a per-file SQLite DB, then asserts the four additive
blocks: ``comp_set`` (one derivation → count AND keys over active hotels),
``ttm_blend`` (rows + period behind the blend), ``demand_growth`` and
``supply_growth`` (values with document + field provenance, or the reason
code when absent). Same bootstrap as ``test_market_property_name_override``.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-market-overview-blocks.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("EVALS_MOCK", "true")

STR_DOC = "STR Trend - Anglers - Jun 2026.xlsx"
MS_DOC = "CoStar Submarket Report - South Beach.pdf"

STR_FIELDS: list[dict[str, Any]] = [
    {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.714, "source_page": 3},
    {"field_name": "ttm_performance.subject.adr_usd", "value": 278.0, "source_page": 3},
    {"field_name": "ttm_performance.subject.revpar_usd", "value": 198.5, "source_page": 3},
    {"field_name": "ttm_performance.indices.mpi_occupancy_index", "value": 103.2, "source_page": 4},
    {"field_name": "ttm_performance.indices.ari_adr_index", "value": 94.2, "source_page": 4},
    {"field_name": "ttm_performance.indices.rgi_revpar_index", "value": 97.2, "source_page": 4},
    {"field_name": "ttm_performance.subject.monthly.2025_07.occupancy_pct", "value": 0.7, "source_page": 8},
    {"field_name": "ttm_performance.subject.monthly.2026_06.occupancy_pct", "value": 0.72, "source_page": 8},
    {"field_name": "ttm_performance.compset.1.name", "value": "Z Ocean Hotel", "source_page": 22},
    {"field_name": "ttm_performance.compset.1.keys", "value": 40, "unit": "rooms", "source_page": 22},
    {"field_name": "ttm_performance.compset.2.name", "value": "Closed - Blue Moon Hotel", "source_page": 22},
    {"field_name": "ttm_performance.compset.2.keys", "value": 0, "unit": "rooms", "source_page": 22},
    {"field_name": "ttm_performance.compset.3.name", "value": "The Betsy South Beach", "source_page": 22},
    {"field_name": "ttm_performance.compset.3.keys", "value": 129, "unit": "rooms", "source_page": 22},
    {"field_name": "ttm_performance.compset.4.name", "value": "The Tony Hotel of South Beach", "source_page": 22},
    {"field_name": "ttm_performance.compset.4.keys", "value": 68, "unit": "rooms", "source_page": 22},
    {"field_name": "ttm_performance.compset.5.name", "value": "Dream South Beach", "source_page": 22},
    {"field_name": "ttm_performance.compset.5.keys", "value": 107, "unit": "rooms", "source_page": 22},
    # The report's own rollup counts the closed hotel — the tester's "5 hotels".
    {"field_name": "comp_set.comp_set_size", "value": 5, "source_page": 22},
    {"field_name": "comp_set.total_keys", "value": 344, "unit": "rooms", "source_page": 22},
]

MS_FIELDS: list[dict[str, Any]] = [
    {"field_name": "market_study.submarket", "value": "South Beach", "source_page": 1},
    {"field_name": "market_study.supply.existing_rooms", "value": 12000, "unit": "rooms", "source_page": 3},
    {"field_name": "under_construction.total_rooms", "value": 600, "unit": "rooms", "source_page": 9},
    {"field_name": "market_study.supply.final_planning_rooms", "value": 300, "unit": "rooms", "source_page": 9},
    {"field_name": "market_study.trend.2025.demand_change_pct", "value": 0.018, "unit": "pct", "source_page": 7},
    {"field_name": "market_study.trend.ttm.demand_change_pct", "value": 0.042, "unit": "pct", "source_page": 6},
    {"field_name": "market_study.trend.ttm.supply_change_pct", "value": 0.012, "unit": "pct", "source_page": 6},
]


async def _seed(*, str_fields: list[dict[str, Any]] | None, ms_fields: list[dict[str, Any]] | None) -> tuple[Any, Any]:
    from sqlalchemy import text

    from app.database import get_session_factory
    from app.migrations import run_startup_migrations

    await run_startup_migrations()
    tenant_id = uuid4()
    deal_id = uuid4()
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO deals (id, tenant_id, name, city, keys, "
                "purchase_price, service, status, deal_stage, risk, "
                "ai_confidence, field_overrides, created_at, updated_at) "
                "VALUES (:id, :tenant, 'Project Unicorn', 'Miami Beach', 132, "
                "36000000, 'Full Service', 'Draft', 'Teaser', 'Medium', "
                "0.8, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": str(deal_id), "tenant": str(tenant_id)},
        )
        now = datetime.now(UTC)
        for i, (doc_type, filename, fields) in enumerate(
            [("STR_TREND", STR_DOC, str_fields), ("MARKET_STUDY", MS_DOC, ms_fields)]
        ):
            if fields is None:
                continue
            document_id = uuid4()
            await session.execute(
                text(
                    "INSERT INTO documents (id, deal_id, tenant_id, filename, "
                    "doc_type, status, storage_key, size_bytes) "
                    "VALUES (:id, :deal, :tenant, :filename, :doc_type, "
                    "'EXTRACTED', :key, 100)"
                ),
                {
                    "id": str(document_id), "deal": str(deal_id), "tenant": str(tenant_id),
                    "filename": filename, "doc_type": doc_type, "key": f"{i}.bin",
                },
            )
            await session.execute(
                text(
                    "INSERT INTO extraction_results (id, deal_id, document_id, "
                    "tenant_id, fields, created_at) "
                    "VALUES (:id, :deal, :doc, :tenant, :fields, :created)"
                ),
                {
                    "id": str(uuid4()), "deal": str(deal_id), "doc": str(document_id),
                    "tenant": str(tenant_id), "fields": json.dumps(fields),
                    "created": (now - timedelta(minutes=i)).isoformat(sep=" "),
                },
            )
        await session.commit()
    return tenant_id, deal_id


async def _overview(tenant_id: Any, deal_id: Any) -> dict[str, Any]:
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/market/{deal_id}/overview", headers={"X-Tenant-Id": str(tenant_id)})
        assert r.status_code == 200, f"got {r.status_code}: {r.text[:300]}"
        return r.json()


@pytest.mark.asyncio
async def test_comp_set_block_counts_active_hotels_for_both_count_and_keys() -> None:
    tenant_id, deal_id = await _seed(str_fields=STR_FIELDS, ms_fields=MS_FIELDS)
    body = await _overview(tenant_id, deal_id)
    cs = body["comp_set"]
    assert cs["active_count"] == 4
    assert cs["active_keys"] == 344
    assert cs["closed_count"] == 1
    assert cs["closed_names"] == ["Blue Moon Hotel"]
    assert cs["count_basis"] == "active_roster" and cs["keys_basis"] == "active_roster"
    assert cs["status_available"] is True
    assert cs["reported_comp_set_size"] == 5  # the tester's "5 hotels", no longer the headline
    assert cs["source_doc_name"] == STR_DOC and cs["source_page"] == 22
    hotels = {h["name"]: h for h in cs["hotels"]}
    assert hotels["Blue Moon Hotel"]["status"] == "closed"
    assert hotels["Blue Moon Hotel"]["status_source"] == "str_closed_label"
    assert hotels["Blue Moon Hotel"]["name_as_reported"] == "Closed - Blue Moon Hotel"
    assert hotels["Z Ocean Hotel"]["status"] == "active"
    assert "Blue Moon Hotel" in cs["note"]


@pytest.mark.asyncio
async def test_ttm_blend_block_names_rows_period_and_documents() -> None:
    tenant_id, deal_id = await _seed(str_fields=STR_FIELDS, ms_fields=None)
    body = await _overview(tenant_id, deal_id)
    b = body["ttm_blend"]
    assert abs(b["occupancy_pct"] - 71.4 / 1.032) < 1e-3
    assert abs(b["adr_usd"] - 278 / 0.942) < 1e-3
    assert b["period_basis"] == "subject_monthly_series"
    assert b["period_start"] == "2025-07" and b["period_end"] == "2026-06" and b["months"] == 2
    assert b["documents"] == [STR_DOC]
    assert {r["field_name"] for r in b["inputs"]} >= {
        "ttm_performance.subject.occupancy_pct",
        "ttm_performance.indices.mpi_occupancy_index",
    }
    assert all(r["doc_name"] == STR_DOC for r in b["inputs"])
    assert "÷ MPI" in b["method"]
    # No MARKET_STUDY on the deal → both growth tiles carry the reason code.
    assert body["demand_growth"]["value_pct"] is None
    assert body["demand_growth"]["reason"] == "no_document"
    assert body["supply_growth"]["reason"] == "no_document"


@pytest.mark.asyncio
async def test_growth_blocks_read_the_market_study_with_provenance() -> None:
    tenant_id, deal_id = await _seed(str_fields=STR_FIELDS, ms_fields=MS_FIELDS)
    body = await _overview(tenant_id, deal_id)
    d = body["demand_growth"]
    assert d["value_pct"] == 4.2 and d["period_label"] == "TTM" and d["basis"] == "reported"
    assert d["reason"] is None
    assert d["inputs"] == [
        {
            "field_name": "market_study.trend.ttm.demand_change_pct",
            "value": 0.042,
            "doc_name": MS_DOC,
            "doc_id": d["inputs"][0]["doc_id"],
            "page": 6,
        }
    ]
    s = body["supply_growth"]
    assert s["existing_rooms"] == 12000
    assert s["under_construction_rooms"] == 600 and s["under_construction_pct"] == 5.0
    assert s["final_planning_rooms"] == 300 and s["final_planning_pct"] == 2.5
    assert s["reported_supply_change_pct"] == 1.2 and s["reported_supply_change_period"] == "TTM"
    assert s["reason"] is None
    assert [r["field_name"] for r in s["inputs"]] == [
        "market_study.supply.existing_rooms",
        "under_construction.total_rooms",
        "market_study.supply.final_planning_rooms",
        "market_study.trend.ttm.supply_change_pct",
    ]
    assert all(r["doc_name"] == MS_DOC for r in s["inputs"])


@pytest.mark.asyncio
async def test_market_study_without_the_series_reports_no_source() -> None:
    sparse = [{"field_name": "market_study.submarket", "value": "South Beach"}]
    tenant_id, deal_id = await _seed(str_fields=None, ms_fields=sparse)
    body = await _overview(tenant_id, deal_id)
    assert body["comp_set"] is None and body["ttm_blend"] is None
    assert body["demand_growth"]["reason"] == "no_source"
    assert "not in the uploaded reports" in body["demand_growth"]["detail"]
    assert body["supply_growth"]["reason"] == "no_source"
    assert body["supply_growth"]["under_construction_pct"] is None


# The LIVE shape of the tester's two CoStar extractions (verbatim paths):
# the submarket report under pnl_benchmark.market.* / property_overview.*,
# and the multi-market pipeline export under market_study.pipeline.<slug>.*.
COSTAR_SUBMARKET_FIELDS: list[dict[str, Any]] = [
    {"field_name": "property_overview.submarket", "value": "Miami Beach", "source_page": 1},
    {"field_name": "pnl_benchmark.market.demand_change_2022_annual", "value": 0.25, "source_page": 6},
    {"field_name": "pnl_benchmark.market.demand_change_2026_forecast", "value": 0.05, "source_page": 6},
    {"field_name": "pnl_benchmark.market.supply_change_2022_annual", "value": 0.15, "source_page": 6},
    {"field_name": "property_overview.rooms_under_construction_count", "value": 1300, "source_page": 3},
    {"field_name": "property_overview.under_construction_pct_of_inventory", "value": 0.057, "source_page": 3},
    {"field_name": "property_overview.final_planning_rooms", "value": 1300, "source_page": 3},
]
COSTAR_EXPORT_FIELDS: list[dict[str, Any]] = [
    {"field_name": f"market_study.pipeline.{slug}.{attr}", "value": value, "source_page": 1}
    for slug, fields in (
        ("shore_club", {"name": "Shore Club", "market": "Miami, FL", "submarket": "Miami Beach", "keys": 100, "status": "Under Construction"}),
        ("boston_seaport", {"name": "Seaport Hotel", "market": "Boston, MA", "submarket": "Seaport", "keys": 400, "status": "Under Construction"}),
        ("tampa_water", {"name": "Water Street", "market": "Tampa Bay, FL", "submarket": "Downtown Tampa", "keys": 250, "status": "Final Planning"}),
    )
    for attr, value in fields.items()
]


@pytest.mark.asyncio
async def test_live_costar_paths_feed_the_tiles_and_the_export_is_filtered_to_the_deal_city() -> None:
    tenant_id, deal_id = await _seed(str_fields=None, ms_fields=COSTAR_SUBMARKET_FIELDS)
    # Add the export as a second MARKET_STUDY document on the same deal.
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        document_id = uuid4()
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, storage_key, size_bytes) "
                "VALUES (:id, :deal, :tenant, 'Miami Beach Supply 12.10.25.xlsx', 'MARKET_STUDY', 'EXTRACTED', 'x.xlsx', 100)"
            ),
            {"id": str(document_id), "deal": str(deal_id), "tenant": str(tenant_id)},
        )
        await session.execute(
            text(
                "INSERT INTO extraction_results (id, deal_id, document_id, tenant_id, fields, created_at) "
                "VALUES (:id, :deal, :doc, :tenant, :fields, :created)"
            ),
            {
                "id": str(uuid4()), "deal": str(deal_id), "doc": str(document_id), "tenant": str(tenant_id),
                "fields": json.dumps(COSTAR_EXPORT_FIELDS),
                "created": (datetime.now(UTC) - timedelta(minutes=5)).isoformat(sep=" "),
            },
        )
        await session.commit()

    body = await _overview(tenant_id, deal_id)
    d = body["demand_growth"]
    assert d["value_pct"] == 25.0 and d["period_label"] == "2022" and d["basis"] == "reported"
    assert d["forecast_pct"] == 5.0 and d["forecast_label"] == "2026 forecast"
    assert d["inputs"][0]["field_name"] == "pnl_benchmark.market.demand_change_2022_annual"
    assert d["inputs"][0]["doc_name"] == MS_DOC
    s = body["supply_growth"]
    assert s["under_construction_rooms"] == 1300
    assert s["under_construction_pct"] == 5.7 and s["under_construction_pct_basis"] == "reported"
    assert s["existing_rooms"] is None
    assert s["final_planning_rooms"] == 1300 and s["final_planning_pct"] is None
    assert s["reported_supply_change_pct"] == 15.0 and s["reported_supply_change_period"] == "2022"
    assert s["reason"] is None
    # The deal's city ("Miami Beach") + the report's own submarket filter the export.
    assert [h["name"] for h in s["pipeline_hotels"]] == ["Shore Club"]
    assert s["pipeline_hotels"][0]["doc_name"] == "Miami Beach Supply 12.10.25.xlsx"
    assert s["pipeline_filter"]["matched"] == 1 and s["pipeline_filter"]["total"] == 3
    assert s["pipeline_filter"]["terms"] == ["miami beach"]


async def _add_document(
    *,
    deal_id: Any,
    tenant_id: Any,
    filename: str,
    doc_type: str,
    fields: list[dict[str, Any]],
    agent_version: str | None = None,
    minutes_ago: int = 0,
) -> None:
    """Insert one more document + extraction on an existing deal."""
    from sqlalchemy import text

    from app.database import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        document_id = uuid4()
        await session.execute(
            text(
                "INSERT INTO documents (id, deal_id, tenant_id, filename, doc_type, status, storage_key, size_bytes) "
                "VALUES (:id, :deal, :tenant, :filename, :doc_type, 'EXTRACTED', :key, 100)"
            ),
            {
                "id": str(document_id), "deal": str(deal_id), "tenant": str(tenant_id),
                "filename": filename, "doc_type": doc_type, "key": f"{document_id}.bin",
            },
        )
        await session.execute(
            text(
                "INSERT INTO extraction_results (id, deal_id, document_id, tenant_id, fields, agent_version, created_at) "
                "VALUES (:id, :deal, :doc, :tenant, :fields, :agent_version, :created)"
            ),
            {
                "id": str(uuid4()), "deal": str(deal_id), "doc": str(document_id), "tenant": str(tenant_id),
                "fields": json.dumps(fields), "agent_version": agent_version,
                "created": (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat(sep=" "),
            },
        )
        await session.commit()


# The LIVE tagging on the tester's deal: both CoStar reports carry the
# analyst's ``doc_type = 'STR_TREND'`` tag while their extraction ran in the
# MARKET_STUDY lane (``agent_version`` ``dt:MARKET_STUDY``).
MS_LANE_AGENT_VERSION = "router:extractor;dt:MARKET_STUDY;extractor;pv=v1"


@pytest.mark.asyncio
async def test_market_study_lane_is_read_even_when_the_document_is_tagged_str_trend() -> None:
    tenant_id, deal_id = await _seed(str_fields=None, ms_fields=None)
    await _add_document(
        deal_id=deal_id, tenant_id=tenant_id, filename=MS_DOC, doc_type="STR_TREND",
        fields=[
            *COSTAR_SUBMARKET_FIELDS,
            # The CoStar report also states a MARKET occupancy under the
            # subject namespace — it must never feed the STR comp-set blend.
            {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.652, "source_page": 2},
            {"field_name": "ttm_performance.indices.mpi_occupancy_index", "value": 1.0, "source_page": 2},
        ],
        agent_version=MS_LANE_AGENT_VERSION,
    )
    await _add_document(
        deal_id=deal_id, tenant_id=tenant_id, filename="Miami Beach Supply 12.10.25.xlsx", doc_type="STR_TREND",
        fields=COSTAR_EXPORT_FIELDS, agent_version=None, minutes_ago=5,  # no dt: stamp → lane by its field paths
    )
    body = await _overview(tenant_id, deal_id)
    assert body["demand_growth"]["reason"] is None
    assert body["demand_growth"]["value_pct"] == 25.0
    assert body["supply_growth"]["under_construction_rooms"] == 1300
    assert body["supply_growth"]["under_construction_pct"] == 5.7
    assert [h["name"] for h in body["supply_growth"]["pipeline_hotels"]] == ["Shore Club"]
    # Neither STR_TREND-tagged CoStar extraction is an STR report: no comp
    # set and no TTM blend come out of them.
    assert body["comp_set"] is None
    assert body["ttm_blend"] is None


@pytest.mark.asyncio
async def test_no_document_only_when_no_extraction_is_in_the_market_study_lane() -> None:
    tenant_id, deal_id = await _seed(str_fields=STR_FIELDS, ms_fields=None)
    body = await _overview(tenant_id, deal_id)
    assert body["demand_growth"]["reason"] == "no_document"
    assert body["supply_growth"]["reason"] == "no_document"
    assert body["comp_set"]["active_count"] == 4  # the real STR report still reads as STR


# The live comp-set case: the NEWEST STR extraction is the May trend report
# (Blue Moon 75 rooms, no marker) and the older one the July daily report
# ("Closed - Blue Moon Hotel", 0 rooms, STR id 34401 in both).
MAY_STR_DOC = "ANG-20250500-USD-E.xlsx"
JULY_STR_DOC = "56387-20250713-USD-E.xlsx"


def _roster_fields(roster: list[tuple[str, str, int, str | None]], *, page: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, (sid, name, keys, status) in enumerate(roster, start=1):
        out.append({"field_name": f"ttm_performance.compset.{i}.name", "value": name, "source_page": page})
        out.append({"field_name": f"ttm_performance.compset.{i}.keys", "value": keys, "unit": "rooms", "source_page": page})
        out.append({"field_name": f"ttm_performance.compset.{i}.str_id", "value": sid, "source_page": page})
        if status:
            out.append({"field_name": f"ttm_performance.compset.{i}.status", "value": status, "source_page": page})
    return out


@pytest.mark.asyncio
async def test_comp_set_unions_the_may_and_july_str_reports_and_blue_moon_is_closed_per_july() -> None:
    may_fields = [
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.714, "source_page": 3},
        {"field_name": "ttm_performance.indices.mpi_occupancy_index", "value": 103.2, "source_page": 4},
        {"field_name": "comp_set.comp_set_size", "value": 5, "source_page": 22},
        {"field_name": "comp_set.total_keys", "value": 419, "unit": "rooms", "source_page": 22},
        *_roster_fields(
            [
                ("44401", "Z Ocean Hotel", 40, None),
                ("34401", "Blue Moon Hotel", 75, None),
                ("44117", "The Betsy South Beach", 129, None),
                ("55512", "The Tony Hotel of South Beach", 68, None),
                ("33931", "Dream South Beach", 107, None),
            ],
            page=22,
        ),
    ]
    july_fields = _roster_fields(
        [
            ("44401", "Z Ocean Hotel", 40, None),
            ("34401", "Closed - Blue Moon Hotel", 0, "closed"),
            ("44117", "The Betsy South Beach", 129, None),
            ("55512", "The Tony Hotel of South Beach", 68, None),
            ("33931", "Dream South Beach", 107, None),
        ],
        page=2,
    )
    tenant_id, deal_id = await _seed(str_fields=None, ms_fields=None)
    await _add_document(
        deal_id=deal_id, tenant_id=tenant_id, filename=MAY_STR_DOC, doc_type="STR_TREND",
        fields=may_fields, agent_version="router:extractor;dt:STR_TREND;extractor;pv=v1", minutes_ago=0,
    )
    await _add_document(
        deal_id=deal_id, tenant_id=tenant_id, filename=JULY_STR_DOC, doc_type="STR_TREND",
        fields=july_fields, agent_version="template:str_trend;dt:STR_TREND", minutes_ago=30,
    )
    body = await _overview(tenant_id, deal_id)
    cs = body["comp_set"]
    assert cs["active_count"] == 4 and cs["active_keys"] == 344
    assert cs["closed_names"] == ["Blue Moon Hotel"] and cs["status_available"] is True
    assert cs["reported_comp_set_size"] == 5 and cs["reported_total_keys"] == 419  # the old "5 hotels / 419"
    assert cs["documents"] == [MAY_STR_DOC, JULY_STR_DOC]
    bm = next(h for h in cs["hotels"] if h["name"] == "Blue Moon Hotel")
    assert bm["status"] == "closed"
    assert bm["status_doc_name"] == JULY_STR_DOC and bm["status_page"] == 2
    assert bm["str_id"] == "34401"
    assert bm["keys"] == 75 and bm["keys_doc_name"] == MAY_STR_DOC
    assert bm["reports"] == [MAY_STR_DOC, JULY_STR_DOC]
    assert JULY_STR_DOC in cs["note"]
    assert len(cs["hotels"]) == 5


@pytest.mark.asyncio
async def test_blocks_are_tenant_scoped() -> None:
    tenant_id, deal_id = await _seed(str_fields=STR_FIELDS, ms_fields=MS_FIELDS)
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get(f"/market/{deal_id}/overview", headers={"X-Tenant-Id": str(uuid4())})
        assert r.status_code == 404
    body = await _overview(tenant_id, deal_id)
    assert body["comp_set"]["active_count"] == 4
