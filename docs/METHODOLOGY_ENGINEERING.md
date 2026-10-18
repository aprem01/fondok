# Methodology — engineering reference

The client-facing `/methodology` page (`apps/web/src/app/methodology/page.tsx`)
describes live behaviour in plain language and, by test
(`apps/web/__tests__/methodologyClientCopy.test.tsx`), carries no engine field
paths, API routes, ticket ids, release dates, repository paths or model-vendor
names.

Everything that was removed from the client page lives in two places:

1. **The rendered engineering reference** —
   `apps/web/src/components/methodology/EngineeringMethodology.tsx`, served at
   `/admin/methodology-engineering` only when `NEXT_PUBLIC_SHOW_DIAG=true`
   (the same gate as `/diag`). It is the former `/methodology` page: every
   stage body, field path, endpoint, env var, reason code, the generated
   concept registry table and the lineage node vocabulary. Three unit tests
   (`methodologyMarkets`, `methodologyReasons`, `methodologyRenovationBudget`)
   render it and pin its wording.
2. **This note** — the index of engineering facts a client never needs.

## Pipeline (worker)

| Topic | Where |
| --- | --- |
| Parse | LlamaParse (when configured) with a PyMuPDF fallback; openpyxl / xlrd / python-pptx. Image-only PDFs → `error_kind=no_text`. |
| Classify | Router agent (fast model) emits `doc_type` ∈ OM, T12, PNL, PNL_MONTHLY, PNL_YTD, STR, STR_TREND, STR_SEGMENTATION, CBRE_HORIZONS, PNL_BENCHMARK, PORTFOLIO_PNL, PARTNERSHIP, RENT_ROLL, ROOM_MIX, MARKET_STUDY, CONTRACT, INSURANCE, PROPERTY_TAX, CAPEX, PROPERTY_INFO, LEASES, SURVEYS, DEBT, OTHER. Filename heuristic fallback. |
| Extract | Extractor agent loads a per-doc-type schema from `apps/worker/app/agents/extraction_schemas/`; rows are `ExtractionField {field_name, value, unit, source_page, confidence, raw_text}`. |
| Reconcile | USALI Summary-sheet reconciler (5% tolerance); cross-statement revenue floor caps confidence at 0.50. |
| Verify | Critic pass: verified fields floor at 0.98, mismatches drop to 0.50. |
| Reclassify | Reads `p_and_l_usali.period_type` → PNL_MONTHLY / PNL_YTD / T12. |
| Stuck jobs | `EXTRACTION_STUCK_MINUTES` (default 30) → `error_kind=timeout`, audit `document.extraction_timed_out`; crashes → `error_kind=cancelled`. |
| Re-run | `POST …/extract` (extracted) / `POST …/reprocess` (failed); audit `document.re_extract_requested`; never reuses the content-hash cache. |
| Duplicate | `POST /deals/{id}/duplicate`; audit `deal.duplicated`. |

## Engines

Outputs persist via `model_dump_json()` without `exclude_none`; the web reads
`GET /deals/{id}/engine_outputs`. Stale detection (`run_freshness.py`) reports
`stale_run.missing_blocks: {engine: [path]}`; the web maps every block to a
product label in `apps/web/src/lib/fieldLabels.ts` (`ENGINE_SECTION_LABEL`,
`ENGINE_TAB_LABEL`) and never prints the dotted path.

Key fields named on the old page: `revenue.years[i].adr`,
`expense.years[].noi_institutional` (registry concept `ebitda`),
`expense.years[].noi` (concept `noi`), `expense.line_methods`,
`expense.stabilization`, `mgmt_fee_pct`, `incentive_mgmt_fee_pct_of_gop`,
`exit_noi_basis`, `terminal_noi_override`, `returns.exit_noi_period_label`,
`y1_occupancy_displacement_pct`, `y1_adr_displacement_pct`,
`renovation_displacement_schedule`, `assumption_sources.displacement_status`,
`capex_plan.pip.total_usd`, `capex_plan.document.plan_date`,
`broker_proforma.renovation_budget_usd`, `historic_capex.*`, `future_capex.*`,
`doc_subtype ∈ {pip, historic, future}`, `noi_override_by_year`,
`field_overrides.worksheet_layout`, `target_irr`, `target_moic`,
`target_stabilized_yoc`, `return_profile`, `reopening_date`,
`default_year_3_reopening`, `str_market_id`, `purchase_price`,
`property_overview.name`, `index_mpi_target_by_year`,
`index_ari_target_by_year`, `revenue.index_drivers_by_year`,
`projection_calendar_years`, `returns.inputs.assumptions`,
`capital.equity_amount`, `returns.cash_flows`, `returns.noi_by_year`.

## Endpoints referenced

`GET /deals/{id}/market-data` (`pnl_benchmark.categories`, `market_history`,
`str_trend`), `GET /deals/{id}/benchmark-suggestions`,
`GET /deals/{id}/renovation-budget-candidates`,
`POST /deals/{id}/engines/returns/preview`, `POST /analysis/{id}/pricing/max-price`
(422 when no target), `POST /analysis/{id}/pricing/max-price-grid`,
`GET /analysis/{id}/pricing/asking-vs-bid`,
`GET /deals/{id}/engines/investment-bridge`, `GET /deals/{id}/lineage`,
`GET /market/{id}/index-methodology` (`subject_period_end`, `cbre_report_date`),
`GET /ontology/concepts`, `/health` (`ontology_version`).
Override rule: `422 override_note_required`.

## Reason codes, registry, lineage

- Reason codes: `packages/schemas-py/fondok_schemas/reasons.py`, mirrored in
  `apps/web/src/lib/ontology/reasons.generated.ts`. The client page prints
  labels only; the engineering page prints `code (label)` via `ReasonTag`.
- Concept registry: `apps/worker/app/ontology/concepts.yaml`, generated into
  `apps/web/src/lib/ontology/concepts.generated.ts`; CI gate
  `scripts/gen_ontology.py --check`.
- Lineage node kinds: `kpi:`, `engine:`, `assumption:`, `line:` (reserved),
  `field:`, `doc:`, `page:`, `override:`, `seed:`, `benchmark:`, `memo:`.
- Regression baseline: `evals/baselines/kimpton-golden-2026-10-16.json`,
  `scripts/compare_golden_baseline.py` (see `docs/REGRESSION_BASELINE.md`).

## Decisions recorded by date on the old page

- Benchmarks are suggestions, not applied automatically (2026-10-15).
- Actual vs forecast chips on Index Analysis (2026-10-15).
- Stabilization anchor fallback to close when reopening Year 3 precedes the
  projection (2026-10-15).
- Budget-amount displacement heuristic (> $5,000/key → −15% occupancy, −8% ADR)
  removed October 2026.
- Positioning source columns added 17 October 2026; earlier deals may carry a
  wizard-filled tier labelled as the analyst's.
