"""FON-41 — deterministic USALI Summary-sheet reconciler.

The Angler's 2024 full-year detailed P&L extracted F&B revenue at 96,528.1
(the ``D_REST_CON`` restaurant-concession line on a hashed department
sheet) while the workbook's ``Summary`` sheet states 2,739,040.71. These
tests pin:

* the REAL workbook (skipped cleanly when the file is not on this machine):
  F&B revenue → 2,739,040.71 ± 1, Total Revenues → 13,481,730 ± 1, Rooms
  revenue → 9,496,407.22 ± 1, and a field the Summary does not state is
  untouched — on both the full-precision workbook path and the lossy
  parser-cache path;
* the 5% replacement rule and the annual-column detection on a synthetic
  grid (sum-of-months beats the labelled column; labelled column is the
  fallback);
* the gate: never invoked for OM / STR / market documents, never for a
  monthly statement, and the ``_apply_pnl_quality_passes`` hook honours it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")

from app.extraction.usali_summary_reconcile import (
    RECONCILED_CONFIDENCE,
    find_summary_page,
    is_pnl_family,
    parse_summary_grid,
    reconcile_extraction,
)

# ─────────────────────────── real workbook ───────────────────────────

_REAL_CANDIDATES = [
    Path(p)
    for p in (
        os.environ.get("FONDOK_ANGLERS_2024_PNL", ""),
        "/Users/prem/fondok/FL Miami South Beach Anglers (Eshan)/1 - Financials/"
        "Detailed Financials/Angler_s 2024 Full Year Detailed P&L.xlsm",
    )
    if p
]
_REAL_WORKBOOK = next((p for p in _REAL_CANDIDATES if p.is_file()), None)

requires_real_workbook = pytest.mark.skipif(
    _REAL_WORKBOOK is None,
    reason="Angler's 2024 Full Year Detailed P&L.xlsm not on this machine "
    "(set FONDOK_ANGLERS_2024_PNL to its path)",
)

# Values stated on the workbook's Summary sheet (row → annual TOTAL column).
_SUMMARY_FB_REVENUE = 2_739_040.71  # row 40  Food & Beverage (Revenues)
_SUMMARY_FB_EXPENSE = 2_290_364.07  # row 47  Food & Beverage (Departmental Expense)
_SUMMARY_ROOMS_REVENUE = 9_496_407.22  # row 39  Rooms
_SUMMARY_TOTAL_REVENUES = 13_481_730  # row 43  Total Revenues (13,481,730.29)
_SUMMARY_PAGE = 5  # 'Summary' is the 5th sheet of the workbook


def _live_fields() -> list[dict[str, Any]]:
    """The live defect's shape: outlet-level F&B lines plus a few others."""
    return [
        {
            "field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue",
            "value": 96528.1,
            "unit": "USD",
            "source_page": 3,
            "confidence": 0.9,
            "raw_text": "D_REST_CON 400000 … 36834.6 5681.2 7375.82 …",
        },
        {
            "field_name": "p_and_l_usali.departmental_expenses.food_beverage",
            "value": 55358.5,
            "unit": "USD",
            "source_page": 3,
            "confidence": 0.9,
            "raw_text": "D_REST_CON DIRECTEXP …",
        },
        {
            "field_name": "p_and_l_usali.operating_revenue.rooms_revenue",
            "value": 8_000_000.0,
            "unit": "USD",
            "source_page": 6,
            "confidence": 0.9,
        },
        {
            "field_name": "p_and_l_usali.operating_revenue.total_revenue",
            "value": 11_000_000.0,
            "unit": "USD",
            "source_page": 5,
            "confidence": 0.9,
        },
        # Not stated on the Summary — must come back byte-identical.
        {
            "field_name": "p_and_l_usali.operating_revenue.resort_fees",
            "value": 250_000.0,
            "unit": "USD",
            "source_page": 9,
            "confidence": 0.8,
        },
        {"field_name": "property_overview.keys", "value": 132, "source_page": 1, "confidence": 0.7},
        {"field_name": "p_and_l_usali.period_type", "value": "annual", "source_page": 5, "confidence": 0.9},
    ]


