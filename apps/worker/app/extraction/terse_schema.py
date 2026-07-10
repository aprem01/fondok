"""Terse output schema for field compression.

Reduces output tokens by replacing long dotted field paths with short IDs (2-8 chars).
Every extraction result now includes a catalog_version so old results still decode.

Catalog structure:
    field_id (str, 2-8 chars): short identifier (e.g., "rooms_rev", "occ_pct")
    full_path (str): canonical dotted path (e.g., "p_and_l_usali.operating_revenue.rooms_revenue")
    description (str): human-readable description
    data_type (str): expected type (USD, pct, count, string, etc.)

Terse JSON format:
    Old: [{"field_name": "p_and_l_usali.operating_revenue.rooms_revenue", "value": 123000, "confidence": 0.95, "unit": "USD"}]
    New: [{"fid": "rooms_rev", "v": 123000, "c": 0.95, "u": "USD"}]

The catalog is versioned (catalog_version in extraction_results) so future expansions don't break old results.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────── Terse catalog (immutable) ───────────────────────

# Comprehensive field ID catalog covering all known extractor output paths.
# Keys are short field IDs (2-8 chars), values are dictionaries with:
#   - full_path: canonical dotted path
#   - description: human-readable explanation
#   - data_type: expected value type (USD, pct, count, date, string)
#
# This catalog is versioned = 1. Future expansions increment the version
# in the migration so old extraction_results rows stay decodable.

FIELD_ID_CATALOG: dict[str, dict[str, str]] = {
    # ─── Property overview ────────────────────────────────────────────
    "prop_name": {
        "full_path": "property_overview.name",
        "description": "Property name",
        "data_type": "string",
    },
    "prop_code": {
        "full_path": "property_overview.code",
        "description": "Property code/identifier",
        "data_type": "string",
    },
    "prop_star": {
        "full_path": "property_overview.star_id",
        "description": "STR star ID number",
        "data_type": "count",
    },
    "prop_own": {
        "full_path": "property_overview.ownership",
        "description": "Owner/ownership entity",
        "data_type": "string",
    },
    "prop_mgmt": {
        "full_path": "property_overview.management_company",
        "description": "Management company name",
        "data_type": "string",
    },
    "prop_brand": {
        "full_path": "property_overview.brand",
        "description": "Hotel brand",
        "data_type": "string",
    },
    "prop_mkt": {
        "full_path": "property_overview.market",
        "description": "Market/MSA",
        "data_type": "string",
    },
    "prop_keys": {
        "full_path": "property_overview.keys",
        "description": "Number of rooms/keys",
        "data_type": "count",
    },
    "prop_room_range": {
        "full_path": "property_overview.room_range",
        "description": "Room type distribution/range",
        "data_type": "string",
    },
    "prop_open": {
        "full_path": "property_overview.open_date",
        "description": "Property open date",
        "data_type": "date",
    },
    "prop_managed": {
        "full_path": "property_overview.managed_or_franchised",
        "description": "Managed or franchised flag",
        "data_type": "string",
    },
    "prop_union": {
        "full_path": "property_overview.union",
        "description": "Union status",
        "data_type": "string",
    },
    "prop_asset_mgr": {
        "full_path": "property_overview.asset_manager",
        "description": "Asset manager entity",
        "data_type": "string",
    },
    "prop_fund": {
        "full_path": "property_overview.fund",
        "description": "Fund/investment vehicle",
        "data_type": "string",
    },
    "prop_str_class": {
        "full_path": "property_overview.str_market_class",
        "description": "STR market classification",
        "data_type": "string",
    },
    "prop_period": {
        "full_path": "property_overview.statement_period",
        "description": "Statement period label",
        "data_type": "string",
    },
    # ─── P&L USALI — Revenue streams ──────────────────────────────────
    "rooms_rev": {
        "full_path": "p_and_l_usali.operating_revenue.rooms_revenue",
        "description": "Rooms/guest room revenue (USD)",
        "data_type": "USD",
    },
    "fb_rev": {
        "full_path": "p_and_l_usali.operating_revenue.food_beverage_revenue",
        "description": "Food & beverage revenue (USD)",
        "data_type": "USD",
    },
    "resort_fees": {
        "full_path": "p_and_l_usali.operating_revenue.resort_fees",
        "description": "Resort fees/daily charges (USD)",
        "data_type": "USD",
    },
    "other_rev": {
        "full_path": "p_and_l_usali.operating_revenue.other_revenue",
        "description": "Other operating revenue (USD)",
        "data_type": "USD",
    },
    "misc_rev": {
        "full_path": "p_and_l_usali.operating_revenue.misc_revenue",
        "description": "Miscellaneous income (USD)",
        "data_type": "USD",
    },
    "total_rev": {
        "full_path": "p_and_l_usali.operating_revenue.total_revenue",
        "description": "Total operating revenue (USD)",
        "data_type": "USD",
    },
    "pnl_total_rev": {
        "full_path": "p_and_l_usali.total_revenues_usd",
        "description": "Total revenues P&L total (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Departmental expenses ────────────────────────────
    "rooms_exp": {
        "full_path": "p_and_l_usali.departmental_expenses.rooms",
        "description": "Rooms department expense (USD)",
        "data_type": "USD",
    },
    "fb_exp": {
        "full_path": "p_and_l_usali.departmental_expenses.food_beverage",
        "description": "F&B department expense (USD)",
        "data_type": "USD",
    },
    "other_dept_exp": {
        "full_path": "p_and_l_usali.departmental_expenses.other_operated",
        "description": "Other operated dept expense (USD)",
        "data_type": "USD",
    },
    "total_dept_exp": {
        "full_path": "p_and_l_usali.total_departmental_expense_usd",
        "description": "Total departmental expenses (USD)",
        "data_type": "USD",
    },
    "total_dept_prof": {
        "full_path": "p_and_l_usali.total_departmental_profit_usd",
        "description": "Total departmental profit (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Undistributed expenses ──────────────────────────
    "admin_exp": {
        "full_path": "p_and_l_usali.undistributed.administrative_general",
        "description": "Administrative & general expense (USD)",
        "data_type": "USD",
    },
    "info_exp": {
        "full_path": "p_and_l_usali.undistributed.information_telecom",
        "description": "Information & telecom expense (USD)",
        "data_type": "USD",
    },
    "sales_exp": {
        "full_path": "p_and_l_usali.undistributed.sales_marketing",
        "description": "Sales & marketing expense (USD)",
        "data_type": "USD",
    },
    "prop_ops": {
        "full_path": "p_and_l_usali.undistributed.property_operations",
        "description": "Property operations/maintenance (USD)",
        "data_type": "USD",
    },
    "util_exp": {
        "full_path": "p_and_l_usali.undistributed.utilities",
        "description": "Utilities expense (USD)",
        "data_type": "USD",
    },
    "total_undist": {
        "full_path": "p_and_l_usali.total_undistributed_expenses_usd",
        "description": "Total undistributed expenses (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Fees & reserves ──────────────────────────────────
    "mgmt_fee": {
        "full_path": "p_and_l_usali.fees_and_reserves.mgmt_fee",
        "description": "Management fee (USD)",
        "data_type": "USD",
    },
    "ffe_res": {
        "full_path": "p_and_l_usali.fees_and_reserves.ffe_reserve",
        "description": "FF&E reserve/capital reserve (USD)",
        "data_type": "USD",
    },
    "mgmt_fee_alt": {
        "full_path": "p_and_l_usali.management_fees_usd",
        "description": "Management fees (USD) - alt path",
        "data_type": "USD",
    },
    # ─── P&L USALI — Fixed charges ────────────────────────────────────
    "prop_tax": {
        "full_path": "p_and_l_usali.fixed_charges.property_taxes",
        "description": "Property taxes (USD)",
        "data_type": "USD",
    },
    "insur": {
        "full_path": "p_and_l_usali.fixed_charges.insurance",
        "description": "Insurance expense (USD)",
        "data_type": "USD",
    },
    "rent": {
        "full_path": "p_and_l_usali.fixed_charges.rent",
        "description": "Ground rent/lease (USD)",
        "data_type": "USD",
    },
    "other_fixed": {
        "full_path": "p_and_l_usali.fixed_charges.other",
        "description": "Other fixed charges (USD)",
        "data_type": "USD",
    },
    "total_fixed": {
        "full_path": "p_and_l_usali.fixed_charges.total",
        "description": "Total fixed charges (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Computed lines (GOP, NOI, etc.) ──────────────────
    "gop": {
        "full_path": "p_and_l_usali.gross_operating_profit_usd",
        "description": "Gross operating profit (USD)",
        "data_type": "USD",
    },
    "noi": {
        "full_path": "p_and_l_usali.net_operating_income_usd",
        "description": "Net operating income (USD)",
        "data_type": "USD",
    },
    "ebitda": {
        "full_path": "p_and_l_usali.ebitda_usd",
        "description": "EBITDA (USD)",
        "data_type": "USD",
    },
    "ebitda_less_res": {
        "full_path": "p_and_l_usali.ebitda_less_replacement_reserve_usd",
        "description": "EBITDA less replacement reserve (USD)",
        "data_type": "USD",
    },
    "pnl_inc_non_op": {
        "full_path": "p_and_l_usali.income_before_non_operating_usd",
        "description": "Income before non-operating (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Non-operating items ──────────────────────────────
    "non_op_inc": {
        "full_path": "p_and_l_usali.non_operating.income_usd",
        "description": "Non-operating income (USD)",
        "data_type": "USD",
    },
    "non_op_tax": {
        "full_path": "p_and_l_usali.non_operating.property_and_other_taxes_usd",
        "description": "Non-op property & other taxes (USD)",
        "data_type": "USD",
    },
    "non_op_insur": {
        "full_path": "p_and_l_usali.non_operating.insurance_usd",
        "description": "Non-op insurance (USD)",
        "data_type": "USD",
    },
    "non_op_rent": {
        "full_path": "p_and_l_usali.non_operating.rent_usd",
        "description": "Non-op rent/ground rent (USD)",
        "data_type": "USD",
    },
    "non_op_other": {
        "full_path": "p_and_l_usali.non_operating.other_usd",
        "description": "Non-op other expenses (USD)",
        "data_type": "USD",
    },
    "total_non_op": {
        "full_path": "p_and_l_usali.total_non_operating_expenses_usd",
        "description": "Total non-operating expenses (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Operational KPIs ────────────────────────────────
    "occ_pct": {
        "full_path": "p_and_l_usali.operational_kpis.occupancy_pct",
        "description": "Occupancy percentage (0-1)",
        "data_type": "pct",
    },
    "adr": {
        "full_path": "p_and_l_usali.operational_kpis.adr_usd",
        "description": "Average daily rate (USD)",
        "data_type": "USD",
    },
    "revpar": {
        "full_path": "p_and_l_usali.operational_kpis.revpar_usd",
        "description": "Revenue per available room (USD)",
        "data_type": "USD",
    },
    "avail_rooms": {
        "full_path": "p_and_l_usali.available_rooms.total",
        "description": "Available rooms (count)",
        "data_type": "count",
    },
    "rooms_sold": {
        "full_path": "p_and_l_usali.rooms_sold.total",
        "description": "Rooms sold/occupied (count)",
        "data_type": "count",
    },
    # ─── P&L USALI — Period metadata ──────────────────────────────────
    "pnl_period_end": {
        "full_path": "p_and_l_usali.period_ending",
        "description": "Period ending date",
        "data_type": "date",
    },
    "pnl_forecast": {
        "full_path": "p_and_l_usali.forecast_type",
        "description": "Forecast type (Actual/Forecast/Blended)",
        "data_type": "string",
    },
    # ─── P&L USALI — Page/section breakdown (alternative paths) ───────
    "page5_gop": {
        "full_path": "p_and_l_usali.page5.gross_operating_profit_usd",
        "description": "GOP per page 5 (USD)",
        "data_type": "USD",
    },
    "page5_noi": {
        "full_path": "p_and_l_usali.page5.income_before_non_operating_usd",
        "description": "NOI per page 5 (USD)",
        "data_type": "USD",
    },
    "page5_ebitda": {
        "full_path": "p_and_l_usali.page5.ebitda_usd",
        "description": "EBITDA per page 5 (USD)",
        "data_type": "USD",
    },
    "page5_ebitda_res": {
        "full_path": "p_and_l_usali.page5.ebitda_less_replacement_reserve_usd",
        "description": "EBITDA less reserve per page 5 (USD)",
        "data_type": "USD",
    },
    "page5_mgmt": {
        "full_path": "p_and_l_usali.page5.management_fees_usd",
        "description": "Management fees per page 5 (USD)",
        "data_type": "USD",
    },
    "page5_tax": {
        "full_path": "p_and_l_usali.page5.property_and_other_taxes_usd",
        "description": "Property tax per page 5 (USD)",
        "data_type": "USD",
    },
    "page5_insur": {
        "full_path": "p_and_l_usali.page5.insurance_usd",
        "description": "Insurance per page 5 (USD)",
        "data_type": "USD",
    },
    "page5_non_op": {
        "full_path": "p_and_l_usali.page5.total_non_operating_usd",
        "description": "Total non-op per page 5 (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Alternative/detail paths ──────────────────────────
    "rooms_dept_exp_alt": {
        "full_path": "p_and_l_usali.rooms.departmental_expense_usd",
        "description": "Rooms dept expense - alt (USD)",
        "data_type": "USD",
    },
    "rooms_exp_total": {
        "full_path": "p_and_l_usali.rooms.expense_usd",
        "description": "Rooms expense total (USD)",
        "data_type": "USD",
    },
    "rooms_rev_alt": {
        "full_path": "p_and_l_usali.rooms.revenue_usd",
        "description": "Rooms revenue - alt (USD)",
        "data_type": "USD",
    },
    "rooms_avail_alt": {
        "full_path": "p_and_l_usali.rooms.available_rooms_total",
        "description": "Available rooms - alt (USD)",
        "data_type": "count",
    },
    "rooms_sold_alt": {
        "full_path": "p_and_l_usali.rooms.rooms_sold_total",
        "description": "Rooms sold - alt (count)",
        "data_type": "count",
    },
    "fb_exp_alt": {
        "full_path": "p_and_l_usali.food_and_beverage.revenue_usd",
        "description": "F&B revenue (USD)",
        "data_type": "USD",
    },
    "fb_exp_dept": {
        "full_path": "p_and_l_usali.food_and_beverage.departmental_expense_usd",
        "description": "F&B dept expense (USD)",
        "data_type": "USD",
    },
    "fb_exp_total": {
        "full_path": "p_and_l_usali.food_and_beverage.expense_usd",
        "description": "F&B expense total (USD)",
        "data_type": "USD",
    },
    "other_dept_rev": {
        "full_path": "p_and_l_usali.other_operated_departments.revenue_usd",
        "description": "Other dept revenue (USD)",
        "data_type": "USD",
    },
    "other_dept_exp_d": {
        "full_path": "p_and_l_usali.other_operated_departments.departmental_expense_usd",
        "description": "Other dept expense (USD)",
        "data_type": "USD",
    },
    "other_dept_exp_t": {
        "full_path": "p_and_l_usali.other_operated_departments.expense_usd",
        "description": "Other dept expense total (USD)",
        "data_type": "USD",
    },
    "admin_exp_alt": {
        "full_path": "p_and_l_usali.undistributed.administrative_and_general_usd",
        "description": "Admin expense - alt (USD)",
        "data_type": "USD",
    },
    "info_exp_alt": {
        "full_path": "p_and_l_usali.undistributed.information_and_telecom_usd",
        "description": "Info/telecom - alt (USD)",
        "data_type": "USD",
    },
    "sales_exp_alt": {
        "full_path": "p_and_l_usali.undistributed.sales_and_marketing_usd",
        "description": "Sales/marketing - alt (USD)",
        "data_type": "USD",
    },
    "prop_ops_alt": {
        "full_path": "p_and_l_usali.undistributed.property_operations_and_maintenance_usd",
        "description": "Property ops - alt (USD)",
        "data_type": "USD",
    },
    "util_exp_alt": {
        "full_path": "p_and_l_usali.utilities.expense_usd",
        "description": "Utilities - alt (USD)",
        "data_type": "USD",
    },
    "prop_ops_detail": {
        "full_path": "p_and_l_usali.property_operations_and_maintenance.expense_usd",
        "description": "Property ops detail (USD)",
        "data_type": "USD",
    },
    # ─── P&L USALI — Monthly breakdown (dynamic, sample patterns) ─────
    "mnth_jan_adr": {
        "full_path": "p_and_l_usali.monthly.jan_2025.adr_usd",
        "description": "January ADR (USD)",
        "data_type": "USD",
    },
    "mnth_jan_occ": {
        "full_path": "p_and_l_usali.monthly.jan_2025.occupancy_pct",
        "description": "January occupancy (0-1)",
        "data_type": "pct",
    },
    "mnth_jan_revpar": {
        "full_path": "p_and_l_usali.monthly.jan_2025.revpar_usd",
        "description": "January RevPAR (USD)",
        "data_type": "USD",
    },
    "mnth_jan_rooms_rev": {
        "full_path": "p_and_l_usali.monthly.jan_2025.rooms_revenue_usd",
        "description": "January rooms revenue (USD)",
        "data_type": "USD",
    },
    "mnth_jan_tot_rev": {
        "full_path": "p_and_l_usali.monthly.jan_2025.total_revenues_usd",
        "description": "January total revenue (USD)",
        "data_type": "USD",
    },
    "mnth_jan_avail": {
        "full_path": "p_and_l_usali.monthly.jan_2025.available_rooms",
        "description": "January available rooms (count)",
        "data_type": "count",
    },
    "mnth_jan_sold": {
        "full_path": "p_and_l_usali.monthly.jan_2025.rooms_sold",
        "description": "January rooms sold (count)",
        "data_type": "count",
    },
    "mnth_jan_ebitda": {
        "full_path": "p_and_l_usali.monthly.jan_2025.ebitda_usd",
        "description": "January EBITDA (USD)",
        "data_type": "USD",
    },
    # ─── TTM Summary (from OM) ─────────────────────────────────────────
    "ttm_occ": {
        "full_path": "ttm_summary_per_om.occupancy_pct",
        "description": "TTM occupancy from OM (0-1)",
        "data_type": "pct",
    },
    "ttm_adr": {
        "full_path": "ttm_summary_per_om.adr_usd",
        "description": "TTM ADR from OM (USD)",
        "data_type": "USD",
    },
    "ttm_revpar": {
        "full_path": "ttm_summary_per_om.revpar_usd",
        "description": "TTM RevPAR from OM (USD)",
        "data_type": "USD",
    },
    # ─── TTM Performance (normalized) ──────────────────────────────────
    "ttm_perf_occ": {
        "full_path": "ttm_performance.subject.occupancy_pct",
        "description": "TTM performance occupancy (0-1)",
        "data_type": "pct",
    },
    "ttm_perf_adr": {
        "full_path": "ttm_performance.subject.adr_usd",
        "description": "TTM performance ADR (USD)",
        "data_type": "USD",
    },
    "ttm_perf_revpar": {
        "full_path": "ttm_performance.subject.revpar_usd",
        "description": "TTM performance RevPAR (USD)",
        "data_type": "USD",
    },
}

# Build reverse mapping (full_path → field_id) for lookup during terse encoding
FULL_PATH_TO_ID: dict[str, str] = {
    entry["full_path"]: fid for fid, entry in FIELD_ID_CATALOG.items()
}

# Current catalog version (increment when adding new entries)
CATALOG_VERSION: int = 1


def field_name_to_id(field_name: str) -> str | None:
    """Look up field_id for a canonical field_name.

    Returns the catalog ID for a KNOWN path, and ``None`` for anything
    not in the catalog.

    We deliberately do NOT auto-generate an ID for non-catalog paths.
    An auto-generated ID (e.g. ``parts[-1][:12]``) is unsafe on three
    counts, all of which corrupted the terse round-trip:

      * It made this function never return ``None``, so the long-form
        fallback in :func:`compress_extraction_result` was dead code —
        every field was force-compressed.
      * A 2-segment path like ``foo.adr`` produced fid ``adr``, which
        COLLIDES with the real catalog entry ``adr`` and decoded back to
        the wrong canonical path.
      * Genuinely-novel paths had no catalog entry to decode against, so
        they expanded to ``__unknown__<fid>`` — permanently losing the
        original path.

    By returning ``None`` here, non-catalog fields are stored verbatim in
    their original long form (see :func:`compress_extraction_result`), so
    they round-trip losslessly and can never collide with a catalog fid.

    Args:
        field_name: dotted path like "p_and_l_usali.operating_revenue.rooms_revenue"

    Returns:
        Short field ID (e.g., "rooms_rev") for a catalog path, else ``None``.
    """
    return FULL_PATH_TO_ID.get(field_name)


def field_id_to_name(field_id: str) -> str | None:
    """Look up canonical field_name for a field_id.

    For IDs in the catalog, returns the canonical path.
    For unknown IDs (generated from new paths), returns None.

    Args:
        field_id: short ID like "rooms_rev"

    Returns:
        Full dotted path or None if not found.
    """
    entry = FIELD_ID_CATALOG.get(field_id)
    return entry["full_path"] if entry else None


def _expand_terse_row(
    field: dict[str, Any], catalog_version: int | None = None
) -> dict[str, Any]:
    """Expand a single terse row ({"fid","v","c","u","sp","rt"}) to long form."""
    fid = field["fid"]
    full_path = field_id_to_name(fid)
    if not full_path:
        # Field ID not in catalog — log warning and emit a fallback
        # field_name so downstream doesn't break. With the new
        # field_name_to_id (never auto-generates), the write path can no
        # longer PRODUCE such an fid; this only guards hand-crafted or
        # forward-version rows.
        logger.warning(f"Field ID '{fid}' not in catalog v{catalog_version or 1}")
        full_path = f"__unknown__{fid}"
    return {
        "field_name": full_path,
        "value": field.get("v"),
        "confidence": field.get("c", 0.0),
        "unit": field.get("u"),
        "source_page": field.get("sp", 1),
        "raw_text": field.get("rt"),
    }


def read_extraction_fields(
    raw_fields: list[dict[str, Any]] | None, catalog_version: int | None = None
) -> list[dict[str, Any]]:
    """Return LONG-FORM extraction fields regardless of how they're stored.

    This is the single shared accessor every read path routes through, so
    callers never need to know whether the persisted rows are terse
    ({"fid": ...}) or long ({"field_name": ...}), and mixed lists (some
    terse, some long — which happens once non-catalog fields are stored
    verbatim alongside compressed catalog fields) are handled per-row.

    Behavior is a strict superset of the old per-list logic:

      * ``None`` / empty            → ``[]``
      * all long-form (flag OFF)    → returned UNCHANGED (same object) —
                                      identical to today's no-op default.
      * any terse row present       → each terse row expanded, each
                                      long-form row passed through untouched.

    This is a pure in-memory transform (catalog dict lookups only, no
    I/O), so it is synchronous — callers no longer need to ``await``.
    """
    if not raw_fields:
        return []

    # Fast path: nothing terse → return the list unchanged. Keeps the
    # flag-OFF default a true no-op and preserves object identity for
    # legacy long-form callers.
    if not any(isinstance(f, dict) and "fid" in f for f in raw_fields):
        return raw_fields

    expanded: list[dict[str, Any]] = []
    for field in raw_fields:
        if not isinstance(field, dict):
            # Unrecognized element — keep as-is.
            expanded.append(field)
        elif "field_name" in field:
            # Already long-form — pass through untouched (mixed-row safe).
            expanded.append(field)
        elif "fid" in field:
            expanded.append(_expand_terse_row(field, catalog_version))
        else:
            # Unrecognized dict shape — keep as-is.
            expanded.append(field)
    return expanded


async def expand_extraction_result(
    fields: list[dict[str, Any]], catalog_version: int | None = None
) -> list[dict[str, Any]]:
    """Expand terse extraction fields back to canonical (long) form.

    Async wrapper retained for backward compatibility with existing
    callers/tests. The work is pure in-memory (see
    :func:`read_extraction_fields`), so this just delegates to the sync
    accessor — mixed terse/long lists expand per-row.

    Args:
        fields: list of extraction field dicts
        catalog_version: which catalog version produced these fields
                         (currently unused since we only have v1, but reserved)

    Returns:
        List of fields with field_name (canonical path), value, confidence, unit, etc.
    """
    return read_extraction_fields(fields, catalog_version)


def compress_extraction_result(
    fields: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Compress long-form extraction fields to terse JSON.

    Args:
        fields: list of extraction fields with field_name (long form)

    Returns:
        (terse_fields, catalog_version) where terse_fields use short IDs
    """
    terse = []
    for field in fields:
        field_name = field.get("field_name")
        if not field_name:
            # Already terse or malformed — skip
            logger.warning(f"Field missing field_name: {field}")
            continue

        fid = field_name_to_id(field_name)
        if not fid:
            # Field not in catalog — emit as-is with a warning
            logger.warning(f"Field '{field_name}' not in catalog, emitting long form")
            terse.append(field)
            continue

        # Compress to terse form: fid, v(alue), c(onfidence), u(nit), sp(ource_page), rt(aw_text)
        terse_field = {
            "fid": fid,
            "v": field.get("value"),
            "c": field.get("confidence", 0.0),
        }
        if field.get("unit"):
            terse_field["u"] = field["unit"]
        if field.get("source_page"):
            terse_field["sp"] = field["source_page"]
        if field.get("raw_text"):
            terse_field["rt"] = field["raw_text"]

        terse.append(terse_field)

    return terse, CATALOG_VERSION


__all__ = [
    "FIELD_ID_CATALOG",
    "FULL_PATH_TO_ID",
    "CATALOG_VERSION",
    "field_name_to_id",
    "field_id_to_name",
    "expand_extraction_result",
    "read_extraction_fields",
    "compress_extraction_result",
]
