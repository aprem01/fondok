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

- A scenario records only its **latest** run (`scenarios.last_run_id`). An
  older run of a non-base scenario looks like an ordinary run in the list.
  The canonical-run resolver (FON-73) has the same limit. When a deal has
  scenario activity, check the run's timestamp against the Activity feed.
- A run made before an engine gained an output field shows that field as
  "not in baseline run" (`added`). That is a schema addition, not a value
  change.

## 2. Repo: the Kimpton golden baseline

`evals/baselines/kimpton-golden-2026-10-16.json` holds the **full** engine
outputs (all 9 engines, including provenance) of the Kimpton golden fixture.
It was generated at commit `f28470d` before any other change. The fixture is
the Kimpton Angler seed: the non-UUID demo deal id `kimpton-angler-2026`
loads `_kimpton_assumptions()` defaults only, with no documents and no
overrides. It is run through `run_all_engines` exactly as
`apps/worker/tests/test_engine_runner.py::test_run_all_engines_kimpton`
does. The run is deterministic: two runs at `f28470d` produced
byte-identical output.

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

The script is a report and always exits 0. When a change is *intended* to
move Kimpton numbers, run it and list every changed value in the change
description. Then cut a new dated baseline with `--write`, and keep the old
file so the history stays readable.
