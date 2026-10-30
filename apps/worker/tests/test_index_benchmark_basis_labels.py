"""Sam decision #6 (2026-10-15) — label actual history vs projected benchmarks.

"Using reported TTM for Year 1 and CBRE forecast data for Year 2 onward …
make sure the source and reporting period are clearly labeled so analysts
understand the distinction between actual historical data and projected
benchmarks."

Pinned here (pure — no DB, no LLM):

* every by-year benchmark figure carries a ``basis`` (actual / forecast /
  assumption) and a one-line ``basis_label``:
    Year 1  → "Actual · STR TTM May 2025 · <doc>"
    Year 2+ → "Forecast · CBRE Horizons 2027 · <doc> p.N" (market benchmark)
              "Assumption · grown at market growth" (comp-set series)
* the STR TTM period end and the CBRE report date reach the API
  (``subject_period_end`` / ``cbre_report_date`` / per-figure fields);
* ``GET /deals/{id}/market-data`` ``str_trend.ttm_period_end`` names the TTM
  window of the extraction that supplied the subject TTM;
* the labels are display-only — the engine driver records (what the revenue
  engine echoes) are unchanged.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-index-basis-labels.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.documents import _aggregate_market_data  # noqa: E402
from app.api.market import index_methodology_response  # noqa: E402
from app.services.index_methodology import (  # noqa: E402
    ASSUMPTION_GROWN_LABEL,
    KEY_ADR_GROWTH,
    KEY_MPI_TARGET_BY_YEAR,
    KEY_OCC_GROWTH,
    METHOD_MARKET_BENCHMARK,
    OVERRIDE_METHOD,
    driver_record,
    period_end_text,
    resolve_index_by_year,
    resolve_index_methodology,
)
from app.services.market_fields import FieldRow  # noqa: E402

STR_DOC = "Subject STR Trend May 2025.xlsx"
MARKET_DOC = "Miami Beach Submarket 2025.pdf"
CBRE_DOC = "CBRE Horizons Miami Q1 2026.pdf"


def _row(
    name: str, value: Any, *, doc: str, doc_type: str, page: int | None = 2, period: str | None = None
) -> FieldRow:
    return FieldRow(
        field_name=name, value=value, unit=None, page=page, doc_name=doc,
        doc_id=f"doc-{doc_type.lower()}", extraction_id=f"ext-{doc_type.lower()}",
        doc_type=doc_type, period=period,
    )


STR_ROWS = [
    _row("ttm_performance.subject.occupancy_pct", 84.6, doc=STR_DOC, doc_type="STR_TREND", page=3),
    _row("ttm_performance.subject.adr_usd", 255.0, doc=STR_DOC, doc_type="STR_TREND", page=3),
    _row("ttm_performance.indices.mpi_occupancy_index", 110.0, doc=STR_DOC, doc_type="STR_TREND", page=4),
    _row("ttm_performance.indices.ari_adr_index", 102.0, doc=STR_DOC, doc_type="STR_TREND", page=4),
    # The subject monthly series — its last month is the TTM window end.
    *[
        _row(f"ttm_performance.subject.monthly.{y}_{m:02d}.occupancy_pct", 80.0, doc=STR_DOC, doc_type="STR_TREND", page=6)
        for y, m in [(2024, 6), (2024, 12), (2025, 5)]
    ],
]
MARKET_ROWS = [
    _row("pnl_benchmark.market.occupancy_pct", 0.712, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=5),
    _row("pnl_benchmark.market.adr_usd", 231.0, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=5),
    _row("pnl_benchmark.market.adr_change_2026_forecast", 0.031, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=6),
    _row("pnl_benchmark.market.occupancy_change_2026_forecast", 0.005, doc=MARKET_DOC, doc_type="MARKET_STUDY", page=6),
]
CBRE_ROWS = [
    _row("cbre_horizons.publication_date", "March 2026", doc=CBRE_DOC, doc_type="CBRE_HORIZONS", page=1),
    _row("cbre_horizons.segment_all.2026.occupancy_pct", 73.0, doc=CBRE_DOC, doc_type="CBRE_HORIZONS", page=4, period="forecast"),
    _row("cbre_horizons.segment_all.2026.adr_usd", 240.0, doc=CBRE_DOC, doc_type="CBRE_HORIZONS", page=4, period="forecast"),
    _row("cbre_horizons.segment_all.2026.period", "forecast", doc=CBRE_DOC, doc_type="CBRE_HORIZONS", page=4),
    _row("cbre_horizons.segment_all.2027.occupancy_pct", 74.0, doc=CBRE_DOC, doc_type="CBRE_HORIZONS", page=4, period="forecast"),
    _row("cbre_horizons.segment_all.2027.adr_usd", 248.0, doc=CBRE_DOC, doc_type="CBRE_HORIZONS", page=4, period="forecast"),
    _row("cbre_horizons.segment_all.2027.period", "forecast", doc=CBRE_DOC, doc_type="CBRE_HORIZONS", page=4),
]
ROWS = [*STR_ROWS, *MARKET_ROWS, *CBRE_ROWS]
CAL = [2025, 2026, 2027]


def test_period_end_text_reads_month_and_year() -> None:
    assert period_end_text("2025-05") == "May 2025"
    assert period_end_text("2025-05-31") == "May 2025"
    assert period_end_text("2025") == "2025"
    assert period_end_text(None) is None
    assert period_end_text("not a date") is None


def test_str_comp_set_year_one_is_actual_and_out_years_are_assumptions() -> None:
    growth = {KEY_OCC_GROWTH: {"value": 0.01, "note": "CBRE"}, KEY_ADR_GROWTH: {"value": 0.03, "note": "CBRE"}}
    r = resolve_index_by_year(ROWS, growth, hold_years=3, calendar_years=CAL)
    y1, y2, y3 = r.years
    for fig in (y1.benchmark_occupancy, y1.benchmark_adr):
        assert fig.basis == "actual"
        assert fig.basis_label == f"Actual · STR TTM May 2025 · {STR_DOC}"
        assert fig.period_end == "2025-05"
    for y in (y2, y3):
        for fig in (y.benchmark_occupancy, y.benchmark_adr):
            assert fig.value is not None
            assert fig.basis == "assumption"
            assert fig.basis_label == ASSUMPTION_GROWN_LABEL == "Assumption · grown at market growth"
    # The comp-set series behind the Index Analysis comp-set table is labelled the same way.
    assert r.comp_set_occupancy[0].basis_label == f"Actual · STR TTM May 2025 · {STR_DOC}"
    assert [f.basis for f in r.comp_set_adr] == ["actual", "assumption", "assumption"]


def test_market_benchmark_year_one_actual_then_cbre_forecast_with_page_and_report_date() -> None:
    r = resolve_index_by_year(ROWS, {OVERRIDE_METHOD: METHOD_MARKET_BENCHMARK}, hold_years=3, calendar_years=CAL)
    assert r.method == METHOD_MARKET_BENCHMARK
    y1, y2, y3 = r.years
    assert y1.benchmark_occupancy.basis == "actual"
    assert y1.benchmark_occupancy.basis_label == f"Actual · Market study · {MARKET_DOC}"
    assert y2.benchmark_occupancy.basis == "forecast"
    assert y2.benchmark_occupancy.basis_label == f"Forecast · CBRE Horizons 2026 · {CBRE_DOC} p.4"
    assert y2.benchmark_occupancy.report_date == "March 2026"
    assert y3.benchmark_adr.basis_label == f"Forecast · CBRE Horizons 2027 · {CBRE_DOC} p.4"
    # The comp-set series is still an assumption (and still Year-1 actual).
    assert [f.basis for f in r.comp_set_occupancy] == ["actual", "assumption", "assumption"]
    assert r.comp_set_occupancy[1].basis_label == ASSUMPTION_GROWN_LABEL


def test_a_figure_with_no_value_carries_no_basis() -> None:
    # No growth assumption → comp-set out-years are "—" with a reason, no label.
    r = resolve_index_by_year(STR_ROWS, {}, hold_years=2, calendar_years=[2025, 2026])
    y2 = r.years[1].benchmark_occupancy
    assert y2.value is None and y2.basis is None and y2.basis_label is None
    assert y2.detail


def test_api_carries_ttm_period_end_cbre_report_date_and_per_figure_labels() -> None:
    overrides = {OVERRIDE_METHOD: METHOD_MARKET_BENCHMARK}
    reading = resolve_index_methodology(ROWS, overrides)
    by_year = resolve_index_by_year(ROWS, overrides, hold_years=3, calendar_years=CAL)
    out = index_methodology_response(uuid4(), reading, by_year).model_dump()
    assert out["subject_period_end"] == "2025-05"
    assert out["cbre_report_date"] == "March 2026"
    assert out["subject_occupancy"]["basis"] == "actual"
    rows = out["by_year"]
    assert rows[0]["benchmark_occupancy"]["basis"] == "actual"
    assert rows[1]["benchmark_occupancy"]["basis"] == "forecast"
    assert rows[1]["benchmark_occupancy"]["basis_label"].startswith("Forecast · CBRE Horizons 2026 · ")
    assert rows[1]["benchmark_occupancy"]["report_date"] == "March 2026"
    comp = out["comp_set_by_year"]
    assert comp[0]["occupancy"]["basis_label"] == f"Actual · STR TTM May 2025 · {STR_DOC}"


def test_labels_are_display_only_engine_driver_records_unchanged() -> None:
    overrides = {
        KEY_MPI_TARGET_BY_YEAR: {"value": [110, 112, 114], "note": "ramp"},
        KEY_OCC_GROWTH: {"value": 0.01, "note": "x"},
        KEY_ADR_GROWTH: {"value": 0.03, "note": "x"},
    }
    r = resolve_index_by_year(ROWS, overrides, hold_years=3, calendar_years=CAL)
    rec = driver_record(r.years[1], r.method)
    # The record the revenue engine echoes names no basis / label fields.
    assert not any(k.startswith("basis") or k in ("period_end", "report_date") for k in rec)
    assert rec["benchmark_occupancy_source"] == r.years[1].benchmark_occupancy.detail


def _str_extraction(fields: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "fields": fields, "doc_type": "STR_TREND", "document_id": str(uuid4()),
        "filename": STR_DOC, **extra,
    }


def test_market_data_str_trend_names_the_ttm_period_end() -> None:
    fields = [
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 84.6, "source_page": 3},
        {"field_name": "ttm_performance.subject.adr_usd", "value": 255.0, "source_page": 3},
        {"field_name": "ttm_performance.subject.monthly.2025_05.occupancy_pct", "value": 80.0},
        {"field_name": "ttm_performance.subject.monthly.2024_06.occupancy_pct", "value": 79.0},
    ]
    env = _aggregate_market_data([_str_extraction(fields)], uuid4())
    assert env.str_trend is not None
    assert env.str_trend.ttm_period_end == "2025-05"
    assert env.str_trend.ttm_period_end_basis == "subject_monthly_series"
    assert env.str_trend.ttm_doc_name == STR_DOC


def test_market_data_ttm_period_end_falls_back_to_the_document_date_then_none() -> None:
    fields = [{"field_name": "ttm_performance.subject.adr_usd", "value": 255.0}]
    env = _aggregate_market_data(
        [_str_extraction(fields, filename="str.xlsx", report_as_of="2025-03-31", report_as_of_precision="day")],
        uuid4(),
    )
    assert env.str_trend is not None
    assert env.str_trend.ttm_period_end == "2025-03-31"
    assert env.str_trend.ttm_period_end_basis == "document_report_as_of"

    env2 = _aggregate_market_data([_str_extraction(fields, filename="str.xlsx")], uuid4())
    assert env2.str_trend is not None
    assert env2.str_trend.ttm_period_end is None and env2.str_trend.ttm_period_end_basis is None
