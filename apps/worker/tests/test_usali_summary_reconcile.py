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
  monthly statement, and the ``_apply_pnl_quality_passes`` hook honours it;
* the ADD rule (2026-10-08): a Summary-stated total the extraction has no
  field for at all (the stale-sibling-mapping shape — 435 fields, no F&B /
  total / rooms revenue) is added at the registry's canonical path with
  ``reviewed="reconciled"`` and ``reconciled_from={"added": true, …}``, on
  the real workbook and through the hook; totals already present within 5%
  are left exactly as extracted;
* the NAMESPACE GUARD (2026-10-08 live defect, pipeline v4): the registry
  matches on the LAST path segment, so department sub-rows
  (``dept_house_laundry.total_revenue`` = 0, ``dept_pm_con.total_dept_expense``
  = 691,361) were read as the hotel totals and overwritten with the Summary's
  13.48M / 5.06M. A hotel-level concept now matches only a statement-level
  path; a department concept only its own department's namespace; budget /
  prior-year / reference / monthly rows are never replaced nor counted as
  present; a zero in a department sub-row is a real zero.
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


# ─────────────────────────── real workbook: add-missing rule ───────────────────────────

_FB_PATH = "p_and_l_usali.operating_revenue.food_beverage_revenue"
_ROOMS_PATH = "p_and_l_usali.operating_revenue.rooms_revenue"
_TOTAL_PATH = "p_and_l_usali.operating_revenue.total_revenue"
_FB_EXP_PATH = "p_and_l_usali.departmental_expenses.food_beverage"

#: (canonical path, Summary annual value, Summary row, Summary label)
_OMITTED_TOTALS = (
    (_FB_PATH, 2_739_040.71, 40, "Food & Beverage"),
    (_TOTAL_PATH, 13_481_730.29, 43, "Total Revenues"),
    (_ROOMS_PATH, 9_496_407.22, 39, "Rooms"),
    (_FB_EXP_PATH, 2_290_364.07, 47, "Food & Beverage"),
)


def _fields_without_totals() -> list[dict[str, Any]]:
    """The 2026-10-08 live shape: a P&L extraction with NO USALI totals.

    The stale v1 sibling mapping reproduced a partial field set — 435 fields
    and not one of F&B revenue / total revenues / F&B department expense.
    """
    return [
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


@requires_real_workbook
def test_real_workbook_adds_the_totals_the_extraction_omitted(real_extraction_data):
    """Workbook-bytes path: the omitted totals are ADDED with the exact Summary values."""
    fields = _fields_without_totals()
    result = reconcile_extraction(
        fields,
        _confidence(fields),
        doc_type="PNL",
        extraction_data=real_extraction_data,
        file_bytes=real_extraction_data["bytes"],
    )
    assert result.skipped_reason is None
    assert result.changes == []  # nothing to replace — the fields did not exist
    assert result.additions and result.changed is True

    out = _by_name(result.fields)
    for path, expected, row, label in _OMITTED_TOTALS:
        f = out[path]
        assert abs(f["value"] - expected) <= 1, (path, f["value"])
        assert f["confidence"] == RECONCILED_CONFIDENCE == 0.98
        assert f["source_page"] == _SUMMARY_PAGE
        assert f["reviewed"] == "reconciled"
        assert f["unit"] == "USD"
        assert f["raw_text"].startswith(f"Summary row {row}: {label}")
        assert f["reconciled_from"] == {"added": True, "sheet": "Summary", "row": row, "label": label}
        assert f"'{label}' annual total (row {row})" in f["note"]
    # The F&B expense came from the Departmental Expense block, not Revenues.
    assert out[_FB_EXP_PATH]["reconciled_from"]["row"] == 47

    # The original fields come back byte-identical, in their original order.
    assert result.fields[: len(fields)] == _fields_without_totals()
    # Every added field is accounted for in ``additions`` and the confidence report.
    added_names = [a.field_name for a in result.additions]
    assert [f["field_name"] for f in result.fields[len(fields):]] == added_names
    assert {_FB_PATH, _TOTAL_PATH, _ROOMS_PATH, _FB_EXP_PATH} <= set(added_names)
    conf = result.confidence
    assert conf["by_field"][_FB_PATH] == 0.98
    assert conf["low_confidence_fields"] == [
        "p_and_l_usali.operating_revenue.resort_fees",
        "property_overview.keys",
    ]
    rec = conf["summary_reconciliation"]
    assert rec["changes"] == []
    assert {a["field_name"] for a in rec["added"]} == set(added_names)
    fb_added = next(a for a in rec["added"] if a["field_name"] == _FB_PATH)
    assert fb_added["concept"] == "fb_revenue" and fb_added["row"] == 40
    assert abs(fb_added["value"] - _SUMMARY_FB_REVENUE) <= 1


@requires_real_workbook
def test_real_workbook_adds_the_omitted_totals_on_the_parser_cache_path(real_extraction_data):
    """Without the bytes the additions come from the ``%g`` grid — six significant digits."""
    fields = _fields_without_totals()
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="T12", extraction_data=real_extraction_data
    )
    assert result.table is not None and result.table.source == "parser_cache"
    out = _by_name(result.fields)
    for path, expected, _row, _label in _OMITTED_TOTALS:
        assert abs(out[path]["value"] - expected) <= expected * 1e-5, (path, out[path]["value"])
        assert out[path]["reviewed"] == "reconciled"
        assert out[path]["reconciled_from"]["added"] is True


