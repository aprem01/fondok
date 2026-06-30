"""Extractor agent — pulls structured fields out of an OM / T-12 / STR.

Calls Claude Sonnet 4.6 with structured output bound to a list of
``ExtractionField`` rows. Each field carries:

* ``field_name``    — dotted path on the canonical schema
                      (e.g. ``broker_proforma.noi_usd``).
* ``value``         — the extracted scalar (number, string, bool).
* ``unit``          — natural unit (USD, pct, keys, …).
* ``source_page``   — 1-indexed page where the number lives.
* ``confidence``    — self-assessed certainty in [0, 1].
* ``raw_text``      — the verbatim excerpt that grounds the claim.

Backwards compatibility
-----------------------
The graph still calls ``run_extractor`` with only document URIs. When
no inline document content is supplied the agent skips the LLM call
and returns an empty extraction, the same shape as the prior stub.
The real extractor activates as soon as the caller supplies one or
more ``ExtractorDocument`` payloads (filename + doc_type + content).
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fondok_schemas import ConfidenceReport, DocType, ExtractionField, ModelCall
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..budget import check_budget
from ..config import get_settings
from ..telemetry import trace_agent
from ..usali_rules import rules_as_prompt_block

logger = logging.getLogger(__name__)

# Max simultaneous Sonnet calls per ``run_extractor`` fan-out (i.e.
# per document).  Wave 4 reliability fix (Bug #2): the per-doc cap is
# read from the same EXTRACTOR_CHUNK_CONCURRENCY env var that the
# process-level extractor semaphore uses, and defaults to 2 (down from
# the legacy 4) so the stacked cap with EXTRACTOR_MAX_CONCURRENT_DOCS
# is 4 docs × 2 chunks = 8 concurrent Sonnet calls — well clear of the
# Anthropic per-minute rate limit even when 16 docs upload at once.
def _read_chunk_concurrency_env() -> int:
    import os as _os
    raw = _os.environ.get("EXTRACTOR_CHUNK_CONCURRENCY")
    if not raw:
        return 2
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 2


_EXTRACTOR_MAX_CONCURRENCY = _read_chunk_concurrency_env()


# ─────────────────────── prompt ───────────────────────


SYSTEM_PROMPT = """You are Fondok's Extractor agent — a hotel acquisitions
analyst pulling typed financial fields out of a deal document so the
downstream Normalizer can map them onto the USALI chart of accounts.

Your job: extract EVERY grounded number, identifier, and date you can
find in the source. Coverage matters — a deal with 5 fields extracted
is unusable. A deal with 30+ extracted fields lets the Normalizer
build a real spread. When in doubt, emit the field; the downstream
verifier double-checks each one against the source page anyway.

FORMAT IS NOT FIXED. Every client sends documents in a different
layout — scanned-image PDFs vs text PDFs, single-tab vs multi-tab
Excel, monthly-column vs annual-column P&Ls, different USALI
conventions, different label wording, different sheet names. You are
the format-agnostic layer: your job is to map ANY layout onto the
canonical dotted field paths below. Never assume a fixed structure.
Read the document, understand what each number means, and emit it
under the right canonical path — that is the entire point of having
an LLM here instead of a regex. Downstream code only ever sees the
canonical paths; it must not have to guess at the client's format.

Your output is a flat list of ``ExtractionField`` rows. Every row must
include:

