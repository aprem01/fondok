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
        # ────────────── Wave 2 artifacts (W3.4 memo refresh) ─────────────
        # These feed the new IC memo sections (revenue mix, renovation
        # plan, capex 3-bucket, op-ratio provenance, sensitivity grid,
        # max-price, historical baseline walk, LOI appendix). Each block
        # mirrors the relevant engine's dataclass shape so the memo
        # template reads them straight through ``_aggregate_wave2_for_memo``.
        "segments_by_year": [
            {
                "year": year,
                "segment_breakdown": [
                    {
                        "name": "transient_bar",
                        "mix_pct": 0.40,
                        "occupied_rooms": 14_658.0 * (1.0 + 0.03 * (year - 1)),
                        "adr": 412.0 * (1.05 ** (year - 1)),
                        "channel_cost_pct": 0.02,
                        "gross_revenue": 6_039_096 * (1.05 ** (year - 1)),
                        "net_revenue": 5_918_314 * (1.05 ** (year - 1)),
                    },
                    {
                        "name": "transient_ota",
                        "mix_pct": 0.25,
                        "occupied_rooms": 9_161.25 * (1.0 + 0.02 * (year - 1)),
                        "adr": 365.0 * (1.04 ** (year - 1)),
                        "channel_cost_pct": 0.18,
                        "gross_revenue": 3_343_856 * (1.04 ** (year - 1)),
                        "net_revenue": 2_741_962 * (1.04 ** (year - 1)),
                    },
                    {
                        "name": "corporate",
                        "mix_pct": 0.20,
                        "occupied_rooms": 7_329.0 * (1.0 + 0.025 * (year - 1)),
                        "adr": 295.0 * (1.045 ** (year - 1)),
                        "channel_cost_pct": 0.05,
                        "gross_revenue": 2_162_055 * (1.045 ** (year - 1)),
                        "net_revenue": 2_053_952 * (1.045 ** (year - 1)),
                    },
                    {
                        "name": "group",
                        "mix_pct": 0.10,
                        "occupied_rooms": 3_664.5 * (1.0 + 0.025 * (year - 1)),
                        "adr": 248.0 * (1.04 ** (year - 1)),
                        "channel_cost_pct": 0.08,
                        "gross_revenue": 908_796 * (1.04 ** (year - 1)),
                        "net_revenue": 836_092 * (1.04 ** (year - 1)),
                    },
                    {
                        "name": "contract",
                        "mix_pct": 0.05,
                        "occupied_rooms": 1_832.25,
                        "adr": 195.0 * (1.02 ** (year - 1)),
                        "channel_cost_pct": 0.0,
                        "gross_revenue": 357_289 * (1.02 ** (year - 1)),
                        "net_revenue": 357_289 * (1.02 ** (year - 1)),
                    },
                ],
            }
            for year in range(1, 6)
        ],
        "pip_displacement": {
            "closure_strategy": "rolling",
            "pct_rooms_offline_by_month": [
                0.25, 0.40, 0.40, 0.35, 0.30, 0.20,
                0.15, 0.10, 0.05, 0.0, 0.0, 0.0,
            ],
            "brand": "Independent",
            "revpar_index_post_reno": 1.08,
            "occupancy_recovery_months": 6,
            "y1_displacement_usd": 1_180_000,
            "y2_recovery_curve": [0.82, 0.88, 0.93, 0.97],
        },
        "capex_schedule": [
            {"year": 1, "pip_usd": 4_224_000, "non_pip_usd": 603_200,
             "roi_investment_usd": 0, "roi_noi_lift_usd": 0,
             "total_capex_usd": 4_827_200},
            {"year": 2, "pip_usd": 1_056_000, "non_pip_usd": 633_360,
             "roi_investment_usd": 750_000, "roi_noi_lift_usd": 0,
             "total_capex_usd": 2_439_360},
            {"year": 3, "pip_usd": 0, "non_pip_usd": 665_040,
             "roi_investment_usd": 0, "roi_noi_lift_usd": 90_000,
             "total_capex_usd": 665_040},
            {"year": 4, "pip_usd": 0, "non_pip_usd": 698_280,
             "roi_investment_usd": 0, "roi_noi_lift_usd": 180_000,
             "total_capex_usd": 698_280},
            {"year": 5, "pip_usd": 0, "non_pip_usd": 733_200,
             "roi_investment_usd": 0, "roi_noi_lift_usd": 180_000,
             "total_capex_usd": 733_200},
        ],
        "op_ratio_provenance": {
            "lines": [
                {"field": "Rooms Dept Exp %",
                 "value": 0.245, "source": "t12_actual",
                 "document_id": "T12_FinancialStatement.xlsx"},
                {"field": "F&B Dept Exp %",
                 "value": 0.741, "source": "t12_actual",
                 "document_id": "T12_FinancialStatement.xlsx"},
                {"field": "A&G %",
                 "value": 0.082, "source": "portfolio_pnl",
                 "document_id": "Portfolio_2025_Lifestyle.xlsx"},
                {"field": "Sales & Marketing %",
                 "value": 0.061, "source": "cbre_horizons",
                 "document_id": "CBRE_Horizons_2025_UpperUpscale.pdf"},
                {"field": "Property Ops & Maintenance %",
                 "value": 0.046, "source": "cbre_horizons",
                 "document_id": "CBRE_Horizons_2025_UpperUpscale.pdf"},
                {"field": "Utilities %",
                 "value": 0.038, "source": "pnl_benchmark",
                 "document_id": None},
                {"field": "Management Fee %",
                 "value": 0.030, "source": "analyst_override",
                 "document_id": "Override_Note_2026_04_19.txt"},
                {"field": "Property Tax / Key",
                 "value": 4200.0, "source": "t12_actual",
                 "document_id": "T12_FinancialStatement.xlsx"},
            ],
        },
        "sensitivity_grid": {
            "base_exit_cap_pct": 0.07,
            "base_stabilized_noi": 4_705_000,
            "target_irr": 0.15,
            "cells": [
                # NOI 1.15x row (top — green leaning)
                {"exit_cap_pct": 0.06, "noi_multiplier": 1.15,
                 "levered_irr": 0.30, "equity_multiple": 2.58,
                 "going_in_cap_rate": 0.149, "dscr_y1": 1.80,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.065, "noi_multiplier": 1.15,
                 "levered_irr": 0.27, "equity_multiple": 2.40,
                 "going_in_cap_rate": 0.149, "dscr_y1": 1.80,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.07, "noi_multiplier": 1.15,
                 "levered_irr": 0.245, "equity_multiple": 2.22,
                 "going_in_cap_rate": 0.149, "dscr_y1": 1.80,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.075, "noi_multiplier": 1.15,
                 "levered_irr": 0.225, "equity_multiple": 2.10,
                 "going_in_cap_rate": 0.149, "dscr_y1": 1.80,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.08, "noi_multiplier": 1.15,
                 "levered_irr": 0.205, "equity_multiple": 2.00,
                 "going_in_cap_rate": 0.149, "dscr_y1": 1.80,
                 "breaches_dscr_floor": False},
                # NOI 1.075x row
                {"exit_cap_pct": 0.06, "noi_multiplier": 1.075,
                 "levered_irr": 0.265, "equity_multiple": 2.30,
                 "going_in_cap_rate": 0.139, "dscr_y1": 1.68,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.065, "noi_multiplier": 1.075,
                 "levered_irr": 0.245, "equity_multiple": 2.20,
                 "going_in_cap_rate": 0.139, "dscr_y1": 1.68,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.07, "noi_multiplier": 1.075,
                 "levered_irr": 0.225, "equity_multiple": 2.10,
                 "going_in_cap_rate": 0.139, "dscr_y1": 1.68,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.075, "noi_multiplier": 1.075,
                 "levered_irr": 0.205, "equity_multiple": 2.00,
                 "going_in_cap_rate": 0.139, "dscr_y1": 1.68,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.08, "noi_multiplier": 1.075,
                 "levered_irr": 0.185, "equity_multiple": 1.90,
                 "going_in_cap_rate": 0.139, "dscr_y1": 1.68,
                 "breaches_dscr_floor": False},
                # NOI 1.0x row (base case)
                {"exit_cap_pct": 0.06, "noi_multiplier": 1.0,
                 "levered_irr": 0.245, "equity_multiple": 2.20,
                 "going_in_cap_rate": 0.129, "dscr_y1": 1.57,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.065, "noi_multiplier": 1.0,
                 "levered_irr": 0.235, "equity_multiple": 2.15,
                 "going_in_cap_rate": 0.129, "dscr_y1": 1.57,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.07, "noi_multiplier": 1.0,
                 "levered_irr": 0.2348, "equity_multiple": 2.12,
                 "going_in_cap_rate": 0.129, "dscr_y1": 1.57,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.075, "noi_multiplier": 1.0,
                 "levered_irr": 0.185, "equity_multiple": 1.85,
                 "going_in_cap_rate": 0.129, "dscr_y1": 1.57,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.08, "noi_multiplier": 1.0,
                 "levered_irr": 0.165, "equity_multiple": 1.75,
                 "going_in_cap_rate": 0.129, "dscr_y1": 1.57,
                 "breaches_dscr_floor": False},
                # NOI 0.925x row (downside)
                {"exit_cap_pct": 0.06, "noi_multiplier": 0.925,
                 "levered_irr": 0.205, "equity_multiple": 1.95,
                 "going_in_cap_rate": 0.120, "dscr_y1": 1.45,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.065, "noi_multiplier": 0.925,
                 "levered_irr": 0.185, "equity_multiple": 1.85,
                 "going_in_cap_rate": 0.120, "dscr_y1": 1.45,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.07, "noi_multiplier": 0.925,
                 "levered_irr": 0.165, "equity_multiple": 1.75,
                 "going_in_cap_rate": 0.120, "dscr_y1": 1.45,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.075, "noi_multiplier": 0.925,
                 "levered_irr": 0.140, "equity_multiple": 1.65,
                 "going_in_cap_rate": 0.120, "dscr_y1": 1.45,
                 "breaches_dscr_floor": False},
                {"exit_cap_pct": 0.08, "noi_multiplier": 0.925,
                 "levered_irr": 0.115, "equity_multiple": 1.55,
                 "going_in_cap_rate": 0.120, "dscr_y1": 1.45,
                 "breaches_dscr_floor": False},
                # NOI 0.85x row (deep downside — DSCR breach)
                {"exit_cap_pct": 0.06, "noi_multiplier": 0.85,
                 "levered_irr": 0.165, "equity_multiple": 1.70,
                 "going_in_cap_rate": 0.110, "dscr_y1": 0.95,
                 "breaches_dscr_floor": True},
                {"exit_cap_pct": 0.065, "noi_multiplier": 0.85,
                 "levered_irr": 0.140, "equity_multiple": 1.60,
                 "going_in_cap_rate": 0.110, "dscr_y1": 0.95,
                 "breaches_dscr_floor": True},
                {"exit_cap_pct": 0.07, "noi_multiplier": 0.85,
                 "levered_irr": 0.110, "equity_multiple": 1.50,
                 "going_in_cap_rate": 0.110, "dscr_y1": 0.95,
                 "breaches_dscr_floor": True},
                {"exit_cap_pct": 0.075, "noi_multiplier": 0.85,
                 "levered_irr": 0.080, "equity_multiple": 1.35,
                 "going_in_cap_rate": 0.110, "dscr_y1": 0.95,
                 "breaches_dscr_floor": True},
                {"exit_cap_pct": 0.08, "noi_multiplier": 0.85,
                 "levered_irr": 0.050, "equity_multiple": 1.25,
                 "going_in_cap_rate": 0.110, "dscr_y1": 0.95,
                 "breaches_dscr_floor": True},
            ],
            "breakeven_exit_cap_pct": 0.0782,
            "breakeven_noi_multiplier": 0.872,
        },
        "max_price": {
            "target_irr": 0.15,
            "target_em": 1.8,
            "max_price_for_irr": 42_800_000,
            "max_price_for_em": 44_100_000,
            "binding_constraint": "irr",
            "final_price_per_key": 324_242,
            "iters": 34,
        },
        "historical_baseline": {
            "look_back_years": 5,
            "coverage_pct": 0.6,
            "gaps": [2020],
            "years": [
                {"fiscal_year": 2022,
                 "occupancy": 0.682, "adr": 348.0, "revpar": 237.0,
                 "rooms_revenue": 11_420_000, "fnb_revenue": 2_980_000,
                 "other_revenue": 640_000, "total_revenue": 15_040_000,
                 "gop": 6_180_000, "noi": 4_140_000,
                 "source_document_ids": ["Monthly_PL_2022.xlsx"]},
                {"fiscal_year": 2023,
                 "occupancy": 0.735, "adr": 372.0, "revpar": 273.0,
                 "rooms_revenue": 13_140_000, "fnb_revenue": 3_140_000,
                 "other_revenue": 690_000, "total_revenue": 16_970_000,
                 "gop": 6_950_000, "noi": 4_650_000,
                 "source_document_ids": ["Monthly_PL_2023.xlsx"]},
                {"fiscal_year": 2024,
                 "occupancy": 0.762, "adr": 385.0, "revpar": 294.0,
                 "rooms_revenue": 14_180_000, "fnb_revenue": 3_240_000,
                 "other_revenue": 720_000, "total_revenue": 18_140_000,
                 "gop": 7_280_000, "noi": 4_181_000,
                 "source_document_ids": ["T12_FinancialStatement.xlsx"]},
            ],
            "walk": [
                {"line": "rooms_revenue", "year": 2023,
                 "value": 13_140_000, "yoy_abs": 1_720_000, "yoy_pct": 0.1506},
                {"line": "noi", "year": 2023,
                 "value": 4_650_000, "yoy_abs": 510_000, "yoy_pct": 0.1232},
                {"line": "total_revenue", "year": 2024,
                 "value": 18_140_000, "yoy_abs": 1_170_000, "yoy_pct": 0.0689},
                {"line": "noi", "year": 2024,
                 "value": 4_181_000, "yoy_abs": -469_000, "yoy_pct": -0.1009},
            ],
        },
        # ────────────── Wave 3 artifacts (W4.2 excel refresh) ─────────────
        # comp_sales: derived exit-cap from 6 transactions (W3.1).
        "comp_sales": {
            "transactions": [
                {
                    "property_name": "The Setai Miami Beach", "city": "Miami Beach",
                    "state": "FL", "sale_date": "2025-08-15", "keys": 130,
                    "sale_price_usd": 245_000_000, "sale_price_per_key_usd": 1_884_615,
                    "noi_usd": 11_760_000, "cap_rate_pct": 4.8,
                    "chain_scale": "luxury", "brand_family": None,
                    "source_document_id": "OM_setai.pdf", "transaction_id": "tx-1",
                    "note": None, "excluded": False,
                },
                {
                    "property_name": "Nautilus by Arlo", "city": "Miami Beach",
                    "state": "FL", "sale_date": "2025-05-10", "keys": 250,
                    "sale_price_usd": 98_000_000, "sale_price_per_key_usd": 392_000,
                    "noi_usd": 6_076_000, "cap_rate_pct": 6.2,
                    "chain_scale": "upper-upscale", "brand_family": "Arlo",
                    "source_document_id": "OM_nautilus.pdf", "transaction_id": "tx-2",
                    "note": None, "excluded": False,
                },
                {
                    "property_name": "Loews Miami Beach", "city": "Miami Beach",
                    "state": "FL", "sale_date": "2025-03-20", "keys": 790,
                    "sale_price_usd": 520_000_000, "sale_price_per_key_usd": 658_228,
                    "noi_usd": 28_080_000, "cap_rate_pct": 5.4,
                    "chain_scale": "upscale", "brand_family": "Loews",
                    "source_document_id": "OM_loews.pdf", "transaction_id": "tx-3",
                    "note": None, "excluded": False,
                },
                {
                    "property_name": "W South Beach", "city": "Miami Beach",
                    "state": "FL", "sale_date": "2025-02-18", "keys": 408,
                    "sale_price_usd": 425_000_000, "sale_price_per_key_usd": 1_041_667,
                    "noi_usd": 21_675_000, "cap_rate_pct": 5.1,
                    "chain_scale": "luxury", "brand_family": "Marriott",
                    "source_document_id": "OM_w.pdf", "transaction_id": "tx-4",
                    "note": None, "excluded": False,
                },
                {
                    "property_name": "SLS South Beach", "city": "Miami Beach",
                    "state": "FL", "sale_date": "2024-12-05", "keys": 140,
                    "sale_price_usd": 95_000_000, "sale_price_per_key_usd": 678_571,
                    "noi_usd": 5_700_000, "cap_rate_pct": 6.0,
                    "chain_scale": "luxury", "brand_family": "Ennismore",
                    "source_document_id": "OM_sls.pdf", "transaction_id": "tx-5",
                    "note": None, "excluded": False,
                },
                {
                    "property_name": "Cadillac Hotel & Beach Club", "city": "Miami Beach",
                    "state": "FL", "sale_date": "2024-11-12", "keys": 357,
                    "sale_price_usd": 130_000_000, "sale_price_per_key_usd": 364_146,
                    "noi_usd": 8_840_000, "cap_rate_pct": 6.8,
                    "chain_scale": "upper-upscale", "brand_family": "Marriott",
                    "source_document_id": "OM_cadillac.pdf", "transaction_id": "tx-6",
                    "note": "Excluded — far outside chain scale",
                    "excluded": True,
                },
            ],
            "total_count": 6,
            "derived_cap_rate_median": 5.55,
            "derived_cap_rate_weighted": 5.32,
            "derived_cap_rate_method": "weighted",
            "weighting_notes": [
                "Recency weight: 0.7 * recency_score",
                "Market weight: 0.2 * MSA match (Miami Beach)",
                "Chain weight: 0.1 * chain_scale match (luxury)",
            ],
            "coverage_quality": "medium",
            "subject_market": "Miami Beach",
            "subject_chain_scale": "luxury",
            "lookback_years": 5,
        },
        # str_forecast: 24 historical + 24 forecast months × 3 scenarios (W3.3).
        "str_forecast": {
            "deal_id": "kimpton-angler-2026",
            "coverage_quality": "high",
            "historical_months": [
                {
                    "period": f"{(2024 + (i // 12))}-{((i % 12) + 1):02d}",
                    "occupancy": 0.74 + 0.005 * (i % 6 - 3),
                    "adr": 372 + 1.2 * i,
                    "revpar": (0.74 + 0.005 * (i % 6 - 3)) * (372 + 1.2 * i),
                    "comp_set_revpar": (0.74 + 0.005 * (i % 6 - 3)) * (372 + 1.2 * i) / 0.96,
                    "revpar_index": 0.96,
                    "is_historical": True,
                }
                for i in range(24)
            ],
            "forecast_months": {
                "downside": [
                    {
                        "period": f"{(2026 + (i // 12))}-{((i % 12) + 1):02d}",
                        "occupancy": 0.68,
                        "adr": 380 - 0.5 * i,
                        "revpar": 0.68 * (380 - 0.5 * i),
                        "comp_set_revpar": (0.68 * (380 - 0.5 * i)) / 0.92,
                        "revpar_index": 0.92,
                        "is_historical": False,
                    }
                    for i in range(24)
                ],
                "base": [
                    {
                        "period": f"{(2026 + (i // 12))}-{((i % 12) + 1):02d}",
                        "occupancy": 0.76,
                        "adr": 395 + 0.8 * i,
                        "revpar": 0.76 * (395 + 0.8 * i),
                        "comp_set_revpar": 0.76 * (395 + 0.8 * i),
                        "revpar_index": 1.00,
                        "is_historical": False,
                    }
                    for i in range(24)
                ],
                "upside": [
                    {
                        "period": f"{(2026 + (i // 12))}-{((i % 12) + 1):02d}",
                        "occupancy": 0.81,
                        "adr": 412 + 1.5 * i,
                        "revpar": 0.81 * (412 + 1.5 * i),
                        "comp_set_revpar": 0.81 * (412 + 1.5 * i) / 1.06,
                        "revpar_index": 1.06,
                        "is_historical": False,
                    }
                    for i in range(24)
                ],
            },
            "scenario_settings": [
                {"name": "downside", "revpar_cagr_pct": -0.02,
                 "revpar_index_target": 0.92, "occupancy_floor": 0.55,
                 "adr_floor": 0.80, "notes": ["Recessionary cycle"]},
                {"name": "base", "revpar_cagr_pct": 0.025,
                 "revpar_index_target": 1.00, "occupancy_floor": 0.60,
                 "adr_floor": 0.88, "notes": ["Mid-cycle"]},
                {"name": "upside", "revpar_cagr_pct": 0.05,
                 "revpar_index_target": 1.06, "occupancy_floor": 0.65,
                 "adr_floor": 0.92, "notes": ["Post-PIP RevPAR lift"]},
            ],
        },
        # named_scenarios: saved what-if scenarios (W3.2).
        "named_scenarios": [
            {
                "name": "Base Case",
                "is_base": True,
                "kpis": {
                    "levered_irr": 0.2348, "equity_multiple": 2.12,
                    "year1_noi_usd": 4_705_000, "stabilized_noi_usd": 6_302_000,
                    "exit_cap_pct": 0.07, "year1_dscr": 1.57,
                },
            },
            {
                "name": "PIP Skinny",
                "is_base": False,
                "description": "$3M PIP only — defer non-PIP FF&E to Y3.",
                "kpis": {
                    "levered_irr": 0.2580, "equity_multiple": 2.28,
                    "year1_noi_usd": 4_950_000, "stabilized_noi_usd": 6_180_000,
                    "exit_cap_pct": 0.07, "year1_dscr": 1.69,
                },
            },
            {
                "name": "Aggressive Exit",
                "is_base": False,
                "description": "6.25% exit cap (vs 7% base).",
                "kpis": {
                    "levered_irr": 0.2710, "equity_multiple": 2.35,
                    "year1_noi_usd": 4_705_000, "stabilized_noi_usd": 6_302_000,
                    "exit_cap_pct": 0.0625, "year1_dscr": 1.57,
                },
            },
            {
                "name": "Downside Stress",
                "is_base": False,
                "description": "10% RevPAR haircut + 50bp cap expansion.",
                "kpis": {
                    "levered_irr": 0.1180, "equity_multiple": 1.55,
                    "year1_noi_usd": 4_180_000, "stabilized_noi_usd": 5_400_000,
                    "exit_cap_pct": 0.075, "year1_dscr": 1.32,
                },
            },
        ],
        "loi_draft": {
            "asset_name": "Kimpton Angler Hotel",
            "asset_address": "660 Washington Ave, Miami Beach, FL 33139",
            "rooms": 132,
            "proposed_price": 42_800_000,
            "proposed_price_per_key": 324_242,
            "binding_constraint": "irr",
            "rendered_markdown": (
                "# LETTER OF INTENT\n\n"
                "**To:** [Seller TBD]\n"
                "**From:** [Buyer Entity TBD]\n"
                "**Re:** Proposed Acquisition of Kimpton Angler Hotel\n"
                "**Property Address:** 660 Washington Ave, Miami Beach, FL 33139\n\n"
                "## 1. Property\n\n"
                "The Property consists of the Kimpton Angler Hotel comprising "
                "132 guest rooms, together with all related fixtures, FF&E, "
                "intangibles, operating licenses, and books and records.\n\n"
                "## 2. Purchase Price\n\n"
                "Buyer offers a purchase price of **$42,800,000** "
                "($324,242 per key), payable in cash at closing.\n\n"
                "## 3. Earnest Money\n\n"
                "Within three (3) business days of mutual execution of a "
                "Purchase and Sale Agreement, Buyer will deposit earnest "
                "money equal to 1.0% of the purchase price ($428,000).\n\n"
                "## 4. Due Diligence\n\n"
                "Buyer will have 30 days from PA execution to conduct "
                "customary due diligence.\n\n"
                "## 5. Closing\n\n"
                "Closing shall occur on or before 60 days following PA "
                "execution, subject to satisfaction of all closing "
                "conditions.\n\n"
                "## 6. Contingencies\n\n"
                "Buyer's obligation to close is contingent upon:\n"
                "   - Satisfactory Phase I ESA\n"
                "   - Satisfactory PIP estimate\n"
                "   - Title commitment review\n\n"
                "## 7. Validity\n\n"
                "This offer is valid until **10 business days from issuance**."
            ),
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
