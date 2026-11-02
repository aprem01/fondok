---
id: engines
title: "How the model is organised"
decisions: [FON-63, FON-66, FON-118]
anchors:
---
<!-- section 4: How the model is organised — from page.tsx lines 728-843 -->
::: conceptual {title="How the model is organised"}
::: intro
Eight deterministic models run in dependency order and publish their results together, so every tab
reads one consistent snapshot.
:::

::: card
::: deflist
- **Revenue** Rooms × occupancy × ADR, plus F&B, other operated departments, resort fees and
  miscellaneous income.
- **F&B** A per-occupied-room F&B model with a food and beverage split; resort fees are a separate
  line.
- **Expense** USALI departmental and undistributed lines, management fee, FF&E reserve and fixed
  charges, to gross operating profit, NOI and net cash flow.
- **Capital** Purchase price, closing costs, renovation budget and working capital, plus the senior
  origination fee owned by the Financing tab — the Sources & Uses.
- **Debt** The senior and PACE tranches from your entered terms — fixed or floating, amortising or
  interest-only, maturity — with the monthly schedule, DSCR, debt yield, LTV and LTC, covenants and
  refinancing.
- **Returns** Levered and unlevered IRR, equity multiple, cash-on-cash, the sale value from the exit
  cap rate and exit NOI, and the returns schedule by exit year. A loss-making deal is reported
  honestly — a negative IRR or a multiple below one is never floored.
- **Sensitivity** IRR and equity multiple across exit cap, hold, growth and other pairs.
- **Partnership** The GP / LP waterfall with preferred return, catch-up and promote tiers. It runs
  only on the deal's own partnership terms; without them it reports “not applicable — no
  partnership terms on this deal”. A shortfall is funded as a dated pro-rata capital call that adds
  to unreturned capital, and is reported as additional contributions.

:::

:::

::: card
#### Financing is an assumptions workspace

Debt documents are optional. The Financing tab is where the financing is entered, and every core
term is your input — Fondok does not read a term sheet in this release. Each edit is saved as an
override {{badge:analyst_override}} and re-runs the chain, so the schedule, DSCR, debt yield, Cash
Flow and Returns reflect what was entered. A year whose NOI is negative does not stop the model: its
DSCR shows as not applicable, the uncovered debt service is reported as that year's shortfall, and
the tab names the negative years in one warning.

- **Senior loan.** Amount or LTV (either resizes the same loan), rate basis, amortisation, an
  interest-only period, and maturity. A floating rate prices off the index assumption plus spread,
  clamped to an optional floor and cap; until an index is entered the build-up shows Fondok's flat
  index assumption and says it is not market data.
- **PACE loan.** One rule: a loan counts in total debt, LTV, LTC and debt yield exactly when the cash
  flows carry it. Once a rate is entered the PACE loan is a real loan everywhere: a source of funds at close (equity falls by its
  principal), its debt service in every year — a refinance retires the senior only — and every sale
  repays its balance plus its exit fee; it is in total debt, LTV, LTC and debt yield. Until a rate is
  entered the tranche is *terms pending*: its principal shows on its own line, outside total debt,
  LTV, LTC and debt yield, and it stays out of Sources & Uses, debt service and the sale payoff
  rather than running on an invented rate.
- **Covenants.** Maximum LTV and LTC and minimum DSCR and debt yield are thresholds you enter. There
  is no default package — a covenant without a threshold shows its live reading, an input, and no pass
  or fail verdict.
- **Senior origination fee.** A platform default of 1.50% of the senior loan {{badge:seed}} until you
  change it. Financing is its only owner: Sources & Uses, total uses, required equity and LTC all move
  with it, and at zero the line disappears.
- **Refinance and exit fees.** A refinance that raises less than the balance it retires plus its
  costs is charged to equity at the refinance — a negative cash flow in IRR, counted as additional
  equity in the multiple. A loan's exit fee is deducted when it is repaid, at the sale or the
  refinance. Prepayment penalties, yield maintenance and defeasance are not modelled.
