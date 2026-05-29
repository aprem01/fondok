"""Engine orchestration — runs the 8 deterministic underwriting engines.

The Run Model button in the web app posts to ``/deals/{id}/engines/run``
or ``/deals/{id}/engines/{name}/run``; the FastAPI handler delegates to
the helpers here. Each engine output is persisted as a row in
``engine_outputs`` so the UI can poll for completion and read back the
last result without re-running the math.

Engine dependency order (used for the run-all chain):

    revenue → fb → expense → capital → debt → returns
                                                  ├─→ sensitivity
                                                  └─→ partnership

When an engine fails its row lands with ``status='failed'`` and a
``error`` blob; downstream dependents that need its output are also
marked failed (with a "skipped: <upstream>" error) so the UI can show a
clear failure path. Independent engines (e.g. ``capital`` does not depend
on ``revenue``) keep running.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from fondok_schemas.financial import ModelAssumptions
from fondok_schemas.partnership import WaterfallTier

from ..engines import (
    CapitalEngine,
    CapitalEngineInput,
    DebtEngine,
    DebtEngineInputExt,
    ExpenseEngine,
    ExpenseEngineInput,
    FBRevenueEngine,
    FBRevenueInput,
    PartnershipEngine,
    PartnershipInputExt,
    ReturnsEngine,
    ReturnsEngineInputExt,
    RevenueEngine,
    SensitivityEngine,
    SensitivityInput,
)
from fondok_schemas.underwriting import RevenueEngineInput

logger = logging.getLogger(__name__)


# Canonical engine identifiers — match what the web app posts.
ENGINE_NAMES: tuple[str, ...] = (
    "revenue",
    "fb",
    "expense",
    "capital",
    "debt",
    "returns",
    "sensitivity",
    "partnership",
)


ENGINE_REGISTRY: dict[str, type] = {
    "revenue": RevenueEngine,
    "fb": FBRevenueEngine,
    "expense": ExpenseEngine,
    "capital": CapitalEngine,
    "debt": DebtEngine,
    "returns": ReturnsEngine,
    "sensitivity": SensitivityEngine,
    "partnership": PartnershipEngine,
}


# Each engine declares the upstream outputs it needs. When any
# dependency failed we mark this engine ``skipped`` rather than running
# it with stale inputs.
ENGINE_DEPS: dict[str, list[str]] = {
    "revenue": [],
    "fb": ["revenue"],
    "expense": ["revenue", "fb"],
    "capital": [],
    "debt": ["expense", "capital"],
    "returns": ["expense", "debt", "capital"],
    "sensitivity": ["returns"],
    "partnership": ["returns", "capital"],
}


# ──────────────────────────── Kimpton fallback ────────────────────────


def _kimpton_assumptions() -> dict[str, Any]:
    """Default underwriting assumptions matching the Kimpton fixture.

    Mirrors ``apps/worker/app/export/fixtures.py`` so a single Run Model
    click on the demo deal reproduces the headline numbers shown in the
    seeded UI (~$4.7M Y1 NOI, ~23% levered IRR).
    """
    return {
        "keys": 132,
        "purchase_price": 36_400_000,
        "starting_occupancy": 0.762,
        "starting_adr": 385.0,
        "occupancy_growth": 0.008,
        "adr_growth": 0.04,
        "fb_revenue_per_occupied_room": 88.0,
        "other_revenue_pct_of_rooms": 0.065,
        "hold_years": 5,
        "hotel_type": "lifestyle",
        "fb_ratio": 0.29,
        "other_ratio": 0.06,
        "mgmt_fee_pct": 0.03,
        "ffe_reserve_pct": 0.04,
        "expense_growth": 0.035,
        "grow_opex_independently": True,
        "renovation_budget": 5_280_000,
        "soft_costs": 528_000,
        "contingency": 528_000,
        "working_capital": 500_000,
        "closing_costs_pct": 0.02,
        "loan_costs_pct": 0.015,
        "ltv": 0.65,
        "interest_rate": 0.068,
        "amortization_years": 30,
        "term_years": 5,
        "interest_only_years": 0,
        "exit_cap_rate": 0.07,
        "revpar_growth": 0.045,
        "selling_costs_pct": 0.02,
        "gp_equity_pct": 0.10,
        "lp_equity_pct": 0.90,
        "pref_rate": 0.08,
    }


# ──────────────────────────── Loading inputs ──────────────────────────


async def _load_engine_inputs(
    session: AsyncSession,
    deal_id: str,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the underwriting assumptions for ``deal_id``.

    Strategy:
        1. Load the deal row (purchase_price, keys) when present.
        2. Layer in caller overrides from the API request body.
        3. Fall back to the Kimpton fixture for everything missing.

    The web app's demo deal id (legacy int 7) does not parse as a UUID
    and never lands in the deals table; that path uses the pure Kimpton
    defaults.
    """
    base = _kimpton_assumptions()
    try:
        # Only try DB lookup when the id is a valid UUID. The Kimpton
        # demo card uses an int-string id which is intentionally
        # outside the deals table.
        UUID(deal_id)
    except (ValueError, TypeError):
        if overrides:
            base.update(overrides)
        return base

    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT keys, purchase_price
                      FROM deals
                     WHERE id = :id
                    """
                ),
                {"id": deal_id},
            )
        ).first()
    except Exception:
        # The migrations may not have been applied for the test DB;
        # fall through to defaults silently.
        row = None

    # Track keys whose values originated on the ``deals`` row so the OM
    # extraction layer below doesn't clobber a user-edited price with the
    # broker's headline (deals table > OM actuals > Kimpton defaults).
    deals_table_keys: set[str] = set()
    if row is not None:
        mapping = row._mapping
        if mapping.get("keys"):
            base["keys"] = int(mapping["keys"])
            deals_table_keys.add("keys")
        if mapping.get("purchase_price"):
            try:
                base["purchase_price"] = float(mapping["purchase_price"])
                deals_table_keys.add("purchase_price")
            except (TypeError, ValueError):
                pass

    # Pull Year-1 T-12 expense actuals from the deal's extraction results
    # so the expense engine can ground synthesis on real numbers (Sam QA
    # #1: synthesized expenses ($457K Insurance vs actual $1.16M;
    # $905K Utilities vs actual $288K) cascaded into wrong DSCR / returns
    # / per-key metrics). Best-effort — partial extraction degrades to
    # ratio synthesis line-by-line.
    base["t12_expense_actuals"] = await _load_t12_expense_actuals(
        session, deal_id=deal_id
    )

    # Same idea on the revenue side (Sam QA #16): when a T-12 has been
    # extracted, prefer the actual occupancy / ADR over the Kimpton seed
    # so the Per-Key tab and downstream rooms-revenue projection reflect
    # the real property instead of the demo defaults.
    revenue_actuals = await _load_t12_revenue_actuals(
        session, deal_id=deal_id
    )
    if "occupancy" in revenue_actuals:
        base["starting_occupancy"] = revenue_actuals["occupancy"]
    if "adr" in revenue_actuals:
        base["starting_adr"] = revenue_actuals["adr"]
    # RevPAR is occupancy × ADR — only consume the extracted RevPAR when
    # one of the two underlying drivers wasn't itself extracted, to avoid
    # contradicting them. (The engine doesn't take RevPAR directly; we
    # back out the missing factor.)
    if (
        "revpar" in revenue_actuals
        and "adr" not in revenue_actuals
        and "occupancy" in revenue_actuals
        and revenue_actuals["occupancy"] > 0
    ):
        base["starting_adr"] = revenue_actuals["revpar"] / revenue_actuals["occupancy"]
    elif (
        "revpar" in revenue_actuals
        and "occupancy" not in revenue_actuals
        and "adr" in revenue_actuals
        and revenue_actuals["adr"] > 0
    ):
        base["starting_occupancy"] = min(
            0.95, revenue_actuals["revpar"] / revenue_actuals["adr"]
        )

    # When the T-12 carries Y1 revenue dollars, derive the engine's
    # per-occupied-room F&B anchor and the other-revenue ratio so the
    # Operating Statement's revenue mix reflects the real property
    # rather than the Kimpton seed (~$88 F&B/occupied room, ~6.5% other).
    # Resort Fees get their OWN engine input (Sam QA #11); we only fold
    # them into the "other" pool as a fallback when an older T-12
    # extraction collapsed them.
    rooms_rev = revenue_actuals.get("rooms_revenue")
    fb_rev = revenue_actuals.get("fb_revenue")
    other_rev = revenue_actuals.get("other_revenue", 0.0) or 0.0
    resort_fees = revenue_actuals.get("resort_fees", 0.0) or 0.0
    misc_rev = revenue_actuals.get("misc_revenue", 0.0) or 0.0
    # When the T-12 broke out Resort Fees as a distinct line, route it
    # to the revenue engine's dedicated ``starting_resort_fees`` input
    # so the Operating Statement renders it on its own row. The
    # remaining "other_pool" then drops resort_fees out — otherwise
    # we'd double-count revenue.
    if resort_fees > 0:
        base["starting_resort_fees"] = resort_fees
        other_pool = other_rev + misc_rev
    else:
        other_pool = other_rev + misc_rev

    starting_occupancy = base.get("starting_occupancy", 0.0)
    keys = base.get("keys", 0)
    occupied_room_nights = (
        starting_occupancy * float(keys) * 365.0
        if starting_occupancy and keys
        else 0.0
    )

    if fb_rev and occupied_room_nights > 0:
        base["fb_revenue_per_occupied_room"] = fb_rev / occupied_room_nights
    if other_pool and rooms_rev and rooms_rev > 0:
        # Cap the ratio at a sane upper bound so a partial extraction
        # (rooms revenue missing, all other_pool present) can't blow up.
        base["other_revenue_pct_of_rooms"] = min(0.30, other_pool / rooms_rev)

    # When the deal has an extracted OM, prefer the broker's published
    # capital + debt numbers over the Kimpton seed so the capital stack
    # and debt service reflect this property rather than the demo
    # defaults. Override priority: deals table > OM actuals > Kimpton
    # defaults — a user-edited purchase price on the deals row is never
    # clobbered by the broker's headline. Best-effort — partial
    # extraction degrades to Kimpton key-by-key.
    capital_actuals = await _load_om_capital_actuals(
        session, deal_id=deal_id
    )
    for key, value in capital_actuals.items():
        if key in deals_table_keys:
            continue
        base[key] = value

    debt_actuals = await _load_om_debt_actuals(
        session, deal_id=deal_id
    )
    for key, value in debt_actuals.items():
        if key in deals_table_keys:
            continue
        base[key] = value

    # External market reports (May 7 scope): when CBRE Horizons has
    # been extracted, derive ADR + RevPAR growth from its 5-year
    # forecast and use that as the revenue engine's growth rate. When
    # the P&L benchmark has landed, use its margins as expense
    # synthesis ratios so unit economics reflect peer-set norms
    # rather than the Kimpton seed.
    cbre_overrides = await _load_cbre_horizons_overrides(
        session, deal_id=deal_id
    )
    for key, value in cbre_overrides.items():
        base[key] = value
    # Wire CBRE Year-1 ADR/RevPAR as the Year-1 anchor when the deal
    # has no T-12 actual on those metrics. Previously cbre_year_1_adr
    # was written to base but never read (Eshan's QA #5: the engine
    # ignored CBRE forecasts even with a CBRE Horizons doc uploaded).
    # T-12 actuals still win — they're analyst-trusted observed values
    # — but in their absence the CBRE forecast is more grounded than
    # the Kimpton seed.
    if "adr" not in revenue_actuals and "cbre_year_1_adr" in cbre_overrides:
        base["starting_adr"] = cbre_overrides["cbre_year_1_adr"]
    if (
        "occupancy" not in revenue_actuals
        and "adr" not in revenue_actuals
        and "cbre_year_1_revpar" in cbre_overrides
        and base.get("starting_occupancy", 0) > 0
    ):
        # Back out a Y1 ADR from CBRE RevPAR + current occupancy
        # baseline when ADR itself isn't surfaced separately.
        starting_occ = base["starting_occupancy"]
        if starting_occ > 0:
            base["starting_adr"] = (
                cbre_overrides["cbre_year_1_revpar"] / starting_occ
            )

    benchmark_overrides = await _load_pnl_benchmark_overrides(
        session, deal_id=deal_id
    )
    if benchmark_overrides:
        # Merge into the engine-defaults override channel so the
        # expense engine's ``HOTEL_TYPE_DEFAULTS`` get patched line
        # by line at run time (rooms_dept_pct, fb_dept_pct, etc.).
        existing = dict(base.get("overrides") or {})
        existing.update(benchmark_overrides.get("expense_overrides", {}))
        base["overrides"] = existing
        for top_level_key in ("mgmt_fee_pct", "ffe_reserve_pct"):
            if top_level_key in benchmark_overrides:
                base[top_level_key] = benchmark_overrides[top_level_key]

    # OM-derived exit-cap anchor — the broker's "Comparable Sales"
    # table gives us market-specific cap rates we should prefer over
    # the 7.0% seed. Analyst overrides via field_overrides still win
    # because they're applied last.
    om_median_cap = await _load_om_transaction_comps_cap_rate(
        session, deal_id=deal_id
    )
    if om_median_cap is not None:
        base["exit_cap_rate"] = om_median_cap

    if overrides:
        base.update(overrides)
    return base


# Map extracted T-12 field paths onto the canonical expense-line keys
# the expense engine recognizes. Both the dotted ``p_and_l_usali.*``
# paths the Extractor agent emits and the bare lowercase aliases the
# legacy normalizer uses are accepted.
_T12_EXPENSE_FIELD_ALIASES: dict[str, str] = {
    # Departmental
    "p_and_l_usali.departmental_expenses.rooms": "rooms_dept_expense",
    "rooms_dept_expense": "rooms_dept_expense",
    "rooms_departmental_expenses": "rooms_dept_expense",
    "p_and_l_usali.departmental_expenses.food_beverage": "fb_dept_expense",
    "fb_dept_expense": "fb_dept_expense",
    "food_beverage_departmental_expenses": "fb_dept_expense",
    "p_and_l_usali.departmental_expenses.other_operated": "other_dept_expense",
    "other_dept_expense": "other_dept_expense",
    # Undistributed
    "p_and_l_usali.undistributed.administrative_general": "administrative_general",
    "administrative_general": "administrative_general",
    "admin_general": "administrative_general",
    "p_and_l_usali.undistributed.information_telecom": "information_telecom",
    "information_telecom": "information_telecom",
    "p_and_l_usali.undistributed.sales_marketing": "sales_marketing",
    "sales_marketing": "sales_marketing",
    "p_and_l_usali.undistributed.property_operations": "property_operations",
    "property_operations": "property_operations",
    "repairs_maintenance": "property_operations",
    "p_and_l_usali.undistributed.utilities": "utilities",
    "utilities": "utilities",
    # Fees & reserves
    "p_and_l_usali.fees_and_reserves.mgmt_fee": "mgmt_fee",
    "mgmt_fee": "mgmt_fee",
    "management_fee": "mgmt_fee",
    "p_and_l_usali.fees_and_reserves.ffe_reserve": "ffe_reserve",
    "ffe_reserve": "ffe_reserve",
    # Fixed charges
    "p_and_l_usali.fixed_charges.property_taxes": "property_taxes",
    "property_taxes": "property_taxes",
    "p_and_l_usali.fixed_charges.insurance": "insurance",
    "insurance": "insurance",
}


# Map extracted T-12 field paths onto the canonical revenue-side keys
# the revenue engine recognizes. As with the expense aliases, we accept
# both the dotted ``p_and_l_usali.operational_kpis.*`` paths the
# Extractor agent emits and the bare aliases the legacy normalizer uses.
_T12_REVENUE_FIELD_ALIASES: dict[str, str] = {
    # Occupancy
    "p_and_l_usali.operational_kpis.occupancy_pct": "occupancy",
    "occupancy_pct": "occupancy",
    "occupancy": "occupancy",
    # ADR
    "p_and_l_usali.operational_kpis.adr_usd": "adr",
    "adr_usd": "adr",
    "adr": "adr",
    # RevPAR
    "p_and_l_usali.operational_kpis.revpar_usd": "revpar",
    "revpar_usd": "revpar",
    "revpar": "revpar",
    # Year-1 revenue dollar amounts. These let the loader derive the
    # engine's per-occupied-room F&B and other-revenue-pct anchors from
    # the actual T-12 instead of the Kimpton seed (~$88 F&B per occupied
    # room, ~6.5% other), so the rooms / F&B / other lines on the
    # Operating Statement reflect the real property's mix.
    "p_and_l_usali.operating_revenue.rooms_revenue": "rooms_revenue",
    "p_and_l_usali.operating_revenue.rooms_revenue_usd": "rooms_revenue",
    "rooms_revenue_usd": "rooms_revenue",
    "rooms_revenue": "rooms_revenue",
    "p_and_l_usali.operating_revenue.food_beverage_revenue": "fb_revenue",
    "p_and_l_usali.operating_revenue.fb_revenue": "fb_revenue",
    "fb_revenue_usd": "fb_revenue",
    "fb_revenue": "fb_revenue",
    "food_beverage_revenue": "fb_revenue",
    "p_and_l_usali.operating_revenue.other_revenue": "other_revenue",
    "other_revenue_usd": "other_revenue",
    "other_revenue": "other_revenue",
    "p_and_l_usali.operating_revenue.resort_fees": "resort_fees",
    "resort_fees_usd": "resort_fees",
    "resort_fees": "resort_fees",
    "p_and_l_usali.operating_revenue.misc_revenue": "misc_revenue",
    "misc_revenue": "misc_revenue",
}


# Rank P&L extraction rows by period_type so an annual T-12 always
# wins over a YTD-through-May or single-month upload — even when the
# monthly was extracted later. Eshan's QA found that a 5/2024 monthly
# upload was clobbering the annual T-12 baseline (~89% YTD occupancy
# vs ~81% true annual). Lower rank = preferred.
_PERIOD_TYPE_RANK: dict[str, int] = {
    "annual": 0,
    "fiscal_year": 0,
    "full_year": 0,
    "trailing_twelve": 1,
    "ttm": 1,
    "t12": 1,
    "rolling_twelve": 1,
    "ytd": 5,
    "year_to_date": 5,
    "quarterly": 7,
    "quarter": 7,
    "monthly": 9,
    "month": 9,
}


def _extract_period_type(raw_fields: list[Any]) -> str:
    """Pull `p_and_l_usali.period_type` (or any `*.period_type`) off a
    flat extraction-fields list. Lower-cases the value so the rank
    map matches.
    """
    for f in raw_fields:
        if not isinstance(f, dict):
            continue
        name = (f.get("field_name") or "").strip().lower()
        if not name.endswith("period_type"):
            continue
        v = f.get("value")
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    return ""


def _rank_pnl_rows(rows: list[Any]) -> list[tuple[list[Any], str]]:
    """Pre-parse + rank P&L extraction rows. Returns (raw_fields_list,
    doc_type) tuples in preference order: best period_type first, then
    newest created_at within a rank tier.
    """
    parsed: list[tuple[int, int, list[Any], str]] = []
    for idx, r in enumerate(rows):
        m = r._mapping
        raw_fields = m["fields"]
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw_fields, list):
            continue
        period_type = _extract_period_type(raw_fields)
        rank = _PERIOD_TYPE_RANK.get(period_type, 50)
        # The SQL ORDER BY created_at DESC already sorted by newest
        # first, so `idx` is a stable created_at proxy — lower idx =
        # newer. We negate it later via the sort key.
        parsed.append((rank, idx, raw_fields, m.get("doc_type") or ""))
    parsed.sort(key=lambda t: (t[0], t[1]))
    return [(p[2], p[3]) for p in parsed]


async def _load_t12_revenue_actuals(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, float]:
    """Read Year-1 occupancy / ADR / RevPAR off the deal's most recent T-12.

    Returns ``{}`` (no overrides — engine falls back to the Kimpton seed)
    when no T-12 has been extracted, when the deal id isn't a UUID, or
    when the migrations haven't been applied to the test DB.

    Occupancy is normalized to a 0..1 fraction (extractors sometimes emit
    it as a percent like ``70.1`` and sometimes as a ratio like ``0.701``).
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND d.doc_type IN ('T12','PNL','PNL_MONTHLY','PNL_YTD')
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return {}

    # Rank-then-merge so a true annual T-12 wins over a YTD-monthly upload
    # even when the monthly was extracted later.
    actuals: dict[str, float] = {}
    for raw_fields, _doc_type in _rank_pnl_rows(rows.fetchall()):
        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not name or not isinstance(value, (int, float)):
                continue
            canonical = _T12_REVENUE_FIELD_ALIASES.get(name)
            if canonical is None:
                tail = name.rsplit(".", 1)[-1] if "." in name else name
                canonical = _T12_REVENUE_FIELD_ALIASES.get(tail)
            if canonical is None or canonical in actuals:
                continue
            v = float(value)
            if canonical == "occupancy":
                # Extractors emit either a 0..1 ratio or a percent.
                if v > 1.0:
                    v = v / 100.0
                v = max(0.0, min(0.99, v))
            actuals[canonical] = v
    return actuals