@requires_real_workbook
def test_real_workbook_present_totals_within_tolerance_are_left_alone(real_extraction_data):
    """A field list that already carries the totals within 5% is unchanged:
    nothing replaced, and none of those concepts is added a second time."""
    present = [
        {"field_name": _FB_PATH, "value": 2_700_000.0, "unit": "USD", "source_page": 5, "confidence": 0.9},  # 1.4% off
        {"field_name": _TOTAL_PATH, "value": 13_400_000.0, "unit": "USD", "source_page": 5, "confidence": 0.9},  # 0.6% off
        {"field_name": _ROOMS_PATH, "value": 9_496_410.0, "unit": "USD", "source_page": 6, "confidence": 0.5},  # live sibling value
        {"field_name": _FB_EXP_PATH, "value": 2_290_364.07, "unit": "USD", "source_page": 5, "confidence": 0.9},  # exact
        {"field_name": "p_and_l_usali.period_type", "value": "annual", "source_page": 5, "confidence": 0.9},
    ]
    fields = [dict(f) for f in present]
    result = reconcile_extraction(
        fields,
        _confidence(fields),
        doc_type="PNL",
        extraction_data=real_extraction_data,
        file_bytes=real_extraction_data["bytes"],
    )
    assert result.changes == []
    out = _by_name(result.fields)
    for f in present:
        assert out[f["field_name"]] == f
        assert "reviewed" not in out[f["field_name"]]
    added = {a.field_name for a in result.additions}
    assert not added & {_FB_PATH, _TOTAL_PATH, _ROOMS_PATH, _FB_EXP_PATH}
    assert [f["field_name"] for f in result.fields].count(_FB_PATH) == 1
    # The Summary's OTHER statement lines (no field for them) are still added.
    assert "p_and_l_usali.gross_operating_profit" in added


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


# ─────────────────────────── the add-missing rule (synthetic) ───────────────────────────

#: Every statement line of ``_summary_grid()`` → (canonical path, annual value, grid row).
_SYNTHETIC_SUMMARY_LINES = [
    ("p_and_l_usali.operating_revenue.rooms_revenue", 1200.0, 4),
    ("p_and_l_usali.operating_revenue.food_beverage_revenue", 600.0, 5),
    ("p_and_l_usali.operating_revenue.other_revenue", 120.0, 6),
    ("p_and_l_usali.operating_revenue.total_revenue", 1920.0, 7),
    ("p_and_l_usali.departmental_expenses.rooms", 360.0, 9),
    ("p_and_l_usali.departmental_expenses.food_beverage", 240.0, 10),
    ("p_and_l_usali.departmental_expenses.total", 600.0, 11),
    ("p_and_l_usali.gross_operating_profit", 1320.0, 12),
]


def _fields_with_no_usali_totals() -> list[dict[str, Any]]:
    return [
        {"field_name": "property_overview.name", "value": "Anglers Boutique Resort", "source_page": 1, "confidence": 0.95},
        {"field_name": "ttm_summary_per_om.occupancy_pct", "value": 0.83, "source_page": 2, "confidence": 0.9},
        {"field_name": "p_and_l_usali.period_type", "value": "annual", "source_page": 2, "confidence": 0.9},
    ]


def test_missing_totals_are_added_at_the_registry_canonical_paths_in_sheet_order():
    fields = _fields_with_no_usali_totals()
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="T12", extraction_data=_extraction(_page(_summary_grid()))
    )
    assert result.changes == []
    assert [(a.field_name, a.value, a.row) for a in result.additions] == _SYNTHETIC_SUMMARY_LINES
    # Originals first and untouched; additions appended.
    assert result.fields[:3] == _fields_with_no_usali_totals()
    assert [f["field_name"] for f in result.fields[3:]] == [p for p, _v, _r in _SYNTHETIC_SUMMARY_LINES]

    fb = _by_name(result.fields)["p_and_l_usali.operating_revenue.food_beverage_revenue"]
    assert fb == {
        "field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue",
        "value": 600.0,
        "unit": "USD",
        "source_page": 2,
        "confidence": 0.98,
        "raw_text": fb["raw_text"],
        "reviewed": "reconciled",
        "reconciled_from": {"added": True, "sheet": "Summary", "row": 5, "label": "Food & Beverage"},
        "note": fb["note"],
    }
    assert fb["raw_text"].startswith("Summary row 5: Food & Beverage")
    assert "600.00 (annual total)" in fb["raw_text"]
    assert fb["note"].startswith("Added from the Summary sheet's 'Food & Beverage' annual total (row 5): 600.00")

    conf = result.confidence
    assert conf["by_field"]["p_and_l_usali.operating_revenue.food_beverage_revenue"] == 0.98
    assert conf["summary_reconciliation"]["changes"] == []
    assert conf["summary_reconciliation"]["added"][1] == {
        "field_name": "p_and_l_usali.operating_revenue.food_beverage_revenue",
        "concept": "fb_revenue",
        "value": 600.0,
        "row": 5,
        "label": "Food & Beverage",
    }


