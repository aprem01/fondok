"""FON-61 E-008 — the LIVE field names of the two production CoStar extractions.

Both reports on the tester's deal were routed MARKET_STUDY and extracted
by the generic extractor before ``market_study.md`` existed, so their data
sits under the closest prefixes the LLM knew — not the canonical
``market_study.trend.*`` / ``market_study.supply.*`` paths. The paths below
are verbatim from those two extractions (coordinator pull, Oct 2026):

* Document A — "Miami Beach-Hospitality-Submarket-2025-12-10" (submarket
  report): ``pnl_benchmark.market.(demand|supply)_change_<YYYY>_(annual|q[1-4]|forecast)``,
  ``property_overview.rooms_under_construction_{count,total}``,
  ``property_overview.under_construction_pct_of_(existing_)inventory``,
  ``property_overview.final_planning_rooms(_total)``,
  ``property_overview.pipeline_hotel_<n>_{name,keys,expected_open,tier,developer}``,
  ``ttm_performance.subject.rooms_under_construction``, per-segment
  ``ttm_performance.segment.<scale>.under_construction``.
* Document B — "Miami Beach Supply 12.10.25.xlsx" (pipeline export,
  MULTI-market): ``market_study.pipeline.<slug>.{name,market,submarket,keys,status,...}``.

Locked here: those paths resolve; the export is filtered to the deal's
market BEFORE any sum; the report's own "% of inventory" beats a computed
share; annual beats quarterly; a forecast is shown as forecast, never as
the value; no match → ``no_source`` naming the market and the file.

Pure unit tests — no DB, no LLM, no I/O.
"""

from __future__ import annotations

from typing import Any

from app.services.market_fields import FieldRow
from app.services.market_study_reader import (
    REASON_NO_SOURCE,
    market_terms_from_rows,
    multi_market_export_docs,
    read_demand_growth,
    read_supply_growth,
)

AS_OF = 2026
DOC_A = "Miami Beach-Hospitality-Submarket-2025-12-10"
DOC_B = "Miami Beach Supply 12.10.25.xlsx"


def _row(name: str, value: Any, *, doc: str = DOC_A, unit: str | None = None, page: int | None = 3) -> FieldRow:
    doc_id = "doc-a" if doc == DOC_A else "doc-b"
    return FieldRow(
        field_name=name, value=value, unit=unit, page=page, doc_name=doc,
        doc_id=doc_id, extraction_id=f"ext-{doc_id}", doc_type="MARKET_STUDY",
    )


# ─────────────────────────── Document A, verbatim ───────────────────────────

DOC_A_ROWS: list[FieldRow] = [
    _row("property_overview.submarket", "Miami Beach", page=1),
    _row("property_overview.market", "Miami, FL", page=1),
    # Trend — fractions, annual / quarterly / forecast.
    _row("pnl_benchmark.market.demand_change_2020_annual", -0.35, page=6),
    _row("pnl_benchmark.market.demand_change_2021_annual", 0.40, page=6),
    _row("pnl_benchmark.market.demand_change_2021_q2", 0.80, page=6),
    _row("pnl_benchmark.market.demand_change_2021_q3", 0.30, page=6),
    _row("pnl_benchmark.market.demand_change_2021_q4", 0.20, page=6),
    _row("pnl_benchmark.market.demand_change_2022_annual", 0.25, page=6),
    _row("pnl_benchmark.market.demand_change_2026_forecast", 0.05, page=6),
    _row("pnl_benchmark.market.supply_change_2020_annual", 0.02, page=6),
    _row("pnl_benchmark.market.supply_change_2021_annual", 0.10, page=6),
    _row("pnl_benchmark.market.supply_change_2021_q4", 0.04, page=6),
    _row("pnl_benchmark.market.supply_change_2022_annual", 0.15, page=6),
    _row("pnl_benchmark.market.supply_change_2026_forecast", 0.05, page=6),
    # TTM levels (not growth by themselves).
    _row("ttm_performance.subject.demand_room_nights", 5_500_000, page=3),
    _row("ttm_performance.subject.supply_room_nights", 7_700_000, page=3),
    # Pipeline headline (p.3 and the p.15 repeat).
    _row("property_overview.rooms_under_construction_count", 1300, page=3),
    _row("property_overview.hotels_under_construction_count", 4, page=3),
    _row("property_overview.under_construction_pct_of_inventory", 0.057, page=3),
    _row("property_overview.final_planning_rooms", 1300, page=3),
    _row("property_overview.final_planning_projects_count", 7, page=3),
    _row("property_overview.rooms_under_construction_total", 1300, page=15),
    _row("property_overview.under_construction_pct_of_existing_inventory", 0.057, page=15),
    _row("property_overview.final_planning_rooms_total", 1300, page=15),
    _row("ttm_performance.subject.rooms_under_construction", 1326, page=4),
    _row("ttm_performance.segment.upscale.under_construction", 600, page=4),
    _row("ttm_performance.segment.luxury.under_construction", 726, page=4),
    # The report's own pipeline list (no status column).
    _row("property_overview.pipeline_hotel_1_name", "Shore Club Miami Beach", page=15),
    _row("property_overview.pipeline_hotel_1_keys", 100, page=15),
    _row("property_overview.pipeline_hotel_1_expected_open", "2026-06", page=15),
    _row("property_overview.pipeline_hotel_1_tier", "Luxury", page=15),
    _row("property_overview.pipeline_hotel_1_developer", "Witkoff", page=15),
    _row("property_overview.pipeline_hotel_2_name", "Aman Miami Beach", page=15),
    _row("property_overview.pipeline_hotel_2_keys", 56, page=15),
]