- **Missing inputs are inputs.** Wherever a required assumption is absent, the tab renders the input
  to provide — “Enter rate”, “Enter spread”, “Enter threshold” — with the consequence stated, instead
  of an unexplained dash.

:::

::: card
#### The Returns sandbox

Overview → Returns → Sensitivities carries a **Live Assumptions** card whose sliders are a sandbox,
not an edit. It answers “what would this deal do if…” without changing what the deal *is*.

- **Five assumptions, and only five.** Exit cap rate, RevPAR growth, hold period, LTV and interest
  rate. Nothing else can be reached from the sandbox.
- **The sliders start where the run started.** Each slider's base is the value the saved run recorded,
  never a default typed into the screen. A run that did not record one of the five shows “Awaiting
  model run” in place of the sliders.
- **It runs the whole chain, in memory.** Nothing is written: no run, no change to the deal's
  assumptions. CAPEX and Financing keep showing the Base Case throughout. While the sandbox differs
  from the Base Case a banner spans the Returns section and every tile that moved carries a Sandbox
  chip.
- **It survives navigation, and only that.** Held in your browser session, per deal. Reset to base
  case, opening a different deal or a fresh model run clears it. It is never shared, bookmarked or
  seen by anyone else, and the Max Price Solver and the pricing grid do not consume it.

:::

::: card
#### When a model cannot finish

The models are independent, so one can fail without taking down the rest. When one does, the deal
shows a red banner naming the model, a plain-language reason and a one-click Re-run; the numbers it
feeds read as an explicit error, never as silent dashes that look like missing data. When a deal's
saved run is older than the current version of Fondok, an amber banner says so and names the tabs
affected, with the same Re-run.

:::

:::


::: detailed
::: intro
What each engine runs on when nothing else is entered, and the rules the Financing tab enforces.
:::

::: card
#### Debt — defaults and clamps

<!-- source: apps/worker/app/services/engine_runner.py (_seed_defaults ltv 0.65, interest_rate 0.068, amortization_years 30, term_years 5, interest_only_years 0, loan_costs_pct 0.015), apps/worker/app/engines/debt.py (_SOFR_DEFAULT = 0.043; rate_floor / rate_cap clamp; terms_pending) -->
Senior loan platform defaults, each badged Seed until entered: LTV 65%, interest rate 6.80%,
amortisation 30 years, term 5 years, no interest-only period, origination fee 1.50% of the loan.
A floating rate prices at index + spread, clamped to the optional floor and cap; until an index is
entered the build-up shows a flat 4.30% index assumption and says it is not market data. A PACE
tranche with an amount but no rate is "terms pending": its principal is shown on its own line and
stays out of total debt, LTV, LTC, debt yield, Sources & Uses, debt service and the sale payoff; with
a rate it counts in all of them, funds the deal at close and is repaid at the sale. LTC is total debt
÷ total uses everywhere (Financing, Overview, exports); capital also publishes the senior-only LTC. A year whose NOI is negative shows DSCR as not
applicable and reports the uncovered debt service as that year's shortfall.

:::

::: card
#### Capital — Sources & Uses defaults

<!-- source: apps/worker/app/services/engine_runner.py (_seed_defaults closing_costs_pct 0.02, working_capital 500000, soft_costs 0, contingency 0, renovation_budget 5280000) -->
Closing costs 2.0% of the purchase price; working capital $500,000; soft costs and contingency 0
(a zero use is omitted from the table; an analyst who carries them enters the amounts). The
renovation budget default is $5,280,000 (see Section 2 for its precedence). Each is a platform
default used only when no document states the figure, badged Seed with its reason (no document of
the needed type, or no field that states the figure) wherever it is printed.

<!-- source: apps/worker/app/engines/capital.py (RENOVATION_SPLIT_DEFAULT 0.75 / 0.15 / 0.10, RENOVATION_SPLIT_DEFAULT_SOURCE platform_default_renovation_split, RenovationBreakdown.source / .note) -->
The renovation budget's breakdown into hard costs, soft costs and professional fees uses the
platform default split of 75% / 15% / 10% when no itemized renovation budget is on file. The
breakdown is badged Platform default and carries that reason; it never reads as the deal's own
breakdown. When only some parts are entered, each part is badged by its own source.

