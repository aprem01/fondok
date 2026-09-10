# Concept registry — drift notes (handover for the adapter builders)

`concepts.yaml` absorbs nine hand-maintained alias maps. This file records
(1) which registry field each legacy map now lives in, (2) every place two
of those maps disagreed and which side the registry took, and (3) what each
adapter must wire. Line numbers are as of the branch this landed on; `~`
marks an approximate anchor.

Nothing in this phase changes any consumer: `field_catalog.py`,
`usali_scorer.py`, `documents.py`, `variance.py`, `analysis.py`,
`engine_runner.py`, `HistoricalsSection.tsx`, `GroundedWorksheet.tsx`,
`reviewState.ts`, `provenance.ts`, `AssumptionBadge.tsx` are untouched. The
registry is the declared vocabulary plus a drift gate
(`scripts/gen_ontology.py --check` in CI); the tests in
`tests/test_ontology_registry.py` prove each legacy map round-trips through
it, which is what makes the adapter swap mechanical.

## 1. Where each legacy map now lives

| # | Legacy map | Registry home |
|---|---|---|
| 1 | `extraction/field_catalog.yaml` namespaces `t12_expense` (:24-60), `t12_revenue` (:63-96), `om_capital` (:99-116), `om_debt` (:119-134) | `bindings.field_catalog: {namespace, key, percentage_key}` + the paths under `aliases`. `period_type_rank` (:141-155) → top-level `period_types` (verbatim; test asserts equality). `percentage_keys` (:161-165) → `percentage_key: true` on `entry_cap_rate`, `interest_rate`, `ltv`. |
| 2 | `api/documents.py` `_canonical_key` (:7283-7310), `_ANNUAL_HINTS` (:~7313-7317), `_build` keys (:~7360-7400), FON-54a basis rules (:~7330-7350) | `bindings.critic_key`; annual preference = alias ORDER + `scope` on aliases + doc `period_type`; basis = `registry._basis_for` (explicit `broker_proforma.*`/`broker.*` → broker; year segment → `om_history` on broker material; `.segment.`/`comp_set.` → market; else by doc type). |
| 3 | `agents/variance.py` `_BROKER_RULE_BY_FIELD` (:160-190), `_normalize_field_key` (:191-198), `_actual_for` (:201-232) | `bindings.variance_rule`, `bindings.actuals_attr` (the `USALIFinancials` attribute path `_actual_for` reads, also the Normalizer envelope path). |
| 4 | `api/analysis.py` `_VARIANCE_CONCEPTS` (:181-201), `variance_concept` (:216-232) | `bindings.variance_concept: {key, label, impact_basis}`. The key is the namespace-stripped, unit-stripped tail `variance_concept()` produces. |
| 5 | `services/usali_scorer.py` `_ALIASES` (:127-~470), `_resolve_field` (:895), `_resolve_via_tokens` (:842), `_has_subordinate_namespace` (:694-705), `_TOKEN_RESOLVE_BLOCKLIST` (:~855-890) | `bindings.scorer_key` / `scorer_synonyms` / `scorer_variants` + every alias path; `subordinate_namespaces`; token match = resolver tier 6 (`allow_token_match=True`, default off — the blocklist becomes "the caller did not opt in"). `_evaluate` (:987) is reused unchanged by `identities.py`. |
| 6 | `services/structural_recognizer.py` `_CONCEPT_PATTERNS` (:205-419), `_STR_CONCEPT_PATTERNS` (:421-513), `_SUBORDINATE_TOKENS` (:517-520), `_is_subordinate_path` (:533-556) | `bindings.recognizer` (pattern name); the regex bodies are NOT copied — the recognizer keeps its own patterns until Phase 2 decides whether regex-on-tokens survives next to the alias tiers. |
| 7 | `web/.../HistoricalsSection.tsx` `buildHistYear` alias lists (:426-618), `findField` (:165-196), `hasSubordinateNamespace` (:139-151) | alias paths (all of them are in the registry); `bindings.worksheet.meta_key` = the `pick()` key; tier 4 of the resolver IS `findField`'s unit strip (`_?(usd|pct|percent|ratio|amount)$`). |
| 8 | `GroundedWorksheet.tsx` `ROWS` (:89-128) + `reviewState.ts` `histValue` (:84-110) | `bindings.worksheet: {row, hist_key, meta_key, review_key, override_key, y1_src, y1_read, fmt}` (`hist_key` = the `HistYear` field `histValue` reads). |
| 9 | Provenance: `provenance.ts` (:14-68), `api.ts` `AssumptionSource` (:86-88), `AssumptionBadge.tsx` `SOURCE_META` (:~140-230), `engine_runner.py` `SOURCE_*` (:250-314) + `_SOURCE_TO_DOC_TYPES` (:1894-1901), `fondok_schemas/provenance.py` label sets (:56-67) | top-level `sources: {label, badge, kind, doc_types, explanation, reason?}` — one entry per `SOURCE_*` constant (18). |
| — | `evals/golden-set/usali-rules.csv` (65 rules) | `usali_rules` per concept — validated to be EXACTLY the rules whose formula names one of the concept's scorer identifiers (a CSV edit that adds a term fails the load until the YAML follows). |
| — | `agents/extraction_schemas/*.md` field names | the canonical paths per doc type are all present as aliases; `doc_types` is the router's `DocType` enum (validated). |
| — | `agents/normalizer.py` synonym prompt (:40-100) | `bindings.actuals_attr` is the envelope path; the prose synonyms ("Rm Rev", "F&B Sales") are LLM guidance, not paths, and stay in the prompt. |

