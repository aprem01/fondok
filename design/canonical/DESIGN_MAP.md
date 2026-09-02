# Design → code map

Each screen in `FONDOK-MVP-CANONICAL.dc.html` and the code that implements it.
Route pattern: `/projects/{id}?tab={key}` (Data Room is the default, no `tab`).
All components live under `apps/web/src/components/project/`.

| # | Prototype tab | Route `tab=` | Primary component | Notable sub-components |
|---|---|---|---|---|
| 1 | Data Room | *(default, `''`)* | `DataRoomTab.tsx` | `DocumentCoverage.tsx` |
| 2 | Overview | `overview` | `OverviewTab.tsx` | |
| 3 | Market | `market` | `MarketTab.tsx` | `STRForecastPanel.tsx` |
| 4 | Financials | `pl` | `PLTab.tsx` | `pl/GroundedWorksheet.tsx` (Historicals), `pl/ProjectionsSection.tsx`, `pl/IndexAnalysisSection.tsx`, `pl/PLReviewSection.tsx` |
| 5 | Investment | `investment` | `InvestmentTab.tsx` | `CapexPlanPanel.tsx`, `CostPanel.tsx` |
| 6 | Debt | `debt` | `DebtTab.tsx` | `DebtStackPanel.tsx` |
| 7 | Partnership | `partnership` | `PartnershipTab.tsx` | |
| 8 | Cash Flow | `cash-flow` | `CashFlowTab.tsx` | |
| 9 | Returns | `returns` | `ReturnsTab.tsx` | `MaxPricePanel.tsx`, `PricingSensitivityPanel.tsx` |
| 10 | Scenario Analysis | `scenarios` | `ScenarioAnalysisTab.tsx` | `ScenarioComparePanel.tsx`, `LiveScenarioBoard.tsx`, `ScenarioEditor.tsx` |
| 11 | IC Memo | `ic-memo` | `ICMemoTab.tsx` | `MemoStream.tsx` |

## Cross-tab data flow (keep the Base Case canonical)

The QA theme is that the Base Case must reconcile as it flows downstream. The
engine chain behind the tabs:

```
Financials / Investment / Debt  →  Cash Flow  →  Returns  →  Scenario Analysis  →  IC Memo
```

Worker engines: `apps/worker/app/engines/` (`revenue`, `expense`, `fb`,
`capital`, `debt`, `monthly_cashflow`, `returns`, `partnership`, `timeline`),
orchestrated by `apps/worker/app/services/engine_runner.py`. UI values on these
tabs read from engine outputs — **never** from prototype placeholder numbers.

## Not in the canonical MVP set

`Forecasting`, `Analysis`, `Activity`, `Export`, `Validation` tabs exist in the
codebase but are **not** among the 11 canonical prototype screens. For MVP,
IC Memo's external Excel/PDF/PPT export + secure sharing may stay **Coming Soon**
(disabled) if not production-ready (FON-54) — they must not block the core
in-app underwriting + IC workflow.