def test_a_present_concept_is_never_added_twice_whatever_its_unit_or_spelling():
    """Present = ANY extracted field resolves to the concept: a ``$000`` F&B
    line (not replaceable) and an alias-spelled rooms line both block the add."""
    fields = _fields(fb=1.0)
    fields[0]["unit"] = "$000"
    fields.append(
        {"field_name": "p_and_l_usali.revenues.rooms_usd", "value": 1150.0, "unit": "USD", "source_page": 7, "confidence": 0.9}
    )
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(_summary_grid()))
    )
    assert result.changes == []
    names = [f["field_name"] for f in result.fields]
    assert names.count("p_and_l_usali.operating_revenue.food_beverage_revenue") == 1
    assert "p_and_l_usali.operating_revenue.rooms_revenue" not in names
    assert "p_and_l_usali.revenues.rooms_usd" in names
    assert {a.concept for a in result.additions} == {
        "other_revenue", "total_revenue", "rooms_dept_expense", "fb_dept_expense", "dept_expenses", "gop",
    }


def test_replace_and_add_rules_compose_in_one_pass():
    """Present-but-wrong F&B is REPLACED; the absent total is ADDED."""
    fields = _fields(fb=100.0, rooms=1200.0)
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(_summary_grid()))
    )
    assert [c.field_name for c in result.changes] == ["p_and_l_usali.operating_revenue.food_beverage_revenue"]
    out = _by_name(result.fields)
    assert out["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == 600.0
    assert out["p_and_l_usali.operating_revenue.food_beverage_revenue"]["reconciled_from"]["old_value"] == 100.0
    assert out["p_and_l_usali.operating_revenue.rooms_revenue"]["value"] == 1200.0  # within 5% → untouched
    assert "reviewed" not in out["p_and_l_usali.operating_revenue.rooms_revenue"]
    total = out["p_and_l_usali.operating_revenue.total_revenue"]
    assert total["value"] == 1920.0 and total["reconciled_from"]["added"] is True
    rec = result.confidence["summary_reconciliation"]
    assert len(rec["changes"]) == 1 and {a["concept"] for a in rec["added"]} >= {"total_revenue", "gop"}


@pytest.mark.parametrize("doc_type", ["PNL", "T12", "PNL_YTD", "PNL_MONTHLY"])
def test_every_reconcilable_concept_has_a_canonical_path_that_resolves_back(doc_type: str):
    from app.extraction.usali_summary_reconcile import (
        RECONCILABLE_CONCEPTS,
        _field_concept,
        canonical_path_for_concept,
    )

    for concept in sorted(RECONCILABLE_CONCEPTS):
        path = canonical_path_for_concept(concept, doc_type)
        assert path and path.startswith("p_and_l_usali."), concept
        assert _field_concept(path, doc_type) == concept, (concept, path)
    assert canonical_path_for_concept("fb_revenue", doc_type) == _FB_PATH
    assert canonical_path_for_concept("rooms_revenue", doc_type) == _ROOMS_PATH
    assert canonical_path_for_concept("total_revenue", doc_type) == _TOTAL_PATH
    assert canonical_path_for_concept("fb_dept_expense", doc_type) == _FB_EXP_PATH
    assert canonical_path_for_concept("not_a_concept", doc_type) is None


# ─────────────────────────── namespace guard (2026-10-08 live defect) ───────────────────────────
#
# Forced re-extraction of the Angler's workbook under pipeline v3. The
# registry resolves a path by its LAST segment, so the department sheets'
# own sub-totals were read as the hotel's and overwritten with the Summary:
#
#   p_and_l_usali.dept_house_laundry.total_revenue      0 → 13,481,730.29
#   p_and_l_usali.dept_staff_dining.total_revenue       0 → 13,481,730.29
#   p_and_l_usali.dept_pm_con.total_dept_expense  691,361 →  5,064,971.75
#
# Those were correct department figures. The five replacements and four
# additions the same run made were right and must keep happening.

_LIVE_ROOMS = 9_496_407.22
_LIVE_FB_REVENUE = 2_739_040.71
_LIVE_MISC = 1_228_475.94
_LIVE_TOTAL_REVENUE = 13_481_730.29
_LIVE_FB_EXPENSE = 2_290_364.07
_LIVE_TOTAL_DEPT_EXPENSE = 5_064_971.75
_LIVE_AG = 1_264_087.54
_LIVE_IT = 177_127.0
_LIVE_SM = 1_169_427.16
_LIVE_POM = 637_067.46


def _live_summary_grid() -> list[list[str]]:
    """The Angler's Summary totals as a single labelled-total column.

    Only the concepts the live field list occupies plus the four additions
    are stated, so the expected ``additions`` are exactly those four.
    """
    return [
        ["", "FY2024"],
        ["Revenues", ""],
        ["Rooms", f"{_LIVE_ROOMS:.2f}"],  # row 3
        ["Food & Beverage", f"{_LIVE_FB_REVENUE:.2f}"],  # row 4
        ["Miscellaneous Income", f"{_LIVE_MISC:.2f}"],  # row 5
        ["Total Revenues", f"{_LIVE_TOTAL_REVENUE:.2f}"],  # row 6
        ["Departmental Expense", ""],
        ["Food & Beverage", f"{_LIVE_FB_EXPENSE:.2f}"],  # row 8
        ["Total Departmental Expenses", f"{_LIVE_TOTAL_DEPT_EXPENSE:.2f}"],  # row 9
        ["Undistributed Expenses", ""],
        ["Administrative & General", f"{_LIVE_AG:.2f}"],  # row 11
        ["Information & Telecom Systems", f"{_LIVE_IT:.2f}"],  # row 12
        ["Sales & Marketing", f"{_LIVE_SM:.2f}"],  # row 13
        ["Property Operation & Maintenance", f"{_LIVE_POM:.2f}"],  # row 14
    ]


def _usd(name: str, value: float, *, page: int = 3) -> dict[str, Any]:
    return {"field_name": name, "value": value, "unit": "USD", "source_page": page, "confidence": 0.9}


def _live_defect_fields() -> list[dict[str, Any]]:
    """The 2026-10-08 production field list, by path and value."""
    return [
        # Statement-level totals the extractor got wrong → replaced.
        _usd("p_and_l_usali.total_revenue_usd", 10_839_200.0),
        _usd("p_and_l_usali.total_departmental_expense_usd", 2_829_970.0),
        _usd("p_and_l_usali.fb.revenue_usd", 96_528.1),
        _usd("p_and_l_usali.fb.departmental_expense_usd", 55_358.5),
        _usd("p_and_l_usali.undistributed.ag_expense_usd", 1_120_710.0),
        # Statement-level total within 5% → untouched (and not re-added).
        _usd("p_and_l_usali.rooms.revenue_usd", _LIVE_ROOMS, page=6),
        # Department sub-rows: real zeros and a real department figure.
        _usd("p_and_l_usali.dept_house_laundry.total_revenue", 0.0, page=11),
        _usd("p_and_l_usali.dept_staff_dining.total_revenue", 0.0, page=12),
        _usd("p_and_l_usali.dept_pm_con.total_dept_expense", 691_361.0, page=13),
        # Budget / prior-year / reference rows (left alone on the live run — pinned).
        _usd("p_and_l_usali.total_revenue.budget_usd", 12_000_000.0, page=5),
        _usd("p_and_l_usali.total_revenue.prior_year_2023_usd", 12_480_000.0, page=5),
        _usd("p_and_l_usali.admin_and_general.total_revenue_reference_2024", _LIVE_TOTAL_REVENUE, page=7),
        {"field_name": "p_and_l_usali.period_type", "value": "annual", "source_page": 5, "confidence": 0.9},
    ]


_LIVE_DEPT_SUBROWS = (
    "p_and_l_usali.dept_house_laundry.total_revenue",
    "p_and_l_usali.dept_staff_dining.total_revenue",
    "p_and_l_usali.dept_pm_con.total_dept_expense",
)
_LIVE_REFERENCE_ROWS = (
    "p_and_l_usali.total_revenue.budget_usd",
    "p_and_l_usali.total_revenue.prior_year_2023_usd",
    "p_and_l_usali.admin_and_general.total_revenue_reference_2024",
)
#: path → (extracted value, Summary value)
_LIVE_REPLACEMENTS: dict[str, tuple[float, float]] = {
    "p_and_l_usali.fb.revenue_usd": (96_528.1, _LIVE_FB_REVENUE),
    "p_and_l_usali.fb.departmental_expense_usd": (55_358.5, _LIVE_FB_EXPENSE),
    "p_and_l_usali.total_revenue_usd": (10_839_200.0, _LIVE_TOTAL_REVENUE),
    "p_and_l_usali.total_departmental_expense_usd": (2_829_970.0, _LIVE_TOTAL_DEPT_EXPENSE),
    "p_and_l_usali.undistributed.ag_expense_usd": (1_120_710.0, _LIVE_AG),
}
#: canonical path → Summary value, in sheet order
_LIVE_ADDITIONS: dict[str, float] = {
    "p_and_l_usali.operating_revenue.misc_revenue": _LIVE_MISC,
    "p_and_l_usali.undistributed.information_telecom": _LIVE_IT,
    "p_and_l_usali.undistributed.sales_marketing": _LIVE_SM,
    "p_and_l_usali.undistributed.property_operations": _LIVE_POM,
}
#: Concepts the live list already occupies — none may be added a second time.
_LIVE_PRESENT_CONCEPTS = {
    "total_revenue", "dept_expenses", "fb_revenue", "fb_dept_expense", "administrative_general", "rooms_revenue",
}


def _assert_live_shape(result: Any, *, tolerance: float) -> None:
    original = _by_name(_live_defect_fields())
    out = _by_name(result.fields)
    names = [f["field_name"] for f in result.fields]
    assert len(names) == len(set(names)), "no field may be emitted twice"

    # 1. Department sub-rows: a zero there is a real zero — byte-identical.
    for name in _LIVE_DEPT_SUBROWS:
        assert out[name] == original[name], (name, out[name])
        assert "reviewed" not in out[name] and "reconciled_from" not in out[name]
    # 2. Reference rows: never replaced.
    for name in _LIVE_REFERENCE_ROWS:
        assert out[name] == original[name], (name, out[name])
    # 3. A present-and-correct statement total is untouched.
    assert out["p_and_l_usali.rooms.revenue_usd"] == original["p_and_l_usali.rooms.revenue_usd"]
    assert out["p_and_l_usali.period_type"] == original["p_and_l_usali.period_type"]

    # 4. The five intended replacements.
    for name, (old, new) in _LIVE_REPLACEMENTS.items():
        f = out[name]
        assert abs(f["value"] - new) <= tolerance, (name, f["value"])
        assert f["reviewed"] == "reconciled" and f["confidence"] == RECONCILED_CONFIDENCE
        assert f["reconciled_from"]["field_name"] == name
        assert f["reconciled_from"]["old_value"] == old
    assert {c.field_name for c in result.changes} == set(_LIVE_REPLACEMENTS)

    # 5. The four intended additions, at the registry's canonical paths.
    for name, value in _LIVE_ADDITIONS.items():
        f = out[name]
        assert abs(f["value"] - value) <= tolerance, (name, f["value"])
        assert f["reviewed"] == "reconciled" and f["reconciled_from"]["added"] is True
    added_concepts = {a.concept for a in result.additions}
    assert set(_LIVE_ADDITIONS) <= {a.field_name for a in result.additions}
    assert not added_concepts & _LIVE_PRESENT_CONCEPTS, added_concepts

    rec = result.confidence["summary_reconciliation"]
    assert {c["field_name"] for c in rec["changes"]} == set(_LIVE_REPLACEMENTS)
    assert set(_LIVE_ADDITIONS) <= {a["field_name"] for a in rec["added"]}


def test_live_shape_department_subrows_are_out_of_scope_while_totals_still_reconcile():
    """Synthetic Summary with the Angler's totals; the live field list."""
    fields = _live_defect_fields()
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(_live_summary_grid()))
    )
    assert result.skipped_reason is None
    assert result.table is not None and result.table.annual_column_rule == "labelled_total"
    _assert_live_shape(result, tolerance=0.005)
    # Exactly the four additions, in sheet order; nothing else grew the list.
    assert [(a.field_name, a.value) for a in result.additions] == list(_LIVE_ADDITIONS.items())
    assert [a.row for a in result.additions] == [5, 12, 13, 14]
    assert len(result.fields) == len(fields) + len(_LIVE_ADDITIONS)
    # Original order preserved, additions appended.
    assert [f["field_name"] for f in result.fields[: len(fields)]] == [f["field_name"] for f in fields]


