---
id: pricing
title: "Return targets and the Max Price Solver"
decisions: [FON-68, FON-100]
anchors:
---
<!-- section 5: Return targets and the Max Price Solver — from page.tsx lines 846-998 -->
::: conceptual {title="Return targets and the Max Price Solver"}
::: intro
The hurdles a deal must clear are yours, set once on Overview → Investment Profile. Pricing reads
them from the deal — there is no hidden default hurdle anywhere in the pricing path.
:::

::: card
#### The Investment Profile is the source of truth

- **Two pricing hurdles.** **Target levered IRR** and **target equity multiple** are stored on the
  deal as your inputs {{badge:analyst_override}}. Either may be unset; an unset target renders as a
  dash.
- **The return profile is a preset.** The target levered IRR control carries a preset for each return
  profile — Core 5–10%, Core Plus 7–12%, Value Add 11–17%, Opportunistic 18%+. Choosing one writes the
  band's midpoint as the target (an open band writes its floor) — an explicit action, never applied
  implicitly. You can then edit the number.
- **Benchmark only.** Saving a target never re-runs the model. The Overview compares the calculated
  levered IRR against the target: below it is *Below target*, at or up to two points over is *Within
  target*, and more than two points over is *Above target*. Display only.
- **Target stabilized yield on cost.** Compared against the stabilized year's NOI divided by total
  capital, both from the model run — Pass at or above the target, Short below it. With no stabilized
  year or no total capital the actual is a dash, never estimated.

:::

::: card
#### Max Price Solver (Overview → Returns → Pricing)

- **No silent hurdles.** If the deal carries no target, the Pricing block says so with a link to the
  Investment Profile and shows no numbers.
- **Lower-of rule.** Each hurdle is solved independently by searching on purchase price with the loan
  amount held fixed and equity flexing, between half and twice the current basis. Max Price is the
  lower of the two solved prices, and the binding constraint is named. With one target set, that
  hurdle governs alone.
- **Unreachable hurdles stay unreachable.** A hurdle no price down to half the basis can clear reports
  “no price clears the hurdles”; one that clears even at twice the basis reports that instead. The
  search boundary is never shown as a price.
- **Pricing sensitivity grid.** The solver re-runs per cell over exit cap rate and RevPAR growth
  around the base assumptions. Each cell shows the maximum purchase price clearing both hurdles with
  its binding constraint, and the base cell reproduces the headline solve exactly.
- **Exports price to your targets, or say they cannot.** The Excel model's Pricing Sensitivity sheet
  and the IC memo's Max-Price Findings solve against the same Investment Profile targets as the
  on-screen solver. With no target on the deal, the sheet and the memo section read “Pricing to target
  not available: no return target set on this deal” and carry no solver output; with only a target
  equity multiple, the max price solves that hurdle alone and the IRR grid is omitted (its breakeven
  sweep needs an IRR target). The LOI draft prices at the solver's offerable headline and refuses —
  with the reason — when the deal record has no property name or key count, or when no price clears
  the hurdle.

:::

::: card
#### What the exports never do

- **Invent a number.** A figure the deal does not carry prints as “not available: \<reason\>” naming
  the field (headline cells, assumption rows, section notes) or as a dash inside a table whose header
  names the field — never 0, 0% or $0. Totals are stated only when every component is; a missing
  component is named instead. The Debt Schedule sheet is the debt engine's own monthly schedule, never
  re-derived from headline terms.
- **Synthesize a sensitivity grid.** The IC deck's heat-map is the Sensitivity engine's persisted
  output for the deal's canonical run. If that engine has not run, failed, or produced no cells, the
  slide states “Sensitivity analysis not available: \<reason\>”. The deck carries no generic “next
  steps”.
- **Hide a default.** The Op-Ratio Provenance sheet and memo section are always produced for a
  modelled deal; when every ratio is a hotel-type seed they say so (“all operating ratios are
  hotel-type defaults; no T-12 was provided”). Wherever an exported debt figure rides on Fondok's flat
  SOFR assumption, the export prints the same note the Financing tab shows. The unsourced brand
  recovery multiplier table is no longer printed.

:::

::: card
#### Asking versus bidding price (Overview KPIs)

