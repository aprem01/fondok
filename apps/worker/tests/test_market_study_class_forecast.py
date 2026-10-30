"""FON-116 topline PR 1 — CoStar submarket class-forecast rows and their reader.

The CoStar submarket report prints, in its appendix, a supply & demand table
and a performance table (occupancy / ADR / RevPAR with YoY changes) for the
whole submarket AND for each of three class buckets — Luxury & Upper Upscale,
Upscale & Upper Midscale, Midscale & Economy. ``extraction_schemas/
market_study.md`` asks for them as ``market_study.class.<bucket>.<YYYY>.*``
rows (fractions, a ``.period`` tag per year, the page of the table); the
reader ``market_study_reader.read_class_forecast`` turns the rows into one
series per bucket for the growth resolver.

Fixture: ``evals/golden-set/kimpton-angler/input/costar_submarket_extracted.json``
— the expected extraction of the REAL "Miami Beach-Hospitality-Submarket-
2025-12-10" report, generated from the PDF's text layer (the class forecast
appendix is PDF page 26, printed "Page 25"). Sam's reference series for
Luxury & Upper Upscale (verified against the PDF): 2026 occupancy -1.2% /
ADR +1.8%, 2027 0.0% / -0.6%, 2028 -1.2% / +3.8%, 2029 +3.0% / +2.8%.

Locked here: bucket mapping, period tags (2025-2029 are the report's
projections, 2024 and earlier actuals), page numbers on every figure, the
overall-series fallback when a report carries no class tables (and NO
silent bucket switch for a missing year), a blank cell → ``None`` never 0,
and the critic rule that a printed YoY change must reconcile to the two
printed levels. Pure unit tests — no DB, no LLM, no I/O beyond the fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.services.market_fields import FieldRow, parse_extraction_records
from app.services.market_study_reader import (
    CLASS_BUCKET_LABELS,
    REASON_NO_DOCUMENT,
    REASON_NO_SOURCE,
    ClassForecastReading,
    ClassSeries,
    read_class_forecast,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE = REPO_ROOT / "evals" / "golden-set" / "kimpton-angler" / "input" / "costar_submarket_extracted.json"
TEXT_FIXTURE = REPO_ROOT / "evals" / "golden-set" / "kimpton-angler" / "input" / "costar_submarket_text.json"
DOC_NAME = "Miami Beach-Hospitality-Submarket-2025-12-10.pdf"
LUU = "luxury_upper_upscale"
UUM = "upscale_upper_midscale"
ME = "midscale_economy"

# Sam's reference series — Luxury & Upper Upscale, PDF page 26 (printed "Page 25").
LUU_EXPECTED = {
    2026: (-0.012, 0.018),
    2027: (0.0, -0.006),
    2028: (-0.012, 0.038),
    2029: (0.03, 0.028),
}


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _record(fixture: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    rec = {
        "fields": fixture["fields"],
        "document_id": fixture["document_id"],
        "filename": fixture["filename"],
        "doc_type": fixture["doc_type"],
        "extraction_id": "ext-costar-1",
        "created_at": "2026-10-30T10:00:00",
        "report_as_of": fixture["report_as_of"],
        "report_as_of_precision": "day",
    }
    rec.update(overrides)
    return rec


@pytest.fixture(scope="module")
def rows() -> list[FieldRow]:
    return parse_extraction_records([_record(_fixture())])


@pytest.fixture(scope="module")
def reading(rows: list[FieldRow]) -> ClassForecastReading:
    return read_class_forecast(rows)


def _row(name: str, value: Any, *, unit: str | None = None, page: int | None = 26,
         doc: str = DOC_NAME, ext: str = "ext-x", **kw: Any) -> FieldRow:
    return FieldRow(
        field_name=name, value=value, unit=unit, page=page, doc_name=doc, doc_id=f"id-{ext}",
        extraction_id=ext, doc_type="MARKET_STUDY", **kw,
    )


# ───────────────────────────── fixture sanity ─────────────────────────────


def test_fixture_rows_carry_the_contract_shape() -> None:
    fx = _fixture()
    assert fx["doc_type"] == "MARKET_STUDY"
    for f in fx["fields"]:
        assert isinstance(f["source_page"], int) and f["source_page"] >= 1, f["field_name"]
        assert f["value"] is not None, f["field_name"]
        assert f["raw_text"], f["field_name"]
    names = {f["field_name"] for f in fx["fields"]}
    for bucket in (LUU, UUM, ME):
        assert f"market_study.class.{bucket}.2026.period" in names
    # The text fixture is the same pages the rows cite.
    text = json.loads(TEXT_FIXTURE.read_text(encoding="utf-8"))
    cited = {f["source_page"] for f in fx["fields"]}
    assert cited <= {p["pdf_page"] for p in text["pages"]}
    assert all(p["printed_page"] == p["pdf_page"] - 1 for p in text["pages"])


def test_fixture_percentages_are_fractions_never_zero_placeholders() -> None:
    fx = _fixture()
    for f in fx["fields"]:
        if f["field_name"].endswith("_pct"):
            assert abs(float(f["value"])) <= 1.5, f


# ───────────────────────────── bucket mapping ─────────────────────────────


def test_reader_maps_the_three_class_buckets_and_the_overall_series(reading: ClassForecastReading) -> None:
    assert reading.reason is None
    assert set(reading.buckets) == {LUU, UUM, ME, "all"}
    assert reading.doc_name == DOC_NAME
    assert reading.submarket == "Miami Beach"
    assert reading.market == "Miami"
    assert reading.report_date == "2025-12-10"
    assert CLASS_BUCKET_LABELS[LUU] == "Luxury & Upper Upscale"
    assert CLASS_BUCKET_LABELS[UUM] == "Upscale & Upper Midscale"
    assert CLASS_BUCKET_LABELS[ME] == "Midscale & Economy"


def test_luxury_upper_upscale_series_matches_sams_reference(reading: ClassForecastReading) -> None:
    s = reading.series(LUU)
    assert isinstance(s, ClassSeries)
    assert s.bucket == LUU and s.requested_bucket == LUU and s.fallback_reason is None
    assert s.label == "Luxury & Upper Upscale"
    for year, (occ, adr) in LUU_EXPECTED.items():
        y = s.year(year)
        assert y is not None, year
        assert y.occupancy_change_pct == pytest.approx(occ, abs=1e-9), year
        assert y.adr_change_pct == pytest.approx(adr, abs=1e-9), year
        assert y.period == "forecast", year
    y26 = s.year(2026)
    assert y26.occupancy_pct == pytest.approx(0.681)
    assert y26.adr_usd == pytest.approx(416.34)
    assert y26.revpar_usd == pytest.approx(283.57)
    assert y26.revpar_change_pct == pytest.approx(0.005)
    # The class supply & demand table (PDF page 24) feeds the same year.
    assert y26.supply_change_pct == pytest.approx(0.07)
    assert y26.demand_change_pct == pytest.approx(0.057)


def test_other_buckets_read_their_own_tables(reading: ClassForecastReading) -> None:
    uum = reading.series(UUM).year(2026)
    assert uum.occupancy_pct == pytest.approx(0.759)
    assert uum.adr_change_pct == pytest.approx(0.036)
    me = reading.series(ME).year(2026)
    assert me.occupancy_change_pct == pytest.approx(-0.055)
    assert me.adr_change_pct == pytest.approx(0.102)
    overall = reading.series("all").year(2026)
    assert overall.occupancy_change_pct == pytest.approx(-0.008)
    assert overall.adr_change_pct == pytest.approx(0.03)
    assert overall.supply_change_pct == pytest.approx(0.059)


# ───────────────────────────── period tags ─────────────────────────────


def test_period_tags_split_the_projection_from_history(reading: ClassForecastReading) -> None:
    assert reading.first_forecast_year == 2025
    assert reading.last_forecast_year == 2029
    s = reading.series(LUU)
    assert [y.year for y in s.forecast_years] == [2025, 2026, 2027, 2028, 2029]
    assert s.year(2024).period == "actual"
    assert s.year(2015).period == "actual"
    assert [y.year for y in s.years] == sorted(y.year for y in s.years)
    assert s.year(2030) is None


def test_period_falls_back_to_the_reports_forecast_window_when_a_tag_is_missing() -> None:
    rows = [
        _row("market_study.submarket", "Miami Beach", page=3),
        _row("market_study.forecast.first_year", 2025, page=24),
        _row("market_study.forecast.last_year", 2029, page=24),
        _row(f"market_study.class.{LUU}.2024.occupancy_change_pct", 0.006),
        _row(f"market_study.class.{LUU}.2026.occupancy_change_pct", -0.012),
        _row(f"market_study.class.{LUU}.2026.adr_change_pct", 0.018),
    ]
    s = read_class_forecast(rows).series(LUU)
    assert s.year(2026).period == "forecast"
    assert s.year(2024).period == "actual"


def test_period_is_none_when_the_report_states_nothing() -> None:
    rows = [_row(f"market_study.class.{LUU}.2026.adr_change_pct", 0.018)]
    y = read_class_forecast(rows).series(LUU).year(2026)
    assert y is not None and y.period is None


# ───────────────────────────── page numbers ─────────────────────────────


def test_every_figure_names_its_page_and_document(reading: ClassForecastReading) -> None:
    luu26 = reading.series(LUU).year(2026)
    assert luu26.page == 26  # performance table — PDF page 26, printed "Page 25"
    assert luu26.doc_name == DOC_NAME
    assert luu26.refs["adr_change_pct"].page == 26
    assert luu26.refs["adr_change_pct"].field_name == f"market_study.class.{LUU}.2026.adr_change_pct"
    assert luu26.refs["supply_change_pct"].page == 24
    assert reading.series(UUM).year(2026).page == 27
    assert reading.series(ME).year(2026).page == 27
    assert reading.series(UUM).year(2026).refs["demand_change_pct"].page == 25
    assert reading.series("all").year(2026).page == 26
    assert reading.series("all").year(2026).refs["supply_rooms"].page == 24


# ───────────────────────────── overall fallback ─────────────────────────────


def test_requesting_the_overall_series_never_reports_a_fallback(reading: ClassForecastReading) -> None:
    for bucket in ("all", None):
        s = reading.series(bucket)
        assert s.bucket == "all" and s.fallback_reason is None
        assert s.label == CLASS_BUCKET_LABELS["all"]


def test_bucket_missing_from_the_report_falls_back_to_the_overall_series(rows: list[FieldRow]) -> None:
    without_class = [r for r in rows if not r.lname.startswith("market_study.class.")]
    reading = read_class_forecast(without_class)
    assert set(reading.buckets) == {"all"}
    s = reading.series(LUU)
    assert s.requested_bucket == LUU
    assert s.bucket == "all"
    assert s.fallback_reason and "Luxury & Upper Upscale" in s.fallback_reason and "overall" in s.fallback_reason
    assert s.year(2026).occupancy_change_pct == pytest.approx(-0.008)


def test_a_year_missing_from_the_bucket_is_not_served_from_the_overall_series() -> None:
    rows = [
        _row(f"market_study.class.{LUU}.2026.adr_change_pct", 0.018),
        _row("market_study.trend.2026.adr_change_pct", 0.03),
        _row("market_study.trend.2027.adr_change_pct", 0.001),
    ]
    s = read_class_forecast(rows).series(LUU)
    assert s.bucket == LUU and s.fallback_reason is None
    assert s.year(2027) is None
    assert s.last_year == 2026


# ───────────────────────────── blanks → None ─────────────────────────────


def test_blank_cells_read_as_none_never_zero(reading: ClassForecastReading) -> None:
    me = reading.series(ME)
    y2020 = me.year(2020)  # occupancy / ADR / RevPAR printed, every change column blank
    assert y2020.occupancy_pct == pytest.approx(0.552)
    assert y2020.adr_usd == pytest.approx(90.01)
    assert y2020.adr_change_pct is None
    assert y2020.occupancy_change_pct is None
    assert y2020.revpar_change_pct is None
    y2016 = me.year(2016)
    assert y2016.adr_usd == pytest.approx(101.76)
    assert y2016.adr_change_pct is None
    # 2019's performance row is entirely blank and its demand side too; only
    # the supply change (-1.3%, PDF page 25) is printed — so the year exists
    # with that single figure and every other cell None.
    y2019 = me.year(2019)
    assert y2019 is not None
    assert y2019.supply_change_pct == pytest.approx(-0.013) and y2019.page == 25
    assert y2019.occupancy_pct is None and y2019.adr_usd is None and y2019.adr_change_pct is None
    assert y2019.demand_change_pct is None
    # The LUU 2029 supply change is a printed 0% — a real figure, kept.
    assert reading.series(LUU).year(2029).supply_change_pct == 0.0


def test_a_year_with_no_readable_figure_is_not_a_year() -> None:
    rows = [
        _row(f"market_study.class.{ME}.2019.period", "actual", page=27),
        _row(f"market_study.class.{ME}.2020.adr_usd", 90.01, page=27),
    ]
    s = read_class_forecast(rows).series(ME)
    assert s.year(2019) is None
    assert s.year(2020) is not None


def test_non_numeric_or_non_positive_levels_are_none() -> None:
    rows = [
        _row(f"market_study.class.{LUU}.2026.adr_usd", "n/a"),
        _row(f"market_study.class.{LUU}.2026.revpar_usd", 0),
        _row(f"market_study.class.{LUU}.2026.occupancy_pct", 0.681),
    ]
    y = read_class_forecast(rows).series(LUU).year(2026)
    assert y.adr_usd is None and y.revpar_usd is None
    assert y.occupancy_pct == pytest.approx(0.681)


# ───────────────────────────── value normalisation ─────────────────────────────


def test_percent_strings_and_whole_percents_normalise_to_fractions() -> None:
    rows = [
        _row(f"market_study.class.{LUU}.2026.occupancy_pct", 68.1, unit="pct"),
        _row(f"market_study.class.{LUU}.2026.adr_change_pct", "1.8%"),
        _row(f"market_study.class.{LUU}.2026.occupancy_change_pct", -1.2, unit="%"),
        _row(f"market_study.class.{LUU}.2026.revpar_change_pct", 0.005),
    ]
    y = read_class_forecast(rows).series(LUU).year(2026)
    assert y.occupancy_pct == pytest.approx(0.681)
    assert y.adr_change_pct == pytest.approx(0.018)
    assert y.occupancy_change_pct == pytest.approx(-0.012)
    assert y.revpar_change_pct == pytest.approx(0.005)


def test_an_absolute_delta_in_a_change_column_is_not_a_rate() -> None:
    rows = [
        _row(f"market_study.class.{LUU}.2026.demand_change_pct", 172637),  # occupied room nights delta
        _row(f"market_study.class.{LUU}.2026.adr_usd", 416.34),
    ]
    y = read_class_forecast(rows).series(LUU).year(2026)
    assert y is not None and y.demand_change_pct is None
    assert "demand_change_pct" not in y.refs


# ───────────────────────────── documents ─────────────────────────────


def test_no_market_study_reports_no_document() -> None:
    reading = read_class_forecast([])
    assert reading.reason == REASON_NO_DOCUMENT
    assert reading.buckets == {}
    s = reading.series(LUU)
    assert s.years == () and s.bucket == "all" and s.fallback_reason
    other = _row("ttm_performance.subject.occupancy_pct", 0.76)
    other = FieldRow(**{**other.__dict__, "doc_type": "STR_TREND"})
    assert read_class_forecast([other]).reason == REASON_NO_DOCUMENT


def test_market_study_without_performance_tables_reports_no_source() -> None:
    rows = [
        _row("market_study.submarket", "Miami Beach", page=3),
        _row("market_study.supply.existing_rooms", 23162, unit="rooms", page=3),
        _row("property_overview.pipeline_hotel_1_name", "Miami Beach Hotel", page=5),
    ]
    reading = read_class_forecast(rows)
    assert reading.reason == REASON_NO_SOURCE
    assert reading.submarket == "Miami Beach"
    assert reading.detail and DOC_NAME in reading.detail


def test_newest_report_with_class_tables_wins(rows: list[FieldRow]) -> None:
    older = [
        _row("market_study.report_date", "2024-12-10", ext="ext-old", doc="Miami Beach 2024.pdf", page=3),
        _row(f"market_study.class.{LUU}.2026.adr_change_pct", 0.099, ext="ext-old", doc="Miami Beach 2024.pdf"),
    ]
    reading = read_class_forecast([*older, *rows])
    assert reading.doc_name == DOC_NAME
    assert reading.series(LUU).year(2026).adr_change_pct == pytest.approx(0.018)
    reversed_order = read_class_forecast([*rows, *older])
    assert reversed_order.doc_name == DOC_NAME


def test_rows_from_other_namespaces_are_ignored(rows: list[FieldRow]) -> None:
    noise = [
        _row("pnl_benchmark.market.adr_change_2026_forecast", 0.5, page=9),
        _row("cbre_horizons.segment_all.2026.adr_change_pct", 0.5, page=9),
    ]
    reading = read_class_forecast([*noise, *rows])
    assert reading.series(LUU).year(2026).adr_change_pct == pytest.approx(0.018)
    assert reading.series("all").year(2026).adr_change_pct == pytest.approx(0.03)


# ───────────────────────────── schema: one source ─────────────────────────────


def test_market_study_schema_advertises_the_class_forecast_contract() -> None:
    from app.agents.extraction_schemas.loader import schema_text

    md = schema_text("MARKET_STUDY")
    assert md is not None
    for leaf in (
        "occupancy_pct", "adr_usd", "revpar_usd", "occupancy_change_pct", "adr_change_pct",
        "revpar_change_pct", "supply_change_pct", "demand_change_pct", "period",
    ):
        assert f"`market_study.class.<bucket>.<YYYY>.{leaf}`" in md, leaf
    for bucket, label in ((LUU, "Luxury & Upper Upscale"), (UUM, "Upscale & Upper Midscale"), (ME, "Midscale & Economy")):
        assert f"`{bucket}`" in md and label in md
    assert "`market_study.forecast.first_year`" in md and "`market_study.forecast.last_year`" in md
    # The overall series gained the two change columns the "all" bucket reads.
    assert "`market_study.trend.<YYYY>.occupancy_change_pct`" in md
    assert "`market_study.trend.<YYYY>.adr_change_pct`" in md
    # Fractions, and the YTD line is the forecast / actual split.
    assert "-0.012" in md and "YTD" in md


def test_legacy_extractor_prompt_embeds_the_schema_file_verbatim() -> None:
    """ONE source for the MARKET_STUDY contract: the legacy ``SYSTEM_PROMPT``
    (sent whenever the dynamic-schema flag is off) splices the schema file
    in at import instead of carrying its own copy."""
    from app.agents.extraction_schemas.loader import schema_text
    from app.agents.extractor import SYSTEM_PROMPT

    md = schema_text("MARKET_STUDY")
    assert md is not None
    assert md.strip() in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.count("market_study.class.<bucket>.<YYYY>.adr_change_pct") == 1


def test_dynamic_schema_prompt_carries_the_class_forecast_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.extraction_schemas.loader import build_system_prompt

    monkeypatch.setenv("EXTRACTOR_USE_DYNAMIC_SCHEMAS", "1")
    prompt = build_system_prompt("MARKET_STUDY")
    assert prompt is not None
    assert "market_study.class.<bucket>.<YYYY>.occupancy_change_pct" in prompt
    assert "luxury_upper_upscale" in prompt


# ───────────────────────────── critic rule ─────────────────────────────


def test_critic_finds_the_real_report_internally_consistent() -> None:
    from app.agents.critic import check_market_study_consistency

    fields = _fixture()["fields"]
    assert check_market_study_consistency(fields) == []


def test_critic_caps_a_change_that_disagrees_with_its_levels() -> None:
    from app.agents.critic import (
        MARKET_STUDY_CHANGE_TOLERANCE_PTS,
        PLAUSIBILITY_CONFIDENCE_CAP,
        apply_market_study_consistency,
    )

    assert MARKET_STUDY_CHANGE_TOLERANCE_PTS == 0.3
    fields = [dict(f) for f in _fixture()["fields"]]
    target = f"market_study.class.{LUU}.2026.occupancy_change_pct"
    for f in fields:
        if f["field_name"] == target:
            f["value"] = -0.05  # printed -1.2%; 0.681 / 0.689 - 1 = -1.16%
    out_fields, conf, flags = apply_market_study_consistency(fields, {"overall": 0.97, "by_field": {}})
    assert [fl.field_name for fl in flags] == [target]
    flag = flags[0]
    assert flag.year == 2026 and flag.prior_year == 2025 and flag.metric == "occupancy"
    assert flag.implied_change_pct == pytest.approx(0.681 / 0.689 - 1, abs=1e-6)
    assert flag.delta_pts > MARKET_STUDY_CHANGE_TOLERANCE_PTS
    flagged = next(f for f in out_fields if f["field_name"] == target)
    assert flagged["confidence"] == PLAUSIBILITY_CONFIDENCE_CAP
    assert flagged["value"] == -0.05  # flagged, never altered
    assert "2025" in flagged["note"] and "0.3" in flagged["note"]
    assert conf["requires_human_review"] is True
    assert target in conf["low_confidence_fields"]
    assert conf["plausibility_flags"][0]["field_name"] == target
    # Inputs untouched.
    assert next(f for f in fields if f["field_name"] == target).get("confidence") == 0.97


def test_critic_checks_adr_and_revpar_and_the_overall_series_too() -> None:
    from app.agents.critic import check_market_study_consistency

    fields = [
        {"field_name": "market_study.trend.2025.adr_usd", "value": 288.33},
        {"field_name": "market_study.trend.2026.adr_usd", "value": 297.02},
        {"field_name": "market_study.trend.2026.adr_change_pct", "value": 0.08},  # printed 3.0%
        {"field_name": f"market_study.class.{UUM}.2025.revpar_usd", "value": 124.36},
        {"field_name": f"market_study.class.{UUM}.2026.revpar_usd", "value": 130.95},
        {"field_name": f"market_study.class.{UUM}.2026.revpar_change_pct", "value": 0.053},  # reconciles
    ]
    flags = check_market_study_consistency(fields)
    assert [(f.field_name, f.metric) for f in flags] == [("market_study.trend.2026.adr_change_pct", "adr")]


def test_critic_is_silent_without_both_levels_or_outside_market_study() -> None:
    from app.agents.critic import check_market_study_consistency

    fields = [
        {"field_name": f"market_study.class.{LUU}.2026.occupancy_pct", "value": 0.681},
        {"field_name": f"market_study.class.{LUU}.2026.occupancy_change_pct", "value": -0.5},  # no 2025 level
        {"field_name": "broker_proforma.2026.occupancy_pct", "value": 0.70},
        {"field_name": "broker_proforma.2025.occupancy_pct", "value": 0.60},
        {"field_name": "broker_proforma.2026.occupancy_change_pct", "value": 0.9},
        {"field_name": f"market_study.class.{LUU}.2026.adr_usd", "value": "n/a"},
        {"field_name": f"market_study.class.{LUU}.2025.adr_usd", "value": 409.18},
        {"field_name": f"market_study.class.{LUU}.2026.adr_change_pct", "value": 0.5},
    ]
    assert check_market_study_consistency(fields) == []
