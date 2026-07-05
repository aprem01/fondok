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

# Field alias maps + period-type ranks are externalized to
# apps/worker/app/extraction/field_catalog.yaml (Phase 2 of the
# dynamic-extensibility refactor). Adding a new extracted-field
# alias is a YAML edit; no Python change required.
from ..extraction.field_catalog import (
    OM_CAPITAL_FIELD_ALIASES as _CATALOG_OM_CAPITAL_FIELD_ALIASES,
    OM_DEBT_FIELD_ALIASES as _CATALOG_OM_DEBT_FIELD_ALIASES,
    OM_PERCENTAGE_KEYS as _CATALOG_OM_PERCENTAGE_KEYS,
    PERIOD_TYPE_RANK as _CATALOG_PERIOD_TYPE_RANK,
    T12_EXPENSE_FIELD_ALIASES as _CATALOG_T12_EXPENSE_FIELD_ALIASES,
    T12_REVENUE_FIELD_ALIASES as _CATALOG_T12_REVENUE_FIELD_ALIASES,
)

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
from fondok_schemas.underwriting import (
    PIPDisplacement,
    RevenueEngineInput,
    RevenueSegment,
)

logger = logging.getLogger(__name__)


# Wave 2 P2.1 — institutional channel-cost defaults per demand segment.
# These become the engine defaults when an STR_SEGMENTATION extraction
# exists for the deal; analyst overrides via ``field_overrides`` win.
#
#   transient_bar   2%  — direct + brand.com — credit-card fees only
#   transient_ota  20%  — OTA / opaque — the big one, IC always asks
#   corporate       8%  — LRA / RFP rates — commissions + TMC fees
#   group           5%  — conferences / weddings — commissions + attrition
#   contract        2%  — crew / sports / airline — low-rate baseline
#
# Sources: STR Market Segmentation Practice Guide (2024); CBRE Hotels
# Insights "OTA Commission Drag on Independent Hotels" (Q4 2024).
_INSTITUTIONAL_CHANNEL_COST_DEFAULTS: dict[str, float] = {
    "transient_bar": 0.02,
    "transient_ota": 0.20,
    "corporate": 0.08,
    "group": 0.05,
    "contract": 0.02,
}

# Default per-segment ADR ratios applied to the property-overall ADR
# when the STR Segmentation report doesn't break ADR down by segment.
# Corporate trades at ~10% below BAR (LRA discount), group ~8% below
# (volume + attrition allowances), contract ~35% below (low-rate steady
# demand). These come from STR Hotel News Now's 2024 segment ADR survey
# and match what Eshan signed off on for the Kimpton fixture.
_DEFAULT_SEGMENT_ADR_RATIO: dict[str, float] = {
    "transient_bar": 1.00,
    "transient_ota": 1.00,
    "corporate": 0.90,
    "group": 0.92,
    "contract": 0.65,
}


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


# Source labels for the assumption-provenance map. The web app uses
# them verbatim as badge text on the Returns / Investment / Overview
# views so analysts can see which numbers are seed defaults vs which
# are grounded in extracted/uploaded data.
SOURCE_SEED = "seed"
SOURCE_DEAL_ROW = "deal_row"
SOURCE_T12_ACTUAL = "t12_actual"
SOURCE_CBRE_HORIZONS = "cbre_horizons"
SOURCE_PNL_BENCHMARK = "pnl_benchmark"
# Wave 2 P2.7 — analyst's in-house portfolio benchmark. Outranks
# ``pnl_benchmark`` (generic HostStats default) and ``cbre_horizons``
# (market-wide CBRE Horizons benchmark) for op-ratios because a firm's
# own portfolio P&L is the most credible peer set for hotels they
# already operate. Outranked only by ``t12_actual`` (subject's own
# historical P&L) and ``analyst_override`` (explicit analyst intent).
# See ``apps/worker/app/services/op_ratio_precedence.py``.
SOURCE_PORTFOLIO_PNL = "portfolio_pnl"
SOURCE_OM_COMPS = "om_comps"
SOURCE_OM_BROKER = "om_broker"
SOURCE_ANALYST_OVERRIDE = "analyst_override"
# Wave 2 P2.1 — institutional revenue segmentation. When the deal has
# an STR_SEGMENTATION extraction the loader seeds a default
# ``segments: list[RevenueSegment]`` on the revenue engine input and
# tags each segment field with this provenance label so the UI badge
# shows "STR Segmentation" instead of "seed".
SOURCE_STR_SEGMENTATION_DEFAULT = "str_segmentation_default"
# Wave 2 P2.4 — structured PIP displacement (``pip_displacement.*``).
# Wave 2 P2.5 — capex three-bucket (``capex_plan.*``). ``SOURCE_PIP_USER``
# covers analyst override on both the PIP-displacement and PIP-cost legs;
# the rest tag the FF&E reserve and discretionary-ROI legs.
SOURCE_PIP_OM = "pip_om"
SOURCE_PIP_USER = "pip_user"
SOURCE_CAPEX_FFE_DEFAULT = "capex_ffe_default"
SOURCE_ROI_USER = "roi_user"
# Wave 3 W3.3 — STR forward-forecast seed. When the analyst opts in via
# ``revenue_seed_from_str_forecast=True`` the revenue engine's
# ``starting_occupancy`` + ``starting_adr`` are seeded from the BASE
# scenario's Month-12 STRMonth so the rooms-revenue projection inherits
# the forecast's bottom-up math (rather than the T-12 / Kimpton seed).
# Default is OFF — no regression to existing deals.
SOURCE_STR_FORECAST = "str_forecast"


