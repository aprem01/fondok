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

---

## IC Memo PDF — content map

The exported IC memo (`GET /deals/{deal_id}/export/memo.pdf`) is
assembled by `apps/worker/app/export/memo_pdf.py` from two inputs:
the structured `memo` dict (executive summary + thesis + risks +
recommendation) and the `model` dict that carries every engine
output. Each section is **conditional** — it renders only when its
backing data is present, so a barebones deal still produces a clean
memo without empty placeholders.

| Memo Section            | Source on `model[...]`                     | Engine / module                                                |
| ----------------------- | ------------------------------------------ | -------------------------------------------------------------- |
| Header chip + KPIs      | `investment_engine`, `returns_engine`      | `engines/returns.py`, `engines/capital.py`                     |
| Executive Summary       | `memo["sections"][executive_summary]`      | `agents/memo_writer` (Claude-drafted)                          |
| Investment Thesis       | `memo["sections"][investment_thesis]`      | `agents/memo_writer`                                           |
| Highlights / Risks      | `memo["sections"][key_insights, risk_…]`   | `agents/memo_writer` + `engines/sensitivity.py`                |
| **Revenue Mix (Y1)**    | `segments_by_year` (or `revenue_engine.…`) | `engines/revenue.py` — P2.1 segmentation                       |
| **Renovation Plan**     | `pip_displacement`                         | `engines/revenue.py` — P2.4 PIP displacement v2                |
| **Historical Walk**     | `historical_baseline` (`years`, `walk`)    | `engines/historical_baseline.py` — P2.6 3-yr baseline          |
| Sources & Uses          | `sources`                                  | `engines/capital.py`                                           |
| Returns Summary         | `returns_engine`, `debt_engine`            | `engines/returns.py`, `engines/debt.py`                        |
| **Capital Plan**        | `capex_schedule`                           | `engines/capex_plan.py` — P2.5 three-bucket                    |
| **Op-Ratio Provenance** | `op_ratio_provenance.lines[]`              | `services/op_ratio_precedence.py` — P2.7 precedence resolver   |
| **Pricing Sensitivity** | `sensitivity_grid` (`cells`, `breakeven`)  | `engines/pricing_sensitivity.py` — P2.8 5x5 grid               |
| **Max-Price Findings**  | `max_price`                                | `engines/price_solver.py` — P2.8 bisection solver              |
| Variance Disclosure     | `memo["sections"][variance_disclosure]`    | `engines/historical_variance.py`                               |
| **LOI Draft Appendix**  | `loi_draft.rendered_markdown`              | `engines/loi_generator.py` — P2.8 LOI template                 |
| Footer (docs + engines) | `memo["appendix"]`                         | `agents/memo_writer` + `api/export.py` real-docs patch         |

Bolded sections are the Wave 3 W3.4 additions.

### How the aggregator decides what to render

`_aggregate_wave2_for_memo(model)` (in `memo_pdf.py`) normalizes every
Wave 2 slot into a clean shape and returns `None` for any section whose
data is missing or trivially empty:

* `segments` — needs a non-empty `segments_by_year[0].segment_breakdown`.
* `pip` — needs `closure_strategy != "none"`. The fixture `"none"`
  placeholder is treated as "no renovation" and the section is omitted.
* `capex_schedule` — needs a non-empty list.
* `op_ratio_provenance` — needs `lines` to be non-empty.
* `sensitivity_grid` — needs at least one cell. Cell colour follows the
  same green / amber / red scale as the UI heatmap, against the grid's
  declared `target_irr` (default 15%). DSCR-breach cells render red
  with a `!` marker.
* `max_price` — needs the dict to be present. `binding_constraint`
  picks the lower of the two prices for the headline chip.
* `historical_baseline` — needs `coverage_pct > 0`. The walk panel
  reads the top YoY swings off `walk[]`.
* `loi_draft` — needs a non-empty `rendered_markdown`. The body is
  converted to inline HTML by `_markdown_to_html` (a tiny dependency-free
  converter that handles headings, bold, `---` hr, and bulleted lists).

### Where to look in code

* `apps/worker/app/export/memo_pdf.py` — the builder. Holds
  `CSS` (the @page + .callout + .grid styling), `_aggregate_wave2_for_memo`,
  the eight per-section renderers (`_render_revenue_mix`,
  `_render_pip_plan`, `_render_capex_plan`,
  `_render_op_ratio_provenance`, `_render_sensitivity_grid`,
  `_render_max_price_callout`, `_render_historical_walk`,
  `_render_loi_appendix`), and the `build_memo_pdf` entrypoint that
  pushes the HTML through WeasyPrint.