@requires_real_workbook
def test_real_workbook_live_shape_department_subrows_are_out_of_scope(real_extraction_data):
    """The same field list against the real Summary sheet (workbook bytes)."""
    fields = _live_defect_fields()
    result = reconcile_extraction(
        fields,
        _confidence(fields),
        doc_type="PNL",
        extraction_data=real_extraction_data,
        file_bytes=real_extraction_data["bytes"],
    )
    assert result.skipped_reason is None
    assert result.table is not None and result.table.source == "workbook"
    _assert_live_shape(result, tolerance=1.0)
    # The real Summary states more lines (GOP, NOI, …) — those are added too,
    # but never for a concept a department sub-row was wrongly standing in for.
    assert {"gop", "noi"} <= {a.concept for a in result.additions}


async def test_quality_passes_hook_live_shape(monkeypatch):
    """Through ``_apply_pnl_quality_passes`` (no session → reconciler only)."""
    from app.api import documents as docs

    fields = _live_defect_fields()
    out_fields, out_conf = await docs._apply_pnl_quality_passes(
        None,
        deal_id="deal",
        doc_id="doc",
        tenant_id="tenant",
        doc_type="PNL",
        fields=fields,
        confidence=_confidence(fields),
        extraction_data=_extraction(_page(_live_summary_grid())),
        storage_key=None,
    )
    out = _by_name(out_fields)
    original = _by_name(_live_defect_fields())
    for name in _LIVE_DEPT_SUBROWS + _LIVE_REFERENCE_ROWS:
        assert out[name] == original[name], name
    assert out["p_and_l_usali.total_revenue_usd"]["value"] == _LIVE_TOTAL_REVENUE
    assert out["p_and_l_usali.dept_pm_con.total_dept_expense"]["value"] == 691_361.0
    assert out["p_and_l_usali.operating_revenue.misc_revenue"]["value"] == _LIVE_MISC
    assert {c["field_name"] for c in out_conf["summary_reconciliation"]["changes"]} == set(_LIVE_REPLACEMENTS)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Statement level: directly under the root, or under a statement namespace.
        ("p_and_l_usali.total_revenue_usd", "total_revenue"),
        ("p_and_l_usali.operating_revenue.total_revenue", "total_revenue"),
        ("p_and_l_usali.summary.total_revenue_usd", "total_revenue"),
        ("p_and_l_usali.totals.gop_usd", "gop"),
        ("p_and_l_usali.total_departmental_expense_usd", "dept_expenses"),
        ("p_and_l_usali.departmental_expenses.total", "dept_expenses"),
        ("p_and_l_usali.undistributed.total", "undistributed_expenses"),
        ("p_and_l_usali.undistributed.ag_expense_usd", "administrative_general"),
        ("p_and_l_usali.fixed_charges.property_taxes", "property_taxes"),
        ("total_revenue_usd", "total_revenue"),
        # A concept's OWN block is statement level for that concept.
        ("p_and_l_usali.gop.total_usd", "gop"),
        ("p_and_l_usali.gross_operating_profit.total", "gop"),
        ("p_and_l_usali.management_fees.total_usd", "mgmt_fee"),
        ("p_and_l_usali.net_operating_income.noi_usd", "noi"),
        # A department concept under its own department's namespace.
        ("p_and_l_usali.fb.revenue_usd", "fb_revenue"),
        ("p_and_l_usali.food_and_beverage.revenue_usd", "fb_revenue"),
        ("p_and_l_usali.fb.departmental_expense_usd", "fb_dept_expense"),
        ("p_and_l_usali.rooms.revenue_usd", "rooms_revenue"),
        ("p_and_l_usali.operating_revenue.food_beverage_revenue", "fb_revenue"),
        ("p_and_l_usali.departmental_expenses.food_beverage", "fb_dept_expense"),
        # Hotel-level concept under a department / cost-centre namespace → out of scope.
        ("p_and_l_usali.dept_house_laundry.total_revenue", None),
        ("p_and_l_usali.dept_staff_dining.total_revenue", None),
        ("p_and_l_usali.dept_pm_con.total_dept_expense", None),
        ("p_and_l_usali.rooms.total_revenue", None),
        ("p_and_l_usali.fb.total_revenue", None),
        ("p_and_l_usali.food_beverage.total_revenue", None),
        ("p_and_l_usali.hc_spa.total_revenue", None),
        ("p_and_l_usali.reservations.total_revenue", None),
        ("p_and_l_usali.minor_operated_departments.total_revenue", None),
        ("p_and_l_usali.fb_detail.total_revenue", None),
        ("p_and_l_usali.fb_retail.total_revenue", None),
        ("p_and_l_usali.payroll_related.total_dept_expense", None),
        ("p_and_l_usali.admin_and_general.total_revenue", None),
        ("p_and_l_usali.sales_and_marketing.total_revenue", None),
        ("p_and_l_usali.information_telecom.total_revenue", None),
        ("p_and_l_usali.property_operations_maintenance.total_revenue", None),
        ("p_and_l_usali.d_rest_con.total_revenue", None),
        # An unknown block is never the statement.
        ("kpis.total_revenue", None),
        ("p_and_l_usali.ratios.gop_usd", None),
        # A department concept under a DIFFERENT department's namespace.
        ("p_and_l_usali.rooms.fb_revenue", None),
        ("p_and_l_usali.fb.rooms_revenue", None),
        ("p_and_l_usali.hc_spa.fb_revenue", None),
        # Reference / budget / prior-year / monthly / pct rows.
        ("p_and_l_usali.total_revenue.budget_usd", None),
        ("p_and_l_usali.budget.total_revenue_usd", None),
        ("p_and_l_usali.total_revenue.prior_year_2023_usd", None),
        ("p_and_l_usali.admin_and_general.total_revenue_reference_2024", None),
        ("p_and_l_usali.operating_revenue.total_revenue_reference", None),
        ("p_and_l_usali.pct_calculations.total_revenue", None),
        ("p_and_l_usali.forecast.total_revenue_usd", None),
        ("p_and_l_usali.total_revenue.variance_usd", None),
        ("p_and_l_usali.monthly.jan_2024.total_revenue", None),
        ("p_and_l_usali.operating_revenue.total_revenue_dec", None),
        ("p_and_l_usali.2023.total_revenue_usd", None),
        ("p_and_l_usali.fy2024.gop_usd", None),
    ],
)
def test_namespace_guard_resolves_statement_level_paths_only(path: str, expected: str | None):
    from app.extraction.usali_summary_reconcile import _field_concept

    assert _field_concept(path, "PNL") == expected