def _confidence(fields: list[dict[str, Any]]) -> dict[str, Any]:
    by_field = {f["field_name"]: float(f.get("confidence", 0)) for f in fields}
    return {
        "overall": sum(by_field.values()) / len(by_field),
        "by_field": by_field,
        "low_confidence_fields": [n for n, c in by_field.items() if c < 0.85],
        "requires_human_review": False,
    }


@pytest.fixture(scope="module")
def real_extraction_data() -> dict[str, Any]:
    """Parse the real workbook exactly as the upload path caches it."""
    import asyncio

    from app.extraction.parser import parse_document

    assert _REAL_WORKBOOK is not None
    body = _REAL_WORKBOOK.read_bytes()
    parsed = asyncio.run(parse_document(body, _REAL_WORKBOOK.name))
    return {
        "parser": parsed.parser,
        "total_pages": parsed.total_pages,
        "content_hash": parsed.content_hash,
        "bytes": body,
        "pages": [
            {"page_num": p.page_num, "text": p.text, "tables": p.tables, "metadata": p.metadata}
            for p in parsed.pages
        ],
    }


def _by_name(fields: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {f["field_name"]: f for f in fields}


@requires_real_workbook
def test_real_workbook_reconciles_fb_rooms_and_total_from_summary(real_extraction_data):
    """Workbook-bytes path: exact Summary values, full provenance."""
    fields = _live_fields()
    result = reconcile_extraction(
        fields,
        _confidence(fields),
        doc_type="PNL",
        extraction_data=real_extraction_data,
        file_bytes=real_extraction_data["bytes"],
    )
    assert result.skipped_reason is None
    assert result.table is not None
    assert result.table.source == "workbook"
    assert result.table.annual_column_rule == "sum_of_months"
    assert result.table.page_index == _SUMMARY_PAGE

    out = _by_name(result.fields)
    fb = out["p_and_l_usali.operating_revenue.food_beverage_revenue"]
    assert abs(fb["value"] - _SUMMARY_FB_REVENUE) <= 1, fb["value"]
    assert abs(out["p_and_l_usali.operating_revenue.total_revenue"]["value"] - _SUMMARY_TOTAL_REVENUES) <= 1
    assert abs(out["p_and_l_usali.operating_revenue.rooms_revenue"]["value"] - _SUMMARY_ROOMS_REVENUE) <= 1
    assert abs(out["p_and_l_usali.departmental_expenses.food_beverage"]["value"] - _SUMMARY_FB_EXPENSE) <= 1

    # Provenance on the replaced field.
    assert fb["confidence"] == RECONCILED_CONFIDENCE == 0.98
    assert fb["source_page"] == _SUMMARY_PAGE
    assert fb["reviewed"] == "reconciled"
    assert fb["raw_text"].startswith("Summary row 40: Food & Beverage")
    assert "2,739,040.71" in fb["raw_text"]
    rf = fb["reconciled_from"]
    assert rf["field_name"] == "p_and_l_usali.operating_revenue.food_beverage_revenue"
    assert rf["old_value"] == 96528.1
    assert rf["sheet"] == "Summary"
    assert rf["row"] == 40
    assert rf["old_source_page"] == 3
    assert "D_REST_CON" in rf["old_raw_text"]
    assert "96,528.10" in fb["note"]
    # The expense line was read from the Departmental Expense block, not Revenues.
    assert out["p_and_l_usali.departmental_expenses.food_beverage"]["reconciled_from"]["row"] == 47

    # Fields the Summary does not state are untouched — byte-identical.
    original = _by_name(_live_fields())
    for name in (
        "p_and_l_usali.operating_revenue.resort_fees",
        "property_overview.keys",
        "p_and_l_usali.period_type",
    ):
        assert out[name] == original[name]
        assert "reviewed" not in out[name]

    # Confidence report follows the fields.
    conf = result.confidence
    assert conf["by_field"]["p_and_l_usali.operating_revenue.food_beverage_revenue"] == 0.98
    assert "p_and_l_usali.operating_revenue.food_beverage_revenue" not in conf["low_confidence_fields"]
    assert conf["low_confidence_fields"] == [
        "p_and_l_usali.operating_revenue.resort_fees",
        "property_overview.keys",
    ]
    rec = conf["summary_reconciliation"]
    assert rec["sheet"] == "Summary" and rec["page"] == _SUMMARY_PAGE
    assert {c["field_name"] for c in rec["changes"]} == {
        "p_and_l_usali.operating_revenue.food_beverage_revenue",
        "p_and_l_usali.departmental_expenses.food_beverage",
        "p_and_l_usali.operating_revenue.rooms_revenue",
        "p_and_l_usali.operating_revenue.total_revenue",
    }


@requires_real_workbook
def test_real_workbook_cache_path_is_exact_to_six_significant_digits(real_extraction_data):
    """Without the bytes the parser cache (``%g`` floats) is still used."""
    fields = _live_fields()
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="T12", extraction_data=real_extraction_data
    )
    assert result.table is not None and result.table.source == "parser_cache"
    out = _by_name(result.fields)
    for name, expected in (
        ("p_and_l_usali.operating_revenue.food_beverage_revenue", _SUMMARY_FB_REVENUE),
        ("p_and_l_usali.operating_revenue.total_revenue", _SUMMARY_TOTAL_REVENUES),
        ("p_and_l_usali.operating_revenue.rooms_revenue", _SUMMARY_ROOMS_REVENUE),
    ):
        got = out[name]["value"]
        assert abs(got - expected) <= expected * 1e-5, (name, got)
        assert out[name]["reviewed"] == "reconciled"
    assert out["property_overview.keys"] == _by_name(_live_fields())["property_overview.keys"]


