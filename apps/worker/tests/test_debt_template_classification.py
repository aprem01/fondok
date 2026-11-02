"""A debt / loan template is never typed as a P&L (W5 extract, item 3).

Prod deal 76d10a64: ``Kimpton_Fondok_Debt_Upload_Template.xlsx`` reached the
filename-hint fallback (``router:extract-hint-fallback;dt:T12``) because the
Router returned an off-enum label, and ``_guess_doc_type`` had no debt rule, so
the template fell through to the ``T12`` default. The extraction itself was
39 ``in_place_debt.*`` fields and not one P&L line (structural P&L score 0),
but nothing post-extraction demoted it, and as an undated "current" T-12 it
outranked every real P&L on the deal.

The fields below are the template's own rows (the workbook ships in the
Kimpton QA kit), in the shape the extractor emitted them on prod.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

_TMP_DB = Path(tempfile.gettempdir()) / "fondok-tests-debt-template-classification.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP_DB}"
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-key-not-real")

from app.api.documents import (  # noqa: E402
    _guess_doc_type,
    refine_doc_type_post_extraction,
)


def _f(name, value, unit=None):
    return {"field_name": name, "value": value, "unit": unit, "confidence": 0.99, "source_page": 2}


DEBT_TEMPLATE_FIELDS = [
    _f("in_place_debt.senior_loan.loan_amount_usd", 35000000, "USD"),
    _f("in_place_debt.senior_loan.rate_type", "Floating"),
    _f("in_place_debt.senior_loan.index", "SOFR"),
    _f("in_place_debt.senior_loan.spread_pct", 0.05, "pct"),
    _f("in_place_debt.senior_loan.sofr_floor_pct", 0.03, "pct"),
    _f("in_place_debt.senior_loan.rate_cap_pct", 0.06, "pct"),
    _f("in_place_debt.senior_loan.interest_only", True),
    _f("in_place_debt.senior_loan.initial_maturity_date", "2029-03-31", "date"),
    _f("in_place_debt.pace_loan.loan_amount_usd", 30000000, "USD"),
    _f("in_place_debt.covenants.maximum_ltv", 0.65, "pct"),
    _f("in_place_debt.covenants.dscr_threshold_1", 1.0, "ratio"),
    _f("in_place_debt.covenants.debt_yield_threshold_1", 0.1, "pct"),
    _f("property_overview.brand", "Kimpton"),
]


@pytest.mark.parametrize(
    "filename",
    [
        "Kimpton_Fondok_Debt_Upload_Template.xlsx",
        "Kimpton_Anglers_Debt_Term_Sheet_Test.pdf",
        "Senior Loan Agreement.pdf",
        "mortgage_terms.xlsx",
        "TermSheet_v3.pdf",
    ],
)
def test_filename_hint_types_debt_documents_as_debt(filename):
    assert _guess_doc_type(filename) == "DEBT"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("Angler_s 2024 Full Year Detailed P&L.xlsm", "PNL"),
        ("Harbor_T12_Jun2025.xlsx", "T12"),
        ("Offering_Memorandum.pdf", "OM"),
    ],
)
def test_filename_hint_unchanged_for_statements(filename, expected):
    assert _guess_doc_type(filename) == expected


def test_debt_template_typed_t12_is_reclassified_debt():
    r = refine_doc_type_post_extraction(
        classified_doc_type="T12",
        route="extract-hint-fallback",
        fields=DEBT_TEMPLATE_FIELDS,
        user_provided_doc_type=None,
        doc_id="ac604fdd-5d38-4b8d-b519-ebfd6e7e55ac",
    )
    assert r.doc_type == "DEBT"
    assert r.classified_doc_type == "DEBT"
    # The rewrite is not silent: the original call travels on the AI-proposal
    # column, as for every other structural override.
    assert r.router_overridden_original == "T12"
    assert r.ai_proposed_doc_type == "T12"
    assert r.misclassified is False


def test_debt_template_under_an_analyst_pnl_tag_raises_the_banner():
    r = refine_doc_type_post_extraction(
        classified_doc_type="T12",
        route="extract",
        fields=DEBT_TEMPLATE_FIELDS,
        user_provided_doc_type="T12",
        doc_id="d",
    )
    assert r.misclassified is True
    assert r.normalized_ai == "DEBT"


def test_a_pnl_carrying_a_debt_block_stays_a_pnl():
    pnl = [
        {"field_name": "p_and_l_usali.period_type", "value": "trailing_twelve"},
        {"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 9_332_100.0, "unit": "USD"},
        {"field_name": "p_and_l_usali.operating_revenue.total_revenue", "value": 13_815_324.0, "unit": "USD"},
        {"field_name": "p_and_l_usali.undistributed.administrative_general", "value": 1_300_000.0, "unit": "USD"},
    ] + DEBT_TEMPLATE_FIELDS[:5]
    r = refine_doc_type_post_extraction(
        classified_doc_type="T12",
        route="extract",
        fields=pnl,
        user_provided_doc_type=None,
        doc_id="d",
    )
    assert r.doc_type == "T12"


# ── Review round 2 (I1): a loan word alone is not a debt document ─────────


@pytest.mark.parametrize(
    "filename",
    [
        "Hotel Operating Statement - Loan Package.xlsx",
        "Monthly Financials - Loan Reporting.xlsx",
        "Operating Statement – Loan Package.xlsx",
        "Loan Sizing - Operating Statement 2024.xlsx",
        "Budget 2025 incl debt service.xlsx",
        "Refinance Appraisal.pdf",
        "debt_service_schedule_P&L.xlsx",
    ],
)
def test_a_statement_filename_mentioning_a_loan_is_not_debt(filename):
    assert _guess_doc_type(filename) != "DEBT"


def test_debt_is_a_non_financial_router_label_so_a_real_pnl_is_rescued():
    """A real P&L that reached DEBT (hint or Router) is retyped a P&L by the
    structural recognizer, exactly as for every other non-financial label."""
    import json
    from pathlib import Path

    from app.api.documents import _NON_FINANCIAL_ROUTER_LABELS

    assert "DEBT" in _NON_FINANCIAL_ROUTER_LABELS
    payload = json.loads(
        (Path(__file__).parent / "fixtures" / "real_payloads" / "anglers_annual_pnl_real.json").read_text()
    )
    fields = payload["fields"] if isinstance(payload, dict) else payload
    r = refine_doc_type_post_extraction(
        classified_doc_type="DEBT",
        route="extract-hint-fallback",
        fields=fields,
        user_provided_doc_type=None,
        doc_id="d",
    )
    assert r.doc_type in {"T12", "PNL", "PNL_MONTHLY", "PNL_YTD"}
    assert r.router_overridden_original == "DEBT"
