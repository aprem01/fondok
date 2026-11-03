# Regression baselines

Sam (2026-10-16): *"preserve the current Kimpton model outputs so we can
distinguish expected changes from unintended calculation discrepancies during
regression testing."*

There are two baselines. The live one covers real deals. The repo one is a
fixed reference that does not depend on any deal's documents.

## 1. Live deals: every earlier run is a baseline

`engine_outputs` is append-only. Each model run INSERTs one row per engine,
all tagged with the same `run_id`. No code path updates a finished row's
outputs, and rows are removed only when the deal is deleted. So the outputs a
deal showed before a change are still in the database under that earlier
`run_id`, and nothing extra has to be saved.

### Endpoints (read-only, tenant-scoped; another tenant's deal returns 404)

| Route | What it returns |
|---|---|
| `GET /deals/{id}/engines/runs[?before=<ISO-8601>]` | Every run on the deal, newest first: `run_id`, `started_at`, `completed_at`, `engines`, `status` (`complete` / `partial` / `failed` / `running`), `is_full_chain` (has all 8 base engines), `is_canonical` (the run the tabs are serving now) and `scenario` (set when a scenario's `last_run_id` points at the run). With `before`, `baseline_run_id` names the newest complete, full-chain run that finished before that instant and is not a non-base scenario's latest run. |
| `GET /deals/{id}/engines/compare?base=<run_id>&head=<run_id or latest>[&all=1]` | Field-level diff of the headline outputs. `head` defaults to `latest`, which is the canonical run (the run every tab serves). A deal whose runs never completed the full 8-engine chain has no canonical run, and `latest` then returns 404 saying so rather than quietly comparing against the newest partial run; pass a run id from the runs list to compare a partial run. Each row has `base_value`, `head_value`, `delta_abs` (head − base), `delta_pct` ((head − base) / \|base\|), and `base_provenance` / `head_provenance` (the engine's recorded state, source, formula and inputs for that field, when it records one). When a delta cannot be computed it is `null` and `delta_reason` says why: a zero base, or a field one run does not carry. Unchanged fields are left out unless you pass `all=1`. Two values count as unchanged within rel 1e-9 / abs 1e-6. |
| `GET /deals/{id}/engines/compare/xlsx?…` | The same comparison as an .xlsx workbook, with a Summary sheet and a "Changed fields" sheet. |

The headline fields:

- **Revenue, per year:** occupancy, ADR, RevPAR, rooms revenue, total revenue.
- **Expenses and NOI, per year:** total revenue; departmental expenses (rooms, F&B, other operated, total); undistributed expenses (A&G, sales and marketing, property ops, utilities, info and telecom, total); GOP; management fee; incentive management fee; fixed charges; NOI before the FF&E reserve (`noi_institutional`); FF&E reserve; Cash NOI after the reserve (`noi`).
- **Stabilization block:** year, basis, occupancy, ADR, revenue, NOI before and after the reserve, NOI margin.
- **Capital:** purchase price, total capitalization, equity, debt, renovation (total, base, contingency), senior loan fee, LTC, price per key.
- **Debt:** loan amount, rate, annual debt service, Year-1 / stabilized / average DSCR, Year-1 debt yield, and DSCR and debt service for each year.
- **Returns:** levered and unlevered IRR, equity multiple, exit value (gross sale price), net proceeds, exit NOI, exit cap, hold, peak equity, Year-1 cash-on-cash.
- **Partnership:** LP and GP IRR, equity multiple and distributions, and the GP promote.

The extractor lives in `apps/worker/app/services/run_compare.py`, and both
the endpoint and the repo script below use it.

### How Sam picks a baseline run on a live deal

1. Open the deal and go to the **Activity** tab. The **Compare runs** panel is
   at the top.
2. **Baseline = newest run before (UTC)** is prefilled with
   `2026-10-15 21:40`, the last deploy before the QA changes. The panel then
   selects the newest complete, full-chain run that finished before that
   time. Move the date to choose a different cut-off, or pick any run directly
   from **Baseline run**. Each entry shows its completion time, the first 8
   characters of its id, and tags such as `canonical`, `partial chain` or
   `scenario: <name>`.
3. **Head run** defaults to **Latest (canonical)**, the run every tab shows
   now. You can pick any other run instead. On a deal that has never
   completed a full chain there is no canonical run; the panel reports the
   404 instead of comparing against a partial run, and you pick the head run
   by hand.
4. The table lists every headline field that moved, grouped by engine. Tick
   **Show unchanged** to see every field. **Export .xlsx** downloads the
   table.
5. For each row, decide whether the change was expected from the change that
   shipped. A row nobody can explain is a calculation discrepancy.

### Tester deals: runs captured during QA on 2026-10-15

These runs were captured during QA on 2026-10-15. They are the last runs
before the QA changes on the two tester deals:

| Tester | Deal | Baseline run |
|---|---|---|
| Eshan | `9edf7d26…` | `b5a08654-52e9-4738-addc-eaf4e1019aae` |
| Rani | `a011fb54…` | `c3c372d4-17bd-4d66-8328-ea9981b937d3` |

The deals may have newer runs since then. The date default above picks the
newest qualifying run before 2026-10-15 21:40 UTC. If that is not the run
above, select the run above by hand.
Example: `GET /deals/<Eshan deal id>/engines/compare?base=b5a08654-52e9-4738-addc-eaf4e1019aae&head=latest`.

### Caveats

- A run is tagged as a scenario run from two sources: the scenario's
  **latest** run (`scenarios.last_run_id`) and, for every scenario run
  including older ones, the `engine_run.ran_with_scenario` audit row the
  scenario runner writes (`resource_id` = run id, `metadata.scenario_id`).
  So an older Downside run is skipped by the default baseline pick even
  after the scenario has been re-run. A run of a scenario deleted since is
  still tagged (shown as "deleted scenario", treated as non-base). The canonical-run
  resolver (FON-73) still reads only `last_run_id`.
- A run's status is folded over the eight canonical engines only. The
  composed `cash_flow` view is listed with the run, but a failed or
  unfinished view does not make a complete base chain `partial`, so such a
  run can still be the default baseline.
- A run made before an engine gained an output field shows that field as
  "not in baseline run" (`added`). That is a schema addition, not a value
  change.

## 2. Repo: the Kimpton golden baseline

`evals/baselines/kimpton-golden-2026-10-16.json` holds the **full** engine
outputs (all 9 engines, including provenance) of the Kimpton golden fixture.
It was generated at commit `f28470d` before any other change. The fixture is
the Kimpton Angler seed set, `apps/worker/tests/fixtures/kimpton_seed.py`
(`kimpton_seed()` plus the baseline's deal id `KIMPTON_FIXTURE_DEAL_ID`),
injected explicitly through `run_all_engines(..., assumptions=...)` — no deal
row, no documents, no overrides are read. It is run exactly as
`apps/worker/tests/test_engine_runner.py::test_run_all_engines_kimpton`
does. The run is deterministic: two runs at `f28470d` produced
byte-identical output, and the 2026-10-30 move of the seed set out of the
production loader (which until then applied these values to any deal lacking
its own, reached through the non-UUID demo id `kimpton-angler-2026`) was
verified byte-identical with `--full`.

The task named `tests/test_golden_set_regression.py` as the template for this
run. That suite pins *extraction* fixtures (parse signals and USALI field
resolution) and never runs the engine chain. The engine-chain Kimpton pins
are in `tests/test_engine_runner.py`, so that is the path used here.

```bash
# from the repo root: re-run the fixture and diff the headline fields
apps/worker/.venv/bin/python scripts/compare_golden_baseline.py
#   --topline-mode benchmark   FON-116 staged rollout: run with the FON-117
#                              topline switched on, against its own baseline
#                              (default growth_only = what deploys)
#   --all     also list unchanged headline fields
#   --full    also list every other raw numeric output that moved
#   --baseline <file>   compare against another baseline file
#   --write evals/baselines/kimpton-golden-<date>.json   cut a new baseline
```

The script is a report: it exits 0 when the comparison completed, with or
without differences, and exits 2 only when it could not run (broken venv,
failed migration, import error, missing baseline), so a caller checking the
exit code never mistakes a failed comparison for "no headline differences".
When a change is *intended* to
move Kimpton numbers, run it and list every changed value in the change
description. Then cut a new dated baseline with `--write`, and keep the old
file so the history stays readable.

## 2026-11-01 re-baseline: engine fixes (`kimpton-golden-2026-11-01-engfix.json`)

Prem approved the Kimpton re-baseline for the engine fixes and the topline
change. The engine fixes ship first, so this baseline covers them alone; the
topline change will cut its own dated baseline with its own entry below. Each
moving commit carries the `golden: expected` trailer
(`scripts/check_golden_gate.py`). Values are `scripts/compare_golden_baseline.py`
against `evals/baselines/kimpton-golden-2026-10-16.json` (kept for history),
measured cumulatively after each fix (so a fix's numbers include the fixes
above it). Headline moves (levered IRR 30.5078% → 31.2852%, unlevered IRR
19.6698% → 20.1489%, equity multiple 3.1731x → 3.2590x, exit value 73,335,054 →
74,797,035, cash NOI Years 2–5 +26.3K / +51.5K / +75.4K / +97.9K) all come from
fix 1a/1b; the management-fee, FF&E, interest-only and fee-in-lieu fixes move
no Kimpton headline (the fixture has no T-12, no IO period and no management
agreement) and only add leaves.

### Fix 1a: departmental expense = Year-1 cost ratio × own department revenue (Sam finding 2a)

Why: out-year Rooms / F&B / Other Operated expense grew the Year-1 dollars at
the 3.5% expense growth while each department's revenue grew at its own rate,
so the F&B cost ratio drifted 75.0% → 83.4% by Year 5 on the fixture. The
ratio is now fixed in Year 1 (the fixture has no T-12, so it is the
lifestyle hotel-type ratio: rooms 30%, F&B 75%, other 50%) and applied to every
year's department revenue. Year 1 does not move. Undistributed and fixed
charges keep their dollar growth.

| Headline field | Before | After |
| --- | --- | --- |
| Y2 dept. Rooms / F&B / Other | 4,388,782.93 / 2,507,875.96 / 475,451.48 | 4,445,264.66 / 2,442,453.11 / 481,570.34 |
| Y3 dept. Rooms / F&B / Other | 4,542,390.33 / 2,595,651.62 / 492,092.29 | 4,660,059.85 / 2,461,992.73 / 504,839.82 |
| Y4 dept. Rooms / F&B / Other | 4,701,373.99 / 2,686,499.42 / 509,315.52 | 4,885,233.94 / 2,481,688.68 / 529,233.68 |
| Y5 dept. Rooms / F&B / Other | 4,865,922.08 / 2,780,526.90 / 527,141.56 | 5,121,288.44 / 2,501,542.18 / 554,806.25 |
| Cash NOI Y2 / Y3 / Y4 / Y5 | 4,087,648.12 / 4,344,427.35 / 4,618,985.49 / 4,912,395.99 | 4,090,470.39 / 4,347,669.19 / 4,620,018.13 / 4,908,349.67 |
| Levered / unlevered IRR | 30.5078% / 19.6698% | 30.4867% / 19.6556% |
| Equity multiple | 3.1731x | 3.1702x |
| Exit value | 73,335,054.46 | 73,274,648.58 |

Also moves (same cause): departmental total, GOP and both NOI bases Y2–Y5,
the stabilization block NOI and margin, DSCR Y2–Y5 / stabilized / average,
net sale proceeds, exit NOI, every partnership headline, and the cash-flow
statement rows built from them. Non-numeric leaves: `expense.line_methods`
for the three departmental lines now read `pct_revenue` at the ratio (were
`growth` at 3.5%), and `expense.provenance` gains a trace per departmental
line for Years 2–5.

### Fix 1b: F&B $ per occupied room inflates at the ADR growth (Sam finding 2b)

Why: F&B revenue = occupied rooms × a $88.00-per-occupied-room figure held
flat for the hold, so F&B revenue grew only with occupancy (+0.8%/yr) against
4% ADR growth. The $ per occupied room now compounds at the deal's ADR growth
path from Year 1 (`fb_price_index`). Year 1 does not move. Through 1a, the
departmental F&B expense follows the higher F&B revenue at its 75% ratio.
Cumulative values (1a + 1b) against the 2026-10-16 baseline:

| Headline field | Before | After |
| --- | --- | --- |
| Total revenue Y2 / Y3 / Y4 / Y5 | 19,037,293.68 / 19,825,869.43 / 20,651,498.71 / 21,515,963.55 | 19,167,557.84 / 20,093,734.24 / 21,064,663.48 / 22,082,508.02 |
| Dept. F&B Y2 / Y3 / Y4 / Y5 | 2,507,875.96 / 2,595,651.62 / 2,686,499.42 / 2,780,526.90 | 2,540,151.23 / 2,662,891.34 / 2,791,562.25 / 2,926,450.54 |
| Management fee Y2 / Y5 | 571,118.81 / 645,478.91 | 575,026.74 / 662,475.24 |
| FF&E reserve Y2 / Y5 | 761,491.75 / 860,638.54 | 766,702.31 / 883,300.32 |
| Cash NOI Y2 / Y3 / Y4 / Y5 | 4,087,648.12 / 4,344,427.35 / 4,618,985.49 / 4,912,395.99 | 4,113,917.94 / 4,395,884.85 / 4,694,387.79 / 5,010,327.67 |
| NOI before reserve Y5 | 5,773,034.53 | 5,893,627.99 |
| Stabilized revenue / Cash NOI | 19,825,869.43 / 4,344,427.35 | 20,093,734.24 / 4,395,884.85 |
| Levered / unlevered IRR | 30.5078% / 19.6698% | 31.2852% / 20.1489% |
| Equity multiple | 3.1731x | 3.2590x |
| Exit value / exit NOI | 73,335,054.46 / 5,133,453.81 | 74,797,034.51 / 5,235,792.42 |
| Net sale proceeds | 49,645,085.87 | 51,077,826.32 |
| LP / GP IRR | 27.6159% / 41.4424% | 28.3142% / 42.4455% |
| GP promote | 3,504,227.86 | 3,675,179.31 |

Cash NOI Y2–Y5 moves +26.3K / +51.5K / +75.4K / +97.9K, matching the triage
estimate (+26K / +51K / +75K / +98K). Also moves: revenue `years[1..4]`
F&B and total revenue, every departmental and fee line Y2–Y5, GOP, both NOI
bases, DSCR Y2–Y5 / stabilized / average, stabilized margin, and the
partnership and cash-flow figures built on them.

### Fix 2: Year-1 management fee is contractual (Sam finding 4)

No golden move: the fixture has no T-12, so its Year 1 already ran at 3% ×
revenue. Live deals with an extracted T-12 management fee move: Year-1 fee
becomes `mgmt_fee_pct` × (displaced) Year-1 revenue, so Year-1 NOI and DSCR
move (Angler orig: 619,834 = 4.84% of revenue → 3%; copy 467,917 = 3.87% → 3%).

### Fix 3: the per-year debt-service series is always published (refi review P-2)

Why: without a refinance or a junior tranche `debt.debt_service_by_year` was
`[]`, so Returns charged the Year-1 payment every year while the sale repaid
the amortized balance: an interest-only-then-amortizing senior got principal
paydown without its amortizing payments (+104 bp levered IRR in the review
probe). The series is now always the senior's schedule (then the refinance
loan, plus juniors), and Returns, Cash Flow, DSCR, sensitivity and partnership
read it.

Kimpton's senior is level-payment, so no headline field moves. Leaves:
`debt.debt_service_by_year[0..4]` are added (`[]` → five values of
1,850,945.99423781xx, the schedule's per-year sums, which differ from the
Year-1 scalar in the last binary digit in Years 3–4), and
`sensitivity.matrices[7].cells[23].value` moves in the 16th significant digit
(3.4856628639456746 → 3.485662863945674). An IO-stub deal's Returns, Cash Flow
(the phantom "Refinance / Junior Debt Service" line for the IO-vs-amortizing
gap disappears) and partnership figures move to the schedule's payments.

### Round 2 (review C-1 / I-2): departmental ratio pairing, band and undisplaced basis

No golden headline move (the fixture has no T-12). Added leaves:
`revenue.years[*].occupancy_displacement_factor` / `adr_displacement_factor`
and `fb.years[*].{rooms,fb,other}_displacement_factor` (all 1.0 on the
fixture). Live T-12 deals move: a department's T-12 ratio now pairs one
statement's expense and revenue, sits inside `DEPT_RATIO_BAND` or is flagged
needs-review and falls back, and the dollar basis divides by undisplaced
Year-1 revenue.

### Round 2 (review I-3 / M-1): FF&E reserve is contractual in Year 1 too

No golden move (the fixture has no T-12). Live T-12 deals move: Year-1 FF&E
reserve becomes `ffe_reserve_pct` × (displaced) Year-1 revenue (Angler copy
480,135 = 3.98% → 4%; orig 539,269 = 4.21% → 4%). An analyst Year-1 fee or
reserve override is a fixed dollar figure, labelled as such.

### Round 2 (review I-1): fee terms from the hotel management agreement

No golden move (the fixture carries no documents). Live deals with an
extracted management agreement move: `mgmt_fee_pct` / `ffe_reserve_pct` take
the agreement's stated % (Angler copy: base fee 3.25% instead of the 3.0%
seed), badged `management_agreement`; an incentive fee with a hurdle (the
Angler's 15% over 10%) is disclosed as not modelled.

### Round 4 (review R3-I-1, R3-M-1, R3-M-2): fee in lieu, incentive basis, HMA evidence

No golden headline move (the fixture has no management agreement); the
headline set is identical to round 1. Added leaves:
`expense.years[*].mgmt_fee_in_lieu` (None on the fixture). Live Angler deals
move: a $76,000 "Management fee in lieu" line every year (flat — the HMA
states no escalation), so NOI before and after the reserve falls by $76,000
a year.

## 2026-11-02 re-baseline: topline methodology v2 (`kimpton-golden-2026-11-02-topline-v2.json`)

Prem approved the topline re-baseline (FON-116 / FON-117, plan
`docs/plans/go-live-2026-10-30/topline-growth-plan.md`; decisions from Sam,
2026-11-01). The moving commits carry `golden: expected`. The file now holds
**two fixtures** (`scripts/compare_golden_baseline.py` runs and diffs both):

* `engines` — the **seed fixture** (`tests/fixtures/kimpton_seed.py`, no
  documents). It mirrors the production defaults, which no longer carry a
  growth seed, so it pins the deterministic "no market forecast" state:
  every rate held flat, no benchmark, `revpar_growth` 0 for the exit year.
* `docs_fixture.engines` — the **documents fixture**
  (`tests/fixtures/kimpton_docs_fixture.py`): a database deal (132 keys,
  $36.4M, upper-upscale) carrying the May 2025 STAR report
  (`ANG-20250500-USD-E.xlsx` through the STR template extractor) and the
  Miami Beach CoStar submarket report, with the reference Index Analysis
  inputs as overrides and a 2025-12-31 close (Year 1 = 2026). It is new, so
  it has no "before".

Both runs record `topline_methodology_version` 2 (the header field
`topline_methodology`); the compare prints "TOPLINE METHODOLOGY CHANGED v1 ->
v2" against any earlier file. Values below are the seed fixture against
`kimpton-golden-2026-11-01-engfix.json` (kept for history): **105 headline
fields moved, 86 unchanged; Year 1 does not move** (the seed's 76.2% /
$385.00 Year 1 is unchanged — only growth is gone).

### Why each field moved

One cause: the 0.8% occupancy / 4.0% ADR / 4.5% RevPAR growth seeds are
retired. They were applied to every deal as if they were the market's; growth
now comes from the analyst or a market forecast and is otherwise unavailable
(held flat, with the 4.5% offered as a labelled suggestion that is never
applied for you). On the seed fixture that means:

| Headline field | Before | After | Why |
| --- | --- | --- | --- |
| Occupancy Y2 / Y3 / Y4 / Y5 | 76.81% / 77.42% / 78.04% / 78.67% | 76.20% every year | no occupancy growth |
| ADR Y2 / Y3 / Y4 / Y5 | $400.40 / $416.42 / $433.07 / $450.40 | $385.00 every year | no ADR growth |
| RevPAR Y2 / Y5 | $307.55 / $354.32 | $293.37 | the two above |
| Rooms revenue Y2 / Y5 | 14,817,548.86 / 17,070,961.47 | 14,134,566.60 | the two above |
| Total revenue Y2 / Y3 / Y4 / Y5 | 19,167,557.84 / 20,093,734.24 / 21,064,663.48 / 22,082,508.02 | 18,284,071.51 every year | rooms revenue; F&B price follows the (absent) ADR growth |
| Dept. Rooms / F&B / Other Y5 | 5,121,288.44 / 2,926,450.54 / 554,806.25 | 4,240,369.98 / 2,423,068.56 / 459,373.41 | departmental ratio × flat department revenue |
| Management fee / FF&E reserve Y5 | 662,475.24 / 883,300.32 | 548,522.15 / 731,362.86 | % of flat revenue |
| GOP Y2 / Y5 | 6,780,327.97 / 8,024,800.71 | 6,241,015.91 / 5,706,097.48 | flat revenue, undistributed costs still grow 3.5% |
| Cash NOI Y2 / Y3 / Y4 / Y5 | 4,113,917.94 / 4,395,884.85 / 4,694,387.79 / 5,010,327.67 | 3,636,449.92 / 3,417,877.56 / 3,191,655.17 / 2,957,514.99 | the same |
| NOI before reserve Y5 | 5,893,627.99 | 3,688,877.85 | the same |
| Stabilized occupancy / ADR / revenue | 77.42% / $416.42 / 20,093,734.24 | 76.20% / $385.00 / 18,284,071.51 | Year 3 of the flat path |
| Stabilized NOI / Cash NOI / margin | 5,199,634.22 / 4,395,884.85 / 25.88% | 4,149,240.42 / 3,417,877.56 / 22.69% | the same |
| DSCR Y2 / Y3 / Y4 / Y5, stabilized / average | 2.22x / 2.37x / 2.54x / 2.71x, 2.37x / 2.38x | 1.96x / 1.85x / 1.72x / 1.60x, 1.85x / 1.84x | Cash NOI over unchanged debt service |
| Exit NOI | 5,235,792.42 | 2,957,514.99 | Year-5 Cash NOI not grown: `revpar_growth` 0 (`market_forecast_unavailable`) instead of the 4.5% seed |
| Exit value / net sale proceeds | 74,797,034.51 / 51,077,826.32 | 42,250,214.15 / 19,181,942.37 | exit NOI ÷ 7.0% |
| Levered / unlevered IRR | 31.2852% / 20.1489% | 8.0602% / 7.4866% | flat operations and the lower sale |
| Equity multiple | 3.2590x | 1.3762x | the same |
| Exit-year rows 1–4 (levered IRR) | 88.22% / 51.22% / 40.35% / 35.07% | 52.75% / 22.37% / 13.05% / 8.11% | each row's forward NOI is flat |
| LP / GP IRR, GP promote | 28.3142% / 42.4455%, 3,675,179.31 | 7.7555% / 7.7555%, 0.00 | below the first hurdle, no promote |

Also moved (same cause): the per-year departmental / undistributed /
fixed-charge leaves, cash-flow statement rows, sensitivity grid cells and the
partnership distributions built on them. Unchanged: Year 1 everywhere, the
capital stack, debt sizing and debt service. New leaves: `revenue.topline_by_year`
records carry the topline fields (`benchmark_level` "none", growth source
"unavailable", `flat_no_growth`).

### The documents fixture (new) and the reference path

Year by year (2026–2030) the documents fixture projects occupancy 75.4959% /
75.4959% / 74.5899% / 76.8276% / 77.2118% and ADR $295.42 / $296.68 /
$311.09 / $323.03 / $329.50 — the reference Index Analysis tab's subject rows
(75.5% / 75.5% / 74.594% / 76.832% / 77.216%, $295.43 / $296.68 / $311.10 /
$323.04 / $329.50) to within the reference's own rounding of its keyed
comp-set level (75.496% → 75.5%, $310.776 → $310.78; every year differs by
exactly those two ratios — `tests/test_topline_kimpton_reference.py`). From the
keyed level itself the path matches to 1e-9. Headlines: levered IRR 8.4911%,
unlevered 7.7223%, equity multiple 1.4467x, exit value 46,224,062.47, exit NOI
3,235,684.37 (the expense side is still the seed's ratios — the fixture carries
no T-12, so it is a topline fixture, not the reference model's P&L).

### Also in this change

* `evals/golden-set/kimpton-angler/expected/model.json` (the 0.5%-tolerance
  eval, `evals/run.py`) does **not** move: it drives the engines directly with
  explicit inputs and never reaches the loader, so the plan's premise for
  retiring it (that it fails under v2) does not hold. It is kept; it does not
  exercise the topline.
* `tests/test_assumption_sources_snapshot.py` regenerated with the recorded
  reason (growth keys leave `sources` / `values`; `revpar_growth` 0.0
  `market_forecast_unavailable`).
* `tests/test_exit_schedule_runner.py` and `test_run_all_engines_kimpton`
  re-pinned / re-banded with the reason in each docstring.

### Re-cut 2026-11-02 (topline review fix I-2: the exit-growth convention)

The same file was re-cut in the commit that changed the exit-growth
convention (trailer `golden: expected`). The seed fixture does not move (0
leaves: it has no growth and no long-run figure, so its exit growth stays 0).
The documents fixture moves in the exit only, 16 headline fields / 414 raw
leaves, all from one cause.

Cause: Returns' exit-year growth (when the analyst set no RevPAR growth) was
the last projected year's `(1 + occupancy growth) × (1 + ADR growth) − 1`. On
CoStar data that is a single forecast year — on the defaults path 2029, a
recovery year (+3.0% occupancy, +2.8% ADR, 5.884%) — compounded for ever, and
mostly from occupancy. The convention is now the source's long-run average
(CBRE Hotel Horizons long-run RevPAR change, else its ADR change) when one is
on file, else the last projected year's ADR growth with occupancy held at its
stabilized level. The documents fixture carries no CBRE report, and its Year 5
(2030) is the reference tab's own 0.5% occupancy / 2.0% ADR, so exit growth
goes from 2.51% to 2.0%.

| Headline field (documents fixture) | Before | After | Why |
| --- | --- | --- | --- |
| Exit NOI | 3,235,684.37 | 3,219,586.44 | Year-5 Cash NOI × 1.020 instead of × 1.0251 |
| Exit value (gross sale price) / net sale proceeds | 46,224,062.47 / 23,076,313.72 | 45,994,092.01 / 22,850,942.67 | exit NOI ÷ 7.0%, less costs and payoff |
| Levered / unlevered IRR | 8.4911% / 7.7223% | 8.3050% / 7.6321% | the lower sale |
| Equity multiple | 1.4467x | 1.4352x | the same |
| Exit Year 5 (selected) row | 8.4911% / 7.7223% / 1.4467x / 23,076,313.72 | 8.3050% / 7.6321% / 1.4352x / 22,850,942.67 | the same |
| LP / GP IRR, equity multiple | 8.3012%, 1.4467x | 8.1185%, 1.4352x | the same (below the first hurdle) |
| LP / GP distributions | 25,523,359.44 / 2,835,928.83 | 25,320,525.50 / 2,813,391.72 | the same |

Also moved (same cause): the exit-year cash-flow rows, the partnership tier
allocation, and the sensitivity grids whose RevPAR-growth axis is centred on
the exit growth (columns 0.51%…4.51% → 0%…4%). Unchanged: every operating
year, the capital stack, debt, exit years 1–4.

Same commit series, no golden move: occupancy no longer repeats its last
forecast growth after a series ends (it holds; ADR carries) — the documents
fixture keys 2030 itself, so nothing is carried there.

## 2026-11-03 re-baseline: FON-116 staged rollout + decisions 1a / 1b (two dated files)

Prem approved the Kimpton re-baseline (wave 5). The domain lead (Sam) asked
for FON-116 (market growth by year) to deploy before FON-117 (benchmark ×
penetration × ramp) and decided how occupancy and ADR behave after
stabilization and after a forecast ends. The moving commits carry
`golden: expected`. While the rollout switch `TOPLINE_BENCHMARK_ENABLED`
exists (DEPLOY.md, "Topline rollout switch" — temporary) there are **two**
baselines, each with both fixtures, and CI checks both:

| File | Mode | CI step |
| --- | --- | --- |
| `evals/baselines/kimpton-golden-2026-11-03-growth-only.json` | `growth_only` (switch off — **what deploys**; `DEFAULT_BASELINE`) | `scripts/check_golden_gate.py` |
| `evals/baselines/kimpton-golden-2026-11-03-topline-v2-benchmark.json` | `benchmark` (switch on; `BENCHMARK_BASELINE`) | `scripts/check_golden_gate.py --topline-mode benchmark` |

The header records `topline_mode`; comparing a head run against the other
mode's file prints `TOPLINE MODE MISMATCH` and counts as a move. Every revenue
output records `topline_mode` beside `topline_methodology_version` (2 in both
modes). `kimpton-golden-2026-11-02-topline-v2.json` is kept for history (it
was never deployed).

### What changed and why

1. **Decision 1a — ADR after a forecast ends** grows at the arithmetic
   average of the series' forecast-tagged ADR years (CoStar Miami Beach
   Luxury & Upper Upscale 2025–2029: +4.4 / +1.8 / −0.6 / +3.8 / +2.8 →
   +2.44%), not the last year's +2.8%. The exit growth past the series uses
   the same figure. Neither fixture is moved by it: the seed fixture has no
   forecast, and the documents fixture keys 2030 itself (the reference tab's
   +2.0%). It is pinned in `tests/test_topline_decisions.py` and on the
   defaults path of `tests/test_topline_kimpton_reference.py`.
2. **Decision 1b — occupancy flat after stabilization**: growth into every
   year after the stabilization year is 0 (`held_at_stabilization`) unless
   the analyst set that year's own occupancy growth. On the documents
   fixture (stabilization Year 3 = 2028) CoStar's 2029 +3.0% occupancy is
   held; 2030's +0.5% is the reference tab's own per-year figure and still
   applies.
3. **The documents fixture carries the subject's T-12** (the occupancy / ADR
   / RevPAR rows of the real Anglers T-12 to May 2025, 82.8892% / $233.446:
   `evals/golden-set/kimpton-angler/input/t12_anglers_2025_05_extracted.json`).
   Without it the growth-only topline has no Year-1 basis on this fixture
   and refuses (`no_revenue_basis`). Revenue-basis rows only, so the expense
   side keeps its default ratios (it stays a topline fixture). In benchmark
   mode the T-12 is the reference row only and moves no number.
4. **Growth-only mode** (the deployed default): Year 1 is the subject's own
   basis, not benchmark × penetration × ramp; growth applies into Years
   2..hold.

### Seed fixture (no documents) — both modes

Identical in both modes and to `kimpton-golden-2026-11-02-topline-v2.json`:
**0 headline fields moved** (6 raw leaves in benchmark mode, 31 in
growth-only: per-year `topline_by_year` provenance — Years 4–5 now say
"occupancy held at its Year 3 stabilized level" instead of "no figure", and
the growth-only records carry no benchmark method/level). Against main's
current `kimpton-golden-2026-11-01-engfix.json` the seed fixture moves
exactly the **105 headline fields** of the 2026-11-02 entry above, for the
same reason (the 0.8% / 4.0% / 4.5% growth seeds are retired): occupancy
76.2000% and ADR $385.00 every year, Cash NOI Y2–Y5 3,636,449.92 /
3,417,877.56 / 3,191,655.17 / 2,957,514.99, levered / unlevered IRR 31.2852%
/ 20.1489% → 8.0602% / 7.4866%, equity multiple 3.2590x → 1.3762x, exit value
74,797,034.51 → 42,250,214.15, LP / GP IRR 28.3142% / 42.4455% → 7.7555% /
7.7555%, GP promote 3,675,179.31 → 0.00 (full table in the 2026-11-02 entry).
Year 1, the capital stack, debt sizing and debt service do not move.

### Documents fixture — growth-only (the deployed mode)

New in this mode (main's engfix file has no documents fixture). Year 1 =
the T-12 as reported; Years 2–5 grow by CoStar 2027–2029 (ADR −0.6% / +3.8%
/ +2.8%, occupancy 0.0% / −1.2% / held) and the tab's own 2030 figures
(+0.5% / +2.0%):

| Year | Occupancy | ADR | RevPAR | Total revenue | Cash NOI |
| --- | --- | --- | --- | --- | --- |
| 1 (2026) | 82.8892% | $233.45 | $193.50 | 13,443,261.50 | 2,330,314.45 |
| 2 (2027) | 82.8892% | $232.05 | $192.34 | 13,362,601.93 | 2,134,445.23 |
| 3 (2028) | 81.8945% | $240.86 | $197.25 | 13,703,936.23 | 2,145,549.85 |
| 4 (2029) | 81.8945% | $247.61 | $202.78 | 14,087,646.44 | 2,172,359.49 |
| 5 (2030) | 82.3040% | $252.56 | $207.87 | 14,441,246.37 | 2,178,191.78 |

Levered IRR −12.3984%, unlevered −0.6419%, equity multiple 0.5401x, exit
value 31,739,365.99, exit NOI 2,221,755.62, stabilized DSCR 1.1592x. (The
fixture's $36.4M price and default expense ratios against the subject's own
T-12 rates — the reference model prices the comp-set path, which is the
benchmark mode below.) Against the 2026-11-02 benchmark file the documents
fixture moves 158 headline fields, all from the mode (Year 1 T-12 82.89% /
$233.45 instead of comp set 75.50% / $295.42) plus 1b.

### Documents fixture — benchmark mode, against `kimpton-golden-2026-11-02-topline-v2.json`

**55 headline fields moved** (387 raw leaves), one cause: decision 1b holds
2029's +3.0% occupancy (Years 1–3 do not move, ADR does not move any year;
the T-12 reference row adds `t12_occupancy` / `t12_adr` leaves only).

| Headline field | Before | After | Why |
| --- | --- | --- | --- |
| Occupancy Y4 / Y5 | 76.8276% / 77.2118% | 74.5899% / 74.9629% | 2029 held at the 2028 level (÷ 1.03); 2030 +0.5% still applies |
| RevPAR Y4 / Y5 | $248.18 / $254.41 | $240.95 / $247.00 | the same |
| Total revenue Y4 / Y5 | 16,189,509.56 / 16,595,866.25 | 15,717,970.45 / 16,112,491.51 | −2.9126% |
| Dept. expenses total Y4 / Y5 | 6,567,032.98 / 6,731,865.50 | 6,375,760.17 / 6,535,791.75 | ratio × revenue |
| GOP Y4 / Y5 | 5,400,791.53 / 5,494,556.71 | 5,120,525.22 / 5,207,255.72 | undistributed costs do not fall |
| Management fee / FF&E reserve Y5 | 497,875.99 / 663,834.65 | 483,374.75 / 644,499.66 | % of revenue |
| Cash NOI Y4 / Y5 | 3,130,918.34 / 3,156,457.29 | 2,883,659.77 / 2,902,992.53 | the same |
| DSCR Y4 / Y5, average | 1.6915x / 1.7053x, 1.5708x | 1.5579x / 1.5684x, 1.5167x | NOI over unchanged debt service |
| Exit NOI / exit value | 3,219,586.44 / 45,994,092.01 | 2,961,052.38 / 42,300,748.35 | Year-5 NOI × 1.02, ÷ 7.0% |
| Net sale proceeds | 22,850,942.67 | 19,231,465.89 | the same |
| Levered / unlevered IRR | 8.3050% / 7.6321% | 4.6180% / 5.9100% | lower Years 4–5 and sale |
| Equity multiple | 1.4352x | 1.2250x | the same |
| Exit Year 3 / 4 rows (levered IRR) | 6.9168% / 7.5189% | 1.0980% / 2.9718% | their forward NOI is a held year |
| LP / GP IRR, equity multiple | 8.1185%, 1.4352x | 4.5156%, 1.2250x | below the first hurdle |
| LP / GP distributions | 25,320,525.50 / 2,813,391.72 | 21,612,345.40 / 2,401,371.71 | the same |

Unchanged: Years 1–3, every ADR, the capital stack, debt sizing.

### Reference tab (Sam's Index Analysis) under the decisions

`tests/test_topline_kimpton_reference.py`: with the default Year-3
stabilization the ADR path matches the tab to 1e-9 every year and
occupancy matches through 2028; from 2029 occupancy is the tab's ÷ 1.03
(2029 74.5940% vs 76.8318%, 2030–2032 77.2160% → 74.9670%). With
stabilization in Year 4 (2029, where her ADR penetration reaches 100) the
whole tab still reproduces to 1e-9.