@requires_real_workbook
def test_real_workbook_summary_labels_cover_the_usali_lines(real_extraction_data):
    """Label map: every statement block of the Summary resolves to a concept."""
    page = find_summary_page(real_extraction_data["pages"])
    assert page is not None and page["metadata"]["sheet_name"] == "Summary"
    table = parse_summary_grid(
        page["tables"][0], sheet_name="Summary", page_index=page["page_num"]
    )
    assert table is not None
    assert set(table.rows) >= {
        "rooms_revenue", "fb_revenue", "other_revenue", "misc_revenue", "total_revenue",
        "rooms_dept_expense", "fb_dept_expense", "other_dept_expense", "dept_expenses",
        "dept_profit", "administrative_general", "information_telecom", "sales_marketing",
        "property_operations", "utilities", "undistributed_expenses", "gop", "mgmt_fee",
        "income_before_nonop", "property_taxes", "insurance", "ebitda", "ffe_reserve", "noi",
    }
    # First statement of a concept wins — the "Adjusted Values" / "Check"
    # restatements further down never shadow the P&L block.
    assert abs(table.rows["fb_revenue"].annual - _SUMMARY_FB_REVENUE) <= 0.01 * _SUMMARY_FB_REVENUE
    assert table.rows["ebitda"].annual > 2_000_000  # row 74, not the "Check" delta row


# ─────────────────────────── synthetic fixture ───────────────────────────

_MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _row(label: str, monthly: float, *, total: float | None = None, extra: list[str] | None = None) -> list[str]:
    months = [f"{monthly:g}"] * 12
    tot = monthly * 12 if total is None else total
    return [label, *months, f"{tot:g}", *(extra or [])]