* `apps/worker/app/export/fixtures.py` — the Kimpton Angler demo
  payload. Carries fully-populated Wave 2 fixture data so the export
  works end-to-end before the DB-backed Wave 2 outputs land.
* `apps/worker/app/api/export.py` — the FastAPI router. Three
  endpoints: `export/excel`, `export/memo.pdf`, `export/presentation.pptx`.
  All patch `memo.appendix.documents_reviewed` with the real uploaded
  filenames before invoking the builder.
* `apps/worker/tests/test_memo_pdf_wave2_sections.py` — the 12 W3.4
  tests (one per section + backward-compat + end-to-end PDF).
* `apps/web/src/app/projects/[id]/page.tsx` — the "Export memo"
  button. Redirects to the worker endpoint; no changes required by
  W3.4 because `build_memo_pdf`'s signature is unchanged.

---

## Pipeline view (Wave 3 W3.5)

Every analyst runs 30+ deals/year and the per-deal drill-down can
hide the obvious portfolio question: *which of my live deals
actually pencil?* The Pipeline page (`/pipeline`, backed by
`GET /deals/pipeline`) puts every active deal on one screen with
its headline returns from the most-recent engine run, plus a
portfolio-level KPI strip at the top.

### What each KPI means

* **Deals in Pipeline.** Count of non-archived deals in the tenant.
  The sub-line breaks it down by lifecycle state (`Onboarding`
  while docs are uploading, `Validating` during gap/anomaly review,
  `Ready` once IC-grade).
* **Median Levered IRR.** Midpoint of `levered_irr` across deals
  that have run the Returns engine — half higher, half lower. The
  sub-line shows the p25 / p75 band so an analyst sees the IRR
  *distribution*, not just the centre. Computed by linear-
  interpolation percentile (sorted index → fractional position).
* **Median $/Key.** Midpoint of `price_per_key` across the visible
  rows. Reads the Capital engine's computed value first; falls
  back to `purchase_price / keys` when the engine hasn't run. Best
  read with the *Median exit cap* sub-line for the
  price-vs-yield trade-off across the book.
* **Meeting Target IRR.** `deals_meeting_target_irr /
  deals_with_target_irr` — only deals whose analyst has set a
  `target_irr` count toward either side, so a sparse pipeline
  doesn't inflate the miss rate. A deal "meets target" when its
  latest `levered_irr ≥ target_irr`.

### Per-row metrics

Each row shows: name + city + brand + keys / lifecycle state /
$/key / Y1 NOI / exit cap rate / levered IRR / equity multiple /
Y1 DSCR / target IRR / last activity. Numbers come from the LATEST
row per engine (ROW_NUMBER OVER PARTITION) so re-running the
model on a deal replaces its rollup without affecting peers.
NULL cells render as dashes — that's "no engine run yet", not
zero.

### Where to look in code

* `apps/worker/app/api/deals.py` —
  `GET /deals/pipeline` endpoint. Models `PipelineDealRow`,
  `PipelineSummary`, `PipelineResponse`. Tenant-scoped via
  `get_tenant_id`; clamps `limit` to 200; rejects unknown sort
  tokens with 400.
* `apps/worker/app/services/pipeline.py` — the aggregator:
  `build_pipeline_snapshot` runs one tenant-scoped SQL pull
  (deals + window-function-latest engine rows + grouped doc
  counts) and caches the projected list for 60 s per tenant.
  Mutations on deals or engines call `invalidate(tenant_id)`.
* `apps/worker/tests/test_pipeline.py` — 14 tests covering
  empty, tenant-scoping, latest-run-per-deal join, every sort
  token, every filter, pagination, summary p25/p50/p75 IRR, and
  `target_irr_met` semantics.
* `apps/web/src/app/pipeline/page.tsx` — the page. Sticky-header
  table, click-to-sort column headers, filter bar (state / min
  IRR / max $/key / sort). Sidebar nav link added in
  `apps/web/src/components/layout/Sidebar.tsx`.

### Why a window-function join (not a materialized view)

