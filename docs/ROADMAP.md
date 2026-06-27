# Fondok — Build Roadmap

**Owner:** Prem
**Design partners:** Sam (PM), Eshan (SME)
**Scope:** Strictly the 9 items Sam and Eshan asked for in the 2026-06-25 call. Eshan's separate UW Process doc, Sam's earlier emails, and Northspyre learnings are recorded in memory but are NOT in this roadmap. Future calls will expand scope.

---

## 🚨 P0 — Critical security finding (ship before next pilot demo)

The tenant-isolation investigation surfaced **multiple endpoints that currently allow cross-tenant data access**. Anyone with a deal ID can read another customer's memo, documents, costs, or market data. This is not theoretical — it's exploitable today.

| Endpoint | Risk | File |
|---|---|---|
| `GET /{deal_id}/memo` | Cross-tenant memo read | `apps/worker/app/api/deals.py:887` |
| `POST /{deal_id}/memo/generate` | Cross-tenant memo trigger | `apps/worker/app/api/deals.py:1571` |
| `GET /{deal_id}/memo/stream` | Cross-tenant SSE stream | `apps/worker/app/api/deals.py:1643` |
| `GET /{deal_id}/memo/edits` | Cross-tenant edit history | `apps/worker/app/api/deals.py:1885` |
| `GET /{deal_id}/documents` | Cross-tenant document list | `apps/worker/app/api/documents.py:1239` |
| `GET /{deal_id}/market-data` | Cross-tenant market data | `apps/worker/app/api/documents.py:1484` |
| Document download path | Cross-tenant file download | `apps/worker/app/api/documents.py:1524` |
| `GET /{deal_id}/costs` | Cross-tenant cost report | `apps/worker/app/api/deals.py:1000` |

**Fix:** Add `tenant_id: Annotated[UUID, Depends(get_tenant_id)]` to each endpoint and append `AND tenant_id = :tenant` to the underlying queries. Return 404 (not 403) when tenants don't match. Same pattern used by the dozen endpoints that already do this correctly.

**Effort: ~1 day to fix all 8 endpoints + add integration tests.** This goes before item #1 in the build order.

---

# The 9 items (strict call scope)

