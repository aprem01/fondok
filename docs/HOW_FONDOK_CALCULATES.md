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

