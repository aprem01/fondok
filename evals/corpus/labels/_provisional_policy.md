# Provisional labels — what they are, why, and how to confirm them

Snapshot at build time (2026-09-10): **598 labels — 38 confirmed / 560 provisional** across
20 cases (7 of them empty). `python evals/corpus/validate_corpus.py` prints the live table.

## Why a label is provisional

A label is provisional when a real value exists but nobody has signed it. Today that means
one of:

1. **Live extractor output, low confidence, unreviewed.** The 2026-09-09 capture holds every
   confidence-0.5 field on the four live statements with `reviewed: null`. The T-12 March 2025
   values were checked against the workbook cells and match, but matching is not confirming.
2. **Direct cell read.** A statement row the capture did not carry (for the 2024 workbook the
   capture carried field *names* only). The value is the workbook's own cell, cited by sheet
   and coordinate, so confirming it is a look-up, not a judgement.
3. **June-2026 stored payload.** `anglers_t12_real.json` / `anglers_annual_pnl_real.json` —
   real extractions, all 357 fields unreviewed. Used for the May-2025 Adjusted T-12 and for
   the 2023 monthly series; every value was checked against the workbook cell.
4. **OM transcription.** Page-40 (2023 and 2024 columns) and page-41 (Year One) figures other
   than the nine the audit confirmed; each is verified to appear on that page but the row
   attribution of a number found in flowing PDF text is what a human must check.
5. **STR sheet reads.** Subject and comp-set Occ/ADR/RevPAR from the `Glance_1` sheet of two
   monthly STAR reports. No extractor has run on these; cells are cited.
6. **Contract / plan figures.** HMA terms, business-plan goals, the 2025 capex plan and the 2025
   insurance projection. Correct as text, but they are not actuals and the basis enum has no
   `plan`/`budget`/`adjusted` value — they carry `basis: "broker"` with a note.

## Per-case state

| case | confirmed | provisional | what is confirmed | first thing to confirm |
| --- | ---: | ---: | --- | --- |
| anglers_t12_2025_03 (live T-12) | 6 | 38 | GOP, NOI, rooms revenue, occ, ADR, RevPAR (audit) | the identity chain: F&B / other / misc / total revenue; dept expenses + total; undistributed lines + total; mgmt fee; fixed-charge lines + total; EBITDA; FF&E |
| anglers_pnl_2024 (live) | 3 | 37 | occ, ADR, RevPAR (audit) | every Summary-sheet row (all direct reads): rooms → NOI |
| anglers_pnl_2023 (live + golden) | 8 | 135 | occ, ADR, RevPAR (audit + golden); rooms, total revenue, GOP, NOI, keys (golden) | the remaining annual rows (27); then accept the monthly series as a block if the annual totals reconcile |
| anglers_pnl_2019 (live) | 3 | 33 | occ, ADR, RevPAR (audit) | annual identity chain; 2019 is pre-pandemic and was marked "verify" by QA — check the period is really FY2019 |
| anglers_om | 9 | 71 | 2024-column occ/ADR/RevPAR/NOI/GOP/rooms; pro forma occ/ADR; keys (audit) | the 2024 column's remaining rows (actuals through Nov-2024 + Dec forecast, per footnote 3), then the 2023 column, then Year One |
| anglers_t12_2025_05_adj (golden, not on live deal) | 8 | 127 | rooms, total revenue, GOP, NOI, occ, ADR, RevPAR, keys (golden) | the annual page-4 rows; decide the basis for the page-5 *Adjusted T-12* figures |
| anglers_room_mix | 1 | 12 | keys = 132 (audit + cell C50) | the 12 room-type counts (they sum to 132) |
| anglers_insurance_summary | 0 | 12 | — | 2023 and 2024 totals — the 2024 figure disagrees with the 2024 P&L and the OM (see below) |
| anglers_capex | 0 | 9 | — | 2024 completed-projects total (507,200) and the 2025 plan total (251,000) |
| anglers_hma_summary | 0 | 8 | — | base fee 3.25% + $76,000 in-lieu, FF&E 4% |
| anglers_business_plan_2024 | 0 | 6 | — | low priority (plan targets) |
| str_ang_2025_05 / str_ang_2023_12 | 0 | 72 | — | Running-12 subject KPIs (they equal the T-12 / 2023 P&L KPIs, so confirming the P&L confirms these) |
| 7 empty cases | 0 | 0 | — | see manifest notes (no extraction, no registry concept, or no scope value) |

## Things the confirmation session must decide (not just check)

* **Miscellaneous Income.** `total_revenue = rooms + fb + other` only holds if `other_revenue`
  includes miscellaneous income. Labels keep them separate (`other_revenue` = Other Operated
  Departments, `misc_revenue` proposed). Registry decision needed.
* **NOI = "EBITDA: less Replacement Reserve".** Audit and golden set agree; the statement never
  prints an "NOI" row. Confirm this stays the definition (it nets the 4% FF&E proforma reserve).
* **Fixed charges.** `fixed_charges_total` is the statement row *Total non-operating income &
  expenses* (includes rent and non-operating income). Confirm, or split rent out.
