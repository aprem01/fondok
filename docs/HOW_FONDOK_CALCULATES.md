# How Fondok calculates — methodology overview

This is the analyst-facing methodology cheat sheet. It explains where
each number on the Operating Statement / Investment / Returns tab comes
from and which source wins when multiple are available.

## Operating-ratio precedence chain (Wave 2 P2.7)

Sam's June 2026 ask: "Wants op-ratios extracted from CBRE / in-house
portfolio P&Ls (not HOST defaults)." Generic HostStats defaults were
killing credibility with institutional analysts who already have their
OWN portfolio data. Fondok now resolves operating-expense ratios
(rooms-dept %, F&B-dept %, admin %, sales %, utilities %, etc.) using
the following precedence chain, highest to lowest:

1. **Analyst override** — explicit, with a justification note. Set via
   the OverridePanel on any P&L row. Final say.
2. **T-12 actual** — Year-1 actual from the subject hotel's own
   extracted T-12. The most credible Y1 anchor we have.
3. **In-house portfolio P&L benchmark** (new) — the analyst firm's own
   roll-up of ratios across hotels they already operate. Most credible
   peer set when it covers the subject chain scale.
4. **CBRE Horizons benchmark** (new) — chain-scale-segmented industry
   benchmark from a CBRE Hotel Horizons report. Skipped when the
   chain scale doesn't match the subject deal — falls through to the
   next-lower tier instead of applying a mismatched benchmark.
5. **HostStats / generic industry default** (formerly the only
   external source) — applied when no portfolio / CBRE candidate
   covers the field. Tagged `pnl_benchmark` on the UI badge.
6. **Kimpton seed default** — absolute fallback. Keeps the engine
   from crashing when no real data is available; analysts see a
   "Seed" badge so they know it's not grounded.

Out-years are always grown forward from the chosen Year-1 anchor at
the configured `expense_growth` rate (default 3.5%) regardless of which
tier supplied the Y1 number.

### Worked example

200-key Marriott Courtyard, T-12 shows 12% rooms-dept ratio, CBRE
Horizons says 14% for that chain scale, portfolio P&L says 11%,
analyst override blank.

| Tier | Available? | Value | Winner? |
|---|---|---|---|
| analyst_override | ✗ | — | — |
| t12_actual | ✓ | 12% | **YES** |
| portfolio_pnl | ✓ | 11% | (outranked) |
| cbre_horizons | ✓ | 14% | (outranked) |
| pnl_benchmark | (seed) | 13% | (outranked) |
| seed | ✓ | 30% | (outranked) |

→ Engine output uses **12%** with source label `t12_actual`. The
Operating Statement renders the row with a green "T-12" badge so the
reviewer can see at a glance the value is grounded in real data.

### Why this order

* **Override beats everything** because the analyst's intent is final;
  the IC sometimes hard-codes a number for partnership / underwriting
  reasons even when actuals disagree.
* **T-12 beats every benchmark** — when we know what the hotel
  actually spent, that's the most credible Y1 anchor full stop.
* **Portfolio P&L beats CBRE** because the firm OWNS the underlying
  P&Ls; CBRE's roll-up is a black box of third-party reports.
* **CBRE beats HostStats default** because CBRE is segmented by chain
  scale + submarket; HostStats is one-size-fits-all.
* **Seed is the absolute fallback** so the engine never crashes.

### Chain-scale matching for CBRE Horizons

When the subject deal carries a chain-scale tag (e.g. "Upper Upscale")
and the CBRE candidate doesn't match (e.g. "Lower Priced"), the
resolver SKIPS the CBRE candidate and falls through to the next-lower
tier (HostStats → seed). A debug log records the fall-through so
analysts can trace it on the Engine Run page. Mismatched benchmarks
are worse than no benchmark.

### Where to look in code

* `apps/worker/app/services/op_ratio_precedence.py` — pure resolver
  (no DB / no engine import), unit-tested in
  `apps/worker/tests/test_op_ratio_precedence.py`.
* `apps/worker/app/services/engine_runner.py` — `_load_engine_inputs`
  builds the per-ratio candidates dict and calls `resolve_ratio` /
  `resolve_all` to compute the winning source per field, then writes
  the winning value into `base["overrides"]` and the winning source
  into `base["__sources__"]`.
* `apps/worker/app/agents/extraction_schemas/portfolio_pnl.md` — the
  Extractor schema for the new `PORTFOLIO_PNL` doc type.
* `apps/web/src/components/help/AssumptionBadge.tsx` — UI badge
  rendering for each source label (T-12, Portfolio, CBRE, PNL Bench,
  Seed, Override).
* `apps/web/src/components/project/PLTab.tsx` — Operating Statement
  with per-row source badges and the "Precedence" coachmark.

## Historical baseline — 3-5 year P&L walk (Wave 2 P2.6)

Sam's June 2026 ask: "Institutional IC analysts will not approve a deal
without seeing the multi-year trend." Today Fondok renders only the
forward proforma (Y1..Y5); the historical-baseline endpoint backs the
HistoricalBaselinePanel that stacks the property's OWN historical
actuals (3-5 prior years) side-by-side with the Y1 forecast.

