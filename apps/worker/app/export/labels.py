"""Canonical NOI row labels shared by the export builders.

The bare word "NOI" means NOI **before** the FF&E replacement reserve
(founder decision — FON-59 #1 / FON-67 #2): the engine field
``expense.years[].noi_institutional`` / registry concept ``ebitda``. The
after-reserve figure ``expense.years[].noi`` (registry concept ``noi``) is
"Cash NOI"; Debt uses it for DSCR / debt yield and Returns capitalises it at
the exit cap.

The proforma carries BOTH rows so the workbook foots (FON-54 #8):

    Total Revenue less Operating Expenses less the Management Fee
        = NOI (before FF&E reserve)
        less the FF&E Reserve
        = Cash NOI (after FF&E reserve)

These are display strings only — nothing here changes an engine value.
"""

from __future__ import annotations

#: NOI before the FF&E reserve — the headline / entry-cap basis.
NOI_BEFORE_RESERVE = "NOI (before FF&E reserve)"

#: NOI net of the FF&E reserve.
CASH_NOI = "Cash NOI (after FF&E reserve)"

#: Pre-upgrade ``engine_outputs`` rows persist ``noi_institutional: null``
#: (apps/worker/app/engines/expense.py). The value then falls back to ``noi``,
#: and the label must NOT assert "before FF&E reserve" about it.
NOI_BASIS_UNCONFIRMED = "NOI (basis unconfirmed — pre-upgrade run)"

#: Every label that can carry the headline (before-reserve) NOI row, newest
#: first. Lookups over ``p_and_l_engine_proforma["lines"]`` should match this.
#: The export payload is assembled fresh from the engine snapshot on every
#: request, so there is no older persisted payload to accommodate.
NOI_HEADLINE_LABELS: tuple[str, ...] = (
    NOI_BEFORE_RESERVE,
    NOI_BASIS_UNCONFIRMED,
)

#: Printed beside every exported debt figure whose rate rides on the debt
#: engine's flat SOFR default (a floating tranche with no analyst index
#: assumption). Same words the Debt tab shows next to the Benchmark row.
BENCHMARK_DEFAULT_NOTE = (
    "Fondok’s flat SOFR assumption, not market data — enter the index you "  # noqa: RUF001 — the Debt tab's exact copy
    "underwrite to"
)

#: Op-Ratio Provenance note when every resolved ratio is a hotel-type seed.
OP_RATIO_ALL_SEED_NOTE = (
    "all operating ratios are hotel-type defaults; no T-12 was provided"
)

#: Pricing / solve-to-target sections when the deal carries no return target.
PRICING_NO_TARGET_NOTE = (
    "Pricing to target not available: no return target set on this deal"
)


def noi_before_reserve_label(*, basis_confirmed: bool) -> str:
    """The proforma row label for a before-reserve NOI series."""
    return NOI_BEFORE_RESERVE if basis_confirmed else NOI_BASIS_UNCONFIRMED


# ─────────────────────── line + source display names ───────────────────────
#
# Historical-baseline and YoY-walk rows are keyed by ``HistoricalYear`` slugs
# (``noi``, ``gop``, ``fnb_revenue``). ``slug.replace("_", " ").title()`` turns
# those into ``Noi`` / ``Gop`` / ``Fnb Revenue`` in the IC memo PDF and the
# workbook, so every builder resolves them here instead.

LINE_LABELS: dict[str, str] = {
    "total_revenue": "Total Revenue",
    "rooms_revenue": "Rooms Revenue",
    "fnb_revenue": "F&B Revenue",
    "other_revenue": "Other Revenue",
    "other_operated_revenue": "Other Operated Revenue",
    "misc_income": "Miscellaneous Income",
    "resort_fees": "Resort Fees",
    "gop": "GOP",
    "noi": "NOI",
    "noi_institutional": NOI_BEFORE_RESERVE,
    "ebitda": "EBITDA",
    "ebitdar": "EBITDAR",
    "occupancy": "Occupancy",
    "adr": "ADR",
    "revpar": "RevPAR",
    "ffe_reserve": "FF&E Reserve",
    "mgmt_fee": "Management Fee",
    "total_expenses": "Total Expenses",
    "departmental_expenses": "Departmental Expenses",
    "undistributed_expenses": "Undistributed Expenses",
    "fixed_charges": "Fixed Charges",
}


def line_label(line: str | None) -> str:
    """Display name for a P&L line slug; title-cases an unknown slug."""
    if not line:
        return ""
    key = str(line)
    return LINE_LABELS.get(key, key.replace("_", " ").title())


#: Provenance source codes → the word a reviewer reads. Covers every
#: ``SOURCE_*`` constant the engine runner / analysis layer emits plus the
#: dossier field sources; an unknown code renders as ``Other``, never as the
#: title-cased code (``Deal_Row``, ``Om_Broker``).
SOURCE_DISPLAY: dict[str, str] = {
    "t12_actual": "T-12",
    "portfolio_pnl": "Portfolio",
    "cbre_horizons": "CBRE",
    "pnl_benchmark": "HOST",
    "analyst_override": "Override",
    "seed": "Seed",
    "deal_row": "Deal record",
    "deals_table": "Deal record",
    "om_broker": "OM",
    "om_comps": "OM comps",
    "pip_om": "OM (PIP)",
    "pip_user": "Analyst (PIP)",
    "pip_document": "PIP document",
    "roi_user": "Analyst (ROI)",
    "capex_ffe_default": "FF&E default",
    "str_segmentation_default": "STR segmentation",
    "str_forecast": "STR forecast",
    "str_subject_ttm": "STR (subject)",
    "str_comp_set": "STR (comp set)",
    "str_forecast_unavailable": "STR unavailable",
    "cbre_horizons_unavailable": "CBRE unavailable",
    "om_comps_unavailable": "OM comps unavailable",
    "derived_from_revpar_growth": "Derived",
    "partnership_doc": "Partnership document",
    "index_assumption": "Index assumption",
    "extraction": "Extracted",
    "engine_output": "Model",
    "computed": "Derived",
}

SOURCE_DISPLAY_FALLBACK = "Other"


def source_display(source: str | None) -> str:
    """Display name for a provenance source code (``Other`` when unknown)."""
    if not source:
        return SOURCE_DISPLAY_FALLBACK
    return SOURCE_DISPLAY.get(str(source), SOURCE_DISPLAY_FALLBACK)


#: ``comp_sales.derived_cap_rate_method`` / ``coverage_quality`` are codes.
COMP_CAP_METHOD_DISPLAY: dict[str, str] = {
    "none": "Not derived — fewer than 3 comparable sales",
    "median": "Median of comps",
    "weighted": "Size-weighted",
}
COMP_COVERAGE_DISPLAY: dict[str, str] = {
    "low": "Low",
    "medium": "Medium",
    "high": "High",
}