The latest-run-per-engine join is the trickiest piece: every
engine writes one row per run, and the Pipeline view needs the
most-recent row per (deal_id, engine_name) for several engines
at once. Two options:

1. **Materialized view** `deal_pipeline_snapshot` refreshed on
   every engine completion. Cheapest at read time but adds a
   migration + cross-dialect divergence (no PG-style materialized
   views on SQLite) and another piece of state to keep in sync.
2. **Window-function pull** with a 60 s in-process LRU cache.
   Portable to SQLite (which supports window functions since
   3.25), one query per Pipeline open, cache-busted on writes.

We chose option 2 for this sprint: the analyst's pipeline is
typically O(100) deals, the window-function plan is sub-200ms
on Postgres + SQLite alike, and the cache absorbs click-storms
without the operational tax of a materialized view. The code is
laid out so a future swap to a materialized view is a single
function-replacement in `services/pipeline.py`.

---

## Named scenarios — save/load/diff (Wave 3 W3.2)

Every IC committee opens with the same question: *what does the
downside look like, and what does the upside look like?* A point
estimate isn't enough; analysts need to compare the base case against
a named "downside", "upside", "broker high case", "IC stress test", etc.
without forking the deal or screenshotting outputs into a deck.

A **scenario** is a named layer of per-field overrides on top of the
deal's persisted `field_overrides`. The same engine_runner that backs
the Run Model button accepts an optional `scenario_id`; when set, the
input loader merges the scenario's overrides on top of the deal-level
overrides BEFORE the existing override-routing logic fires. That means
every override path the deal-level OverridePanel already supports —
top-level keys (`exit_cap_rate`, `starting_occupancy`), structured
overrides (`pip_displacement.brand`,
`segments.transient_ota.adr`, `capex_plan.pip.total_usd`) — works
identically inside a scenario.

### Data model

```python
class ScenarioOverride(BaseModel):
    field_path: str          # e.g. "exit_cap_rate" or "pip_displacement.brand"
    value: Any               # scalar or JSON-compatible
    source: str = "analyst_override"

class Scenario(BaseModel):
    id: str
    deal_id: str
    tenant_id: str
    name: str                # "Base", "downside", "Apollo IC target"
    description: str | None
    is_base: bool = False    # exactly one base per deal
    overrides: list[ScenarioOverride]
    created_at: datetime
    updated_at: datetime
    last_run_id: str | None  # most recent engine_outputs.run_id
```

The `scenarios` table mirrors this shape:

* Postgres — `id UUID PK`, `deal_id UUID NOT NULL REFERENCES deals(id) ON DELETE CASCADE`,
  `tenant_id UUID NOT NULL`, `name TEXT NOT NULL`,
  `description TEXT NULL`, `is_base BOOLEAN NOT NULL DEFAULT false`,
  `overrides JSONB NOT NULL DEFAULT '[]'`, `last_run_id UUID NULL`,
  `created_at`, `updated_at`, `UNIQUE (deal_id, name)`,
  index on `(deal_id, tenant_id)`.
* SQLite mirror — `BOOLEAN → INTEGER 0/1`, `JSONB → TEXT` (API
  json.dumps / json.loads), `UUID → TEXT`, no FK enforcement (matches
  the rest of the dev mirror).

### Auto-created base scenario

Every freshly created deal (`POST /deals`) gets a single
`is_base=true` scenario inserted in the same transaction via
`apps/worker/app/api/scenarios.create_base_scenario_for_deal`. The
base scenario carries an empty override list — running the engine
chain with `scenario_id = base.id` is byte-identical to running with
no `scenario_id` at all. That's the test
`test_run_scenario_without_overrides_matches_base` pins, so we never
silently diverge the two code paths.

### Override precedence

Scenario overrides win on conflict with deal-level overrides; analyst
intent at the scenario level beats everything else (T-12 actuals,
CBRE Horizons, OM comps, deal-row values, Kimpton seed). The
provenance badge keeps reading `analyst_override` because both layers
carry the same source label.

```
seed
  ← deal_row (purchase_price, keys)
  ← OM / T-12 / CBRE / portfolio P&L (when extracted)
  ← deal.field_overrides (OverridePanel)
  ← scenarios.overrides   ← WINS  (Wave 3 W3.2)
```

### Engine runs

`POST /deals/{id}/scenarios/{scenario_id}/run` runs the full 8-engine
chain synchronously with the scenario applied, stamps the run id back
into `scenarios.last_run_id`, and returns the engine output map. The
UI uses `last_run_id` to deep-link back into `engine_outputs` without
re-running the math.

