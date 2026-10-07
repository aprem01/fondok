"""FON-61 E-008 — Demand Growth / Supply Growth from MARKET_STUDY extractions.

Two CoStar submarket reports routed to MARKET_STUDY extracted hundreds of
``market_study.*`` / ``under_construction.*`` / ``cbre_horizons.*`` fields
and nothing read them. These tests pin the reader: canonical paths and
the LLM's looser names both resolve, every value names its document and
field, forecast years never masquerade as actuals, and a missing series
yields the shared reason code (``no_document`` / ``no_source``) with a
"not in the uploaded reports" detail — never a fabricated rate.

Pure unit tests — no DB, no LLM, no I/O.
"""

from __future__ import annotations

from typing import Any

from app.services.market_fields import FieldRow
from app.services.market_study_reader import (
    REASON_NO_DOCUMENT,
    REASON_NO_SOURCE,
    read_demand_growth,
    read_supply_growth,
)

AS_OF = 2026
DOC = "CoStar Submarket Report - South Beach.pdf"


def _row(name: str, value: Any, *, unit: str | None = None, page: int | None = 5,
         doc: str = DOC, period: str | None = None) -> FieldRow:
    return FieldRow(
        field_name=name, value=value, unit=unit, page=page, doc_name=doc,
        doc_id="doc-ms-1", extraction_id="ext-ms-1", doc_type="MARKET_STUDY", period=period,
    )


# ─────────────────────────── demand growth ───────────────────────────