def test_namespace_guard_rule_components():
    from app.extraction.usali_summary_reconcile import (
        STATEMENT_NAMESPACES,
        department_namespaces,
        is_department_namespace,
        is_reference_path,
        namespace_permits,
    )

    # Derived from the registry's department / cost-centre line concepts …
    derived = department_namespaces()
    assert {
        "rooms", "fb", "food_and_beverage", "other_operated_departments", "miscellaneous_income",
        "administrative_and_general", "information_and_telecom", "sales_and_marketing",
        "property_operations_and_maintenance", "utilities",
        # … including the concepts' own bare names used as a block name …
        "rooms_revenue", "fb_revenue", "food_beverage", "sales_marketing",
    } <= derived
    # … never a statement namespace.
    assert not derived & STATEMENT_NAMESPACES
    assert "p_and_l_usali" not in derived
    # The supplement + the ``dept_`` / ``d_`` sheet prefixes.
    for seg in (
        "dept_house_laundry", "dept_pm_con", "d_rest_con", "hc_spa", "reservations", "payroll_related",
        "fb_detail", "fb_retail", "minor_operated_departments", "admin_and_general", "information_telecom",
        "property_operations_maintenance",
    ):
        assert is_department_namespace(seg), seg
    for seg in ("operating_revenue", "undistributed", "summary", "totals", "fixed_charges", "departmental_expenses", "gop"):
        assert not is_department_namespace(seg), seg

    # Statement-level spellings the requirement names are permitted for the hotel total.
    assert namespace_permits("p_and_l_usali.total_revenue_usd", "total_revenue")
    assert namespace_permits("p_and_l_usali.total_revenue.annual_usd", "total_revenue")
    assert namespace_permits("p_and_l_usali.summary.total_revenue_usd", "total_revenue")
    # The generic sub-row leaves under a department path are denied outright.
    for leaf in ("total_revenue", "total_dept_expense", "total_expense", "total", "total_usd"):
        assert not namespace_permits(f"p_and_l_usali.dept_house_laundry.{leaf}", "total_revenue"), leaf
        assert not namespace_permits(f"p_and_l_usali.fb.{leaf}", "total_revenue"), leaf
    # A department concept: own namespace yes, another department's no.
    assert namespace_permits("p_and_l_usali.fb.revenue_usd", "fb_revenue")
    assert not namespace_permits("p_and_l_usali.rooms.fb_revenue", "fb_revenue")

    for p in (
        "p_and_l_usali.total_revenue.budget_usd", "p_and_l_usali.total_revenue.prior_year_2023_usd",
        "p_and_l_usali.admin_and_general.total_revenue_reference_2024", "p_and_l_usali.pct_calculations.total_revenue",
        "p_and_l_usali.monthly.jan_2024.total_revenue", "p_and_l_usali.forecast.total_revenue_usd",
        "p_and_l_usali.total_revenue.variance_usd", "p_and_l_usali.operating_revenue.rooms_revenue_sep",
    ):
        assert is_reference_path(p), p
    for p in (
        "p_and_l_usali.total_revenue_usd", "p_and_l_usali.operating_revenue.food_beverage_revenue",
        "p_and_l_usali.undistributed.sales_marketing", "p_and_l_usali.ffe_reserve.proforma_calculation_usd",
        "p_and_l_usali.net_operating_income.noi_before_reserve_usd",
    ):
        assert not is_reference_path(p), p