## 2. Concept-id choices

Where the engine (`field_catalog`) and the scorer name the same line
differently the registry id follows the ENGINE key; the scorer name is kept
in `bindings.scorer_key` so the scorer adapter is a lookup, not a rename.

| Registry id | field_catalog key | usali_scorer canonical | variance / analysis key | worksheet |
|---|---|---|---|---|
| `insurance` | `insurance` | `insurance_expense` | `insurance` | override `insurance`, hist `insurance` |
| `property_taxes` | `property_taxes` | `property_tax` | `property_taxes` | override `property_taxes`, hist `property_tax` |
| `utilities` | `utilities` | `utilities_expense` | — | override `utilities` |
| `sales_marketing` | `sales_marketing` | `marketing_expense` | — | override `sales_marketing` |
| `property_operations` | `property_operations` | `rm_expense` | — | override `property_operations` |
| `administrative_general` | `administrative_general` | `ag_expense` | — | override `administrative_general` |
| `dept_expenses` | — | `dept_expenses` AND `total_dept_expense` (two scorer canonicals aliasing each other, `usali_scorer.py:232-249`) | `departmental_expenses` | — |
| `fixed_charges` | — | `fixed_charges` | `fixed_charges` | row `fixed_total`, hist `fixed_expenses` |

Basis-qualified scorer canonicals (`broker_noi`/`t12_noi`, `broker_occupancy`/
`t12_occupancy`, `broker_adr`/`t12_adr`, `t12_revpar`; `usali_scorer.py:~430-440`)
are NOT separate concepts: they are `bindings.scorer_variants` on `noi`,
`occupancy`, `adr`, `revpar` with the basis they imply, and their alias
paths (`broker_proforma.noi`, `proforma_noi`, `t12.noi`, `actual_noi` …)
carry that basis explicitly. `t24_revpar` IS a separate concept
(`revpar_prior_ttm`) — a different period, not a different basis.

## 3. Disagreements recorded (side A / side B → registry decision)

1. **GOP alias shapes.** `documents.py:7286-7287` keys by last segment
   (`gross_operating_profit`, `gross_operating_profit_usd`, `gop`, `gop_usd`);
   `usali_scorer.py:~300-336` lists 15 dotted paths; `HistoricalsSection.tsx:565-578`
   lists a superset (adds `p_and_l_usali.gop.gross_operating_profit_usd`,
   `.gop.gop_usd`, `.gop.total_usd`, `.gross_operating_profit.total(_usd)`).
   The last-segment loader let a P&L's monthly `gop` shadow the T-12's annual
   `gross_operating_profit` (the FON-54a comment at `documents.py:~7255`).
   → Registry: every shape under `gop`; monthly slices excluded by scope, and
   the tail tier only matches BARE aliases — never a dotted alias' tail.
2. **`other_revenue` vs `misc_revenue`.** `HistoricalsSection.tsx:490-514` folds
   both into the single `misc` column ("Historicals collapses them");
   `usali_scorer.py:~176-196` keeps two canonicals; `field_catalog.yaml:88-96`
   keeps two keys; `GroundedWorksheet.tsx:101` row `other_rev` reads `h.misc`
   with `reviewKey: 'other_revenue'`. → Registry: two concepts.
   `other_revenue.bindings.worksheet.hist_key = misc` records the collapse;
   the worksheet adapter must either sum both concepts into the `misc`
   column or split the row — it must not pick one and drop the other.