:::

::: card
#### Partnership — the deal's own terms, or not applicable

<!-- source: apps/worker/app/engines/partnership.py (NO_PARTNERSHIP_TERMS_REASON), apps/worker/app/services/engine_runner.py (partnership_terms, _incomplete_tiers, STATUS_NOT_APPLICABLE, _partnership_terms_view, OPTIONAL_WHEN_NOT_APPLICABLE), apps/worker/app/api/deals.py (assumption_sources partnership.*) -->
The waterfall runs only on partnership terms the deal carries — from an uploaded partnership / JV
document or entered on the Partnership tab: the GP / LP equity split (one side derives the other),
the preferred return and at least one promote tier (a hurdle and a split). Without them the
Partnership engine reports **not applicable — “no partnership terms on this deal”** — a neutral
status, never an engine error. When only some are entered it names what is missing, and a tier
entered by half blocks the waterfall and is named (“Tier 3 has a GP split but no hurdle”) rather
than being dropped from the stack. The Partnership sheet of the Excel export states the same
reason, and the Cash Flow statement runs without distribution lines and says why. No benchmark
waterfall is ever applied in their place.

<!-- source: apps/worker/app/services/engine_runner.py (_default_assumptions carries no partnership term; _partnership_terms_view), apps/worker/app/engines/partnership.py (PartnershipEngine deficit capital call) -->
Fondok has no default or suggested partnership terms: the Partnership tab shows only the terms you
or a partnership document have entered, and a term not entered shows a dash awaiting your input.
When the waterfall is not applicable the tab shows "No partnership terms on this deal" with the
reason, and a half-entered tier is named in a notice above the tabs; neither is shown as an error.
A shortfall is funded as a dated pro-rata capital call by ownership split; the preferred return
accrues on it.

:::

::: card
#### The Returns sandbox

<!-- source: apps/worker/app/api/deals.py (POST /deals/{id}/engines/returns/preview), apps/web/src/components/project/OverviewTab.tsx (Live Assumptions card) -->
Five inputs — exit cap rate, RevPAR growth, hold period, LTV and interest rate — run the whole
engine chain in memory through a preview call. Nothing is written: no run, no change to the deal's
assumptions. Each slider starts at the value the saved run recorded; a run that did not record one
of the five shows "Awaiting model run".

:::

::: card
#### When an engine cannot finish

<!-- source: packages/schemas-py/fondok_schemas/reasons.py (engine_error, load_error, engine_skipped, missing_identity_inputs, stale_run), apps/worker/app/services/engine_runner.py (EngineFailure rows) -->
A failed engine records one failure row with a reason — the engine raised (engine error), the loader
could not read the deal's data (load error), or an upstream engine did not complete (engine skipped)
— and the tabs it feeds read as that explicit error, never as silent dashes. A deal with no key
count or purchase price is refused before any engine that needs them runs (missing identity inputs);
the message names where to enter them — the purchase price on the Overview or CAPEX tab, the key
count on CAPEX — and CAPEX shows those inputs in place of its summary until the model can run. A
saved run older than the current version is marked stale and the affected tabs are named.

:::
:::

<!-- section 4: Engine architecture — from EngineeringMethodology.tsx lines 668-778 -->
::: engineering {title="Engine architecture"}
::: intro
Eight deterministic engines run in dependency order. Engine outputs persist as typed payloads; the
web app reads them via /deals/\{id\}/engine_outputs.
:::

::: card
::: deflist
- **Revenue** Rooms × occupancy × ADR projection + F&B + Other Operated + Resort Fees + Misc.
- **F&B** Per-occupied-room F&B model with food/beverage split; resort fees handled as a separate
  line.
- **Expense** USALI 11th departmental + undistributed + management fee + FF&E reserve + fixed charges
  → GOP, NOI (institutional), Net Cash Flow.
- **Capital** Purchase price + closing costs + renovation budget + working capital → property uses;
  plus the senior origination fee (owned by the Financing tab) → total capital; Sources & Uses.