async def _load_t12_expense_actuals(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, float]:
    """Read Year-1 expense actuals off the deal's most recent T-12 extraction.

    Returns ``{}`` (no overrides — engine falls back to USALI ratios) when
    no T-12 has been extracted, when the deal id isn't a UUID, or when
    the migrations haven't been applied to the test DB.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND d.doc_type IN ('T12','PNL','PNL_MONTHLY','PNL_YTD')
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return {}

    # Rank-then-merge so an annual T-12's expense lines win over a
    # partial-year YTD extract that's missing some buckets.
    actuals: dict[str, float] = {}
    for raw_fields, _doc_type in _rank_pnl_rows(rows.fetchall()):
        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not name or not isinstance(value, (int, float)):
                continue
            canonical = _T12_EXPENSE_FIELD_ALIASES.get(name)
            if canonical is None:
                # Try the last segment of a dotted path as a fallback.
                tail = name.rsplit(".", 1)[-1] if "." in name else name
                canonical = _T12_EXPENSE_FIELD_ALIASES.get(tail)
            if canonical is None or canonical in actuals:
                continue
            v = float(value)
            # Zero-leak guard (Eshan's NOI bug): every real hotel has
            # admin & general, sales & marketing, utilities, property
            # ops, mgmt fee, FF&E reserve, fixed charges. Extractor
            # emits 0.0 when it can't find the line. If we honored that
            # zero, the engine wrote it into the projection P&L and NOI
            # collapsed to ~100% of revenue. Drop zeros and let the
            # USALI ratio fallback supply the missing line. Negative
            # values are nonsense for expenses.
            if v <= 0.0:
                continue
            actuals[canonical] = v
    return actuals


# Map extracted OM field paths onto the canonical capital-side keys the
# capital engine recognizes. The Extractor agent emits OM fields under
# the ``broker_proforma.*``, ``asking_price.*``, ``in_place_debt.*`` and
# ``property_overview.*`` roots (see apps/worker/app/agents/extractor.py
# SYSTEM_PROMPT). Both the dotted root paths and the bare last-segment
# aliases are accepted so partial extractions still flow through.
_OM_CAPITAL_FIELD_ALIASES: dict[str, str] = {
    # Asking price → purchase price (the headline number on the OM).
    "asking_price.headline_price_usd": "purchase_price",
    "asking_price.purchase_price": "purchase_price",
    "headline_price_usd": "purchase_price",
    # Per-key price (informational; not consumed by the capital engine
    # directly but threaded through so the assumption dict carries it).
    "asking_price.price_per_key_usd": "price_per_key",
    "price_per_key_usd": "price_per_key",
    # Renovation budget — the broker's published PIP / capex budget.
    "broker_proforma.renovation_budget_usd": "renovation_budget",
    "renovation_budget_usd": "renovation_budget",
    "renovation_budget": "renovation_budget",
    # Entry cap rate — used by the capital engine when present.
    "broker_proforma.entry_cap_rate": "entry_cap_rate",
    "broker_proforma.cap_rate": "entry_cap_rate",
    "entry_cap_rate": "entry_cap_rate",
    "cap_rate": "entry_cap_rate",
    # Year built (informational, for completeness).
    "property_overview.year_built": "year_built",
    "year_built": "year_built",
}


# Map extracted OM field paths onto the canonical debt-side keys the
# debt engine recognizes. ``in_place_debt.*`` carries the broker's quote
# of the seller's existing financing; we let it override the LTV-derived
# default loan amount when the broker publishes a hard balance.
_OM_DEBT_FIELD_ALIASES: dict[str, str] = {
    "in_place_debt.loan_balance_usd": "loan_amount",
    "loan_balance_usd": "loan_amount",
    "in_place_debt.interest_rate_pct": "interest_rate",
    "interest_rate_pct": "interest_rate",
    "in_place_debt.amortization_years": "amortization_years",
    "amortization_years": "amortization_years",
    "in_place_debt.term_years": "term_years",
    "term_years": "term_years",
    "in_place_debt.ltv_pct": "ltv",
    "ltv_pct": "ltv",
}


# Canonical keys whose extracted value lands in the assumption dict as a
# 0..1 fraction. Extractors emit either a 0..1 ratio or a 0..100 percent;
# we normalize defensively (mirrors the T-12 occupancy normalization).
_OM_PERCENTAGE_KEYS: frozenset[str] = frozenset(
    {"entry_cap_rate", "interest_rate", "ltv"}
)


async def _load_deal_overrides(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, Any]:
    """Read the deal's `field_overrides` JSONB column.

    Returns ``{}`` for non-UUID ids, missing rows, or schemas where the
    migration hasn't run yet (test DBs). The column is keyed by canonical
    extractor field path (e.g. ``property_overview.year_built``) →
    primitive value.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return {}
    try:
        row = (
            await session.execute(
                text("SELECT field_overrides FROM deals WHERE id = :id"),
                {"id": deal_id},
            )
        ).first()
    except Exception:
        return {}
    if row is None:
        return {}
    raw = row._mapping.get("field_overrides")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _apply_overrides(
    actuals: dict[str, float],
    overrides: Mapping[str, Any],
    aliases: Mapping[str, str],
    *,
    percentage_keys: frozenset[str] = frozenset(),
) -> None:
    """Layer per-field overrides on top of extracted actuals in-place.

    Each override key is run through the same alias map the extracted
    loader uses, so an analyst editing ``property_overview.year_built``
    on the Overview lands on the same canonical key the engine reads.
    Non-numeric overrides are dropped silently — only the descriptive
    OM fields are editable from the UI today.
    """
    for path, value in overrides.items():
        if not isinstance(value, (int, float)):
            continue
        name = (path or "").strip().lower()
        canonical = aliases.get(name)
        if canonical is None:
            tail = name.rsplit(".", 1)[-1] if "." in name else name
            canonical = aliases.get(tail)
        if canonical is None:
            continue
        v = float(value)
        if canonical in percentage_keys and v > 1.0:
            v = v / 100.0
        actuals[canonical] = v


