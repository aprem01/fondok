---
id: regression-baselines
title: "Telling an intended change from a calculation drift"
decisions: []
detailed: none
anchors:
  - methodology-regression-baselines
---
<!-- section 9: Telling an intended change from a calculation drift — from page.tsx lines 1200-1221 -->
::: conceptual {title="Telling an intended change from a calculation drift"}
::: intro
Every model run is kept, so the numbers a deal showed before a change can always be put next to the
numbers it shows after — field by field, with the source of each side.
:::

::: card {testid=methodology-regression-baselines}
Each run is kept and never overwrites an earlier one. The **Compare runs** panel at the top of a
deal's Activity tab picks a baseline — by default the newest complete run before a date you choose —
and a head run, by default the run every tab is showing now. It lists each headline output that
moved, grouped by model: occupancy, ADR, RevPAR, revenue, expense lines, management fee, FF&E
reserve, gross operating profit and both NOI figures for each year; purchase price, total
capitalisation, equity and renovation; loan amount and DSCR by year; levered and unlevered IRR,
equity multiple, exit value and exit NOI; the selected exit month, the peak levered exit year and
each exit-year row's IRRs, equity multiple and net proceeds; the stabilization block; and LP and GP
IRR. Each row shows both values, the absolute change and the percentage change, with each side's
source. The same table exports to Excel. Comparing never re-runs or edits the model.

:::

:::

<!-- section 10: Telling an intended change from a calculation drift — from EngineeringMethodology.tsx lines 1122-1136 -->
::: engineering {title="Telling an intended change from a calculation drift"}
::: intro
Every model run is kept, so the numbers a deal showed before a change can always be put next to the
numbers it shows after — field by field, with the source of each side.
:::

::: card {testid=methodology-regression-baselines}
Each engine run writes a new row per engine and never overwrites an earlier one; only deleting the
deal removes them. The **Compare runs** panel at the top of a deal's Activity tab picks a
**baseline** — by default the newest complete run of the full engine chain that finished before a
date you choose, skipping every run recorded as a scenario run other than Base — each scenario run
leaves an audit entry naming its scenario, so older scenario runs are recognised too, not only the
latest (the Base scenario's runs, normally what every tab serves, stay eligible); when no run
finished before the default date — every run on a duplicated or newly created deal — the panel falls
back to the deal's newest complete run before the current one and says so beneath the pickers, while
a date you choose yourself never falls back — and a **head** run (by default the run every tab is
serving now). It lists each headline output that moved, grouped by engine: occupancy, ADR, RevPAR,
revenue, departmental and undistributed expenses, management fee, FF&E reserve, GOP and NOI before
and after the reserve for each year; purchase price, total capitalization, equity and renovation;
loan amount and DSCR by year; levered and unlevered IRR, equity multiple, exit value and exit NOI;
the stabilization block; and LP and GP IRR. Each row shows both values, the absolute change and the
percentage change, with the engine's recorded source and formula for each side. A percentage change
on a zero baseline, or a field one run does not carry, shows a dash and says why. The same table
exports to Excel. Comparing never re-runs or edits the model.

The repository also keeps a fixed reference: the golden reference fixture's full engine outputs as
of 16 October 2026 (`evals/baselines/kimpton-golden-2026-10-16.json`).
`scripts/compare_golden_baseline.py` re-runs that fixture on the current code and prints the same
field-level diff, so every change to a calculation can be checked against what it was expected to
move.

:::

:::