Every item below was asked for directly by Sam or Eshan on the June 25 call. Each carries:
- What it is (with the SME's exact framing where possible)
- Implementation plan with file paths
- Complexity
- Dependencies on other items
- Open questions that need a product decision before code starts

---

## Build sequence

Order is impact × readiness, not by time.

### Wave 1 — Foundation (do first)
- **P0** — Tenant-isolation security fix (top of doc)
- **#6** Manual override + justification note — small, surfaces Eshan's exact ask
- **#2** Onboarding → Validation separation — unblocks several other items as a UI surface
- **#1** Guided onboarding wizard — the loudest signal, biggest first-impression change

### Wave 2 — Data quality (closes Eshan's "validate everything" thread)
- **#3** USALI compliance scoring — engine exists, surface it
- **#7** Gap detection — pairs with the new wizard and validation tab
- **#4** YoY variance → broker questions — Fondok starts generating analyst work product

### Wave 3 — Closed loop (the "agent of AI work" Eshan kept referencing)
- **#5** Seller Q&A re-ingestion loop — depends on #4
- **#8** STR comp-set drift tracking — depends on #2

### Before any external pilot
- **#9** Per-customer tenant isolation (full architecture; the P0 fix above buys time but the full audit is needed for Brookfield/Apollo)

---

## #1 — Guided per-category onboarding wizard

**Said by:** Sam and Eshan, multiple times. Loudest signal from the call.

**Eshan's framing:** *"It's kind of dashboard, says done, done, done — financial document, three years got it. Then they know what's missing."*

### What changes

Replace the current "drag 72 files into one bucket" Step 3 of the project-creation wizard with a multi-step guided flow:

1. **Step 1 — OM upload** (optional). Single-file drop zone for Offering Memorandum.
2. **Step 2 — Financials by year** (required). Year-by-year prompts. Drop 2025 P&L → drop 2024 P&L → etc.
3. **Step 3 — STR comps** (optional). Drop zone for STR Trend reports.
4. **Step 4 — Catch-all bucket** (optional). Tax returns, room mix, capex, rent rolls.
5. **Throughout:** persistent checklist dashboard showing what's uploaded vs. missing per category.

### Files to touch

- `apps/web/src/app/projects/new/page.tsx:17-23` — wizard step definitions. Replace Step3 (lines 334–467) with a multi-step doc-upload sub-wizard.
- `apps/web/src/app/projects/new/page.tsx` — new components: `DocumentChecklistCard`, `FinancialsByYearStep`, `DocumentCategorySelect`.
- `apps/worker/app/api/documents.py:633` — extend upload FormData to accept `user_doc_type` and `fiscal_year`.
- `apps/worker/app/api/documents.py:750` — INSERT statement: persist user-provided doc_type + fiscal_year.
- `apps/worker/app/extraction/router.py` — Router agent uses user hint when provided; flags misclassifications post-extraction (when confidence > threshold on a different type).
- DB migration: add `documents.user_provided_doc_type`, `documents.fiscal_year`, `documents.misclassified`.

### Complexity: MEDIUM

Frontend restructure + backend FormData extension + Router agent hint integration. Schema migration is small. ~5–7 days.

### Depends on

- **#2** for the post-upload Validation surface (the wizard hands off to Validation when checklist is complete)
- **#7** for gap-detection banners that link back into the wizard

### Open questions for Sam/Eshan

1. **Can a deal exist with NO documents?** ("Shell deal" intent — current wizard allows this.)
2. **Year labeling:** when dropping a 2025 P&L, does the analyst type "2025" or does Fondok infer it? Recommendation: ask only when ambiguous (monthly P&L).
3. **Misclassification policy:** if user pre-categorizes as "T12" but Router strongly believes "PNL_MONTHLY", do we warn or override?

---

## #2 — Onboarding → Validation strict separation

**Said by:** Eshan, explicitly.

**Eshan's framing:** *"Onboarding is collecting all the information, second step is validating the current state of the data, and there may be iteration back and forth between Fondok and the user."*

### What changes

Today validation content is dispersed across DataRoomTab, OverviewTab, VarianceTab, and AnalysisTab sub-tabs. Eshan wants strict separation:

- **Phase A — Onboarding:** Data Room only. Collect data. NO "your numbers look wrong" messaging.
- **Phase B — Validation:** New dedicated tab where gaps + USALI deviations + variance flags + broker questions all surface together for analyst review.

### Files to touch

- `apps/web/src/app/projects/[id]/page.tsx:60-79` — new `?tab=validation` route; tab visibility gating based on deal state.
- New file: `apps/web/src/components/project/ValidationTab.tsx` — consolidates the validation content. Cards: Gaps Panel, USALI Deviations, Variance Heatmap, Broker Questions, Critic Findings.
- `apps/web/src/components/project/DataRoomTab.tsx:601-613` — REMOVE "N critical variance flags" CTA (validation moves to its own tab).
- `apps/web/src/components/project/AnalysisTab.tsx:115-127` — REMOVE Variance + Critic sub-tabs (they move to Validation).
- `apps/worker/app/api/deals.py:70-155` — extend `UpdateDealBody` + `DealRecord` with `state` field.
- `apps/worker/app/api/deals.py` — new endpoint `POST /{deal_id}/transition` with pre-condition checks.
- `apps/worker/app/migrations.py` — add `deals.state` enum column (`ONBOARDING / VALIDATING / READY`), `deals.validation_started_at`, `deals.validation_complete_at`.

### Complexity: MEDIUM

Mostly a UI restructure plus a small state machine. ~3 days.

### Depends on / unblocks

- **Unblocks #3, #4, #7, #8** — all of these surface their UI on the Validation tab.
- **No upstream dependencies.**

### Open questions

1. **Auto-transition vs. manual:** does the deal auto-advance ONBOARDING → VALIDATING when the first document hits EXTRACTED status, or only on user CTA?
2. **What counts as "ready to advance to READY"?** All checklist items green? All critical variance flags marked accepted? All USALI deviations acknowledged? Start strict (require all three) and loosen if Sam pushes back.

---

## #3 — USALI compliance scoring on every P&L upload

**Said by:** Eshan, multiple times.

**Eshan's framing:** *"Fondok should be looking through USALI, which is the universal standard of whatever hotel launching. So Fondok can say I'm looking at the financial, and there's some categories that are not being placed properly on the PnL."*

### What changes

Wire the existing 66-rule USALI engine (in `evals/golden-set/usali-rules.csv`, severity-tagged CRITICAL/WARN/INFO) into the document upload flow. Every extracted P&L gets a 0–100 score with specific deviation callouts.

> **Note:** The CSV has 66 rules, not 56 as previously assumed. All rules are deterministic — no LLM judgment needed in the scorer itself.

### Files to touch

- New file: `apps/worker/app/services/usali_scorer.py` (~150–200 lines). Loads rules from CSV via existing `load_usali_rules()`. Runs each rule's deterministic check against extracted fields. Emits `usali_score: float` + `usali_deviations: list[dict]`.
- `apps/worker/app/api/documents.py:1011-1015` — after extraction completes, call scorer + persist score and deviations to documents row.
- `apps/worker/app/api/documents.py:84-109` — extend `DocumentRecord` with optional `usali_score` and `usali_deviations` fields.
- `apps/worker/app/migrations.py` — add `documents.usali_score FLOAT` and `documents.usali_deviations JSONB` columns.
- `apps/web/src/components/project/DataRoomTab.tsx:920-1023` — USALI compliance badge per document (green ≥90 / amber 70–89 / red <70).
- `apps/web/src/components/project/DataRoomTab.tsx:1028-1161` — expandable "USALI Compliance" accordion below extracted fields, with severity-sorted deviation list.

### Complexity: LOW–MEDIUM

Engine already exists. ~2–3 days for scorer + persistence + UI.

### Depends on

- **#2** — high-severity deviations surface on Validation tab.
- **#4** — high-severity USALI deviations become broker questions in the variance system.

### Open questions

1. **Inconclusive scoring:** if fewer than 5 rules are applicable (sparse extraction), show "Inconclusive — insufficient extracted fields" instead of a percentage. Confirm with Sam.
2. **Market-context-dependent rules** (coastal insurance, seasonal RevPAR) — mark as "requires market context" when deal lacks it.

---

## #4 — YoY/MoM variance → auto-generated broker questions

**Said by:** Eshan, with a specific example.

**Eshan's framing:** *"Look at this 15% swing in F&B month over month, or year over year, and then it'll say that's a major drop or improvement, and then that could be a question that goes down to a broker."*

### What changes

**NEW engine** distinct from the existing `variance.py` agent:

- Existing `variance.py` = broker proforma vs. T-12 actuals (LLM-orchestrated, broker-comparison).
- NEW `historical_variance.py` = YoY (and optionally MoM) deltas on the property's own historical financials (deterministic, no LLM).

When a P&L line moves more than its threshold YoY, Fondok drafts a copy-paste-ready broker question with the supporting data.

### Files to touch

- New file: `apps/worker/app/engines/historical_variance.py` (~300 lines). Deterministic YoY engine. Input: multi-year `USALIFinancials[]`. Output: `BrokerQuestion[]`.
- New file: `packages/schemas-py/fondok_schemas/broker_question.py` — `BrokerQuestion` model with `state: pending/dismissed/sent/answered`.
- `apps/worker/app/migrations.py` — add `broker_questions` table.
- `apps/worker/app/api/analysis.py` — new endpoints: `GET /analysis/{deal_id}/broker_questions`, `PATCH /analysis/{deal_id}/broker_questions/{q_id}`, `POST /analysis/{deal_id}/broker_questions/{q_id}/response`.
- `apps/worker/app/services/engine_runner.py` — call `HistoricalVarianceEngine.run()` after P&L normalization.
- New file: `apps/web/src/components/project/BrokerQuestionsPanel.tsx` — list view + inline actions (dismiss, mark sent, mark answered).
- Integration: `apps/web/src/components/project/ValidationTab.tsx` — embed `BrokerQuestionsPanel`.

### Complexity: MEDIUM (2 weeks)

Engine logic is simple deterministic math. Complexity is in schema + UI + state-transition validation.

### Thresholds (need Eshan to confirm before code starts)

Eshan said roughly:
- 10% YoY on departmental revenue/expenses
- 15% YoY on F&B
- 20% YoY on Other Operated
- ~5% on NOI / GOP (stricter for rolled-up metrics)

Recommend hardcoded defaults in the engine for v1; move to per-tenant config in a later phase.

### Depends on

- **#2** — Validation tab must exist for the panel to render.
- **Unblocks #5** — broker questions are the input to the Q&A re-ingestion loop.

### Open questions

1. **YoY only, or also MoM?** Roadmap title says both; the spec emphasizes YoY. Recommend YoY-only for v1; add MoM later if analyst feedback warrants.
2. **Threshold defaults** — Eshan to confirm exact values before Week 1 starts.

---

## #5 — Seller Q&A re-ingestion loop

**Said by:** Eshan — the "agent of AI work" he kept referencing.

**Eshan's framing:** *"You can explain all that to Fondok by pasting an email or an updated file and say 'this is how the seller addressed it', and Fondok could be like 'oh great, I see now why F&B dropped 10% year over year.'"*

### What changes

Closes the loop opened by item #4. Analyst sends broker questions → broker replies → analyst pastes reply into Fondok → Fondok re-runs analysis with new context → variance flag state updates ("resolved / partially resolved / still concerning"). New context can propagate to engine input overrides (with analyst confirmation).

### Files to touch

- New file: `apps/worker/app/agents/qa_resolver.py` (~150 lines). Sonnet 4.6 agent. Reads: original question + broker reply + surrounding data. Emits: `QAResolverOutput { verdict, summary, proposed_overrides, audit_note }`.
- New file: schema migration — `broker_qa_pairs` table with: question_id, analyst_question, broker_response, resolver_verdict, proposed_overrides JSONB, applied_overrides JSONB.
- `apps/worker/app/api/deals.py` — three new endpoints: `POST /deals/{id}/broker_responses`, `GET /deals/{id}/qa_history`, `PATCH /deals/{id}/broker_responses/{qa_pair_id}/apply`.
- `apps/worker/app/graph.py` — new LangGraph node `qa_resolver` (triggered on-demand, not part of linear pipeline).
- `apps/worker/app/agents/analyst.py` — extend the analyst prompt to read Q&A history; cite `audit_note` in memo footnotes.
- `apps/web/src/components/project/ValidationTab.tsx` — Broker Response intake form + Q&A history panel + "Apply proposed overrides" confirmation modal.

### Complexity: HIGH

~850 lines across 6 modules. Multi-component integration with the memo system.

### Cost impact

~3–10 Q&A pairs per deal × ~$0.005/Sonnet call = ~$0.015–0.05 per deal. Well within the $20/deal budget.

### Depends on

- **#2** — intake form lives on Validation tab.
- **#4** — broker questions are the input.
- **#6** — proposed overrides go through the override flow with auto-populated notes.

### Trust model decision

**Recommendation: always analyst-confirmed.** No auto-apply. Every proposed override requires explicit analyst confirmation (PATCH `/apply`) before landing in `field_overrides`. Preserves IC memo integrity: every number is either extracted, seeded, or explicitly analyst-justified.

---

## #6 — Manual override + mandatory justification note

**Said by:** Eshan, with a specific example.

**Eshan's framing:** *"You should have the option to essentially go in and hard code a number, and then have a note that you should always have a note there if you're going to hard code something. When you're underwriting something, 3% is the standard for management fees, so if you're paying 2.8 or 2.9 you just manually hard code it, but then have a note to why you did it."*

### What changes

Reshape `deals.field_overrides` JSONB from flat `{path: value}` to structured `{path: {value, note, overridden_by, overridden_at}}`. Note field is REQUIRED — non-empty, validates server-side. Backward compatible: legacy flat overrides auto-migrate on first read.

### Files to touch

- `apps/worker/app/services/engine_runner.py:818-886` — `_load_deal_overrides()` detects legacy flat shape, auto-migrates; `_apply_overrides()` unpacks new nested shape.
- `apps/worker/app/api/deals.py:105-109` — `UpdateDealBody.field_overrides` changes type to `dict[str, FieldOverrideRecord]`. New Pydantic model `FieldOverrideRecord(value, note=Field(min_length=1), overridden_by, overridden_at)`.
- `apps/web/src/components/help/AssumptionBadge.tsx:138-144` — add "Override" action button to badge. Tooltip displays note when an override is active.
- New component: `apps/web/src/components/help/OverrideModal.tsx` — current value (read-only), new value input, note textarea (REQUIRED, non-empty validation blocks submit).
- `apps/web/src/components/project/OverviewTab.tsx:77-136` — integrate modal trigger.

### Complexity: LOW

~260 lines total. Schema column already exists. ~1 week.

### Depends on

- **Unblocks #5** — Q&A agent's `audit_note` becomes the auto-populated value of this note field.
- **Reads in #2's Validation tab** — variance flags can use this override flow with prefilled values.

### Open questions

1. **Legacy migration:** auto-migrate on first read with empty note + `overridden_by: "legacy"`, or force analyst to add a note for legacy overrides? Recommend auto-migrate (don't block existing deals).

---

## #7 — Gap detection — sequential + detail-level

**Said by:** Sam, with two specific patterns.

**Sam's framing:** *"If I have all my financials from 2019 to 2025 but I'm missing detailed for 2024 to 2025 — only summary — that's a gap I'd want Fondok to flag for me."*

### What changes

Two flavors of gap detection on financial uploads, surfaced as actionable banners on Validation tab:

- **Sequential:** "You have 2019, 2020, 2022–2025; missing 2021"
- **Detail-level:** "You have summary 2024 but monthly detail only through October"

### Files to touch

- New file: `apps/worker/app/services/coverage_audit.py` (~200 lines). Computes coverage maps from extraction_results joined to documents. Returns typed `DocumentCoverage` with `gaps: list[CoverageGap]`.
- `apps/worker/app/api/deals.py` — new endpoint `GET /deals/{deal_id}/document_coverage`.
- New file: `apps/web/src/components/project/validation/GapBanners.tsx` — renders each gap as a banner with color-coding (red year-gap, amber month-gap, amber summary-only). "Upload [year/months]" button deep-links to the onboarding wizard at the right step.

### Complexity: LOW–MEDIUM

Period-type ranking already exists in `engine_runner` (per FORMULAS.md). Mostly aggregation + UI. ~1 week.

### Depends on

- **#1** — "Upload 2021" banner deep-links into the wizard.
- **#2** — banners live on Validation tab.

### Open questions

1. **Look-back window:** assume 5 years from today? Or infer from the earliest uploaded year?
2. **Non-calendar fiscal years:** how to handle? Existing `period_start` + `period_ending` fields support arbitrary periods but the gap detection needs to know when to be lenient.

---

## #8 — STR comp-set drift tracking

**Said by:** Eshan, with a specific example.

**Eshan's framing:** *"In 2024 you had Hilton South Beach in your comp set; in 2025 it was replaced with W South Beach. Fondok could make those notes on the side."*

### What changes

When the same property's STR Trend report changes its competitive set across years, Fondok detects the diff and surfaces it as context.

### Files to touch

- `apps/worker/app/agents/extraction_schemas/str_trend.md` — augment STR_TREND extraction to capture `report_year` and full `compset[].name` list.
- New file: `apps/worker/app/services/comp_set_drift.py` (~200 lines). Year-over-year diff of comp sets with fuzzy property-name matching (Levenshtein >80%).
- `apps/worker/app/api/documents.py` — extend `_aggregate_market_data` to preserve multiple STR_TREND extractions for year-over-year comparison (currently only keeps the most recent).
- `apps/worker/app/api/deals.py` — new endpoint `GET /deals/{deal_id}/comp_set_drift`.
- New component: `apps/web/src/components/project/validation/CompSetDriftCallout.tsx` — surfaces drift on Validation tab.

### Complexity: LOW–MEDIUM

Fuzzy-matching is the main wrinkle. ~3–4 days.

### Depends on

- **#2** — drift callout lives on Validation tab.

### Open questions

1. **Property name normalization:** "Hilton South Beach" vs. "Hilton Hotel South Beach" — exact match wins, fuzzy match (>80%) flagged for review, <80% treated as drift. Confirm threshold.

---

## #9 — Per-customer tenant isolation (full architecture)

**Said by:** Sam, flagged as a hard requirement before any external pilot.

**Sam's framing:** *"From a security standpoint, how do we get each customer in their own instance with their own data — that's a huge concern."*

### Current state (audit findings)

**Working:** Web→Worker `X-Tenant-Id` header is reliably set from Clerk org. Every primary CRUD endpoint (deals list/get/patch/archive, documents upload, extraction results, due diligence, analysis variance, market data retrieval, memo edits POST) correctly filters by `tenant_id`. Schema has `tenant_id` on every table with composite indexes. Audit log captures tenant_id on every mutation.

**Critical gaps — listed at the top of this doc as P0 security finding.** Eight endpoints currently allow cross-tenant data access.

### What changes (the full Phase 2 architecture, beyond the P0 fix)

After the P0 endpoint patches ship, the full institutional-grade tenant isolation:

1. **Defense-in-depth query middleware** — new file `apps/worker/app/tenant_middleware.py`. SQLAlchemy event listener auto-injects `tenant_id` filter into every SELECT. Catches developer oversight.
2. **Subdomain routing** — `apollo.fondok.com` resolves to a tenant-scoped view. Next.js middleware extracts subdomain, sets `X-Tenant-Id` header for unauthenticated requests. Vercel wildcard DNS `*.fondok.com`.
3. **Comprehensive isolation test suite** — `apps/worker/tests/test_tenant_isolation.py`. Cross-tenant request must always return 404 for every endpoint, every resource type.
4. **Audit log tenant filtering** — audit dashboard always filters by authenticated tenant. Log access attempts outside user's tenant as security event.
5. **SECURITY_ARCHITECTURE.md** — threat model + data-residency commitments for security questionnaires.

### Complexity: HIGH

P0 fix: 1 day. Full architecture: ~90 engineer-hours / 2–3 weeks.

### Open questions for Sam

1. **Subdomain vs. path prefix:** `apollo.fondok.com` (cleaner, better cookie isolation) or `fondok.com/tenant/apollo` (easier Railway routing, visible URLs)? Recommend subdomain.
2. **Customer tier:** offer full instance-per-customer (separate Railway worker + DB) as a premium "White-Glove Security" tier for largest contracts?
3. **Internal team access:** does the Fondok team need cross-tenant visibility (for support)? If yes, RBAC layer is needed; if no, current single-tenant-per-user model works.

---

# Cross-cutting themes from the parallel investigation

Patterns the agents independently surfaced:

### Engine numbering correction

The USALI rule catalog has **66 rules**, not 56 as previously documented. Doc references should update.

### Two variance engines, not one

The existing `variance.py` agent does broker-pro-forma-vs-T12. The new item #4 needs `historical_variance.py` (YoY on actuals). Keep them distinct files with distinct names — don't try to overload `variance.py`.

### Backward compatibility is recurring

Items #2 (deal.state), #6 (override structure), and the security fix all need backward-compat handling for existing data. Plan for "auto-migrate on first read, set sensible defaults for legacy rows" as the default pattern.

### Validation tab is load-bearing

5 of the 9 items (#3, #4, #5, #7, #8) surface their UI on the new Validation tab. **#2 is the single highest-leverage prerequisite** — landing it early unblocks everything else.

### The closed-loop trio

Items #4 + #5 + #6 form a coherent agent-of-AI-work workflow:
- #4 generates broker questions
- #5 ingests broker replies and proposes overrides
- #6 applies overrides with justification notes
The Q&A history then surfaces as IC memo footnotes via the existing analyst agent. This is Fondok's killer demo flow. Build all three together.

---

# Open questions for Sam + Eshan (consolidate for next Friday)

Send these as a single batched list before the next call:

1. **(#1)** Can a deal exist with zero documents (shell deal)? Year-tag prompts at upload — always, or only for ambiguous P&Ls? Misclassification policy when user disagrees with Router?
2. **(#2)** Auto-transition ONBOARDING → VALIDATING on first extraction, or manual CTA only? What gates VALIDATING → READY?
3. **(#3)** USALI inconclusive-scoring threshold (5 rules min?). Market-context-dependent rules behavior?
4. **(#4)** Confirm variance thresholds: 10% departmental / 15% F&B / 20% other / 5% NOI? YoY only or also MoM?
5. **(#5)** Q&A trust model: always require analyst confirmation of proposed overrides, never auto-apply?
6. **(#6)** Legacy override migration: auto-fill empty note, or force analyst to add note for existing overrides?
7. **(#7)** Gap detection look-back window: fixed 5 years, or inferred from earliest uploaded year?
8. **(#8)** Comp-set name-match fuzzy threshold: 80% Levenshtein OK?
9. **(#9)** Subdomain vs. path-prefix routing? Premium instance-per-customer tier? Internal team cross-tenant access for support?

---

# Source

All scope here is sourced from `memory/project_fondok_call_2026_06_25.md` — the June 25 Sam + Eshan catchup notes.

Future scope (Eshan's separate UW Process doc, Sam's earlier email feedback, Northspyre competitive lessons) is intentionally NOT in this roadmap. It's preserved in:
- `memory/project_fondok_call_2026_06_25.md` (call notes)
- `memory/project_fondok_sam_feedback_2026_06.md` (Sam's emails — operating ratios, PIP displacement v2, in-house portfolio data)
- `memory/project_fondok_competitor_northspyre.md` (Northspyre analysis)
- `docs/new doc/Hotel UW Process (AutoRecovered).docx` (Eshan's 11-step institutional spec)

These will become roadmap items in future calls when Sam and Eshan raise them explicitly. Until then, build only what was asked for on June 25.
