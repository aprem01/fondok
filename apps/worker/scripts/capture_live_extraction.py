"""Capture LIVE extraction payloads from Sam Anglers fixture files.

Sam QA Bug #3 v4 (June 28 2026). The v1/v2/v3 USALI fixes all
"worked against saved fixtures" then failed in production because the
LLM extractor emits DIFFERENT path namespaces between runs (the v2
real-prod payload at ``p_and_l_usali.rooms.revenue_usd`` becomes
``p_and_l_usali.revenues.rooms_usd`` on the next day's invocation).

This script re-runs the real ``run_extractor`` Sonnet 4.6 pipeline
against ``apps/worker/tests/fixtures/sam_anglers_t12.xlsx`` and
``…/sam_anglers_2023_pnl.xlsx`` and dumps the resulting payloads to
``apps/worker/tests/fixtures/usali_v4/live_extraction_anglers_*.json``.

Run twice to capture two extractions of the SAME source file — the
diff demonstrates LLM-namespace drift, and the v4 structural
recognizer's test suite asserts that both runs surface the same
canonical concepts.

Usage:
    cd apps/worker
    ANTHROPIC_API_KEY=sk-… python scripts/capture_live_extraction.py

When ``ANTHROPIC_API_KEY`` is unset the script falls back to a
deterministic "perturbed" payload synthesis — useful for offline CI
where re-running the LLM isn't free. The perturbation flips key
namespaces between three known styles
(``p_and_l_usali.rooms.revenue_usd`` /
``pages.financial_summary.rooms_segment.gross_revenue`` /
``hotel_revenues.rooms_segment.gross``) so the structural recognizer's
tests still cover the cross-namespace surface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

WORKER_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = WORKER_ROOT.parents[1]
FIXTURE_DIR = WORKER_ROOT / "tests" / "fixtures"
OUTPUT_DIR = FIXTURE_DIR / "usali_v4"

# Inputs Sam uploaded on Wave 4 QA.
T12_SOURCE = FIXTURE_DIR / "sam_anglers_t12.xlsx"
ANNUAL_SOURCE = FIXTURE_DIR / "sam_anglers_2023_pnl.xlsx"

logging.basicConfig(
    level=os.environ.get("LOGLEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("capture_live")


async def _run_real_extractor(source: Path, doc_type: str) -> dict:
    """Call the real Sonnet extractor on ``source`` and return the
    extraction payload as a dict the test suite can persist.

    Requires ``ANTHROPIC_API_KEY``. Raises if the extractor errors.
    """
    sys.path.insert(0, str(WORKER_ROOT))
    from app.agents.extractor import (  # type: ignore[import]
        ExtractorDocument,
        ExtractorInput,
        run_extractor,
    )

    # Parse the xlsx via the extractor's normal feed. We use the
    # parser module to mirror prod — the extractor expects text
    # content, not raw bytes.
    from app.parser import parse_document  # type: ignore[import]

    parsed = parse_document(str(source))
    content = parsed.get("text") or parsed.get("content") or ""
    if not content:
        raise RuntimeError(f"parser returned empty content for {source}")

    payload = ExtractorInput(
        tenant_id="default",
        deal_id="5e81749e-live-capture",
        documents=[
            ExtractorDocument(
                document_id="live-capture",
                filename=source.name,
                doc_type=doc_type,
                content=content,
            )
        ],
    )
    out = await run_extractor(payload)
    if not out.extracted_documents:
        raise RuntimeError("extractor returned no documents")
    doc = out.extracted_documents[0]
    return {
        "document_id": doc.document_id,
        "status": "EXTRACTED",
        "fields": [f.model_dump() for f in doc.fields],
        "confidence_report": doc.confidence.model_dump(),
        "agent_version": "router:extractor;extractor;v4-live-capture",
        "page_count": parsed.get("page_count"),
    }


def _synthesize_perturbed(source_fixture: Path, namespace_style: str) -> dict:
    """Offline fallback — re-namespace the saved real_payloads fixture
    under one of three LLM-emission styles so the recognizer's tests
    still cover cross-namespace coverage.

    ``namespace_style`` ∈ {``"p_and_l_usali"`` (default),
    ``"pages_financial"``, ``"hotel_revenues"``}.
    """
    with source_fixture.open() as f:
        base = json.load(f)

    if namespace_style == "p_and_l_usali":
        # Untouched — matches saved real_payloads.
        return base

    if namespace_style == "pages_financial":
        path_map = {
            "p_and_l_usali.rooms.revenue_usd": "pages.financial_summary.rooms_segment.gross_revenue",
            "p_and_l_usali.food_and_beverage.revenue_usd": "pages.financial_summary.food_beverage_segment.gross_revenue",
            "p_and_l_usali.other_operated_departments.revenue_usd": "pages.financial_summary.other_operated.gross_revenue",
            "p_and_l_usali.miscellaneous_income.revenue_usd": "pages.financial_summary.misc_income.revenue",
            "p_and_l_usali.total_revenues_usd": "pages.financial_summary.total_revenue",
            "p_and_l_usali.rooms.expense_usd": "pages.dept_expenses.rooms_dept.cost",
            "p_and_l_usali.food_and_beverage.expense_usd": "pages.dept_expenses.fb_dept.cost",
            "p_and_l_usali.other_operated_departments.expense_usd": "pages.dept_expenses.other_dept.cost",
            "p_and_l_usali.total_departmental_expense_usd": "pages.dept_expenses.total",
            "p_and_l_usali.administrative_and_general.expense_usd": "pages.undist.a_and_g.cost",
            "p_and_l_usali.sales_and_marketing.expense_usd": "pages.undist.sales_marketing.cost",
            "p_and_l_usali.property_operations_and_maintenance.expense_usd": "pages.undist.repairs_maintenance.cost",
            "p_and_l_usali.utilities.expense_usd": "pages.undist.utilities.cost",
            "p_and_l_usali.information_and_telecom.expense_usd": "pages.undist.information_telecom.cost",
            "p_and_l_usali.total_undistributed_expenses_usd": "pages.undist.total",
            "p_and_l_usali.gross_operating_profit_usd": "pages.rollups.gross_operating_profit",
            "p_and_l_usali.management_fees_usd": "pages.fees.management_fee",
            "p_and_l_usali.ffe_replacement_reserve_usd": "pages.fees.ffe_reserve",
            "p_and_l_usali.non_operating.property_and_other_taxes_usd": "pages.non_op.property_tax",
            "p_and_l_usali.non_operating.insurance_usd": "pages.non_op.insurance",
            "p_and_l_usali.total_non_operating_expenses_usd": "pages.non_op.total",
            "p_and_l_usali.ebitda_less_replacement_reserve_usd": "pages.rollups.ebitda",
            "ttm_summary_per_om.revpar_usd": "pages.kpis.revpar",
            "ttm_summary_per_om.adr_usd": "pages.kpis.adr",
            "ttm_summary_per_om.occupancy_pct": "pages.kpis.occupancy",
        }
    elif namespace_style == "hotel_revenues":
        path_map = {
            "p_and_l_usali.rooms.revenue_usd": "hotel_revenues.rooms_segment.gross",
            "p_and_l_usali.food_and_beverage.revenue_usd": "hotel_revenues.fb_segment.gross",
            "p_and_l_usali.other_operated_departments.revenue_usd": "hotel_revenues.other_segment.gross",
            "p_and_l_usali.miscellaneous_income.revenue_usd": "hotel_revenues.misc_income.gross",
            "p_and_l_usali.total_revenues_usd": "hotel_revenues.total_revenue_amount",
            "p_and_l_usali.rooms.expense_usd": "operating_costs.rooms_dept.amount",
            "p_and_l_usali.food_and_beverage.expense_usd": "operating_costs.fb_dept.amount",
            "p_and_l_usali.other_operated_departments.expense_usd": "operating_costs.other_dept.amount",
            "p_and_l_usali.administrative_and_general.expense_usd": "operating_costs.a_and_g.amount",
            "p_and_l_usali.sales_and_marketing.expense_usd": "operating_costs.sales_marketing.amount",
            "p_and_l_usali.property_operations_and_maintenance.expense_usd": "operating_costs.repairs_maintenance.amount",
            "p_and_l_usali.utilities.expense_usd": "operating_costs.utilities.amount",
            "p_and_l_usali.information_and_telecom.expense_usd": "operating_costs.information_telecom.amount",
            "p_and_l_usali.gross_operating_profit_usd": "rollups.gop_amount",
            "p_and_l_usali.management_fees_usd": "fees.management_fee_amount",
            "p_and_l_usali.ffe_replacement_reserve_usd": "fees.ffe_reserve_amount",
            "p_and_l_usali.non_operating.property_and_other_taxes_usd": "non_operating.property_tax_amount",
            "p_and_l_usali.non_operating.insurance_usd": "non_operating.insurance_amount",
            "ttm_summary_per_om.revpar_usd": "kpis.revpar_amount",
            "ttm_summary_per_om.adr_usd": "kpis.adr_amount",
            "ttm_summary_per_om.occupancy_pct": "kpis.occupancy_amount",
        }
    else:
        raise ValueError(f"unknown namespace_style: {namespace_style}")

    new_fields = []
    for f in base["fields"]:
        name = f.get("field_name")
        new_name = path_map.get(name, name) if isinstance(name, str) else name
        new_f = dict(f)
        new_f["field_name"] = new_name
        new_fields.append(new_f)
    base["fields"] = new_fields
    base["agent_version"] = f"v4-synthetic-{namespace_style}"
    return base


async def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if os.environ.get("ANTHROPIC_API_KEY"):
        log.info("ANTHROPIC_API_KEY set — running real extractor (may take 1-2 min)")
        # Two consecutive runs of the SAME source to capture LLM drift.
        for source, doc_type, name in (
            (T12_SOURCE, "T12", "anglers_t12"),
            (ANNUAL_SOURCE, "PNL", "anglers_annual"),
        ):
            log.info("extracting %s as %s", source.name, doc_type)
            run1 = await _run_real_extractor(source, doc_type)
            (OUTPUT_DIR / f"live_extraction_{name}.json").write_text(
                json.dumps(run1, indent=2)
            )
            log.info("wrote run1 → live_extraction_%s.json (fields=%d)", name, len(run1["fields"]))
            if name == "anglers_t12":
                # Re-run T-12 to capture LLM drift.
                run2 = await _run_real_extractor(source, doc_type)
                (OUTPUT_DIR / f"live_extraction_{name}_altrun.json").write_text(
                    json.dumps(run2, indent=2)
                )
                log.info("wrote run2 → live_extraction_%s_altrun.json (fields=%d)", name, len(run2["fields"]))
    else:
        log.warning("ANTHROPIC_API_KEY unset — synthesizing from saved real_payloads "
                    "with cross-namespace perturbation. Re-run with API key for true live capture.")
        src_t12 = FIXTURE_DIR / "real_payloads" / "anglers_t12_real.json"
        src_annual = FIXTURE_DIR / "real_payloads" / "anglers_annual_pnl_real.json"
        if not src_t12.exists() or not src_annual.exists():
            log.error("real_payloads source fixtures missing: %s / %s", src_t12, src_annual)
            sys.exit(1)
        # Primary capture — keep the saved namespace.
        (OUTPUT_DIR / "live_extraction_anglers_t12.json").write_text(
            json.dumps(_synthesize_perturbed(src_t12, "p_and_l_usali"), indent=2)
        )
        (OUTPUT_DIR / "live_extraction_anglers_annual.json").write_text(
            json.dumps(_synthesize_perturbed(src_annual, "p_and_l_usali"), indent=2)
        )
        # Alt-run capture — perturb to a fresh namespace style.
        (OUTPUT_DIR / "live_extraction_anglers_t12_altrun.json").write_text(
            json.dumps(_synthesize_perturbed(src_t12, "pages_financial"), indent=2)
        )
        (OUTPUT_DIR / "live_extraction_anglers_t12_altrun2.json").write_text(
            json.dumps(_synthesize_perturbed(src_t12, "hotel_revenues"), indent=2)
        )
        log.info("wrote 4 perturbed fixtures to %s", OUTPUT_DIR)


if __name__ == "__main__":
    asyncio.run(main())
