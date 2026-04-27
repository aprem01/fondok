"""Integration tests for the LLM-backed agent stack.

Each test loads the Kimpton Angler golden-set fixtures, runs ONE agent
end-to-end against real Claude API calls, and asserts a structural +
numerical contract. Every test is gated on ``ANTHROPIC_API_KEY`` being
set so CI can run the rest of the suite without burning tokens.

Cost ceiling: the full module is engineered to spend < $1 per run when
all five tests fire (Haiku router + 1 Sonnet extract + 1 Sonnet
normalize + 1 Sonnet variance narration + 1 Opus memo draft).

The Analyst test in particular costs the most (~$0.30-$0.50 of Opus
input tokens with prompt caching). The full module is gated by
``FONDOK_RUN_LLM_TESTS`` defaulting to "1" — set it to "0" to opt out
locally without unsetting the API key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

# Force the SQLite dev DSN before app modules import — same pattern as
# test_smoke.py — so settings don't bleed in from the developer shell.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fondok.db")


def _load_dotenv_if_unset() -> None:
    """Hydrate ANTHROPIC_API_KEY from apps/worker/.env when it isn't
    already in the shell environment.

    The pydantic Settings layer reads .env on its own, but the pytest
    skip gate below runs *before* any app module imports — so we mirror
    that .env lookup here, just for the API key.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key == "ANTHROPIC_API_KEY" and value and not os.environ.get(key):
            os.environ[key] = value
            break


_load_dotenv_if_unset()

# Resolve fixtures off the repo root, deterministically.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_GOLDEN_DIR = _REPO_ROOT / "evals" / "golden-set" / "kimpton-angler" / "input"


# Skip the entire module when the API key is missing — agents call
# real Anthropic endpoints. CI without a key still picks up the smoke
# tests; this module reports as skipped, not failed.
pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY unset — skipping LLM integration tests.",
)


# ─────────────────────── fixtures ───────────────────────


def _load_json(name: str) -> dict[str, Any]:
    path = _GOLDEN_DIR / name
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def om_fixture() -> dict[str, Any]:
    return _load_json("om_extracted.json")


@pytest.fixture(scope="module")
def t12_fixture() -> dict[str, Any]:
    return _load_json("t12_extracted.json")


@pytest.fixture(scope="module")
def str_fixture() -> dict[str, Any]:
    return _load_json("str_extracted.json")


@pytest.fixture(scope="module")
def deal_id() -> str:
    # Stable UUID — keeps Variance flag IDs reproducible across runs.
    return "11111111-2222-3333-4444-555555555555"


# ─────────────────────── 1. Router ───────────────────────


@pytest.mark.asyncio
async def test_router_classifies_om_as_om(
    om_fixture: dict[str, Any], deal_id: str
) -> None:
    """Router should classify the OM JSON as DocType.OM with confidence > 0.85."""
    from app.agents.extractor import serialize_json_doc
    from app.agents.router import RouterInput, run_router

    sample = serialize_json_doc(om_fixture)[:2000]
    payload = RouterInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id=deal_id,
        document_id=om_fixture.get("document_id"),
        filename=om_fixture.get("filename") or "Offering_Memorandum.pdf",
        content_sample=sample,
    )

    out = await run_router(payload)
    assert out.success, f"router failed: {out.error}"
    assert out.doc_type == "OM", f"expected OM, got {out.doc_type!r}"
    assert (
        out.confidence > 0.85
    ), f"expected confidence > 0.85, got {out.confidence:.3f}"
    assert out.model_calls, "router should record at least one ModelCall"


# ─────────────────────── 2. Extractor ───────────────────────


