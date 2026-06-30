"""USALI compliance scorer.

Given a flat dict of extracted P&L fields (``{revpar: 245, occupancy: 0.74,
adr: 331, ...}``) and the canonical 66-rule USALI catalog (loaded from
``apps/worker/app/usali_rules.py``), score the document on a 0-100 scale
and surface every deviation as a human-readable message.

Design rules (Wave 1, June 2026 — see ``project_fondok_wave1_decisions.md``):

* **Inconclusive threshold** — fewer than 5 applicable rules → score is
  ``None`` and ``inconclusive=True``. We have too little signal to call
  a percent; the UI shows "Inconclusive" instead of a misleading 100%
  ("4 of 4 rules passed").
* **Market-context-dependent rules** — when a rule needs context the
  deal lacks (e.g. ``INSURANCE_PER_KEY_COASTAL`` requires a coastal
  flag), the rule is excluded from the applicable count and surfaced
  as ``requires_market_context=True`` in the deviations list instead
  of being failed. Coverage-blind rules (everything else) gracefully
  skip when their inputs are missing.
* **Safe evaluator** — formulas come from a CSV that lives next to the
  code; we never run them through ``eval()``. A small AST visitor
  permits constants, the formula field names, ``+ - * /`` arithmetic,
  comparison, ``abs()``, and ``sum()`` over a list literal — and
  nothing else. Any node outside that allowlist raises and the rule
  is skipped (never failed) so an unparseable formula can't poison a
  document's score.

Rule patterns (mapped from ``evals/golden-set/usali-rules.csv``):

* **Math identities** (``CRITICAL``, threshold 0..~0.005-0.01) —
  formula evaluates a *relative drift* that should be near zero, e.g.
  ``abs(revpar - (occupancy * adr)) / revpar``. We pass when the
  drift is ≤ ``threshold_max``.
* **Ratios** (``WARN``/``INFO``) — formula like ``gop / total_revenue``
  whose value must fall in ``[threshold_min, threshold_max]``.
* **Single fields** (``WARN``/``INFO``) — formula is a bare field name
  like ``occupancy``; value must fall in ``[threshold_min, threshold_max]``.

Field-name resolution: the catalog uses canonical names
(``revpar``, ``total_revenue``, ``mgmt_fee``, …); the extraction
payload upstream can carry slightly different paths
(``p_and_l_usali.revpar_usd``, ``broker_proforma.total_revenue``). The
``_ALIASES`` map below lists tolerated alternative names per canonical
field so the scorer doesn't fail valid documents on a path mismatch.
"""

from __future__ import annotations

import ast
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from ..usali_rules import USALIRule, load_usali_rules

logger = logging.getLogger(__name__)


# ─────────────────────────── public dataclasses ───────────────────────────


@dataclass(frozen=True)
class USALIDeviation:
    """One rule's evaluation result.

    Pure data — serializable straight to JSON for the ``usali_deviations``
    column. ``actual_value`` is ``None`` only when ``requires_market_context``
    is True (we couldn't evaluate because the deal lacks context) or when
    a formula short-circuited on a zero denominator.
    """

    rule_id: str
    rule_name: str
    severity: str  # CRITICAL / WARN / INFO
    message: str
    actual_value: float | None
    threshold_min: float | None
    threshold_max: float | None
    requires_market_context: bool = False


@dataclass
class USALIScore:
    """End-to-end result of scoring a P&L extraction.

    * ``score`` is a 0-100 percentage (passed / applicable × 100) OR
      ``None`` when we couldn't evaluate enough rules to call a score.
    * ``deviations`` is a flat list of every rule whose evaluation
      surfaced a finding — out-of-range values, identity violations,
      or market-context placeholders. A rule that PASSED never appears
      here; a rule that was simply skipped for missing inputs never
      appears here either.
    * ``inconclusive`` is True when ``applicable_count < 5``. Mirrors
      the Wave 1 product decision: don't show a percent on weak signal.
    """

    score: float | None
    applicable_count: int
    passed_count: int
    deviations: list[USALIDeviation] = field(default_factory=list)
    inconclusive: bool = False


# ─────────────────────────── inconclusive threshold ───────────────────────────


# Below this many applicable rules a percent score is more misleading
# than helpful ("4 of 4 passed = 100%" tells you nothing). The product
# rule (Wave 1, June 2026) is to surface "Inconclusive" instead.
_INCONCLUSIVE_FLOOR = 5


# ─────────────────────────── alias map ───────────────────────────