def _summary_grid(*, total_override: dict[str, float] | None = None, trailing: list[str] | None = None) -> list[list[str]]:
    """A compact USALI Summary: month columns + TOTAL (+ optional trailing cols)."""
    ov = total_override or {}
    tr = trailing or []
    rows = [
        ["Anglers Boutique Resort", *[""] * (13 + len(tr))],
        ["", *_MONTHS, "TOTAL", *tr],
        ["Revenues", *[""] * (13 + len(tr))],
        _row("Rooms", 100.0, total=ov.get("Rooms rev"), extra=["0.5"] if tr else None),
        _row("Food & Beverage", 50.0, total=ov.get("F&B rev"), extra=["0.25"] if tr else None),
        _row("Other Operated Departments", 10.0, extra=["0.05"] if tr else None),
        _row("Total Revenues", 160.0, total=ov.get("Total"), extra=["1"] if tr else None),
        ["Departmental Expense", *[""] * (13 + len(tr))],
        _row("Rooms", 30.0, extra=["0.3"] if tr else None),
        _row("Food & Beverage", 20.0, extra=["0.4"] if tr else None),
        _row("Total Departmental Expenses", 50.0, extra=["0.31"] if tr else None),
        _row("Gross Operating Profit", 110.0, extra=["0.69"] if tr else None),
    ]
    return rows


def _page(grid: list[list[str]], *, sheet_name: str = "Summary", page_num: int = 2) -> dict[str, Any]:
    return {
        "page_num": page_num,
        "text": "\n".join("\t".join(r) for r in grid),
        "tables": [grid],
        "metadata": {"source": "xls", "sheet_name": sheet_name, "sheet_state": "visible"},
    }


def _extraction(*pages: dict[str, Any]) -> dict[str, Any]:
    return {"parser": "openpyxl", "total_pages": len(pages), "pages": list(pages)}


def _fields(**values: float) -> list[dict[str, Any]]:
    names = {
        "fb": "p_and_l_usali.operating_revenue.food_beverage_revenue",
        "rooms": "p_and_l_usali.operating_revenue.rooms_revenue",
        "total": "p_and_l_usali.operating_revenue.total_revenue",
        "fb_exp": "p_and_l_usali.departmental_expenses.food_beverage",
        "rooms_exp": "p_and_l_usali.departmental_expenses.rooms",
    }
    out = [
        {"field_name": names[k], "value": v, "unit": "USD", "source_page": 7, "confidence": 0.9}
        for k, v in values.items()
    ]
    out.append({"field_name": "p_and_l_usali.period_type", "value": "annual", "source_page": 2, "confidence": 0.9})
    return out