def test_demand_growth_prefers_ttm_over_years_and_names_the_row() -> None:
    rows = [
        _row("market_study.trend.2024.demand_change_pct", 0.031, unit="pct", page=7),
        _row("market_study.trend.2025.demand_change_pct", 0.018, unit="pct", page=7),
        _row("market_study.trend.ttm.demand_change_pct", 0.042, unit="pct", page=6),
        _row("market_study.trend.2027.demand_change_pct", 0.055, unit="pct", page=7, period="forecast"),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 4.2
    assert g.period_label == "TTM"
    assert g.basis == "reported"
    assert g.reason is None
    assert len(g.inputs) == 1
    assert g.inputs[0].field_name == "market_study.trend.ttm.demand_change_pct"
    assert g.inputs[0].doc_name == DOC and g.inputs[0].page == 6


def test_demand_growth_latest_actual_year_beats_forecast_and_older_years() -> None:
    rows = [
        _row("market_study.trend.2024.demand_change_pct", 0.031),
        _row("market_study.trend.2025.demand_change_pct", 0.018),
        # Forecast years (tagged by sibling .period, by the row's own period
        # tag, and by being later than the as-of year) are never "actual".
        _row("market_study.trend.2026.demand_change_pct", 0.07),
        _row("market_study.trend.2026.period", "forecast"),
        _row("market_study.trend.2027.demand_change_pct", 0.08, period="forecast"),
        _row("market_study.trend.2028.demand_change_pct", 0.09),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 1.8 and g.period_label == "2025"


def test_demand_growth_reads_the_llm_named_cbre_style_rows_in_a_market_study() -> None:
    """The generic prompt steered the CoStar trend table onto the CBRE paths
    inside the MARKET_STUDY document — those rows count, and are named."""
    rows = [
        _row("cbre_horizons.segment_all.2025.demand_change_pct", 2.6, unit="pct"),
        _row("cbre_horizons.segment_all.2025.period", "actual"),
        _row("cbre_horizons.segment_all.2026.demand_change_pct", 3.9, unit="pct"),
        _row("cbre_horizons.segment_all.2026.period", "forecast"),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 2.6 and g.period_label == "2025"
    assert g.inputs[0].field_name == "cbre_horizons.segment_all.2025.demand_change_pct"


def test_demand_growth_tolerates_loose_llm_names_and_percent_strings() -> None:
    rows = [_row("market_study.demand_growth_yoy", "4.2%")]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 4.2 and g.period_label == "as reported"


def test_demand_growth_only_forecast_is_surfaced_as_forecast_never_as_the_value() -> None:
    rows = [_row("market_study.trend.2027.demand_change_pct", 0.05, period="forecast")]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct is None and g.reason == REASON_NO_SOURCE
    assert g.forecast_pct == 5.0 and g.forecast_label == "2027 forecast"
    assert g.forecast_input is not None and g.forecast_input.field_name == "market_study.trend.2027.demand_change_pct"


def test_demand_growth_derived_from_room_night_series_names_both_rows() -> None:
    rows = [
        _row("market_study.trend.2023.demand_room_nights", 1_000_000, unit="rooms"),
        _row("market_study.trend.2024.demand_room_nights", 1_050_000, unit="rooms"),
        _row("market_study.trend.2025.demand_room_nights", 1_102_500, unit="rooms"),
        _row("market_study.trend.2026.demand_room_nights", 1_300_000, unit="rooms", period="forecast"),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.basis == "derived_from_series"
    assert abs(g.value_pct - 5.0) < 1e-9
    assert g.period_label == "2024→2025"
    assert [r.field_name for r in g.inputs] == [
        "market_study.trend.2024.demand_room_nights",
        "market_study.trend.2025.demand_room_nights",
    ]
    assert "2025 ÷ 2024" in (g.detail or "")


def test_demand_growth_missing_series_reports_no_source() -> None:
    rows = [
        _row("market_study.supply.existing_rooms", 12_000, unit="rooms"),
        _row("market_study.trend.2025.demand_room_nights", 1_000_000, unit="rooms"),  # one year only
        _row("market_study.comp_set.demand_change_pct", 0.9),  # comp-set stat, not the submarket
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct is None
    assert g.reason == REASON_NO_SOURCE
    assert "not in the uploaded reports" in (g.detail or "")
    assert g.inputs == []


def test_demand_growth_without_any_market_study_reports_no_document() -> None:
    g = read_demand_growth([], has_documents=False, as_of_year=AS_OF)
    assert g.value_pct is None and g.reason == REASON_NO_DOCUMENT


# ─────────────────────────── supply growth ───────────────────────────


def test_supply_growth_pipeline_over_existing_with_final_planning_separately() -> None:
    rows = [
        _row("market_study.supply.existing_rooms", 12_000, unit="rooms", page=3),
        _row("under_construction.total_rooms", 600, unit="rooms", page=9),
        _row("market_study.supply.final_planning_rooms", 300, unit="rooms", page=9),
        _row("market_study.supply.planned_rooms", 1_200, unit="rooms", page=9),
        _row("market_study.trend.ttm.supply_change_pct", 0.012, unit="pct", page=6),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.existing_rooms == 12_000
    assert s.under_construction_rooms == 600
    assert s.final_planning_rooms == 300
    assert s.planned_rooms == 1_200
    assert s.under_construction_pct == 5.0  # 600 ÷ 12,000
    assert s.final_planning_pct == 2.5  # 300 ÷ 12,000
    assert s.reported_supply_change_pct == 1.2 and s.reported_supply_change_period == "TTM"
    assert s.reason is None and s.detail is None
    names = [r.field_name for r in s.inputs]
    assert names == [
        "market_study.supply.existing_rooms",
        "under_construction.total_rooms",
        "market_study.supply.final_planning_rooms",
        "market_study.supply.planned_rooms",
        "market_study.trend.ttm.supply_change_pct",
    ]
    assert s.inputs[1].page == 9 and s.inputs[0].doc_name == DOC


def test_supply_growth_sums_listed_projects_by_status_when_no_total() -> None:
    rows = [
        _row("market_study.supply.existing_rooms", 10_000, unit="rooms"),
        _row("under_construction.1.name", "Hotel A"),
        _row("under_construction.1.rooms", 200, unit="rooms"),
        _row("under_construction.1.status", "Under Construction"),
        _row("under_construction.2.name", "Hotel B"),
        _row("under_construction.2.rooms", 150, unit="rooms"),  # no status → under construction
        _row("under_construction.3.name", "Hotel C"),
        _row("under_construction.3.rooms", 400, unit="rooms"),
        _row("under_construction.3.status", "Final Planning"),
        _row("under_construction.4.name", "Hotel D"),
        _row("under_construction.4.rooms", 90, unit="rooms"),
        _row("under_construction.4.status", "Proposed"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.under_construction_rooms == 350
    assert s.final_planning_rooms == 400
    assert s.planned_rooms == 90
    assert s.under_construction_pct == 3.5
    assert s.final_planning_pct == 4.0
    assert {r.field_name for r in s.inputs} >= {
        "under_construction.1.rooms", "under_construction.2.rooms",
        "under_construction.3.rooms", "under_construction.4.rooms",
    }


def test_supply_growth_existing_inventory_from_latest_actual_supply_year() -> None:
    rows = [
        _row("market_study.trend.2024.supply_rooms", 11_500, unit="rooms"),
        _row("market_study.trend.2025.supply_rooms", 12_000, unit="rooms"),
        _row("market_study.trend.2026.supply_rooms", 12_600, unit="rooms", period="forecast"),
        _row("market_study.under_construction_rooms", 600, unit="rooms"),
        # Comp-set / subject room counts never stand in for the submarket.
        _row("market_study.comp_set.total_rooms", 344, unit="rooms"),
        _row("market_study.subject.rooms", 132, unit="rooms"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.existing_rooms == 12_000 and s.existing_period_label == "2025"
    assert s.under_construction_rooms == 600
    assert s.under_construction_pct == 5.0


def test_supply_growth_without_existing_inventory_reports_no_source_but_keeps_pieces() -> None:
    rows = [_row("under_construction.total_rooms", 600, unit="rooms")]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.under_construction_rooms == 600
    assert s.under_construction_pct is None
    assert s.reason == REASON_NO_SOURCE
    assert "existing submarket inventory not in the uploaded reports" in (s.detail or "")


def test_supply_growth_without_pipeline_reports_no_source() -> None:
    rows = [_row("market_study.supply.existing_rooms", 12_000, unit="rooms")]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.existing_rooms == 12_000
    assert s.under_construction_pct is None
    assert s.reason == REASON_NO_SOURCE
    assert "under-construction rooms not in the uploaded reports" in (s.detail or "")


def test_supply_growth_without_any_market_study_reports_no_document() -> None:
    s = read_supply_growth([], has_documents=False, as_of_year=AS_OF)
    assert s.reason == REASON_NO_DOCUMENT and s.under_construction_pct is None


def test_rows_from_other_namespaces_are_ignored() -> None:
    rows = [
        _row("ttm_performance.subject.monthly.2025_06.demand_rooms", 3_000),
        _row("ttm_performance.subject.monthly.2025_05.demand_rooms", 2_900),
        _row("pnl_benchmark.peer.rooms_revenue.total_usd", 1_000_000, unit="usd"),
    ]
    assert read_demand_growth(rows, has_documents=True, as_of_year=AS_OF).reason == REASON_NO_SOURCE
    assert read_supply_growth(rows, has_documents=True, as_of_year=AS_OF).reason == REASON_NO_SOURCE
