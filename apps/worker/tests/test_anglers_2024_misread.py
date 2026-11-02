"""W5 extract, item 4 — "Angler_s 2024 Full Year Detailed P&L.xlsm" misread.

Prod (deals 7a9928e0 / 2e1db521 / 76d10a64 / e577f547) still serves the
pipeline-v1 extraction of this workbook: F&B 96,528.1 (the restaurant outlet
sheet ``D_REST_CON``), rooms revenue and other revenue as the TEXT "$10M" /
"$1.24M", and ``prior_year.rooms_revenue`` as the only numeric rooms line.

Root causes, each pinned here against the real workbook and the real v1 field
list (``fixtures/real_payloads/anglers_2024_misread/v1_extraction.json``):

1. The F&B line — the v1 LLM read the outlet sheet. The Summary-sheet
   reconciler (pipeline v3+) replaces it from the workbook's own Summary.
2. The "$10M" / "$1.24M" strings are not an extractor read: both fields carry
   ``reviewed = "edited"``. The Data Room's inline review editor pre-filled its
   input with the DISPLAY value (``formatValue`` → "$10M") and the worker
   stored whatever string came back. A text value on a statement total then
   blocked the reconciler too: it counted the concept as present (so it added
   nothing) but only replaced numbers (so it kept the text). Now:
   * the reconciler replaces a non-numeric statement-level total with the
     Summary value, keeping the text in ``reconciled_from.old_value``;
   * the review endpoint refuses a non-numeric edit of a numeric field
     (``tests/test_field_review.py``), and the editor starts from the raw
     number (web ``DataRoomTab``).
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-anglers-2024-misread.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"

_REPO = Path(__file__).resolve().parents[3]
_WORKBOOK = (
    _REPO
    / "FL Miami South Beach Anglers (Eshan)"
    / "1 - Financials"
    / "Detailed Financials"
    / "Angler_s 2024 Full Year Detailed P&L.xlsm"
)
_FIXTURE = Path(__file__).parent / "fixtures" / "real_payloads" / "anglers_2024_misread" / "v1_extraction.json"

pytestmark = pytest.mark.skipif(not _WORKBOOK.is_file(), reason="Angler's 2024 workbook not in this checkout")


@pytest.fixture(scope="module")
def parsed() -> tuple[dict, bytes]:
    from app.extraction.parser import parse_document

    body = _WORKBOOK.read_bytes()
    doc = asyncio.run(parse_document(body, _WORKBOOK.name))
    return json.loads(doc.model_dump_json()), body


def _v1_fields() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))["fields"]


def _by(fields: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in fields:
        out.setdefault(f["field_name"], f)
    return out


def test_fixture_carries_the_prod_misreads():
    by = _by(_v1_fields())
    assert by["p_and_l_usali.operating_revenue.rooms_revenue"]["value"] == "$10M"
    assert by["p_and_l_usali.operating_revenue.rooms_revenue"]["reviewed"] == "edited"
    assert by["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == pytest.approx(96528.1)
    assert by["p_and_l_usali.prior_year.rooms_revenue"]["value"] == pytest.approx(9_807_990)


@pytest.mark.parametrize("use_bytes", [True, False])
def test_full_year_columns_are_read_from_the_workbook(parsed, use_bytes):
    from app.extraction.usali_summary_reconcile import reconcile_extraction

    extraction_data, body = parsed
    r = reconcile_extraction(
        _v1_fields(),
        {},
        doc_type="T12",
        extraction_data=extraction_data,
        file_bytes=body if use_bytes else None,
    )
    by = _by(r.fields)
    tol = 0.01 if use_bytes else 60.0  # parser cache = six significant digits
    rooms = by["p_and_l_usali.operating_revenue.rooms_revenue"]
    assert rooms["value"] == pytest.approx(9_496_407.22, abs=tol)
    assert rooms["reviewed"] == "reconciled"
    assert rooms["reconciled_from"]["old_value"] == "$10M"
    assert "$10M" in rooms["note"]
    assert by["p_and_l_usali.operating_revenue.food_beverage_revenue"]["value"] == pytest.approx(
        2_739_040.71, abs=tol
    )
    assert by["p_and_l_usali.operating_revenue.other_revenue"]["value"] == pytest.approx(17_806.42, abs=tol)
    assert by["p_and_l_usali.operating_revenue.other_revenue"]["reconciled_from"]["old_value"] == "$1.24M"
    # Reference rows are never touched.
    assert by["p_and_l_usali.prior_year.rooms_revenue"]["value"] == pytest.approx(9_807_990)
    assert by["p_and_l_usali.budget.rooms_revenue"]["value"] == pytest.approx(9_985_570)


def test_the_loader_reads_full_year_rooms_not_the_prior_year(parsed):
    from app.extraction.field_catalog import T12_REVENUE_FIELD_ALIASES
    from app.extraction.usali_summary_reconcile import reconcile_extraction
    from app.services import engine_runner as er

    extraction_data, body = parsed
    before = er._resolve_statement_lines(
        _v1_fields(), T12_REVENUE_FIELD_ALIASES, opposite_section=er._is_cost_section
    )
    # The prod symptom: with rooms as text, the only numeric rooms line read
    # was a prior-year / budget row.
    assert before["rooms_revenue"][1]["field_name"] != "p_and_l_usali.operating_revenue.rooms_revenue"

    r = reconcile_extraction(
        _v1_fields(), {}, doc_type="T12", extraction_data=extraction_data, file_bytes=body
    )
    after = er._resolve_statement_lines(
        r.fields, T12_REVENUE_FIELD_ALIASES, opposite_section=er._is_cost_section
    )
    v, f = after["rooms_revenue"]
    assert f["field_name"] == "p_and_l_usali.operating_revenue.rooms_revenue"
    assert v == pytest.approx(9_496_407.22, abs=0.01)
    assert after["fb_revenue"][0] == pytest.approx(2_739_040.71, abs=0.01)


def test_a_non_numeric_total_with_no_summary_row_is_left_alone():
    from app.extraction.usali_summary_reconcile import SummaryTable, reconcile_fields

    table = SummaryTable(sheet_name="Summary", page_index=1, annual_column_rule="sum_of_months", source="t")
    fields = [{"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": "$10M", "unit": "USD"}]
    r = reconcile_fields(fields, {}, table, doc_type="T12")
    assert r.fields == fields and not r.changes