async def _load_engine_inputs(
    session: AsyncSession,
    deal_id: str,
    overrides: dict[str, Any] | None = None,
    scenario_id: str | None = None,
    *,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Resolve the underwriting assumptions for ``deal_id``.

    Strategy:
        1. Load the deal row (purchase_price, keys) when present.
        2. Layer in caller overrides from the API request body.
        3. Fall back to the Kimpton fixture for everything missing.

    Also populates ``base["__sources__"]`` — a parallel dict mapping
    each canonical assumption key to its provenance label (one of the
    ``SOURCE_*`` constants above). Callers that build engine inputs
    pick specific keys with .get() and ignore the metadata; the
    ``GET /deals/{id}/assumption_sources`` endpoint reads it.

    The web app's demo deal id (legacy int 7) does not parse as a UUID
    and never lands in the deals table; that path uses the pure Kimpton
    defaults.

    Wave 3 W3.2: when ``scenario_id`` is set, the scenario's overrides
    merge on top of the deal's persisted ``field_overrides`` and feed
    the same override-routing loop the deal overrides use. Without a
    ``scenario_id`` the path is unchanged — running with the deal's
    base scenario id is byte-identical to running with no scenario_id
    at all (the base scenario carries an empty override list).
    """
    base = _kimpton_assumptions()
    # Every key starts marked as a seed; later steps overwrite the
    # source label as data sources land. Only the subset of keys the
    # web app surfaces gets badged — extras are tracked anyway so
    # downstream callers can introspect freely.
    sources: dict[str, str] = {k: SOURCE_SEED for k in base.keys()}
    # Resolve tenant_id up-front so every helper below can scope its
    # SELECT — production callers always pass one; test / demo callers
    # (Kimpton fixture) fall back to the seed tenant.
    if tenant_id is None:
        from ..config import get_settings as _get_settings

        effective_tenant = str(_get_settings().DEFAULT_TENANT_ID)
    else:
        effective_tenant = str(tenant_id)
    try:
        # Only try DB lookup when the id is a valid UUID. The Kimpton
        # demo card uses an int-string id which is intentionally
        # outside the deals table.
        UUID(deal_id)
    except (ValueError, TypeError):
        if overrides:
            base.update(overrides)
            for k in overrides:
                sources[k] = SOURCE_ANALYST_OVERRIDE
        base["__sources__"] = sources
        return base

    try:
        row = (
            await session.execute(
                text(
                    # tenant-scope predicate required by tenant_middleware
                    """
                    SELECT keys, purchase_price, positioning, brand
                      FROM deals
                     WHERE id = :id AND tenant_id = :tenant
                    """
                ),
                {"id": deal_id, "tenant": effective_tenant},
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
    # Subject chain scale — Wave 4 W4.1 uses this to filter portfolio
    # library entries to the ones whose ``chain_scales_covered`` overlap.
    # Best-effort: positioning carries the chain-scale tier today; when
    # unset the library loader degrades to "no chain-scale filter" so an
    # entry with empty ``chain_scales_covered`` (tenant didn't tag) can
    # still contribute.
    subject_chain_scale: str | None = None
    if row is not None:
        mapping = row._mapping
        if mapping.get("keys"):
            base["keys"] = int(mapping["keys"])
            deals_table_keys.add("keys")
            sources["keys"] = SOURCE_DEAL_ROW
        if mapping.get("purchase_price"):
            try:
                base["purchase_price"] = float(mapping["purchase_price"])
                deals_table_keys.add("purchase_price")
                sources["purchase_price"] = SOURCE_DEAL_ROW
            except (TypeError, ValueError):
                pass
        pos = mapping.get("positioning")
        if isinstance(pos, str) and pos.strip():
            subject_chain_scale = pos.strip()

    # Pull Year-1 T-12 expense actuals from the deal's extraction results
    # so the expense engine can ground synthesis on real numbers (Sam QA
    # #1: synthesized expenses ($457K Insurance vs actual $1.16M;
    # $905K Utilities vs actual $288K) cascaded into wrong DSCR / returns
    # / per-key metrics). Best-effort — partial extraction degrades to
    # ratio synthesis line-by-line.
    base["t12_expense_actuals"] = await _load_t12_expense_actuals(
        session, deal_id=deal_id, tenant_id=effective_tenant
    )

    # Same idea on the revenue side (Sam QA #16): when a T-12 has been
    # extracted, prefer the actual occupancy / ADR over the Kimpton seed
    # so the Per-Key tab and downstream rooms-revenue projection reflect
    # the real property instead of the demo defaults.
    revenue_actuals = await _load_t12_revenue_actuals(
        session, deal_id=deal_id, tenant_id=effective_tenant
    )
    if "occupancy" in revenue_actuals:
        base["starting_occupancy"] = revenue_actuals["occupancy"]
        sources["starting_occupancy"] = SOURCE_T12_ACTUAL
    if "adr" in revenue_actuals:
        base["starting_adr"] = revenue_actuals["adr"]
        sources["starting_adr"] = SOURCE_T12_ACTUAL
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
        sources["starting_adr"] = SOURCE_T12_ACTUAL
    elif (
        "revpar" in revenue_actuals
        and "occupancy" not in revenue_actuals
        and "adr" in revenue_actuals
        and revenue_actuals["adr"] > 0
    ):
        base["starting_occupancy"] = min(
            0.95, revenue_actuals["revpar"] / revenue_actuals["adr"]
        )
        sources["starting_occupancy"] = SOURCE_T12_ACTUAL

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
        session, deal_id=deal_id, tenant_id=effective_tenant
    )
    for key, value in capital_actuals.items():
        if key in deals_table_keys:
            continue
        base[key] = value

    debt_actuals = await _load_om_debt_actuals(
        session, deal_id=deal_id, tenant_id=effective_tenant
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
        session, deal_id=deal_id, tenant_id=effective_tenant
    )
    for key, value in cbre_overrides.items():
        base[key] = value
        sources[key] = SOURCE_CBRE_HORIZONS
    # Wire CBRE Year-1 ADR/RevPAR as the Year-1 anchor when the deal
    # has no T-12 actual on those metrics. Previously cbre_year_1_adr
    # was written to base but never read (Eshan's QA #5: the engine
    # ignored CBRE forecasts even with a CBRE Horizons doc uploaded).
    # T-12 actuals still win — they're analyst-trusted observed values
    # — but in their absence the CBRE forecast is more grounded than
    # the Kimpton seed.
    if "adr" not in revenue_actuals and "cbre_year_1_adr" in cbre_overrides:
        base["starting_adr"] = cbre_overrides["cbre_year_1_adr"]
        sources["starting_adr"] = SOURCE_CBRE_HORIZONS
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
            sources["starting_adr"] = SOURCE_CBRE_HORIZONS

    benchmark_overrides = await _load_pnl_benchmark_overrides(
        session, deal_id=deal_id, tenant_id=effective_tenant
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
                sources[top_level_key] = SOURCE_PNL_BENCHMARK

    # Wave 4 W4.1 — firm-level Portfolio P&L Library. When the tenant
    # has uploaded active library entries that cover the subject deal's
    # chain scale (within the 3-year vintage look-back), the engine
    # uses the per-ratio median as the portfolio_pnl candidate. This
    # OUTRANKS pnl_benchmark + cbre_horizons (per
    # ``op_ratio_precedence``) so we layer it AFTER those steps.
    # Per-deal PORTFOLIO_PNL docs take precedence over the library
    # median for the same chain scale (analyst intent on this specific
    # deal beats firm-wide benchmarks).
    if tenant_id is not None:
        library_overrides = await _load_portfolio_library_overrides(
            session,
            tenant_id=tenant_id,
            subject_chain_scale=subject_chain_scale,
        )
    else:
        library_overrides = {}
    per_deal_portfolio = await _load_per_deal_portfolio_pnl_overrides(
        session, deal_id=deal_id, tenant_id=effective_tenant
    )
    # Per-deal docs overlay the library median (same chain scale wins
    # per-deal). Both feed into ``SOURCE_PORTFOLIO_PNL`` provenance.
    portfolio_overrides: dict[str, float] = {}
    portfolio_overrides.update(library_overrides)
    portfolio_overrides.update(per_deal_portfolio)
    if portfolio_overrides:
        existing = dict(base.get("overrides") or {})
        existing.update(portfolio_overrides)
        base["overrides"] = existing
        for key, value in portfolio_overrides.items():
            # Promote the top-level engine keys (mgmt_fee_pct, ffe_reserve_pct)
            # AND tag the per-ratio overrides so the UI badges render
            # "Portfolio" instead of "Seed" / "PnL Bench".
            if key in ("mgmt_fee_pct", "ffe_reserve_pct"):
                base[key] = value
            sources[key] = SOURCE_PORTFOLIO_PNL

    # OM-derived exit-cap anchor — the broker's "Comparable Sales"
    # table gives us market-specific cap rates we should prefer over
    # the 7.0% seed. Analyst overrides via field_overrides still win
    # because they're applied last.
    om_median_cap = await _load_om_transaction_comps_cap_rate(
        session, deal_id=deal_id, tenant_id=effective_tenant
    )
    if om_median_cap is not None:
        base["exit_cap_rate"] = om_median_cap
        sources["exit_cap_rate"] = SOURCE_OM_COMPS

    # Year-1 renovation displacement (Eshan v2 QA). When the deal has
    # a non-trivial PIP, Y1 occupancy + ADR get knocked down to
    # reflect rooms-out-of-service + disruption pricing. The 15% occ
    # and 8% ADR defaults are mid-range institutional assumptions for
    # a full-service hotel under a $5M+ PIP; analysts can override
    # via field_overrides. Y2+ snap back to the stabilized baseline
    # so a heavy PIP doesn't permanently depress the projection curve.
    renovation_budget = base.get("renovation_budget", 0.0) or 0.0
    keys_for_displacement = base.get("keys", 0) or 0
    pip_per_key = (
        renovation_budget / keys_for_displacement
        if keys_for_displacement > 0
        else 0.0
    )
    # Only displace when the PIP is material (>$5k per key — small
    # capex like new mattresses doesn't take rooms offline). Pure
    # cosmetic refreshes pass without displacement.
    if renovation_budget > 0 and pip_per_key > 5_000:
        base.setdefault("y1_occupancy_displacement_pct", 0.15)
        base.setdefault("y1_adr_displacement_pct", 0.08)
        sources["y1_occupancy_displacement_pct"] = SOURCE_SEED
        sources["y1_adr_displacement_pct"] = SOURCE_SEED
    else:
        base.setdefault("y1_occupancy_displacement_pct", 0.0)
        base.setdefault("y1_adr_displacement_pct", 0.0)
        sources["y1_occupancy_displacement_pct"] = SOURCE_SEED
        sources["y1_adr_displacement_pct"] = SOURCE_SEED

    # Wave 2 P2.1 — when the deal has an STR_SEGMENTATION extraction,
    # build a default ``segments`` list on the revenue engine input. The
    # analyst's segment-field overrides (loaded below) merge on top so
    # an analyst tweak survives a re-run.
    str_seg_payload = await _load_str_segmentation_payload(
        session, deal_id=deal_id, tenant_id=effective_tenant
    )
    if str_seg_payload:
        # The analyst's segment-field overrides — read once here so
        # the seed builder can apply them in-place. The override-routing
        # loop below ALSO updates ``base["segments_overrides"]`` but the
        # seed needs them up-front to compute correct provenance.
        pre_load_seg_overrides_raw = await _load_deal_overrides(
            session, deal_id=deal_id, tenant_id=effective_tenant
        )
        if scenario_id:
            # Wave 3 W3.2 — scenario overrides win over deal-level
            # overrides on conflicts (same precedence model as the
            # below ``persisted_overrides`` merge).
            scenario_overrides_pre = await _load_scenario_overrides(
                session, scenario_id=scenario_id
            )
            pre_load_seg_overrides_raw = {
                **pre_load_seg_overrides_raw,
                **scenario_overrides_pre,
            }
        pre_load_seg_overrides: dict[str, dict[str, float]] = {}
        for path, value in pre_load_seg_overrides_raw.items():
            if not isinstance(value, (int, float)):
                continue
            parsed = _parse_segment_override_path(path)
            if parsed:
                seg_name, field = parsed
                pre_load_seg_overrides.setdefault(seg_name, {})[field] = float(value)
        # Property-overall ADR (post any T-12 / CBRE / OM override that
        # already landed on ``base["starting_adr"]``).
        overall_adr = float(base.get("starting_adr") or 0.0)
        if overall_adr > 0:
            seeded_segments, seg_sources = _build_segments_from_str(
                payload=str_seg_payload,
                overall_adr=overall_adr,
                overrides=pre_load_seg_overrides or None,
            )
            if seeded_segments:
                base["segments"] = seeded_segments
                # Stash the pre-applied analyst-override map so the
                # below override-routing loop doesn't re-apply (no-op
                # safe, but explicit is clearer).
                if pre_load_seg_overrides:
                    base["segments_overrides"] = pre_load_seg_overrides
                for k, src in seg_sources.items():
                    sources[k] = src

    # Caller-supplied overrides from the API request body (the
    # ``assumptions`` payload on ``POST /deals/{id}/engines/run``).
    if overrides:
        base.update(overrides)
        for k in overrides:
            sources[k] = SOURCE_ANALYST_OVERRIDE

    # Persisted analyst overrides from the deal's ``field_overrides``
    # JSONB column (Roadmap item #6, June 2026 — see
    # ``_normalize_override_shape``). These are the structured
    # ``{value, note}`` records the OverridePanel writes via
    # ``PATCH /deals/{id}`` and they MUST be applied to the headline
    # assumptions (starting_occupancy, starting_adr, exit_cap_rate,
    # mgmt_fee_pct, etc.) — without this step the override was a no-op
    # because nothing in the engine chain read ``field_overrides`` for
    # these top-level keys. Applied last so analyst intent beats every
    # other data source (T12 actuals, CBRE, OM comps, deal row, seed).
    persisted_overrides = await _load_deal_overrides(
        session, deal_id=deal_id, tenant_id=effective_tenant
    )
    if scenario_id:
        # Wave 3 W3.2 — overlay the scenario's overrides on top of the
        # deal's persisted overrides; scenario values win on conflict
        # so the analyst can flex any base assumption inside a what-if.
        scenario_overrides = await _load_scenario_overrides(
            session, scenario_id=scenario_id
        )
        if scenario_overrides:
            persisted_overrides = {**persisted_overrides, **scenario_overrides}
    if persisted_overrides:
        for path, value in persisted_overrides.items():
            # Wave 2 P2.5 — capex array overrides land first because
            # they're the only override paths that legitimately carry a
            # JSON list (``roi_projects``, ``timing_pct_by_year``). The
            # scalar guard below would otherwise skip them.
            if path in _OVERRIDE_CAPEX_LIST_KEYS:
                if path == "capex_plan.roi_projects" and isinstance(value, list):
                    capex_overrides = base.setdefault("capex_plan_overrides", {})
                    capex_overrides["roi_projects"] = value
                    sources[path] = SOURCE_ROI_USER
                    continue
                if (
                    path == "capex_plan.pip.timing_pct_by_year"
                    and isinstance(value, list)
                ):
                    capex_overrides = base.setdefault("capex_plan_overrides", {})
                    capex_overrides.setdefault("pip", {})[
                        "timing_pct_by_year"
                    ] = value
                    sources[path] = SOURCE_PIP_USER
                    continue
            # Wave 2 P2.4 — PIP-displacement overrides include a list field
            # (``pct_rooms_offline_by_month``). Accept it as JSON-array
            # string or real list; everything else has to be a scalar.
            # Wave 3 W3.1 — comp_sales overrides similarly include a
            # list field (``exclude_transaction_ids``); same exemption.
            is_pip_key = path in _OVERRIDE_PIP_KEYS
            is_comps_key = path in _OVERRIDE_COMPS_KEYS
            if (
                not is_pip_key
                and not is_comps_key
                and not isinstance(value, (int, float, str, bool))
            ):
                continue
            # Route overrides to the right consumer based on key family.
            # Top-level base keys (starting_occupancy, exit_cap_rate,
            # mgmt_fee_pct, revpar_growth, ...) feed the engine inputs
            # directly. Expense-line dollar overrides need to flow into
            # ``t12_expense_actuals`` so the Expense engine treats them
            # as Y1 anchors. Ratio overrides land in ``overrides`` so
            # they beat the hotel-type defaults. Without this routing
            # an analyst override on ``rooms_dept_expense`` or
            # ``rooms_dept_pct`` would land at ``base[path]`` and never
            # be read by any engine.
            if path in _OVERRIDE_EXPENSE_ACTUAL_KEYS:
                base.setdefault("t12_expense_actuals", {})[path] = value
            elif path in _OVERRIDE_RATIO_KEYS:
                base.setdefault("overrides", {})[path] = value
            elif path in _OVERRIDE_SEGMENT_KEYS:
                # Wave 2 P2.1 — segment field override
                # ``segments.<name>.<field>``. Park in a nested map; the
                # segment builder layers these over the STR_SEGMENTATION
                # defaults (analyst always wins).
                parsed = _parse_segment_override_path(path)
                if parsed:
                    seg_name, field = parsed
                    seg_overrides = base.setdefault("segments_overrides", {})
                    seg_overrides.setdefault(seg_name, {})[field] = value
            elif is_pip_key:
                # Wave 2 P2.4 — PIP-displacement override. Park each
                # field on ``base['pip_displacement_overrides']`` as a
                # flat dict; ``_build_input_for('revenue')`` reads it
                # and materializes a ``PIPDisplacement`` object.
                pip_field = path.split(".", 1)[1]
                pip_value = _coerce_pip_override_value(pip_field, value)
                if pip_value is not None:
                    base.setdefault("pip_displacement_overrides", {})[
                        pip_field
                    ] = pip_value
                    sources[path] = SOURCE_PIP_USER
                continue
            elif path in _OVERRIDE_CAPEX_SCALAR_KEYS:
                # Wave 2 P2.5 — capex three-bucket scalar override.
                # ``capex_plan.<bucket>.<field>`` lands in a nested
                # map the build_capex_plan() helper consumes.
                parts = path.split(".")
                if len(parts) == 3:
                    _, bucket, field = parts
                    capex_overrides = base.setdefault("capex_plan_overrides", {})
                    capex_overrides.setdefault(bucket, {})[field] = value
                    sources[path] = (
                        SOURCE_PIP_USER if bucket == "pip"
                        else SOURCE_CAPEX_FFE_DEFAULT
                    )
                    continue
            elif is_comps_key:
                # Wave 3 W3.1 — Comparable Sales override. Two paths:
                # ``comp_sales.derived_cap_rate_override`` pins a manual
                # cap rate (0..100 percent); ``comp_sales.exclude_transaction_ids``
                # marks specific comp rows as excluded from derivation.
                # Both land on ``base['comp_sales_overrides']`` so the
                # comp-sales loader can consume them transparently.
                comps_field = path.split(".", 1)[1]
                comps_value = _coerce_comp_sales_override_value(
                    comps_field, value
                )
                if comps_value is not None:
                    base.setdefault("comp_sales_overrides", {})[
                        comps_field
                    ] = comps_value
                    sources[path] = SOURCE_OM_COMPS
                continue
            elif path in _OVERRIDE_DEBT_KEYS:
                # Wave 4 W4.4 — debt stack v2 override. Tranche-indexed
                # scalars (``debt_stack.tranches.<idx>.<field>``) land
                # in ``base['debt_stack_overrides']['tranches'][idx]``;
                # stack-level scalars (refi knobs) land at the top of
                # that map. The debt-stack builder reads from this
                # nested dict after seeding the default stack.
                parsed_debt = _parse_debt_stack_override_path(path)
                if parsed_debt is not None:
                    kind, idx, field = parsed_debt
                    debt_overrides = base.setdefault("debt_stack_overrides", {})
                    if kind == "tranche" and idx is not None:
                        tranche_overrides = debt_overrides.setdefault(
                            "tranches", {}
                        )
                        try:
                            num_value = float(value) if isinstance(value, (int, float, str)) else None
                        except (TypeError, ValueError):
                            num_value = None
                        if num_value is not None:
                            tranche_overrides.setdefault(idx, {})[field] = num_value
                            sources[path] = SOURCE_ANALYST_OVERRIDE
                    elif kind == "stack":
                        try:
                            num_value = float(value) if isinstance(value, (int, float, str)) else None
                        except (TypeError, ValueError):
                            num_value = None
                        if num_value is not None:
                            debt_overrides[field] = num_value
                            sources[path] = SOURCE_ANALYST_OVERRIDE
                continue
            else:
                base[path] = value
            sources[path] = SOURCE_ANALYST_OVERRIDE

    # Wave 3 W3.3 — optional STR forward-forecast seed. When the analyst
    # has flipped ``revenue_seed_from_str_forecast`` to True (default is
    # False so existing deals are unaffected), seed the revenue engine's
    # ``starting_occupancy`` + ``starting_adr`` from the BASE scenario's
    # Month-12 forecast point. Implemented as a no-op when:
    #   * the flag is False / absent (default — no regression);
    #   * the STR Trend extraction is missing or below coverage;
    #   * the load fails (best-effort — analyst sees badge stay at the
    #     prior source rather than a 500).
    if base.get("revenue_seed_from_str_forecast") is True:
        try:
            forecast = await _load_str_forecast_for_seed(
                session, deal_id=deal_id, tenant_id=effective_tenant
            )
        except Exception:
            forecast = None
        if forecast is not None:
            seed_occ, seed_adr = forecast
            base["starting_occupancy"] = seed_occ
            base["starting_adr"] = seed_adr
            sources["starting_occupancy"] = SOURCE_STR_FORECAST
            sources["starting_adr"] = SOURCE_STR_FORECAST

    base["__sources__"] = sources
    return base


# Canonical T-12 expense-actual keys (per-line dollar amounts). When the
# analyst overrides one of these, route it into ``t12_expense_actuals``
# so the Expense engine sees it as a Y1 anchor instead of a stray
# top-level field. Keep in sync with `T12_EXPENSE_FIELD_ALIASES` keys.
_OVERRIDE_EXPENSE_ACTUAL_KEYS: frozenset[str] = frozenset({
    "rooms_dept_expense",
    "fb_dept_expense",
    "other_dept_expense",
    "administrative_general",
    "information_telecom",
    "sales_marketing",
    "property_operations",
    "utilities",
    "property_taxes",
    "insurance",
    "mgmt_fee",
    "ffe_reserve",
})

# Hotel-type ratio overrides — these beat the HOTEL_TYPE_DEFAULTS dict
# on the Expense engine. Land them in ``base['overrides']`` so the
# engine's existing override-merge path picks them up.
_OVERRIDE_RATIO_KEYS: frozenset[str] = frozenset({
    "rooms_dept_pct",
    "fb_dept_pct",
    "other_dept_pct",
    "undistributed_pct_revenue",
    "fixed_pct_revenue",
})


# Wave 2 P2.1 — segment overrides. The analyst can override any of the
# 5 segments × 4 fields (mix_pct, adr, channel_cost_pct, adr_growth)
# via the OverridePanel. We route these into ``base["segments_overrides"]``
# as a nested ``{segment_name: {field: value}}`` dict; the segment
# builder consumes that map after layering on the STR_SEGMENTATION
# defaults so analyst intent wins regardless of seed source.
_SEGMENT_NAMES: tuple[str, ...] = (
    "transient_bar",
    "transient_ota",
    "corporate",
    "group",
    "contract",
)
_SEGMENT_FIELDS: tuple[str, ...] = (
    "mix_pct",
    "adr",
    "channel_cost_pct",
    "adr_growth",
)
_OVERRIDE_SEGMENT_KEYS: frozenset[str] = frozenset({
    f"segments.{seg}.{field}"
    for seg in _SEGMENT_NAMES
    for field in _SEGMENT_FIELDS
})




# Wave 2 P2.5 - capex three-bucket override paths. Scalars route into
# the canonical ``capex_plan.*`` map; ``roi_projects`` is a full-array
# replacement (analysts add/remove projects explicitly).
_OVERRIDE_CAPEX_SCALAR_KEYS: frozenset[str] = frozenset({
    "capex_plan.pip.total_usd",
    "capex_plan.pip.per_key_usd",
    "capex_plan.pip.completion_quarter",
    "capex_plan.non_pip.annual_pct_of_revenue",
    "capex_plan.non_pip.minimum_per_key_per_year",
})
_OVERRIDE_CAPEX_LIST_KEYS: frozenset[str] = frozenset({
    "capex_plan.pip.timing_pct_by_year",
    "capex_plan.roi_projects",
})

def _parse_segment_override_path(path: str) -> tuple[str, str] | None:
    """Split ``segments.<name>.<field>`` into ``(name, field)``.

    Returns ``None`` for paths that aren't segment overrides — caller
    treats those as legacy top-level keys.
    """
    if not path.startswith("segments."):
        return None
    parts = path.split(".", 2)
    if len(parts) != 3:
        return None
    _, name, field = parts
    if name not in _SEGMENT_NAMES or field not in _SEGMENT_FIELDS:
        return None
    return name, field


# Wave 2 P2.4 — structured PIP displacement overrides. The OverridePanel
# writes scalar keys (and one JSON-array key for the month-by-month
# schedule) under the ``pip_displacement.*`` path prefix; we route them
# into ``base['pip_displacement_overrides']`` so the revenue builder can
# materialize a ``PIPDisplacement``.
_OVERRIDE_PIP_KEYS: frozenset[str] = frozenset({
    "pip_displacement.closure_strategy",
    "pip_displacement.brand",
    "pip_displacement.revpar_index_post_reno",
    "pip_displacement.occupancy_recovery_months",
    "pip_displacement.pct_rooms_offline_by_month",
})


# Wave 3 W3.1 — Comparable Sales overrides. The analyst pins a manual
# derived cap rate (``derived_cap_rate_override``, 0..100 percent) or
# marks specific comp rows as excluded from the derivation
# (``exclude_transaction_ids``, JSON array of transaction_id strings).
# Both land on ``base['comp_sales_overrides']`` and the comp-sales
# engine consumes them when building the CompSalesSet.
_OVERRIDE_COMPS_KEYS: frozenset[str] = frozenset({
    "comp_sales.derived_cap_rate_override",
    "comp_sales.exclude_transaction_ids",
})


# Wave 4 W4.4 — debt stack v2 overrides. The OverridePanel writes
# tranche-indexed scalars (``debt_stack.tranches.<idx>.<field>``) for
# rate/principal/amort along with the stack-level refi knobs. Every
# override here lands on ``base['debt_stack_overrides']`` as a nested
# map the debt-stack builder consumes; provenance is tagged
# SOURCE_ANALYST_OVERRIDE so the UI badge reads "Analyst override".
_DEBT_STACK_TRANCHE_FIELDS: tuple[str, ...] = (
    "rate_pct",
    "principal_usd",
    "amortization_months",
    "io_period_months",
    "upfront_fee_pct",
    "exit_fee_pct",
)
_DEBT_STACK_TRANCHE_INDEXES: tuple[int, ...] = (0, 1, 2)
_OVERRIDE_DEBT_KEYS: frozenset[str] = frozenset(
    {
        f"debt_stack.tranches.{idx}.{field}"
        for idx in _DEBT_STACK_TRANCHE_INDEXES
        for field in _DEBT_STACK_TRANCHE_FIELDS
    }
    | {
        "debt_stack.refi_test_year",
        "debt_stack.refi_market_debt_yield_pct",
        "debt_stack.refi_market_dscr_min",
        "debt_stack.refi_market_cap_rate",
        "debt_stack.refi_market_rate_pct",
    }
)


def _parse_debt_stack_override_path(path: str) -> tuple[str, int | None, str] | None:
    """Split a ``debt_stack.*`` override path into (kind, index, field).

    Returns ``("tranche", idx, field)`` for tranche scalar paths and
    ``("stack", None, field)`` for stack-level scalars; ``None`` for
    paths that don't fit either pattern.
    """
    if not path.startswith("debt_stack."):
        return None
    parts = path.split(".")
    if len(parts) == 4 and parts[1] == "tranches":
        try:
            idx = int(parts[2])
        except ValueError:
            return None
        field = parts[3]
        if field not in _DEBT_STACK_TRANCHE_FIELDS:
            return None
        return ("tranche", idx, field)
    if len(parts) == 2:
        return ("stack", None, parts[1])
    return None


def _coerce_comp_sales_override_value(field: str, value: Any) -> Any:
    """Best-effort coercion of a comp_sales override value to its native type.

    ``derived_cap_rate_override`` is a scalar percent (0..100 — Sam's
    pilots write the value the same way the OM publishes it). The
    exclude list comes in either as a real Python list (PATCH from a
    typed client) or a JSON-array string (the OverridePanel writes the
    JSONB value as a string). Returns ``None`` to skip a malformed
    override rather than crash the run.
    """
    if field == "exclude_transaction_ids":
        if isinstance(value, list):
            return [str(x) for x in value if x is not None]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x is not None]
            return None
        return None
    if field == "derived_cap_rate_override":
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        # Tolerate 0..1 fractions (analyst typed "0.0725") or 0..100
        # percents (analyst typed "7.25"). Treat anything ≤ 0.30 as a
        # fraction and rescale to percent — keeps the engine math
        # uniformly in percent space.
        if 0 < v <= 0.30:
            v = v * 100.0
        if 0 < v <= 30.0:
            return v
        return None
    return None


def _coerce_pip_override_value(field: str, value: Any) -> Any:
    """Best-effort coercion of a PIP override value to its native type.

    The ``pct_rooms_offline_by_month`` field comes in as a JSON-array
    string from the OverridePanel (the underlying ``field_overrides``
    JSONB column carries scalars in the Roadmap-#6 design). Other PIP
    fields are scalars. Returns ``None`` to skip the override on a
    malformed value rather than crash the run.
    """
    if field == "pct_rooms_offline_by_month":
        if isinstance(value, list):
            try:
                return [float(x) for x in value]
            except (TypeError, ValueError):
                return None
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return None
            if isinstance(parsed, list):
                try:
                    return [float(x) for x in parsed]
                except (TypeError, ValueError):
                    return None
            return None
        return None
    if field == "occupancy_recovery_months":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if field == "revpar_index_post_reno":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if field == "closure_strategy":
        if isinstance(value, str) and value in (
            "rolling",
            "full_closure",
            "wing_by_wing",
            "none",
        ):
            return value
        return None
    if field == "brand":
        return str(value) if value is not None else None
    return None


def _build_pip_displacement(
    base: dict[str, Any],
) -> "PIPDisplacement | None":
    """Materialize a ``PIPDisplacement`` from the override map.

    Returns ``None`` unless the analyst has supplied at least
    ``closure_strategy`` (the gating field — without a strategy the
    engine has no way to interpret the rest). All other fields fall
    back to the schema defaults. Validation errors (e.g. inconsistent
    strategy/schedule) are swallowed at this layer so a malformed
    override doesn't crash the whole run; the caller sees ``None`` and
    we fall back to the legacy flat-pct path.
    """
    overrides = base.get("pip_displacement_overrides") or {}
    if not overrides or not overrides.get("closure_strategy"):
        return None
    kwargs: dict[str, Any] = {
        "closure_strategy": overrides["closure_strategy"],
    }
    if "brand" in overrides:
        kwargs["brand"] = overrides["brand"]
    if "revpar_index_post_reno" in overrides:
        kwargs["revpar_index_post_reno"] = overrides["revpar_index_post_reno"]
    if "occupancy_recovery_months" in overrides:
        kwargs["occupancy_recovery_months"] = overrides["occupancy_recovery_months"]
    if "pct_rooms_offline_by_month" in overrides:
        kwargs["pct_rooms_offline_by_month"] = overrides[
            "pct_rooms_offline_by_month"
        ]
    try:
        return PIPDisplacement(**kwargs)
    except Exception:  # noqa: BLE001 - intentional swallow
        logger.warning(
            "failed to materialize PIPDisplacement from overrides: %r",
            overrides,
        )
        return None


# Map extracted T-12 field paths onto the canonical expense-line keys
# the expense engine recognizes. Both the dotted ``p_and_l_usali.*``
# paths the Extractor agent emits and the bare lowercase aliases the
# legacy normalizer uses are accepted.
# Alias map externalized to extraction/field_catalog.yaml.
_T12_EXPENSE_FIELD_ALIASES: dict[str, str] = _CATALOG_T12_EXPENSE_FIELD_ALIASES


# Map extracted T-12 field paths onto the canonical revenue-side keys
# the revenue engine recognizes. As with the expense aliases, we accept
# both the dotted ``p_and_l_usali.operational_kpis.*`` paths the
# Extractor agent emits and the bare aliases the legacy normalizer uses.
# Alias map externalized to extraction/field_catalog.yaml.
_T12_REVENUE_FIELD_ALIASES: dict[str, str] = _CATALOG_T12_REVENUE_FIELD_ALIASES


# Rank P&L extraction rows by period_type so an annual T-12 always
# wins over a YTD-through-May or single-month upload — even when the
# monthly was extracted later. Eshan's QA found that a 5/2024 monthly
# upload was clobbering the annual T-12 baseline (~89% YTD occupancy
# vs ~81% true annual). Lower rank = preferred.
# Externalized to extraction/field_catalog.yaml.
_PERIOD_TYPE_RANK: dict[str, int] = _CATALOG_PERIOD_TYPE_RANK


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
        # Accept both SQLAlchemy Row objects and plain dict shims —
        # the terse-expansion call sites pre-process rows (await the
        # async expander) and hand us {"fields", "doc_type"} dicts
        # (hotfix 2026-07-05; 4fa867b passed bare tuples here which
        # crashed on ._mapping for every P&L deal).
        m = r._mapping if hasattr(r, "_mapping") else r
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


# Source-label → list of doc_types that produce that label. Used by
# _load_source_documents to map each provenance-tagged assumption back
# to the specific document_id that fed it.
_SOURCE_TO_DOC_TYPES: dict[str, tuple[str, ...]] = {
    SOURCE_T12_ACTUAL: ("T12", "PNL", "PNL_MONTHLY", "PNL_YTD"),
    SOURCE_CBRE_HORIZONS: ("CBRE_HORIZONS",),
    SOURCE_PNL_BENCHMARK: ("PNL_BENCHMARK",),
    SOURCE_OM_COMPS: ("OM",),
    SOURCE_OM_BROKER: ("OM",),
}


async def _load_source_documents(
    session: AsyncSession,
    *,
    deal_id: str,
    sources: Mapping[str, str],
    tenant_id: str,
) -> dict[str, str]:
    """For each provenance-tagged assumption key, return the
    ``document_id`` that most likely contributed the value.

    Approximate but useful: we don't track at value-write time which
    individual extraction row supplied each canonical key, so this
    function maps source labels back to doc_types and picks the
    highest-ranked document of that type for the deal:

      - P&L family (t12_actual) → highest-ranked by period_type
        (annual > trailing_twelve > ytd > monthly), then newest.
      - OM / CBRE / PNL_BENCHMARK → newest extracted doc of that type.

    Source labels with no document provenance (seed / deal_row /
    analyst_override) are omitted from the returned dict.

    The web UI uses this for "click NOI → jump to the T-12 that
    fed it" deep links.
    """
    out: dict[str, str] = {}
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return out

    # Build the by-doc-type pick once, then map source-tagged keys
    # onto it. Keeps the SQL count to one round-trip regardless of how
    # many assumption keys carry a source.
    needed_doc_types: set[str] = set()
    for src in sources.values():
        for dt in _SOURCE_TO_DOC_TYPES.get(src, ()):
            needed_doc_types.add(dt)
    if not needed_doc_types:
        return out

    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.document_id, er.fields, d.doc_type, er.created_at
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type = ANY(:types)
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id, "types": list(needed_doc_types)},
        )
        all_rows = rows.fetchall()
    except Exception:
        # SQLite doesn't support ARRAY parameters — fall back to a
        # plain IN-list. Best-effort; the API caller treats a missing
        # map as a degraded UI signal, not a failure.
        try:
            placeholders = ",".join(f":t{i}" for i, _ in enumerate(needed_doc_types))
            params: dict[str, Any] = {"deal": deal_id, "tenant": tenant_id}
            for i, t in enumerate(needed_doc_types):
                params[f"t{i}"] = t
            rows = await session.execute(
                text(
                    # tenant-scope predicate required by tenant_middleware
                    f"""
                    SELECT er.document_id, er.fields, d.doc_type, er.created_at
                      FROM extraction_results er
                      JOIN documents d ON d.id = er.document_id
                     WHERE er.deal_id = :deal
                       AND er.tenant_id = :tenant
                       AND d.tenant_id = :tenant
                       AND d.doc_type IN ({placeholders})
                     ORDER BY er.created_at DESC
                    """
                ),
                params,
            )
            all_rows = rows.fetchall()
        except Exception:
            return out

    # Group rows by doc_type, ranking the P&L family by period_type.
    by_doc_type: dict[str, list[Any]] = {}
    for r in all_rows:
        dt = (r._mapping.get("doc_type") or "").upper()
        by_doc_type.setdefault(dt, []).append(r)

    # Pick the winner per doc_type.
    winners: dict[str, str] = {}
    pnl_family = {"T12", "PNL", "PNL_MONTHLY", "PNL_YTD"}
    for dt, rs in by_doc_type.items():
        if dt in pnl_family:
            # Use the same period_type ranking as the loaders.
            best_rank = 99
            best_doc: str | None = None
            for r in rs:
                m = r._mapping
                raw = m["fields"]
                if isinstance(raw, str):
                    try:
                        raw = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not isinstance(raw, list):
                    continue
                pt = _extract_period_type(raw)
                rank = _PERIOD_TYPE_RANK.get(pt, 50)
                if rank < best_rank:
                    best_rank = rank
                    best_doc = str(m["document_id"])
            if best_doc:
                winners[dt] = best_doc
        else:
            # OM / CBRE / PNL_BENCHMARK — newest doc by created_at.
            winners[dt] = str(rs[0]._mapping["document_id"])

    # Map each source-tagged key to whichever doc_type's winner applies.
    for canonical, src in sources.items():
        candidate_types = _SOURCE_TO_DOC_TYPES.get(src, ())
        for dt in candidate_types:
            if dt in winners:
                out[canonical] = winners[dt]
                break
    return out


async def _load_t12_revenue_actuals(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
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
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields, d.doc_type, er.catalog_version
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type IN ('T12','PNL','PNL_MONTHLY','PNL_YTD')
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
    except Exception:
        return {}

    # Rank-then-merge so a true annual T-12 wins over a YTD-monthly upload
    # even when the monthly was extracted later.
    # HOTFIX 2026-07-05: 4fa867b fed bare (fields, doc_type) tuples into
    # _rank_pnl_rows one row at a time — crashing on ._mapping AND
    # destroying the rank-across-rows semantics — and called
    # asyncio.run() inside this async function (RuntimeError on any
    # terse row). Restored: expand terse first (await), then rank all
    # rows together via dict shims.
    from ..extraction.terse_schema import expand_extraction_result

    shims: list[dict[str, Any]] = []
    for row in rows.fetchall():
        raw_fields = row[0]
        catalog_version = row[2] if len(row) > 2 else None
        if (
            isinstance(raw_fields, list)
            and raw_fields
            and isinstance(raw_fields[0], dict)
            and "fid" in raw_fields[0]
        ):
            raw_fields = await expand_extraction_result(raw_fields, catalog_version)
        shims.append({"fields": raw_fields, "doc_type": row[1]})

    actuals: dict[str, float] = {}
    for ranked_fields, _ in _rank_pnl_rows(shims):
        for f in ranked_fields:
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
    tenant_id: str,
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
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields, d.doc_type, er.catalog_version
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type IN ('T12','PNL','PNL_MONTHLY','PNL_YTD')
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
    except Exception:
        return {}

    # Rank-then-merge so an annual T-12's expense lines win over a
    # partial-year YTD extract that's missing some buckets.
    # HOTFIX 2026-07-05: same restore as _load_t12_revenue_actuals —
    # see comment there (4fa867b tuple/_mapping + asyncio.run bugs).
    from ..extraction.terse_schema import expand_extraction_result

    shims: list[dict[str, Any]] = []
    for row in rows.fetchall():
        raw_fields = row[0]
        catalog_version = row[2] if len(row) > 2 else None
        if (
            isinstance(raw_fields, list)
            and raw_fields
            and isinstance(raw_fields[0], dict)
            and "fid" in raw_fields[0]
        ):
            raw_fields = await expand_extraction_result(raw_fields, catalog_version)
        shims.append({"fields": raw_fields, "doc_type": row[1]})

    actuals: dict[str, float] = {}
    for ranked_fields, _ in _rank_pnl_rows(shims):
        for f in ranked_fields:
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
# Externalized to extraction/field_catalog.yaml.
_OM_CAPITAL_FIELD_ALIASES: dict[str, str] = _CATALOG_OM_CAPITAL_FIELD_ALIASES


# Map extracted OM field paths onto the canonical debt-side keys the
# debt engine recognizes. ``in_place_debt.*`` carries the broker's quote
# of the seller's existing financing; we let it override the LTV-derived
# default loan amount when the broker publishes a hard balance.
# Externalized to extraction/field_catalog.yaml.
_OM_DEBT_FIELD_ALIASES: dict[str, str] = _CATALOG_OM_DEBT_FIELD_ALIASES


# Canonical keys whose extracted value lands in the assumption dict as a
# 0..1 fraction. Extractors emit either a 0..1 ratio or a 0..100 percent;
# we normalize defensively (mirrors the T-12 occupancy normalization).
# Externalized to extraction/field_catalog.yaml (percentage_keys).
_OM_PERCENTAGE_KEYS: frozenset[str] = _CATALOG_OM_PERCENTAGE_KEYS


def _normalize_override_shape(raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten both legacy and structured override shapes to ``{path: value}``.

    Roadmap item #6 (June 2026 call). Two shapes coexist:

    * Legacy (pre-2026-06): ``{"property_overview.year_built": 2005}``
    * Structured (2026-06+): ``{"property_overview.year_built": {
        "value": 2005, "note": "Major facade refresh per OM",
        "overridden_by": "user:alice", "overridden_at": "..."}}``

    Engines only need the scalar; the note + audit fields are surfaced
    by the deal-row response shape and the IC memo's underwriting
    section. Normalize here so ``_apply_overrides`` stays simple.
    """
    out: dict[str, Any] = {}
    for path, val in raw.items():
        if isinstance(val, dict) and "value" in val:
            out[path] = val["value"]
        else:
            out[path] = val
    return out


async def _load_scenario_overrides(
    session: AsyncSession,
    *,
    scenario_id: str,
) -> dict[str, Any]:
    """Read a scenario's overrides as a flat ``{field_path: value}`` map.

    Wave 3 W3.2. Scenarios store overrides as a JSONB array of
    ``{field_path, value, source}`` objects (see
    :class:`apps.worker.app.api.scenarios.ScenarioOverrideBody`); we
    flatten here so the engine input loader's existing override-routing
    loop accepts the same shape it already understands.

    Returns ``{}`` for non-UUID ids, missing rows, malformed payloads,
    or test DBs where the migration hasn't run yet.
    """
    try:
        UUID(scenario_id)
    except (ValueError, TypeError):
        return {}
    try:
        row = (
            await session.execute(
                text("SELECT overrides FROM scenarios WHERE id = :id"),
                {"id": scenario_id},
            )
        ).first()
    except Exception:
        return {}
    if row is None:
        return {}
    raw = row._mapping.get("overrides")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        raw = parsed
    if not isinstance(raw, list):
        return {}
    out: dict[str, Any] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        path = entry.get("field_path")
        if not isinstance(path, str) or not path:
            continue
        out[path] = entry.get("value")
    return out


async def _load_deal_overrides(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> dict[str, Any]:
    """Read the deal's `field_overrides` JSONB column.

    Returns ``{}`` for non-UUID ids, missing rows, or schemas where the
    migration hasn't run yet (test DBs). The column is keyed by canonical
    extractor field path (e.g. ``property_overview.year_built``) →
    primitive value. See ``_normalize_override_shape`` for the legacy /
    structured shape handling.
    """
    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return {}
    try:
        row = (
            await session.execute(
                text(
                    # tenant-scope predicate required by tenant_middleware
                    "SELECT field_overrides FROM deals "
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": deal_id, "tenant": tenant_id},
            )
        ).first()
    except Exception:
        return {}
    if row is None:
        return {}
    raw = row._mapping.get("field_overrides")
    if isinstance(raw, dict):
        return _normalize_override_shape(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(parsed, dict):
            return {}
        return _normalize_override_shape(parsed)
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
    tenant_id: str,
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
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
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
    overrides = await _load_deal_overrides(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
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
    tenant_id: str,
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
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
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


async def _load_comp_transactions(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> list[Any]:
    """Build the list of ``CompTransaction`` rows for a deal.

    Reads OM extraction results and assembles per-comp records keyed
    on the row's 1-based index. Reads BOTH the rich ``comparable_sales.<n>.*``
    namespace (Wave 3 W3.1) and the legacy ``transaction_comps.<n>.*``
    namespace (pre-W3.1) — both produce a ``CompTransaction``. When a
    comp appears under both namespaces, the richer ``comparable_sales.*``
    fields win.

    Returns an empty list for non-UUID deal ids and for deals with no
    OM extraction (degrades gracefully — the engine then reports
    ``coverage_quality="low"`` with no derivation).
    """
    from fondok_schemas.comp_sales import CompTransaction

    try:
        UUID(deal_id)
    except (ValueError, TypeError):
        return []
    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields, er.document_id
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
    except Exception:
        return []

    # Each comp is keyed by (document_id, index) so two OMs uploaded
    # for the same deal don't collide on comp #1. ``slot`` is a dict
    # the schema fields land into; we convert to ``CompTransaction``
    # at the end.
    comps: dict[tuple[str, int], dict[str, Any]] = {}

    for r in rows.fetchall():
        raw_fields = r._mapping["fields"]
        doc_id = str(r._mapping.get("document_id") or "")
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
            page = f.get("page_number") if isinstance(f.get("page_number"), int) else None

            # Match both namespaces. Skip anything that isn't structured
            # as ``<prefix>.<index>.<field>``.
            prefix: str
            if name.startswith("comparable_sales."):
                prefix = "comparable_sales"
            elif name.startswith("transaction_comps."):
                prefix = "transaction_comps"
            else:
                continue
            parts = name.split(".", 2)
            if len(parts) != 3:
                continue
            try:
                idx = int(parts[1])
            except (TypeError, ValueError):
                continue
            field = parts[2]

            key = (doc_id, idx)
            slot = comps.setdefault(key, {
                "_doc_id": doc_id,
                "_idx": idx,
                "_page": page,
                "_namespace_priority": 0,
            })
            # Track namespace priority. ``comparable_sales`` (priority 1)
            # wins over ``transaction_comps`` (priority 0) when both
            # produce the same field.
            ns_priority = 1 if prefix == "comparable_sales" else 0

            # Field aliasing: the legacy namespace uses ``name`` /
            # ``market`` / ``sale_date`` / ``buyer_name`` / ``buyer_type``;
            # the new namespace uses ``property_name`` / ``city`` /
            # ``state`` / ``brand_family`` / ``flag`` / ``chain_scale`` /
            # ``note``. Reconcile here.
            field_canonical = {
                # legacy → new
                "name": "property_name",
                "market": "city",
                "sale_date": "sale_date",
                "keys": "keys",
                "sale_price_usd": "sale_price_usd",
                "price_per_key_usd": "sale_price_per_key_usd",
                "sale_price_per_key_usd": "sale_price_per_key_usd",
                "cap_rate_pct": "cap_rate_pct",
                "cap_rate": "cap_rate_pct",
                # new namespace fields pass through
                "property_name": "property_name",
                "city": "city",
                "state": "state",
                "noi_usd": "noi_usd",
                "chain_scale": "chain_scale",
                "brand_family": "brand_family",
                "flag": "flag",
                "note": "note",
            }.get(field)
            if field_canonical is None:
                continue

            # Apply priority: only overwrite if the incoming row's
            # namespace is at least as authoritative.
            if (
                field_canonical in slot
                and slot.get("_namespace_priority", 0) > ns_priority
            ):
                continue

            # Type coerce per-field.
            if field_canonical in (
                "keys",
            ):
                try:
                    slot[field_canonical] = int(value)
                except (TypeError, ValueError):
                    continue
            elif field_canonical in (
                "sale_price_usd",
                "sale_price_per_key_usd",
                "noi_usd",
                "cap_rate_pct",
            ):
                try:
                    fv = float(value)
                except (TypeError, ValueError):
                    continue
                # cap_rate_pct: tolerate 0..1 fractions from the legacy
                # extractor (transaction_comps.<n>.cap_rate).
                if field_canonical == "cap_rate_pct" and 0 < fv <= 1.0:
                    fv = fv * 100.0
                slot[field_canonical] = fv
            elif field_canonical == "sale_date":
                if isinstance(value, str):
                    try:
                        from datetime import date as _date

                        slot[field_canonical] = _date.fromisoformat(value[:10])
                    except (ValueError, TypeError):
                        continue
            else:
                # All other fields are str | None on the schema.
                if value is None:
                    continue
                slot[field_canonical] = str(value)
            slot["_namespace_priority"] = ns_priority
            if page is not None and slot.get("_page") is None:
                slot["_page"] = page

    out: list[Any] = []
    for (doc_id, idx), slot in comps.items():
        # transaction_id is a stable per-deal identifier — the analyst
        # exclude-list keys off this. Use ``<doc-tail>:<idx>`` so two
        # OMs uploaded for the same deal don't collide.
        doc_tail = (doc_id or "doc")[:8] or "doc"
        transaction_id = f"{doc_tail}:{idx}"
        try:
            out.append(
                CompTransaction(
                    property_name=slot.get("property_name"),
                    city=slot.get("city"),
                    state=slot.get("state"),
                    sale_date=slot.get("sale_date"),
                    keys=slot.get("keys"),
                    sale_price_usd=slot.get("sale_price_usd"),
                    sale_price_per_key_usd=slot.get("sale_price_per_key_usd"),
                    noi_usd=slot.get("noi_usd"),
                    cap_rate_pct=slot.get("cap_rate_pct"),
                    chain_scale=slot.get("chain_scale"),
                    brand_family=slot.get("brand_family"),
                    flag=slot.get("flag"),
                    source_document_id=doc_id or "",
                    source_page_number=slot.get("_page"),
                    note=slot.get("note"),
                    transaction_id=transaction_id,
                )
            )
        except Exception:  # noqa: BLE001 - silent skip malformed rows
            logger.warning(
                "comp_sales: skipping malformed extracted comp idx=%s doc=%s",
                idx,
                doc_id,
            )
            continue
    # Sort by sale_date desc with no-date rows last — keeps the table
    # view stable across runs.
    out.sort(
        key=lambda t: (
            t.sale_date is None,
            -(t.sale_date.toordinal() if t.sale_date else 0),
        )
    )
    return out


async def _build_comp_sales_set(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
    subject_market: str | None = None,
    subject_chain_scale: str | None = None,
    lookback_years: int = 5,
) -> Any:
    """Run the comp-sales engine for a deal, end-to-end.

    Reads comp transactions from extraction, layers analyst overrides
    (exclude list + manual cap rate pin) on top, and returns the
    ``CompSalesSet`` Pydantic model. The API endpoint hands it back to
    the web UI as-is.

    When a ``derived_cap_rate_override`` is pinned, both the median +
    weighted derivations are recomputed normally — the override stays
    on the side as a "pinned" indicator the UI surfaces; the engine
    runner is responsible for actually wiring the pinned number to
    ``exit_cap_rate``.
    """
    from app.engines.comp_sales import build_comp_set

    transactions = await _load_comp_transactions(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    overrides = await _load_deal_overrides(
        session, deal_id=deal_id, tenant_id=tenant_id
    )

    # Pull comp_sales overrides off the persisted map (analyst exclude
    # list + manual cap pin).
    exclude_ids_raw = overrides.get("comp_sales.exclude_transaction_ids")
    if isinstance(exclude_ids_raw, list):
        exclude_ids = [str(x) for x in exclude_ids_raw if x is not None]
    elif isinstance(exclude_ids_raw, str):
        try:
            parsed = json.loads(exclude_ids_raw)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        exclude_ids = (
            [str(x) for x in parsed if x is not None]
            if isinstance(parsed, list)
            else []
        )
    else:
        exclude_ids = []

    comp_set = build_comp_set(
        deal_id=deal_id,
        transactions=transactions,
        subject_market=subject_market,
        subject_chain_scale=subject_chain_scale,
        lookback_years=lookback_years,
        exclude_transaction_ids=exclude_ids,
    )
    return comp_set


async def _load_om_debt_actuals(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
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
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields, d.doc_type
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND d.doc_type = 'OM'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
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
    overrides = await _load_deal_overrides(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    _apply_overrides(
        actuals,
        overrides,
        _OM_DEBT_FIELD_ALIASES,
        percentage_keys=_OM_PERCENTAGE_KEYS,
    )
    return actuals


# ─────────────────── CBRE Horizons projection overrides ─────────────────


async def _load_cbre_horizons_overrides(
    session: AsyncSession, *, deal_id: str, tenant_id: str
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
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND UPPER(COALESCE(d.doc_type, '')) = 'CBRE_HORIZONS'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
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


# ─────────────────── STR Segmentation → segment defaults ──────────────


async def _load_str_segmentation_payload(
    session: AsyncSession, *, deal_id: str, tenant_id: str
) -> dict[str, Any]:
    """Read the deal's most recent STR_SEGMENTATION extraction into a
    flat namespace dict shaped as ``{<path>: value}``.

    Returns ``{}`` when no STR_SEGMENTATION extraction exists, when the
    deal id isn't a UUID, or when the migrations haven't been applied.
    """
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND UPPER(COALESCE(d.doc_type, '')) = 'STR_SEGMENTATION'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
    except Exception:
        return {}

    out: dict[str, Any] = {}
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
            if not name or value is None:
                continue
            # First-write wins (we sort newest first; the freshest
            # extraction's value lands first and stays).
            out.setdefault(name, value)
    return out


def _seg_pct(value: Any) -> float | None:
    """Coerce a percentage / ratio into the 0..1 form.

    Returns ``None`` when the value isn't numeric or rounds to 0. We
    treat values strictly > 1 as 0..100 percentages (the extractor
    sometimes emits them in either form).
    """
    if not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v <= 0:
        return None
    if v > 1.0:
        v = v / 100.0
    if v <= 0 or v > 1.0:
        return None
    return v


def _select_segmentation_period(payload: dict[str, Any]) -> str | None:
    """Pick the best STR Segmentation period — TTM > YTD > MTD.

    Returns the canonical prefix (e.g. ``"str_segmentation.ttm"``) or
    ``None`` when no transient mix is present in any period.
    """
    for period in ("ttm", "ytd", "mtd"):
        prefix = f"str_segmentation.{period}"
        transient_mix = _seg_pct(payload.get(f"{prefix}.transient.mix_pct"))
        if transient_mix is not None and transient_mix > 0:
            return prefix
    return None


def _build_segments_from_str(
    *,
    payload: dict[str, Any],
    overall_adr: float,
    overrides: Mapping[str, Mapping[str, float]] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Translate an STR_SEGMENTATION extraction into a default list of
    ``RevenueSegment``-shaped dicts plus a provenance map.

    The five segments are always emitted in canonical order (transient_bar,
    transient_ota, corporate, group, contract). Empty buckets get
    ``mix_pct=0`` so the engine can short-circuit. The mix shares are
    re-normalized to sum to 1.0 within ±0.001 tolerance so analyst-rounded
    inputs survive the validator.

    Returns a tuple ``(segments, sources_by_field)`` where
    ``sources_by_field`` keys look like ``segments.transient_ota.mix_pct``
    so the assumption-source map matches the override-routing path
    naming.

    ``overrides`` (if provided) lets the analyst's persisted segment-field
    overrides take priority; they're applied AFTER the seed so analyst
    intent wins regardless of seed source.
    """
    period_prefix = _select_segmentation_period(payload)

    # Mix shares (room-night basis)
    if period_prefix:
        transient_mix = _seg_pct(payload.get(f"{period_prefix}.transient.mix_pct")) or 0.0
        group_mix = _seg_pct(payload.get(f"{period_prefix}.group.mix_pct")) or 0.0
        contract_mix = _seg_pct(payload.get(f"{period_prefix}.contract.mix_pct")) or 0.0
    else:
        # No useful STR Segmentation data — return empty so the engine
        # stays on the legacy single-line path.
        return [], {}

    # When mix shares already saturate room nights, leave the contract
    # bucket empty (most real reports don't break out contract demand).
    declared_total = transient_mix + group_mix + contract_mix
    if declared_total <= 0:
        return [], {}

    # Channel mix WITHIN transient demand: if the report carries the
    # Direct / OTA / Brand / Voice block, use it to split transient
    # into BAR / OTA / Corporate; otherwise fall back to a 60/30/10
    # default (institutional default for lifestyle / select-service).
    ota_pct: float | None = None
    corporate_within_transient_pct: float | None = None
    if period_prefix:
        ota_pct = _seg_pct(payload.get(f"{period_prefix}.channel_mix.ota_pct"))
        corporate_within_transient_pct = _seg_pct(
            payload.get(f"{period_prefix}.channel_mix.corporate_pct")
        )

    if ota_pct is not None or corporate_within_transient_pct is not None:
        ota = ota_pct or 0.0
        corp_in_t = corporate_within_transient_pct or 0.0
        # BAR = whatever remains; Voice + Direct + Brand.com all roll up
        # into BAR for the engine's purposes (CC-fee-only channel).
        bar = max(0.0, 1.0 - ota - corp_in_t)
        transient_bar_mix = transient_mix * bar
        transient_ota_mix = transient_mix * ota
        corporate_mix = transient_mix * corp_in_t
    else:
        # 60/30/10 default within transient.
        transient_bar_mix = transient_mix * 0.60
        transient_ota_mix = transient_mix * 0.30
        corporate_mix = transient_mix * 0.10

    # Per-segment ADRs: prefer the segment-level numbers when the report
    # carries them; otherwise apply default ratios to the property
    # overall ADR.
    def _adr_for(seg_name: str, str_field: str | None) -> float:
        if period_prefix and str_field is not None:
            extracted = payload.get(f"{period_prefix}.{str_field}.adr_usd")
            if isinstance(extracted, (int, float)) and extracted > 0:
                return float(extracted)
        return overall_adr * _DEFAULT_SEGMENT_ADR_RATIO[seg_name]

    transient_adr = _adr_for("transient_bar", "transient")
    group_adr = _adr_for("group", "group")
    contract_adr = _adr_for("contract", "contract")

    segments: list[dict[str, Any]] = [
        {
            "name": "transient_bar",
            "mix_pct": transient_bar_mix,
            "adr": transient_adr,
            "channel_cost_pct": _INSTITUTIONAL_CHANNEL_COST_DEFAULTS["transient_bar"],
        },
        {
            "name": "transient_ota",
            "mix_pct": transient_ota_mix,
            "adr": transient_adr,
            "channel_cost_pct": _INSTITUTIONAL_CHANNEL_COST_DEFAULTS["transient_ota"],
        },
        {
            "name": "corporate",
            "mix_pct": corporate_mix,
            "adr": overall_adr * _DEFAULT_SEGMENT_ADR_RATIO["corporate"],
            "channel_cost_pct": _INSTITUTIONAL_CHANNEL_COST_DEFAULTS["corporate"],
        },
        {
            "name": "group",
            "mix_pct": group_mix,
            "adr": group_adr,
            "channel_cost_pct": _INSTITUTIONAL_CHANNEL_COST_DEFAULTS["group"],
        },
        {
            "name": "contract",
            "mix_pct": contract_mix,
            "adr": contract_adr,
            "channel_cost_pct": _INSTITUTIONAL_CHANNEL_COST_DEFAULTS["contract"],
        },
    ]

    # Renormalize mix shares to 1.0 — analyst-rounded STR percentages
    # frequently sum to 0.998 or 1.002, which the validator rejects.
    total_mix = sum(s["mix_pct"] for s in segments)
    if total_mix <= 0:
        return [], {}
    for s in segments:
        s["mix_pct"] = s["mix_pct"] / total_mix

    # Apply analyst overrides (segment-field grain). Overrides win over
    # the STR seed unconditionally.
    if overrides:
        for s in segments:
            seg_overrides = overrides.get(s["name"])
            if not seg_overrides:
                continue
            for field, val in seg_overrides.items():
                if field not in _SEGMENT_FIELDS:
                    continue
                s[field] = val
        # Re-normalize if mix overrides made the total drift outside
        # tolerance. We don't force-rescale — that would silently
        # rewrite analyst intent — but if the sum is within 0.5% we
        # nudge it back, otherwise we leave it for the validator.
        total_mix = sum(s["mix_pct"] for s in segments)
        if 0 < total_mix and abs(total_mix - 1.0) <= 0.005 and total_mix != 1.0:
            for s in segments:
                s["mix_pct"] = s["mix_pct"] / total_mix

    sources_by_field: dict[str, str] = {}
    for s in segments:
        for field in _SEGMENT_FIELDS:
            key = f"segments.{s['name']}.{field}"
            if overrides and s["name"] in overrides and field in overrides[s["name"]]:
                sources_by_field[key] = SOURCE_ANALYST_OVERRIDE
            else:
                sources_by_field[key] = SOURCE_STR_SEGMENTATION_DEFAULT

    return segments, sources_by_field


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
    session: AsyncSession, *, deal_id: str, tenant_id: str
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
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND UPPER(COALESCE(d.doc_type, '')) = 'PNL_BENCHMARK'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
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


# ────────────── Portfolio P&L Library + per-deal PORTFOLIO_PNL ───────────


# Map PORTFOLIO_PNL extraction field_names → canonical engine ratio keys.
# Mirrors ``apps/worker/app/api/portfolio_library.py::_PORTFOLIO_FIELD_MAP``
# (kept in sync — they're the same paths defined in the schema MD).
_PORTFOLIO_PNL_FIELD_MAP: dict[str, str] = {
    "portfolio_pnl.rooms_dept_pct": "rooms_dept_pct",
    "portfolio_pnl.fb_dept_pct": "fb_dept_pct",
    "portfolio_pnl.other_ops_dept_pct": "other_ops_dept_pct",
    "portfolio_pnl.admin_pct": "admin_pct",
    "portfolio_pnl.sales_pct": "sales_pct",
    "portfolio_pnl.prop_ops_pct": "prop_ops_pct",
    "portfolio_pnl.utilities_pct": "utilities_pct",
    "portfolio_pnl.marketing_pct": "marketing_pct",
    "portfolio_pnl.management_fee_pct": "mgmt_fee_pct",
    "portfolio_pnl.property_tax_pct": "property_tax_pct",
    "portfolio_pnl.insurance_pct": "insurance_pct",
    "portfolio_pnl.ffe_reserve_pct": "ffe_reserve_pct",
    "portfolio_pnl.gop_margin": "gop_margin",
    "portfolio_pnl.noi_margin": "noi_margin",
}

# Vintage look-back window — Wave 1 product decision (2026-06-27):
# 5-year gap look-back for op-ratios is too stale; portfolio benchmarks
# decay faster than that. The library defaults to a 3-year window
# (current_year - vintage_year <= 3) so analysts can't accidentally
# apply a 2018 portfolio roll-up to a 2026 deal.
_PORTFOLIO_LIBRARY_LOOKBACK_YEARS = 3


def _normalize_chain_scale_for_library(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    return s.lower().replace("_", " ").replace("-", " ")


def _current_year_for_library() -> int:
    return datetime.now(UTC).year


async def _load_portfolio_library_overrides(
    session: AsyncSession,
    *,
    tenant_id: str,
    subject_chain_scale: str | None,
) -> dict[str, float]:
    """Aggregate firm-level Portfolio Library entries into per-ratio medians.

    Strategy:
        1. Query every active library entry for the tenant whose
           ``vintage_year`` is within the 3-year look-back window.
        2. Filter to entries whose ``chain_scales_covered`` overlap the
           subject deal's chain scale. An entry with an EMPTY
           ``chain_scales_covered`` list is treated as "covers
           everything" (analyst opt-in to apply across all deals).
        3. For each ratio present in ANY qualifying entry, compute the
           median across entries that define that ratio.
        4. Return the median map keyed by canonical engine ratio name.

    Returns ``{}`` when no qualifying entries exist — the engine then
    falls through to per-deal PORTFOLIO_PNL docs, then CBRE, etc.
    """
    try:
        UUID(tenant_id)
    except (TypeError, ValueError):
        return {}
    min_vintage = _current_year_for_library() - _PORTFOLIO_LIBRARY_LOOKBACK_YEARS
    try:
        rows = await session.execute(
            text(
                """
                SELECT chain_scales_covered, expense_ratios
                  FROM portfolio_library
                 WHERE tenant_id = :tenant
                   AND is_active = :is_active
                   AND vintage_year >= :min_vintage
                """
            ),
            {
                "tenant": tenant_id,
                "is_active": True,
                "min_vintage": min_vintage,
            },
        )
    except Exception:
        return {}

    normalized_subject = _normalize_chain_scale_for_library(subject_chain_scale)
    per_ratio_values: dict[str, list[float]] = {}
    for r in rows.fetchall():
        m = r._mapping
        chain_scales_raw = m.get("chain_scales_covered")
        # JSONB parsed by asyncpg arrives as list; SQLite as TEXT.
        if isinstance(chain_scales_raw, str):
            try:
                chain_scales = json.loads(chain_scales_raw)
            except (json.JSONDecodeError, TypeError):
                chain_scales = []
        elif isinstance(chain_scales_raw, list):
            chain_scales = chain_scales_raw
        else:
            chain_scales = []

        # Chain-scale gate: empty list ⇒ entry covers everything; non-
        # empty ⇒ at least one element must match the subject (loose
        # equality). When the subject's chain scale is unknown we accept
        # every entry (degraded matching beats no library at all).
        if chain_scales:
            normalized_entries = [
                _normalize_chain_scale_for_library(s) for s in chain_scales
            ]
            normalized_entries = [x for x in normalized_entries if x]
            if normalized_subject is not None and normalized_entries:
                if normalized_subject not in normalized_entries:
                    continue

        ratios_raw = m.get("expense_ratios")
        if isinstance(ratios_raw, str):
            try:
                ratios = json.loads(ratios_raw)
            except (json.JSONDecodeError, TypeError):
                ratios = {}
        elif isinstance(ratios_raw, dict):
            ratios = ratios_raw
        else:
            ratios = {}
        if not isinstance(ratios, dict):
            continue

        for k, v in ratios.items():
            if not isinstance(v, (int, float)):
                continue
            value = float(v)
            # Normalize 0..100 → 0..1 when the analyst stored as percent.
            if value > 1.0 and value <= 100.0:
                value = value / 100.0
            if not (0.0 < value < 1.5):
                # Reject obvious garbage (negatives, > 150% are not ratios).
                continue
            per_ratio_values.setdefault(str(k), []).append(value)

    out: dict[str, float] = {}
    for key, values in per_ratio_values.items():
        if not values:
            continue
        sorted_vals = sorted(values)
        n = len(sorted_vals)
        mid = n // 2
        median = (
            (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
            if n % 2 == 0
            else sorted_vals[mid]
        )
        out[key] = median
    return out


async def _load_per_deal_portfolio_pnl_overrides(
    session: AsyncSession,
    *,
    deal_id: str,
    tenant_id: str,
) -> dict[str, float]:
    """Translate per-deal PORTFOLIO_PNL extractions into engine overrides.

    Per-deal docs always outrank the library median for the same chain
    scale (analyst intent on this specific deal beats firm-wide
    benchmarks). The loader merges every PORTFOLIO_PNL extraction for
    the deal onto a single ``{ratio: value}`` dict — when multiple docs
    cover the same ratio we keep the first non-None value (most-recent
    first via ``ORDER BY created_at DESC``).
    """
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return {}
    try:
        rows = await session.execute(
            text(
                # tenant-scope predicate required by tenant_middleware
                """
                SELECT er.fields
                  FROM extraction_results er
                  JOIN documents d ON d.id = er.document_id
                 WHERE er.deal_id = :deal
                   AND er.tenant_id = :tenant
                   AND d.tenant_id = :tenant
                   AND UPPER(COALESCE(d.doc_type, '')) = 'PORTFOLIO_PNL'
                 ORDER BY er.created_at DESC
                """
            ),
            {"deal": deal_id, "tenant": tenant_id},
        )
    except Exception:
        return {}

    out: dict[str, float] = {}
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
            canonical = _PORTFOLIO_PNL_FIELD_MAP.get(name)
            if canonical is None:
                continue
            v = float(value)
            if v > 1.0 and v <= 100.0:
                v = v / 100.0
            if not (0.0 < v < 1.5):
                continue
            out.setdefault(canonical, v)
    return out


async def _load_str_forecast_for_seed(
    session: AsyncSession, *, deal_id: str, tenant_id: str
) -> tuple[float, float] | None:
    """Seed (starting_occupancy, starting_adr) from the BASE STR forecast.

    Wave 3 W3.3 — when the analyst opts in
    (``base["revenue_seed_from_str_forecast"] is True``), the loader
    pulls the deal's STR Trend monthly history, runs the forecast
    engine with default scenarios, and returns the BASE scenario's
    Month-12 ``(occupancy, ADR)`` so the revenue engine can ground Y1
    on the forecast rather than the T-12 / Kimpton seed.

    Returns ``None`` when the deal has no STR_TREND extraction, when
    coverage is ``"low"`` (< 12 historical months → forecast disabled),
    or when the base forecast list is empty for any reason. Caller
    leaves the prior ``starting_occupancy`` / ``starting_adr`` (and
    their source badges) untouched in those cases.
    """
    try:
        UUID(deal_id)
    except (TypeError, ValueError):
        return None

    from ..engines.str_forecast import build_str_forecast
    from .str_forecast_loader import load_str_history_for_deal

    # tenant-scope predicate required by tenant_middleware — pass through
    history = await load_str_history_for_deal(
        session, deal_id=deal_id, tenant_id=tenant_id
    )
    if not history:
        return None
    forecast = build_str_forecast(deal_id=deal_id, historical_months=history)
    if forecast.coverage_quality == "low":
        return None
    base_months = forecast.forecast_months.get("base") or []
    if len(base_months) < 12:
        return None
    month12 = base_months[11]
    return (month12.occupancy, month12.adr)


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
        # Wave 2 P2.1 — institutional revenue segmentation. The loader
        # populates ``base["segments"]`` with a list[dict] when the
        # deal has an STR_SEGMENTATION extraction; otherwise it's
        # empty and the engine runs the legacy single-line path.
        seg_dicts = base.get("segments") or []
        # Analyst overrides that landed AFTER the seed (rare — the
        # loader already merged them when building the seed) get
        # re-applied here so a late-arriving override on a deal that
        # didn't have an STR Segmentation report at seed time still
        # surfaces. When no seed exists and only an override is
        # present we can't synthesize a partial segment list — the
        # validator would reject mix_pct < 1.0 — so a standalone
        # override without an STR extraction is a no-op (analyst
        # needs to upload the STR report first).
        post_seed_overrides = base.get("segments_overrides") or {}
        if seg_dicts and post_seed_overrides:
            for s in seg_dicts:
                seg_overrides = post_seed_overrides.get(s["name"])
                if not seg_overrides:
                    continue
                for field, value in seg_overrides.items():
                    if field in _SEGMENT_FIELDS:
                        s[field] = value
        segments = [RevenueSegment(**s) for s in seg_dicts]
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
            # Y1 renovation/PIP displacement (Eshan v2 QA). Defaults
            # come from ``_load_engine_inputs`` which sets non-zero
            # values when the capital engine carries a renovation
            # budget. Pass 0 explicitly when there's no PIP so the
            # engine behaves identically to pre-displacement code.
            y1_occupancy_displacement_pct=base.get(
                "y1_occupancy_displacement_pct", 0.0
            ),
            y1_adr_displacement_pct=base.get(
                "y1_adr_displacement_pct", 0.0
            ),
            segments=segments,
            pip_displacement=_build_pip_displacement(base),
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
    tenant_id: str,
    output: BaseModel,
    inputs: BaseModel | dict[str, Any] | None,
    runtime_ms: int,
) -> None:
    await session.execute(
        # tenant_id predicate keeps tenant_middleware / Sentry quiet — see
        # apps/worker/app/tenant_middleware.py.
        text(
            """
            UPDATE engine_outputs
               SET status = 'complete',
                   inputs = :inputs,
                   outputs = :outputs,
                   completed_at = :ts,
                   runtime_ms = :runtime_ms
             WHERE id = :id
               AND tenant_id = :tenant
            """
        ),
        {
            "id": row_id,
            "tenant": str(tenant_id),
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
    tenant_id: str,
    error: str,
) -> None:
    await session.execute(
        # tenant_id predicate keeps tenant_middleware / Sentry quiet — see
        # apps/worker/app/tenant_middleware.py.
        text(
            """
            UPDATE engine_outputs
               SET status = 'failed',
                   error = :error,
                   completed_at = :ts
             WHERE id = :id
               AND tenant_id = :tenant
            """
        ),
        {"id": row_id, "tenant": str(tenant_id), "error": error, "ts": _now()},
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
    scenario_id: str | None = None,
) -> dict[str, Any]:
    """Run a single engine and persist the output.

    Returns a serializable dict suitable for the API response.

    Wave 3 W3.2: ``scenario_id`` (when set) layers a named scenario's
    overrides on top of the deal's persisted ``field_overrides`` before
    the engine input is built. Without ``scenario_id`` the path is
    unchanged.
    """
    if engine_name not in ENGINE_REGISTRY:
        raise ValueError(
            f"unknown engine {engine_name!r}; "
            f"expected one of {sorted(ENGINE_REGISTRY)}"
        )

    run_id = run_id or str(uuid4())
    accumulated = accumulated if accumulated is not None else {}
    base_inputs = base_inputs or await _load_engine_inputs(
        session, deal_id, overrides, scenario_id=scenario_id,
        tenant_id=tenant_id,
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
                scenario_id=scenario_id,
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
        try:
            from ..alerting import report_alert

            report_alert(
                severity="error",
                title=f"Engine run failure: {engine_name}",
                deal_id=str(deal_id),
                tenant_id=tenant_id,
                stage=f"engine.{engine_name}",
                exc=exc,
                extra={"runtime_ms": runtime_ms, "run_id": run_id},
            )
        except Exception:
            pass
        await _persist_failed(
            session, row_id=row_id, tenant_id=tenant_id, error=str(exc)
        )
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
        tenant_id=tenant_id,
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
    scenario_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Run the full 8-engine chain; persist each output as it lands.

    Returns a dict keyed by engine name with the per-engine result
    dicts (matching ``run_single_engine``'s return shape).

    Failure policy: when an engine fails we mark its row failed AND
    skip downstream engines that need its output (their rows land with
    ``status='failed'`` and a ``skipped: <upstream>`` error). Engines
    independent of the failure keep running so the user sees partial
    progress instead of a blank page.

    Wave 3 W3.2: ``scenario_id`` (when set) layers the named scenario's
    overrides on top of the deal's persisted ``field_overrides`` so the
    same run_id chain reflects the scenario-specific assumptions. The
    base scenario carries an empty override list — a run against it is
    byte-identical to a run without ``scenario_id``.
    """
    base_inputs = await _load_engine_inputs(
        session, deal_id, overrides, scenario_id=scenario_id,
        tenant_id=tenant_id,
    )
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
            await _persist_failed(
                session, row_id=row_id, tenant_id=tenant_id, error=err
            )
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
            scenario_id=scenario_id,
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
    tenant_id: str,
) -> dict[str, dict[str, Any]]:
    """Return the latest row per engine for ``deal_id``.

    Result is keyed by engine name; engines with no rows are omitted.
    """
    rows = await session.execute(
        text(
            # tenant-scope predicate required by tenant_middleware
            """
            SELECT id, deal_id, tenant_id, run_id, engine_name,
                   status, inputs, outputs, error,
                   started_at, completed_at, runtime_ms
              FROM engine_outputs
             WHERE deal_id = :deal AND tenant_id = :tenant
             ORDER BY started_at DESC
            """
        ),
        {"deal": str(_coerce_uuid(deal_id)), "tenant": str(tenant_id)},
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
    tenant_id: str,
) -> list[dict[str, Any]]:
    """Return every engine row tagged with ``run_id`` for ``deal_id``."""
    rows = await session.execute(
        text(
            # tenant-scope predicate required by tenant_middleware
            """
            SELECT id, deal_id, tenant_id, run_id, engine_name,
                   status, inputs, outputs, error,
                   started_at, completed_at, runtime_ms
              FROM engine_outputs
             WHERE deal_id = :deal AND run_id = :run AND tenant_id = :tenant
             ORDER BY started_at ASC
            """
        ),
        {"deal": str(_coerce_uuid(deal_id)), "run": run_id, "tenant": str(tenant_id)},
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