3. **What "fixed charges" contains.** Scorer rollup = `property_tax + insurance`
   (`usali_scorer.py:~1620`) or the `non_operating` total alias; `HistYear.fixed_expenses`
   = `property_tax + insurance + mgmt_fee` (`HistoricalsSection.tsx:609-612`);
   `GroundedWorksheet.tsx:118` `fixed_total` = `mgmt + ffe + taxes + insurance`;
   `variance._actual_for("fixed_charges")` = `USALIFinancials.fixed_charges.total`
   (`variance.py:~229`); the Normalizer prompt (`normalizer.py:~78-83`) =
   `property_taxes + insurance + rent + other_fixed`. → Registry:
   `fixed_charges = property_taxes + insurance + rent` (USALI non-operating;
   `rent` optional), `mgmt_fee` and `ffe_reserve` stay separate, `noi = gop −
   mgmt_fee − ffe_reserve − fixed_charges` (matches NOI_IDENTITY). The web
   `fixed_expenses` / `fixed_total` composites are UI subtotals, not the concept.
4. **NOI ≡ EBITDA less replacement reserve.** `usali_scorer.py:~360-366` and
   `HistoricalsSection.tsx:611-614` alias `p_and_l_usali.ebitda_less_replacement_reserve(_usd|.total_usd)`
   to `noi`; the FON-41 T-12 also emits `p_and_l_usali.net_operating_income.ebitda`
   (EBITDA nested under an NOI parent). → Registry: the EBITDA-less-reserve
   paths stay under `noi` (USALI line "EBITDA Less Replacement Reserve"),
   `ebitda` is its own concept and owns `net_operating_income.ebitda`.
5. **`total_revenue` composition.** `documents.py:~7368-7380` `_build` sums
   rooms + fb + resort_fees + other (no misc); the scorer rollup
   (`usali_scorer.py:~1590-1600`) sums rooms + fb + other + resort + misc; the
   Normalizer prompt says "sum of the four" (rooms, fb, resort, other).
   → Registry identity: `rooms + fb + other + misc + resort` with `misc` and
   `resort` optional — the Angler's T-12 proves misc is a component
   (9,322,920 + 3,394,470 + 21,657 + 1,270,770 = 14,009,800 = `total_revenues_usd`).
6. **Subordinate namespaces.** `usali_scorer._has_subordinate_namespace` (:694-705)
   and `HistoricalsSection.hasSubordinateNamespace` (:139-151) = monthly / page /
   per_month / quarterly / q1-q4; `structural_recognizer._SUBORDINATE_TOKENS`
   (:517-520) adds `permonth` / `perquarter` + month-name segments + `.pageN.`;
   `variance._PERIOD_SLICE_TAGS` (:476-484) adds weekly / daily / ytd / mtd / qtd
   but NOT page / q1-q4 / per_month; the scorer's tail-write skip
   (`usali_scorer.py:~1462-1470`) covers only monthly / page / per_month — a
   `.quarterly.` tail still leaks into the flat dict there. → Registry:
   the union, plus `forecast`, `budget`, `prior_year`, `day_of_week`.
   Namespaces that name a period (monthly / quarterly / ytd) give that scope
   and are served when `want` IS that scope; the rest are never a total.
7. **`variance_rule` is not "the rule that tests the concept".**
   `_BROKER_RULE_BY_FIELD` (`variance.py:160-190`) flags `revpar` with
   `REVPAR_GROWTH_RANGE` (a YoY-growth rule), `rooms_revenue` / `total_revenue`
   with `BROKER_VS_T12_NOI_VARIANCE`, `fb_revenue` with `FB_DEPT_MARGIN_FULL`,
   `undistributed_expenses` with `A_AND_G_PCT_REVENUE`, `fixed_charges` with
   `INSURANCE_PER_KEY`. → Registry keeps these verbatim in
   `bindings.variance_rule` (what the agent uses to pick a severity band) but
   `usali_rules` lists only the rules whose FORMULA names the concept. The two
   differ on purpose; do not "fix" one to match the other.
