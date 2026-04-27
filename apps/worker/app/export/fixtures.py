"""Demo fixtures for the export endpoints.

Until the agent runtime persists EngineOutputs to the database, the
export endpoints load a hard-coded Kimpton Angler dataset reconciled
to ``apps/web/src/lib/mockData.ts.kimptonAnglerOverview`` and
``evals/golden-set/kimpton-angler/expected/{model,memo}.json``.
"""

from __future__ import annotations

from typing import Any


def kimpton_deal() -> dict[str, Any]:
    return {
        "id": "kimpton-angler-2026",
        "name": "Kimpton Angler Hotel",
        "city": "Miami Beach, FL",
        "location": "Miami Beach, FL",
        "brand": "Kimpton",
        "service": "Lifestyle Boutique",
        "keys": 132,
        "year_built": 2015,
    }


def kimpton_model() -> dict[str, Any]:
    """Engine outputs envelope. Mirrors model.json plus chrome the
    Excel/PPTX builders need (deal_name, sources/uses, market comps,
    variance flags)."""
    return {
        "deal_id": "kimpton-angler-2026",
        "deal_name": "Kimpton Angler Hotel",
        "location": "Miami Beach, FL",
        "brand": "Kimpton",
        "keys": 132,
        "model_version": "fondok-engine-1.0",
        "investment_engine": {
            "purchase_price_usd": 36_400_000,
            "price_per_key_usd": 275_758,
            "closing_costs_usd": 728_736,
            "renovation_budget_usd": 5_280_000,
            "renovation_per_key_usd": 40_000,
            "soft_costs_usd": 528_000,
            "contingency_usd": 528_000,
            "working_capital_usd": 500_000,
            "loan_costs_usd": 364_368,
            "total_capital_usd": 43_309_906,
            "total_capital_per_key_usd": 328_105,
            "entry_cap_rate_t12": 0.1148,
            "entry_cap_rate_year1_uw": 0.1293,
            "year1_yield_on_cost": 0.1086,
        },
        "p_and_l_engine_proforma": {
            "year1_period": "2027-01 to 2027-12",
            "lines": [
                {"label": "Room Revenue", "y1": 11120, "y2": 11676, "y3": 12260, "y4": 12873, "y5": 13517, "cagr": 0.05},
                {"label": "F&B Revenue", "y1": 3240, "y2": 3402, "y3": 3572, "y4": 3751, "y5": 3938, "cagr": 0.05},
                {"label": "Other Revenue", "y1": 720, "y2": 756, "y3": 794, "y4": 833, "y5": 875, "cagr": 0.05},
                {"label": "Total Revenue", "y1": 15080, "y2": 15834, "y3": 16626, "y4": 17457, "y5": 18330, "cagr": 0.05, "bold": True},
                {"label": "Operating Expenses", "y1": 9320, "y2": 9660, "y3": 10010, "y4": 10372, "y5": 10745},
                {"label": "Management Fee", "y1": 452, "y2": 475, "y3": 499, "y4": 524, "y5": 550},
                {"label": "FF&E Reserve", "y1": 603, "y2": 633, "y3": 665, "y4": 698, "y5": 733},
                {"label": "Net Operating Income", "y1": 4705, "y2": 5066, "y3": 5452, "y4": 5863, "y5": 6302, "cagr": 0.075, "bold": True},
                {"label": "Debt Service", "y1": 1610, "y2": 1610, "y3": 1610, "y4": 1610, "y5": 1610},
                {"label": "Cash Flow After Debt", "y1": 3095, "y2": 3456, "y3": 3842, "y4": 4253, "y5": 4692, "bold": True},
            ],
        },
        "debt_engine": {
            "loan_amount_usd": 23_683_922,
            "ltv_cost": 0.547,
            "ltv_value": 0.65,
            "ltc": 0.547,
            "interest_rate_pct": 0.0680,
            "amortization_years": 30,
            "term_years": 5,
            "annual_debt_service_usd": 1_610_507,
            "year1_dscr": 1.57,
            "year1_debt_yield": 0.1986,
            "interest_only_period_months": 0,
        },
        "refi_engine": {
            "refi_year": 4,
            "refi_ltv": 0.60,
            "refi_rate_pct": 0.06,
            "refi_term_years": 5,
            "refi_amortization_years": 30,
            "refi_proceeds_usd": 32_000_000,
            "cash_out_to_equity_usd": 9_500_000,
        },
        "cash_flow_engine": {
            "year1_cf_after_debt_usd": 3_095_000,
            "year2_cf_after_debt_usd": 3_456_000,
            "year3_cf_after_debt_usd": 3_842_000,
            "year4_cf_after_debt_usd": 4_253_000,
            "year5_cf_after_debt_usd": 4_692_000,
            "cumulative_cf_5yr_usd": 19_338_000,
        },
        "returns_engine": {
            "hold_years": 5,
            "exit_cap_rate_pct": 0.0700,
            "terminal_noi_usd": 5_120_000,
            "gross_sale_price_usd": 73_142_000,
            "selling_costs_usd": 1_462_840,
            "net_sale_proceeds_usd": 71_679_160,
            "levered_irr": 0.2348,
            "unlevered_irr": 0.1684,
            "equity_multiple": 2.12,
            "year1_cash_on_cash": 0.046,
            "avg_cash_on_cash": 0.0518,
        },
        "partnership_engine": {
            "structure": "GP/LP waterfall",
            "lp_equity_usd": 17_663_386,
            "gp_equity_usd": 1_962_598,
            "total_equity_usd": 19_625_984,
            "lp_pref_pct": 0.08,
            "gp_promote_tier_1_pct": 0.20,
            "gp_promote_tier_1_irr_hurdle": 0.12,
            "gp_promote_tier_2_pct": 0.30,
            "gp_promote_tier_2_irr_hurdle": 0.18,
            "lp_irr_after_promote": 0.1820,
            "gp_irr_after_promote": 0.4870,
            "lp_equity_multiple": 1.85,
            "gp_equity_multiple": 4.45,
        },
        "scenario_outputs": [
            {"name": "Downside", "irr": 0.148, "unlevered_irr": 0.092, "multiple": 1.65, "avg_coc": 0.032, "exit_value_usd": 58_200_000},
            {"name": "Base Case", "irr": 0.2348, "unlevered_irr": 0.1684, "multiple": 2.12, "avg_coc": 0.046, "exit_value_usd": 73_142_000, "base": True},
            {"name": "Upside", "irr": 0.3120, "unlevered_irr": 0.2210, "multiple": 2.58, "avg_coc": 0.061, "exit_value_usd": 84_500_000},
        ],
        # UI / chrome — Sources & Uses, comps, variance
        "sources": [
            {"label": "Senior Debt", "amount": 23_683_922, "pct": 0.547},
            {"label": "Equity", "amount": 19_625_984, "pct": 0.453},
        ],
        "uses": [
            {"label": "Purchase Price", "amount": 36_436_802},
            {"label": "Closing Costs", "amount": 728_736},
            {"label": "Renovation", "amount": 5_280_000},
            {"label": "Working Capital", "amount": 500_000},
            {"label": "Loan Costs", "amount": 364_368},
        ],
        "variance_flags": [
            {
                "flag_id": "VF-001",
                "severity": "CRITICAL",
                "metric": "NOI",
                "broker_value": 5_200_000,
                "t12_value": 4_181_000,
                "variance_pct": -0.1960,
                "recommended_action": "Apply T-12 actual NOI as base case in underwriting; treat broker proforma as upside case only after PIP completion in Year 2.",
            },
            {
                "flag_id": "VF-002",
                "severity": "WARN",
                "metric": "Occupancy",
                "broker_value": 0.80,
                "t12_value": 0.762,
                "variance_pct_pts": 3.8,
                "recommended_action": "Cap base case occupancy at 77.5 percent (T-12 plus 130 bps) reflecting modest market growth.",
            },
            {
                "flag_id": "VF-003",
                "severity": "WARN",
                "metric": "ADR",
                "broker_value": 395.00,
                "t12_value": 385.00,
                "variance_pct": 0.026,
                "recommended_action": "Acceptable. Underwrite ADR uplift to $395 in Year 2 post-PIP.",
            },
            {
                "flag_id": "VF-004",
                "severity": "INFO",
                "metric": "Insurance / Key",
                "value": 3803,
                "threshold_min": 2500,
                "threshold_max": 8000,
                "recommended_action": "Acceptable for Miami Beach coastal exposure.",
            },
            {
                "flag_id": "VF-005",
                "severity": "WARN",
                "metric": "Utilities / Key",
                "value": 5136,
                "threshold_min": 800,
                "threshold_max": 2500,
                "recommended_action": "Validate against energy audit; PIP retrofit may yield 15-20% savings.",
            },
        ],
        "market_comps": [
            {"name": "The Setai Miami Beach", "keys": 130, "date": "Aug 2025", "price": "$245M", "per_key": "$1.9M", "cap": "4.8%", "buyer": "Ashkenazy Acquisition"},
            {"name": "Nautilus by Arlo", "keys": 250, "date": "May 2025", "price": "$98M", "per_key": "$392k", "cap": "6.2%", "buyer": "Private"},
            {"name": "Loews Miami Beach", "keys": 790, "date": "Mar 2025", "price": "$520M", "per_key": "$658k", "cap": "5.4%", "buyer": "Institutional"},
            {"name": "W South Beach", "keys": 408, "date": "Feb 2025", "price": "$425M", "per_key": "$1.04M", "cap": "5.1%", "buyer": "PE Fund"},
            {"name": "SLS South Beach", "keys": 140, "date": "Dec 2024", "price": "$95M", "per_key": "$679k", "cap": "6.0%", "buyer": "REIT"},
            {"name": "Cadillac Hotel & Beach Club", "keys": 357, "date": "Nov 2024", "price": "$130M", "per_key": "$364k", "cap": "6.8%", "buyer": "Institutional"},
        ],
        "market": {
            "kpis": {
                "Submarket": "Miami Beach / South Beach, FL",
                "RevPAR (TTM)": "$238",
                "ADR (TTM)": "$312",
                "Occupancy": "76.2%",
                "RGI": "112.2",
                "Supply Pipeline": "414 rooms (2.2%)",
                "Demand Growth": "4.8%",
                "Supply Growth": "1.2%",
                "Comp Set Avg Cap": "6.1%",
            },
        },
    }


