"""FON-94 / FON-95 — the market-data envelope behind Index Analysis.

Pure unit tests (no DB, no LLM) for the assembly in ``app.api.documents``:

* ``CbreYearProjection.calendar_year`` — the legacy forecast ordinal placed on
  the calendar: the extractor's own ``year_<n>.calendar_year`` row first, then
  the report's calendar-keyed ``segment_all.<YYYY>`` forecast rows, then the
  publication year; ``None`` when nothing in the report can place it.
* ``StrTrendBlock.subject_annual`` — the subject's STR annual history.
* ``market_history`` — market-level / chain-scale actuals by calendar year,
  with document + page provenance, forecast study years excluded.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

# Isolated SQLite so importing ``app.api.documents`` (which pulls the FastAPI
# app graph) touches no shared state. Same convention as test_documents.py.
_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-market-data-ia.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.documents import (  # noqa: E402
    _aggregate_market_data,
    _build_cbre_block,
    _build_market_history,
    _build_str_trend_block,
)


def _legacy_cbre(**extra: object) -> dict[str, object]:
    flat: dict[str, object] = {
        "cbre_horizons.market": "Miami, FL",
        "cbre_horizons.year_1.occupancy_pct": 72.5,
        "cbre_horizons.year_1.adr_usd": 300.0,
        "cbre_horizons.year_2.occupancy_pct": 73.1,
        "cbre_horizons.year_2.adr_usd": 312.0,
        "cbre_horizons.year_3.adr_usd": 320.0,
    }
    flat.update(extra)
    return flat


# ───────────────────────── CBRE calendar_year ─────────────────────────


def test_cbre_calendar_year_from_extractor_row_wins() -> None:
    block = _build_cbre_block(
        _legacy_cbre(
            **{
                "cbre_horizons.year_1.calendar_year": 2026,
                "cbre_horizons.year_2.calendar_year": 2027,
                "cbre_horizons.year_3.calendar_year": 2028,
                # A conflicting publication year must NOT override the row.
                "cbre_horizons.publication_date": "Q3 2024",
            }
        )
    )
    assert block is not None
    assert [(y.year_index, y.calendar_year) for y in block.years] == [
        (1, 2026),
        (2, 2027),
        (3, 2028),
    ]


def test_cbre_calendar_year_from_segment_all_forecast_rows() -> None:
    """A v6-cached extraction: no calendar_year rows, but the report's own
    calendar-keyed segment_all rows say which years are forecast."""
    block = _build_cbre_block(
        _legacy_cbre(
            **{
                "cbre_horizons.publication_date": "Q1 2025",
                "cbre_horizons.segment_all.2023.adr_usd": 280.0,
                "cbre_horizons.segment_all.2023.period": "historical",
                "cbre_horizons.segment_all.2024.adr_usd": 290.0,
                "cbre_horizons.segment_all.2024.period": "historical",
                "cbre_horizons.segment_all.2025.adr_usd": 300.0,
                "cbre_horizons.segment_all.2025.period": "forecast",
                "cbre_horizons.segment_all.2026.adr_usd": 312.0,
                "cbre_horizons.segment_all.2026.period": "forecast",
                "cbre_horizons.segment_all.2027.adr_usd": 320.0,
                "cbre_horizons.segment_all.2027.period": "forecast",
            }
        )
    )
    assert block is not None
    assert [y.calendar_year for y in block.years] == [2025, 2026, 2027]


def test_cbre_calendar_year_falls_back_to_publication_year() -> None:
    block = _build_cbre_block(_legacy_cbre(**{"cbre_horizons.publication_date": "Q3 2024"}))
    assert block is not None
    assert [y.calendar_year for y in block.years] == [2024, 2025, 2026]


def test_cbre_calendar_year_is_none_when_nothing_places_it() -> None:
    """No calendar_year row, no segment rows, no publication date: the
    envelope says so (None) rather than guessing "next year"."""
    block = _build_cbre_block(_legacy_cbre())
    assert block is not None
    assert len(block.years) == 3
    assert all(y.calendar_year is None for y in block.years)


def test_cbre_segment_all_only_extraction_builds_the_series() -> None:
    """An LLM-path extraction with only the calendar-keyed rows still yields
    the forecast series, Year 1 = the first forecast calendar year."""
    block = _build_cbre_block(
        {
            "cbre_horizons.publication_date": "2025-02-01",
            "cbre_horizons.segment_all.2024.occupancy_pct": 70.0,
            "cbre_horizons.segment_all.2024.adr_usd": 290.0,
            "cbre_horizons.segment_all.2024.period": "actual",
            "cbre_horizons.segment_all.2025.occupancy_pct": 71.0,
            "cbre_horizons.segment_all.2025.adr_usd": 300.0,
            "cbre_horizons.segment_all.2025.revpar_change_pct": 0.04,
            "cbre_horizons.segment_all.2025.period": "forecast",
            "cbre_horizons.segment_all.2026.occupancy_pct": 72.0,
            "cbre_horizons.segment_all.2026.adr_usd": 309.0,
            "cbre_horizons.segment_all.2026.period": "forecast",
        }
    )
    assert block is not None
    assert [(y.year_index, y.calendar_year, y.adr_usd) for y in block.years] == [
        (1, 2025, 300.0),
        (2, 2026, 309.0),
    ]
    assert block.years[0].revpar_growth_pct == 0.04


# ───────────────────────── STR subject annual ─────────────────────────


def test_str_subject_annual_history_and_report_year() -> None:
    flat: dict[str, object] = {
        "str_trend.report_year": 2025,
        "ttm_performance.subject.occupancy_pct": 0.716,
        "ttm_performance.subject.adr_usd": 288.0,
        "ttm_performance.subject.annual.2023.occupancy_pct": 68.2,
        "ttm_performance.subject.annual.2023.adr_usd": 262.0,
        "ttm_performance.subject.annual.2023.revpar_usd": 178.7,
        "ttm_performance.subject.annual.2024.occupancy_pct": 70.1,
        "ttm_performance.subject.annual.2024.adr_usd": 275.0,
        # A year with only non-positive cells is not a year.
        "ttm_performance.subject.annual.2022.occupancy_pct": 0,
    }
    block = _build_str_trend_block(flat, {}, doc_name="STR Trend May 2025.xlsx")
    assert block is not None
    assert block.report_year == 2025
    assert block.doc_name == "STR Trend May 2025.xlsx"
    assert [(a.calendar_year, a.occupancy_pct, a.adr_usd, a.revpar_usd) for a in block.subject_annual] == [
        (2023, 68.2, 262.0, 178.7),
        (2024, 70.1, 275.0, None),
    ]


# ───────────────────────── market history ─────────────────────────


def test_market_history_collects_market_segment_and_study_actuals() -> None:
    str_flat = {
        "ttm_performance.segment.upper_upscale.occupancy_pct_2024": 74.0,
        "ttm_performance.segment.upper_upscale.adr_usd_2024": 240.0,
        "ttm_performance.market.occupancy_pct_ttm": 71.0,
    }
    pnl_flat = {"pnl_benchmark.market.adr_usd_2023": 231.0}
    study_flat = {
        "market_study.trend.2022.occupancy_pct": 0.66,
        "market_study.trend.2022.adr_usd": 210.0,
        "market_study.trend.2022.period": "actual",
        "market_study.trend.2026.occupancy_pct": 0.72,
        "market_study.trend.2026.period": "forecast",
    }
    points = _build_market_history(str_flat, pnl_flat, study_flat)
    keyed = {(p.doc_type, p.segment, p.period, p.calendar_year): p for p in points}
    # Annual points first, oldest year first; TTM last.
    assert [(p.period, p.calendar_year) for p in points] == [
        ("annual", 2022),
        ("annual", 2023),
        ("annual", 2024),
        ("ttm", None),
    ]
    assert keyed[("MARKET_STUDY", None, "annual", 2022)].adr_usd == 210.0
    assert keyed[("PNL_BENCHMARK", None, "annual", 2023)].adr_usd == 231.0
    seg = keyed[("STR_TREND", "upper_upscale", "annual", 2024)]
    assert (seg.occupancy_pct, seg.adr_usd) == (74.0, 240.0)
    assert sorted(seg.fields) == [
        "ttm_performance.segment.upper_upscale.adr_usd_2024",
        "ttm_performance.segment.upper_upscale.occupancy_pct_2024",
    ]
    # The study's 2026 forecast row never becomes "history".
    assert ("MARKET_STUDY", None, "annual", 2026) not in keyed


# ───────────────────────── end to end ─────────────────────────


def test_aggregate_carries_doc_and_page_provenance() -> None:
    deal_id = uuid4()
    cbre_doc, str_doc, study_doc = uuid4(), uuid4(), uuid4()
    rows = [
        {
            "document_id": cbre_doc,
            "doc_type": "CBRE_HORIZONS",
            "filename": "Miami Horizons Q3 2025.xlsx",
            "fields": [
                {"field_name": "cbre_horizons.publication_date", "value": "Q3 2025"},
                {"field_name": "cbre_horizons.year_1.adr_usd", "value": 300.0, "source_page": 4},
                {"field_name": "cbre_horizons.year_1.calendar_year", "value": 2025, "source_page": 4},
                {"field_name": "cbre_horizons.year_2.adr_usd", "value": 312.0, "source_page": 4},
                {"field_name": "cbre_horizons.year_2.calendar_year", "value": 2026, "source_page": 4},
            ],
        },
        {
            "document_id": str_doc,
            "doc_type": "STR_TREND",
            "filename": "STR Trend May 2025.xlsx",
            "fields": [
                {"field_name": "ttm_performance.subject.occupancy_pct", "value": 0.716},
                {"field_name": "ttm_performance.subject.annual.2024.adr_usd", "value": 275.0, "source_page": 3},
                {"field_name": "ttm_performance.segment.luxury.adr_usd_2024", "value": 410.0, "source_page": 7},
            ],
        },
        {
            "document_id": study_doc,
            "doc_type": "MARKET_STUDY",
            "filename": "CoStar Submarket.pdf",
            "fields": [
                {"field_name": "market_study.trend.2024.occupancy_pct", "value": 0.7, "source_page": 12},
                {"field_name": "market_study.trend.2024.period", "value": "actual"},
            ],
        },
    ]
    env = _aggregate_market_data(rows, deal_id)

    assert env.cbre_horizons is not None
    assert env.cbre_horizons.doc_name == "Miami Horizons Q3 2025.xlsx"
    assert [(y.calendar_year, y.page) for y in env.cbre_horizons.years] == [(2025, 4), (2026, 4)]

    assert env.str_trend is not None
    assert env.str_trend.doc_name == "STR Trend May 2025.xlsx"
    assert env.str_trend.subject_annual[0].calendar_year == 2024
    assert env.str_trend.subject_annual[0].page == 3

    by = {(p.doc_type, p.segment, p.calendar_year): p for p in env.market_history}
    study = by[("MARKET_STUDY", None, 2024)]
    assert (study.doc_name, study.page, study.occupancy_pct) == ("CoStar Submarket.pdf", 12, 0.7)
    seg = by[("STR_TREND", "luxury", 2024)]
    assert (seg.doc_name, seg.page, seg.adr_usd) == ("STR Trend May 2025.xlsx", 7, 410.0)

    # The market study feeds only market_history, and is cited as a source.
    assert set(env.sources) == {"CBRE_HORIZONS", "STR_TREND", "MARKET_STUDY"}
    assert env.pnl_benchmark is None


def test_aggregate_without_filename_column_still_builds() -> None:
    """``due_diligence`` calls the aggregator with rows that carry no
    ``filename`` — provenance is simply absent, nothing breaks."""
    env = _aggregate_market_data(
        [
            {
                "document_id": str(uuid4()),
                "doc_type": "CBRE_HORIZONS",
                "fields": [{"field_name": "cbre_horizons.year_1.adr_usd", "value": 300.0}],
            }
        ],
        uuid4(),
    )
    assert env.cbre_horizons is not None
    assert env.cbre_horizons.doc_name is None
    assert env.cbre_horizons.years[0].calendar_year is None
    assert env.market_history == []