# Canonical field name → tolerated alternative paths that the extractor
# may emit on real uploads. The first hit wins. The canonical name is
# always tried first (so a payload that already uses ``revpar`` doesn't
# pay the alias cost).
#
# Keep this list conservative — every alias is a place a real upload
# might collide with the wrong number. The choices below mirror what
# the extractor actually emits today (see ``apps/worker/app/agents/
# extractor`` and the ``_load_critic_inputs`` resolver in
# ``api/documents.py`` for the same kind of soft-resolution).
_ALIASES: dict[str, tuple[str, ...]] = {
    # Top-line ops KPIs. The Extractor emits bare ``revpar_usd`` /
    # ``adr_usd`` / ``occupancy_pct`` per ``extraction_schemas/t12.md``
    # AND occasionally the fully-namespaced
    # ``p_and_l_usali.operational_kpis.*`` form when the LLM mirrors
    # the prompt's hierarchy. List both.
    #
    # Sam QA Bug #3 v2 (June 2026): the REAL prod T-12 (saved as
    # ``tests/fixtures/real_payloads/anglers_t12_real.json``) emits ops
    # KPIs at ``ttm_summary_per_om.{occupancy_pct, adr_usd, revpar_usd}``
    # — completely orthogonal to the schema doc. Listed below as the
    # bare-extractor fallback so the rule catalog can resolve them.
    "revpar": (
        "revpar_usd",
        "p_and_l_usali.revpar",
        "p_and_l_usali.revpar_usd",
        "p_and_l_usali.operational_kpis.revpar",
        "p_and_l_usali.operational_kpis.revpar_usd",
        # Real prod payload: TTM summary block.
        "ttm_summary_per_om.revpar_usd",
        "ttm_summary_per_om.revpar",
    ),
    "occupancy": (
        "occupancy_pct",
        "p_and_l_usali.occupancy",
        "p_and_l_usali.occupancy_pct",
        "p_and_l_usali.operational_kpis.occupancy",
        "p_and_l_usali.operational_kpis.occupancy_pct",
        "ttm_summary_per_om.occupancy_pct",
        "ttm_summary_per_om.occupancy",
    ),
    "adr": (
        "adr_usd",
        "p_and_l_usali.adr",
        "p_and_l_usali.adr_usd",
        "p_and_l_usali.operational_kpis.adr",
        "p_and_l_usali.operational_kpis.adr_usd",
        "ttm_summary_per_om.adr_usd",
        "ttm_summary_per_om.adr",
    ),
    # Revenue rollups. ``total_revenue`` is derived in
    # ``flatten_extraction_fields`` from the operating_revenue
    # components when the extractor doesn't emit it directly.
    #
    # Sam QA Bug #3 v2: real prod emits the canonical totals DIRECTLY
    # at the bucket-root level (``p_and_l_usali.total_revenues_usd``)
    # OR under a ``revenues.`` namespace (the annual P&L flavor).
    # Listed first so we don't have to synthesize when the extractor
    # already did.
    "total_revenue": (
        "total_revenue_usd",
        "total_revenues_usd",
        "p_and_l_usali.total_revenue",
        "p_and_l_usali.total_revenue_usd",
        "p_and_l_usali.total_revenues_usd",  # T-12 prod
        "p_and_l_usali.revenues.total_revenues_usd",  # annual P&L prod
        "p_and_l_usali.revenues.total_revenue",
        "p_and_l_usali.operating_revenue.total_revenue",
        "p_and_l_usali.operating_revenue.total",
    ),
    "rooms_revenue": (
        "rooms_revenue_usd",
        "p_and_l_usali.rooms_revenue",
        # T-12 prod: per-dept bucket carries revenue under ``.revenue_usd``.
        "p_and_l_usali.rooms.revenue_usd",
        "p_and_l_usali.rooms.revenue",
        # Annual P&L prod: revenues namespace.
        "p_and_l_usali.revenues.rooms_usd",
        "p_and_l_usali.revenues.rooms",
        "p_and_l_usali.revenues.rooms_revenue",
        # Schema-doc canonical (legacy fixture path).
        "p_and_l_usali.operating_revenue.rooms_revenue",
        "p_and_l_usali.operating_revenue.rooms_revenue_usd",
    ),
    "fb_revenue": (
        "fb_revenue_usd",
        "food_beverage_revenue",
        "p_and_l_usali.fb_revenue",
        # T-12 prod
        "p_and_l_usali.food_and_beverage.revenue_usd",
        "p_and_l_usali.food_and_beverage.revenue",
        # Annual P&L prod
        "p_and_l_usali.revenues.fb_usd",
        "p_and_l_usali.revenues.fb",
        "p_and_l_usali.revenues.food_beverage_revenue",
        # Schema-doc canonical
        "p_and_l_usali.operating_revenue.food_beverage_revenue",
        "p_and_l_usali.operating_revenue.fb_revenue",
    ),
    "other_revenue": (
        "other_revenue_usd",
        "p_and_l_usali.other_revenue",
        # T-12 prod
        "p_and_l_usali.other_operated_departments.revenue_usd",
        "p_and_l_usali.other_operated_departments.revenue",
        # Annual P&L prod
        "p_and_l_usali.revenues.other_operated_departments_usd",
        "p_and_l_usali.revenues.other_operated_departments",
        "p_and_l_usali.revenues.other_revenue",
        # Schema-doc canonical
        "p_and_l_usali.operating_revenue.other_revenue",
    ),
    "misc_revenue": (
        "misc_revenue_usd",
        # T-12 prod
        "p_and_l_usali.miscellaneous_income.revenue_usd",
        "p_and_l_usali.miscellaneous_income.revenue",
        # Annual P&L prod
        "p_and_l_usali.revenues.miscellaneous_income_usd",
        "p_and_l_usali.revenues.misc_revenue",
        # Schema-doc canonical
        "p_and_l_usali.operating_revenue.misc_revenue",
    ),
    "resort_fees": (
        "resort_fees_usd",
        "p_and_l_usali.resort_fees",
        "p_and_l_usali.operating_revenue.resort_fees",
        "p_and_l_usali.revenues.resort_fees",
        "p_and_l_usali.revenues.resort_fees_usd",
    ),
    # Departmental — extractor flavors:
    #   * T-12 prod: ``p_and_l_usali.{rooms,food_and_beverage,other_operated_departments}.expense_usd``
    #                OR same path with ``.departmental_expense_usd`` suffix.
    #   * Annual P&L prod: ``p_and_l_usali.departmental_expense.{rooms,fb,other_operated_departments}_usd``.
    #   * Schema doc: ``p_and_l_usali.departmental_expenses.{rooms,food_beverage,other_operated}``.
    # All listed so the rule resolver hits one of them.
    "total_dept_expense": (
        "departmental_expenses",
        "dept_expenses",
        "total_dept_expense_usd",
        "p_and_l_usali.dept_expenses",
        "p_and_l_usali.total_departmental_expense_usd",  # T-12 prod
        "p_and_l_usali.departmental_expense.total_usd",  # annual P&L prod
        "p_and_l_usali.departmental_expenses.total",
    ),
    "dept_expenses": (
        "departmental_expenses",
        "total_dept_expense",
        "p_and_l_usali.dept_expenses",
        "p_and_l_usali.total_departmental_expense_usd",
        "p_and_l_usali.departmental_expense.total_usd",
        "p_and_l_usali.departmental_expenses.total",
    ),
    "rooms_dept_expense": (
        "p_and_l_usali.departmental_expenses.rooms",
        # T-12 prod (two flavors observed on the SAME workbook —
        # both ``.expense_usd`` and ``.departmental_expense_usd``).
        "p_and_l_usali.rooms.expense_usd",
        "p_and_l_usali.rooms.departmental_expense_usd",
        # Annual P&L prod
        "p_and_l_usali.departmental_expense.rooms_usd",
        "p_and_l_usali.departmental_expense.rooms",
    ),
    "fb_dept_expense": (
        "p_and_l_usali.departmental_expenses.food_beverage",
        "food_beverage_dept_expense",
        # T-12 prod
        "p_and_l_usali.food_and_beverage.expense_usd",
        "p_and_l_usali.food_and_beverage.departmental_expense_usd",
        # Annual P&L prod
        "p_and_l_usali.departmental_expense.fb_usd",
        "p_and_l_usali.departmental_expense.fb",
        "p_and_l_usali.departmental_expense.food_beverage_usd",
    ),
    "other_dept_expense": (
        "p_and_l_usali.departmental_expenses.other_operated",
        "other_operated_dept_expense",
        # T-12 prod
        "p_and_l_usali.other_operated_departments.expense_usd",
        "p_and_l_usali.other_operated_departments.departmental_expense_usd",
        # Annual P&L prod
        "p_and_l_usali.departmental_expense.other_operated_departments_usd",
        "p_and_l_usali.departmental_expense.other_operated_usd",
    ),
    # Undistributed roll-up — derived from the five undistributed
    # line items when not emitted directly.
    #
    # Sam QA Bug #3 v2: real prod emits the rollup DIRECTLY at
    # ``p_and_l_usali.total_undistributed_expenses_usd`` (T-12) and
    # ``p_and_l_usali.undistributed_expenses.total_usd`` (annual P&L).
    "undistributed_expenses": (
        "p_and_l_usali.undistributed_expenses",
        "undistributed",
        "p_and_l_usali.undistributed.total",
        "p_and_l_usali.total_undistributed_expenses_usd",  # T-12 prod
        "p_and_l_usali.undistributed_expenses.total_usd",  # annual P&L prod
        "total_undistributed_expenses_usd",
    ),
    # GOP / NOI. Both real prod flavors emit GOP directly; we honor it
    # before falling back to the synthesis path in
    # ``_derive_usali_rollups``.
    "gop": (
        "gop_usd",
        "p_and_l_usali.gop",
        "gross_operating_profit",
        "p_and_l_usali.gross_operating_profit",
        "p_and_l_usali.gross_operating_profit.gop_usd",
        # T-12 prod
        "p_and_l_usali.gross_operating_profit_usd",
        # Annual P&L prod (the 2022 schema variant Sam hit — the
        # extractor nests dollar + margin siblings under
        # `p_and_l_usali.gop.*`; the explicit dollar alias here means
        # the token-match v3 fallback never runs and `gop_margin_pct`
        # can't beat the dollar field on path length).
        "p_and_l_usali.gop.gross_operating_profit_usd",
        "p_and_l_usali.gop.gop_usd",
        "p_and_l_usali.gop.total_usd",
        "p_and_l_usali.gross_operating_profit.total_usd",
        "p_and_l_usali.gross_operating_profit.total",
    ),
    "noi": (
        "noi_usd",
        "p_and_l_usali.noi",
        "net_operating_income",
        "p_and_l_usali.net_operating_income",
        "p_and_l_usali.net_operating_income.noi_usd",
        # Same nested-sibling pattern as gop above.
        "p_and_l_usali.noi.noi_usd",
        "p_and_l_usali.noi.net_operating_income_usd",
        "p_and_l_usali.noi.total_usd",
        # Real prod emits EBITDA-less-reserve which is a reasonable
        # NOI proxy when the doc never publishes a NOI line directly
        # (the rule catalog's NOI margin band is generous enough that
        # EBITDA-less-reserve falls inside it).
        "p_and_l_usali.ebitda_less_replacement_reserve_usd",
        "p_and_l_usali.ebitda_less_replacement_reserve.total_usd",
    ),
    # Fees / reserves / fixed — schema-doc emits under
    # ``p_and_l_usali.fees_and_reserves.*`` + ``fixed_charges.*``; real
    # prod emits ``p_and_l_usali.management_fees_usd`` (T-12) and
    # ``p_and_l_usali.management_fees.total_usd`` (annual P&L).
    "mgmt_fee": (
        "management_fee",
        "mgmt_fee_usd",
        "p_and_l_usali.mgmt_fee",
        "p_and_l_usali.fees_and_reserves.mgmt_fee",
        "p_and_l_usali.fees_and_reserves.management_fee",
        # T-12 prod
        "p_and_l_usali.management_fees_usd",
        # Annual P&L prod
        "p_and_l_usali.management_fees.total_usd",
        "p_and_l_usali.management_fees.total",
    ),
    "ffe_reserve": (
        "ffe_reserve_usd",
        "p_and_l_usali.ffe_reserve",
        "p_and_l_usali.fees_and_reserves.ffe_reserve",
        # T-12 prod
        "p_and_l_usali.ffe_replacement_reserve_usd",
        # Annual P&L prod
        "p_and_l_usali.ffe_reserve.proforma_calculation_usd",
        "p_and_l_usali.ffe_reserve.total_usd",
    ),
    # Fixed charges = property tax + insurance. Real prod buckets these
    # under ``non_operating`` (not ``fixed_charges``) — covered by the
    # individual aliases for ``insurance_expense`` and ``property_tax``;
    # the rollup is synthesized in ``_derive_usali_rollups``.
    "fixed_charges": (
        "fixed_charges_usd",
        "p_and_l_usali.fixed_charges",
        "p_and_l_usali.fixed_charges.total",
        # Real prod treats fixed charges as the non-operating bucket
        # total (insurance + taxes + rent + other). Match the rollup
        # field name when the extractor emits it.
        "p_and_l_usali.total_non_operating_expenses_usd",
        "p_and_l_usali.non_operating.total_usd",
    ),
    "insurance_expense": (
        "insurance",
        "insurance_usd",
        "p_and_l_usali.insurance",
        "p_and_l_usali.fixed_charges.insurance",
        # Real prod (both T-12 and annual P&L bucket insurance under
        # non_operating, not fixed_charges).
        "p_and_l_usali.non_operating.insurance_usd",
        "p_and_l_usali.non_operating.insurance",
    ),
    "property_tax": (
        "property_taxes",
        "property_tax_usd",
        "p_and_l_usali.property_taxes",
        "p_and_l_usali.fixed_charges.property_taxes",
        # Real prod paths
        "p_and_l_usali.non_operating.property_and_other_taxes_usd",
        "p_and_l_usali.non_operating.property_other_taxes_usd",
        "p_and_l_usali.non_operating.property_taxes",
    ),
    "utilities_expense": (
        "utilities",
        "p_and_l_usali.utilities",
        "p_and_l_usali.undistributed.utilities",
        # T-12 prod (per-dept bucket carries the expense).
        "p_and_l_usali.utilities.expense_usd",
        "p_and_l_usali.undistributed.utilities_usd",
        # Annual P&L prod
        "p_and_l_usali.undistributed_expenses.utilities_usd",
    ),
    "marketing_expense": (
        "marketing",
        "sales_marketing",
        "p_and_l_usali.marketing",
        "p_and_l_usali.undistributed.sales_marketing",
        # T-12 prod
        "p_and_l_usali.sales_and_marketing.expense_usd",
        "p_and_l_usali.undistributed.sales_and_marketing_usd",
        # Annual P&L prod
        "p_and_l_usali.undistributed_expenses.sales_marketing_usd",
    ),
    "rm_expense": (
        "repairs_maintenance",
        "rm",
        "p_and_l_usali.repairs_maintenance",
        "p_and_l_usali.undistributed.property_operations",
        "property_operations",
        # T-12 prod
        "p_and_l_usali.property_operations_and_maintenance.expense_usd",
        "p_and_l_usali.undistributed.property_operations_and_maintenance_usd",
        # Annual P&L prod
        "p_and_l_usali.undistributed_expenses.property_operations_maintenance_usd",
    ),
    "ag_expense": (
        "admin_general",
        "a_and_g",
        "p_and_l_usali.admin_general",
        "p_and_l_usali.undistributed.administrative_general",
        "administrative_general",
        # T-12 prod
        "p_and_l_usali.administrative_and_general.expense_usd",
        "p_and_l_usali.undistributed.administrative_and_general_usd",
        # Annual P&L prod
        "p_and_l_usali.undistributed_expenses.administrative_general_usd",
    ),
    # Information & telecom (one of the 5 undistributed lines — needed
    # for the undistributed rollup synthesis).
    "information_telecom": (
        "information_and_telecom",
        # T-12 prod
        "p_and_l_usali.information_and_telecom.expense_usd",
        "p_and_l_usali.undistributed.information_and_telecom_usd",
        # Annual P&L prod
        "p_and_l_usali.undistributed_expenses.information_telecom_systems_usd",
        # Schema doc
        "p_and_l_usali.undistributed.information_telecom",
    ),
    "total_labor": (
        "labor",
        "labor_cost",
        "p_and_l_usali.total_labor",
        "p_and_l_usali.labor.total",
    ),
    "labor_cost_per_occupied_room": ("labor_per_or", "labor_por"),
    # Department margins. Extractor emits revenue + expense per
    # department; ``rooms_dept_profit`` is derived in
    # ``flatten_extraction_fields`` (rooms_revenue - rooms_dept_expense).
    "rooms_dept_profit": ("rooms_profit", "p_and_l_usali.rooms_dept_profit"),
    "fb_dept_profit": ("fb_profit", "p_and_l_usali.fb_dept_profit"),
    "incentive_mgmt_fee": ("incentive_fee", "p_and_l_usali.incentive_mgmt_fee"),
    "franchise_royalty_fee": ("royalty_fee", "p_and_l_usali.franchise_royalty_fee"),
    "franchise_marketing_fee": ("marketing_program_fee", "p_and_l_usali.franchise_marketing_fee"),
    # Property metadata.
    "keys": ("room_count", "property_overview.keys"),
    "property_value": ("assessed_value", "property_overview.property_value"),
    "purchase_price": ("price", "deal.purchase_price"),
    # Variance / growth (broker vs actual).
    "broker_noi": ("broker_proforma.noi", "proforma_noi"),
    "t12_noi": ("t12.noi", "actual_noi"),
    "broker_occupancy": ("broker_proforma.occupancy", "proforma_occupancy"),
    "t12_occupancy": ("t12.occupancy", "actual_occupancy"),
    "broker_adr": ("broker_proforma.adr", "proforma_adr"),
    "t12_adr": ("t12.adr", "actual_adr"),
    "t12_revpar": ("t12.revpar",),
    "t24_revpar": ("t24.revpar",),
    "revpar_yoy_growth": ("revpar_yoy", "p_and_l_usali.revpar_yoy_growth"),
    # Financing.
    "loan_amount": ("financing.loan_amount", "debt.loan_amount"),
    "annual_debt_service": ("financing.annual_debt_service", "debt_service"),
    "interest_rate": ("financing.interest_rate",),
    "entry_cap_rate": ("financing.entry_cap_rate", "valuation.entry_cap_rate"),
    "exit_cap_rate": ("valuation.exit_cap_rate",),
    "total_capital": ("valuation.total_capital", "total_capitalization"),
    "stabilized_noi": ("valuation.stabilized_noi",),
    # Returns.
    "levered_irr": ("returns.levered_irr",),
    "equity_multiple": ("returns.equity_multiple",),
    # STR comp-set.
    "compset_adr": ("comp_set.adr", "compset.adr_usd"),
    "compset_revpar": ("comp_set.revpar", "compset.revpar_usd"),
    "compset_occupancy": ("comp_set.occupancy", "compset.occupancy_pct"),
    # Other operated.
    "parking_revenue": ("parking_revenue_usd",),
    "spa_revenue": ("spa_revenue_usd",),
    "meeting_space_revenue": ("meeting_revenue", "banquet_revenue"),
    # Seasonality / cross-field — these come from list-of-month data
    # the extractor doesn't ship today; rules that need them will skip
    # via the missing-field path.
}