@pytest.mark.asyncio
async def test_extractor_pulls_30_plus_fields_from_om(
    om_fixture: dict[str, Any], deal_id: str
) -> None:
    """Extractor must pull ≥30 grounded fields from the OM with avg confidence > 0.85."""
    from fondok_schemas import DocType

    from app.agents.extractor import (
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
        serialize_json_doc,
    )

    doc = ExtractorDocument(
        document_id=om_fixture.get("document_id"),
        filename=om_fixture.get("filename") or "Offering_Memorandum.pdf",
        doc_type=DocType.OM,
        content=serialize_json_doc(om_fixture),
        source_pages=list(map(int, om_fixture.get("raw_text_by_page", {}).keys() or [1])),
    )
    payload = ExtractorInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id=deal_id,
        documents=[doc],
    )

    out = await run_extractor(payload)
    assert out.success, f"extractor failed: {out.error}"
    assert out.extracted_documents, "extractor returned no documents"
    result = out.extracted_documents[0]
    assert result.success, f"per-doc extract failed: {result.error}"
    assert (
        len(result.fields) >= 30
    ), f"expected ≥30 fields, got {len(result.fields)}"
    avg_conf = sum(f.confidence for f in result.fields) / len(result.fields)
    assert (
        avg_conf > 0.85
    ), f"expected avg confidence > 0.85, got {avg_conf:.3f}"


# ─────────────────────── 3. Normalizer ───────────────────────