- **Debt** Senior + PACE tranche stack from analyst-entered terms (fixed or index + spread with floor
  / cap, amortization or interest-only, IO stub, maturity); monthly amortization schedule; DSCR, debt
  yield, LTV / LTC; analyst-entered covenant thresholds; refi optionality.
- **Returns** Levered + unlevered IRR, equity multiple, Year-1 CoC, terminal value via exit cap ×
  terminal NOI. Handles loss-making (underwater) deals — a negative IRR or sub-1x multiple is
  reported honestly, not floored or crashed.
- **Sensitivity** IRR heatmap across exit cap × RevPAR growth (or other configurable pairs); the IC
  deck prints this persisted grid, never a synthesized one.
- **Partnership** GP / LP waterfall with preferred return, catch-up, promote tiers — run only on the
  deal's own terms (a partnership document or the analyst's entries); otherwise the engine row is a
  `not_applicable` refusal and the Cash Flow statement runs without distribution lines. A deficit
  period is funded as a dated pro-rata GP/LP capital call (by ownership split) that adds to
  unreturned capital — the preferred return accrues on it — and is reported as additional
  contributions; Cash Flow → Partnership → Returns carry one treatment of additional equity.

:::

:::

::: card
#### Debt is an assumptions workspace

Debt documents are optional. The Financing tab is where the financing is entered, and every core
term is an analyst input the model runs on — Fondok does not read the term sheet in this release.
Each edit is persisted as an analyst override {{badge:analyst_override}} and re-runs the chain, so
the Debt Schedule, DSCR, debt yield, LTV / LTC, Cash Flow and Returns all reflect what was entered.
A year whose NOI is negative (a ramp or PIP year) no longer stops the model: its DSCR shows N/A, the
debt service its NOI does not cover is reported as that year's shortfall, and the Financing tab
names the negative years in one warning. Returns, Cash Flow and Partnership then run on the actual
negative cash flow; only a non-positive exit-year NOI stops Returns, because no sale value can be
capitalised from it.

- **Senior loan (tranche 1).** Amount (or LTV — either resizes the same loan), rate basis,
  amortization (0 = interest-only for the full term), an interest-only stub in months before principal
  begins, and maturity. Fixed prices off the entered coupon; Floating prices off the index assumption
  plus spread, clamped to an optional floor / cap. Switching basis asks for the term the new basis
  needs (a spread or a coupon) in the same save. Until an index is entered the floating build-up shows
  Fondok's flat SOFR assumption and says so — it is not market data. If floating is picked without a
  spread, the floating terms are pending: the all-in rate shows "Awaiting spread", and the senior is
  serviced at the rate on file meanwhile — the schedule, the headline DSCR and the capital stack carry
  the same debt service, and the tab says so.