async def _load_om_capital_actuals(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, float]:
    """Read capital-side broker numbers off the deal's most recent OM.

    Returns ``{}`` (no overrides — engine falls back to the Kimpton seed)
    when no OM has been extracted, when the deal id isn't a UUID, or
    when the migrations haven't been applied to the test DB.

    Percentage-style keys (``entry_cap_rate``) are normalized from a
    ``0..100`` percent to a ``0..1`` fraction when the broker emitted the
    raw percent.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return {}

    actuals: dict[str, float] = {}
    for r in rows.fetchall():
        raw_fields = r._mapping["fields"]
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw_fields, list):
            continue
        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not name or not isinstance(value, (int, float)):
                continue
            canonical = _OM_CAPITAL_FIELD_ALIASES.get(name)
            if canonical is None:
                tail = name.rsplit(".", 1)[-1] if "." in name else name
                canonical = _OM_CAPITAL_FIELD_ALIASES.get(tail)
            if canonical is None or canonical in actuals:
                continue
            v = float(value)
            if canonical in _OM_PERCENTAGE_KEYS and v > 1.0:
                v = v / 100.0
            actuals[canonical] = v
    # Analyst overrides win over extracted broker numbers.
    overrides = await _load_deal_overrides(session, deal_id=deal_id)
    _apply_overrides(
        actuals,
        overrides,
        _OM_CAPITAL_FIELD_ALIASES,
        percentage_keys=_OM_PERCENTAGE_KEYS,
    )
    return actuals


async def _load_om_transaction_comps_cap_rate(
    session: AsyncSession,
    *,
    deal_id: str,
) -> float | None:
    """Derive a median exit-cap-rate anchor from OM transaction comps.

    The Extractor emits ``transaction_comps.<n>.cap_rate_pct`` per
    broker-published comp. When 3+ comps are present we take the median
    as the exit-cap-rate prior — gives the deal a market-specific
    anchor instead of the 7.0% Kimpton seed. Eshan's QA #5: "OM exit-
    cap median wired up to market.py but never reaches ModelAssumptions."

    Returns the median as a 0..1 fraction (extractor may emit either),
    or ``None`` when fewer than 3 cap-rate-bearing comps are extracted.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return None
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return None

    cap_rates: list[float] = []
    for r in rows.fetchall():
        raw_fields = r._mapping["fields"]
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw_fields, list):
            continue
        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not name.startswith("transaction_comps."):
                continue
            if not name.endswith(".cap_rate_pct") and not name.endswith(".cap_rate"):
                continue
            if not isinstance(value, (int, float)):
                continue
            v = float(value)
            if v <= 0:
                continue
            # Normalize 0..100 percent → 0..1 fraction.
            if v > 1.0:
                v = v / 100.0
            # Sanity bounds: any "cap rate" outside 3-15% is broken data.
            if 0.03 <= v <= 0.15:
                cap_rates.append(v)

    if len(cap_rates) < 3:
        return None
    cap_rates.sort()
    n = len(cap_rates)
    if n % 2:
        return cap_rates[n // 2]
    return (cap_rates[n // 2 - 1] + cap_rates[n // 2]) / 2


async def _load_om_debt_actuals(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, float]:
    """Read in-place debt terms off the deal's most recent OM extraction.

    Returns ``{}`` (no overrides — engine falls back to the Kimpton seed)
    when no OM has been extracted, when the deal id isn't a UUID, or
    when the migrations haven't been applied to the test DB.

    Percentage-style keys (``interest_rate``, ``ltv``) are normalized
    from ``0..100`` percent to ``0..1`` fraction when the broker emitted
    the raw percent.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return {}

    actuals: dict[str, float] = {}
    for r in rows.fetchall():
        raw_fields = r._mapping["fields"]
        if isinstance(raw_fields, str):
            try:
                raw_fields = json.loads(raw_fields)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw_fields, list):
            continue
        for f in raw_fields:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not name or not isinstance(value, (int, float)):
                continue
            canonical = _OM_DEBT_FIELD_ALIASES.get(name)
            if canonical is None:
                tail = name.rsplit(".", 1)[-1] if "." in name else name
                canonical = _OM_DEBT_FIELD_ALIASES.get(tail)
            if canonical is None or canonical in actuals:
                continue
            v = float(value)
            if canonical in _OM_PERCENTAGE_KEYS and v > 1.0:
                v = v / 100.0
            actuals[canonical] = v
    # Analyst overrides win over extracted broker numbers.
    overrides = await _load_deal_overrides(session, deal_id=deal_id)
    _apply_overrides(
        actuals,
        overrides,
        _OM_DEBT_FIELD_ALIASES,
        percentage_keys=_OM_PERCENTAGE_KEYS,
    )
    return actuals


# ─────────────────── CBRE Horizons projection overrides ─────────────────


async def _load_cbre_horizons_overrides(
    session: AsyncSession, *, deal_id: str
) -> dict[str, float]:
    """Translate the deal's extracted CBRE Horizons report into engine
    growth-rate overrides.

    Reads BOTH field-path conventions emitted by the extractor:

    * Legacy: ``cbre_horizons.year_<i>.{adr_usd,revpar_usd}`` where ``i``
      is the 1-indexed forecast year (older mock reports).
    * Real CBRE Hotel Horizons: ``cbre_horizons.segment_<scope>.<YYYY>.{adr_usd,revpar_usd}``
      where ``<scope>`` ∈ ``{all, upper_priced, mid_priced, lower_priced}``
      and ``<YYYY>`` is the calendar year. We use ``segment_all`` as the
      anchor today; positioning-based segment selection is a follow-up.

    CAGR is computed over the FORECAST window — for the segment paths we
    keep only years strictly greater than the most recent historical
    year (forecast period) so backward-looking history doesn't pollute
    the growth rate.

    Also surfaces ``long_run_avg_revpar_growth`` (the next-4-quarters
    Long Run Average block) for use by the broker-vs-market variance
    flag — broker projections that exceed the long-run average by more
    than 2 percentage points warrant a warning.

    Returns ``{}`` on missing data or when neither path yields a
    valid CAGR.
    """
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND UPPER(COALESCE(d.doc_type, '')) = 'CBRE_HORIZONS'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return {}

    # Legacy (1-indexed forecast-year) path
    legacy_adr: dict[int, float] = {}
    legacy_revpar: dict[int, float] = {}
    # Real CBRE segmented path: segment_<scope>.<YYYY>.<metric>
    segment_adr: dict[str, dict[int, float]] = {}
    segment_revpar: dict[str, dict[int, float]] = {}
    # ``period`` flag per (scope, year): "actual" vs "forecast"
    segment_period: dict[str, dict[int, str]] = {}
    long_run_revpar_growth: float | None = None
    long_run_adr_change: float | None = None
    long_run_occupancy: float | None = None

    for r in rows.fetchall():
        raw = r._mapping["fields"]
        if isinstance(raw, str):
            try:
                raw = json.loads(raw) if raw else None
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(raw, list):
            continue
        for f in raw:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not name.startswith("cbre_horizons."):
                continue

            # Long run averages — anchor for variance flags.
            if name == "cbre_horizons.long_run_avg.revpar_change_pct" and isinstance(
                value, (int, float)
            ):
                long_run_revpar_growth = _normalize_pct(float(value))
                continue
            if name == "cbre_horizons.long_run_avg.adr_change_pct" and isinstance(
                value, (int, float)
            ):
                long_run_adr_change = _normalize_pct(float(value))
                continue
            if name == "cbre_horizons.long_run_avg.occupancy_pct" and isinstance(
                value, (int, float)
            ):
                long_run_occupancy = _normalize_pct(float(value))
                continue

            if not isinstance(value, (int, float)):
                continue
            v = float(value)

            # Real segmented path: cbre_horizons.segment_<scope>.<YYYY>.<metric>
            if name.startswith("cbre_horizons.segment_"):
                try:
                    _, scope_part, year_part, metric = name.split(".", 3)
                    scope = scope_part.removeprefix("segment_")
                    year_idx = int(year_part)
                except (ValueError, IndexError):
                    continue
                if metric in ("adr_usd", "adr"):
                    segment_adr.setdefault(scope, {}).setdefault(year_idx, v)
                elif metric in ("revpar_usd", "revpar"):
                    segment_revpar.setdefault(scope, {}).setdefault(year_idx, v)
                elif metric == "period":
                    # Stored numerically as 0/1 by some extractors; only
                    # the segmented forecast filter uses this hint.
                    segment_period.setdefault(scope, {}).setdefault(year_idx, str(v))
                continue

            # Legacy 1-indexed path: cbre_horizons.year_<i>.<metric>
            if name.startswith("cbre_horizons.year_"):
                try:
                    _, year_part, metric = name.split(".", 2)
                    year_idx = int(year_part.split("_", 1)[1])
                except (ValueError, IndexError):
                    continue
                if metric in ("adr_usd", "adr"):
                    legacy_adr.setdefault(year_idx, v)
                elif metric in ("revpar_usd", "revpar"):
                    legacy_revpar.setdefault(year_idx, v)

    out: dict[str, float] = {}

    def _cagr(series: dict[int, float]) -> float | None:
        if len(series) < 2:
            return None
        years_sorted = sorted(series)
        start, end = series[years_sorted[0]], series[years_sorted[-1]]
        n = years_sorted[-1] - years_sorted[0]
        if start <= 0 or end <= 0 or n <= 0:
            return None
        return (end / start) ** (1.0 / n) - 1.0

    # Prefer the segmented (real-report) path. Use ``segment_all`` as
    # the headline market view; restrict to forecast years (strictly >
    # max historical year) so the CAGR reflects the forward window
    # rather than the post-COVID rebound.
    primary_adr = segment_adr.get("all", {})
    primary_revpar = segment_revpar.get("all", {})
    if primary_adr or primary_revpar:
        # When the report carries 5+ years history + 5y forecast, the
        # forecast window starts at the second-highest year ≥ today.
        all_years = sorted(set(primary_adr) | set(primary_revpar))
        if all_years:
            # Heuristic: drop the bottom half of years (treat them as
            # historical). Real CBRE reports carry 2019..2028, and the
            # forecast block begins at the report's vintage year.
            cutoff = all_years[len(all_years) // 2]
            forecast_adr = {y: v for y, v in primary_adr.items() if y >= cutoff}
            forecast_revpar = {y: v for y, v in primary_revpar.items() if y >= cutoff}
        else:
            forecast_adr = primary_adr
            forecast_revpar = primary_revpar
        adr_cagr = _cagr(forecast_adr) or _cagr(primary_adr)
        if adr_cagr is not None and -0.20 <= adr_cagr <= 0.20:
            out["adr_growth"] = adr_cagr
        revpar_cagr = _cagr(forecast_revpar) or _cagr(primary_revpar)
        if revpar_cagr is not None and -0.20 <= revpar_cagr <= 0.20:
            out["revpar_growth"] = revpar_cagr
        # Year-1 baseline for the deal's starting ADR/RevPAR fallback
        # is the first FORECAST year on the segment_all curve.
        if forecast_adr:
            first = sorted(forecast_adr)[0]
            if forecast_adr[first] > 0:
                out["cbre_year_1_adr"] = forecast_adr[first]
        if forecast_revpar:
            first = sorted(forecast_revpar)[0]
            if forecast_revpar[first] > 0:
                out["cbre_year_1_revpar"] = forecast_revpar[first]
    else:
        # Legacy path — only emit if no segmented data was present.
        adr_cagr = _cagr(legacy_adr)
        if adr_cagr is not None and -0.20 <= adr_cagr <= 0.20:
            out["adr_growth"] = adr_cagr
        revpar_cagr = _cagr(legacy_revpar)
        if revpar_cagr is not None and -0.20 <= revpar_cagr <= 0.20:
            out["revpar_growth"] = revpar_cagr
        if 1 in legacy_adr and legacy_adr[1] > 0:
            out["cbre_year_1_adr"] = legacy_adr[1]
        if 1 in legacy_revpar and legacy_revpar[1] > 0:
            out["cbre_year_1_revpar"] = legacy_revpar[1]

    if long_run_revpar_growth is not None and -0.20 <= long_run_revpar_growth <= 0.20:
        out["long_run_avg_revpar_growth"] = long_run_revpar_growth
    if long_run_adr_change is not None and -0.20 <= long_run_adr_change <= 0.20:
        out["long_run_avg_adr_change"] = long_run_adr_change
    if long_run_occupancy is not None and 0 < long_run_occupancy <= 1.0:
        out["long_run_avg_occupancy"] = long_run_occupancy

    return out


def _normalize_pct(v: float) -> float:
    """Normalize a percentage value to the 0..1 decimal form.

    Accepts both forms — 5.8 (as printed) and 0.058 (decimal). Anything
    >1 is treated as a percent and divided by 100. ``-50`` becomes
    ``-0.50``; ``0.058`` stays as-is.
    """
    if abs(v) > 1.0:
        return v / 100.0
    return v


# ─────────────────── P&L Benchmark expense-ratio overrides ──────────────


# Map extracted P&L benchmark fields onto the engine's hotel-type
# default keys. The expense engine accepts an ``overrides`` dict that
# patches HOTEL_TYPE_DEFAULTS line by line at run time, so when the
# benchmark report has landed we route the published peer-set ratios
# straight through without changing the engine signature.
_PNL_BENCHMARK_TO_OVERRIDE: dict[str, str] = {
    "pnl_benchmark.rooms_dept_pct": "rooms_dept_pct",
    "pnl_benchmark.fb_dept_margin": "fb_dept_pct",  # margin → dept-pct conversion handled below
    "pnl_benchmark.gop_margin": "gop_margin",
    "pnl_benchmark.a_and_g_pct": "undistributed_pct_revenue",
    "pnl_benchmark.sales_marketing_pct": "sales_marketing_pct",
    "pnl_benchmark.utilities_pct": "utilities_pct",
    "pnl_benchmark.property_taxes_pct": "property_taxes_pct",
    "pnl_benchmark.insurance_pct": "insurance_pct",
}


async def _load_pnl_benchmark_overrides(
    session: AsyncSession, *, deal_id: str
) -> dict[str, Any]:
    """Translate the deal's extracted P&L benchmark report into engine
    expense overrides.

    Returns a dict shaped as ``{ expense_overrides: {...}, mgmt_fee_pct,
    ffe_reserve_pct }``. The caller folds ``expense_overrides`` into
    the engine ``base["overrides"]`` channel and uses the top-level
    keys to seed mgmt fee / FF&E reserve. Extracted percentages are
    normalized 0..100 → 0..1 before returning.
    """
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        rows = await session.execute(
            text(
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND UPPER(COALESCE(d.doc_type, '')) = 'PNL_BENCHMARK'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id},
        )
    except Exception:
        return {}

    raw_fields: dict[str, float] = {}
    for r in rows.fetchall():
        blob = r._mapping["fields"]
        if isinstance(blob, str):
            try:
                blob = json.loads(blob) if blob else None
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(blob, list):
            continue
        for f in blob:
            if not isinstance(f, dict):
                continue
            name = (f.get("field_name") or "").strip().lower()
            value = f.get("value")
            if not isinstance(value, (int, float)):
                continue
            v = float(value)
            # Normalize percentages emitted as 0..100.
            if v > 1.0 and name.endswith(("_pct", "_margin")):
                v = v / 100.0
            raw_fields.setdefault(name, v)

    if not raw_fields:
        return {}

    expense_overrides: dict[str, float] = {}
    out: dict[str, Any] = {}

    rooms_dept = raw_fields.get("pnl_benchmark.rooms_dept_pct")
    if rooms_dept is not None and 0 < rooms_dept < 1.0:
        expense_overrides["rooms_dept_pct"] = rooms_dept

    fb_margin = raw_fields.get("pnl_benchmark.fb_dept_margin")
    if fb_margin is not None and 0 < fb_margin < 1.0:
        # F&B dept-pct = 1 - margin (margin is profit %, dept-pct is
        # cost %; the engine takes the cost ratio).
        expense_overrides["fb_dept_pct"] = max(0.05, 1.0 - fb_margin)

    if expense_overrides:
        out["expense_overrides"] = expense_overrides

    mgmt = raw_fields.get("pnl_benchmark.mgmt_fee_pct")
    if mgmt is not None and 0 < mgmt < 0.10:
        out["mgmt_fee_pct"] = mgmt
    ffe = raw_fields.get("pnl_benchmark.ffe_reserve_pct")
    if ffe is not None and 0 < ffe < 0.10:
        out["ffe_reserve_pct"] = ffe

    return out


# ─────────────────────────── Per-engine input ─────────────────────────


def _build_input_for(
    engine_name: str,
    deal_id: str,
    base: dict[str, Any],
    accumulated: dict[str, BaseModel],
) -> BaseModel:
    """Materialize the typed Pydantic input for ``engine_name``."""
    deal_uuid = _coerce_uuid(deal_id)

    if engine_name == "revenue":
        return RevenueEngineInput(
            deal_id=deal_uuid,
            keys=base["keys"],
            starting_occupancy=base["starting_occupancy"],
            starting_adr=base["starting_adr"],
            occupancy_growth=base["occupancy_growth"],
            adr_growth=base["adr_growth"],
            fb_revenue_per_occupied_room=base["fb_revenue_per_occupied_room"],
            other_revenue_pct_of_rooms=base["other_revenue_pct_of_rooms"],
            # Sam QA #11: when the deal has an extracted T-12 with a
            # distinct Resort Fees line, the loader stashes the Y1
            # actual on ``base["starting_resort_fees"]``. Default 0
            # leaves the engine emitting the Resort Fees column at
            # zero so the UI hides the row on legacy deals.
            starting_resort_fees=base.get("starting_resort_fees", 0.0),
            resort_fees_growth=base.get("resort_fees_growth", base.get("revpar_growth", 0.03)),
            hold_years=base["hold_years"],
        )

    if engine_name == "fb":
        return FBRevenueInput(
            deal_id=deal_uuid,
            revenue=accumulated["revenue"],
            hotel_type=base.get("hotel_type", "full"),
            fb_ratio=base.get("fb_ratio"),
            other_ratio=base.get("other_ratio"),
        )

    if engine_name == "expense":
        return ExpenseEngineInput(
            deal_id=deal_uuid,
            revenue=accumulated["fb"],
            hotel_type=base.get("hotel_type", "full"),
            mgmt_fee_pct=base["mgmt_fee_pct"],
            ffe_reserve_pct=base["ffe_reserve_pct"],
            expense_growth=base["expense_growth"],
            grow_opex_independently=base["grow_opex_independently"],
            # Benchmark overrides populated by `_load_engine_inputs`
            # from the CBRE / brand catalog. Previously dropped before
            # construction so brand-specific ratios never made it into
            # the projection.
            overrides=base.get("overrides") or {},
            # When the deal has an extracted T-12, the engine prefers
            # actuals over USALI benchmark ratios for Year 1. Loaded by
            # ``_load_engine_inputs`` below; absent on demo deals.
            t12_actuals=base.get("t12_expense_actuals", {}) or {},
        )

    if engine_name == "capital":
        return CapitalEngineInput(
            deal_id=deal_uuid,
            purchase_price=base["purchase_price"],
            keys=base["keys"],
            renovation_budget=base.get("renovation_budget", 0.0),
            soft_costs=base.get("soft_costs", 0.0),
            contingency=base.get("contingency", 0.0),
            working_capital=base.get("working_capital", 0.0),
            closing_costs_pct=base.get("closing_costs_pct", 0.02),
            loan_costs_pct=base.get("loan_costs_pct", 0.015),
            ltv=base["ltv"],
            debt_basis="purchase",
        )

    if engine_name == "debt":
        capital_out = accumulated["capital"]
        expense_out = accumulated["expense"]
        noi_by_year = [yr.noi for yr in expense_out.years]
        return DebtEngineInputExt(
            deal_id=deal_uuid,
            loan_amount=capital_out.debt_amount,
            ltv=base["ltv"],
            interest_rate=base["interest_rate"],
            term_years=base["term_years"],
            amortization_years=base["amortization_years"],
            interest_only_years=base.get("interest_only_years", 0),
            noi_by_year=noi_by_year,
        )

    if engine_name == "returns":
        capital_out = accumulated["capital"]
        debt_out = accumulated["debt"]
        expense_out = accumulated["expense"]
        noi_by_year = [yr.noi for yr in expense_out.years]
        assumptions = ModelAssumptions(
            purchase_price=base["purchase_price"],
            ltv=base["ltv"],
            interest_rate=base["interest_rate"],
            amortization_years=base["amortization_years"],
            loan_term_years=base["term_years"],
            hold_years=base["hold_years"],
            exit_cap_rate=base["exit_cap_rate"],
            revpar_growth=base["revpar_growth"],
            expense_growth=base["expense_growth"],
            selling_costs_pct=base["selling_costs_pct"],
            closing_costs_pct=base["closing_costs_pct"],
        )
        return ReturnsEngineInputExt(
            deal_id=deal_uuid,
            assumptions=assumptions,
            year_one_noi=noi_by_year[0],
            noi_by_year=noi_by_year,
            annual_debt_service=debt_out.annual_debt_service,
            loan_amount=capital_out.debt_amount,
            loan_balance_at_exit=(
                debt_out.schedule[-1].ending_balance
                if debt_out.schedule
                else capital_out.debt_amount
            ),
            equity=capital_out.equity_amount,
        )

    if engine_name == "sensitivity":
        # Reuse the returns engine input as the base; flex exit cap × revpar.
        returns_input = _build_input_for(
            "returns", deal_id, base, accumulated
        )
        # mypy: returns_input is ReturnsEngineInputExt by construction
        assert isinstance(returns_input, ReturnsEngineInputExt)
        ec = base["exit_cap_rate"]
        rp = base["revpar_growth"]
        row_values = [round(ec - 0.01, 4), round(ec - 0.005, 4), ec,
                      round(ec + 0.005, 4), round(ec + 0.01, 4)]
        col_values = [round(rp - 0.02, 4), round(rp - 0.01, 4), rp,
                      round(rp + 0.01, 4), round(rp + 0.02, 4)]
        # Clamp to engine bounds (exit_cap > 0).
        row_values = [max(0.001, v) for v in row_values]
        col_values = [max(-0.49, min(0.49, v)) for v in col_values]
        return SensitivityInput(
            deal_id=deal_uuid,
            base_returns_input=returns_input,
            row_variable="exit_cap_rate",
            row_values=row_values,
            col_variable="revpar_growth",
            col_values=col_values,
            metric="levered_irr",
        )

    if engine_name == "partnership":
        capital_out = accumulated["capital"]
        returns_out = accumulated["returns"]
        # The returns engine emits the levered cash-flow series; strip
        # the Year 0 (-equity) entry so we feed annual project cash.
        flows = returns_out.cash_flows[1:] if returns_out.cash_flows else []
        if not flows:
            # Defensive fallback: synthesize a flat annual cash flow.
            flows = [returns_out.equity_multiple * capital_out.equity_amount / max(1, base["hold_years"])]
        waterfall = [
            WaterfallTier(label="Pref", hurdle_rate=0.08, gp_split=0.10, lp_split=0.90),
            WaterfallTier(label="Tier 1", hurdle_rate=0.12, gp_split=0.20, lp_split=0.80),
            WaterfallTier(label="Tier 2", hurdle_rate=0.18, gp_split=0.30, lp_split=0.70),
        ]
        return PartnershipInputExt(
            deal_id=deal_uuid,
            total_equity=capital_out.equity_amount,
            gp_equity_pct=base["gp_equity_pct"],
            lp_equity_pct=base["lp_equity_pct"],
            pref_rate=base["pref_rate"],
            waterfall=waterfall,
            cash_flows=flows,
            catch_up=False,
        )

    raise ValueError(f"unknown engine: {engine_name!r}")


def _coerce_uuid(value: str) -> UUID:
    """Best-effort UUID coercion — fall back to a deterministic UUID5
    for legacy int-string ids (e.g. the Kimpton demo deal '7')."""
    try:
        return UUID(value)
    except (ValueError, TypeError):
        # Stable derivation so repeat calls produce the same UUID.
        from uuid import NAMESPACE_URL, uuid5

        return uuid5(NAMESPACE_URL, f"fondok://deal/{value}")


# ──────────────────────────── Persistence ─────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _json_dumps(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, default=str)


async def _persist_status(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    engine_name: str,
    run_id: str,
    inputs: BaseModel | dict[str, Any] | None,
    started_at: datetime,
) -> str:
    """Insert a ``running`` row; return the row id."""
    row_id = str(uuid4())
    await session.execute(
        text(
            """
            INSERT INTO engine_outputs (
                id, deal_id, tenant_id, run_id, engine_name,
                status, inputs, outputs, error,
                started_at, completed_at, runtime_ms
            ) VALUES (
                :id, :deal, :tenant, :run, :engine,
                'running', :inputs, NULL, NULL,
                :started_at, NULL, NULL
            )
            """
        ),
        {
            "id": row_id,
            "deal": deal_id,
            "tenant": tenant_id,
            "run": run_id,
            "engine": engine_name,
            "inputs": _json_dumps(inputs),
            "started_at": started_at,
        },
    )
    await session.commit()
    return row_id


async def _persist_complete(
    session: AsyncSession,
    *,
    row_id: str,
    output: BaseModel,
    inputs: BaseModel | dict[str, Any] | None,
    runtime_ms: int,
) -> None:
    await session.execute(
        text(
            """
            UPDATE engine_outputs
               SET status = 'complete',
                   inputs = :inputs,
                   outputs = :outputs,
                   completed_at = :ts,
                   runtime_ms = :runtime_ms
             WHERE id = :id
            """
        ),
        {
            "id": row_id,
            "inputs": _json_dumps(inputs),
            "outputs": _json_dumps(output),
            "ts": _now(),
            "runtime_ms": runtime_ms,
        },
    )
    await session.commit()


async def _persist_failed(
    session: AsyncSession,
    *,
    row_id: str,
    error: str,
) -> None:
    await session.execute(
        text(
            """
            UPDATE engine_outputs
               SET status = 'failed',
                   error = :error,
                   completed_at = :ts
             WHERE id = :id
            """
        ),
        {"id": row_id, "error": error, "ts": _now()},
    )
    await session.commit()


# ────────────────────────────── Runners ───────────────────────────────


def _summary_for(engine_name: str, output: BaseModel) -> str:
    """Compact one-line headline shown next to the Run button."""
    try:
        if engine_name == "returns":
            return (
                f"IRR {output.levered_irr * 100:.1f}% "
                f"· Multiple {output.equity_multiple:.2f}x"
            )
        if engine_name == "expense":
            y1 = output.years[0].noi if output.years else 0.0
            return f"Y1 NOI ${y1 / 1e6:.2f}M"
        if engine_name == "revenue":
            cagr = getattr(output, "total_revenue_cagr", 0.0)
            return f"Revenue CAGR {cagr * 100:.1f}%"
        if engine_name == "fb":
            ratio = getattr(output, "fb_ratio_used", 0.0)
            return f"F&B {ratio * 100:.0f}% of rooms"
        if engine_name == "capital":
            return (
                f"Equity ${output.equity_amount / 1e6:.2f}M "
                f"· LTC {output.ltc * 100:.1f}%"
            )
        if engine_name == "debt":
            dscr = getattr(output, "year_one_dscr", None) or 0.0
            return f"DSCR {dscr:.2f}x"
        if engine_name == "sensitivity":
            return f"{len(output.cells)} cells"
        if engine_name == "partnership":
            return (
                f"LP IRR {output.lp.irr * 100:.1f}% "
                f"· GP IRR {output.gp.irr * 100:.1f}%"
            )
    except Exception:
        return ""
    return ""


async def run_single_engine(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    engine_name: str,
    run_id: str | None = None,
    overrides: dict[str, Any] | None = None,
    accumulated: dict[str, BaseModel] | None = None,
    base_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a single engine and persist the output.

    Returns a serializable dict suitable for the API response.
    """
    if engine_name not in ENGINE_REGISTRY:
        raise ValueError(
            f"unknown engine {engine_name!r}; "
            f"expected one of {sorted(ENGINE_REGISTRY)}"
        )

    run_id = run_id or str(uuid4())
    accumulated = accumulated if accumulated is not None else {}
    base_inputs = base_inputs or await _load_engine_inputs(
        session, deal_id, overrides
    )

    # Some engines need upstream outputs. When called in single-engine
    # mode we transparently run prerequisites first so the user can hit
    # any one Run button without manually orchestrating the chain.
    for dep in ENGINE_DEPS[engine_name]:
        if dep not in accumulated:
            await run_single_engine(
                session,
                deal_id=deal_id,
                tenant_id=tenant_id,
                engine_name=dep,
                run_id=run_id,
                overrides=overrides,
                accumulated=accumulated,
                base_inputs=base_inputs,
            )

    started_at = _now()
    # Persist the running row first so a build-time failure (e.g. a
    # Pydantic ValidationError on a bad override) still surfaces to
    # the UI as a normal failed row instead of bubbling up to FastAPI
    # as a 500.
    row_id = await _persist_status(
        session,
        deal_id=str(_coerce_uuid(deal_id)),
        tenant_id=tenant_id,
        engine_name=engine_name,
        run_id=run_id,
        inputs=None,
        started_at=started_at,
    )

    t0 = time.monotonic()
    try:
        engine_input = _build_input_for(
            engine_name, deal_id, base_inputs, accumulated
        )
        engine = ENGINE_REGISTRY[engine_name]()
        output = engine.run(engine_input)
    except Exception as exc:
        runtime_ms = int((time.monotonic() - t0) * 1000)
        logger.exception(
            "engine %s failed deal=%s runtime=%dms", engine_name, deal_id, runtime_ms
        )
        await _persist_failed(session, row_id=row_id, error=str(exc))
        return {
            "engine": engine_name,
            "status": "failed",
            "error": str(exc),
            "runtime_ms": runtime_ms,
        }

    runtime_ms = int((time.monotonic() - t0) * 1000)
    accumulated[engine_name] = output
    await _persist_complete(
        session,
        row_id=row_id,
        output=output,
        inputs=engine_input,
        runtime_ms=runtime_ms,
    )
    return {
        "engine": engine_name,
        "status": "complete",
        "outputs": json.loads(output.model_dump_json()),
        "summary": _summary_for(engine_name, output),
        "runtime_ms": runtime_ms,
    }


async def run_all_engines(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    run_id: str,
    overrides: dict[str, Any] | None = None,
    on_complete: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the full 8-engine chain; persist each output as it lands.

    Returns a dict keyed by engine name with the per-engine result
    dicts (matching ``run_single_engine``'s return shape).

    Failure policy: when an engine fails we mark its row failed AND
    skip downstream engines that need its output (their rows land with
    ``status='failed'`` and a ``skipped: <upstream>`` error). Engines
    independent of the failure keep running so the user sees partial
    progress instead of a blank page.
    """
    base_inputs = await _load_engine_inputs(session, deal_id, overrides)
    accumulated: dict[str, BaseModel] = {}
    results: dict[str, dict[str, Any]] = {}

    for name in ENGINE_NAMES:
        deps = ENGINE_DEPS[name]
        missing = [d for d in deps if d not in accumulated]
        if missing:
            # Upstream failed — record a skipped row and move on.
            started_at = _now()
            row_id = await _persist_status(
                session,
                deal_id=str(_coerce_uuid(deal_id)),
                tenant_id=tenant_id,
                engine_name=name,
                run_id=run_id,
                inputs=None,
                started_at=started_at,
            )
            err = f"skipped: upstream {', '.join(missing)} did not complete"
            await _persist_failed(session, row_id=row_id, error=err)
            results[name] = {
                "engine": name,
                "status": "failed",
                "error": err,
                "runtime_ms": 0,
            }
            if on_complete:
                on_complete(name, results[name])
            continue

        result = await run_single_engine(
            session,
            deal_id=deal_id,
            tenant_id=tenant_id,
            engine_name=name,
            run_id=run_id,
            overrides=overrides,
            accumulated=accumulated,
            base_inputs=base_inputs,
        )
        results[name] = result
        if on_complete:
            on_complete(name, result)

    return results


# ─────────────────────────── Reading back ─────────────────────────────


async def get_latest_output(
    session: AsyncSession,
    *,
    deal_id: str,
    engine_name: str,
) -> dict[str, Any] | None:
    """Return the latest persisted row for ``(deal_id, engine_name)``."""
    row = (
        await session.execute(
            text(
                """
                SELECT id, deal_id, tenant_id, run_id, engine_name,
                       status, inputs, outputs, error,
                       started_at, completed_at, runtime_ms
                  FROM engine_outputs
                 WHERE deal_id = :deal AND engine_name = :engine
                 ORDER BY started_at DESC
                 LIMIT 1
                """
            ),
            {"deal": str(_coerce_uuid(deal_id)), "engine": engine_name},
        )
    ).first()
    if row is None:
        return None
    return _row_to_dict(row._mapping)


async def get_latest_outputs(
    session: AsyncSession,
    *,
    deal_id: str,
) -> dict[str, dict[str, Any]]:
    """Return the latest row per engine for ``deal_id``.

    Result is keyed by engine name; engines with no rows are omitted.
    """
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, run_id, engine_name,
                   status, inputs, outputs, error,
                   started_at, completed_at, runtime_ms
              FROM engine_outputs
             WHERE deal_id = :deal
             ORDER BY started_at DESC
            """
        ),
        {"deal": str(_coerce_uuid(deal_id))},
    )
    seen: dict[str, dict[str, Any]] = {}
    for r in rows.fetchall():
        mapping = r._mapping
        name = mapping["engine_name"]
        if name in seen:
            continue
        seen[name] = _row_to_dict(mapping)
    return seen


async def get_run_status(
    session: AsyncSession,
    *,
    deal_id: str,
    run_id: str,
) -> list[dict[str, Any]]:
    """Return every engine row tagged with ``run_id`` for ``deal_id``."""
    rows = await session.execute(
        text(
            """
            SELECT id, deal_id, tenant_id, run_id, engine_name,
                   status, inputs, outputs, error,
                   started_at, completed_at, runtime_ms
              FROM engine_outputs
             WHERE deal_id = :deal AND run_id = :run
             ORDER BY started_at ASC
            """
        ),
        {"deal": str(_coerce_uuid(deal_id)), "run": run_id},
    )
    return [_row_to_dict(r._mapping) for r in rows.fetchall()]


def _row_to_dict(mapping: Any) -> dict[str, Any]:
    """Coerce a SQL row into the engine-output JSON envelope.

    JSONB columns come back as dicts on Postgres; on SQLite they're
    serialized strings (we did the encoding ourselves) so we decode
    here to keep the API response shape consistent across backends.
    """
    def _decode(value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return value
        return value

    name = mapping["engine_name"]
    outputs = _decode(mapping["outputs"])
    summary = ""
    if outputs and mapping["status"] == "complete":
        summary = _summary_from_dict(name, outputs)

    return {
        "id": str(mapping["id"]),
        "deal_id": str(mapping["deal_id"]),
        "tenant_id": str(mapping["tenant_id"]),
        "run_id": str(mapping["run_id"]) if mapping["run_id"] else None,
        "engine": name,
        "status": mapping["status"],
        "inputs": _decode(mapping["inputs"]),
        "outputs": outputs,
        "summary": summary,
        "error": mapping["error"],
        "started_at": _coerce_iso(mapping["started_at"]),
        "completed_at": _coerce_iso(mapping["completed_at"]),
        "runtime_ms": mapping["runtime_ms"],
    }


def _summary_from_dict(engine_name: str, output: dict[str, Any]) -> str:
    """Compute the headline summary directly from a JSON dict.

    Mirrors ``_summary_for`` but operates on the deserialized output so
    the GET endpoints don't have to re-instantiate the Pydantic model.
    """
    try:
        if engine_name == "returns":
            return (
                f"IRR {output.get('levered_irr', 0) * 100:.1f}% "
                f"· Multiple {output.get('equity_multiple', 0):.2f}x"
            )
        if engine_name == "expense":
            years = output.get("years") or []
            y1 = years[0]["noi"] if years else 0.0
            return f"Y1 NOI ${y1 / 1e6:.2f}M"
        if engine_name == "revenue":
            cagr = output.get("total_revenue_cagr", 0.0)
            return f"Revenue CAGR {cagr * 100:.1f}%"
        if engine_name == "fb":
            return f"F&B {output.get('fb_ratio_used', 0) * 100:.0f}% of rooms"
        if engine_name == "capital":
            return (
                f"Equity ${output.get('equity_amount', 0) / 1e6:.2f}M "
                f"· LTC {output.get('ltc', 0) * 100:.1f}%"
            )
        if engine_name == "debt":
            dscr = output.get("year_one_dscr") or 0.0
            return f"DSCR {dscr:.2f}x"
        if engine_name == "sensitivity":
            return f"{len(output.get('cells') or [])} cells"
        if engine_name == "partnership":
            lp = output.get("lp", {})
            gp = output.get("gp", {})
            return (
                f"LP IRR {lp.get('irr', 0) * 100:.1f}% "
                f"· GP IRR {gp.get('irr', 0) * 100:.1f}%"
            )
    except Exception:
        return ""
    return ""


def _coerce_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)


__all__ = [
    "ENGINE_DEPS",
    "ENGINE_NAMES",
    "ENGINE_REGISTRY",
    "get_latest_output",
    "get_latest_outputs",
    "get_run_status",
    "run_all_engines",
    "run_single_engine",
]
