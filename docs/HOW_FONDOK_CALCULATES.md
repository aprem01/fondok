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


## STR forward forecast — 24-month, 3-scenario projection (Wave 3 W3.3)

Sam's June 2026 ask: institutional analysts won't approve a deal
without seeing a forward RevPAR forecast across multiple scenarios.
The historical baseline above looks backward at the property's own
P&L; the STR Forward Forecast looks **forward** at the property's
RevPAR vs the comp set across 24 months in three branches.

### Inputs

The engine consumes the trailing 24 months of subject + comp-set
monthly RevPAR / Occ / ADR from the deal's STR_TREND extractions
(see `apps/worker/app/agents/extraction_schemas/str_trend.md`). The
loader (`apps/worker/app/services/str_forecast_loader.py`) reads
every STR_TREND extraction for the deal, normalizes the
`ttm_performance.subject.monthly.<YYYY_MM>.*` field paths into
`STRMonth` records, and derives the comp-set RevPAR from the
trailing-12 comp rows (the STR Trend report doesn't publish per-
month comp data — only trailing aggregates).

### Three default scenarios

* **downside** — RevPAR CAGR `-2.0%`, subject index target `0.92`
  (subject loses share to comp set), occupancy floor `0.55`, ADR
  floor `0.80` (80% of trailing-12 ADR).
* **base** — RevPAR CAGR `+2.5%`, index target `1.00` (subject
  matches comp set), occupancy floor `0.60`, ADR floor `0.88`.
* **upside** — RevPAR CAGR `+5.0%`, index target `1.06` (subject
  pulls ahead by 6%), occupancy floor `0.65`, ADR floor `0.92`.

Analysts can override any scenario's knobs via
`POST /deals/{id}/str-forecast/scenarios`. Omitted fields inherit
from the default.

### Per-month math

For each scenario and each forward month `m` in 1..24:

1. **Comp-set RevPAR** — projected at the scenario's CAGR off the
   trailing-12 average comp RevPAR. The monthly factor is
   `(1 + CAGR) ** (m / 12)` — annualized growth distributed across
   the horizon.
2. **Subject RevPAR Index** — linearly interpolated from the
   trailing-12 subject index to the scenario's
   `revpar_index_target` over the 24-month horizon. Month 1 nudges
   off the starting index; month 24 lands at the target.
3. **Subject RevPAR** — `comp_revpar[m] × subject_index[m]`.
4. **Decompose into Occ × ADR** — preserves the trailing-12 occ:adr
   ratio `r`. With `occ × adr = revpar` and `occ / adr = r` we solve
   `occ = sqrt(revpar × r)`, `adr = sqrt(revpar / r)`.
5. **Clip to floors** — if the decomposed occupancy is below the
   scenario's `occupancy_floor` we hold occupancy at the floor and
   solve ADR back out. Same for the ADR floor (interpreted as a
   multiplier on the trailing-12 ADR). When both floors bite, the
   resulting RevPAR equals the floor product (a deliberate
   guardrail, not a math bug).

### Coverage tiers

* `high` — 24+ historical months on file (full STR Trend window).
* `medium` — 12-23 months.
* `low` — < 12 months. Forecast is disabled; the engine returns
  empty `forecast_months` per scenario and the UI renders an
  "Awaiting more STR Trend history" banner.

### Optional revenue-engine seed

When the analyst flips `revenue_seed_from_str_forecast` to True on
the deal's `field_overrides`, the engine_runner seeds
`RevenueEngineInput.starting_occupancy` and `starting_adr` from the
BASE scenario's Month-12 forecast point. Both fields then tag with
`SOURCE_STR_FORECAST` in the assumption-provenance map; the
`AssumptionBadge` UI renders "STR Fcst". Default is OFF — existing
deals are unaffected.

### Where to look in code

* `packages/schemas-py/fondok_schemas/str_forecast.py` — Pydantic
  schemas (`STRMonth`, `STRForecastScenario`, `STRForecastResult`).
* `apps/worker/app/engines/str_forecast.py` — the engine. Exports
  `build_str_forecast` (pure function) and `default_scenarios`.
* `apps/worker/app/services/str_forecast_loader.py` — DB loader
  that materializes monthly STR Trend rows into `STRMonth`s.
* `apps/worker/app/api/documents.py` —
  `GET /deals/{deal_id}/str-forecast` and
  `POST /deals/{deal_id}/str-forecast/scenarios`. Both tenant-scoped.
* `apps/worker/app/services/engine_runner.py` —
  `_load_str_forecast_for_seed` + `SOURCE_STR_FORECAST` source tag.
* `apps/worker/tests/test_str_forecast.py` — 14 tests covering
  coverage tiers, scenario defaults, monotonic RevPAR ordering,
  linear-index interpolation, occupancy + ADR floors, comp-set
  growth math, subject = comp × index identity, endpoint tenant
  scoping, and the revenue-engine seed flag.
* `apps/web/src/components/project/STRForecastPanel.tsx` — the
  panel. Renders the 24-month historical + forecast chart, three
  scenario cards with inline edit, and the revenue-engine seed
  toggle.
* `apps/web/src/components/project/ForecastingTab.tsx` — tab host
  mounted on the Project Detail page as the **Forecasting** tab.

