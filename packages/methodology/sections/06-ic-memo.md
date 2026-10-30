---
id: ic-memo
title: "IC Memo — diligence flags and the decision"
decisions: [FON-54a, FON-59, FON-67]
detailed: none
anchors:
---
<!-- section 6: IC Memo — diligence flags and the decision — from page.tsx lines 1001-1075 -->
::: conceptual {title="IC Memo — diligence flags and the decision"}
::: intro
How broker-versus-actual variance flags reach the committee, what an NOI impact on a flag means, how
diligence status is kept, and why the recommendation is a recorded decision rather than a model
inference.
:::

::: card
- **One flag per business concept.** The variance report groups the broker's claims by concept — rooms
  revenue, F&B revenue, total revenue, occupancy, ADR, RevPAR, gross operating profit, NOI and each
  expense line — and the memo lists one item per concept with a readable title (“Rooms revenue —
  broker overstates T-12 by 5.3%”). The underlying rows stay available under Technical detail on each
  item and in the exported variance sheet.
- **“Estimated NOI impact” only when the delta is an NOI delta.** A dollar NOI impact is shown only
  for the NOI and gross-operating-profit concepts. A revenue, KPI or expense variance says that its
  NOI impact is not estimated rather than showing a dollar figure — Fondok does not translate a
  revenue delta into NOI without a flow-through assumption.
- **Diligence status is kept per concept.** Resolve, Accept variance and Reopen are recorded on the
  deal, so the IC-readiness checklist and the Excel export read the same status after a reload. Fondok
  never resolves a flag on its own.
- **The recommendation is a decision, not an inference.** The Model Assessment card shows the model's
  own read of the Base Case — Clears Hurdles, Clears with Conditions or Below Hurdles — labelled as
  the model's. The IC recommendation reads *Pending analyst decision* until you select Proceed,
  Proceed with Conditions or Do Not Proceed *and* confirm it. Only the confirmed verdict is written
  into the memo and the export header.

:::

::: card
#### What a variance flag compares — and what it refuses to

- **Only the broker's own claim is the broker side.** A figure from a T-12 or P&L is an actual, never
  a broker figure; a reading from an STR or market report is reported performance, not the broker's
  claim; a competitive-set statistic or the OM's historical block is neither. Rows excluded for one of
  these reasons are still listed under Technical detail with the reason.
- **Units are normalised before comparing.** Ratios are compared as fractions and currency in whole
  dollars. A value whose unit cannot be established is not compared, and a percentage claim is never
  read against a dollar line — Fondok does not reconstruct dollars from a percent whose denominator is
  itself an unverified broker figure.
- **Annual against annual.** The actual side is the document's annual or trailing-twelve-month line,
  never a monthly or quarterly slice, and the T-12 is preferred over a supporting P&L. Fondok never
  compares a month against a year.
- **Plausibility guard.** A pair of figures more than three hundred percent apart is reported as
  “Basis mismatch — needs review” with both figures and no severity: two numbers that far apart are on
  different bases, not evidence of an overstatement.
- **NOI is compared like for like across the FF&E reserve.** A broker pro forma states NOI before the
  reserve while the normalised actual is after it. When the statement states its own before-reserve
  line, the broker's claim is compared against that one and the row says so; when it does not, the
  pair is refused as a basis mismatch. The before-reserve figure is only ever read from the document,
  never reconstructed.

:::

:::

<!-- section 6: IC Memo — diligence flags and the decision — from EngineeringMethodology.tsx lines 881-933 -->
::: engineering {title="IC Memo — diligence flags and the decision"}
::: intro
How broker-vs-T-12 variance flags reach the committee, what an 'NOI impact' on a flag means, how
diligence status is kept, and why the IC recommendation is a recorded decision rather than a model
inference.
:::

::: card
- **One flag per business concept.** The extractor can emit the same broker line under several field
  paths (for example `broker_proforma.rooms_revenue_usd`, `broker.rooms_revenue` and a flat
  `rooms_revenue_usd`). The variance report groups those by their normalized concept — rooms revenue,
  F&B revenue, total revenue, occupancy, ADR, RevPAR, GOP, NOI, and each expense line — and the IC
  memo lists one item per concept with a business-readable title (“Rooms revenue — broker overstates
  T-12 by 5.3%”). The consolidated severity is the highest severity across the merged rows. The
  normalized keys, every raw field path, its own broker and T-12 values, and the USALI rule ids stay
  available under *Technical detail* on each item and in the Technical detail column of the exported
  Variance sheet.
