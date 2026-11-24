"""FON-60 / FON-61 — Index Analysis and Market Overview read ONE comp-set
roster; the supply pipeline lists only the deal's market (Sam QA 2026-11-16).

Live facts (deal b67bfdee, read-only 2026-11-16):

* Four STR-lane extractions: the May-2025 trend (5 rows incl. "Blue Moon
  Hotel · 75"), the Dec-2023 trend ("The Betsy Hotel"), the July-2025 daily
  ("Closed - Blue Moon Hotel · 0") and a Business Plan .docx mis-tagged
  STR_TREND (FON-123) carrying its own 5-row "comp set" (Z Ocean 45, Dream
  South Beach 107, The Confidante 339, Nautilus 250, Shelborne closed).
  Market Overview read the union → "7 hotels · 978 keys"; Index Analysis
  read ONE report's roster through ``/market-data`` → 344 keys.
* The CoStar "Miami Beach Supply 12.10.25.xlsx" came through as 45
  ``under_construction.<n>.*`` rows naming no market (Home2 Suites San
  Diego Downtown, Bally's Chicago, Fairmont Orlando, Kimpton Garden Grove
  Anaheim …) and was listed as the subject's pipeline.

Pure functions — no DB.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-fon60-roster.db"
if _TMP_DB.exists():
    _TMP_DB.unlink()
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{_TMP_DB}")
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.api.documents import _aggregate_market_data  # noqa: E402
from app.api.market import MarketCompSetBlock  # noqa: E402
from app.services.market_comp_set import build_str_inputs, derive_comp_set_from_inputs  # noqa: E402
from app.services.market_fields import FieldRow, parse_extraction_records  # noqa: E402
from app.services.market_study_reader import read_supply_growth  # noqa: E402

STR_STAMP = "template:str_trend:v1;ps=959bc26b;reg=2;pv=v8"
DOCX_STAMP = "router:extract-pre-router-structural-override;dt:STR_TREND;extractor;ps=8ec5df00;reg=2;pv=v8"


def _str_record(
    filename: str,
    roster: list[tuple[str, int, str | None, str | None]],
    *,
    rollup_size: int,
    rollup_keys: int,
    created_at: str,
    report_as_of: str,
    agent_version: str = STR_STAMP,
) -> dict[str, Any]:
    fields: list[dict[str, Any]] = [
        {"field_name": "ttm_performance.subject.occupancy_pct", "value": 78.4},
        {"field_name": "comp_set.comp_set_size", "value": rollup_size},
        {"field_name": "comp_set.total_keys", "value": rollup_keys},
    ]
    for i, (name, keys, status, str_id) in enumerate(roster, start=1):
        fields.append({"field_name": f"ttm_performance.compset.{i}.name", "value": name})
        fields.append({"field_name": f"ttm_performance.compset.{i}.keys", "value": keys})
        if status is not None:
            fields.append({"field_name": f"ttm_performance.compset.{i}.status", "value": status})
        if str_id is not None:
            fields.append({"field_name": f"ttm_performance.compset.{i}.str_id", "value": str_id})
    return {
        "extraction_id": uuid4(),
        "document_id": uuid4(),
        "doc_type": "STR_TREND",
        "agent_version": agent_version,
        "ai_proposed_doc_type": None,
        "filename": filename,
        "fields": fields,
        "created_at": created_at,
        "report_as_of": report_as_of,
        "report_as_of_precision": "year",
    }


def _live_str_records() -> list[dict[str, Any]]:
    """The three STR reports, newest EXTRACTION first (the ``created_at
    DESC`` order both endpoints read)."""
    return [
        _str_record(
            "Copy of ANG-20250500-USD-E.xlsx",
            [
                ("Z Ocean Hotel, Classico A Sonesta Collection", 35, None, "53909"),
                ("Blue Moon Hotel", 75, None, "34401"),
                ("The Betsy South Beach", 129, None, "44117"),
                ("The Tony Hotel of South Beach", 73, None, "39070"),
                ("Dream South Beach", 107, None, "33931"),
            ],
            rollup_size=5, rollup_keys=419,
            created_at="2026-10-19T18:49:56+00:00", report_as_of="2025-12-31",
        ),
        _str_record(
            "Copy of ANG-20231200-USD-E.xlsx",
            [
                ("Z Ocean Hotel, Classico A Sonesta Collection", 40, None, "53909"),
                ("Blue Moon Hotel", 75, None, "34401"),
                ("The Betsy Hotel", 129, None, "44117"),
                ("The Tony Hotel of South Beach", 73, None, "39070"),
                ("Dream South Beach", 107, None, "33931"),
            ],
            rollup_size=5, rollup_keys=424,
            created_at="2026-10-19T18:47:22+00:00", report_as_of="2023-12-31",
        ),
        _str_record(
            "Copy of 56387-20250713-USD-E.xlsx",
            [
                ("Dream South Beach", 107, None, "33931"),
                ("Closed - Blue Moon Hotel", 0, "closed", "34401"),
                ("The Tony Hotel of South Beach", 73, None, "39070"),
                ("The Betsy South Beach", 129, None, "44117"),
                ("Z Ocean Hotel, Classico A Sonesta Collection", 35, None, "53909"),
            ],
            rollup_size=5, rollup_keys=344,
            created_at="2026-10-19T18:47:21+00:00", report_as_of="2025-12-31",
        ),
    ]


def _business_plan_docx() -> dict[str, Any]:
    return _str_record(
        "Copy of 2024 Business Plan.docx",
        [
            ("Z Ocean", 45, "Rebranded to Sonesta Classico, undergoing soft goods renovation", None),
            ("Dream South Beach", 107, "Rebranding into a Hyatt hotel", None),
            ("The Confidante", 339, "Converting into an Andaz and renovating", None),
            ("Nautilus South Beach", 250, "Rebranded to a Sonesta", None),
            ("Shelborne", 270, "closed", None),
        ],
        rollup_size=5, rollup_keys=1011,
        created_at="2026-10-19T18:48:23+00:00", report_as_of="2024-12-31",
        agent_version=DOCX_STAMP,
    )


def _overview_comp_set(records: list[dict[str, Any]]) -> MarketCompSetBlock:
    """Exactly what ``GET /market/{deal}/overview`` puts in ``comp_set``
    (``_market_blocks`` → ``build_str_inputs`` → ``derive_comp_set_from_inputs``)."""
    return MarketCompSetBlock.of(derive_comp_set_from_inputs(build_str_inputs(parse_extraction_records(records))))


def _envelope_keys(records: list[dict[str, Any]]) -> tuple[int | None, int | None, int]:
    """What Index Analysis reads off ``/market-data``: ``total_keys``, the
    active-roster key sum (``compSetKeys``) and the active hotel count."""
    env = _aggregate_market_data(records, uuid4())
    assert env.str_trend is not None
    active = [e for e in env.str_trend.compset if e.status != "closed"]
    return env.str_trend.total_keys, sum(e.keys or 0 for e in active), len(active)


def test_index_analysis_and_market_overview_read_the_same_roster_on_the_live_deal() -> None:
    """Sam 2026-11-16: 344 (Index Analysis) vs 978 (Market Overview). Both
    endpoints now derive from ``derive_comp_set_union`` over the same
    records, so hotel count and keys agree. With FON-123 merged the
    mis-tagged Business Plan (no STR id on any comp row) is not an STR
    roster: ``build_str_inputs`` skips it on BOTH surfaces, so the live
    deal reads 4 hotels · 344 keys everywhere and the Overview names the
    skipped document instead of unioning its 5 hotels into 978."""
    records = [*_live_str_records()]
    records.insert(1, _business_plan_docx())  # created 18:48:23 — between the May trend and the Dec trend
    block = _overview_comp_set(records)
    total_keys, roster_sum, active_count = _envelope_keys(records)

    assert block.active_keys == 344 and block.active_count == 4
    assert total_keys == block.active_keys == 344
    assert roster_sum == block.active_keys
    assert active_count == block.active_count == 4
    assert [(sk.doc_name, sk.hotels_listed, sk.reason) for sk in block.skipped_documents] == [
        ("Copy of 2024 Business Plan.docx", 5, "no_str_identifiers")
    ]
    assert "1 document skipped: no STR identifiers" in (block.note or "")


def test_retagged_deal_reads_344_keys_four_hotels_one_closed_on_both_surfaces() -> None:
    """The re-tagged copy (d0bfe816): Business Plan out of the STR lane →
    "4 hotels · 344 keys · 1 closed excluded" everywhere."""
    records = _live_str_records()
    block = _overview_comp_set(records)
    env = _aggregate_market_data(records, uuid4())
    assert env.str_trend is not None

    assert (block.active_count, block.active_keys, block.closed_count) == (4, 344, 1)
    assert env.str_trend.total_keys == 344
    assert env.str_trend.comp_set_size == 4
    by_name = {e.name: e for e in env.str_trend.compset}
    assert by_name["Blue Moon Hotel"].status == "closed"
    assert sorted(n for n, e in by_name.items() if e.status == "active") == [
        "Dream South Beach",
        "The Betsy South Beach",
        "The Tony Hotel of South Beach",
        "Z Ocean Hotel, Classico A Sonesta Collection",
    ]
    # The envelope lists the union's hotels in the union's order with the
    # union's keys — the same rows the Market Overview roster renders.
    assert [(e.name, e.keys, e.status) for e in env.str_trend.compset] == [
        (h.name, h.keys, h.status) for h in block.hotels
    ]
    # The report's rollup (419 / 424 / 344) is never the headline while a roster exists.
    assert block.keys_basis == "active_roster" and block.reported_total_keys in (344, 419, 424)


def test_union_keys_follow_report_period_order_not_upload_order() -> None:
    """Z Ocean is 40 keys in the Dec-2023 report and 35 in the 2025 reports;
    the union reads keys from the most recent REPORT PERIOD, and the
    envelope must agree whatever order the rows arrived in."""
    records = list(reversed(_live_str_records()))  # oldest extraction first
    block = _overview_comp_set(records)
    total_keys, roster_sum, active_count = _envelope_keys(records)
    z_ocean = next(h for h in block.hotels if h.str_id == "53909")
    assert z_ocean.keys == 35
    assert total_keys == roster_sum == block.active_keys == 344
    assert active_count == block.active_count == 4


# ───────────────────────────── supply pipeline ─────────────────────────────

XLSX = "Miami Beach Supply 12.10.25.xlsx"
PDF = "Miami Beach-Hospitality-Submarket-2025-12-10.pdf"


def _row(name: str, value: Any, *, doc: str, doc_id: str = "d1", page: int | None = 1) -> FieldRow:
    return FieldRow(
        field_name=name, value=value, unit=None, page=page, doc_name=doc, doc_id=doc_id,
        extraction_id=f"ext-{doc_id}", doc_type="MARKET_STUDY",
    )


def _hotel_rows(prefix: str, doc: str, doc_id: str, name: str, rooms: int, status: str, **extra: str) -> list[FieldRow]:
    rows = [
        _row(f"{prefix}.name", name, doc=doc, doc_id=doc_id),
        _row(f"{prefix}.rooms", rooms, doc=doc, doc_id=doc_id),
        _row(f"{prefix}.status", status, doc=doc, doc_id=doc_id),
    ]
    rows.extend(_row(f"{prefix}.{k}", v, doc=doc, doc_id=doc_id) for k, v in extra.items())
    return rows


def test_spreadsheet_pipeline_rows_naming_no_market_are_not_listed_as_the_deals_supply() -> None:
    """The live export shape: canonical ``under_construction.<n>`` rows from
    the Supply workbook with no market on any row. None is the subject's
    pipeline; the filter line says so; no market is invented for them."""
    rows = [
        *_hotel_rows("under_construction.1", XLSX, "x", "Home2 Suites by Hilton San Diego Downtown", 130, "under_construction"),
        *_hotel_rows("under_construction.23", XLSX, "x", "Bally's Chicago Hotel", 500, "final_planning"),
        *_hotel_rows("under_construction.44", XLSX, "x", "Fairmont Orlando Global Gateway", 550, "planned"),
        *_hotel_rows("under_construction.12", XLSX, "x", "Kimpton Garden Grove Anaheim", 371, "under_construction"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=2025, market_terms=["Miami Beach, FL"])
    assert s.pipeline_hotels == []
    assert s.under_construction_rooms is None and s.final_planning_rooms is None and s.planned_rooms is None
    assert s.pipeline_filter is not None
    assert s.pipeline_filter.total == 4 and s.pipeline_filter.matched == 0
    assert s.pipeline_filter.market_unknown_rows == 4 and s.pipeline_filter.other_market_rows == 0
    assert s.pipeline_filter.doc_name == XLSX
    assert s.pipeline_filter.note == (
        f"{XLSX} names no market for any of its 4 pipeline rows; none was counted as Miami Beach, FL supply."
    )
    assert s.pipeline_filter.note in (s.detail or "")
    assert s.pipeline_filter.listed_count == 0 and s.pipeline_filter.listed_docs == []
    assert s.under_construction_rooms_basis is None and s.final_planning_rooms_basis is None


def test_report_list_under_a_market_less_export_names_where_the_listed_projects_come_from() -> None:
    """FON-126 (Sam QA 2026-11-23) — live: the Pipeline Hotel table listed 9
    Miami Beach projects (Grand Hyatt Miami Beach Convention Center, Hilton
    Miami Beach, Delano South Beach …) while the note under it said the
    Supply workbook's 45 rows named no market, so a reader could not tell
    whether the 9 fed the 1,326 / 1,300 room totals. The listed projects
    are the CoStar submarket PDF's own list; the export contributed none.
    The note must say so, and each room total must say whether it is the
    report's own stated figure or the listed projects summed."""
    rows = [
        *_hotel_rows("under_construction.1", PDF, "p", "Grand Hyatt Miami Beach Convention Center", 800, "under_construction"),
        *_hotel_rows("under_construction.2", PDF, "p", "Hilton Miami Beach", 289, "under_construction"),
        _row("property_overview.pipeline_hotel_1_name", "Delano South Beach", doc=PDF, doc_id="p"),
        _row("property_overview.pipeline_hotel_1_rooms", 170, doc=PDF, doc_id="p"),
        _row("property_overview.pipeline_hotel_1_status", "Final Planning", doc=PDF, doc_id="p"),
        # The report's own stated under-construction total (≠ 800 + 289).
        _row("property_overview.rooms_under_construction_total", 1326, doc=PDF, doc_id="p"),
        *_hotel_rows("under_construction.1", XLSX, "x", "Home2 Suites by Hilton San Diego Downtown", 130, "under_construction"),
        *_hotel_rows("under_construction.23", XLSX, "x", "Bally's Chicago Hotel", 500, "final_planning"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=2025, market_terms=["Miami Beach, FL"])
    assert [h.name for h in (s.pipeline_hotels or [])] == [
        "Grand Hyatt Miami Beach Convention Center",
        "Hilton Miami Beach",
        "Delano South Beach",
    ]
    assert all(h.doc_name == PDF for h in (s.pipeline_hotels or []))
    # The headline is the report's stated total, not the sum of the listed rows.
    assert s.under_construction_rooms == 1326 and s.under_construction_rooms_basis == "reported"
    # No final-planning total is stated → the listed final-planning project is summed.
    assert s.final_planning_rooms == 170 and s.final_planning_rooms_basis == "summed"
    assert s.planned_rooms is None and s.planned_rooms_basis is None
    f = s.pipeline_filter
    assert f is not None and (f.total, f.matched, f.market_unknown_rows, f.other_market_rows) == (2, 0, 2, 0)
    assert f.listed_count == 3 and f.listed_docs == [PDF]
    assert f.note == (
        f"{XLSX} names no market for any of its 2 pipeline rows; none was counted as Miami Beach, FL supply. "
        f"The 3 projects listed are from {PDF}, not from {XLSX}."
    )
    assert f.note in (s.detail or "")


def test_spreadsheet_rows_count_only_when_they_name_the_deals_market() -> None:
    rows = [
        *_hotel_rows("under_construction.1", XLSX, "x", "Shore Club", 100, "under_construction", market="Miami, FL", submarket="Miami Beach"),
        *_hotel_rows("under_construction.2", XLSX, "x", "Home2 Suites by Hilton San Diego Downtown", 130, "under_construction", market="San Diego, CA"),
        *_hotel_rows("under_construction.3", XLSX, "x", "Unplaced Hotel", 90, "final_planning"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=2025, market_terms=["Miami Beach, FL", "Miami Beach"])
    assert [h.name for h in (s.pipeline_hotels or [])] == ["Shore Club"]
    assert s.under_construction_rooms == 100
    f = s.pipeline_filter
    # San Diego is the unmatched remainder (3 - 1 matched - 1 unknown); the
    # unknown row is named in the note and never taken as Miami Beach supply.
    assert f is not None and (f.total, f.matched, f.market_unknown_rows, f.other_market_rows) == (3, 1, 1, 0)
    assert f.note == f"1 of 3 rows carry no market or submarket and were not counted in {XLSX}."


def test_submarket_report_list_is_kept_but_a_row_naming_another_market_is_not() -> None:
    """The PDF submarket report's own "Under Construction" table IS the
    submarket's — listed as it is — except a row that itself names a
    different market."""
    rows = [
        *_hotel_rows("under_construction.1", PDF, "p", "Grand Hyatt Miami Beach Convention Center", 147, "under_construction"),
        *_hotel_rows("under_construction.2", PDF, "p", "Hilton Miami Beach", 289, "under_construction"),
        *_hotel_rows("under_construction.3", PDF, "p", "Hilton Garden Miami Airport South", 160, "planned", market="Miami Airport"),
        _row("property_overview.pipeline_hotel_1_name", "The Setai Annex", doc=PDF, doc_id="p"),
        _row("property_overview.pipeline_hotel_1_rooms", 60, doc=PDF, doc_id="p"),
        _row("property_overview.pipeline_hotel_1_status", "Final Planning", doc=PDF, doc_id="p"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=2025, market_terms=["Miami Beach, FL", "Miami Beach"])
    assert sorted(h.name or "" for h in (s.pipeline_hotels or [])) == [
        "Grand Hyatt Miami Beach Convention Center",
        "Hilton Miami Beach",
        "The Setai Annex",
    ]
    assert s.under_construction_rooms == 147 + 289
    assert s.final_planning_rooms == 60
    assert s.planned_rooms is None
    f = s.pipeline_filter
    assert f is not None and f.total == 0 and f.other_market_rows == 1
    assert f.note == "1 listed project names another market and was left out."


def test_submarket_report_list_without_a_deal_market_is_listed_unchanged() -> None:
    """With no market term there is nothing to test a row against: the
    report's list stays the report's list (no row is excluded on a guess)."""
    rows = [
        *_hotel_rows("under_construction.1", PDF, "p", "Grand Hyatt Miami Beach Convention Center", 147, "under_construction", market="Miami, FL"),
    ]
    s = read_supply_growth(rows, has_documents=True, as_of_year=2025, market_terms=[])
    assert [h.name for h in (s.pipeline_hotels or [])] == ["Grand Hyatt Miami Beach Convention Center"]
    assert s.pipeline_filter is None