# ─────────────────────────── Document B, verbatim shape ───────────────────────────


def _export_hotel(slug: str, *, name: str, market: str, submarket: str, keys: int, status: str) -> list[FieldRow]:
    p = f"market_study.pipeline.{slug}"
    return [
        _row(f"{p}.name", name, doc=DOC_B, page=1),
        _row(f"{p}.market", market, doc=DOC_B, page=1),
        _row(f"{p}.submarket", submarket, doc=DOC_B, page=1),
        _row(f"{p}.keys", keys, doc=DOC_B, unit="keys", page=1),
        _row(f"{p}.brand", "Independent", doc=DOC_B, page=1),
        _row(f"{p}.scale", "Luxury", doc=DOC_B, page=1),
        _row(f"{p}.status", status, doc=DOC_B, page=1),
        _row(f"{p}.year_built", 2026, doc=DOC_B, page=1),
        _row(f"{p}.address", "1 Ocean Dr", doc=DOC_B, page=1),
        _row(f"{p}.stories", 12, doc=DOC_B, page=1),
    ]


DOC_B_ROWS: list[FieldRow] = [
    *_export_hotel("shore_club", name="Shore Club", market="Miami, FL", submarket="Miami Beach", keys=100, status="Under Construction"),
    *_export_hotel("aman_miami_beach", name="Aman Miami Beach", market="Miami, FL", submarket="Miami Beach", keys=56, status="Under Construction"),
    *_export_hotel("raleigh", name="The Raleigh", market="Miami, FL", submarket="Miami Beach", keys=60, status="Final Planning"),
    *_export_hotel("downtown_miami_x", name="Downtown Miami Hotel", market="Miami, FL", submarket="Downtown Miami", keys=300, status="Under Construction"),
    *_export_hotel("boston_seaport", name="Seaport Hotel", market="Boston, MA", submarket="Seaport", keys=400, status="Under Construction"),
    *_export_hotel("tampa_water", name="Water Street Hotel", market="Tampa Bay, FL", submarket="Downtown Tampa", keys=250, status="Final Planning"),
    *_export_hotel("san_diego_bay", name="Bayfront Hotel", market="San Diego, CA", submarket="Downtown San Diego", keys=500, status="Under Construction"),
]


# ─────────────────────────── demand (Document A) ───────────────────────────


