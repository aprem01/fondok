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