@pytest.mark.parametrize(
    "path",
    [
        "p_and_l_usali.total_revenue.budget_usd",
        "p_and_l_usali.total_revenue.prior_year_2023_usd",
        "p_and_l_usali.admin_and_general.total_revenue_reference_2024",
        "p_and_l_usali.operating_revenue.total_revenue_reference",
        "p_and_l_usali.pct_calculations.total_revenue",
        "p_and_l_usali.monthly.jan_2024.total_revenue",
        "p_and_l_usali.operating_revenue.total_revenue_dec",
        "p_and_l_usali.forecast.total_revenue_usd",
        "p_and_l_usali.total_revenue.variance_usd",
        "p_and_l_usali.2023.total_revenue_usd",
        # And a department sub-row standing in for the hotel total.
        "p_and_l_usali.dept_house_laundry.total_revenue",
    ],
)
def test_reference_and_department_rows_are_neither_replaced_nor_present(path: str):
    """Excluded from replacement AND from presence: the only ``total_revenue``
    field is a reference / sub-row, so the actual total is ADDED at the
    canonical path and the reference row comes back byte-identical."""
    fields = [
        _usd(path, 1.0, page=7),
        {"field_name": "p_and_l_usali.period_type", "value": "annual", "source_page": 2, "confidence": 0.9},
    ]
    result = reconcile_extraction(
        fields, _confidence(fields), doc_type="PNL", extraction_data=_extraction(_page(_summary_grid()))
    )
    assert result.changes == []
    assert result.fields[0] == fields[0]
    added = {a.field_name: a.value for a in result.additions}
    assert added["p_and_l_usali.operating_revenue.total_revenue"] == 1920.0


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


