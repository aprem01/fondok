""""Financials" trailing-twelve statements are typed as a TTM with their period
(W5 extract, item 2).

The Angler's "<Month> 2025 Financials" workbooks carry a periodic/YTD sheet and
a ``T12`` sheet whose columns are twelve dated month-ends and a TOTAL. The
sibling-template path reads the T12 sheet: twelve ``monthly.<mon>`` cells per
line plus a full-period line equal to their sum — a trailing twelve months —
but no ``period_type`` / ``period_ending``, so the documents stayed typed (or
analyst-tagged) PNL_MONTHLY and the loader had to guess.

The fixtures under ``fixtures/real_payloads/financials_ttm`` are the latest
prod extractions of those documents (read-only copy); the workbooks themselves
are the data-room files committed at the repo root.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-trailing-twelve.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app.extraction.trailing_twelve import (  # noqa: E402
    TRAILING_TWELVE_RULE,
    label_trailing_twelve,
)

_REPO = Path(__file__).resolve().parents[3]
_ROOM = _REPO / "FL Miami South Beach Anglers (Eshan)" / "1 - Financials"
_FIX = Path(__file__).parent / "fixtures" / "real_payloads" / "financials_ttm"

_WORKBOOKS = {
    "march": _ROOM / "Summary Financials" / "The Angler_s - March 2025 Financials.xlsx",
    "jun": _ROOM / "Summary Financials" / "The Angler_s - Jun 2025 Financials.xlsx",
    "november": _ROOM / "Summary Financials" / "The Angler_s - November 2024 Financials.xlsx",
    "y2019": _ROOM / "Summary Financials" / "Angler_s 2019 P&L.xlsx",
    "nov_detailed": _ROOM / "Detailed Financials" / "Angler_s 2024 November Detailed P&Lv.xlsm",
}

pytestmark = pytest.mark.skipif(
    not all(p.is_file() for p in _WORKBOOKS.values()),
    reason="Angler's data-room workbooks are not in this checkout",
)


def _fixture(name: str) -> tuple[str, list[dict]]:
    rec = json.loads((_FIX / name).read_text(encoding="utf-8"))
    return rec["doc_type"], rec["fields"]


_PARSED: dict[str, dict] = {}


def _pages(key: str) -> dict:
    if key not in _PARSED:
        from app.extraction.parser import parse_document

        p = _WORKBOOKS[key]
        doc = asyncio.run(parse_document(p.read_bytes(), p.name))
        _PARSED[key] = json.loads(doc.model_dump_json())
    return _PARSED[key]


def _value(fields: list[dict], suffix: str):
    for f in fields:
        if str(f.get("field_name", "")).endswith(suffix):
            return f.get("value")
    return None


def test_sibling_march_financials_is_a_ttm_ending_march_31():
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    assert doc_type == "PNL_MONTHLY"
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("march"))
    assert r.applied, r.skipped_reason
    assert _value(r.fields, "period_type") == "trailing_twelve"
    assert _value(r.fields, "period_ending") == "2025-03-31"
    # Both period fields come FIRST: every reader takes the first *.period_type.
    assert r.fields[0]["field_name"] == "p_and_l_usali.period_type"
    assert r.fields[1]["field_name"] == "p_and_l_usali.period_ending"
    prov = r.fields[0]["reconciled_from"]
    assert prov["rule"] == TRAILING_TWELVE_RULE
    assert prov["period_end"] == "2025-03-31"
    assert prov["period_start_month"] == "2024-04-30"
    assert prov["sheet"] == "T12"
    assert abs(prov["rooms_total"] - 9_332_100) < 1
    assert abs(prov["rooms_month_sum"] - 9_332_095) < 1
    assert r.fields[0]["reviewed"] == "reconciled"
    assert "twelve" in r.fields[0]["note"]
    # The sibling read already IS the T12 sheet: nothing else moves.
    assert r.fields[2:] == fields


def test_sibling_june_financials_is_a_ttm_ending_june_30():
    doc_type, fields = _fixture("jun_2025_financials_sibling.json")
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("jun"))
    assert r.applied, r.skipped_reason
    assert _value(r.fields, "period_ending") == "2025-06-30"


def test_llm_read_of_march_financials_gets_its_t12_statement():
    """The LLM read keeps the month (``march_2025_periodic``) and YTD under
    their own namespaces and has no statement-level full-period line; the T12
    sheet's totals and KPIs are added at the canonical paths, so the
    trailing-twelve statement is what the engines read."""
    doc_type, fields = _fixture("march_2025_financials_llm_v8.json")
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("march"))
    assert r.applied, r.skipped_reason
    by = {f["field_name"]: f for f in r.fields}
    assert by["p_and_l_usali.operating_revenue.rooms_revenue"]["value"] == pytest.approx(9_332_100, abs=60)
    assert by["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == pytest.approx(3_216_620, abs=60)
    assert by["p_and_l_usali.fixed_charges.insurance"]["value"] == pytest.approx(1_429_570, abs=60)
    assert by["p_and_l_usali.fixed_charges.property_taxes"]["value"] == pytest.approx(568_560, abs=60)
    assert by["p_and_l_usali.undistributed.property_operations"]["value"] == pytest.approx(614_428, abs=6)
    assert by["p_and_l_usali.operational_kpis.occupancy_pct"]["value"] == pytest.approx(0.832109, abs=1e-6)
    assert by["p_and_l_usali.operational_kpis.adr_usd"]["value"] == pytest.approx(232.773, abs=1e-3)
    # The month and the YTD are kept exactly as read.
    for f in fields:
        assert f in r.fields
    assert _value(r.fields, "period_ending") == "2025-03-31"


def test_a_t12_sheet_whose_months_are_not_twelve_consecutive_actuals_is_not_proof():
    """The November 2024 workbook's T12 header reads 2023-11-30, 2024-12-31
    ("Forecast"), 2024-02-29 … 2024-11-30 — not twelve consecutive months, one
    of them a forecast. No trailing twelve is claimed; the statement stays
    as typed."""
    doc_type, fields = _fixture("november_2024_financials_llm.json")
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("november"))
    assert not r.applied
    assert r.skipped_reason == "no_trailing_twelve_sheet"
    assert r.fields == fields


def test_genuine_monthly_statement_stays_monthly():
    """A detailed monthly P&L has no dated trailing-twelve sheet."""
    doc_type, fields = _fixture("november_2024_detailed_pnl_sibling.json")
    assert doc_type == "PNL_MONTHLY"
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("nov_detailed"))
    assert not r.applied
    assert r.skipped_reason == "no_trailing_twelve_sheet"
    assert r.fields == fields


def test_calendar_year_annual_pnl_stays_an_annual():
    doc_type, fields = _fixture("angler_2019_pnl_llm.json")
    assert doc_type == "PNL"
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("y2019"))
    assert not r.applied
    assert r.fields == fields


def test_a_sheet_whose_months_do_not_add_up_is_not_a_ttm():
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    pages = copy.deepcopy(_pages("march"))
    t12 = next(p for p in pages["pages"] if p["metadata"]["sheet_name"] == "T12")
    for row in t12["tables"][0]:
        if len(row) > 3 and row[1].strip() == "Rooms":
            row[3] = str(float(row[3]) + 50_000)
            break
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=pages)
    assert not r.applied
    assert r.skipped_reason == "sheet_does_not_add_up"


def test_statement_lines_of_another_period_are_left_alone():
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    other = copy.deepcopy(fields)
    for f in other:
        if f["field_name"] == "p_and_l_usali.operating_revenue.rooms_revenue":
            f["value"] = 8_000_000.0
    r = label_trailing_twelve(other, {}, doc_type=doc_type, extraction_data=_pages("march"))
    assert not r.applied
    assert r.skipped_reason == "statement_lines_are_another_period"


def test_a_stated_period_end_that_disagrees_is_left_to_the_analyst():
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    stated = [{"field_name": "p_and_l_usali.period_ending", "value": "2024-12-31"}] + fields
    r = label_trailing_twelve(stated, {}, doc_type=doc_type, extraction_data=_pages("march"))
    assert not r.applied
    assert r.skipped_reason == "stated_period_end_disagrees"


def test_without_the_workbook_grid_no_period_is_invented():
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data={"pages": []})
    assert not r.applied
    assert r.skipped_reason == "no_trailing_twelve_sheet"


def test_an_already_labelled_full_year_is_untouched():
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    labelled = [{"field_name": "p_and_l_usali.period_type", "value": "annual"}] + fields
    r = label_trailing_twelve(labelled, {}, doc_type="PNL", extraction_data=_pages("march"))
    assert not r.applied


def test_a_partial_label_is_replaced_with_its_old_value_kept():
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    labelled = [{"field_name": "p_and_l_usali.period_type", "value": "monthly"}] + fields
    r = label_trailing_twelve(labelled, {}, doc_type=doc_type, extraction_data=_pages("march"))
    assert r.applied
    types = [f for f in r.fields if f["field_name"].endswith("period_type")]
    assert len(types) == 1 and types[0]["value"] == "trailing_twelve"
    assert types[0]["reconciled_from"]["old_value"] == "monthly"


def test_refine_types_the_labelled_statement_t12():
    from app.api.documents import refine_doc_type_post_extraction

    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("march"))
    refined = refine_doc_type_post_extraction(
        classified_doc_type="PNL_MONTHLY",
        route=None,
        fields=r.fields,
        user_provided_doc_type=None,
        doc_id="d",
    )
    assert refined.doc_type == "T12"


def test_an_analyst_monthly_tag_on_a_proven_ttm_raises_the_banner():
    """The analyst's tag is kept as the document type, but not silently: the
    misclassification banner proposes T-12. The statement's own period fields
    (what the engines rank on) already say trailing twelve."""
    from app.api.documents import refine_doc_type_post_extraction

    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=_pages("march"))
    refined = refine_doc_type_post_extraction(
        classified_doc_type="PNL_MONTHLY",
        route=None,
        fields=r.fields,
        user_provided_doc_type="PNL_MONTHLY",
        doc_id="d",
    )
    assert refined.misclassified is True
    assert refined.normalized_ai == "T12"
    assert refined.ai_proposed_doc_type == "T12"


def test_an_analyst_monthly_tag_on_a_monthly_statement_is_still_trusted():
    from app.api.documents import refine_doc_type_post_extraction

    doc_type, fields = _fixture("november_2024_financials_llm.json")
    refined = refine_doc_type_post_extraction(
        classified_doc_type="PNL_MONTHLY",
        route=None,
        fields=fields,
        user_provided_doc_type="PNL_MONTHLY",
        doc_id="d",
    )
    assert refined.misclassified is False


async def test_the_quality_pass_hook_labels_before_persisting():
    from app.api.documents import _apply_pnl_quality_passes

    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    out, _conf = await _apply_pnl_quality_passes(
        None,
        deal_id="deal",
        doc_id="doc",
        tenant_id="t",
        doc_type=doc_type,
        fields=fields,
        confidence={},
        extraction_data=_pages("march"),
        storage_key=None,
    )
    assert _value(out, "period_type") == "trailing_twelve"
    assert _value(out, "period_ending") == "2025-03-31"


def test_a_t12_sheet_with_a_forecast_month_is_not_proof_even_without_a_title():
    """Review M1: the row under the dated header says Actual for every month.
    A sheet carrying a Forecast month is not a trailing twelve of actuals —
    rejected on the sheet itself, not only by a mismatching title."""
    doc_type, fields = _fixture("march_2025_financials_sibling.json")
    pages = copy.deepcopy(_pages("march"))
    t12 = next(p for p in pages["pages"] if p["metadata"]["sheet_name"] == "T12")
    grid = t12["tables"][0]
    for row in grid[:4]:
        for j, c in enumerate(row):
            if "PERIOD ENDING" in str(c).upper():
                row[j] = ""
    hdr = next(i for i, r in enumerate(grid) if sum("00:00:00" in str(c) for c in r) >= 12)
    actual_cols = [j for j, c in enumerate(grid[hdr + 1]) if str(c).strip() == "Actual"]
    grid[hdr + 1][actual_cols[-1]] = "Forecast"
    r = label_trailing_twelve(fields, {}, doc_type=doc_type, extraction_data=pages)
    assert not r.applied
    assert r.skipped_reason == "no_trailing_twelve_sheet"