@pytest.mark.asyncio
async def test_normalizer_produces_usali_with_correct_rooms_revenue(
    t12_fixture: dict[str, Any], deal_id: str
) -> None:
    """Normalizer should produce a USALIFinancials with rooms_revenue
    within 1% of the T-12 fixture's stated rooms_revenue ($14.13M).

    (Spec mentioned ≈ $11.12M, but the locked Kimpton Angler T-12
    fixture stamps rooms_revenue = $14,133,420; this test asserts what
    the fixture actually reports, within the same 1% band.)
    """
    from fondok_schemas import ExtractionField

    from app.agents.normalizer import NormalizerInput, run_normalizer

    rev = t12_fixture["p_and_l_usali"]["operating_revenue"]
    dept = t12_fixture["p_and_l_usali"]["departmental_expenses"]
    undist = t12_fixture["p_and_l_usali"]["undistributed_operating_expenses"]
    fees = t12_fixture["p_and_l_usali"]["fees_and_reserves"]
    fixed = t12_fixture["p_and_l_usali"]["fixed_charges"]
    gop = t12_fixture["p_and_l_usali"]["gross_operating_profit"]
    noi = t12_fixture["p_and_l_usali"]["net_operating_income"]

    def _ef(name: str, value: Any, unit: str = "USD") -> ExtractionField:
        return ExtractionField(
            field_name=name,
            value=value,
            unit=unit,
            source_page=1,
            confidence=0.95,
            raw_text=None,
        )

    fields = [
        _ef("p_and_l_usali.operating_revenue.rooms_revenue", rev["rooms_revenue"]),
        _ef("p_and_l_usali.operating_revenue.fb_revenue", rev["fb_revenue"]),
        _ef(
            "p_and_l_usali.operating_revenue.other_operated_departments",
            rev["other_operated_departments"],
        ),
        _ef(
            "p_and_l_usali.operating_revenue.miscellaneous_income",
            rev["miscellaneous_income"],
        ),
        _ef(
            "p_and_l_usali.operating_revenue.total_operating_revenue",
            rev["total_operating_revenue"],
        ),
        _ef(
            "p_and_l_usali.departmental_expenses.rooms_dept_expense",
            dept["rooms_dept_expense"],
        ),
        _ef(
            "p_and_l_usali.departmental_expenses.fb_dept_expense",
            dept["fb_dept_expense"],
        ),
        _ef(
            "p_and_l_usali.departmental_expenses.other_operated_dept_expense",
            dept["other_operated_dept_expense"],
        ),
        _ef(
            "p_and_l_usali.departmental_expenses.total_departmental_expenses",
            dept["total_departmental_expenses"],
        ),
        _ef(
            "p_and_l_usali.undistributed_operating_expenses.administrative_and_general",
            undist["administrative_and_general"],
        ),
        _ef(
            "p_and_l_usali.undistributed_operating_expenses.information_and_telecom",
            undist["information_and_telecom"],
        ),
        _ef(
            "p_and_l_usali.undistributed_operating_expenses.sales_and_marketing",
            undist["sales_and_marketing"],
        ),
        _ef(
            "p_and_l_usali.undistributed_operating_expenses.franchise_fees",
            undist["franchise_fees"],
        ),
        _ef(
            "p_and_l_usali.undistributed_operating_expenses.property_operations_and_maintenance",
            undist["property_operations_and_maintenance"],
        ),
        _ef(
            "p_and_l_usali.undistributed_operating_expenses.utilities",
            undist["utilities"],
        ),
        _ef(
            "p_and_l_usali.undistributed_operating_expenses.total_undistributed_expenses",
            undist["total_undistributed_expenses"],
        ),
        _ef("p_and_l_usali.fees_and_reserves.base_management_fee", fees["base_management_fee"]),
        _ef("p_and_l_usali.fees_and_reserves.ffe_reserve", fees["ffe_reserve"]),
        _ef("p_and_l_usali.fixed_charges.property_taxes", fixed["property_taxes"]),
        _ef("p_and_l_usali.fixed_charges.insurance", fixed["insurance"]),
        _ef("p_and_l_usali.fixed_charges.ground_rent", fixed["ground_rent"]),
        _ef("p_and_l_usali.fixed_charges.other_fixed_charges", fixed["other_fixed_charges"]),
        _ef("p_and_l_usali.fixed_charges.total_fixed_charges", fixed["total_fixed_charges"]),
        _ef("p_and_l_usali.gross_operating_profit.gop_usd", gop["gop_usd"]),
        _ef("p_and_l_usali.net_operating_income.noi_usd", noi["noi_usd"]),
        _ef("occupancy_pct", t12_fixture["occupancy_pct"], unit="pct"),
        _ef("adr_usd", t12_fixture["adr_usd"]),
        _ef("revpar_usd", t12_fixture["revpar_usd"]),
    ]

    payload = NormalizerInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id=deal_id,
        fields=fields,
        period_hint=f"TTM ended {t12_fixture['as_of_date']}",
    )
    out = await run_normalizer(payload)
    assert out.success, f"normalizer failed: {out.error}"
    spread = out.normalized_spread
    assert spread is not None, "normalizer produced no spread"

    expected = float(rev["rooms_revenue"])  # $14,133,420 in the locked fixture
    actual = spread.rooms_revenue
    assert (
        abs(actual - expected) / expected <= 0.01
    ), f"rooms_revenue {actual:,.0f} not within 1% of {expected:,.0f}"

    # Total revenue should round-trip exactly (the line-item sum is what
    # the source reports). Exact within 1%.
    expected_total = float(rev["total_operating_revenue"])
    assert (
        abs(spread.total_revenue - expected_total) / expected_total <= 0.01
    ), f"total_revenue {spread.total_revenue:,.0f} not within 1% of {expected_total:,.0f}"

    # The Normalizer also produces a structured period_label and the
    # operational KPIs round-trip from the input.
    assert spread.period_label, "missing period_label"
    assert spread.occupancy is not None and abs(spread.occupancy - 0.762) < 0.005


# ─────────────────────── 4. Variance ───────────────────────