# Rule families that require an explicit market-context flag the deal
# may not carry. When the flag is absent we emit a
# ``requires_market_context=True`` placeholder INSTEAD of failing the
# rule, and exclude it from ``applicable_count``.
#
# Each entry is ``rule_id → (context_key, human_phrase)``. The scorer
# probes ``context_key`` on the fields dict (False/None → context
# absent) and on a few common alternate spellings. If absent, the
# placeholder is emitted.
_MARKET_CONTEXT_RULES: dict[str, tuple[tuple[str, ...], str]] = {
    "INSURANCE_PER_KEY_COASTAL": (
        ("coastal", "is_coastal", "coastal_market", "market.coastal"),
        "coastal market designation",
    ),
    "MULTI_FIELD_INSURANCE_COASTAL_RISK": (
        ("coastal", "is_coastal", "coastal_market", "market.coastal"),
        "coastal market designation",
    ),
    "MULTI_FIELD_SEASONAL_PATTERN_MISSING": (
        ("seasonal_market", "is_seasonal", "market.seasonal"),
        "seasonal market designation",
    ),
}


# ─────────────────────────── field resolution ───────────────────────────


# Sam QA Bug #3 v3 (June 28 2026) — token-aware fallback resolver
# ─────────────────────────────────────────────────────────────────
#
# Background. v1 expanded the alias map against schema docs. v2 expanded
# it against the saved-fixture prod paths. Both still left Sam's
# day-of-QA upload at "Inconclusive" because the Extractor LLM emits a
# slightly different namespace on every run (``p_and_l_usali.rooms.revenue_usd``
# one day, ``p_and_l_usali.revenues.rooms_usd`` the next, ``p_and_l.rooms_dept.revenue_usd``
# the day after — see the divergence between the T-12 and annual
# fixtures already saved under ``tests/fixtures/real_payloads/``).
#
# A path-by-path alias chase is a losing battle. v3 instead generalizes
# by tokenizing both the canonical name and every flat-payload key on
# ``.``/``_``, expanding canonical tokens through a small synonym map
# (``rooms`` ↔ ``rooms_dept``, ``mgmt`` ↔ ``management``, ``fb`` ↔
# ``food_beverage``, …) and finding the flat key whose token bag is the
# tightest match. Three scoring rules, in order:
#
#   1. Every canonical concept-token must appear in the candidate key's
#      tokens (after synonym expansion). Otherwise the candidate is
#      discarded.
#   2. Discriminator tokens (``revenue`` vs ``expense`` vs ``profit``,
#      ``rooms`` vs ``fb`` vs ``other``) act as REQUIRED filters AND as
#      forbidden filters — a canonical with ``revenue`` rejects keys
#      containing ``expense`` / ``profit``, and vice versa.
#   3. Tie-break: prefer the candidate with the fewest extra tokens,
#      then the shortest path string. Deterministic.
#
# Monthly / page / quarterly / per-month namespaces are dropped entirely
# (they're subordinate slices, never the period total — same exclusion
# logic ``flatten_extraction_fields`` already applies).


