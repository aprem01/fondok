# Design → code map

Canonical design bundle for the Fondok MVP (FON-72), vendored from Sam's
"VERSION 2 — Fondok design transfer" (Sep 2026). The index is
`FONDOK - MVP CANONICAL PROTOTYPE.dc.html`; open `Fondok Prototype (shareable).html`
for a self-contained render (no runtime needed). The `.dc.html` files render via
`support.js` / `deck-stage.js` in this folder.

**Route pattern:** `/projects/{id}?tab={key}` (Data Room is the default, no `tab`).
Components live under `apps/web/src/components/project/`.

| # | Prototype tab | Canonical `.dc.html` | Route `tab=` | Primary component |
|---|---|---|---|---|
| 1 | Data Room (+ Document Review) | `Data Room v2.dc.html` | *(default `''`)* | `DataRoomTab.tsx` |
| 2 | Overview | `Overview Tab v3.dc.html` | `overview` | `OverviewTab.tsx` |
| 3 | Market (+ Transaction Comps, Index Analysis) | `Market Tab.dc.html` | `market` | `MarketTab.tsx` |
| 4 | Financials | `Financials Tab.dc.html` | `pl` | `PLTab.tsx` (+ `pl/GroundedWorksheet.tsx`, `pl/ProjectionsSection.tsx`, `pl/IndexAnalysisSection.tsx`) |
| 5 | Investment | `Investment Tab.dc.html` | `investment` | `InvestmentTab.tsx` |
| 6 | Debt | `Debt Tab.dc.html` | `debt` | `DebtTab.tsx` |
| 7 | Partnership | `Partnership Tab.dc.html` | `partnership` | `PartnershipTab.tsx` |
| 8 | Cash Flow *(output-only)* | `Cash Flow Tab.dc.html` | `cash-flow` | `CashFlowTab.tsx` |
| 9 | Returns (+ Sensitivities, Pricing) *(output-only)* | `Returns Tab.dc.html` | `returns` | `ReturnsTab.tsx` |
| 10 | Scenario Analysis (+ New Scenario) | `Scenarios Tab.dc.html` | `scenarios` | `ScenarioAnalysisTab.tsx` |
| 11 | IC Memo | `IC Memo Tab.dc.html` | `ic-memo` | `ICMemoTab.tsx` |

> Canonical-version picks use the prototype's "latest supersedes" rule (v3 > v2 > v1).
> Confirm with Sam if any tab's definitive screen differs.

## Design-system references (cross-cutting)
- `Fondok Data Key.dc.html` — the provenance taxonomy (below).
- `Fondok Field System.dc.html` — field/input states + editability.
- `Fondok Component Kit.dc.html` — shared components.
- `Data Provenance System.dc.html` — how a value's lineage is shown.
- `Fondok Sub-Tabs.dc.html` — the sub-tab standard.
- `Fondok Engine Deck.dc.html` — engine explainer (supporting, not a tab).

## Canonical ownership model (from the prototype — one owner per assumption)
- **Financials** owns the operating model + operating assumptions.
- **Investment** owns acquisition, capitalization, exit. **No debt assumptions.**
- **Debt** owns financing + refinance structure → flows into Cash Flow and Returns.
- **Partnership** owns GP/LP allocation + waterfall.
- **Scenario Analysis** owns saved scenarios (explicit overrides vs Base).
- **IC Memo** owns analysis + export deliverables.
- **Cash Flow and Returns are OUTPUTS ONLY — no assumptions.**
- **Base Case is the source of truth and read-only from Scenario Analysis.**
  Prototype numbers are placeholders; implementation consumes canonical model
  outputs. (This is exactly the FON-73 / canonical deal-state work.)

## Data Key — provenance taxonomy (resolves FON-65 + FON-42b)
Six states every value carries: **Document sourced · Linked · Assumption ·
Calculated · Awaiting data · Needs review**. Belongs as a per-field tag on the
canonical deal-state object so every tab renders one consistent badge.

## Deliberate deviations from canonical (Linear overrides the design)
Per the conflict rule, the latest explicit Linear decision governs behaviour and
the vendored `.dc.html` files are never edited to match the app.

- **IC Memo — no Preview Memo action, 6-item readiness checklist (FON-54 §7,
  Sam 09-11).** `IC Memo Tab.dc.html` still shows `Preview Memo` / `✓ Preview
  reviewed` and the checklist item `IC memo previewed and reviewed`.
  `ICMemoTab.tsx` has neither: the button rendered no artifact — it only flipped
  a session flag implying an artifact had been reviewed — while IC Memo `.pdf`
  and Deal Presentation `.pptx` are Coming Soon for MVP. The checklist is 6
  items and `canMark` no longer requires the preview flag. `Configure IC memo`
  (format + six included sections) and the export cards are unchanged.
  **Restore when** PDF/PPT generation is enabled — bring Preview Memo back as an
  actual rendered preview, and only then the checklist item with it.

## Archive — obsolete, DO NOT implement from
`Data Room Redesign.dc.html`, `Overview Tab.dc.html`, `Overview Tab v2.dc.html`,
`OverviewCard.dc.html`, `Financials Tab Options.dc.html`,
`Financials Tab Merge Concept.dc.html`, `IC Memo Tab v1.dc.html`,
`Fondok Suite.dc.html`, `Archive - Previous Iterations.dc.html`.

## Cross-tab data flow (keep the Base Case canonical)
```
Financials / Investment / Debt  →  Cash Flow  →  Returns  →  Scenario Analysis  →  IC Memo
```
Worker engines: `apps/worker/app/engines/`, orchestrated by
`apps/worker/app/services/engine_runner.py`. Tab values read from engine outputs,
never from prototype placeholder numbers.