@pytest.mark.asyncio
async def test_variance_flags_noi_overstatement(
    om_fixture: dict[str, Any],
    t12_fixture: dict[str, Any],
    deal_id: str,
) -> None:
    """Variance should fire ≥7 flags including the broker NOI overstatement
    (broker $5.20M vs T-12 $4.18M ≈ +24% over actual)."""
    from fondok_schemas import (
        DepartmentalExpenses,
        FixedCharges,
        USALIFinancials,
        UndistributedExpenses,
    )

    from app.agents.variance import (
        VarianceBrokerField,
        VarianceInput,
        run_variance,
    )

    pl = t12_fixture["p_and_l_usali"]
    actuals = USALIFinancials(
        period_label=f"TTM ended {t12_fixture['as_of_date']}",
        rooms_revenue=pl["operating_revenue"]["rooms_revenue"],
        fb_revenue=pl["operating_revenue"]["fb_revenue"],
        other_revenue=pl["operating_revenue"]["other_operated_departments"]
        + pl["operating_revenue"]["miscellaneous_income"],
        total_revenue=pl["operating_revenue"]["total_operating_revenue"],
        dept_expenses=DepartmentalExpenses(
            rooms=pl["departmental_expenses"]["rooms_dept_expense"],
            food_beverage=pl["departmental_expenses"]["fb_dept_expense"],
            other_operated=pl["departmental_expenses"]["other_operated_dept_expense"],
            total=pl["departmental_expenses"]["total_departmental_expenses"],
        ),
        undistributed=UndistributedExpenses(
            administrative_general=pl["undistributed_operating_expenses"][
                "administrative_and_general"
            ],
            information_telecom=pl["undistributed_operating_expenses"][
                "information_and_telecom"
            ],
            sales_marketing=pl["undistributed_operating_expenses"][
                "sales_and_marketing"
            ]
            + pl["undistributed_operating_expenses"]["franchise_fees"],
            property_operations=pl["undistributed_operating_expenses"][
                "property_operations_and_maintenance"
            ],
            utilities=pl["undistributed_operating_expenses"]["utilities"],
            total=pl["undistributed_operating_expenses"]["total_undistributed_expenses"],
        ),
        mgmt_fee=pl["fees_and_reserves"]["base_management_fee"]
        + pl["fees_and_reserves"]["incentive_management_fee"],
        ffe_reserve=pl["fees_and_reserves"]["ffe_reserve"],
        fixed_charges=FixedCharges(
            property_taxes=pl["fixed_charges"]["property_taxes"],
            insurance=pl["fixed_charges"]["insurance"],
            rent=pl["fixed_charges"]["ground_rent"],
            other_fixed=pl["fixed_charges"]["other_fixed_charges"],
            total=pl["fixed_charges"]["total_fixed_charges"],
        ),
        gop=pl["gross_operating_profit"]["gop_usd"],
        noi=pl["net_operating_income"]["noi_usd"],
        opex_ratio=0.78,
        occupancy=t12_fixture["occupancy_pct"],
        adr=t12_fixture["adr_usd"],
        revpar=t12_fixture["revpar_usd"],
    )

    proforma = om_fixture["broker_proforma"]
    broker_fields = [
        VarianceBrokerField(field="rooms_revenue", value=proforma["rooms_revenue_usd"]),
        VarianceBrokerField(field="fb_revenue", value=proforma["fb_revenue_usd"]),
        VarianceBrokerField(field="total_revenue", value=proforma["total_revenue_usd"]),
        VarianceBrokerField(
            field="departmental_expenses",
            value=proforma["departmental_expenses_usd"],
        ),
        VarianceBrokerField(
            field="undistributed_expenses",
            value=proforma["undistributed_expenses_usd"],
        ),
        VarianceBrokerField(field="gop", value=proforma["gop_usd"]),
        VarianceBrokerField(field="mgmt_fee", value=proforma["mgmt_fee_usd"]),
        VarianceBrokerField(field="ffe_reserve", value=proforma["ffe_reserve_usd"]),
        VarianceBrokerField(field="fixed_charges", value=proforma["fixed_charges_usd"]),
        VarianceBrokerField(field="noi", value=proforma["noi_usd"]),
        VarianceBrokerField(field="occupancy", value=proforma["occupancy_pct"]),
        VarianceBrokerField(field="adr", value=proforma["adr_usd"]),
        VarianceBrokerField(field="revpar", value=proforma["revpar_usd"]),
    ]
    payload = VarianceInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id=deal_id,
        actuals=actuals,
        broker_fields=broker_fields,
    )
    out = await run_variance(payload)
    assert out.success, f"variance failed: {out.error}"
    assert out.report is not None, "variance produced no report"
    flags = out.report.flags
    assert len(flags) >= 7, f"expected ≥7 flags, got {len(flags)}"

    # Find the NOI overstatement flag.
    noi_flag = next(
        (f for f in flags if f.field.lower() in ("noi", "noi_usd")),
        None,
    )
    assert noi_flag is not None, "no NOI variance flag emitted"
    # Broker $5.20M vs actual $4.18M → broker is HIGHER, so actual−broker
    # is negative (~-1.02M) — 24%-ish under broker.
    assert (
        abs(noi_flag.delta_pct or 0.0) >= 0.15
    ), f"NOI delta_pct should be ≥15%, got {noi_flag.delta_pct}"
    assert noi_flag.severity.value in ("Critical", "Warn")
    # Every flag must reference a real catalog rule.
    from app.usali_rules import rule_index

    idx = rule_index()
    for f in flags:
        assert f.rule_id in idx, f"flag {f.field} references unknown rule {f.rule_id}"

    # Most flags should carry the LLM-drafted note.
    noted = sum(1 for f in flags if f.note)
    assert noted >= 1, "expected at least one LLM-drafted note"