- **Two comparable sets.** On an acquisition the Overview shows the same metrics twice — purchase
  price, total capitalisation, renovation, stabilized NOI, levered and unlevered IRR — once at the
  *asking* price and once at the *bidding* price. Nothing is saved.
- **Asking price.** The OM's asking price as extracted. With no OM price the modelled purchase price
  stands in and is labelled with its source.
- **Bidding price — solved backward from the target.** The Max Price Solver run on the IRR hurdle
  alone: the purchase price at which the levered IRR equals your target, with the loan held and equity
  absorbing the difference. With no target the bidding set reads “Set target levered IRR to solve” and
  shows no numbers.

:::

::: card
#### Investment Bridge

- **Equity invested to equity returned.** A waterfall from the equity funded at close to the total
  cash returned to equity, deal-level before the GP / LP split, read from the Base Case run.
- **Five legs.** *Acquisition* is every use except renovation and the loan fee; *Renovation* is the
  renovation line including contingency; *Operations* is NOI over the hold; *Financing* is loan
  proceeds less the origination fee, debt service and the payoff at exit, plus any refinancing
  proceeds; *Exit* is the sale price less selling costs and transfer tax.
- **It foots by construction.** Equity invested plus the five legs equals equity returned exactly, and
  the tab states the check. A leg the run cannot support shows a dash with the reason, never a zero
  bar, and the bridge then says it does not foot.

:::

:::


::: detailed
::: intro
The targets as stored, the bands the Overview compares against, and the solver's bracket,
tolerances and refusals.
:::

::: card
#### Targets and presets