def kimpton_memo() -> dict[str, Any]:
    return {
        "deal_id": "kimpton-angler-2026",
        "memo_version": "1.0",
        "drafted_at": "2026-04-19T14:42:00Z",
        "drafted_by": "Fondok AI - Investment Memo Engine",
        "header": {
            "title": "Investment Committee Memorandum",
            "subject_property": "Kimpton Angler Hotel",
            "location": "Miami Beach, FL",
            "deal_stage": "Under NDA",
            "recommendation": "PROCEED TO LOI",
            "ic_date": "TBD",
            "lead_analyst": "Eshan Mehta",
            "workspace": "Brookfield Real Estate",
        },
        "sections": [
            {
                "section_id": "executive_summary",
                "title": "Executive Summary",
                "body": "Kimpton Angler is a compelling value-add acquisition in the South Beach submarket at $36.4M ($276K/key) — a 22% discount to recent comparable lifestyle-tier transactions. The basis provides meaningful downside protection and supports a 23.5% levered IRR over a 5-year hold.",
            },
            {
                "section_id": "investment_thesis",
                "title": "Investment Thesis",
                "body": "The Brickell-adjacent location captures both leisure and corporate demand, and Kimpton brand affiliation commands a 14% ADR premium versus independent boutique competitors. STR data shows the asset trailing the comp set on RGI by 4 points relative to top performers, suggesting near-term yield management upside.",
            },
            {
                "section_id": "recommendation",
                "title": "Recommendation",
                "body": "We recommend proceeding to LOI at the current ask. PIP requirement of $5.3M ($40K/key) is in line with brand standards refresh and is captured in Year 1 capital plan. Senior debt sized at 65% LTC delivers 1.57x DSCR with comfortable covenant headroom.",
            },
            {
                "section_id": "key_insights",
                "title": "Key Insights",
                "items": [
                    {"title": "Prime South Beach Location", "body": "Walking distance to ocean and Lincoln Road; positioned for both leisure compression weekends and corporate weekday demand from Brickell."},
                    {"title": "Lifestyle Brand Premium", "body": "Kimpton affiliation delivers a 14% ADR premium versus independent boutique competitors with comparable amenity packages."},
                    {"title": "Seasonal Concentration", "body": "Q1 RevPAR runs 80% above Q3 trough — strong seasonal hedging in revenue model is critical for stable distributions."},
                    {"title": "Attractive Basis", "body": "$276K/key represents a 22% discount to replacement cost and 18% discount to last-trade lifestyle-tier comp set."},
                ],
            },
            {
                "section_id": "risk_assessment",
                "title": "Risk Assessment",
                "overall_risk_score": 24,
                "overall_risk_tier": "Low Risk",
                "subscores": [
                    {"name": "RevPAR Volatility", "tier": "Low Risk", "score": 32},
                    {"name": "Market Supply Risk", "tier": "Medium Risk", "score": 38},
                    {"name": "Operator Risk", "tier": "Low Risk", "score": 18},
                    {"name": "Capital Needs", "tier": "Low Risk", "score": 28},
                ],
            },
            {
                "section_id": "variance_disclosure",
                "title": "Variance Disclosure",
                "body": "Broker proforma NOI of $5.20M is materially above T-12 actual of $4.18M (-19.6 percent variance, CRITICAL flag). Underwriting uses T-12 as base, with broker proforma achievable only after PIP completion in Year 2.",
            },
        ],
        "appendix": {
            "documents_reviewed": [
                "Offering_Memorandum_Final.pdf",
                "T12_FinancialStatement.xlsx",
                "STR_MarketReport_Q1.pdf",
                "Monthly_PL_2024_2025.xlsx",
                "PIP_Estimate_2026.pdf",
                "Lender_Term_Sheet.pdf",
                "STR_Comp_Set_Detail.pdf",
                "Property_Survey_2024.pdf",
            ],
            "engines_run": ["investment", "p_and_l", "debt", "cash_flow", "returns", "partnership"],
            "ai_confidence": 0.87,
        },
    }


def load_demo_payload(deal_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return (deal, model, memo) for the export endpoints.

    Today every deal_id resolves to the Kimpton Angler fixture so the
    exports work end-to-end before the DB-backed fetch lands.
    """
    deal = kimpton_deal()
    deal["id"] = deal_id  # echo the requested id back
    return deal, kimpton_model(), kimpton_memo()


__all__ = [
    "kimpton_deal",
    "kimpton_memo",
    "kimpton_model",
    "load_demo_payload",
]