# Token synonyms — canonical token → set of acceptable variants. The
# canonical token itself is always a match; the set lists alternate
# spellings the LLM might pick. Conservative — every entry is grounded
# in observed prod payloads or USALI vocabulary.
_TOKEN_SYNONYMS: dict[str, frozenset[str]] = {
    # Departments
    "rooms": frozenset({"rooms", "room"}),
    "fb": frozenset({"fb", "food", "beverage", "fnb"}),
    "other": frozenset({"other", "operated", "miscellaneous", "misc"}),
    "telecom": frozenset({"telecom", "telecommunications", "information"}),
    # Functional groups
    "dept": frozenset({"dept", "departmental", "department"}),
    "undistributed": frozenset({"undistributed", "undist"}),
    "fixed": frozenset({"fixed", "nonoperating", "non"}),
    # Money tokens
    "revenue": frozenset({"revenue", "revenues", "income", "sales"}),
    "expense": frozenset({"expense", "expenses", "cost", "costs"}),
    "profit": frozenset({"profit", "profits", "margin"}),
    # Operations / KPIs
    "occupancy": frozenset({"occupancy", "occ"}),
    "adr": frozenset({"adr"}),
    "revpar": frozenset({"revpar"}),
    # Fees / reserves
    "mgmt": frozenset({"mgmt", "management"}),
    "ffe": frozenset({"ffe", "ff", "replacement", "fixturesandequipment"}),
    "reserve": frozenset({"reserve", "reserves", "replacement"}),
    "fee": frozenset({"fee", "fees"}),
    "incentive": frozenset({"incentive"}),
    "royalty": frozenset({"royalty"}),
    "franchise": frozenset({"franchise"}),
    "marketing": frozenset({"marketing", "sales"}),
    # Charges
    "insurance": frozenset({"insurance"}),
    "property": frozenset({"property"}),
    "tax": frozenset({"tax", "taxes"}),
    "utilities": frozenset({"utilities", "utility"}),
    "labor": frozenset({"labor", "payroll", "wages"}),
    # Roll-ups
    "gop": frozenset({"gop", "grossoperatingprofit"}),
    "noi": frozenset({"noi", "netoperatingincome"}),
    "total": frozenset({"total", "summary", "grand"}),
    # Repairs & maintenance / A&G
    "rm": frozenset({"rm", "repairs", "maintenance", "operations"}),
    "ag": frozenset({"ag", "administrative", "admin", "general"}),
    "resort": frozenset({"resort"}),
    # Variance pairs (broker/t12 references — handled by direct alias).
}


# Each canonical name → ordered list of "concept tokens" it represents
# (after splitting on _, lowercase). Used by the token-match resolver.
# The order is meaningful only for documentation — matching is bag-based.
#
# Concept tokens are STRICT: every entry must appear in the candidate
# key (after synonym expansion). "Discriminator" tokens are listed
# under ``_TOKEN_DISCRIMINATORS`` and disqualify candidates that don't
# carry the right discriminator — e.g. ``rooms_revenue`` rejects keys
# with ``expense`` / ``profit`` even though the rest of the tokens
# match.
def _split_tokens(name: str) -> list[str]:
    """Tokenize a flat-path key like
    ``p_and_l_usali.rooms.revenue_usd`` into
    ``['p', 'and', 'l', 'usali', 'rooms', 'revenue', 'usd']``."""
    return [t for t in name.replace(".", "_").lower().split("_") if t]


# Forbidden-token rules. When the canonical name contains a token from
# the left column, candidate flat keys MUST NOT contain any token from
# the right column — keeps ``rooms_revenue`` from matching
# ``rooms_dept_expense`` just because two tokens overlap.
_TOKEN_FORBIDDEN: dict[str, frozenset[str]] = {
    "revenue": frozenset({"expense", "expenses", "cost", "costs", "profit", "margin"}),
    "expense": frozenset({"revenue", "revenues", "income", "profit", "margin", "sales"}),
    "profit": frozenset({"revenue", "revenues", "income", "expense", "expenses", "cost"}),
    "fee": frozenset({"profit", "margin"}),
    "reserve": frozenset({"profit", "margin"}),
    # GOP / NOI dollar-canonicals must reject margin / pct / ratio
    # candidates. Without this, the token-match v3 fallback prefers
    # `p_and_l_usali.gop.gop_margin_pct` (shorter path, "gop" token
    # appears twice) over `p_and_l_usali.gop.gross_operating_profit_usd`
    # — Sam QA 2026-06-29 saw the broker engine emit
    # "GOP $4.85M → $0" because 0.40 is a valid float and slipped past
    # the dict/list/NaN guard (commit 287f602).
    "gop": frozenset({"margin", "pct", "percent", "percentage", "ratio"}),
    "noi": frozenset({"margin", "pct", "percent", "percentage", "ratio"}),
}


def _expand_with_synonyms(token: str) -> frozenset[str]:
    """Token → its synonym set (always includes the token itself)."""
    syns = _TOKEN_SYNONYMS.get(token)
    if syns is None:
        return frozenset({token})
    return syns


def _has_subordinate_namespace(key: str) -> bool:
    """``True`` for monthly / page / quarterly / per-month slices that
    must never be matched as a period total."""
    lowered = key.lower()
    return (
        ".monthly." in lowered
        or ".page" in lowered
        or ".per_month." in lowered
        or ".quarterly." in lowered
        or ".q1." in lowered
        or ".q2." in lowered
        or ".q3." in lowered
        or ".q4." in lowered
    )


# "Soft" concept tokens. When the canonical contains one of these, the
# candidate match doesn't strictly require it — a candidate with the
# right discriminator tokens but no explicit ``expense`` token still
# counts IF the candidate has a money indicator (``usd``/``dollar``/
# ``amount``) instead. The LLM sometimes emits a money line under an
# expense-flavored bucket without repeating the ``expense`` word, e.g.
# ``p_and_l.utilities_usd`` or ``p_and_l.admin_general_usd`` — both
# carry implicit expense semantics.
#
# Soft tokens still participate in the FORBIDDEN check (we still reject
# candidates with ``revenue`` / ``profit``).
_SOFT_CANONICAL_TOKENS: frozenset[str] = frozenset({
    "expense",
    "revenue",
    "fee",
    "reserve",
    "cost",
})

# Money indicator tokens — when a candidate is missing the soft token
# but has one of these, count it as a soft-match. Keeps ``rooms_sold``
# from matching ``rooms_revenue`` (no money indicator).
_MONEY_INDICATOR_TOKENS: frozenset[str] = frozenset({
    "usd",
    "dollar",
    "dollars",
    "amount",
    "value",
})