def test_demand_growth_reads_pnl_benchmark_market_annual_and_shows_forecast_apart() -> None:
    g = read_demand_growth(DOC_A_ROWS, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 25.0  # 0.25 fraction → +25.0%
    assert g.period_label == "2022"  # latest ANNUAL actual, not 2021 Q4
    assert g.basis == "reported" and g.reason is None
    assert g.inputs[0].field_name == "pnl_benchmark.market.demand_change_2022_annual"
    assert g.inputs[0].doc_name == DOC_A and g.inputs[0].page == 6
    assert g.forecast_pct == 5.0 and g.forecast_label == "2026 forecast"
    assert g.forecast_input is not None
    assert g.forecast_input.field_name == "pnl_benchmark.market.demand_change_2026_forecast"


def test_demand_growth_annual_beats_a_later_quarterly() -> None:
    rows = [
        _row("pnl_benchmark.market.demand_change_2021_annual", 0.40),
        _row("pnl_benchmark.market.demand_change_2022_q1", 0.12),
        _row("pnl_benchmark.market.demand_change_2022_q2", 0.09),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 40.0 and g.period_label == "2021"


def test_demand_growth_quarterly_is_used_when_no_annual_exists_and_labelled() -> None:
    rows = [
        _row("pnl_benchmark.market.demand_change_2021_q3", 0.30),
        _row("pnl_benchmark.market.demand_change_2021_q4", 0.20),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 20.0 and g.period_label == "2021 Q4"


def test_demand_growth_forecast_only_is_labelled_forecast_and_ttm_levels_are_not_growth() -> None:
    rows = [
        _row("pnl_benchmark.market.demand_change_2026_forecast", 0.05),
        _row("ttm_performance.subject.demand_room_nights", 5_500_000),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    # No actual: the series has one level only, so the value is None and the
    # forecast is carried separately — never promoted to the value.
    assert g.value_pct is None and g.reason == REASON_NO_SOURCE
    assert g.forecast_pct == 5.0 and g.forecast_label == "2026 forecast"


# ─────────────────────────── supply (Document A) ───────────────────────────


def test_supply_growth_doc_a_reported_share_wins_rooms_are_the_numerator() -> None:
    s = read_supply_growth(DOC_A_ROWS, has_documents=True, as_of_year=AS_OF, market_terms=["Miami Beach"])
    # No explicit existing-inventory rooms field in the report → None, and
    # the share is the report's own 5.7% (not rooms ÷ anything).
    assert s.existing_rooms is None
    assert s.under_construction_rooms == 1300
    assert s.under_construction_pct == 5.7
    assert s.under_construction_pct_basis == "reported"
    assert s.final_planning_rooms == 1300
    assert s.final_planning_pct is None and s.final_planning_pct_basis is None
    assert s.reason is None and s.detail is None
    names = [r.field_name for r in s.inputs]
    assert "property_overview.rooms_under_construction_count" in names  # p.3 headline first
    assert "property_overview.under_construction_pct_of_inventory" in names
    assert "property_overview.final_planning_rooms" in names
    # Counts of hotels / projects and the per-segment split never pose as rooms.
    assert "property_overview.hotels_under_construction_count" not in names
    assert "property_overview.final_planning_projects_count" not in names
    assert not any("segment" in n for n in names)
    # Reported supply change: latest annual actual + the forecast apart.
    assert s.reported_supply_change_pct == 15.0 and s.reported_supply_change_period == "2022"
    assert s.forecast_supply_change_pct == 5.0 and s.forecast_supply_change_period == "2026 forecast"
    # The report's own pipeline list is surfaced (no status → listed, not summed).
    listed = {h.name: h for h in (s.pipeline_hotels or [])}
    assert listed["Shore Club Miami Beach"].keys == 100
    assert listed["Shore Club Miami Beach"].expected_open == "2026-06"
    assert listed["Shore Club Miami Beach"].bucket is None
    assert s.pipeline_filter is None  # no export in this set


def test_supply_growth_doc_a_without_headline_falls_back_to_subject_rooms_under_construction() -> None:
    rows = [r for r in DOC_A_ROWS if "property_overview.rooms_under_construction" not in r.field_name]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF, market_terms=["Miami Beach"])
    assert s.under_construction_rooms == 1326
    assert s.inputs[0].field_name == "ttm_performance.subject.rooms_under_construction"


def test_supply_growth_computed_share_when_report_states_inventory_but_no_pct() -> None:
    rows = [
        _row("property_overview.existing_inventory_rooms", 22_800),
        _row("property_overview.rooms_under_construction_total", 1300),
        _row("property_overview.final_planning_rooms_total", 1300),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.existing_rooms == 22_800
    assert s.under_construction_pct == round(1300 / 22_800 * 100, 4)
    assert s.under_construction_pct_basis == "computed"
    assert s.final_planning_pct == round(1300 / 22_800 * 100, 4)
    assert s.final_planning_pct_basis == "computed"


def test_supply_growth_reported_share_beats_computed_when_both_exist() -> None:
    rows = [
        _row("property_overview.existing_inventory_rooms", 20_000),
        _row("property_overview.rooms_under_construction_total", 1300),
        _row("property_overview.under_construction_pct_of_inventory", 0.057),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.under_construction_pct == 5.7  # not 6.5 (1300 ÷ 20,000)
    assert s.under_construction_pct_basis == "reported"


# ─────────────────────────── pipeline export (Document B) ───────────────────────────


def test_export_is_filtered_to_the_deal_market_before_summing() -> None:
    s = read_supply_growth(DOC_B_ROWS, has_documents=True, as_of_year=AS_OF, market_terms=["Miami Beach"])
    # Only the Miami Beach rows: 100 + 56 under construction; 60 final planning.
    assert s.under_construction_rooms == 156
    assert s.final_planning_rooms == 60
    hotels = s.pipeline_hotels or []
    assert sorted(h.name for h in hotels) == ["Aman Miami Beach", "Shore Club", "The Raleigh"]
    assert all(h.submarket == "Miami Beach" for h in hotels)
    assert {h.bucket for h in hotels} == {"under_construction", "final_planning"}
    f = s.pipeline_filter
    assert f is not None
    assert (f.matched, f.total) == (3, 7)
    assert f.doc_name == DOC_B and f.terms == ["miami beach"] and f.note is None
    # Boston / Tampa / San Diego / Downtown Miami never reach a sum.
    assert all(r.doc_name == DOC_B for r in s.inputs if r.field_name.startswith("market_study.pipeline."))
    assert {r.field_name for r in s.inputs} == {
        "market_study.pipeline.shore_club.keys",
        "market_study.pipeline.aman_miami_beach.keys",
        "market_study.pipeline.raleigh.keys",
    }
    # No inventory in the export → the share is unavailable and says why.
    assert s.under_construction_pct is None and s.reason == REASON_NO_SOURCE
    assert "existing submarket inventory not in the uploaded reports" in (s.detail or "")


def test_export_market_filter_is_case_insensitive_and_matches_market_column_too() -> None:
    s = read_supply_growth(DOC_B_ROWS, has_documents=True, as_of_year=AS_OF, market_terms=["boston, ma"])
    assert s.under_construction_rooms == 400
    assert [h.name for h in (s.pipeline_hotels or [])] == ["Seaport Hotel"]


def test_export_with_no_matching_rows_reports_no_source_naming_market_and_file() -> None:
    s = read_supply_growth(DOC_B_ROWS, has_documents=True, as_of_year=AS_OF, market_terms=["Austin"])
    assert s.under_construction_rooms is None and s.pipeline_hotels == []
    assert s.reason == REASON_NO_SOURCE
    assert f"no pipeline rows for Austin in {DOC_B}" in (s.detail or "")
    assert s.pipeline_filter is not None and s.pipeline_filter.matched == 0


def test_export_is_never_summed_without_a_market_to_filter_by() -> None:
    s = read_supply_growth(DOC_B_ROWS, has_documents=True, as_of_year=AS_OF, market_terms=[])
    assert s.under_construction_rooms is None
    assert s.pipeline_filter is not None and s.pipeline_filter.matched == 0
    assert "No deal market to filter" in (s.detail or "")


def test_single_market_export_without_geo_columns_counts_every_row() -> None:
    rows = [
        _row("market_study.pipeline.a.name", "A", doc=DOC_B),
        _row("market_study.pipeline.a.keys", 120, doc=DOC_B),
        _row("market_study.pipeline.a.status", "Under Construction", doc=DOC_B),
        _row("market_study.pipeline.b.name", "B", doc=DOC_B),
        _row("market_study.pipeline.b.keys", 80, doc=DOC_B),
        _row("market_study.pipeline.b.status", "Final Planning", doc=DOC_B),
        _row("market_study.pipeline.c.name", "C (open)", doc=DOC_B),
        _row("market_study.pipeline.c.keys", 500, doc=DOC_B),
        _row("market_study.pipeline.c.status", "Completed", doc=DOC_B),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF, market_terms=["Miami Beach"])
    assert s.under_construction_rooms == 120 and s.final_planning_rooms == 80
    assert s.pipeline_filter is not None and "carries no market column" in (s.pipeline_filter.note or "")


# ─────────────────────────── both documents together ───────────────────────────


def test_both_documents_report_total_wins_and_export_rows_are_listed() -> None:
    rows = [*DOC_A_ROWS, *DOC_B_ROWS]
    terms = ["Miami Beach", *market_terms_from_rows(rows)]
    assert market_terms_from_rows(rows) == ["Miami Beach"]  # export columns are not terms
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF, market_terms=terms)
    # The submarket report's own totals are the headline; the export is the list.
    assert s.under_construction_rooms == 1300 and s.final_planning_rooms == 1300
    assert s.under_construction_pct == 5.7 and s.under_construction_pct_basis == "reported"
    assert s.reason is None
    names = {h.name for h in (s.pipeline_hotels or [])}
    assert {"Shore Club", "Aman Miami Beach", "The Raleigh", "Shore Club Miami Beach"} <= names
    assert "Seaport Hotel" not in names and "Downtown Miami Hotel" not in names
    assert s.pipeline_filter is not None and (s.pipeline_filter.matched, s.pipeline_filter.total) == (3, 7)


# ─────────────────────── unit guard: deltas are not rates ───────────────────────
#
# Live (2026-10-07): Demand growth showed "-35,997 · reported, 2025" from
# ``cbre_horizons.overall_supply.2025_ytd.demand_change = -35997`` (p.24) —
# a room-night delta ranked as the latest annual actual; supply change
# likewise showed 48,998. With the guard the document resolves to the 2022
# annual fractions and the deltas are surfaced as levels with provenance.

CBRE_DELTA_ROWS: list[FieldRow] = [
    _row("cbre_horizons.overall_supply.2025_ytd.demand_change", -35997, page=24),
    _row("cbre_horizons.overall_supply.2025_ytd.supply_change", 48998, page=24),
]


def test_room_night_deltas_are_levels_never_the_growth_rate() -> None:
    rows = [*DOC_A_ROWS, *CBRE_DELTA_ROWS]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 25.0 and g.period_label == "2022" and g.basis == "reported"
    assert g.inputs[0].field_name == "pnl_benchmark.market.demand_change_2022_annual"
    assert g.forecast_pct == 5.0 and g.forecast_label == "2026 forecast"
    assert g.forecast_input is not None
    assert g.forecast_input.field_name == "pnl_benchmark.market.demand_change_2026_forecast"
    assert g.demand_room_nights_change is not None
    assert g.demand_room_nights_change.field_name == "cbre_horizons.overall_supply.2025_ytd.demand_change"
    assert g.demand_room_nights_change.value == -35997
    assert g.demand_room_nights_change.page == 24 and g.demand_room_nights_change.doc_name == DOC_A
    # A ``2025_ytd`` row is a partial year — labelled "2025 YTD", never "2025".
    assert g.demand_room_nights_change_period == "2025 YTD"

    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF, market_terms=["Miami Beach"])
    assert s.reported_supply_change_pct == 15.0 and s.reported_supply_change_period == "2022"
    assert s.forecast_supply_change_pct == 5.0 and s.forecast_supply_change_period == "2026 forecast"
    assert s.supply_rooms_change is not None
    assert s.supply_rooms_change.field_name == "cbre_horizons.overall_supply.2025_ytd.supply_change"
    assert s.supply_rooms_change.value == 48998 and s.supply_rooms_change.page == 24
    assert s.supply_rooms_change_period == "2025 YTD"
    # The delta row is never one of the rate's inputs.
    assert all(r.field_name != "cbre_horizons.overall_supply.2025_ytd.supply_change" for r in s.inputs)
    assert any(r.field_name == "pnl_benchmark.market.supply_change_2022_annual" for r in s.inputs)


def test_unit_guard_fraction_percent_hint_and_delta_boundaries() -> None:
    def demand(row: FieldRow) -> Any:
        return read_demand_growth([row], has_documents=True, as_of_year=AS_OF)

    # A fraction (|v| ≤ 1.5) is always a rate.
    assert demand(_row("pnl_benchmark.market.demand_change_2024_annual", 0.48)).value_pct == 48.0
    assert demand(_row("pnl_benchmark.market.demand_change_2024_annual", -0.35)).value_pct == -35.0
    # A percent-sized number is a rate only with a % / pct hint — unit, path or raw text.
    for row in (
        _row("pnl_benchmark.market.demand_change_2024_annual", 48, unit="pct"),
        _row("pnl_benchmark.market.demand_change_pct_2024_annual", 48),
        _row("pnl_benchmark.market.demand_change_2024_annual", "48%"),
    ):
        assert demand(row).value_pct == 48.0, row.field_name
    # Without a hint it is an absolute delta: no rate, the level surfaced instead.
    g = demand(_row("pnl_benchmark.market.demand_change_2024_annual", 48))
    assert g.value_pct is None and g.reason == REASON_NO_SOURCE
    assert g.demand_room_nights_change is not None and g.demand_room_nights_change.value == 48
    assert g.demand_room_nights_change_period == "2024"
    # Above 150 a hint does not rescue it.
    g = demand(_row("pnl_benchmark.market.demand_change_2024_annual", 480, unit="pct"))
    assert g.value_pct is None and g.demand_room_nights_change is not None
    # A forecast-tagged delta is not surfaced as the actual level either.
    g = demand(_row("pnl_benchmark.market.demand_change_2027_forecast", -35997))
    assert g.value_pct is None and g.forecast_pct is None and g.demand_room_nights_change is None


# ─────────────── the LIVE export: 30 rows, 3 with a market, 27 unknown ───────────────
#
# ``pipeline_filter.terms`` was ["miami beach", "miami beach hospitality
# capital submarket", "miami airport"] — "miami airport" came from the Supply
# export's OWN ``property_overview.submarket`` (a mis-read header on a
# multi-market file). Only 3 of its 30 rows carry a ``market`` ('Boston, MA',
# 'Tampa Bay, FL', 'Miami') / ``submarket`` ('Cambridge/Waltham', 'St
# Petersburg', 'Miami Airport'); the other 27 carry neither.


def _export_hotel_without_geo(slug: str, *, name: str, keys: int, status: str) -> list[FieldRow]:
    p = f"market_study.pipeline.{slug}"
    return [
        _row(f"{p}.name", name, doc=DOC_B, page=1),
        _row(f"{p}.keys", keys, doc=DOC_B, unit="keys", page=1),
        _row(f"{p}.status", status, doc=DOC_B, page=1),
    ]


LIVE_SUBMARKET_HEADER: list[FieldRow] = [
    _row("property_overview.submarket", "Miami Beach Hospitality Capital Submarket", page=1),
]
LIVE_EXPORT_ROWS: list[FieldRow] = [
    _row("property_overview.submarket", "Miami Airport", doc=DOC_B, page=1),  # the export's own header
    *_export_hotel("cambridge_x", name="Cambridge Hotel", market="Boston, MA", submarket="Cambridge/Waltham", keys=200, status="Under Construction"),
    *_export_hotel("st_pete_x", name="St Pete Hotel", market="Tampa Bay, FL", submarket="St Petersburg", keys=150, status="Final Planning"),
    *_export_hotel("airport_x", name="Airport Hotel", market="Miami", submarket="Miami Airport", keys=180, status="Under Construction"),
    *[
        r
        for i in range(27)
        for r in _export_hotel_without_geo(f"row_{i}", name=f"Project {i}", keys=100 + i, status="Under Construction")
    ],
]


def test_live_export_unknown_rows_are_reported_and_miami_airport_never_matches_miami_beach() -> None:
    rows = [*LIVE_SUBMARKET_HEADER, *LIVE_EXPORT_ROWS]
    # The export's self-stated submarket is NOT a term (multi-market file).
    assert multi_market_export_docs(rows) == {"doc-b"}
    assert market_terms_from_rows(rows) == ["Miami Beach Hospitality Capital Submarket"]
    terms = ["Miami Beach", *market_terms_from_rows(rows)]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF, market_terms=terms)
    f = s.pipeline_filter
    assert f is not None
    assert f.terms == ["miami beach", "miami beach hospitality capital submarket"]
    assert (f.matched, f.total, f.market_unknown_rows) == (0, 30, 27)
    assert f.doc_name == DOC_B
    assert f.note == (
        f"no pipeline rows for Miami Beach in {DOC_B} "
        "(27 of 30 rows carry no market or submarket and were not counted)"
    )
    assert s.pipeline_hotels == [] and s.under_construction_rooms is None
    assert s.reason == REASON_NO_SOURCE
    assert f"no pipeline rows for Miami Beach in {DOC_B}" in (s.detail or "") and "27 of 30" in (s.detail or "")


def test_live_export_unknown_rows_stay_uncounted_when_another_market_matches() -> None:
    s = read_supply_growth(LIVE_EXPORT_ROWS, has_documents=True, as_of_year=AS_OF, market_terms=["Boston, MA"])
    f = s.pipeline_filter
    assert f is not None and (f.matched, f.total, f.market_unknown_rows) == (1, 30, 27)
    assert [h.name for h in (s.pipeline_hotels or [])] == ["Cambridge Hotel"]
    assert s.under_construction_rooms == 200  # the 27 unknown rows are never summed
    assert f.note == f"27 of 30 rows carry no market or submarket and were not counted in {DOC_B}."


def test_single_market_export_keeps_its_own_submarket_as_a_term() -> None:
    rows = [
        _row("property_overview.submarket", "Miami Beach", doc=DOC_B, page=1),
        *_export_hotel("a", name="A", market="Miami, FL", submarket="Miami Beach", keys=10, status="Under Construction"),
        *_export_hotel("b", name="B", market="Miami, FL", submarket="Downtown Miami", keys=20, status="Under Construction"),
    ]
    assert multi_market_export_docs(rows) == set()
    assert market_terms_from_rows(rows) == ["Miami Beach"]


def test_filter_matches_the_whole_term_or_the_submarket_never_a_shared_token() -> None:
    rows = [
        *_export_hotel("airport", name="Airport Hotel", market="Miami", submarket="Miami Airport", keys=180, status="Under Construction"),
        *_export_hotel("plain", name="Plain Miami Hotel", market="Miami", submarket="", keys=70, status="Under Construction"),
        *_export_hotel("beach", name="Beach Hotel", market="Miami", submarket="Miami Beach/South Beach", keys=90, status="Under Construction"),
        *_export_hotel("exact", name="Exact Hotel", market="Miami, FL", submarket="Miami Beach", keys=60, status="Final Planning"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF, market_terms=["Miami Beach"])
    assert sorted(h.name for h in (s.pipeline_hotels or [])) == ["Beach Hotel", "Exact Hotel"]
    assert s.under_construction_rooms == 90 and s.final_planning_rooms == 60
    f = s.pipeline_filter
    assert f is not None and (f.matched, f.total, f.market_unknown_rows) == (2, 4, 0)


def test_canonical_paths_still_win_over_the_live_names() -> None:
    rows = [
        _row("market_study.trend.ttm.demand_change_pct", 0.042),
        _row("pnl_benchmark.market.demand_change_2022_annual", 0.25),
        _row("market_study.supply.existing_rooms", 12_000, unit="rooms"),
        _row("under_construction.total_rooms", 600, unit="rooms"),
        _row("property_overview.rooms_under_construction_total", 1300),
        _row("property_overview.under_construction_pct_of_inventory", 0.057),
    ]
    g = read_demand_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert g.value_pct == 4.2 and g.period_label == "TTM"
    s = read_supply_growth(rows, has_documents=True, as_of_year=AS_OF)
    assert s.under_construction_rooms == 600  # canonical total before the property_overview one
    # … but a reported share still beats the computed one.
    assert s.under_construction_pct == 5.7 and s.under_construction_pct_basis == "reported"