`POST /deals/{id}/scenarios/compare` accepts up to 4 scenario ids and
returns one column per scenario. Scenarios that haven't been run yet
are auto-run inline so the side-by-side never renders an empty column.
Every scenario id is verified to belong to the deal + tenant; mixing
in a scenario id from another tenant returns 404.

### Where to look in code

* `packages/schemas-py/fondok_schemas/scenario.py` — `Scenario` +
  `ScenarioOverride` (Pydantic v2).
* `apps/worker/app/api/scenarios.py` — 7 endpoints
  (`list / create / get / patch / delete / run / compare`) +
  `create_base_scenario_for_deal` helper used by `deals.create_deal`.
* `apps/worker/app/services/engine_runner.py` —
  `_load_scenario_overrides`, `_load_engine_inputs(..., scenario_id=)`,
  `run_single_engine(..., scenario_id=)`,
  `run_all_engines(..., scenario_id=)`.
* `apps/worker/app/migrations.py` — `scenarios.create_table` +
  `scenarios.idx_deal_tenant`, both Postgres and SQLite mirrors.
* `apps/worker/tests/test_scenarios.py` — 15 tests covering
  auto-base creation, tenant scoping, override routing through PIP /
  segment / capex paths, compare side-by-side, base-undeletable,
  unique-name-per-deal, last_run_id stamping.
* `apps/web/src/components/project/ScenarioSelector.tsx` — pill row
  at the top of the project workspace.
* `apps/web/src/components/project/ScenarioComparePanel.tsx` —
  side-by-side compare table on the Scenarios tab.
* `apps/web/src/components/project/ScenarioEditor.tsx` — side panel
  for editing overrides (NO modal; Wave 1 no-popups rule).

---

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

---

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

---

## Debt stack v2 (Wave 4 W4.4)

Pre-W4.4 Fondok modeled debt as a single senior loan: LTV × purchase
price, an annual interest rate, an optional interest-only stub, and
a 30-year amortization. Real institutional hotel deals layer the
capital stack — senior first-lien at 55-65% LTV, mezzanine at the
65-75% LTC band, preferred equity at 75-80% — each with its own
rate, IO horizon, and amortization. The Y1 DSCR and debt yield IC
analysts read off the memo are *blended* metrics across the entire
stack, not just the senior. Year-N (typically Y3 or Y5) the deal
has to clear a refinance test against market debt yield and DSCR.

### Tranche schema

`packages/schemas-py/fondok_schemas/debt_stack.py` defines the
canonical shapes:

* `DebtTranche` — one slice of the stack. Fields: `name` (senior /
  mezz / pref_equity), `principal_usd`, `rate_pct`,
  `io_period_months`, `amortization_months`, `upfront_fee_pct`,
  `exit_fee_pct`, `is_senior`, `priority_rank`. Validators pin
  `is_senior=True ↔ priority_rank=1` and reject ranks > 3 or rank
  conflicts within the same stack.

* `DebtStackInput` — engine input. Requires `tranches` (length ≥ 1
  with exactly one senior) plus `noi_by_year`, `term_years`, the
  refi test knobs (`refi_test_year`, `refi_market_debt_yield_pct`,
  `refi_market_dscr_min`, `refi_market_rate_pct`,
  `refi_market_cap_rate`, `exit_cap_rate`).

### Engine math

`apps/worker/app/engines/debt.py` exposes three pure helpers:

1. **`build_amort_schedule(tranche, term_years=N)`** — replicates
   the legacy single-loan path on a per-tranche basis. IO stub
   (zero principal) until `io_period_months`, then standard P&I
   PMT. Exit fees land on the FINAL month's payment so the
   per-year roll-up captures them in Y5 DS — they aren't
   capitalized to a new principal balance.

2. **`build_stack_schedule(payload)`** — runs every tranche through
   `build_amort_schedule`, then aggregates:

   * `total_ds_by_year[y]` = Σ over tranches of `debt_service_usd[y]`
   * `debt_yield_by_year[y]` = `noi[y] / Σ ending_balance[y]`
     (EOP balance — bank-credit convention)
   * `dscr_by_year_per_tranche[name][y]` — cumulative through that
     tranche's `priority_rank`. Rank 1 (senior) sees senior DS
     only; rank 2 (mezz) sees senior + mezz DS; rank 3 (pref) sees
     the entire stack.
   * `dscr_blended_by_year[y]` = `noi[y] / total_ds_by_year[y]`
   * `cumulative_ltc` = `total_debt / purchase_price`
   * `weighted_avg_rate_pct` — principal-weighted across tranches