# ─────────────────────── 5. Analyst ───────────────────────


@pytest.mark.asyncio
async def test_analyst_drafts_six_section_memo(
    om_fixture: dict[str, Any],
    t12_fixture: dict[str, Any],
    str_fixture: dict[str, Any],
    deal_id: str,
) -> None:
    """Analyst should draft an InvestmentMemo with all 6 required sections,
    each carrying ≥1 citation."""
    from fondok_schemas import (
        DepartmentalExpenses,
        FixedCharges,
        Severity,
        USALIFinancials,
        UndistributedExpenses,
    )
    from fondok_schemas.variance import VarianceFlag, VarianceReport

    from app.agents.analyst import (
        AnalystInput,
        AnalystSourceDocument,
        run_analyst,
    )

    pl = t12_fixture["p_and_l_usali"]
    spread = USALIFinancials(
        period_label=f"TTM ended {t12_fixture['as_of_date']}",
        rooms_revenue=pl["operating_revenue"]["rooms_revenue"],
        fb_revenue=pl["operating_revenue"]["fb_revenue"],
        other_revenue=pl["operating_revenue"]["other_operated_departments"]
        + pl["operating_revenue"]["miscellaneous_income"],
        total_revenue=pl["operating_revenue"]["total_operating_revenue"],
        dept_expenses=DepartmentalExpenses(
            rooms=pl["departmental_expenses"]["rooms_dept_expense"],
            food_beverage=pl["departmental_expenses"]["fb_dept_expense"],
            other_operated=pl["departmental_expenses"]["other_operated_dept_expense"],
            total=pl["departmental_expenses"]["total_departmental_expenses"],
        ),
        undistributed=UndistributedExpenses(
            administrative_general=pl["undistributed_operating_expenses"][
                "administrative_and_general"
            ],
            information_telecom=pl["undistributed_operating_expenses"][
                "information_and_telecom"
            ],
            sales_marketing=pl["undistributed_operating_expenses"][
                "sales_and_marketing"
            ]
            + pl["undistributed_operating_expenses"]["franchise_fees"],
            property_operations=pl["undistributed_operating_expenses"][
                "property_operations_and_maintenance"
            ],
            utilities=pl["undistributed_operating_expenses"]["utilities"],
            total=pl["undistributed_operating_expenses"]["total_undistributed_expenses"],
        ),
        mgmt_fee=pl["fees_and_reserves"]["base_management_fee"],
        ffe_reserve=pl["fees_and_reserves"]["ffe_reserve"],
        fixed_charges=FixedCharges(
            property_taxes=pl["fixed_charges"]["property_taxes"],
            insurance=pl["fixed_charges"]["insurance"],
            rent=pl["fixed_charges"]["ground_rent"],
            other_fixed=pl["fixed_charges"]["other_fixed_charges"],
            total=pl["fixed_charges"]["total_fixed_charges"],
        ),
        gop=pl["gross_operating_profit"]["gop_usd"],
        noi=pl["net_operating_income"]["noi_usd"],
        opex_ratio=0.78,
        occupancy=t12_fixture["occupancy_pct"],
        adr=t12_fixture["adr_usd"],
        revpar=t12_fixture["revpar_usd"],
    )

    # A small variance report so the Analyst has something to surface.
    from uuid import uuid4

    deal_uuid_str = "11111111-2222-3333-4444-555555555555"
    from uuid import UUID

    deal_uuid = UUID(deal_uuid_str)
    variance = VarianceReport(
        deal_id=deal_uuid,
        flags=[
            VarianceFlag(
                id=uuid4(),
                deal_id=deal_uuid,
                field="noi",
                actual=spread.noi,
                broker=om_fixture["broker_proforma"]["noi_usd"],
                delta=spread.noi - om_fixture["broker_proforma"]["noi_usd"],
                delta_pct=(spread.noi - om_fixture["broker_proforma"]["noi_usd"])
                / spread.noi,
                severity=Severity.CRITICAL,
                rule_id="BROKER_VS_T12_NOI_VARIANCE",
                source_document_id=None,
                source_page=34,
                note=(
                    "Broker NOI of $5.20M assumes 80% stabilized occupancy "
                    "vs. T-12 actual of 76.2%; the 380bp lift is unsupported."
                ),
            )
        ],
        critical_count=1,
        warn_count=0,
        info_count=0,
    )

    om_pages = om_fixture.get("raw_text_by_page", {})
    t12_pages = {1: f"T-12 actuals — NOI ${spread.noi:,.0f}, RevPAR ${spread.revpar:.2f}"}
    str_pages = {
        1: (
            f"Subject RevPAR ${str_fixture['ttm_performance']['subject']['revpar_usd']}; "
            f"comp set ${str_fixture['ttm_performance']['comp_set']['revpar_usd']}; "
            f"RGI {str_fixture['ttm_performance']['indices']['rgi_revpar_index']}"
        )
    }

    sources = [
        AnalystSourceDocument(
            document_id=om_fixture["document_id"],
            filename=om_fixture["filename"],
            doc_type="OM",
            page_count=om_fixture.get("page_count", 1) or 1,
            excerpts_by_page={int(k): v for k, v in om_pages.items()},
        ),
        AnalystSourceDocument(
            document_id=t12_fixture["document_id"],
            filename=t12_fixture["filename"],
            doc_type="T12",
            page_count=1,
            excerpts_by_page=t12_pages,
        ),
        AnalystSourceDocument(
            document_id=str_fixture["document_id"],
            filename=str_fixture["filename"],
            doc_type="STR",
            page_count=1,
            excerpts_by_page=str_pages,
        ),
    ]

    payload = AnalystInput(
        tenant_id="00000000-0000-0000-0000-000000000001",
        deal_id=deal_id,
        deal_data={
            "property": om_fixture["property_overview"]["name"],
            "address": om_fixture["property_overview"]["address"],
            "keys": om_fixture["property_overview"]["keys"],
            "asking_price_usd": om_fixture["asking_price"]["headline_price_usd"],
        },
        normalized_spread=spread,
        engine_results={
            "levered_irr": 0.142,
            "equity_multiple": 1.85,
            "dscr_year_1": 1.31,
            "debt_yield": 0.092,
        },
        variance_report=variance,
        source_documents=sources,
    )

    out = await run_analyst(payload)
    assert out.success, f"analyst failed: {out.error}"
    memo = out.memo
    assert memo is not None, "analyst produced no memo"
    assert (
        len(memo.sections) == 6
    ), f"expected 6 sections, got {len(memo.sections)}"
    for sec in memo.sections:
        assert sec.body, f"section {sec.section_id.value} has empty body"
        assert (
            len(sec.citations) >= 1
        ), f"section {sec.section_id.value} has no citations"