- **“Estimated NOI impact” only when the delta is an NOI delta.** A dollar NOI impact is shown only
  for the NOI and GOP concepts, where the broker-vs-T-12 delta *is* the NOI difference. A
  revenue-line, KPI, or expense-line variance shows “Revenue-line variance — NOI impact not estimated”
  (or the expense / market-forecast equivalent) rather than a dollar figure — Fondok does not
  translate a revenue or expense delta into NOI without a flow-through assumption, and it never
  presents a revenue delta as if it were NOI.
- **Diligence status is persisted per concept.** Resolve, Accept variance and Reopen are recorded on
  the deal (`memo_diligence`, keyed by concept), so the IC-readiness checklist reads the same open /
  resolved status after a reload, and the Excel export's Variance sheet prints the same status in its
  Diligence column. A concept with no recorded status is Open. Status is analyst-recorded only —
  Fondok never auto-resolves a flag.
- **The IC recommendation is a decision, not an inference.** The Model Assessment card shows the
  model's own read of the Base Case (Clears Hurdles / Clears with Conditions / Below Hurdles, with the
  inferred verdict labelled as the model's). The IC recommendation reads *Pending analyst decision*
  until the analyst selects Proceed, Proceed with Conditions or Do Not Proceed *and* confirms it. Only
  the confirmed verdict is written into the memo's Recommendation section and the export header; a
  selected-but-unconfirmed verdict, or a verdict recorded before confirmation existed, still reads
  Pending analyst decision, under the reason code {{reason:awaiting_analyst}}. Confirmation is part of
  the IC-readiness checklist.

:::

::: card
#### What a variance flag compares — and what it refuses to

- **Only the broker's own claim is the “broker” side.** A row counts as the broker's claim about the
  subject only when it comes from the OM / broker materials under a claim path (`broker_proforma.*`,
  `broker.*`, `ttm_summary_per_om.*`, `ttm_performance.subject.*`). A line from a T-12 or P&L document
  is an actual, never a broker figure, even when the extractor labelled it with an OM-style path; a
  subject or submarket reading from an STR / CoStar report is STR-reported performance, not the
  broker's claim; a competitive-set stat (`ttm_performance.segment.*`), the OM's historical-year block
  (`p_and_l_usali.2021.*`), and lines from CBRE, CapEx, insurance or property-information documents
  are none of these either. Rows excluded for one of these reasons are still listed under Technical
  detail with the reason and their source document, each carrying {{reason:basis_excluded}} — the
  report says what it left out.
- **Units are normalised before comparing.** Occupancy and other ratio lines are compared as fractions
  (an extracted 83 or 83% reads as 0.83; the conversion is noted on the row); currency is compared in
  whole dollars (a “$000” unit is scaled). A value whose unit cannot be established — an occupancy
  above 100, for example — is not compared at all ({{reason:unit_unknown}}), and the Technical detail
  says why. A figure whose *path* declares a unit the concept does not carry is refused under the same
  code: an OM proforma's %-of-revenue column (`broker_proforma.rooms_revenue_pct`) read against a
  dollar T-12 line is rejected before any comparison, so it never produces a variance percentage.
  Fondok does not reconstruct dollars from a percent claim — the denominator would itself be an
  unverified broker figure.
- **Annual against annual.** The T-12 side is the document's annual / trailing-twelve-month line
  (annual rooms revenue, gross operating profit, net operating income), never a monthly or quarterly
  slice, and the T-12 is preferred over a supporting P&L. If no annual actual exists for a concept
  there is no flag — Fondok never compares a month against a year, and the report names the concept it
  could not compare with {{reason:period_mismatch}} or {{reason:no_source}}.
- **Plausibility guard.** As a last line of defence, a pair of figures more than 300% apart is
  reported as “Basis mismatch — needs review” ({{reason:basis_mismatch}}) with both raw figures and no
  severity, instead of a Critical variance: two numbers that far apart are on different bases, not
  evidence of a broker overstatement.
- **NOI is compared like-for-like across the FF&E reserve.** A broker proforma states NOI *before* the
  FF&E replacement reserve (USALI “EBITDA”), while the T-12 line Fondok normalises to is *after* it
  (USALI “EBITDA Less Replacement Reserve”). Comparing the two reports the reserve as if it were a
  broker overstatement. So when the actuals document states its own before-reserve line, the broker's
  NOI claim is compared against that one, and the row says which figure it used; when the document
  states only the after-reserve line, the pair is refused as “Basis mismatch — needs review”
  ({{reason:basis_mismatch}}) with both figures and no severity. The before-reserve figure is only
  ever read from the document — never reconstructed by adding the reserve back.

:::

:::