3. **`run_refi_test(payload, schedule)`** — at `refi_test_year`:

   * `refi_noi` = `noi[refi_test_year]` (next-year NOI — what the
     lender underwrites against, not trailing)
   * `refi_property_value` = `refi_noi / market_cap_rate`
     (fallback: deal's `exit_cap_rate`)
   * `max_debt_dy` = `refi_noi / market_debt_yield_pct`
   * `max_debt_dscr` = `refi_noi / (dscr_floor × refi_rate_pct)`
     (assumes IO-equivalent debt service for the constraint;
     `refi_rate_pct` defaults to the senior tranche's rate)
   * `max_refi_debt` = `min(max_debt_dy, max_debt_dscr)` — the
     binding constraint wins
   * `outstanding` = Σ ending balances across tranches at the END
     of `refi_test_year`
   * `can_refi` = `(max_debt_dy ≥ outstanding) AND
     (dscr_at_outstanding ≥ dscr_floor)`
   * `cash_to_close_equity` = `max(0, outstanding - max_refi_debt)`
     — the sponsor cash-in required to refi at market terms

### Backward compatibility

A single-tranche stack with the senior matching the legacy loan's
principal, rate, and amortization reproduces the pre-W4.4 monthly
schedule byte-for-byte. Verified by
`test_single_senior_tranche_matches_legacy_single_loan` in
`apps/worker/tests/test_debt_stack_v2.py`. The legacy `DebtEngine`
class is preserved unchanged.

### Returns engine integration

`ReturnsEngineInputExt` now accepts an optional
`debt_service_by_year: list[float]`. When non-empty the levered
CFAD series uses year-by-year DS (the full stack); when empty the
legacy scalar `annual_debt_service` path runs unchanged. The
engine_runner can wire the new path once the debt-stack engine is
registered in the `ENGINE_REGISTRY`; for now the panel previews
the math client-side and only the returns engine is impacted on
deals that surface a stacked DS series.

### Worked example

$40M deal, $24M senior @ 6.0% / 30-yr amort, $8M mezz @ 11% / 60-mo
IO, $4M pref equity @ 14% / 60-mo IO. NOI Y1 = $3.50M, Y5 = $4.50M.

* Total debt = $36M; LTC = 90%; weighted rate ≈ 8.0%.
* Y1 senior DS ≈ $1.73M, Y1 mezz interest ≈ $0.88M, Y1 pref
  interest ≈ $0.56M. **Total Y1 DS ≈ $3.17M.**
* **Blended Y1 DSCR ≈ 1.10x**, **Y1 debt yield ≈ 9.9%** against
  ~$35.4M EOP balance.
* Refi test Y5: refi NOI = ~$4.80M (next year). At 9% market DY,
  `max_debt_dy` = $53.3M; outstanding ≈ $35.0M → `can_refi=True`,
  cash-to-close = $0.

### Override paths

Routed by `engine_runner._OVERRIDE_DEBT_KEYS`:

* `debt_stack.tranches.<idx>.rate_pct`
* `debt_stack.tranches.<idx>.principal_usd`
* `debt_stack.tranches.<idx>.amortization_months`
* `debt_stack.tranches.<idx>.io_period_months`
* `debt_stack.tranches.<idx>.upfront_fee_pct`
* `debt_stack.tranches.<idx>.exit_fee_pct`
* `debt_stack.refi_test_year`
* `debt_stack.refi_market_debt_yield_pct`
* `debt_stack.refi_market_dscr_min`
* `debt_stack.refi_market_cap_rate`
* `debt_stack.refi_market_rate_pct`

All tagged `SOURCE_ANALYST_OVERRIDE` and parked under
`base["debt_stack_overrides"]` for the future engine_runner debt-
stack builder to consume.

### Where to look in code

* `packages/schemas-py/fondok_schemas/debt_stack.py` — Pydantic
  schemas (`DebtTranche`, `DebtStackInput`, `DebtStackOutput`,
  `RefiTestResult`).
* `apps/worker/app/engines/debt.py` — engine (legacy `DebtEngine`
  + `build_amort_schedule` / `build_stack_schedule` /
  `run_refi_test`).
* `apps/worker/app/engines/returns.py` — accepts
  `debt_service_by_year` for the levered cash-flow series.
* `apps/worker/app/services/engine_runner.py` —
  `_OVERRIDE_DEBT_KEYS` + `_parse_debt_stack_override_path`.
* `apps/worker/tests/test_debt_stack_v2.py` — 16 tests covering
  byte-identity, IO stub, P&I after IO, debt yield EOP convention,
  blended DSCR, refi pass/fail/cash-in, rank invariant, upfront +
  exit fees, empty-tranche rejection, returns engine integration,
  and a 5×5 DSCR sensitivity grid.
* `apps/web/src/components/project/DebtStackPanel.tsx` — UI panel
  with KPI strip, three collapsible tranche sections, refi-test
  card with status pill, and five institutional templates (Senior
  Only, Senior + Mezz 15%, Senior + Mezz 20%, Senior + Mezz +
  Pref, Bridge Debt).
* `apps/web/src/components/project/InvestmentTab.tsx` — mounts
  `DebtStackPanel` between Valuation Assumptions and the timeline
  (replacing the former Senior Loan Financing card).

---

## Excel acquisition model — sheet catalog (Wave 4 W4.2)

The Excel export (`GET /deals/{id}/export/excel`) ships the same
institutional workbook IC analysts have used since launch, plus every
Wave 2/3 analytical artifact as a **conditional** sheet — each new
sheet renders only when its source data is present on the engine
outputs dict. A barebones deal still produces a valid 9-sheet workbook;
the Kimpton fixture (all engines run) ships 19 sheets.

### Always-on sheets

| # | Sheet | Source data | Notes |
|---|-------|-------------|-------|
| 1 | Cover | (all engines) | Quick Read (6 headline KPIs) + Key Metrics + Sections Included list (mirrors which Wave 2/3 sheets shipped) + Generated-by timestamp |
| 2 | Assumptions | `investment_engine`, `debt_engine`, `refi_engine`, `returns_engine` | Acquisition, debt, refi, reversion sections |
| 3 | Sources & Uses | `sources`, `uses`, `investment_engine`, `debt_engine` | Auto-totals via SUM formulas |
| 4 | Operating Proforma | `p_and_l_engine_proforma` | 5-year proforma with CAGR column |
| 5 | Debt Schedule | `debt_engine` | Month-by-month amortization through term |
| 6 | Returns | `returns_engine`, `cash_flow_engine`, `debt_engine` | Reversion math + 5yr CF waterfall |
| 7 | Partnership | `partnership_engine`, `cash_flow_engine` | Waterfall tiers + LP/GP year-by-year split estimate |
| 8 | Variance | `variance_flags` | Severity-coloured rows with recommended actions |
| 9 | Market Comps | `market_comps` | UI chrome — comp-set transactions list |

### Wave 2/3 conditional sheets

| # | Sheet | Triggered when | Wave |
|---|-------|----------------|------|
| 10 | Revenue Mix | `segments_by_year` non-empty | 2 (revenue segmentation) |
| 11 | Renovation Plan | `pip_displacement.closure_strategy` ≠ `"none"` | 2 (PIP displacement v2) |
| 12 | Capital Plan | `capex_schedule` non-empty | 2 (three-bucket capex) |
| 13 | Op-Ratio Provenance | `op_ratio_provenance.lines` non-empty | 2 (op-ratio precedence) |
| 14 | Pricing Sensitivity | `sensitivity_grid.cells` non-empty | 2 (pricing sensitivity grid) |
| 15 | Comparable Sales | `comp_sales.transactions` non-empty | 3 W3.1 |
| 16 | Historical Baseline | `historical_baseline.years` non-empty + coverage_pct > 0 | 2 (3-5 year baseline) |
| 17 | STR Forecast | `str_forecast` carries historical or forecast months | 3 W3.3 |
| 18 | Named Scenarios | `named_scenarios` has ≥ 2 scenarios | 3 W3.2 |
| 19 | LOI Appendix | `loi_draft.rendered_markdown` non-empty | 2 (LOI generator) |

### Sheet-level details

**Revenue Mix.** One sub-block per hold year, segment-by-segment rows
with Mix %, ADR, Gross Revenue, Channel Cost %, Net Revenue. Rows with
channel cost ≥ 15% (OTA-heavy) get an amber fill so analysts can spot
distribution-drag segments at a glance. Year-total and grand-total
rows.

**Renovation Plan.** Summary block (closure strategy, brand, recovery
months, post-reno RevPAR index, Y1 displacement $) + brand recovery
multiplier reference table + month-by-month % rooms offline + Y2
recovery curve.

**Capital Plan (3-bucket).** Replaces the legacy single capex line in
Assumptions. Cols: PIP / Non-PIP FF&E / ROI Investment / ROI NOI Lift
/ Total Capex. Rows = hold years + Total. Cell comments
(`override_note`) attach analyst-provenance notes on a per-cell basis.

**Op-Ratio Provenance.** One row per operating expense ratio. Cols:
Field, Value, Source, Document / Note. Rows are tinted by source
(T-12 = green, Portfolio = blue, CBRE = amber, HOST = neutral,
Override = pink). Legend row at bottom shows the precedence chain.

**Pricing Sensitivity.** Replaces the legacy Sensitivity sheet. Two
5×5 grids stacked: the Levered IRR grid on top, the Equity Multiple
grid below. Each carries a real `ColorScaleRule` (3-color, red →
amber → green) so analysts see the rule under Excel's "Manage Rules"
dialog. DSCR-breach cells get a red fill + medium red border + a `!`
cell comment over both grids. Top of the sheet shows the Max-Price
findings (max price for IRR target, max price for EM target, binding
constraint, breakeven exit cap, breakeven NOI multiplier).

**Comparable Sales.** Derivation summary callouts (Method, Coverage,
Median Cap, Weighted Cap) + Weighting Notes block + per-transaction
table. Excluded comps (`excluded: true`) get a red row fill.

**Historical Baseline.** Coverage banner at top + gaps callout
(missing fiscal years) + horizontal P&L table (rows = USALI line
items, cols = fiscal years). YoY Walk section lists the top 5 swings
with a color-scale rule on the YoY % column.

**STR Forecast.** 48 data rows: 24 historical + 24 forecast. Cols:
Month, Historical RevPAR, Downside RevPAR, Base RevPAR, Upside
RevPAR, Comp Set RevPAR, Subject Index. Historical rows leave the
three scenario cols blank; forecast rows leave the Historical RevPAR
col blank.

**Named Scenarios.** Side-by-side compare. Cols are paired (Scenario,
Δ vs Base) per scenario. Rows are the 6 IC headline KPIs: Levered IRR,
Equity Multiple, Year 1 NOI, Stabilized NOI, Exit Cap Rate, Year 1
DSCR. Base scenario's Δ column is blank.

**LOI Appendix.** Plain-text rendering of the LOI markdown body, one
line per row in a wide column. H1/H2/H3 markdown headings get
bold-size formatting; `---` rules become row breaks.

### Backward compat

Every Wave 2/3 sheet builder no-ops when its source data is missing
or empty. A barebones deal (no Wave 2/3 keys on the engine outputs
dict) produces a 9-sheet workbook; the Cover sheet's "Sections
Included" list reflects only the sheets that actually rendered. The
test suite covers this in
`apps/worker/tests/test_excel_wave2_3_sections.py::test_barebones_deal_still_produces_valid_xlsx`.

### Where to look in code

* `apps/worker/app/export/excel.py` — the builder. Top-level
  `build_excel(deal_id, model, output_path)` runs the always-on
  builders, then iterates the Wave 2/3 builders gated on
  `_aggregate_wave2_3_for_excel(model)`, then renders the Cover sheet
  with the final section list. Each Wave 2/3 builder returns `True`
  iff it added a sheet.
* `apps/worker/app/api/export.py` — the
  `GET /deals/{id}/export/excel` endpoint. No signature change from
  W4.2; the endpoint streams the workbook with the right MIME type.
* `apps/worker/app/export/fixtures.py` — the Kimpton fixture. W4.2
  added `comp_sales`, `str_forecast`, and `named_scenarios` blocks +
  expanded `segments_by_year` to 5 hold years.
* `apps/worker/tests/test_excel_wave2_3_sections.py` — 14 tests
  covering presence/absence of each conditional sheet, the 5×5 grid +
  color-scale rules, DSCR-breach cell comments, the 48-row STR
  forecast table, and the Cover section-list omission.