### Lookback window

Default `lookback_years=5` per the Wave 1 product-decision doc
(`project_fondok_wave1_decisions.md` #5: "5-yr gap look-back"). The
engine surfaces `coverage_pct = years_with_data / lookback_years` so the
UI can render a "Coverage 3/5 yrs · Missing 2020-2021" chip without
having to re-derive the math. The endpoint clamps to `[2, 10]` years so
a malformed query string can't produce a 0-year walk or scan more
history than any institutional UW model bothers with.

### Document selection

The engine reads `documents` joined to `extraction_results` filtered
to the P&L family (`T12 / PNL / PNL_MONTHLY / PNL_YTD`),
`status='Extracted'`, and `fiscal_year IS NOT NULL`. Per-year selection
rule: the highest-confidence extraction wins — proxied by the USALI
deviation count (fewer = cleaner). Ties break on `created_at DESC` so
the most-recent extraction is canonical when two docs land on the
same year with equal scoring.

### Derived vs extracted fields

Every numeric field in the `HistoricalYear` dataclass is `float |
None`. `None` means the extractor didn't ship that line (UI renders
an em-dash). Fields the engine derives when missing:

* **`revpar`** — institutional shorthand `occupancy × ADR`. Computed
  when occ + ADR are both present and revpar wasn't extracted
  directly. RevPAR drift > 0.5% from this identity is an extraction
  bug.
* **`total_revenue`** — `rooms_revenue + fnb_revenue + other_revenue
  (+ resort_fees + misc_revenue)`. Synthesized by
  `services.usali_scorer._derive_usali_rollups` when at least 2 of the
  3 main components landed.
* **`undistributed`** — A&G + sales/mkt + utilities + prop_ops +
  info/telecom (5 buckets). Same USALI rollup honors a direct
  emission first; falls through to the 5-line sum when at least 2
  components are present.
* **`gop`** — `total_revenue − dept_expenses − undistributed`. Honored
  from a direct emission when the extractor ships
  `p_and_l_usali.gross_operating_profit_usd`; synthesized otherwise.
* **`fixed_expenses`** — `property_tax + insurance + mgmt_fee`.
  Institutional IC convention bundles mgmt fee into the fixed block
  (USALI's `fixed_charges` only covers tax + insurance, with mgmt_fee
  sitting between GOP and NOI — same dollars either way).
* **`noi`** — `gop − fixed_expenses` when both are derivable;
  otherwise honored from a direct emission.

### YoY walk

`walk_yoy(baseline)` projects consecutive-year deltas as a flat list
ordered by `abs(yoy_pct) DESC` so the UI's "Walk" chips render the
biggest swings first. A 0.5% noise floor (`_YOY_NOISE_FLOOR = 0.005`)
drops swings whose magnitude is below 0.5% — those are extractor
rounding artifacts, not analytical signal. The first year of the
series yields `yoy_pct=None` entries (no prior to compare) that sort
last.

### Gap detection

The engine walks `min(fiscal_year)..max(fiscal_year)` inclusive and
returns every year not represented in the result set as a gap. The UI
labels a contiguous gap range as "Missing 2020-2021" and a single
missing year as "Missing 2023".

### Where to look in code

* `apps/worker/app/engines/historical_baseline.py` — the engine.
  Exports `build_historical_baseline` (async, DB-backed),
  `build_baseline_from_pnls` (pure-function, used by tests), and
  `walk_yoy` (YoY projection).
* `apps/worker/app/api/documents.py` — `GET
  /deals/{deal_id}/historical-baseline` endpoint. Tenant-scoped via
  `_assert_deal_belongs_to_tenant`. Returns
  `HistoricalBaselineResponse` carrying `years` + `gaps` +
  `coverage_pct` + `walk`.
* `apps/worker/tests/test_historical_baseline.py` — 13 tests:
  empty-coverage, 3-year happy path, gap detection (single + multi),
  USALI deviation tie-break, derived RevPAR, walk ordering, noise
  floor, null prior, undistributed rollup, tenant isolation, endpoint
  happy path, endpoint empty.
* `apps/web/src/components/project/HistoricalBaselinePanel.tsx` — the
  panel. Hides itself when `coverage_pct === 0` (no historical docs
  uploaded). Mounted on InvestmentTab below the CapexPlanPanel.
* `apps/worker/app/migrations.py` —
  `documents.idx_deal_fy_pnl_family` partial index covers the
  baseline query on Postgres; SQLite gets a non-partial
  `(deal_id, fiscal_year)` index.

## Comparable Sales cap rate derivation (Wave 3 W3.1)

Sam's #1 institutional-credibility question is "where does your exit
cap rate come from?" Pre-W3.1, the answer was a deal-row column, the
median of a flat `transaction_comps.<n>.cap_rate_pct` list, or the
7.0% Kimpton seed. W3.1 upgrades the answer to a transparent comp-set
derivation: every transaction the engine considered, every comp it
filtered out, every weight it applied, and two derived numbers
(median + weighted) the analyst can pick between.

