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

## 2026-11 re-baseline: pending

Prem approved ONE Kimpton re-baseline for branch `w5/topline`. The engine
fixes below move the golden on purpose; the baseline file is cut once, by the
topline task on the same branch, covering every move listed here. Each moving
commit carries the `golden: expected` trailer (`scripts/check_golden_gate.py`).
Values are `scripts/compare_golden_baseline.py` against
`evals/baselines/kimpton-golden-2026-10-16.json`, measured cumulatively after
each fix (so a fix's numbers include the fixes above it).

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