- **PACE loan (tranche 2).** One leverage rule: a tranche counts in Total Debt, LTV, LTC and debt
  yield exactly when the cash flows carry it — the senior always, the PACE loan once it is funded
  **and** priced. Once a rate is entered the tranche is in every cash flow: its principal is a **source
  of funds at close** (Sources & Uses shows it; equity = total uses − senior − PACE) and its
  origination fee a use; its own monthly schedule (interest-only, or an IO stub then amortization)
  sets its debt service in DSCR, Cash Flow and Returns in every year — a refinance retires the
  **senior only**, so the PACE debt service continues after it — and every sale, the headline and
  each exit-year row, repays its outstanding balance plus its exit fee (Balance at Exit includes it;
  Cash Flow shows a **PACE Loan Proceeds** line at close). No PACE maturity is modelled within the
  hold. Until a rate is entered the tranche is *terms pending*: its principal is shown on its own
  **Pending PACE Principal** line, outside Total Debt, LTV, LTC and debt yield, and it stays out of
  Sources & Uses, debt service, DSCR and the sale payoff rather than running on an invented rate (or
  as free money); the tab says so next to the input. **LTC** is total debt ÷ total uses on the
  Financing tab, Overview (labelled "LTC (all debt)") and the exports; Overview shows both **Senior
  LTV** and **LTV (all debt)** (the Financing tab's LTV). The Financing tab's origination and exit fee
  rows are the **senior's** (beside its percentage), with the total over every carried tranche noted
  when a priced PACE adds to it — an unpriced PACE's fees are in neither; the capital engine also
  publishes the senior-only LTC, and run-compare lists senior and junior debt as separate sources.
  The exports keep one basis per block: **Senior Debt** (senior amount, LTV, LTC and terms) and
  **All Debt** (total debt, LTV, LTC, any pending principal, debt service and coverage).
- **Covenants.** Maximum LTV / LTC and minimum DSCR / debt yield are thresholds you enter. There is no
  default package — a covenant without an entered threshold shows its live Current reading, an “Enter
  threshold” input, and no pass / fail verdict. Entered thresholds are tested against the modeled
  Year-1 metrics (LTV / LTC at close).
- **Maturity and the payoff at sale.** The schedule runs to maturity, but the sale repays the loan
  balance outstanding at the **sale month** — the hold's last month, or the disposition month when
  one is set — not the balance at maturity: a 10-year loan sold in Year 5 repays its Year-5 balance.
  After a refinance (modeled on the Refinance sub-tab) the sale repays the refinanced loan's balance;
  a sale set before the refinance repays the senior balance at that month and draws no refinance
  cash-out (Returns, Cash Flow and the Investment Bridge all leave it out). **Balance at Exit** on
  the Debt tab is exactly that payoff, and the Cash Flow statement's **Exit Debt Payoff** is the
  payoff the Returns engine deducted. When the loan **matures before the sale** and no refinance
  replaces it, the model does not invent a refinance or an extension: it carries the maturity
  balance to the sale and keeps the same debt service, and the Debt tab says so in one warning — the
  maturity year, the sale, the balance carried — asking you to model a refinance or set a term that
  covers the hold. A refinance that lands **after maturity** retires that carried maturity balance
  (never zero), and the Debt tab warns that the loan matures before the refinance.
- **Refinance that needs equity.** The refinance's net cash flow to equity is its proceeds less the
  senior balance it retires and the refinance costs (the new loan's fee plus the retired loan's exit
  fee). When that is positive it is the cash-out; when it is negative — the new loan raises less
  than the balance plus costs — the difference is **charged to equity** as a contribution at the
  refinance month (the refinance year-end, or the refinance month when one is set). It is a negative
  equity cash flow in the levered IRR, the Cash Flow statement (**Refinance Equity Contribution**),
  the Investment Bridge, every exit-year row after the refinance, the financing sensitivity grid and
  the partnership waterfall, where the full amount is its own capital call by the GP / LP ownership
  split (monthly or annual waterfall alike) and that period's operating cash is distributed on its
  own — so the GP and LP contributions equal the amount charged. The **equity multiple** counts it as additional equity invested:
  distributions ÷ (equity + the refinance contribution). The Debt tab states the amount, the
  balance it retires and when it is charged. A sale set before the refinance draws neither a
  cash-out nor a contribution. A refinance set in or after the sale year never happens, so it is not
  modelled, and the Debt tab says so.
- **Senior origination fee.** The senior loan's origination fee is a **Fondok seed of 1.50% of the
  senior loan** — a platform default, not a figure read from a term sheet or an OM, and it carries the
  Seed badge {{badge:seed}} until you change it. It is editable on Financing, and Financing is its
  only owner: the capital engine charges the resolved tranche fee as the Sources & Uses line **Senior
  Loan Origination Fee**, and Overview → Capitalization's **Financing Costs** reads that same number.
  Change it on Financing and Sources & Uses, Total Uses, required equity and LTC all move; set it to
  0% and the line disappears. One assumption, one number, three labels retired.
- **Exit fee.** A tranche's exit fee is a percent of its **original principal** (the loan amount, as
  the Debt tab shows it — not the balance repaid). It is paid when the loan is repaid: a sale deducts
  the exit fee of every tranche still outstanding (the senior, or after a refinance only the junior
  tranches) from the net sale proceeds — the headline and every exit-year row alike, shown as
  **Lender Exit Fees** on the Cash Flow statement, in the Investment Bridge and in the Excel / IC
  memo exit tables — while the senior loan a refinance retires pays its fee at the refinance, as a
  refinance cost. The Excel Assumptions sheet and the IC memo state the refinance itself: year, new
  loan, balance retired, costs and the cash-out or the equity contribution. Prepayment penalties, yield maintenance and defeasance are **not modelled**: the
  payoff at sale is the loan balance plus any exit fee, without them.