def _token_match_candidates(
    canonical: str,
    fields: dict[str, Any],
) -> list[tuple[int, int, str, Any]]:
    """Find every flat-key candidate that satisfies the canonical's
    token bag and the forbidden-token rules. Returns
    ``[(extras, len_path, key, value)]`` so a caller can pick the
    tightest match (fewer extras first, then shorter path)."""
    canonical_tokens = _split_tokens(canonical)
    if not canonical_tokens:
        return []
    # Expand each canonical token to its synonym set. We accept the
    # candidate if every HARD concept token has SOME synonym present
    # in the candidate tokens; SOFT concept tokens (expense/revenue/
    # fee/reserve) only enforce the forbidden filter — see
    # ``_SOFT_CANONICAL_TOKENS``.
    hard_tokens: list[str] = [
        t for t in canonical_tokens if t not in _SOFT_CANONICAL_TOKENS
    ]
    soft_tokens: list[str] = [
        t for t in canonical_tokens if t in _SOFT_CANONICAL_TOKENS
    ]
    hard_expanded: list[frozenset[str]] = [
        _expand_with_synonyms(t) for t in hard_tokens
    ]
    if not hard_expanded and not soft_tokens:
        return []
    if not hard_expanded:
        # A canonical made of only soft tokens (e.g. bare ``revenue``) is
        # too ambiguous to safely resolve via tokens.
        return []
    # Build the forbidden bag — tokens that must NOT appear in any
    # candidate (e.g. ``revenue`` forbids ``expense``). Both hard AND
    # soft canonical tokens contribute forbidden tokens.
    forbidden: set[str] = set()
    for t in canonical_tokens:
        forbidden |= _TOKEN_FORBIDDEN.get(t, frozenset())
    # Don't let synonyms of canonical's own tokens count as forbidden
    # (e.g. canonical contains ``revenue`` AND ``income`` is a synonym —
    # we still want it to match keys with ``income``).
    own_token_synonym_bag: set[str] = set()
    for syns in hard_expanded:
        own_token_synonym_bag |= syns
    for t in soft_tokens:
        own_token_synonym_bag |= _expand_with_synonyms(t)
    forbidden -= own_token_synonym_bag

    candidates: list[tuple[int, int, str, Any]] = []
    for key, value in fields.items():
        if value is None:
            continue
        if _has_subordinate_namespace(key):
            continue
        key_tokens = _split_tokens(key)
        if not key_tokens:
            continue
        key_token_set = set(key_tokens)
        # Forbidden filter — reject if any forbidden token present.
        if forbidden & key_token_set:
            continue
        # Required filter — every HARD canonical concept token must
        # have a synonym present in the candidate's token set.
        if not all(syns & key_token_set for syns in hard_expanded):
            continue
        # Soft-token gate — when the canonical has soft tokens
        # (revenue/expense/fee/reserve/cost), the candidate must EITHER
        # carry the soft token (or a synonym) OR carry a money
        # indicator (usd/dollar/amount/value). Without one of those
        # signals the candidate is too ambiguous (e.g. ``rooms_sold``
        # has the ``rooms`` token but no money signal, so it's not a
        # valid match for ``rooms_revenue``).
        soft_bonus = 0
        if soft_tokens:
            soft_hit = False
            money_hit = bool(_MONEY_INDICATOR_TOKENS & key_token_set)
            for t in soft_tokens:
                if _expand_with_synonyms(t) & key_token_set:
                    soft_hit = True
                    soft_bonus -= 2
                    break
            if not soft_hit and not money_hit:
                continue
            if not soft_hit and money_hit:
                soft_bonus -= 1
        # Score:
        #   - prefer candidates whose token set ALSO contains the soft
        #     token (or one of its synonyms) — they're a tighter match
        #     than candidates relying only on the money indicator.
        #     Encoded as a negative bonus in the "extras" tally.
        #   - fewer extra tokens (tokens not contributing to the
        #     match) → tighter.
        #   - shorter path → tighter on ties.
        match_pool = own_token_synonym_bag
        extras = sum(1 for t in key_tokens if t not in match_pool)
        # ``soft_bonus`` is negative when the soft token matches, so it
        # lowers the sort key (better candidate).
        score = extras + soft_bonus
        candidates.append((score, len(key), key, value))
    return candidates


def _resolve_via_tokens(fields: dict[str, Any], canonical: str) -> Any | None:
    """Token-match fallback — returns the value from the tightest
    candidate or ``None`` if nothing matches. Pure; safe to call on
    every rule eval."""
    cands = _token_match_candidates(canonical, fields)
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], c[1]))
    return cands[0][3]


# Canonicals that the token resolver MUST NOT try — they're either
# multi-word concept phrases the LLM never emits as a path
# (``broker_noi_yoy_growth_with_flat_opex_ratio``) or context-only
# names whose tokens would over-match the payload (e.g. ``keys`` would
# match every ``rooms_sold_total`` / ``available_rooms`` line).
#
# These names are resolved only via the explicit alias map (or direct
# ``fields[name]`` hit). Listed by exact canonical name.
_TOKEN_RESOLVE_BLOCKLIST: frozenset[str] = frozenset({
    # Single-token names whose token is overly common in P&L paths.
    "keys",
    "monthly_revpar",
    # Multi-word cross-field synthetic checks — these never appear as
    # paths in any extractor flavor; they're computed inputs the
    # critic agent fills in separately.
    "broker_noi_yoy_growth_with_flat_opex_ratio",
    "coastal_insurance_yoy_increase",
    "debt_yield_growth_with_dscr_shrinkage",
    "labor_yoy_growth_vs_market_wage_growth",
    "q1_q3_revpar_swing_in_seasonal_market",
    "revenue_growth_in_flat_demand_market",
    "fb_margin_on_select_service_property",
    "year_one_noi_dip_during_pip",
    # Roll-up totals — the token resolver can't disambiguate
    # "total dept expense" from a single per-dept expense line because
    # both share the ``dept`` + ``expense`` token bag. The explicit
    # alias map covers the canonical TOTAL paths
    # (``p_and_l_usali.total_departmental_expense_usd``,
    # ``p_and_l_usali.departmental_expense.total_usd``); when neither
    # alias hits, the synthesis sums the per-dept components instead.
    # Listed here so the token resolver doesn't grab a single per-dept
    # line and mis-report it as the rollup.
    "dept_expenses",
    "total_dept_expense",
    "dept_expenses_by_line",
    "undistributed_expenses",
    "fixed_charges",
    # Cross-field math-identity drift fields used by some catalog
    # rules — none ever appear as a literal extractor path.
})


def _resolve_field(fields: dict[str, Any], canonical: str) -> Any | None:
    """Look up ``canonical`` on ``fields``, then walk the alias list,
    then fall back to the v3 token-match resolver.

    Resolution order (first non-``None`` wins):

    1. Direct hit: ``fields[canonical]``.
    2. Explicit alias map: each entry in ``_ALIASES[canonical]``.
    3. Token-match resolver (v3): tokenize canonical + payload keys,
       require synonym-aware coverage of every concept token, reject
       on discriminator forbidden tokens, pick tightest candidate.

    Step 3 is gated by ``_TOKEN_RESOLVE_BLOCKLIST`` to keep
    single-token / synthetic / list-typed canonicals from over-matching
    the payload.

    Tolerates dotted paths the extractor uses
    (``p_and_l_usali.revpar_usd``) at the top level of ``fields`` —
    they're stored as flat keys with the dot baked in, not as nested
    dicts, so a single ``fields.get(name)`` covers both.

    Returns the raw value (caller numeric-coerces). ``None`` only when
    neither the canonical key, any alias, NOR a token-match produced a
    value.
    """
    val = fields.get(canonical)
    if val is not None:
        return val
    for alias in _ALIASES.get(canonical, ()):
        val = fields.get(alias)
        if val is not None:
            return val
    # v3 fallback — token-aware match. Skipped for names on the
    # blocklist to keep ``keys`` / synthetic-cross-field names from
    # over-matching.
    if canonical in _TOKEN_RESOLVE_BLOCKLIST:
        return None
    return _resolve_via_tokens(fields, canonical)