8. **Rule ids that are not in the catalog.** `analysis.py:701,733` emit
   `BROKER_VS_CBRE_ADR_GROWTH` / `BROKER_VS_CBRE_REVPAR_GROWTH` for the
   growth-vs-market flags; they do not exist in `usali-rules.csv`, so they
   cannot be bound. The concepts `adr_growth_vs_market` /
   `revpar_growth_vs_market` carry the `variance_concept` binding only.
9. **Basis default on an OM.** `documents.py:~7335-7345` treats every non-claim
   path on an OM as the broker side; `variance._broker_fields_from_extraction(strict=True)`
   (:551-620) admits only claim paths (`BROKER_CLAIM_PREFIXES`, :436-441) or a
   known flat key, and only on broker material. → Registry default basis for an
   OM is `broker` (documents.py); strict admission is a filter the variance
   adapter applies on top (`resolve(..., basis="broker")` + its own path guard).
   `ttm_summary_per_om.*` / `ttm_performance.subject.*` are `"*"` aliases whose
   basis follows the document (actual on a T-12, broker on an OM, market on an
   STR report) — exactly FON-54a part 3.
10. **Provenance kinds.** `provenance.ts:14-49` — `deal_row` grounded,
    `cbre_horizons` / `pnl_benchmark` benchmark, `derived_from_revpar_growth`
    override; `provenance.py:56-67` — `deal_row` assumption, `pnl_benchmark`
    document_sourced, `cbre_horizons` assumption, `derived_*` unlisted.
    → Registry `kind`: `deal_row` grounded (it is the analyst's own entry),
    `pnl_benchmark` assumption (not this deal's data — provenance.ts wording),
    `cbre_horizons` assumption, `derived_from_revpar_growth` calculated (it is a
    formula over an override; the web colours it blue — the colour is the UI's
    call, the kind is not). `str_forecast_unavailable` is kind `refusal` with
    `reason: str_unavailable`.
11. **`_SOURCE_TO_DOC_TYPES` is incomplete.** `engine_runner.py:1894-1901` maps
    six labels; `portfolio_pnl`, `str_forecast`, `str_segmentation_default`,
    `pip_om` also come from documents. → Registry `sources[*].doc_types` fills
    them (`portfolio_pnl` → PORTFOLIO_PNL, `str_forecast` → STR / STR_TREND,
    `str_segmentation_default` → STR_SEGMENTATION, `pip_om` → OM).
12. **`AssumptionSource` is narrower than the worker.** `api.ts:86-88` lists 12
    labels; the worker emits 18 (`engine_runner.py:250-314`);
    `AssumptionBadge.tsx:57` falls back to `SOURCE_META.seed` for the six it
    does not know (`str_segmentation_default`, `pip_om`, `pip_user`,
    `capex_ffe_default`, `roi_user`, `partnership_doc`) — a PIP-from-OM value
    badges as "Seed". → Registry `sources` carries all 18 with the badge text;
    the web adapter should derive `AssumptionSource` from `SourceId`.
13. **Seed tooltip copy.** `AssumptionBadge.tsx:~148` says "Kimpton fixture
    default"; `provenance.ts:~92` says "A default assumption — no deal-specific
    data has grounded this yet". → Registry uses the provenance.ts text (the
    Kimpton demo fork was removed 2026-08-28).
14. **`purchase_price` ← asking price.** `field_catalog.yaml:80-83` maps
    `asking_price.headline_price_usd` / `asking_price.purchase_price` to the
    capital engine's `purchase_price`; `usali_scorer.py:~431` maps `price` /
    `deal.purchase_price`. → One concept (`purchase_price`, label "Purchase
    price (asking price)") — the OM's ask IS the engine's default price until
    the analyst overrides it.
15. **Bare aliases that collide with another concept's dotted tail.** The
    registry keeps every legacy bare alias, and the resolver's tail tier
    refuses a field whose full path is an exact alias of another concept. The
    full list (`registry.tail_collisions()`):
    `occupancy` / `occupancy_pct` / `occupancy_percent` ↔ `comp_set.occupancy`,
    `compset.occupancy_pct` (compset_occupancy); `adr` / `adr_usd` ↔
    `comp_set.adr`, `compset.adr_usd` (compset_adr); `revpar` / `revpar_usd` ↔
    `comp_set.revpar`, `compset.revpar_usd` (compset_revpar) and `t24.revpar`
    (revpar_prior_ttm); `food_beverage` (fb_revenue, from
    `HistoricalsSection.tsx:456`) ↔ `p_and_l_usali.departmental_expenses.food_beverage`,
    `p_and_l_usali.departmental_expense.food_beverage_usd` (fb_dept_expense) —
    the QA #2 misplacement `findField` pass-1 fixes; the registry fixes it at
    the tier level. `cap_rate` (entry_cap_rate, `field_catalog.yaml:~89`) ↔
    `transaction_comps.{n}.cap_rate_pct` (comp_cap_rate) is the same shape via
    the wildcard pattern.
16. **`keys` token-matching.** `usali_scorer._TOKEN_RESOLVE_BLOCKLIST` (:~857)
    bans token-resolving `keys`, `monthly_revpar`, the roll-up totals and the
    synthetic cross-field names. → Registry: token match is tier 6 and off by
    default for EVERY concept; the blocklist is subsumed by the opt-in.
17. **`t12_*` bare aliases on the web.** `HistoricalsSection.tsx:427,434,441,448`
    list `t12_occupancy`, `t12_adr`, `t12_revpar`, `t12_rooms_revenue` as plain
    aliases; the scorer treats the first three as variance canonicals.
    → Registry: `t12_occupancy` / `t12_adr` / `t12_revpar` are `"*"` aliases
    with `basis: actual` (and scorer variants); `t12_rooms_revenue` a plain alias.
18. **COMPARABLE_SALES is not a router doc type.** `extraction_schemas/comparable_sales.md`
    and `engines/comp_sales.py` exist, but `DocType` (`fondok_schemas/document.py:13-83`)
    has no such value — comps arrive on the OM as `transaction_comps.<n>.*`.
    → `doc_types` omits it; the comps concepts carry both path shapes.
19. **`_canonical_key` parent-qualified forms.** `documents.py:7290-7296`
    special-cases `rooms.revenue` / `fb.revenue` (parent + `revenue`). → The
    registry lists `p_and_l_usali.rooms.revenue(_usd)`,
    `p_and_l_usali.fb.revenue(_usd)`, `p_and_l_usali.food_and_beverage.revenue(_usd)`
    explicitly; the adapter can drop the parent logic.
20. **`_ANNUAL_HINTS` vs alias scope.** `documents.py:~7313` ranks paths by
    substring hints (`ttm_summary`, `operating_revenue.`, `annual`, `_ttm`,
    `trailing` …). → Registry: TTM/annual is a per-alias `scope` or a path
    hint (`registry._path_scope_hint`: TTM namespaces, `_ttm`, `trailing`,
    `annual`, a four-digit-year segment), and the doc's own `period_type` line
    scopes everything else. `operating_revenue.` is no longer a hint — it is
    simply listed first.

## 3b. Eval corpus input (`evals/corpus/`, 2026-09-10) — what was folded in

The corpus builder labelled 598 real fields against a hand-off id list
(`validate_corpus.py:REGISTRY_CONCEPTS`, "keep this list in sync with [the
registry]") and proposed 44 ids. Decisions:

**Added now (item 1).** `misc_revenue` (already present — see the decision
below), `income_before_nonop` (renamed from `income_before_non_operating`),
`nonop_income` (`p_and_l_usali.non_operating.income_usd`), `rent_expense`
(renamed from `rent`), `other_nonop_expense` (`p_and_l_usali.non_operating.other_usd`),
`rooms_dept_profit` / `fb_dept_profit` (already present), `noi_per_key`
(identity `noi / keys`), and the STR indices as `mpi`, `ari`, `rgi` (renamed
from `mpi_occupancy_index` / `ari_adr_index` / `rgi_revpar_index`; the
recognizer pattern names stay in `bindings.recognizer`). Also `closing_costs`
and `working_capital` — they are on the corpus's registry list and ARE
`CapitalEngineInput` fields (`engines/capital.py:47,124,154`), with no
extractor path today (bare aliases only).

**Enum gaps (item 2).** Basis gained `budget`, `plan`, `adjusted`; scope
gained `weekly`; `default_scope: null` is allowed only on `period: point`
concepts carrying a `note` (39 point facts converted; the loader enforces
it). Resolver: a `.budget.` / `.forecast.` / `.plan.` / `.adjusted.`
namespace gives that basis and is NOT a period slice (`registry._NAMESPACE_BASIS`);
`.weekly.` is a slice with scope `weekly`. The corpus labels that used
`basis: broker` "as the closest enum" for plan targets, the capex plan, the
2025 insurance projection and the page-5 seller-adjusted T-12 sheet should
move to `plan` / `budget` / `plan` / `adjusted` respectively.

**Decision — miscellaneous income.** `other_revenue` does NOT subsume
miscellaneous income. `other_revenue` = Other Operated Departments,
`misc_revenue` = Miscellaneous Income (USALI 11th ed.), and the registry's
`total_revenue` identity is `rooms + fb + other + misc + resort` (misc and
resort optional). Consequence for the catalog: `REVENUE_SUM`
(`usali-rules.csv:3`, `total − (rooms + fb + other)`) fails on any statement
that prints miscellaneous income — 9.1 % of revenue on the Angler's T-12 —
even though the scorer's own roll-up adds misc and resort
(`usali_scorer.py:~1590-1600`). Proposed CSV fix (not made here — the CSV is
live scoring input): `abs(total_revenue - (rooms_revenue + fb_revenue +
other_revenue + misc_revenue + resort_fees)) / total_revenue`.

**Decision — fixed charges.** The corpus reads `fixed_charges_total` as the
statement row *Total non-operating income & expenses* (rent and non-operating
income included). Adopted: `fixed_charges` identity is `property_taxes +
insurance + rent_expense + other_nonop_expense − nonop_income` (the last three
optional) and `p_and_l_usali.total_non_operating_income_and_expenses_usd` is
listed ahead of `total_non_operating_expenses_usd`. With that, the NOI chain
closes on the live T-12 (GOP 5,081,540 − mgmt 650,353 − FF&E 560,393 − fixed
1,922,240 = 1,948,554 vs the stated 1,948,560) — see
`test_identities_evaluate_on_the_real_t12`.

**Decision — NOI.** Stays "EBITDA less Replacement Reserve" (audit, golden
set and the two legacy resolvers agree); `ebitda` gets the identity
`income_before_nonop − fixed_charges`.

**Real-data facts (item 4).** (a) The OM's `ttm_summary_per_om.*` block is
the page-40 *Year Ended December 31, 2024 (3)* column — actuals through
November plus a December forecast. The corpus labels it `basis: om_history,
scope: annual`; `variance.BROKER_CLAIM_PREFIXES` (:436-441) admits it as the
broker's claim. Registry: OM-specific aliases with `basis: broker, scope:
annual` (the block is what the broker presents as the property's latest year,
and it is what the variance report compares to the live T-12), and the `"*"`
entries no longer assert `scope: ttm` — `ttm_summary_per_om` was removed from
`registry._TTM_SEGMENTS`; on a T-12 the path takes the document's period.
Record for the confirmation session: if Sam wants the 2024 column read as
history rather than claim, flip the seven OM entries to `om_history` — one
YAML edit, no code. (b) The live extractor labels the T-12's Jan–Mar 2025
columns `monthly.jan_2024` …: the resolver treats a month-name segment as
"a monthly slice" only and never derives WHICH month from it
(`registry._subordinate_scope`); `concept_for_path` returns scope `monthly`,
not a period. Period attribution stays with the corpus labels (`period`) and
is an extractor defect to log. (c) The OM's 2023 column restates the
management fee (388,206 pro forma vs 404,604 actual) — an `om_history` row is
the OM's restatement, not the P&L's actual; nothing to encode, noted.

**Id mapping the corpus should adopt** (the registry follows the engine's
canonical keys — `field_catalog.yaml` — where they exist):

| corpus id | registry id |
|---|---|
| `dept_expenses_total` | `dept_expenses` |
| `dept_profit_total` | `dept_profit` |
| `undistributed_total` | `undistributed_expenses` |
| `ag_expense` | `administrative_general` (scorer key `ag_expense` kept in bindings) |
| `it_expense` | `information_telecom` |
| `property_ops` | `property_operations` (scorer key `rm_expense`) |
| `property_tax` | `property_taxes` (scorer key `property_tax`) |
| `fixed_charges_total` | `fixed_charges` |

**Recorded as proposed, not added (item 3)** — no live consumer names them
yet; add when a screen or engine reads them:
room mix `room_type_count` (12 labels; `ROOM_MIX` docs); owner capex
`capex_plan_total` / `capex_actual_total` (`CAPEX`; distinct from the buyer's
`renovation_budget`); insurance `property_insurance_premium`,
`manager_gl_premium` (`INSURANCE`); HMA terms `base_mgmt_fee_pct`,
`mgmt_fee_in_lieu_usd`, `incentive_mgmt_fee_pct`, `incentive_fee_hurdle_pct`,
`ffe_reserve_pct`, `performance_test_gop_pct`, `performance_test_rgi_pct`,
`hma_term_end_date` (`PROPERTY_INFO`; unit `pct` / `usd` / `date`);
business-plan `group_revpar` / `transient_revpar` (basis `plan`); property
facts `operator`, `owner`, `management_structure`, `open_date`, `str_id`,
`str_market_class`, `keys_historic`, `keys_tower`, `building_area_sf`
(`brand` and `parking_spaces` already exist; `property_overview.str_market_class`
is currently an alias of `property_type` and `property_overview.open_date` is
unmapped); below-EBITDA lines `interest_expense`, `depreciation`,
`amortization`, `ida_total`, `pre_tax_income`, `income_taxes`, `net_income`
(a `below_ebitda` group when the worksheet grows past NOI).

## 4. What each adapter wires (next phase)

* **field_catalog adapter** — replace `_invert_aliases` inputs with
  `{c.bindings.field_catalog.key: [a.path for aliases…]}` per namespace;
  `PERIOD_TYPE_RANK = registry.period_types`; `OM_PERCENTAGE_KEYS = {keys
  with percentage_key}`. Keep the module-level constant names so
  `engine_runner.py:45`, `qa_resolver.py:67`, `coverage_audit.py:48` are untouched.
* **usali_scorer adapter** — `_ALIASES[canonical]` ← the concept whose
  `scorer_identifiers()` contains `canonical`, flattening every alias path
  (all doc types); `_has_subordinate_namespace` ← `registry._subordinate_scope`;
  `_TOKEN_RESOLVE_BLOCKLIST` ← keep until `allow_token_match` is the only
  token path. The rollup synthesis (`_derive_usali_rollups`) can become
  `identities.py` identities evaluated with optional terms.
* **documents `_load_critic_inputs`** — `_canonical_key` ←
  `concept_for_path(name, doc_type)[0]` → `bindings.critic_key`; period +
  basis guards ← `resolve(..., want="annual", basis=…)`.
* **variance agent** — `_BROKER_RULE_BY_FIELD` ← `bindings.variance_rule`;
  `_actual_for` ← `bindings.actuals_attr`; `_normalize_field_key` ←
  `concept_for_path`. `analysis._VARIANCE_CONCEPTS` ← `bindings.variance_concept`.
* **web HistoricalsSection / GroundedWorksheet / reviewState** — alias lists
  ← `CONCEPTS[id].aliases` (flatten; `findField` semantics = tiers 1-5);
  `ROWS` ← `bindings.worksheet`; `histValue` ← `worksheet.hist_key`.
  Or, per the comment at `HistoricalsSection.tsx:413-418`, a worker
  `GET /deals/{id}/historicals/normalized` that runs `resolve_many` server-side.
* **provenance** — `SOURCE_LABEL` / `sourceExplanation` / `AssumptionBadge`
  labels ← `SOURCES[id].{label,badge,explanation}`; `sourceKind` ←
  `SOURCES[id].kind` (with the web's 3-colour mapping on top).

## 5. Resolver semantics (as implemented in `registry.resolve`)

Tier 1 exact path among the document type's aliases (own key, families,
then tenant aliases) · Tier 2 exact among `"*"` · Tier 3 exact among ANY
other doc type's aliases · Tier 4 unit-suffix-stripped full path · Tier 5
field tail (stripped) vs bare alias (stripped), refused when the field's
full path is an exact alias of another concept · Tier 6 token match, opt-in.
Within a tier: registry (alias) order, then first seen. Scope and basis are
filters, never preferences: a subordinate slice is served only when `want`
is that scope; an unscoped path takes the document's `period_type` (or the
doc type's default: T12 → ttm, PNL → annual, PNL_MONTHLY → monthly,
PNL_YTD → ytd); `want="annual"` admits annual, ttm and unknown. `reason`
(set iff `value is None`): `basis_excluded` > `period_mismatch` >
`unit_unknown` > `no_source`, i.e. the most specific exclusion seen.