* **OM "TTM" block is the 2024 column.** The extractor's `ttm_summary_per_om.*` values sit in
  the OM's *Year Ended December 31, 2024 (3)* column: actuals through November 2024 plus a
  December forecast. Labels use `scope: annual, period: 2024, basis: om_history`. Confirm that
  is how the variance engine should read it (it is *not* the same as the live T-12).
* **OM 2023 column restates the management fee.** The OM's 2023 column matches the 2023
  P&L to the dollar on revenue, departmental and undistributed lines, GOP and insurance, but
  shows management fees of 388,206 (3.0% of revenue, pro forma) against 404,604 actual and
  omits the −9,638 non-operating income, so its 2023 NOI is 1,964,064 vs the P&L's 1,957,310.
  Decide whether `om_history` labels should carry the broker's restated fee or the actual.
* **T-12 month labels.** The live extractor named Jan–Mar 2025 values `jan_2024`, `feb_2024`,
  `mar_2024`. The labels carry the column the value actually sits in; confirm the fix is a
  period-attribution bug, not a labelling choice.
* **2024 insurance.** Insurance Summary total 1,468,819 vs 2024 detailed P&L 1,474,459 vs OM
  1,461,690. Three seller documents, three numbers — pick the one the underwriting uses.
* **Seller-adjusted T-12 (May 2025, page 5).** Insurance 1,161,390 adjusted vs 1,392,610
  actual, mgmt fees 420,295 vs 650,353. Labelled `basis: broker`; registry may want `adjusted`.
* **Bases for plans / projections** (capex plan, insurance projection, business-plan goals) and a
  **weekly scope** for the weekly STAR report — enum additions for the registry builder.
* **Keys on the 2023 P&L.** The live extractor emitted `property_overview.keys = 132` at
  confidence 0.5; it is confirmed here via the audit and golden `usali.keys`. Fine, but note the
  extractor derived it (132 x 365 = 48,180 available rooms) — there is no keys cell.

## Two-hour confirmation session (Sam + Prem)

Goal: sign every identity participant on every statement — revenue components and total,
departmental expenses and total, undistributed lines and total, GOP, then the NOI chain —
so that the USALI identity rules (`REVENUE_SUM`, `DEPT_EXPENSE_SUM`, `GOP_IDENTITY`,
`NOI_IDENTITY`) can be evaluated against confirmed values, not extractor output.

Preparation (before the call): open the four live workbooks at the cited sheets/columns
(`T12!P`, `Summary!Z`, `P&L!O`), the OM at pages 40–41, and this file. Confirmations are
recorded by editing the label (`status`, `confirmed_by`, `confirmed_at`, `source`) and running
the validator; keep a running list of decisions from the section above.

| time | block | labels | what to sign |
| --- | --- | ---: | --- |
| 0:00–0:05 | Setup | — | Agree the definitions: NOI row, fixed-charges row, misc-income treatment. Everything below is faster once these are settled. |
| 0:05–0:35 | **T-12 March 2025** (live, drives the current model) | 27 annual + 11 monthly | Rooms / F&B / Other / Misc / Total revenue → Dept expenses (3) + total → Dept profit → A&G / IT / S&M / POM / Utilities + total → GOP (already confirmed) → Mgmt fee → Income before non-op → non-op lines + total → EBITDA → FF&E → NOI (confirmed). Then rooms sold / available rooms. |
| 0:35–0:55 | **2024 detailed P&L** | 37 | Same chain on `Summary!Z33:Z88`. Below-EBITDA rows (interest, depreciation, net income) only if time allows. |
| 0:55–1:10 | **2023 P&L** | 27 annual | Same chain; the four golden headline values are already signed. Monthly series: accept as a block once the 12 monthly totals reconcile to the annual total (they do in the workbook). |
| 1:10–1:20 | **2019 P&L** | 33 | Same chain, faster (single sheet). Confirm the statement is FY2019 actuals. |
| 1:20–1:35 | **OM pages 40–41** | 26 + 19 + 22 | 2024 column first (feeds variance vs the live T-12), then 2023 column (spot-check three rows against the 2023 P&L — they match to the dollar), then Year One pro forma GOP / NOI / mgmt fee only. |
| 1:35–1:45 | **May-2025 Adjusted T-12** | 25 annual + 7 property facts + 7 adjusted | Page-4 chain (the golden document). Decide the basis for page-5 adjusted figures. Monthly pages: accept as blocks. |
| 1:45–1:55 | **Static documents** | 12 + 12 + 9 + 8 | Room-type counts (sum = 132), insurance totals (resolve the 2024 discrepancy), capex 2024 actual + 2025 plan, HMA fee terms. |
| 1:55–2:00 | **Registry decisions** | — | Write down the enum/basis decisions above so the registry builder can finish `concept` mapping for the 44 proposed ids. |

If the session runs short, stop after the T-12 March 2025 and 2024 blocks: those two
statements are what the live deal's Historicals and variance flags are computed from.

## Recording a confirmation

```json
"status": "confirmed",
"confirmed_by": "Sam",
"confirmed_at": "2026-09-1x",
"source": "<existing source>; confirmed in the 2026-09-1x confirmation session"
```

If the confirmed number differs from the provisional one, change `value`, keep the old value
in `note` ("provisional value was …"), and — if it came from the live extractor — treat it as
an extraction defect to log, not just a label fix.