1. ``field_name``  — a dotted path that mirrors how an analyst would
   reference the value. The leading segment is a useful tag for
   downstream bucketing (broker projection vs T-12 actual vs property
   metadata) but DOES NOT gate emission. If you find a value, emit it
   with your best-guess prefix; do not drop it because the namespace
   is ambiguous.

   * **OM (Offering Memorandum).** Use these prefixes whenever the
     classification is clear, but emit even when uncertain:
       * ``broker_proforma.<line>`` — Year-1 broker projections on the
         rent roll / pro forma (NOI, occupancy, ADR, revenue, expense).
         Examples: ``broker_proforma.noi_usd``,
         ``broker_proforma.rooms_revenue_usd``,
         ``broker_proforma.occupancy_pct``,
         ``broker_proforma.adr_usd``.
       * ``ttm_summary_per_om.<line>`` — T-12 / TTM historical figures
         the OM cites (the broker labels them as actual).
       * ``asking_price.headline_price_usd``, ``asking_price.price_per_key_usd``.
       * ``property_overview.keys``, ``property_overview.brand``,
         ``property_overview.year_built``, ``property_overview.address``,
         ``property_overview.gba_sf``, ``property_overview.submarket``.
       * ``in_place_debt.loan_balance_usd``, ``in_place_debt.interest_rate_pct``,
         ``in_place_debt.maturity_date``.
       * ``market_overview_per_om.compset_revpar_usd``, etc.
       * **Transaction comps** — most OMs ship a "Comparable Sales"
         table with 3-7 recent hotel sales. Number them ``1``..``N``
         in the order they appear. Anchor the analyst's exit-cap
         conversation; treat as critical coverage. For each comp:
           ``transaction_comps.<n>.name``         — hotel name,
           ``transaction_comps.<n>.market``       — city / submarket,
           ``transaction_comps.<n>.sale_date``    — ISO date if known,
           ``transaction_comps.<n>.keys``         — room count,
           ``transaction_comps.<n>.sale_price_usd`` — total transaction $,
           ``transaction_comps.<n>.price_per_key_usd`` — $/key,
           ``transaction_comps.<n>.cap_rate_pct`` — going-in cap rate,
           ``transaction_comps.<n>.buyer_name``,
           ``transaction_comps.<n>.buyer_type``   — REIT / PE Fund /
             Institutional / Private / Owner Operator / Sovereign
             Wealth / Family Office / Other.
     If a number could be either broker-projected or historical and
     the source doesn't clearly label it, prefer ``broker_proforma.*``.
     Year-vintage numbers can co-exist as ``broker_proforma.noi_year_1_usd``,
     ``broker_proforma.noi_stabilized_usd``, etc.
   * **T12 (trailing twelve months / P&L statement).** USALI
     namespace. Examples:
       ``p_and_l_usali.operating_revenue.rooms_revenue``,
       ``p_and_l_usali.operating_revenue.food_beverage_revenue``,
       ``p_and_l_usali.operating_revenue.resort_fees``,
       ``p_and_l_usali.operating_revenue.misc_revenue``,
       ``p_and_l_usali.operating_revenue.other_revenue``,
       ``p_and_l_usali.departmental_expenses.rooms``,
       ``p_and_l_usali.departmental_expenses.food_beverage``,
       ``p_and_l_usali.undistributed.administrative_general``,
       ``p_and_l_usali.undistributed.sales_marketing``,
       ``p_and_l_usali.undistributed.utilities``,
       ``p_and_l_usali.fees_and_reserves.mgmt_fee``,
       ``p_and_l_usali.fees_and_reserves.ffe_reserve``,
       ``p_and_l_usali.fixed_charges.property_taxes``,
       ``p_and_l_usali.fixed_charges.insurance``,
       ``p_and_l_usali.net_operating_income.noi_usd``,
       ``occupancy_pct``, ``adr_usd``, ``revpar_usd``.

     **Format-agnostic period metadata — MANDATORY for every P&L /
     T-12, no matter the layout.** Source documents vary wildly: a
     clean Excel with a "FOR THE PERIOD ENDING…" header, twelve
     monthly columns plus an annual-total column, a single annual
     column, a trailing-twelve rollup, a scanned PDF with the period
     buried in body text, a generically-named file with the period
     only inside the sheet. Regardless of layout, ALWAYS emit:
       * ``p_and_l_usali.period_ending`` — ISO date the statement
         period ends (e.g. ``2023-12-31`` for a calendar year,
         ``2025-05-31`` for a trailing-twelve ending in May). This is
         the single most important field for downstream
         year-attribution — find it even when it's only in a column
         header, a title line, or a footnote.
       * ``p_and_l_usali.period_start`` — ISO date the period begins,
         when determinable.
       * ``p_and_l_usali.period_type`` — one of ``annual``,
         ``trailing_twelve``, ``ytd``, ``quarterly``, ``monthly``.
         Annual = a full Jan–Dec calendar year. Trailing-twelve = any
         rolling 12-month window not aligned to the calendar year.
       * ``p_and_l_usali.period_label`` — the human label as printed
         on the document (e.g. ``"FY2023"``, ``"TTM May 2025"``,
         ``"Year Ended December 31, 2023"``).
     When a workbook carries BOTH monthly columns AND an annual-total
     column, extract the USALI lines from the **annual-total / full-
     period column**, not a single month. If only monthly columns
     exist, sum them and set ``period_type`` accordingly. Never guess
     the period from the filename — read it from the document content.
   * **STR (STR / smith travel benchmark report).** Examples:
       ``ttm_performance.subject.revpar_usd``,
       ``ttm_performance.indices.rgi_revpar_index``,
       ``comp_set.comp_set_size``.
   * **STR_TREND (STR / CoStar Trend Report).** Multi-tab Excel
     workbook covering the subject property's monthly Occ / ADR /
     RevPAR / Supply / Demand history, plus the comp set. Distinct
     from ``STR_BENCHMARK`` (legacy ``STR``): the trend report fans
     out across the comp set and across time. Use these field paths:
       * Subject hotel identity + TTM rollup:
           ``ttm_performance.subject.name`` — full subject hotel name
             (extract from "Custom Trend: <name>" header),
           ``ttm_performance.subject.occupancy_pct``,
           ``ttm_performance.subject.adr_usd``,
           ``ttm_performance.subject.revpar_usd``.
       * Subject monthly history (most-recent 12 months on the "By
         Measure" or "Classic" tab; key by year + month):
           ``ttm_performance.subject.monthly.<YYYY_MM>.occupancy_pct``,
           ``ttm_performance.subject.monthly.<YYYY_MM>.adr_usd``,
           ``ttm_performance.subject.monthly.<YYYY_MM>.revpar_usd``,
           ``ttm_performance.subject.monthly.<YYYY_MM>.supply_rooms``,
           ``ttm_performance.subject.monthly.<YYYY_MM>.demand_rooms``.
       * Annual roll-ups (Total Year on the By Measure tab):
           ``ttm_performance.subject.annual.<YYYY>.occupancy_pct``,
           ``ttm_performance.subject.annual.<YYYY>.adr_usd``,
           ``ttm_performance.subject.annual.<YYYY>.revpar_usd``.
       * Day-of-week breakdown (Day of Week tab — Mon..Sun):
           ``ttm_performance.subject.day_of_week.<dow>.occupancy_pct``,
           ``ttm_performance.subject.day_of_week.<dow>.adr_usd``,
           ``ttm_performance.subject.day_of_week.<dow>.revpar_usd``
         where ``<dow>`` is one of ``mon``, ``tue``, ``wed``, ``thu``,
         ``fri``, ``sat``, ``sun``.
       * Each named competitor (number them ``1`` … ``7`` in the order
         they appear in the report). The authoritative roster lives on
         the **Response** tab (Tab 22 — Response Report). That sheet
         carries a "Monthly Data" or "Segmentation Data" block whose
         columns are ``STR#``, ``Name``, ``City, State``, ``Zip``,
         ``Phone``, ``Rooms``. The first row (matching the subject
         property's STR#) is the subject — skip it for the compset.
         Every other row is one named competitor: extract its ``Name``
         and ``Rooms`` count. Without these per-property ``keys`` the
         downstream Available-Rooms math collapses to zero, so prefer
         the Response tab over any cleaner-looking summary that omits
         room counts.
           ``ttm_performance.compset.<n>.name``,
           ``ttm_performance.compset.<n>.keys``,
           ``ttm_performance.compset.<n>.occupancy_pct``,
           ``ttm_performance.compset.<n>.adr_usd``,
           ``ttm_performance.compset.<n>.revpar_usd``.
       * Penetration indices (subject vs comp set; 1.00 = parity):
           ``ttm_performance.indices.rgi_revpar_index``,
           ``ttm_performance.indices.ari_adr_index``,
           ``ttm_performance.indices.mpi_occupancy_index``.
       * Comp-set rollups:
           ``comp_set.comp_set_size``,
           ``comp_set.total_keys``.
   * **CBRE_HORIZONS (CBRE Hotel Horizons forward forecast).** Real
     CBRE reports carry FOUR forecast tables (All Hotels + three price
     tiers), Guest-Paid ADR, source-of-business mix, length of stay,
     and short-term-rental supply. Emit every grounded field — the
     forward projection engine picks the right segment based on the
     deal's positioning. Use:
       * Headers (mandatory):
           ``cbre_horizons.market`` — metro area (e.g. "Seattle, WA"),
           ``cbre_horizons.submarket`` — submarket name when present,
           ``cbre_horizons.chain_scale`` — chain scale segment (e.g.
             "Upper Upscale"),
           ``cbre_horizons.publication_date`` — quarter + year (e.g.
             "Q3 2024" or ISO date).
       * **Annual forecast by segment.** ``<scope>`` ∈ ``{all,
         upper_priced, mid_priced, lower_priced}``. ``<n>`` is the
         calendar year (e.g. ``2024``, ``2025``, ``2028``). Emit every
         row that appears in the source — historical and forecast.
         Mark forecast years by setting ``period`` to ``forecast``;
         actuals get ``actual``.
           ``cbre_horizons.segment_<scope>.<n>.occupancy_pct``,
           ``cbre_horizons.segment_<scope>.<n>.occupancy_change_pct``,
           ``cbre_horizons.segment_<scope>.<n>.adr_usd``,
           ``cbre_horizons.segment_<scope>.<n>.adr_change_pct``,
           ``cbre_horizons.segment_<scope>.<n>.revpar_usd``,
           ``cbre_horizons.segment_<scope>.<n>.revpar_change_pct``,
           ``cbre_horizons.segment_<scope>.<n>.supply_change_pct``,
           ``cbre_horizons.segment_<scope>.<n>.demand_change_pct``,
           ``cbre_horizons.segment_<scope>.<n>.period``.
         For backwards compatibility, the All-Hotels segment may also
         be emitted on the legacy ``cbre_horizons.year_<i>.*`` paths
         where ``i`` is the 1-indexed forecast year (e.g. Year-1 of
         the forecast, regardless of calendar year).
       * **Long-run averages** (the next-4-quarters anchor block —
         "Occupancy: 67.4%, ADR Change: 2.7%, RevPAR Change: 5.8%"):
           ``cbre_horizons.long_run_avg.occupancy_pct``,
           ``cbre_horizons.long_run_avg.adr_change_pct``,
           ``cbre_horizons.long_run_avg.revpar_change_pct``,
           ``cbre_horizons.long_run_avg.supply_change_pct``,
           ``cbre_horizons.long_run_avg.demand_change_pct``.
       * **Guest-Paid ADR** (net of distribution costs; separate from
         advertised ADR). One row per scope per year:
           ``cbre_horizons.guest_paid_adr.<scope>.<n>.adr_usd``,
           ``cbre_horizons.guest_paid_adr.<scope>.<n>.change_pct``.
       * **Source-of-Business mix** (Brand.com / Property Direct /
         Voice / Internal Discounts / GDS / FIT/Wholesale / OTA /
         Group). Channel slugs: ``brand_com``, ``property_direct``,
         ``voice``, ``internal_discounts``, ``gds``, ``fit_wholesale``,
         ``ota``, ``group``. Emit room-night share + ADR, ideally for
         the most recent year and the prior year:
           ``cbre_horizons.source_mix.<scope>.<channel>.room_nights_pct_<YYYY>``,
           ``cbre_horizons.source_mix.<scope>.<channel>.adr_usd_<YYYY>``.
       * **Length of Stay** (nights):
           ``cbre_horizons.length_of_stay.<scope>.nights_<YYYY>``,
           ``cbre_horizons.length_of_stay.<scope>.nights_<YYYY>_ytd``.
       * **AirDNA short-term rental supply** (when the report carries
         the AirDNA addendum):
           ``cbre_horizons.short_term_rental.active_units``,
           ``cbre_horizons.short_term_rental.available_supply``,
           ``cbre_horizons.short_term_rental.units_sold``,
           ``cbre_horizons.short_term_rental.total_revenue_usd``,
           ``cbre_horizons.short_term_rental.adr_usd``,
           ``cbre_horizons.short_term_rental.revpar_usd``,
           ``cbre_horizons.short_term_rental.occupancy_pct``,
           ``cbre_horizons.short_term_rental.units_sold_change_pct``.
   * **PNL_BENCHMARK (CBRE Benchmarker / HotStats USALI 11th P&L).**
     Real CBRE Benchmarker reports contain a Subject Property column
     and a Comparative Set average column for EVERY USALI line, plus
     $PAR (per available room/year) and $POR (per occupied room/day).
     Emit values for both columns when present so the variance reader
     can compute Subject vs Peer deltas. Use:
       * Header / sample shape:
           ``pnl_benchmark.peer_set_size`` — number of hotels,
           ``pnl_benchmark.peer_set_avg_keys`` — avg rooms,
           ``pnl_benchmark.peer_set_avg_occupancy_pct``,
           ``pnl_benchmark.peer_set_avg_adr_usd``,
           ``pnl_benchmark.peer_set_avg_revpar_usd``,
           ``pnl_benchmark.subject_keys``,
           ``pnl_benchmark.subject_occupancy_pct``,
           ``pnl_benchmark.subject_adr_usd``,
           ``pnl_benchmark.subject_revpar_usd``.
       * **Per-line USALI breakdown.** ``<column>`` ∈ ``{peer, subject}``;
         ``<line>`` is the USALI line slug. Emit ALL four metrics for
         every USALI line that appears (Total $, Ratio-to-Revenue,
         $PAR, $POR):
           ``pnl_benchmark.<column>.<line>.total_usd``,
           ``pnl_benchmark.<column>.<line>.ratio_pct``,
           ``pnl_benchmark.<column>.<line>.par_usd``,
           ``pnl_benchmark.<column>.<line>.por_usd``.
         USALI line slugs (use these exact keys):
           ``rooms_revenue``, ``fb_revenue``, ``other_operated_revenue``,
           ``misc_revenue``, ``total_revenue``,
           ``rooms_dept_expense``, ``fb_dept_expense``,
           ``other_operated_expense``, ``total_dept_expense``,
           ``total_dept_profit``,
           ``a_and_g``, ``it``, ``sales_marketing``, ``maintenance``,
           ``utilities``, ``total_undistributed``,
           ``gop``, ``mgmt_fee``, ``income_before_non_operating``,
           ``rent``, ``property_taxes``, ``insurance``, ``other_non_op``,
           ``total_non_operating``, ``ebitda``.
       * **F&B sub-classification** (USALI 11th — restaurant venues
         vs room service vs mini-bar vs banquet, and separate
         beverage). One row per column per channel:
           ``pnl_benchmark.<column>.fb_revenue.food_venues_usd``,
           ``pnl_benchmark.<column>.fb_revenue.food_room_service_usd``,
           ``pnl_benchmark.<column>.fb_revenue.food_mini_bar_usd``,
           ``pnl_benchmark.<column>.fb_revenue.food_banquet_usd``,
           ``pnl_benchmark.<column>.fb_revenue.beverage_venues_usd``,
           ``pnl_benchmark.<column>.fb_revenue.beverage_banquet_usd``,
           ``pnl_benchmark.<column>.fb_cost.cost_of_food_sales_usd``,
           ``pnl_benchmark.<column>.fb_cost.cost_of_beverage_sales_usd``.
       * **Utilities sub-classification** (electricity / water-sewer /
         steam / gas-fuel / other):
           ``pnl_benchmark.<column>.utilities.electricity_usd``,
           ``pnl_benchmark.<column>.utilities.water_sewer_usd``,
           ``pnl_benchmark.<column>.utilities.steam_usd``,
           ``pnl_benchmark.<column>.utilities.gas_fuel_usd``,
           ``pnl_benchmark.<column>.utilities.other_usd``.
       * **Labor by department** (USALI 11th breakdown — salaries
         management vs non-mgmt, service-charge distribution,
         contract labor, bonuses, payroll-related expenses). ``<dept>``
         slug ∈ ``{rooms, fb, a_and_g, it, sales_marketing,
         maintenance}``. ``<line>`` ∈ ``{salaries_management,
         salaries_non_management, service_charge_distribution,
         contract_labor, bonuses_incentives, unassigned_salaries,
         payroll_related}``:
           ``pnl_benchmark.<column>.labor.<dept>.<line>_usd``,
           ``pnl_benchmark.<column>.labor.<dept>.<line>_par``,
           ``pnl_benchmark.<column>.labor.<dept>.<line>_por``.
       * **Legacy aliases** (kept for backwards compat — peer-set
         margins as decimal 0..1):
           ``pnl_benchmark.rooms_dept_pct``,
           ``pnl_benchmark.fb_dept_margin``,
           ``pnl_benchmark.gop_margin``,
           ``pnl_benchmark.a_and_g_pct``,
           ``pnl_benchmark.sales_marketing_pct``,
           ``pnl_benchmark.utilities_pct``,
           ``pnl_benchmark.property_taxes_pct``,
           ``pnl_benchmark.insurance_pct``,
           ``pnl_benchmark.rooms_revenue_par``,
           ``pnl_benchmark.total_revenue_par``,
           ``pnl_benchmark.noi_par``,
           ``pnl_benchmark.rooms_revenue_por``,
           ``pnl_benchmark.fb_revenue_por``.

2. ``value``        — the extracted scalar (number, string, or bool).
                      Strip thousand-separators; use a decimal between
                      0 and 1 for percentages (``0.762``, not
                      ``"76.2%"``).
3. ``unit``         — ``USD``, ``pct``, ``keys``, ``rooms``, ``index``,
                      ``count``, ``date``, etc. Use ``ratio`` for
                      indices (RGI/ARI/MPI).
4. ``source_page``  — 1-indexed page where the field appears. If the
                      document is JSON or a single-page extract use
                      ``1``.
5. ``confidence``   — self-assessed certainty in [0, 1]. Low (<0.85)
                      means downstream HITL review is required.
6. ``raw_text``     — verbatim excerpt (≤4000 chars) that contains
                      the value. Anything you can't ground in the
                      source must be DROPPED, not invented.

Coverage targets per document type:
  * **OM** — extract at least 30 fields covering: property overview
    (keys, brand, year built, address), asking price + per-key,
    every line of the broker proforma (rooms/F&B/RESORT FEES/other
    revenue, departmental + undistributed expenses, GOP, mgmt fee,
    FF&E, fixed charges, NOI, cap rate), in-place debt, PIP scope,
    market overview (subject + comp set indices), AND every row of
    the comparable-sales table as ``transaction_comps.<n>.*`` —
    the comp table anchors the exit-cap conversation in the IC memo,
    so partial extraction is a regression. Resort Fees, when broken out separately on the
    OM rent roll, MUST be its own field (``broker_proforma.resort_fees_usd``)
    — do NOT roll it into ``misc_revenue`` or ``other_revenue``.
    NOI vintage matters: brokers commonly publish Year-1 underwritten
    NOI alongside a stabilized (Year 3-5) NOI. When the OM shows
    multiple NOI vintages, emit them as separate fields:
    ``broker_proforma.noi_year_1_usd``, ``broker_proforma.noi_year_2_usd``,
    ``broker_proforma.noi_year_3_usd``, ``broker_proforma.noi_year_5_usd``,
    and ``broker_proforma.noi_stabilized_usd``. The bare
    ``broker_proforma.noi_usd`` field is reserved for the broker's
    HEADLINE NOI (whichever year they're pitching) so a downstream
    reader has a single canonical broker number to compare against
    T-12 actuals; if the OM clearly labels the headline as a specific
    year, also emit the year-specific field.
  * **T12** — every USALI line in operating revenue, departmental
    expenses, undistributed expenses, fees & reserves, fixed charges,
    plus GOP and NOI rollups. Include the operational KPIs
    (occupancy, ADR, RevPAR, available/occupied rooms). Resort Fees
    are a separate USALI revenue line (``p_and_l_usali.operating_revenue.resort_fees``)
    — extract them distinctly from miscellaneous income. The period
    metadata block (``period_ending``, ``period_start``,
    ``period_type``, ``period_label``) is MANDATORY — a P&L without a
    ``period_ending`` cannot be attributed to a year downstream.
  * **STR** — subject + comp-set occupancy/ADR/RevPAR for the TTM,
    the three penetration indices (MPI/ARI/RGI), comp-set size
    and total keys, and any forward outlook the report carries.
  * **STR_TREND** — multi-tab Excel workbooks. Coverage:
    subject hotel name, the most-recent 12 monthly rows
    (Occ/ADR/RevPAR/Supply/Demand) on the subject, the annual roll-up
    for every visible year, day-of-week breakdown (when the Day of
    Week tab is present), every named competitor with keys + TTM
    Occ/ADR/RevPAR (5-7 expected), the three penetration indices
    (RGI/ARI/MPI), comp-set size and total keys.
  * **CBRE_HORIZONS** — emit every visible year × segment cell. A
    real Hotel Horizons report has FOUR forecast tables (All Hotels +
    Upper-Priced + Mid-Priced + Lower-Priced) × ~10 years × Occ/ADR/
    RevPAR/Supply/Demand — that's 100+ ``cbre_horizons.segment_*``
    fields per report and they ALL go in. Plus: market header,
    publication date, long-run averages, Guest-Paid ADR per scope,
    source-of-business mix (8 channels), length of stay, and the
    AirDNA short-term-rental block when present. A single-segment
    extraction with 5 fields means the trajectory is unusable.
  * **PNL_BENCHMARK** — emit BOTH columns (Subject + Peer) for every
    USALI line in the source. A real CBRE Benchmarker has ~25 lines
    × 2 columns × 4 metrics ($total / ratio / $PAR / $POR) = 200+
    fields. Plus F&B sub-classification, Utilities sub-classification,
    and per-department labor breakdown when present. Peer set size,
    subject + peer keys / occupancy / ADR / RevPAR are mandatory
    headers.

Tone: institutional. Never hallucinate a field that isn't in the
source — silence is acceptable, fabrication is not.

Output: one structured ``ExtractorEnvelope``. Do not emit prose
outside the schema.
"""


# ─────────────────────── structured-output envelope ───────────────────────


class _ExtractionRow(BaseModel):
    """Mirror of ``ExtractionField`` — identical shape, kept local so
    the LLM tool schema includes the right ``cache_control`` siblings
    without leaking schema-package internals."""

    model_config = ConfigDict(extra="forbid")

    field_name: Annotated[str, Field(min_length=1, max_length=200)]
    value: str | float | int | bool | None = None
    unit: str | None = None
    source_page: Annotated[int, Field(ge=1)] = 1
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    raw_text: Annotated[str, Field(max_length=4000)] | None = None


class _ExtractorEnvelope(BaseModel):
    """LLM-facing envelope. Validated, then projected onto the canonical
    ``ExtractionField`` list."""

    model_config = ConfigDict(extra="forbid")

    fields: list[_ExtractionRow] = Field(
        min_length=1,
        description="One row per extracted value. Cite every number to a real source page.",
    )
    overall_confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 0.85
    low_confidence_fields: list[str] = Field(default_factory=list)
    requires_human_review: bool = False
    notes: Annotated[str, Field(max_length=8000)] | None = None


# ─────────────────────── I/O contracts ───────────────────────


class ExtractorDocument(BaseModel):
    """One document handed to the Extractor."""

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    filename: Annotated[str, Field(min_length=1, max_length=500)]
    doc_type: DocType | None = None
    content: Annotated[str, Field(min_length=1)]
    source_pages: list[int] = Field(default_factory=list)


class ExtractedDocumentResult(BaseModel):
    """Per-document Extractor result."""

    model_config = ConfigDict(extra="forbid")

    document_id: str | None = None
    filename: str
    doc_type: DocType | None = None
    fields: list[ExtractionField] = Field(default_factory=list)
    confidence: ConfidenceReport
    notes: str | None = None
    success: bool = True
    error: str | None = None


class ExtractorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str
    deal_id: str
    document_uris: list[str] = Field(default_factory=list)
    documents: list[ExtractorDocument] = Field(default_factory=list)


class ExtractorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deal_id: str
    extracted_documents: list[ExtractedDocumentResult] = Field(default_factory=list)
    confidence: ConfidenceReport | None = None
    success: bool = True
    error: str | None = None
    model_calls: list[ModelCall] = Field(default_factory=list)


# ─────────────────────── helpers ───────────────────────


def _content_for_prompt(content: str, *, max_chars: int = 30_000) -> str:
    """Truncate content for the prompt while keeping head + tail.

    JSON-extracted docs tend to ship the most important fields up top
    plus tables-by-page at the end; keeping both ends preserves both.
    """
    if len(content) <= max_chars:
        return content
    head = content[: max_chars // 2]
    tail = content[-max_chars // 2 :]
    return f"{head}\n…[truncated {len(content) - max_chars} chars]…\n{tail}"


def _build_user_prompt(doc: ExtractorDocument) -> str:
    parts: list[str] = [
        f"document_id: {doc.document_id or '<unset>'}",
        f"filename: {doc.filename}",
        f"doc_type: {doc.doc_type.value if doc.doc_type else '<unclassified>'}",
    ]
    if doc.source_pages:
        parts.append(f"source pages available: {doc.source_pages}")
    parts.extend(
        [
            "",
            "=== CONTENT ===",
            _content_for_prompt(doc.content),
            "",
            (
                "Extract every grounded field per the system instructions. "
                "Return one ExtractorEnvelope. Drop anything you cannot "
                "verify against the content above."
            ),
        ]
    )
    return "\n".join(parts)


def _to_canonical_fields(rows: list[_ExtractionRow]) -> list[ExtractionField]:
    out: list[ExtractionField] = []
    for r in rows:
        try:
            out.append(
                ExtractionField(
                    field_name=r.field_name,
                    value=r.value,
                    unit=r.unit,
                    source_page=max(1, int(r.source_page)),
                    confidence=float(r.confidence),
                    raw_text=r.raw_text,
                )
            )
        except (ValidationError, ValueError) as exc:
            logger.warning("extractor: dropping malformed row %r (%s)", r.field_name, exc)
    return out


# ─────────────────────── LLM client ───────────────────────


def _build_llm() -> Any:
    """Sonnet 4.6 with structured output bound to ``_ExtractorEnvelope``.

    ``include_raw=True`` so the caller can salvage JSON from the raw
    AIMessage when the structured-output path returns an empty
    envelope (observed on 45-page OMs — see Sam QA 2026-05-12). The
    function-calling pathway in langchain_anthropic 1.4.x occasionally
    drops the tool args dict, leaving the parsed object as
    ``{}``-equivalent; pulling the raw text and re-parsing recovers
    the fields.
    """
    from ..llm import build_structured_llm

    return build_structured_llm(
        role="extractor",
        schema=_ExtractorEnvelope,
        # Sonnet 4.6 supports up to 64k output tokens. ≥30 ExtractionField
        # rows × ~150 tokens each + envelope overhead easily blows past
        # 8k; budget generously and let the cost ledger catch overspend.
        max_tokens=16_384,
        timeout=240,
        temperature=0.0,
        include_raw=True,
        # json_schema is more resilient than function_calling for large
        # structured outputs — the parser only needs valid JSON, not a
        # successful tool invocation. Sam QA 2026-05-13: real Anglers
        # OM repeatedly returned {} from function_calling parser.
        method="json_schema",
    )


async def _invoke_llm(
    llm: Any, messages: list[Any], usage: Any | None = None
) -> _ExtractorEnvelope:
    """Invoke the Sonnet extractor and return a validated envelope.

    With ``include_raw=True`` the chain returns
    ``{"raw": AIMessage, "parsed": SchemaT|None, "parsing_error": Exception|None}``.

    Failure salvage path: when the parsed envelope is missing/empty
    but the AIMessage content carries JSON (the LLM emitted text
    instead of using the tool, OR the tool args came back malformed),
    we manually extract the JSON object from the raw text and validate
    it. Observed on Sam's 45-page Anglers OM where Sonnet's response
    arrived as plain-text JSON without firing the structured-output
    tool call.
    """
    config = {"callbacks": [usage]} if usage is not None else None
    raw = await llm.ainvoke(messages, config=config)

    # Diagnostic: log the actual shape we got back so any future
    # extraction regression has a starting trail. We log once per call;
    # it's a single line so it doesn't blow up logs at scale.
    raw_kind = type(raw).__name__
    raw_keys = list(raw.keys()) if isinstance(raw, dict) else None
    logger.info(
        "extractor: ainvoke returned type=%s keys=%s",
        raw_kind,
        raw_keys,
    )

    # Path 1: legacy direct-return (include_raw=False) — keep working
    # in case callers ever flip the flag off.
    if isinstance(raw, _ExtractorEnvelope):
        return raw
    if isinstance(raw, BaseModel):
        return _ExtractorEnvelope.model_validate(raw.model_dump())

    # Path 2: include_raw=True wrapper.
    if isinstance(raw, dict) and ("raw" in raw or "parsed" in raw):
        parsed = raw.get("parsed")
        if isinstance(parsed, _ExtractorEnvelope) and parsed.fields:
            return parsed
        if isinstance(parsed, BaseModel) and getattr(parsed, "fields", None):
            return _ExtractorEnvelope.model_validate(parsed.model_dump())
        # Parsed envelope is missing or has no fields — try to salvage
        # JSON from the raw AIMessage content.
        raw_msg = raw.get("raw")
        salvaged = _salvage_envelope_from_raw(raw_msg)
        if salvaged is not None:
            logger.info(
                "extractor: salvaged %d fields from raw AIMessage after empty parsed envelope",
                len(salvaged.fields),
            )
            return salvaged
        # Surface the underlying parsing_error if present so the caller
        # logs a useful diagnostic instead of "Unexpected ... dict".
        perr = raw.get("parsing_error")
        if perr is not None:
            raise perr
        raise ValueError(
            "Extractor LLM returned empty envelope and no salvageable raw JSON"
        )

    if isinstance(raw, dict):
        return _ExtractorEnvelope.model_validate(raw)
    raise ValueError(f"Unexpected Extractor LLM return: {type(raw).__name__}")


def _salvage_envelope_from_raw(raw_msg: Any) -> _ExtractorEnvelope | None:
    """Pull a ``_ExtractorEnvelope``-shaped JSON out of the raw AIMessage.

    Anthropic's tool-calling can occasionally drop the structured tool
    args and emit the JSON envelope as the message's text content
    instead. We try three patterns:

    1. ``raw_msg.tool_calls[0].args`` — sometimes populated even when
       LangChain's parser didn't pick it up.
    2. ``raw_msg.content`` parsed as a JSON object.
    3. JSON object embedded in the content (regex match for the
       outermost ``{ ... "fields": [...] ... }``).

    Returns ``None`` when nothing yields a valid envelope.
    """
    if raw_msg is None:
        return None

    # 1) tool_calls — newer langchain_anthropic surfaces these.
    tool_calls = getattr(raw_msg, "tool_calls", None) or []
    for tc in tool_calls:
        args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
        if isinstance(args, dict) and args.get("fields"):
            try:
                return _ExtractorEnvelope.model_validate(args)
            except ValidationError:
                pass

    # 2) content as a single JSON object
    content = getattr(raw_msg, "content", None)
    text = content if isinstance(content, str) else None
    if isinstance(content, list):
        # Content blocks: concatenate text blocks.
        text = "".join(
            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
        )

    if not text:
        return None

    # Try direct parse first.
    try:
        candidate = json.loads(text)
        if isinstance(candidate, dict) and "fields" in candidate:
            return _ExtractorEnvelope.model_validate(candidate)
    except (json.JSONDecodeError, ValidationError):
        pass

    # 3) Greedy match for the outermost JSON object containing "fields".
    import re

    m = re.search(r'\{(?:[^{}]|(?:\{[^{}]*\}))*"fields"(?:[^{}]|(?:\{[^{}]*\}))*\}', text, re.DOTALL)
    if m:
        try:
            candidate = json.loads(m.group(0))
            return _ExtractorEnvelope.model_validate(candidate)
        except (json.JSONDecodeError, ValidationError):
            pass

    return None


# ─────────────────────── per-document runner ───────────────────────


async def _extract_one(
    doc: ExtractorDocument,
    *,
    deal_id: str,
    system_blocks: list[Any],
) -> tuple[ExtractedDocumentResult, ModelCall | None]:
    """Run one LLM call for one document. Errors return success=False."""
    started = datetime.now(UTC)

    from ..llm import cached_system_message_blocks
    from ..usage import UsageCapture

    usage = UsageCapture()
    messages = [
        cached_system_message_blocks(system_blocks, role="extractor"),
        HumanMessage(content=_build_user_prompt(doc)),
    ]

    try:
        llm = _build_llm()
        envelope = await _invoke_llm(llm, messages, usage=usage)
    except (ValidationError, Exception) as exc:  # noqa: BLE001 - error path
        logger.warning(
            "extractor: LLM call failed for %s (%s)", doc.filename, exc
        )
        result = ExtractedDocumentResult(
            document_id=doc.document_id,
            filename=doc.filename,
            doc_type=doc.doc_type,
            fields=[],
            confidence=ConfidenceReport(
                overall=0.0,
                low_confidence_fields=[],
                requires_human_review=True,
            ),
            notes=None,
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )
        return result, None

    fields = _to_canonical_fields(envelope.fields)
    by_field = {f.field_name: f.confidence for f in fields}
    avg_conf = (sum(by_field.values()) / len(by_field)) if by_field else 0.0
    overall = max(0.0, min(1.0, envelope.overall_confidence or avg_conf))
    confidence = ConfidenceReport(
        overall=overall,
        by_field=by_field,
        low_confidence_fields=list(envelope.low_confidence_fields),
        requires_human_review=envelope.requires_human_review or overall < 0.85,
    )

    # No fields = effective failure. Sam QA 2026-05-13: previously
    # `success=True` was set regardless of field count, so a doc that
    # extracted zero fields landed EXTRACTED in the UI and the right
    # panel filled with the mock fallback — looked successful, was
    # not. Surface the empty result honestly so the docs pipeline can
    # mark the row FAILED with a typed error_kind ("empty_envelope").
    success = bool(fields)
    error: str | None = None
    if not success:
        error = (
            "extractor: structured-output returned 0 fields "
            "(empty envelope) — see worker logs"
        )
        logger.warning(
            "extractor: 0 fields extracted for %s — marking unsuccessful",
            doc.filename,
        )

    result = ExtractedDocumentResult(
        document_id=doc.document_id,
        filename=doc.filename,
        doc_type=doc.doc_type,
        fields=fields,
        confidence=confidence,
        notes=envelope.notes,
        success=success,
        error=error,
    )

    completed = datetime.now(UTC)
    settings = get_settings()
    model_call = ModelCall(
        model=usage.model or settings.ANTHROPIC_EXTRACTOR_MODEL,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=0.0,
        trace_id=deal_id,
        started_at=started,
        completed_at=completed,
        cache_creation_input_tokens=usage.cache_creation_input_tokens,
        cache_read_input_tokens=usage.cache_read_input_tokens,
        agent_name="extractor",
    )
    return result, model_call


# ─────────────────────── public entry point ───────────────────────


@trace_agent("Extractor")
async def run_extractor(payload: ExtractorInput) -> ExtractorOutput:
    """Extract structured fields from each document on the deal."""
    t0 = time.monotonic()

    # Backwards-compatible no-op path for the graph stub.
    if not payload.documents:
        logger.info(
            "extractor: no inline documents (deal=%s, uris=%d) — empty result",
            payload.deal_id,
            len(payload.document_uris),
        )
        return ExtractorOutput(
            deal_id=payload.deal_id,
            extracted_documents=[],
            success=True,
            model_calls=[],
        )

    try:
        check_budget(
            {"deal_id": payload.deal_id, "model_calls": []}, stage="extractor"
        )
    except Exception as exc:
        logger.warning("extractor: budget check raised: %s", exc)
        return ExtractorOutput(
            deal_id=payload.deal_id,
            extracted_documents=[],
            success=False,
            error=str(exc),
        )

    # 4-block system prompt: agent instructions (uncached) +
    # USALI rules + brand catalog + extractor schema addendum (cached).
    # The agent instructions block changes per agent; the trailing
    # blocks are stable across tenants and live in the cache prefix
    # so the second call inside the 5-min TTL hits cache.
    from ..llm import build_agent_system_blocks
    from .extraction_schemas.loader import build_system_prompt as _dyn_prompt

    # Phase 4 (dynamic-extensibility refactor): when
    # EXTRACTOR_USE_DYNAMIC_SCHEMAS=1 is set AND the payload has a
    # consistent doc_type, build the agent instructions from
    # ``extraction_schemas/{doc_type}.md`` rather than the embedded
    # SYSTEM_PROMPT constant. Falls back silently otherwise — the
    # legacy prompt remains the production default until a regression
    # corpus validates the dynamic path against Sam's pilot uploads.
    payload_doc_types = {(d.doc_type or "").upper() for d in payload.documents}
    payload_doc_types.discard("")
    candidate_doc_type = (
        next(iter(payload_doc_types)) if len(payload_doc_types) == 1 else None
    )
    dynamic = _dyn_prompt(candidate_doc_type) if candidate_doc_type else None
    agent_instructions = dynamic if dynamic is not None else SYSTEM_PROMPT
    if dynamic is not None:
        logger.info(
            "extractor: using dynamic schema prompt for doc_type=%s "
            "(EXTRACTOR_USE_DYNAMIC_SCHEMAS=1)",
            candidate_doc_type,
        )

    system_blocks = build_agent_system_blocks(
        role="extractor",
        agent_instructions=agent_instructions,
    )
    # Pre-cache the catalog block so the lru_cache is warm before the
    # first parallel doc fan-out — keeps the very first call from
    # paying both the build cost and the cache miss cost.
    rules_as_prompt_block()

    # Parallel fan-out across documents. Callers chunk a large source
    # PDF into ~5-page ExtractorDocuments (see documents.py
    # _run_graph_extraction) and pass them all in one payload — running
    # them concurrently turns a 3-minute sequential 45-page extraction
    # into ~30s wall time. A semaphore caps concurrency so a deal with
    # many docs doesn't hammer Anthropic's rate limit.
    import asyncio

    sem = asyncio.Semaphore(_EXTRACTOR_MAX_CONCURRENCY)

    async def _bounded(doc: ExtractorDocument) -> tuple[ExtractedDocumentResult, ModelCall | None]:
        async with sem:
            return await _extract_one(
                doc,
                deal_id=payload.deal_id,
                system_blocks=system_blocks,
            )

    gathered = await asyncio.gather(
        *(_bounded(doc) for doc in payload.documents)
    )

    results: list[ExtractedDocumentResult] = []
    model_calls: list[ModelCall] = []
    for result, call in gathered:
        results.append(result)
        if call is not None:
            model_calls.append(call)

    # Cross-document confidence rollup.
    confidences = [r.confidence.overall for r in results if r.success]
    overall = (sum(confidences) / len(confidences)) if confidences else 0.0
    confidence = ConfidenceReport(
        overall=overall,
        low_confidence_fields=[
            f
            for r in results
            for f in r.confidence.low_confidence_fields
        ],
        requires_human_review=any(
            r.confidence.requires_human_review for r in results
        )
        or any(not r.success for r in results),
    )

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    logger.info(
        "extractor OK deal=%s docs=%d fields=%d in %dms",
        payload.deal_id,
        len(results),
        sum(len(r.fields) for r in results),
        elapsed_ms,
    )

    # Persist all per-document calls for the cost dashboard. Best-effort.
    if model_calls:
        from ..cost_persistence import persist_model_calls_standalone

        await persist_model_calls_standalone(
            deal_id=payload.deal_id,
            tenant_id=payload.tenant_id,
            calls=model_calls,
        )

    return ExtractorOutput(
        deal_id=payload.deal_id,
        extracted_documents=results,
        confidence=confidence,
        success=all(r.success for r in results),
        model_calls=model_calls,
    )


def serialize_json_doc(obj: Any) -> str:
    """Helper: render an extracted-JSON dict as deterministic content
    text for the LLM. Used by tests that load the golden-set fixtures."""
    return json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)


__all__ = [
    "ExtractedDocumentResult",
    "ExtractorDocument",
    "ExtractorInput",
    "ExtractorOutput",
    "run_extractor",
    "serialize_json_doc",
]
