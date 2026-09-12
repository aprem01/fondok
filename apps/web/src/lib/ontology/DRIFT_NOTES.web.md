# Web ↔ concept registry — drift notes (Phase 1.4)

The web consumers now read `concepts.generated.ts` (generated from
`apps/worker/app/ontology/concepts.yaml`, CI-gated by
`scripts/gen_ontology.py --check`) instead of their own alias / label maps.
The seam is `src/lib/ontology/adapters.ts`.

**Nothing Sam sees changes** except one sanctioned widening (§6). Every place
the registry and the old hand-written map disagreed, the OLD behaviour was
kept and is recorded below. The pre-change truth is pinned verbatim in
`apps/web/__tests__/fixtures/ontology/web_aliases_pre_registry.json` and
asserted by `apps/web/__tests__/ontologyAliases.test.ts`. **Fix the adapter,
never the fixture.**

Companion document: `apps/worker/app/ontology/DRIFT_NOTES.md` (§ references
below are to that file).

---

## 1. What each web map now reads

| Legacy map | Registry home | Adapter |
|---|---|---|
| `HistoricalsSection.buildHistYear` — 15 alias lists | `CONCEPTS[id].aliases`, flattened for `PNL_FAMILY` + `T12`/`PNL`/`PNL_MONTHLY`/`PNL_YTD` + `"*"` | `HISTORICALS_ALIASES` |
| `HistoricalsSection.deriveYearLabel` — 3 alias lists | `period_ending` / `period_type` / `period_label` | `PERIOD_ALIASES` |
| `HistoricalsSection.hasSubordinateNamespace` + the same 8 literals inside `actualsOnly` | `SUBORDINATE_NAMESPACES` | `isSubordinatePath` |
| `GroundedWorksheet.ROWS` keys (`overrideKey` / `reviewKey` / `metaKey` / `y1Read` / `y1Src` / `fmt`) | `CONCEPTS[id].bindings.worksheet` | `worksheetBinding` → `ws()` in `GroundedWorksheet` |
| `reviewState.histValue` switch | `bindings.worksheet.hist_key` | `HIST_KEY_BY_ROW` |
| `provenance.SOURCE_LABEL` | `SOURCES[id].label` | `SOURCE_LABEL_FROM_REGISTRY` |
| `provenance.GROUNDED_SOURCES` / `OVERRIDE_SOURCES` | `SOURCES[id].kind` + a 5→3 colour map | `SOURCE_REGISTRY_KIND` |
| `AssumptionBadge.SOURCE_META[*].label` | `SOURCES[id].badge` | `SOURCE_BADGE_FROM_REGISTRY` |
| `api.AssumptionSource` (12 labels) | `SourceId` (18) | direct type alias |
| `HistoricalsSection.derivePeriodBasis` — the period-basis resolver (FON-41 #4) | `registry._doc_scope` = `period_types` rank map + `_DOC_DEFAULT_SCOPE` | `PERIOD_TYPES` (generated) + `docDefaultScope()` inline; the worker half is pinned by `apps/worker/tests/test_doc_scope_pnl_family.py` |

Row ORDER, section grouping, labels, `compute` functions, tones, icons and
tooltip copy stay hand-authored — they are layout and copy, not vocabulary.

---

## 2. `web_only_aliases` — paths the registry does not have

Kept inline in `adapters.ts` so nothing that resolved before stops resolving.
The registry builder should fold them into `concepts.yaml` in a follow-up;
then delete `WEB_ONLY_PERIOD_ENDING`.

| Concept | Alias | Where it came from |
|---|---|---|
| `period_ending` | `period_end` | `deriveYearLabel` (`HistoricalsSection.tsx:~287` pre-change) |
| `period_ending` | `statement_period_end` | same — the registry has `property_overview.statement_period_end`, which does NOT normalize to the bare tail |

Every other web alias — all 15 `buildHistYear` lists, 189 paths — is already
in the registry. `period_type` and `period_label` are exact matches.

---

## 3. Alias-set growth (additive; nothing removed)

Each derived list is a strict superset of the pinned one. Only these add a
NEW match key (i.e. can newly resolve a field that used to render "—"); the
rest of the added paths already normalized onto an existing key.

| Row | New match surface |
|---|---|
| `occ` | `actual_occupancy`, `broker_occupancy`, `occ_pct`, `occupancy_percent`, `ttm_performance.subject.occupancy(_pct)` |
| `adr` | `actual_adr`, `broker_adr`, `average_daily_rate_usd`, `ttm_performance.subject.adr(_usd)` |
| `revpar` | `ttm_performance.subject.revpar(_usd)` |
| `rooms` | `ttm_summary_per_om.rooms_revenue(_usd)` |
| `fb` | `food_beverage_revenue_usd` |
| `rooms_dept` | `rooms_departmental_expenses` (plural) |
| `fb_dept` | `food_beverage_departmental_expenses` (plural) |
| `undistributed` | `undistributed_expenses_usd`, `p_and_l_usali.undistributed.total_undistributed_expenses(_usd)` |
| `gop` | `gross_operating_profit_usd`, `p_and_l_usali.gop_usd`, `ttm_summary_per_om.gop(_usd)` |
| `noi` | `net_operating_income_usd`, `t12_noi`, `actual_noi`, `broker_noi`, `ttm_summary_per_om.noi(_usd)`, `p_and_l_usali.net_operating_income.{net_operating_income_usd,total,total_usd}`, `p_and_l_usali.income_statement.{noi,net_operating_income}(_usd)` |

`misc`, `property_tax`, `insurance`, `mgmt_fee` and `other_dept` are
byte-identical to the hand-written lists.

`findField` is order-insensitive (it iterates FIELDS against an alias **Set**),
so registry alias ORDER cannot change which field wins — only membership can.

---

## 4. `other_revenue` vs `misc_revenue` — the Misc. Income collapse

Worker §3.2. The registry keeps two concepts; Historicals has one
"Misc. Income" column, and `other_revenue.bindings.worksheet.hist_key = misc`
records the collapse. Per the handover the adapter must not pick one and drop
the other, so **`HISTORICALS_ALIASES.misc` is the UNION of both concepts'
alias lists** — exactly what the hand-written list was. Splitting the row into
two is a product decision, not a refactor.

Consequence to keep in mind: the worksheet's `total_rev` row still sums
`rooms + fb + other` (three rendered columns), while the registry's
`total_revenue` identity is `rooms + fb + other + misc + resort`. The
worksheet's "other" column already carries misc, so the arithmetic agrees;
`resort_fees` is not a worksheet row at all.

## 5. "Fixed charges" — the UI subtotals are not the concept

Worker §3.3. `HistYear.fixed_expenses` = `property_tax + insurance +
mgmt_fee`; the worksheet's `fixed_total` row = `mgmt + ffe + taxes +
insurance`; the registry's `fixed_charges` concept is `property_taxes +
insurance + rent_expense + other_nonop_expense − nonop_income`. The registry
binds `fixed_charges` to the `fixed_total` ROW and to the
`fixed_expenses` hist key (so the cell reads the same number it always did),
but the composition stays the web's — these are UI subtotals, unchanged here.

---

## 6. Provenance — labels widened, colours and copy pinned

### 6a. Labels + badges: exact match, then widened (the sanctioned change)

All 12 long labels (`SOURCE_LABEL`) and all 12 badge strings
(`AssumptionBadge`) the web hand-maintained match the registry byte-for-byte,
so those are a pure re-source.

`api.AssumptionSource` was 12 labels while the worker emits 18. The six it did
not know fell back to `SOURCE_META.seed` — a PIP read off the OM badged as
**"Seed"** (worker §3.12). They now badge as themselves:

| Source | Was | Long label | Badge |
|---|---|---|---|
| `str_segmentation_default` | Seed | STR segmentation | STR Seg |
| `pip_om` | Seed | PIP (OM) | PIP OM |
| `pip_user` | Seed | PIP (analyst) | PIP |
| `capex_ffe_default` | Seed | FF&E default | FF&E default |
| `roi_user` | Seed | ROI capex (analyst) | ROI |
| `partnership_doc` | Seed | Partnership document | Partnership |

Their hover/tooltip copy is the registry's `explanation` (the web has none),
and their badge tone/icon are hand-assigned in `AssumptionBadge` alongside the
other twelve.

### 6b. Explanation copy — WEB WINS (10 disagreements)

`sourceExplanation` keeps the shipped web wording; the registry's longer,
worker-oriented text is not adopted. `seed` and `str_forecast_unavailable`
already match and fall through to the registry.

| Source | Web (kept) | Registry (not adopted) |
|---|---|---|
| `t12_actual` | Extracted from the deal's T-12 actuals. | …T-12 / P&L actuals. Out-years are grown forward at the configured growth rates. |
| `deal_row` | Entered on the deal record. | …(create-deal wizard or a PATCH via the API). |
| `om_comps` | From the offering memorandum's comparable set. | Median cap rate derived from the OM's comparable-sales table. |
| `om_broker` | From the broker's pro forma in the OM. | …the broker's claim, not an actual. |
| `portfolio_pnl` | From your portfolio P&L library. | …hotels the firm already operates at this chain scale. Outranks generic benchmarks. |
| `str_forecast` | From the STR / comp-set forecast. | Seeded from STR — comp-set rates, subject TTM, or BASE Month-12. |
| `cbre_horizons` | CBRE Horizons market benchmark — not this deal's own data. | CBRE Horizons market forecast for the subject submarket / chain scale — … |
| `pnl_benchmark` | Industry (USALI/HOST) benchmark — not this deal's own data. | …ratio applied as a USALI ratio override — … |
| `analyst_override` | Set by an analyst with a justification note. | …Wins over every other source. |
| `derived_from_revpar_growth` | Derived from the analyst's RevPAR-growth override: ADR growth = … | ADR growth derived from the analyst's RevPAR-growth override — … |

### 6c. AssumptionBadge tooltips — WEB WINS (all 12)

The badge tooltip is separate, longer copy than `sourceExplanation` and
differs from the registry for every one of the twelve. It is kept verbatim.

The one to know about: the **seed** tooltip still reads
*"Kimpton fixture default — no deal-specific data has overridden this yet…"*.
Worker §3.13 decided the registry should use the `provenance.ts` wording
because the Kimpton demo fork was removed 2026-08-28. Adopting it here is a
copy change, and `/methodology` (§ "Seed") quotes the Kimpton line verbatim —
so both must move together, in a change that owns `methodology/page.tsx`
(out of scope for this phase, which may not touch that file).

### 6d. Kind disagreements — WEB WINS

Registry `kind` (5 values) → the web's 3-colour `SourceKind`:
`grounded → grounded`, `override → override`, `calculated → override`,
`assumption → benchmark`, `refusal → benchmark`.

Two of those mappings are themselves recorded disagreements, both resolved in
the web's favour and both no-ops for the rendered colour:

* `derived_from_revpar_growth` — registry `calculated`, web `override`
  (FON-69: a formula over an analyst override is analyst intent). Worker §3.10
  explicitly leaves the colour to the UI. Both render blue.
* `str_forecast_unavailable` — registry `refusal`, web `benchmark`: the value
  shown fell back to the T-12/seed base, so it reads as an assumption.

The six previously-unknown labels are PINNED to `benchmark`
(`LEGACY_UNCLASSIFIED` in `provenance.ts`) — they have always fallen through
to it. Adopting the registry would move:

| Source | Registry kind → web kind | Effect if adopted |
|---|---|---|
| `pip_om` | grounded | dot turns green; Ledger "Grounded" count +1 |
| `partnership_doc` | grounded | dot turns green; Ledger "Grounded" count +1 |
| `pip_user` | override | Ledger "Override" count +1 (colour unchanged — both blue) |
| `roi_user` | override | Ledger "Override" count +1 (colour unchanged) |
| `str_segmentation_default` | assumption → benchmark | no change |
| `capex_ffe_default` | assumption → benchmark | no change |

Flipping them is one line (remove the id from `LEGACY_UNCLASSIFIED`) and a
product decision, not a refactor.

---

## 7. Subordinate namespaces — the web enforces 8 of 17

`SUBORDINATE_NAMESPACES` is the union across every resolver (worker §3.6).
`isSubordinatePath` filters it down to the eight the web has always rejected:
`monthly`, `page`, `per_month`, `quarterly`, `q1`–`q4`.

Not enforced on the web (`WEB_SUBORDINATE_NAMESPACES` skips them):
`ytd`, `weekly`, `daily`, `mtd`, `qtd`, `prior_year`, `day_of_week`,
`forecast`, `budget`.

Turning them on would BLANK cells that render today (a `.ytd.` slice currently
resolves as if it were the period total), which this phase must not do.
`forecast` / `budget` are already stripped upstream by `actualsOnly`, so those
two are a no-op in the real path and only matter to a direct `findField` call.
Widening = delete the `LEGACY_WEB_SUBORDINATE` filter in `adapters.ts`.

Matching semantics are byte-identical to the literals they replace: every
namespace matches as a whole dotted segment (`.monthly.`) except `page`, which
names a numbered segment and matches as a prefix (`.page5.`).

---

## 8. Small mechanical pins

* **`y1Read` must stay `undefined`, never `[]`.** `GroundedWorksheet` tests it
  for truthiness (`if (!r.y1Read) continue;` and the trace path), and an empty
  array is truthy. `ws()` omits the key when `bindings.worksheet.y1_read` is
  empty (`total_rev`, `undist_total`, `gop`, `fixed_total`, `noi`).
* **`fmt: 'currency'` is omitted.** It is the `RowDef` default, so omitting it
  keeps the row objects identical to the table they replace.
* **`total_rev` has no `hist_key`.** Its historical value is a SUM
  (`rooms + fb + misc`), not an extracted line, so it stays hand-computed in
  `reviewState.histValue`; every other row reads `HIST_KEY_BY_ROW`.
* **`worksheetBinding` throws on an unknown row id.** A renamed row fails the
  build instead of silently losing its `overrideKey` (which would silently
  make a Model-column cell read-only).
* **Templated alias paths are dropped** (`p_and_l_usali.{year}.adr_usd`,
  `str_segmentation.{n}.overall.*`). `findField` is a literal matcher with no
  placeholder expander, and none were in the hand-written lists. They live on
  the `OM` / `STR_FAMILY` alias keys, which the P&L flattening does not read.

---

## 9. Not done here (follow-ups)

* Fold `period_end` / `statement_period_end` into `concepts.yaml` (§2).
* Decide the six `LEGACY_UNCLASSIFIED` kinds with Sam (§6d).
* Move the seed tooltip off "Kimpton fixture default" together with
  `/methodology` (§6c).
* The real fix behind all of this remains the worker route the
  `HistoricalsSection` comment has always pointed at:
  `GET /deals/{id}/historicals/normalized`, running `resolve_many`
  server-side, which would delete `findField` and these lists entirely.