- **Missing inputs are inputs.** Wherever a required assumption is absent, the Financing tab renders
  the input to provide (“Enter rate”, “Enter spread”, “Enter threshold”) with the consequence stated,
  instead of an unexplained dash.

:::

::: card
#### The Returns sensitivity sandbox

Overview → Returns → Sensitivities carries a **Live Assumptions** card whose sliders are a sandbox,
not an edit. It answers “what would this deal do if…” without changing what the deal *is*.

- **Five assumptions, and only five.** Exit cap rate, RevPAR growth, hold period, LTV and interest
  rate. The worker applies those keys and ignores everything else in the request, so a sandbox call
  cannot reach an unrelated engine input.
- **The sliders start where the run started.** Each slider's base is the value the returns engine
  recorded for the canonical run (`returns.inputs.assumptions`), never a client-side default. A run
  that did not record one of the five keys shows *Awaiting model run* in place of the sliders, naming
  the missing key. The same rule holds for every Returns figure: a headline tile, Return Bridge row or
  sensitivity cell the engine did not produce reads “—” with the missing engine field named — never 0.
- **It runs the whole chain, in memory.** `POST /deals/{id}/engines/returns/preview` re-runs revenue →
  F&B → expense → capital → debt → returns on a copy of the deal's canonical inputs. Nothing is
  written: no `engine_outputs` row, no run id, no advance of the canonical run, and no change to the
  deal's `field_overrides`. CAPEX and Financing keep showing the canonical case throughout — that is
  the point, not a bug.
- **The LTV lever is honest.** The Financing tab writes both “Senior Loan Amount” and “LTV” as the
  same pinned senior principal. When the sandbox flexes LTV, that pin is released *on the in-memory
  copy only*, so the flexed LTV resizes the senior end-to-end — interest, principal, debt service and
  DSCR all move with it. Without the release the preview reported returns levered on one loan amount
  against another loan's debt service, and DSCR never moved at all. A funded PACE or mezzanine tranche
  keeps its principal: LTV sizes the senior.
- **Where the sandbox number shows, it says so.** While the sandbox differs from the base case a
  banner spans every Returns sub-tab and each Returns Summary tile that moved carries a **Sandbox**
  chip. A tile showing a sandbox number does not offer a provenance popover — the trace describes the
  canonical run, which is not what the tile is showing.
- **It survives navigation, and only that.** The sandbox is held in the browser session, per deal.
  Leaving Returns and coming back keeps it; **Reset to base case**, opening a different deal, or a
  fresh canonical run clears it. It is never a URL parameter and is never written to the deal record,
  so it cannot be shared, bookmarked, or seen by anyone else.
- **Pricing does not consume it.** The Max Price Solver and the pricing grid are solved on the
  canonical case, and say so inline while a sandbox is active — a “max price under a hypothetical LTV”
  is a materially different question from the one the solver answers.
- **The slider ranges are the grid's ranges.** The RevPAR-growth slider reads the top of the
  sensitivity matrix's own axis rather than restating it, so the sliders can never offer less than the
  grids show.

:::

::: card
#### When an engine can't finish

Engines are deterministic and independent, so one can fail without taking down the rest. When one
does, the deal shows a clear red banner naming the model, a plain-language reason, and a one-click
Re-run — the numbers it feeds read as an explicit error, never as silent “—” dashes that look like
missing data. Engines are also hardened against valid-but-extreme inputs (a deeply negative-return
scenario, a zero base), so an ugly deal computes rather than crashes.

:::

:::