### Filter rules (applied in order)

1. **Analyst exclude** — rows whose `transaction_id` appears in
   `comp_sales.exclude_transaction_ids` are dropped before any
   derivation. The analyst made an explicit "this comp doesn't
   reflect the deal" call.
2. **Look-back** — drop comps with a `sale_date` older than
   `lookback_years` (default 5). Comps with no `sale_date` are *kept*
   (we can't prove they're stale) but tagged "no sale_date — recency
   bucketed as unknown" in `weighting_notes`.
3. **Cap-rate presence** — drop comps with no `cap_rate_pct`. A row
   can't contribute to a cap-rate average if it doesn't have one.

### Median derivation

Simple median of the surviving `cap_rate_pct` values, in percent
(e.g. `7.25` for 7.25%). Always computed when ≥1 comp survives. The
institutional fallback when subject metadata isn't available.

### Weighted derivation

Each surviving comp gets a per-row weight:

    weight = 0.7 * recency_score
           + 0.2 * market_match
           + 0.1 * chain_match

Where:

* **recency_score** — `1.0` if ≤ 2 yrs, `0.7` if ≤ 4 yrs, `0.4` if
  ≤ 6 yrs, `0.0` beyond. Comps with no `sale_date` get `0.4` (middle
  bucket — neither penalized nor rewarded).
* **market_match** — `1.0` if same MSA (approximated as same-city),
  `0.5` if same state, `0.0` otherwise. The MSA lookup is roadmapped;
  same-city is a reasonable proxy for institutional comp sets.
* **chain_match** — `1.0` if same chain-scale label, `0.5` if
  adjacent (`upscale ↔ upper-upscale`, `midscale ↔ upper-midscale`,
  `economy ↔ midscale`, `upper-upscale ↔ luxury`), `0.0` otherwise.

    weighted_cap = Σ(cap_rate * weight) / Σ(weight)

The 70/20/10 split reflects how hospitality IC anchors exit cap:
recency dominates because the market is moving (rate volatility
2023-2025), then market specificity, then chain-scale fit. An
analyst-driven re-weight is roadmapped but not in W3.1.

The weighted derivation is **emitted as the headline method only
when** the analyst provided a subject market or subject chain-scale.
Without either, the formula collapses to recency-only and we report
`method=median` (no information gain over the simple median).

### Coverage quality

Coverage label = `high` when ≥ 8 qualifying comps, `medium` when 4-7,
`low` when < 4. Surfaced in the UI as a colour-coded chip — a `low`
coverage label is the engine telling the analyst "this anchor is too
thin to ride; consider an analyst override or asking the broker for
more comps".

### Where to look in code

* `packages/schemas-py/fondok_schemas/comp_sales.py` — the
  `CompTransaction` + `CompSalesSet` Pydantic models.
* `apps/worker/app/engines/comp_sales.py` — `build_comp_set()`, the
  pure deterministic engine. No DB, no LLM, no I/O. Constants
  `W_RECENCY`, `W_MARKET`, `W_CHAIN`, `RECENCY_LE_2YR` etc are
  exported on the module so tests can pin the bucket boundaries.
* `apps/worker/app/agents/extraction_schemas/comparable_sales.md` —
  the extractor schema. Documents the
  `comparable_sales.<n>.{property_name, city, state, sale_date,
  keys, sale_price_usd, sale_price_per_key_usd, noi_usd,
  cap_rate_pct, chain_scale, brand_family, flag}` namespace.
* `apps/worker/app/services/engine_runner.py` —
  `_load_comp_transactions()` reads both the new
  `comparable_sales.<n>.*` namespace and the legacy
  `transaction_comps.<n>.*` namespace off OM extraction results.
  `_build_comp_sales_set()` is the high-level orchestrator the API
  calls. `_OVERRIDE_COMPS_KEYS` routes the two analyst override
  paths (`comp_sales.derived_cap_rate_override`,
  `comp_sales.exclude_transaction_ids`).
* `apps/worker/app/api/deals.py` —
  `GET /deals/{deal_id}/comp-sales` returns the full `CompSalesSet`
  for the deal (tenant-scoped 404). `POST
  /deals/{deal_id}/comp-sales/exclude` with body
  `{"transaction_id": "..."}` pins a row as excluded and returns the
  refreshed set.
* `apps/worker/tests/test_comp_sales.py` — 12 tests covering:
  empty-set fallback, median of 5 comps, weighted-recency-dominates,
  weighted-component-validation (0.7/0.2/0.1), coverage-quality
  thresholds, look-back filter, exclude-list, adjacent-chain-scale
  half-weight, weighting-notes emission, fallback method when no
  subject metadata, two endpoint tests (tenant-scoped + full
  derivation round-trip).
* `apps/web/src/components/project/CompSalesPanel.tsx` — the table
  view + median/weighted toggle + per-row exclusion checkbox.
  Source badge (`om_comps`) and coverage-quality chip in the header.
  Mounted in `ReturnsTab` under the new "Comps" sub-tab.