def test_five_percent_rule_replaces_only_material_disagreements():
    # Summary: F&B 600, Rooms 1200, Total 1920, F&B expense 240, Rooms expense 360.
    fields = _fields(fb=570.0, rooms=1250.0, total=1500.0, fb_exp=60.0, rooms_exp=360.0)
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(_summary_grid()))
    )
    out = _by_name(result.fields)
    # Exactly 5% off → within tolerance → untouched.
    assert out["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == 570.0
    # 4.2% off → untouched.
    assert out["p_and_l_usali.operating_revenue.rooms_revenue"]["value"] == 1250.0
    # Identical → untouched.
    assert out["p_and_l_usali.departmental_expenses.rooms"]["value"] == 360.0
    # 22% off → replaced with the Summary total.
    assert out["p_and_l_usali.operating_revenue.total_revenue"]["value"] == 1920.0
    # Section scoping: "Food & Beverage" under Departmental Expense is the
    # expense line (240), not the revenue line (600).
    assert out["p_and_l_usali.departmental_expenses.food_beverage"]["value"] == 240.0
    assert {c.field_name for c in result.changes} == {
        "p_and_l_usali.operating_revenue.total_revenue",
        "p_and_l_usali.departmental_expenses.food_beverage",
    }
    changed = out["p_and_l_usali.operating_revenue.total_revenue"]
    assert changed["reviewed"] == "reconciled"
    assert changed["source_page"] == 2
    assert changed["confidence"] == 0.98
    assert changed["reconciled_from"] == {
        "field_name": "p_and_l_usali.operating_revenue.total_revenue",
        "old_value": 1500.0,
        "sheet": "Summary",
        "row": 7,
        "label": "Total Revenues",
        "old_source_page": 7,
        "old_raw_text": None,
    }
    for name in ("p_and_l_usali.operating_revenue.food_beverage_revenue", "p_and_l_usali.operating_revenue.rooms_revenue"):
        assert "reviewed" not in out[name] and "reconciled_from" not in out[name]


def test_annual_column_is_the_one_that_sums_the_months_not_the_trailing_pct():
    grid = _summary_grid(trailing=["%"])
    fields = _fields(fb=100.0)
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="T12", extraction_data=_extraction(_page(grid))
    )
    assert result.table is not None
    assert result.table.annual_column_rule == "sum_of_months"
    assert _by_name(result.fields)["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == 600.0


def test_labelled_total_column_is_the_fallback_when_no_column_sums_the_months():
    # Every TOTAL is off by 3% from Σ months (a re-stated / adjusted total).
    grid = _summary_grid(total_override={"Rooms rev": 1236.0, "F&B rev": 618.0, "Total": 1978.0})
    for r in grid[8:]:
        if r[13]:
            r[13] = f"{float(r[13]) * 1.03:g}"
    fields = _fields(fb=100.0)
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(grid))
    )
    assert result.table is not None
    assert result.table.annual_column_rule == "labelled_total"
    assert _by_name(result.fields)["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == 618.0


def test_labelled_total_column_without_month_columns():
    grid = [
        ["", "FY2024"],
        ["Revenues", ""],
        ["Rooms", "1200"],
        ["Food & Beverage", "600"],
        ["Total Revenues", "1800"],
    ]
    fields = _fields(fb=100.0)
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(grid))
    )
    assert result.table is not None and result.table.annual_column_rule == "labelled_total"
    assert _by_name(result.fields)["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == 600.0


def test_no_annual_column_means_no_reconciliation():
    grid = [["Revenues", ""], ["Rooms", "abc"], ["Food & Beverage", ""]]
    fields = _fields(fb=100.0)
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(grid))
    )
    assert result.skipped_reason == "no_annual_column"
    assert result.fields == fields


@pytest.mark.parametrize("sheet_name", ["Summary", "SUMMARY", "P&L Summary", "Summary P&L", "USALI Summary"])
def test_summary_sheet_name_variants_are_found(sheet_name: str):
    pages = [_page([["x"]], sheet_name="D_REST_CON", page_num=1), _page(_summary_grid(), sheet_name=sheet_name, page_num=4)]
    page = find_summary_page(pages)
    assert page is not None and page["page_num"] == 4


def test_department_sheets_are_not_summary_sheets():
    pages = [_page(_summary_grid(), sheet_name=n, page_num=i) for i, n in enumerate(["Rooms", "D_REST_CON", "FB_Cons", "YohATNUe5UGPqdNsFUsWuw=="], start=1)]
    assert find_summary_page(pages) is None
    fields = _fields(fb=100.0)
    result = reconcile_extraction(fields, _confidence(fields), doc_type="PNL", extraction_data={"pages": pages})
    assert result.skipped_reason == "no_summary_sheet"
    assert result.fields == fields


def test_non_whole_dollar_fields_are_never_replaced():
    fields = _fields(fb=1.0)
    fields[0]["unit"] = "$000"
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(_summary_grid()))
    )
    assert result.changes == []
    assert _by_name(result.fields)["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == 1.0


def test_monthly_statement_is_never_reconciled():
    fields = _fields(fb=100.0)
    fields[-1]["value"] = "monthly"
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL_MONTHLY", extraction_data=_extraction(_page(_summary_grid()))
    )
    assert result.skipped_reason == "partial_period:MONTHLY"
    assert result.fields == fields


# ─────────────────────────── the gate ───────────────────────────