def _coerce_number(v: Any) -> float | None:
    """Best-effort numeric coerce — booleans rejected, strings stripped of
    common formatting (``$``, ``,``, trailing ``%``)."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            f = float(s)
        except ValueError:
            return None
        if not math.isfinite(f):
            return None
        return f / 100.0 if pct else f
    return None


# ─────────────────────────── safe evaluator ───────────────────────────


class _UnsupportedFormulaError(Exception):
    """Raised when a formula uses an AST node outside the allowlist."""


class _MissingFieldError(Exception):
    """Raised when a formula references a field name that doesn't resolve.

    Treated as "rule not applicable" by the caller (skip, don't fail).
    """

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


# AST node types that are safe to interpret. Everything else (Attribute,
# Subscript, Lambda, Comprehension, Import, ...) raises. ``Index`` is
# pre-3.9 — we don't include it; AST nodes for ``a[0]`` are Subscript
# which we don't allow anyway.
_ALLOWED_BINOPS: tuple[type[ast.AST], ...] = (
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
)
_ALLOWED_UNARYOPS: tuple[type[ast.AST], ...] = (ast.UAdd, ast.USub)
_ALLOWED_CALLS: frozenset[str] = frozenset({"abs", "sum", "min", "max"})


def _evaluate(node: ast.AST, fields: dict[str, Any]) -> float:
    """Recursively interpret an AST node against the fields dict.

    Returns a float. Raises ``_MissingFieldError`` when a referenced
    field is absent (caller treats that as "skip rule"). Raises
    ``_UnsupportedFormulaError`` for any disallowed AST shape.
    """
    if isinstance(node, ast.Expression):
        return _evaluate(node.body, fields)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        raise _UnsupportedFormulaError(
            f"constant {node.value!r} is not numeric"
        )
    if isinstance(node, ast.Name):
        val = _resolve_field(fields, node.id)
        num = _coerce_number(val)
        if num is None:
            raise _MissingFieldError(node.id)
        return num
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise _UnsupportedFormulaError(f"unary op {type(node.op).__name__}")
        operand = _evaluate(node.operand, fields)
        return +operand if isinstance(node.op, ast.UAdd) else -operand
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise _UnsupportedFormulaError(f"binop {type(node.op).__name__}")
        left = _evaluate(node.left, fields)
        right = _evaluate(node.right, fields)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                # Treat zero-denominator as "not applicable" rather than
                # ZeroDivisionError. The most common case is a brand-new
                # property with no revenue yet — failing every ratio rule
                # on it would be misleading.
                raise _MissingFieldError("<zero-denominator>")
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise _MissingFieldError("<zero-denominator>")
            return left // right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise _MissingFieldError("<zero-denominator>")
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise _UnsupportedFormulaError(f"binop {type(node.op).__name__}")
    if isinstance(node, ast.Call):
        # Only bare-name calls to a tiny allowlist.
        if not isinstance(node.func, ast.Name):
            raise _UnsupportedFormulaError("call to non-name")
        fname = node.func.id
        if fname not in _ALLOWED_CALLS:
            raise _UnsupportedFormulaError(f"call to {fname!r}")
        if node.keywords:
            raise _UnsupportedFormulaError(f"{fname}() with kwargs")
        args = [_evaluate(a, fields) for a in node.args]
        if fname == "abs":
            if len(args) != 1:
                raise _UnsupportedFormulaError("abs() takes 1 arg")
            return abs(args[0])
        if fname == "sum":
            return float(sum(args)) if args else 0.0
        if fname == "min":
            if not args:
                raise _UnsupportedFormulaError("min() requires args")
            return float(min(args))
        if fname == "max":
            if not args:
                raise _UnsupportedFormulaError("max() requires args")
            return float(max(args))
    if isinstance(node, ast.Compare):
        # Not used by the current catalog but harmless to support.
        left = _evaluate(node.left, fields)
        for op, comp in zip(node.ops, node.comparators, strict=True):
            right = _evaluate(comp, fields)
            ok: bool
            if isinstance(op, ast.Eq):
                ok = left == right
            elif isinstance(op, ast.NotEq):
                ok = left != right
            elif isinstance(op, ast.Lt):
                ok = left < right
            elif isinstance(op, ast.LtE):
                ok = left <= right
            elif isinstance(op, ast.Gt):
                ok = left > right
            elif isinstance(op, ast.GtE):
                ok = left >= right
            else:
                raise _UnsupportedFormulaError(f"compare op {type(op).__name__}")
            if not ok:
                return 0.0
            left = right
        return 1.0
    raise _UnsupportedFormulaError(type(node).__name__)


# ─────────────────────────── rule classification ───────────────────────────


def _is_math_identity(rule: USALIRule) -> bool:
    """Math identities use threshold_min=0 and a tight threshold_max
    (~0.005-0.01) on a *relative drift* expression like ``abs(... )/...``.

    We detect by category=='Math' or by the formula starting with
    ``abs(`` and the threshold_min being 0. The "cross_field" category
    also carries identities (``MULTI_FIELD_REVPAR_INTERNAL_INCONSISTENCY``).
    """
    if (rule.category or "").lower() in ("math", "variance", "cross_field"):
        # Some variance rules ARE drifts (abs(a-b)/b). Confirm via formula.
        if rule.formula_or_check.strip().startswith("abs("):
            return True
        # Pure variance like "abs(broker_occupancy - t12_occupancy)" is
        # also an identity-style check (drift should be ≤ max).
        if (
            rule.threshold_min in (0, 0.0)
            and rule.threshold_max is not None
            and rule.threshold_max <= 0.5
        ):
            return True
    return False


def _has_market_context_dependency(
    rule: USALIRule, fields: dict[str, Any]
) -> tuple[bool, str]:
    """Return ``(needs_context, missing_phrase)``.

    ``needs_context`` is True when the rule is in
    ``_MARKET_CONTEXT_RULES`` AND the deal payload doesn't carry the
    flag the rule needs. ``missing_phrase`` is a short human label for
    the deviation message.
    """
    cfg = _MARKET_CONTEXT_RULES.get(rule.rule_id)
    if cfg is None:
        return False, ""
    context_keys, phrase = cfg
    for key in context_keys:
        v = fields.get(key)
        if isinstance(v, bool):
            if v:
                return False, ""
        elif v not in (None, "", 0):
            return False, ""
    return True, phrase


# ─────────────────────────── deviation messages ───────────────────────────


def _fmt_pct(v: float) -> str:
    """``0.082 → "8.2%"``. Used when the formula yields a ratio."""
    return f"{v * 100:.1f}%"


def _looks_like_ratio(rule: USALIRule) -> bool:
    """A ratio rule's formula contains ``/`` AND its threshold range sits
    within ``(0, 5)`` — wide enough to admit margins (0.10..0.45) and
    fee ratios (0.02..0.06) without catching dollar-denominated checks
    like ``insurance_expense / keys`` (which is 500..2500).
    """
    if "/" not in rule.formula_or_check:
        return False
    return (
        rule.threshold_max is not None
        and rule.threshold_max <= 5.0
    )


def _format_message(
    rule: USALIRule,
    actual: float | None,
    is_identity: bool,
) -> str:
    """Build a human-readable deviation message.

    Identity violations report the relative drift in percent; ratio
    rules report the actual value as a percent; everything else reports
    the raw number. The threshold range is always echoed back so the
    UI can show the analyst exactly what would have passed.
    """
    name = rule.name
    if actual is None:
        return f"{name}: could not evaluate (missing inputs)."
    if is_identity:
        return (
            f"{name}: drift of {_fmt_pct(actual)} exceeds "
            f"{_fmt_pct(rule.threshold_max or 0.005)} tolerance "
            f"— the reported number doesn't reconcile."
        )
    if _looks_like_ratio(rule):
        lo = _fmt_pct(rule.threshold_min) if rule.threshold_min is not None else "?"
        hi = _fmt_pct(rule.threshold_max) if rule.threshold_max is not None else "?"
        return (
            f"{name}: {_fmt_pct(actual)} falls outside typical {lo}-{hi} range."
        )
    lo = (
        f"{rule.threshold_min:g}"
        if rule.threshold_min is not None
        else "?"
    )
    hi = (
        f"{rule.threshold_max:g}"
        if rule.threshold_max is not None
        else "?"
    )
    return f"{name}: {actual:g} falls outside typical {lo}-{hi} range."


# ─────────────────────────── core scoring ───────────────────────────


def score_extraction(
    fields: dict[str, Any],
    *,
    rules: list[USALIRule] | None = None,
) -> USALIScore:
    """Score a P&L extraction against the USALI catalog.

    Args:
        fields: flat ``{name: value}`` dict of extracted fields. Keys
            may be canonical (``revpar``) or any of the alternates
            listed in ``_ALIASES`` (``p_and_l_usali.revpar_usd``).
            Numeric strings ("$185.40", "74%") are coerced.
        rules: optional override of the rule catalog — defaults to
            ``load_usali_rules()`` (the canonical 66-rule CSV).

    Returns: a ``USALIScore`` whose ``score`` is ``None`` (inconclusive)
    when fewer than ``_INCONCLUSIVE_FLOOR=5`` rules were applicable,
    else a 0-100 percentage.
    """
    rules = rules if rules is not None else load_usali_rules()
    fields = fields or {}

    applicable = 0
    passed = 0
    deviations: list[USALIDeviation] = []

    for rule in rules:
        formula = (rule.formula_or_check or "").strip()
        if not formula:
            continue

        # Market-context guard: rules that explicitly need a deal-level
        # flag the payload may not carry get parked instead of failed.
        needs_ctx, missing_phrase = _has_market_context_dependency(rule, fields)
        if needs_ctx:
            deviations.append(
                USALIDeviation(
                    rule_id=rule.rule_id,
                    rule_name=rule.name,
                    severity=rule.severity or "INFO",
                    message=(
                        f"{rule.name}: requires {missing_phrase} — "
                        "evaluate once deal context is provided."
                    ),
                    actual_value=None,
                    threshold_min=rule.threshold_min,
                    threshold_max=rule.threshold_max,
                    requires_market_context=True,
                )
            )
            continue

        try:
            tree = ast.parse(formula, mode="eval")
        except SyntaxError:
            logger.debug(
                "usali_scorer: rule %s has unparseable formula %r — skipping",
                rule.rule_id,
                formula,
            )
            continue

        try:
            value = _evaluate(tree, fields)
        except _MissingFieldError as exc:
            # Missing inputs ⇒ not applicable; don't penalize.
            # Debug-log the canonical name that didn't resolve so a
            # future QA cycle can see exactly which alias / token
            # match needs widening.
            logger.debug(
                "usali_scorer: rule %s skipped — %s not resolved on payload",
                rule.rule_id,
                exc.name,
            )
            continue
        except _UnsupportedFormulaError as exc:
            # Catalog rule references an AST shape we don't model — skip
            # rather than crash. Logged so a future catalog change is
            # observable.
            logger.debug(
                "usali_scorer: rule %s uses unsupported formula %r (%s) — skipping",
                rule.rule_id,
                formula,
                exc,
            )
            continue
        except Exception as exc:  # noqa: BLE001 - defensive
            logger.warning(
                "usali_scorer: unexpected eval error on rule %s: %s",
                rule.rule_id,
                exc,
            )
            continue

        applicable += 1
        is_identity = _is_math_identity(rule)

        # Identity rules pass when |drift| ≤ threshold_max.
        if is_identity:
            tol = (
                rule.threshold_max
                if rule.threshold_max is not None
                else 0.005
            )
            ok = abs(value) <= tol + 1e-12
        else:
            lo = rule.threshold_min
            hi = rule.threshold_max
            ok = True
            if lo is not None and value < lo - 1e-12:
                ok = False
            if hi is not None and value > hi + 1e-12:
                ok = False

        if ok:
            passed += 1
            continue

        deviations.append(
            USALIDeviation(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                severity=rule.severity or "INFO",
                message=_format_message(rule, value, is_identity),
                actual_value=value,
                threshold_min=rule.threshold_min,
                threshold_max=rule.threshold_max,
                requires_market_context=False,
            )
        )

    inconclusive = applicable < _INCONCLUSIVE_FLOOR
    score: float | None
    if inconclusive or applicable == 0:
        score = None
    else:
        score = round(100.0 * passed / applicable, 2)

    return USALIScore(
        score=score,
        applicable_count=applicable,
        passed_count=passed,
        deviations=deviations,
        inconclusive=inconclusive,
    )


# ─────────────────────────── JSONB serializer ───────────────────────────


def deviations_to_jsonb(score: USALIScore) -> dict[str, Any]:
    """Turn a ``USALIScore`` into the JSONB shape we persist on the
    documents row.

    The persisted shape includes ``inconclusive`` and the applicable
    counts so the UI doesn't need a second query to render an
    "Inconclusive (3 of 4 rules)" badge.
    """
    return {
        "inconclusive": score.inconclusive,
        "applicable_count": score.applicable_count,
        "passed_count": score.passed_count,
        "deviations": [
            {
                "rule_id": d.rule_id,
                "rule_name": d.rule_name,
                "severity": d.severity,
                "message": d.message,
                "actual_value": d.actual_value,
                "threshold_min": d.threshold_min,
                "threshold_max": d.threshold_max,
                "requires_market_context": d.requires_market_context,
            }
            for d in score.deviations
        ],
    }


# ─────────────────────────── extraction-payload adapter ───────────────────────────


def flatten_extraction_fields(
    fields: list[dict[str, Any]],
    *,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convert the extractor's list-of-records into a flat
    ``{name: value}`` dict the scorer can read.

    The extractor emits ``[{"field_name": "p_and_l_usali.revpar_usd",
    "value": 137.2, ...}, ...]``. We also strip a few common path
    prefixes so a payload that uses ``p_and_l_usali.revpar_usd`` works
    the same as one that uses bare ``revpar`` — both end up as
    ``revpar`` in the resolver's lookup chain (we keep both forms so
    direct alias hits still work).

    ``extra_context`` is merged in last and wins over extractor values
    when both are present — it carries deal-level fields like
    ``coastal``, ``keys`` from the deals row, ``purchase_price``, etc.

    Sam QA Bug #3 v4 (June 28 2026) — structural recognizer pre-pass:
    BEFORE the path-flattening + alias map + token resolver chain runs,
    we ask the structural recognizer (``services/structural_recognizer``)
    to walk the raw payload and surface every canonical USALI line item
    it can find via regex-on-key-names at any nesting depth. The
    recognizer's surfaced canonical values are written to the flat dict
    under their canonical names (``rooms_revenue``, ``property_tax``,
    ``gop``, …) before any other key. This is the v4 fix: the rule
    catalog's formulas reference canonical names directly — once the
    recognizer has populated them, the resolver chain becomes a
    fallback, not the main load-bearing path. The v3 token resolver is
    still wired in below to backstop any concept the structural
    recognizer's pattern catalog hasn't memorialized yet (defensive).
    """
    flat: dict[str, Any] = {}

    # ── v4 structural-recognizer pre-pass ──
    #
    # Pull every recognizable canonical line item out of the raw
    # payload BEFORE the path-flattening pass. The recognizer doesn't
    # care which namespace the LLM picked — it matches regex on key
    # names at every depth — so even when the LLM ships a freshly
    # invented namespace (e.g. ``hotel_revenues.rooms_segment.gross``)
    # the canonical concept gets surfaced. Writing the canonical
    # values FIRST means the rule catalog's formulas resolve
    # immediately without going through the alias map / token
    # resolver — the v3 chain becomes a backstop instead of the
    # main load-bearing path. Import-locally so callers that skip
    # ``flatten_extraction_fields`` don't pay the import cost.
    try:
        from .structural_recognizer import classify_structure

        signals = classify_structure(fields)
        for cname, cval in signals.canonical_values.items():
            flat[cname] = cval
    except Exception as exc:  # noqa: BLE001 - defensive
        # Recognizer failure is never a gate — the v1/v2/v3 chain still
        # runs below. Logged so a future schema change is observable.
        logger.debug("usali_scorer: structural recognizer failed: %s", exc)

    for f in fields or []:
        if not isinstance(f, dict):
            continue
        name = (f.get("field_name") or "").strip()
        if not name:
            continue
        value = f.get("value")
        if value is None:
            continue
        # Raw extractor paths (e.g. ``p_and_l_usali.rooms.revenue_usd``)
        # are written under their literal name — the recognizer wrote
        # under the canonical name (``rooms_revenue``), so the two don't
        # collide. The dotted-path key is still needed for direct hits
        # the alias map enumerates AND for the v3 token resolver.
        flat[name] = value
        # Also expose the last path component so a payload using
        # ``p_and_l_usali.revpar_usd`` becomes resolvable under
        # ``revpar_usd`` (which the alias map already maps to
        # canonical ``revpar``).
        #
        # Sam QA Bug #3 v2: SKIP the tail-write for monthly / per-page
        # records. The real prod T-12 ships dozens of
        # ``p_and_l_usali.monthly.jan_2025.rooms_revenue_usd`` entries
        # — tail-writing them clobbers ``rooms_revenue_usd`` with a
        # single-month figure, which then leaks through the alias map
        # and lands as the per-period ``rooms_revenue`` (1M instead of
        # the 9M actual TTM total). The monthly/page namespaces are
        # subordinate slices, never the period total.
        if "." in name:
            lowered = name.lower()
            if (
                ".monthly." in lowered
                or ".page" in lowered  # ``.page5.`` is a real prod alias
                or ".per_month." in lowered
            ):
                continue
            tail = name.rsplit(".", 1)[-1]
            # First write wins so a direct flat hit (e.g. "revpar") on
            # a later record doesn't clobber an earlier one.
            flat.setdefault(tail, value)
    if extra_context:
        for k, v in extra_context.items():
            if v is None:
                continue
            flat[k] = v

    # ─── Derive USALI roll-ups from line items ───
    #
    # Sam QA Bug #3 (June 2026): real T-12s (200+ extracted fields)
    # were scoring "Inconclusive — too few applicable rules" because
    # the catalog rules reference ``total_revenue``, ``gop``,
    # ``dept_expenses``, ``undistributed_expenses``, ``fixed_charges``,
    # and the dept-profit margins — fields the extractor does NOT
    # emit directly (it emits per-line items per
    # ``extraction_schemas/t12.md``). Synthesize them here so the
    # scorer can evaluate margin / ratio / identity rules. Each
    # derived field uses ``setdefault`` so a direct extractor emission
    # always wins.
    _derive_usali_rollups(flat)
    return flat


def _coerce_for_sum(v: Any) -> float | None:
    """Numeric coerce for the roll-up derivations (rejects booleans /
    NaN). Kept private + duplicated from ``_coerce_number`` so it's
    easy to inline in the hot path without import gymnastics."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        pct = s.endswith("%")
        if pct:
            s = s[:-1]
        try:
            f = float(s)
        except ValueError:
            return None
        if not math.isfinite(f):
            return None
        return f / 100.0 if pct else f
    return None


def _derive_usali_rollups(flat: dict[str, Any]) -> None:
    """Compute roll-up totals from extractor line items, in place.

    Called from ``flatten_extraction_fields`` AFTER the per-record
    flatten pass + tail-write so every derivation sees the full
    component set. Each write is ``setdefault`` so an extractor-emitted
    canonical value (rare but possible — e.g. a synthesized T-12
    workbook with a "Total Revenue" row) always wins.
    """

    def _get(*keys: str) -> float | None:
        for k in keys:
            v = _coerce_for_sum(flat.get(k))
            if v is not None:
                return v
        return None

    # ── Resolver helper that uses the FULL resolution chain ──
    #
    # ``_get`` above only walks a literal keys list. For the roll-up
    # synthesis we want to honor every alias for a canonical name AND
    # the v3 token-match fallback — otherwise the synthesis can return
    # None on a payload shape the explicit alias map doesn't cover but
    # the token resolver does. ``_via_alias`` runs the canonical →
    # aliases chain in ``_ALIASES`` and then the token resolver.
    def _via_alias(canonical: str) -> float | None:
        return _coerce_for_sum(_resolve_field(flat, canonical))

    # ``_setdefault_synth`` is the synthesis-aware setter: we only write
    # the synthesized value if the canonical name does NOT already
    # resolve through any path (direct hit / explicit alias / token
    # match). Otherwise the synthesis would mask the LLM's actual
    # emission on a non-canonical path (e.g. ``p_and_l.gop_usd`` ⇒
    # token-resolves to ``gop``; the synthesis must defer).
    #
    # NOTE: We compare against the value-presence on the canonical key
    # itself when writing — once we write it, ``flat[canonical]``
    # exists; future synthesis steps that read via ``_via_alias`` will
    # pick up the cached canonical value (so the call order matters).
    def _setdefault_synth(canonical: str, value: float | int) -> None:
        if canonical in flat:
            return
        # Check the full resolution chain: if the canonical resolves via
        # any path, don't overwrite with the synthesis. We DO still
        # write the canonical key (cached) so downstream synthesis
        # steps don't have to re-traverse the chain.
        existing = _resolve_field(flat, canonical)
        if existing is not None:
            flat[canonical] = existing
            return
        flat[canonical] = value

    # total_revenue = rooms_revenue + fb_revenue + other_revenue
    #                 + resort_fees + misc_revenue
    rooms_rev = _via_alias("rooms_revenue")
    fb_rev = _via_alias("fb_revenue")
    other_rev = _via_alias("other_revenue")
    resort_fees = _via_alias("resort_fees") or 0.0
    misc_rev = _via_alias("misc_revenue") or 0.0
    components = [v for v in (rooms_rev, fb_rev, other_rev) if v is not None]
    if len(components) >= 2:
        # We need at least two components to call a synthesized total
        # meaningful. Resort fees + misc add on when present.
        _setdefault_synth(
            "total_revenue",
            sum(components) + resort_fees + misc_rev,
        )

    # dept_expenses = rooms_dept_expense + fb_dept_expense + other_dept_expense
    rooms_dept_exp = _via_alias("rooms_dept_expense")
    fb_dept_exp = _via_alias("fb_dept_expense")
    other_dept_exp = _via_alias("other_dept_expense") or 0.0
    dept_parts = [v for v in (rooms_dept_exp, fb_dept_exp) if v is not None]
    if dept_parts:
        total_dept = sum(dept_parts) + other_dept_exp
        _setdefault_synth("dept_expenses", total_dept)
        _setdefault_synth("total_dept_expense", total_dept)
        # dept_expenses_by_line is list-typed; never extractor-emitted —
        # safe to write directly with setdefault semantics.
        flat.setdefault(
            "dept_expenses_by_line",
            [v for v in (rooms_dept_exp, fb_dept_exp, other_dept_exp)
             if v is not None and v != 0],
        )

    # undistributed_expenses = sum of the five undistributed lines
    a_g = _via_alias("ag_expense")
    it = _via_alias("information_telecom")
    sm = _via_alias("marketing_expense")
    prop_ops = _via_alias("rm_expense")
    utilities = _via_alias("utilities_expense")
    undist_parts = [v for v in (a_g, it, sm, prop_ops, utilities) if v is not None]
    if len(undist_parts) >= 2:
        _setdefault_synth("undistributed_expenses", sum(undist_parts))

    # fixed_charges = property_taxes + insurance (+ ground rent etc.)
    prop_tax = _via_alias("property_tax")
    insurance = _via_alias("insurance_expense")
    fixed_parts = [v for v in (prop_tax, insurance) if v is not None]
    if fixed_parts:
        _setdefault_synth("fixed_charges", sum(fixed_parts))

    # gop = total_revenue - dept_expenses - undistributed_expenses.
    # We honor a direct GOP emission first (real prod ships it under
    # ``p_and_l_usali.gross_operating_profit_usd`` / ``.total_usd``);
    # the synthesis only fires when it isn't directly emitted.
    tr = _via_alias("total_revenue")
    de = _via_alias("dept_expenses") or _via_alias("total_dept_expense")
    ue = _via_alias("undistributed_expenses")
    if tr is not None and de is not None and ue is not None:
        _setdefault_synth("gop", tr - de - ue)

    # noi can be back-derived if gop + mgmt_fee + ffe_reserve + fixed_charges are known.
    gop_val = _via_alias("gop")
    mgmt_fee = _via_alias("mgmt_fee")
    ffe = _via_alias("ffe_reserve")
    fixed = _via_alias("fixed_charges")
    if (
        gop_val is not None
        and mgmt_fee is not None
        and ffe is not None
        and fixed is not None
    ):
        _setdefault_synth("noi", gop_val - mgmt_fee - ffe - fixed)

    # Department profits — needed for ROOMS_DEPT_MARGIN_* and FB_DEPT_MARGIN_*.
    if rooms_rev is not None and rooms_dept_exp is not None:
        _setdefault_synth("rooms_dept_profit", rooms_rev - rooms_dept_exp)
    if fb_rev is not None and fb_dept_exp is not None:
        _setdefault_synth("fb_dept_profit", fb_rev - fb_dept_exp)

    # Total labor — needed for the LABOR_PCT_REVENUE_* range rules.
    # Real prod doesn't emit a labor line directly (it's embedded in
    # the per-dept expenses), so the rule will skip when missing —
    # that's correct behavior. Kept as a placeholder synthesis in case
    # a future extractor flavor ships a ``total_labor_usd`` line.
    total_labor = _via_alias("total_labor")
    if total_labor is not None:
        _setdefault_synth("total_labor", total_labor)


__all__ = [
    "USALIDeviation",
    "USALIScore",
    "deviations_to_jsonb",
    "flatten_extraction_fields",
    "score_extraction",
]