@pytest.mark.parametrize("doc_type", ["T12", "PNL", "pnl-ytd"])
async def test_quality_passes_hook_adds_the_totals_a_partial_extraction_lacks(doc_type: str):
    """Hook coverage (2026-10-08): a P&L-family document whose extraction —
    whatever path produced it (LLM, template, or a stale sibling mapping) —
    carries none of the USALI totals, with a Summary sheet in the parsed
    workbook → after ``_apply_pnl_quality_passes`` the totals exist with
    ``reviewed = "reconciled"`` and the confidence report follows."""
    from app.api import documents as docs

    fields = _fields_with_no_usali_totals()
    pages = _extraction(
        _page([["D_REST_CON", "400000", "36834.6"]], sheet_name="D_REST_CON", page_num=1),
        _page(_summary_grid(), page_num=4),
    )
    out_fields, out_conf = await docs._apply_pnl_quality_passes(
        None,  # no session → plausibility critic is skipped; reconciler still runs
        deal_id="deal",
        doc_id="doc",
        tenant_id="tenant",
        doc_type=doc_type,
        fields=fields,
        confidence=_confidence(fields),
        extraction_data=pages,
        storage_key=None,
    )
    assert out_fields is not fields  # the hook returned the reconciled list
    out = _by_name(out_fields)
    for path, value, row in _SYNTHETIC_SUMMARY_LINES:
        f = out[path]
        assert f["value"] == value, path
        assert f["reviewed"] == "reconciled"
        assert f["source_page"] == 4
        assert f["confidence"] == 0.98
        assert f["reconciled_from"] == {"added": True, "sheet": "Summary", "row": row, "label": f["reconciled_from"]["label"]}
        assert out_conf["by_field"][path] == 0.98
    # The partial extraction's own fields are untouched.
    for f in _fields_with_no_usali_totals():
        assert out[f["field_name"]] == f
    rec = out_conf["summary_reconciliation"]
    assert rec["changes"] == [] and rec["page"] == 4
    assert [a["field_name"] for a in rec["added"]] == [p for p, _v, _r in _SYNTHETIC_SUMMARY_LINES]