@pytest.mark.parametrize(
    ("doc_type", "expected"),
    [
        ("T12", True), ("t-12", True), ("PNL", True), ("pnl", True),
        ("PNL_MONTHLY", True), ("pnl-monthly", True), ("PNL_YTD", True),
        ("OM", False), ("STR", False), ("STR_TREND", False), ("MARKET_STUDY", False),
        ("CBRE_HORIZONS", False), ("PNL_BENCHMARK", False), ("ROOM_MIX", False),
        (None, False), ("", False),
    ],
)
def test_is_pnl_family(doc_type: str | None, expected: bool):
    assert is_pnl_family(doc_type) is expected


@pytest.mark.parametrize("doc_type", ["OM", "STR", "STR_TREND", "MARKET_STUDY", "CBRE_HORIZONS", None])
def test_reconciler_is_a_no_op_for_non_pnl_documents(doc_type: str | None):
    """A Summary-looking sheet inside an OM / STR deck must never rewrite fields."""
    fields = _fields(fb=100.0)
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type=doc_type, extraction_data=_extraction(_page(_summary_grid()))
    )
    assert result.skipped_reason == "not_pnl_family"
    assert result.changes == []
    assert result.fields == fields


@pytest.mark.parametrize("doc_type", ["OM", "STR", "STR_TREND", "MARKET_STUDY", None])
async def test_quality_passes_hook_never_invokes_the_reconciler_for_non_pnl(monkeypatch, doc_type):
    import app.extraction.usali_summary_reconcile as recon
    from app.api import documents as docs

    def _must_not_run(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("reconciler invoked for a non-P&L document")

    monkeypatch.setattr(recon, "reconcile_extraction", _must_not_run)
    fields = _fields(fb=100.0)
    conf = _confidence(fields)
    out_fields, out_conf = await docs._apply_pnl_quality_passes(
        None,
        deal_id="deal",
        doc_id="doc",
        tenant_id="tenant",
        doc_type=doc_type,
        fields=fields,
        confidence=conf,
        extraction_data=_extraction(_page(_summary_grid())),
        storage_key=None,
    )
    assert out_fields is fields and out_conf is conf


async def test_quality_passes_hook_reconciles_a_pnl_document():
    from app.api import documents as docs

    fields = _fields(fb=100.0)
    out_fields, out_conf = await docs._apply_pnl_quality_passes(
        None,  # no session → plausibility pass is skipped; reconciler still runs
        deal_id="deal",
        doc_id="doc",
        tenant_id="tenant",
        doc_type="PNL",
        fields=fields,
        confidence=_confidence(fields),
        extraction_data=_extraction(_page(_summary_grid())),
        storage_key=None,
    )
    fb = _by_name(out_fields)["p_and_l_usali.operating_revenue.food_beverage_revenue"]
    assert fb["value"] == 600.0 and fb["reviewed"] == "reconciled"
    assert out_conf["by_field"]["p_and_l_usali.operating_revenue.food_beverage_revenue"] == 0.98
    assert out_conf["summary_reconciliation"]["changes"][0]["old_value"] == 100.0


def test_pipeline_version_bumped_so_v2_rows_rerun():
    from app.api.documents import EXTRACTION_PIPELINE_VERSION

    assert EXTRACTION_PIPELINE_VERSION == "v3"


def test_extraction_field_out_carries_reconciliation_provenance():
    """The read path is ``extra="forbid"`` — the new keys must be modelled."""
    from app.api.documents import ExtractionFieldOut

    f = ExtractionFieldOut.model_validate(
        {
            "field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue",
            "value": 2739040.71,
            "unit": "USD",
            "source_page": 5,
            "confidence": 0.98,
            "raw_text": "Summary row 40: Food & Beverage …",
            "reviewed": "reconciled",
            "reconciled_from": {"field_name": "x", "old_value": 96528.1, "sheet": "Summary", "row": 40},
            "note": "Reconciled …",
        }
    )
    assert f.reviewed == "reconciled" and f.reconciled_from["row"] == 40 and f.note