<!-- source: apps/worker/app/api/deals.py (target_irr, target_moic, target_stabilized_yoc, return_profile deal columns), apps/web/src/lib/catalogs.ts (returnProfiles bands) -->
Target levered IRR, target equity multiple and target stabilized yield on cost are deal columns;
either may be unset and an unset target renders as a dash. The return-profile presets and the
value each writes when chosen (the band's midpoint; an open band writes its floor): Core 5–10% →
7.5%; Core Plus 7–12% → 9.5%; Value Add 11–17% → 14.0%; Opportunistic 18%+ → 18.0%. A preset is
applied only by an explicit choice; nothing auto-fills a target.

:::

::: card
#### How the Overview reads a target

<!-- source: apps/web/src/components/project/OverviewTab.tsx (Within target = at or up to 200bp over; Pass / Short on yield on cost) -->
Levered IRR against the target: below the target is "Below target"; at the target or up to 2.0
percentage points over is "Within target"; more than 2.0 points over is "Above target". Display
only — saving a target never re-runs the model. Stabilized yield on cost = the stabilized year's
NOI ÷ total capital, both from the run; "Pass" at or above the target, "Short" below it; with no
stabilized year or no total capital the actual is a dash.

:::

::: card
#### Max Price Solver — bracket, tolerance, refusals

<!-- source: apps/worker/app/engines/price_solver.py (PRICE_FLOOR_MULTIPLIER 0.5, PRICE_CEILING_MULTIPLIER 2.0, PRICE_TOLERANCE_USD 50000, IRR_TOLERANCE_PCT 0.0005, MAX_ITERS), apps/worker/app/api/analysis.py (max-price routes; 422 when no target) -->
Each hurdle is solved independently by bisection on purchase price with the loan amount held fixed
and equity flexing, between 0.5× and 2.0× the current basis, to a price tolerance of $50,000 (IRR
tolerance 5 basis points; the equity-multiple search uses 0.005). Max Price is the lower of the two
converged prices, and the binding hurdle is named; with one target set, that hurdle governs alone.
A hurdle not cleared at half the basis reports "no price clears the hurdles"; one cleared even at
twice the basis reports that instead; a bracket end is never shown as a price. With no target the
Pricing block shows no numbers and the route refuses the solve.

:::

::: card
#### Pricing grid and bidding price

<!-- source: apps/worker/app/engines/max_price_grid.py (exit cap axis −100bp…+100bp in 50bp steps; RevPAR growth axis −2pp…+2pp in 1pp steps), apps/worker/app/engines/asking_bid.py (IRR constraint only) -->
The grid re-runs the solver per cell over exit cap rate (base −100, −50, 0, +50, +100 basis
points) and RevPAR growth (base −2, −1, 0, +1, +2 percentage points); the base cell reproduces
the headline solve exactly. The bidding price is the solver run on the IRR hurdle alone — the
purchase price at which the levered IRR equals the target with the loan held; with no target it
reads "Set target levered IRR to solve".

:::

::: card
#### Exports

<!-- source: apps/worker/app/export/ (Pricing Sensitivity sheet, Max-Price Findings, LOI draft), apps/worker/app/engines/loi_generator.py -->
The Excel Pricing Sensitivity sheet and the IC memo's Max-Price Findings solve against the same
Investment Profile targets. With no target they read "Pricing to target not available: no return
target set on this deal" and carry no solver output; with only a target equity multiple, the max
price solves that hurdle alone and the IRR grid is omitted. The LOI draft prices at the solver's
offerable headline and refuses, with the reason, when the deal has no property name or key count
or when no price clears the hurdle.

:::
:::

<!-- section 5: Return targets & the Max Price Solver — from EngineeringMethodology.tsx lines 781-878 -->
::: engineering {title="Return targets & the Max Price Solver"}
::: intro
The hurdles a deal must clear are the analyst's, set once on Overview → Investment Profile. Overview
→ Returns → Pricing reads them from the deal — there is no hidden 15% / 1.80x default anywhere in
the pricing path.
:::

::: card
#### The Investment Profile is the source of truth

- **Two pricing hurdles.** **Target LIRR (levered IRR)** and **Target MOIC** are stored on the deal
  (`target_irr` / `target_moic`) as analyst inputs {{badge:analyst_override}}. Either may be unset; an
  unset target renders as “—”. There is one Target LIRR control — no separate returns-profile field.
- **The return profile is a preset.** The Target LIRR control carries a preset for each return profile
  — **Core 5–10%**, **Core Plus 7–12%**, **Value Add 11–17%**, **Opportunistic 18%+** (target levered
  IRR bands; the same four appear on the wizard's Return Profile step). Choosing one stores it as the
  deal's `return_profile` tag and writes the band's midpoint (Value Add 11–17% → 14%; an open band
  such as 18%+ → its floor) as the target — an explicit action, never applied implicitly; nothing
  auto-fills a target. The analyst can then edit the number; the tag stays as the preset it started
  from. While no target is set, “Use profile midpoint” writes the current preset's figure. Deals
  created under the earlier bands keep their stored profile id and simply read the new band.
- **Benchmark only.** Saving a target never re-runs the model. The Overview's Return benchmark strip
  compares the calculated levered IRR from the canonical returns run against Target Levered IRR: below
  the target is *Below target*, at or above it up to 200bp over is *Within target*, and more than
  200bp over is *Above target*. It is display only and does not drive any engine.
- **Target Stabilized Yield on Cost.** The Overview's Yield on cost benchmark compares the analyst's
  target against the stabilized year's yield on cost — stabilized-year NOI ÷ total capital, both from
  the model run — and reads *Pass* at or above the target, *Short* below it. With no stabilized year
  or no total capital the actual is “—”, never estimated.

:::

::: card
#### Max Price Solver (Overview → Returns → Pricing)

- **No silent hurdles.** `POST /analysis/{id}/pricing/max-price` reads the deal's targets when the
  request omits them. If neither the deal nor the request carries at least one target, the worker
  answers 422 — “No return target set — set Target Levered IRR / Target MOIC on the Investment Profile
  or pass them explicitly” — and the Pricing block shows that message with a link to the Investment
  Profile and no numbers.
- **Lower-of rule.** Each hurdle is solved independently by bisecting on purchase price (loan amount
  held fixed, equity flexes; 50%–200% of the current basis; ≤ 40 iterations). Max Price is the lower
  of the IRR-solved and MOIC-solved prices and the *binding constraint* is named (IRR, MOIC, or both
  when they land within $50K). With a single target set, that hurdle governs on its own.
- **Unreachable hurdles stay unreachable.** A hurdle that no price down to 50% of the basis can clear
  reports “no price clears the hurdles” (Max Price “—”); one that clears even at 2× the basis reports
  “≥ 2× basis”. The bracket endpoint is never shown as a price.
- **Pricing sensitivity grid.** `POST /analysis/{id}/pricing/max-price-grid` re-runs the solver per
  cell over exit cap rate (base ± 100bp in 50bp steps) × RevPAR growth (base ± 2pp in 1pp steps), at
  most 25 cells. Each cell shows the maximum purchase price clearing both hurdles with its binding
  constraint. The growth axis is the deal's `revpar_growth` assumption — the same lever the
  Sensitivities grids flex and the same one the P&L assumption names — so both places call it RevPAR
  growth. Here it also re-tilts the model's canonical NOI series relative to the base growth
  assumption: year 1 is unchanged, later years and the exit NOI move, so the base cell reproduces the
  headline solve exactly.
- **The Returns sandbox is not applied.** Both pricing blocks call the solver with no overrides, so an
  active Live Assumptions sandbox does not reach them. While one is on, each block says so inline
  rather than quietly answering a different question. Reset the sandbox on Sensitivities to compare
  like for like.
- **Exports.** `export/live_payload.py` resolves the deal's targets through the same
  `_deal_return_targets` read the pricing routes use; the Excel Pricing Sensitivity sheet and memo
  Max-Price Findings carry no solver output without a target (they state “Pricing to target not
  available: no return target set on this deal”), and the deck's heat-map is the persisted
  `sensitivity` engine row or a stated reason. No export module holds a default hurdle, a fixture, or
  a demo path.

:::

::: card
#### Asking vs Bidding price (Overview KPIs)

- **Two comparable sets.** On an acquisition deal the Overview KPI area shows the same metrics twice —
  Purchase Price, Total Capitalization, Renovation, Stabilized NOI, Levered and Unlevered IRR — once
  at the *asking* price and once at the *bidding* price, from `GET
  /analysis/{id}/pricing/asking-vs-bid` (read-only; nothing is persisted).
- **Asking price.** The OM's extracted asking price (`asking_price.headline_price_usd`, as extracted,
  before any analyst override). With no OM price the modeled purchase price stands in and is labelled
  with its source (deal record, analyst override or seed). When the asking price is the modeled price
  the set shows the canonical Base Case run; otherwise the deal is re-priced at the asking price with
  everything else held.
- **Bidding price — solved backward from Target LIRR.** The bid is the existing Max Price Solver run
  on the IRR hurdle alone: bisect the purchase price until the returns engine's levered IRR equals the
  deal's Target LIRR (50%–200% of the modeled price, 5bp / $50K tolerance). As in the solver, the loan
  amount is held, equity absorbs the price difference, and closing costs, renovation and the NOI path
  are unchanged — so Total Capitalization moves one-for-one with price and Renovation / Stabilized NOI
  read the same in both sets. Each set's IRRs are a fresh returns-engine run at that price.
- **No target, no bid.** With no Target LIRR on the Investment Profile the Bidding set reads “Set
  Target LIRR to solve” and shows no numbers; a target outside the solver's bracket reports that
  instead of a bracket-end price.

:::

::: card
#### Investment Bridge

- **Equity invested → equity returned.** The Investment Bridge tab (immediately before Scenario
  Analysis) is a waterfall from the equity funded at close (`−returns.cash_flows[0]` =
  `capital.equity_amount`) to the total cash returned to equity (`Σ returns.cash_flows[1:]`),
  deal-level before GP/LP allocation, read from the Base Case run via `GET
  /deals/{id}/engines/investment-bridge`.
- **Five legs, each an engine field.** *Acquisition* = −every Sources & Uses line except Renovation
  and the senior loan fee (purchase price, closing costs, working capital, reserves…). *Renovation /
  PIP* = −the Renovation use line (contingency included). *Operations* = Σ `returns.noi_by_year` over
  the hold. *Financing* = loan proceeds − loan origination fee − Σ debt service + any refinance
  cash-out − the loan payoff at exit, using the exact debt-service series, exit balance and refi
  cash-out the returns engine consumed. *Exit* = gross sale price − selling costs − transfer tax.
- **It foots by construction.** Because equity = total uses − debt and each levered cash flow = NOI −
  debt service (+ refi, + net sale less payoff at exit), equity invested plus the five legs equals
  equity returned exactly. The tab states the check. A leg the run cannot support shows “—” with the
  reason (never a zero bar) and the bridge then says it does not foot.

:::

:::