async def test_quality_passes_hook_leaves_a_complete_extraction_alone():
    """A P&L whose totals already agree with the Summary (within 5%) is
    returned with those fields byte-identical — no replace, no re-add."""
    from app.api import documents as docs

    fields = _fields(fb=600.0, rooms=1200.0, total=1920.0, fb_exp=240.0, rooms_exp=360.0)
    out_fields, out_conf = await docs._apply_pnl_quality_passes(
        None,
        deal_id="deal",
        doc_id="doc",
        tenant_id="tenant",
        doc_type="PNL",
        fields=fields,
        confidence=_confidence(fields),
        extraction_data=_extraction(_page(_summary_grid())),
        storage_key=None,
    )
    out = _by_name(out_fields)
    for f in fields:
        assert out[f["field_name"]] == f
    names = [f["field_name"] for f in out_fields]
    assert len(names) == len(set(names)), "no field may be emitted twice"
    assert out_conf["summary_reconciliation"]["changes"] == []
    # Only the Summary lines with no field at all (other revenue, dept total, GOP) were added.
    assert {a["concept"] for a in out_conf["summary_reconciliation"]["added"]} == {
        "other_revenue", "dept_expenses", "gop",
    }


def test_pipeline_version_bumped_so_v3_rows_rerun():
    """v3 rows persisted the over-reached department sub-rows (the live
    defect) into the cache; without the bump a re-extract would serve them."""
    from app.api.documents import EXTRACTION_PIPELINE_VERSION

    assert EXTRACTION_PIPELINE_VERSION == "v4"


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
