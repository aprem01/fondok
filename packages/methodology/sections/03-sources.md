---
id: sources
title: "Where market assumptions come from"
decisions: [FON-61, FON-74, FON-96, FON-109]
anchors:
  - brand-positioning-methodology
  - market-submarket-methodology
  - market-tab-methodology
  - property-type-methodology
---
<!-- section 3: Where market assumptions come from — from page.tsx lines 418-725 -->
::: conceptual {title="Where market assumptions come from"}
::: intro
Every assumption on the Overview carries a source badge saying where the value came from. The legend
below explains each badge and the order of precedence between them.
:::

::: card {testid=market-tab-methodology}
#### The Market Comps tab: comp set, the trailing-twelve-month blend, demand and supply

**One comp set, one derivation.** The hotel count and key count on the Market Comps tab come from
the same set: the active hotels on the STR roster, combined across every STR report on the deal. A
hotel is treated as closed only when a report says so explicitly; closed hotels are listed with a
“closed · excluded” chip and are left out of both the count and the keys. Which reports count as STR
is decided by the lane each document was processed in, not by its tag, so a market report filed
under STR is read as a market study and never feeds the comp set.

**“TTM · comp-set blend” is defined on the card.** Market occupancy, ADR and RevPAR are the comp
set's blended trailing-twelve-month figures, recovered exactly from the STR penetration indices:
comp-set occupancy is the subject's occupancy divided by its occupancy index, and likewise for ADR
and RevPAR. The card prints a methodology line composed from the inputs actually used — the reports,
the period and the active hotels. A metric whose index is missing shows a dash rather than a
substitute.

**Demand growth and supply growth read the market study.** A submarket report feeds both tiles, and
every figure names its document and page. Demand growth is the report's own occupied-room-night
growth. Under-construction rooms are shown as a share of existing inventory, with the report's own
supply-change figure shown alongside and never substituted. A tile the reports cannot populate shows
a dash with its reason — no market study on the deal, or a report that does not carry the series. No
growth rate is ever assumed.

:::

::: card
::: badges
::: badge {source=t12_actual name="T-12 Actual"}
Year-1 anchor read from the deal's uploaded T-12. Later years grow forward at the configured revenue
and expense growth rates.

:::

::: badge {source=cbre_horizons name="CBRE Horizons"}
Forecast curve read from an uploaded CBRE Hotel Horizons report for the subject's submarket and
chain scale.

:::

::: badge {source=om_comps name="OM Comps"}
Median cap rate derived from the OM's comparable-sales table.

:::

::: badge {source=om_broker name="OM Broker"}
A broker pro forma value read from the OM. Treat with appropriate scepticism — these are the
broker's pitched numbers.

:::

::: badge {source=pnl_benchmark name="P&L Benchmark"}
An industry benchmark ratio from a CBRE or HotStats report. The model no longer applies these on its
own; an applied suggestion shows as Analyst Override with its note. The badge can still appear on
older runs.

:::

::: badge {source=analyst_override name="Analyst Override"}
Set through an inline editor and carrying your written justification. Wins over every other source —
but only a value that actually changed is recorded as one; see “What counts as an override” below.

:::

::: badge {source=str_subject_ttm name="STR · Subject TTM Actual"}
The subject property's own trailing-twelve-month occupancy and ADR as reported by STR — an actual,
not a forecast. This is what the Base Year is seeded from when the STR basis is on.

:::

::: badge {source=str_comp_set name="STR Comp-Set Rates"}
The comp set's blended occupancy and ADR shown on the Market Comps tab, applied as the Year-1 input
by “Use STR rates in the model”. This is the competitive set's performance, not the subject's own,
and the Market Comps tab shows the two as separate figures.

:::

::: badge {source=str_forecast name="STR Trend Forecast"}
The forward-forecast scenario's month-twelve point — a projection of the subject, used only when the
subject's own trailing twelve months is not extracted. The P&L shows the active basis and a
one-click Revert whenever rates carry any STR tag.

:::

::: badge {source=str_forecast_unavailable name="STR Unavailable"}
STR rates were requested but could not populate. The model stays on the T-12 base and says so — an
STR basis is never silently “active”.

:::

::: badge {source=derived_from_revpar_growth name="Derived from RevPAR Growth"}
A RevPAR-growth override derives ADR growth with the occupancy path held, so the lever moves
operating NOI. Setting ADR growth explicitly takes direct control.

:::

::: badge {source=index_assumption name="Index Analysis Assumption"}
Index Analysis indexes the subject against one of three benchmarks, chosen on Market Comps → Index
Analysis: the **STR competitive set** (the default), a **market or chain-scale benchmark** from the
deal's market reports, or a **CoStar comp set** when that export has been extracted. Four
assumptions ride on the choice — market occupancy growth, market ADR growth, and the occupancy and
rate penetration targets — each showing its document source or “Your override”. They feed the Future
P&L only while “Use STR rates in the model” is on; with the toggle off nothing moves. \
**Targets by
year.** Penetration targets can be set per model year; a by-year entry beats the all-years target.
The all-years target drives Year 1 only — later years are indexed only where a by-year target is set
or you override that metric's market growth; otherwise the year follows the model's own growth and
is flagged as applying the all-years target to Year 1 only. A year with a target but no benchmark
value is flagged as benchmark unavailable: the model keeps its own growth and no figure is invented.
\
**Target units.** A target is index points from 25 to 250 (100 is parity) or a ratio from 0.25 to
2.5 (1.0 is parity); the two bands do not overlap, so a value names its own unit. A stored target
outside both bands is ignored by the model and the panel says so, with a prompt to re-enter it.

:::

::: badge {source=index_assumption name="Index Analysis · Columns & Rows"}
The Index Analysis year columns follow the acquisition close calendar — the same calendar the Future
P&L prints. The six columns before Year 1 are history; the forecast columns are exactly the model
horizon, and nothing is carried beyond it. Each benchmark value names its kind with a worded chip —
**Actual** with its period and document, **Forecast** with the report and year, or **Assumption**
for a comp set grown at market growth — never colour alone. STR publishes no comp-set forecast, so
comp-set out-years are always an assumption and say so. Penetration rows print a target only when
the subject rows actually run on it; otherwise the published index is held flat with a note. Every
cell shows its formula and inputs on hover; a cell with no reported input is a dash.

:::

::: badge {source=seed name="Platform Default"}
A Fondok default used because no deal document has set the value yet, shown in grey. The senior
origination fee (1.50% of the senior loan) is one of these: editable on Financing, and the single
source for the Sources & Uses origination-fee line and the Overview's Financing Costs.

:::

::: badge {source=deal_row name="Deal Details"}
Entered on the new-deal wizard or the Overview: project name, city, brand, keys, the reopening date
— and the **purchase price**. The wizard's price field is read to whole dollars (“$36.4M”,
“36,400,000”; a range stores its midpoint and is labelled as such). Text that does not read as a
price is kept as a note and sends nothing — the model keeps its default until you set the price on
CAPEX → Deal Summary.

:::

:::

:::

::: card
#### What counts as an override

Opening a field to inspect it never changes its source. An override is recorded only when the saved
value **differs from the current effective value** — compared in the field's own unit, so “6.80”
typed over a stored 6.8% writes nothing. Every editor exits without writing on Cancel, Escape or a
click outside, and a Save that changed nothing says so. The same rule holds for overrides already
stored: a value equal to what the deal resolves to on its own is reported under its source's badge,
not as an override. Use **Clear override** in the source popover to remove one.

:::

::: card
#### Every override carries a justification

An override that **changes a number the model runs on** carries your written justification, required
at the moment of the edit and shown to reviewers beside the number and in the activity trail. The
rule is enforced on the server as well as in the editor, so no path — screen, spreadsheet import or
integration — can record an unexplained number. The justification is never written for you.

**What is exempt, and why.** Edits that change no modelled number need no note: the Historical P&L's
row layout, the Stabilization Year, the Property Name, the completion-guarantee status, the number
of promote tiers, and the IC memo's prose. Clearing an override is a return to source and needs no
note either.

**Target levered IRR, target equity multiple and target stabilized yield on cost are not
overrides.** They are stated objectives — the benchmark the returns are read against and the hurdle
the Max Price Solver prices to — not replacements for a value Fondok read from a document, so they
are editable without a justification.

:::

::: card
#### Project Name and Property Name

**Project Name** is your confidential deal identifier — set and renamed on the Overview; a document
never writes it. **Property Name** is the asset as named in the offering documents and is never
inferred from the Project Name; it shows a dash until the OM is extracted. You may override the
Property Name from its Overview row; the extracted value and its source page are kept, and “Restore
sourced value” brings them back.

:::

::: card
#### Which documents set the deal's key count, brand and city

Only documents about the subject property — the Offering Memorandum, a T-12, P&Ls, a hotel program
or a property information sheet — can set the key count, and when more than one states a room count
the Offering Memorandum wins. The existing brand comes from the OM and fills a blank only; the
proposed brand is yours and is never touched by a document. Market reports, STR reports, comparable
sets, capital budgets, insurance documents and leases describe other things and never change the key
count, brand or city. Until a qualifying document is extracted the deal header shows a dash for
keys, never zero.

{testid=brand-positioning-methodology} **Brand and positioning.** Positioning is a deal setting, chosen on the wizard's Brand and
Positioning step and editable on the Overview. Choosing a brand *suggests* that brand's STR chain
scale wherever STR lists it; a suggestion is only that, and the tier changes only when you select
one. When an Offering Memorandum is extracted and the deal has no tier, a confident match on the
brand the OM names sets the positioning, and the Overview says where the tier came from — “From OM
brand” with the document and page, or “Analyst” for a tier you chose. A tier you chose is never
replaced by an OM. The stored tier is what the model reads for the comparable-sales match and for
which portfolio ratios may contribute.

{testid=property-type-methodology} **Property type.** A deal's property type is one of five: Full-Service, Select-Service,
Limited-Service, Extended-Stay or Resort. It is read from the Offering Memorandum only, and only
when the OM states it plainly (“boutique lifestyle full-service” reads as Full-Service; “luxury” on
its own names a positioning, not a type, and sets nothing). Nothing is assumed when the OM is
silent. The comparable-sales match uses the positioning tier; only a deal with no tier falls back to
its property type for an approximate scale — Full-Service and Resort as Upper Upscale,
Select-Service as Upper Midscale, Limited-Service as Midscale. Extended-Stay spans several scales,
so it sets no fallback.

{testid=market-submarket-methodology} **City, market and submarket.** These are three separate deal fields. City is the property's city,
filled from the OM only while empty. Market and Submarket are your choice on the wizard's first step
and correctable on the Overview; no document writes them and no model reads them, so changing them
never re-runs the model. The Market field suggests STR-defined market names; typing a market or
submarket that is not suggested is always allowed, and Fondok never invents an STR identifier or a
submarket name.

:::

::: card
#### Which statement is the Year-1 basis

Year-1 occupancy, ADR, revenue lines and expense lines all come from one statement: the document the
Data Room badges as **Primary source**. The model and the badge share one ranking — full-year
statements before partial ones, the most recent period first, a T-12 ahead of a calendar-year P&L of
the same year. Older annual P&Ls never blend into Year 1: a line is corroborated only across
statements that describe the same period, so every Year-1 number traces to a single row of the
primary statement. Earlier years still feed the Historical P&L and the variance checks.

:::

::: card
#### Hover any number to trace it

- **Inputs — “where did this come from?”** Assumptions show a source badge coloured by origin: green
  when grounded in the deal's own documents, amber when still a default or benchmark, violet when you
  overrode it. A badge backed by a document is clickable and jumps to that source in the Data Room.
- **Outputs — “how was this computed?”** Modelled values — revenue, NOI, debt service, DSCR, equity
  multiple, sale price, IRR — show their formula and every named input on hover, and each input chains
  one hop further, back to a document, a default, an override or another computed value.
- **NOI pin.** A deal can carry an analyst-entered NOI schedule used to reconcile to a source model.
  While it is set, the Debt and Returns models read that schedule instead of the operating model, so
  operating edits do not move NOI; the Future P&L shows an “NOI pinned” notice with a one-click clear,
  after which NOI follows the operating assumptions again.
- **IRR is calculated, and says so.** IRR is the discount rate that sets the equity cash flows to
  zero, solved iteratively. Its hover says so and lists the year-by-year cash flows the solver ran
  over.
- **Total Revenue is the statement's own line.** On the Historical P&L the Total Revenue row shows the
  statement's stated total when it publishes one, and the sum of the extracted lines — labelled
  calculated — only when it does not. The Revenue footing row under the grid always compares the two
  and shows the difference; no total is invented when neither exists.
- **Every historical column states its period.** A statement is read as a full year, a year to date
  through a month end, or a trailing twelve months ending at a month end, and the column header says
  which. When the basis cannot be established the column shows the bare year with a “basis unknown”
  chip, never an invented period.
- **Cross-checked across statements.** When more than one full-year statement covers the same period,
  each revenue and expense line is cross-checked and Year 1 is grounded on the corroborated value, so
  one misread line cannot skew the model. A line reported by only one statement is used as-is.
- **One review state, Data Room and P&L.** A value is “to review” when its extraction confidence is
  below the threshold and it has not been accepted or corrected. The Data Room's per-statement count
  is exactly the number of flagged cells in that statement's column; accepting or correcting a value
  clears both at once.

:::

::: card
#### As-of rule

An underwriting is a claim about what was knowable on a date. When a deal sets an underwriting as-of
date, Fondok will not ground a market assumption on a report published after it: the assumption
keeps the basis it already had and the badge says unavailable with the reason, never a silent
substitution. A report whose date cannot be established is used and flagged — a missing date is a
caveat, not grounds to withhold a number. The same date anchors the comparable-sales lookback, so
re-opening a deal months later does not quietly age its comp set out of the window.

:::

::: card
#### The full ledger

The Sources ledger, linked from every traced figure's tooltip, lists every modelled assumption, its
value and its origin in one table. Filter to the ungrounded set to see exactly what is still riding
on a default, and export the ledger so every figure in the IC memo is defensible.

:::

:::


::: detailed
::: intro
The source labels, precedence rules, bands and dates behind the badges — the values the engines
read, with the file each one is read from.
:::

::: card
#### Source labels and the precedence the loader applies

<!-- source: apps/worker/app/services/engine_runner.py (SOURCE_* labels, load_engine_inputs, _seed_reasons), apps/worker/app/ontology/concepts.yaml (sources) -->
Every modelled assumption carries one source id — seed, deal_row, t12_actual, cbre_horizons,
om_comps, om_broker, pnl_benchmark (older runs only), portfolio_pnl, analyst_override,
str_subject_ttm, str_comp_set, str_forecast, str_forecast_unavailable, derived_from_revpar_growth,
index_assumption, deal_type_default — and the badge text comes from the concept registry. The
loader starts every key as a platform default with a reason (no document of the needed type, or
no field that resolves to the concept), then lets grounded sources overwrite it, then the firm's
portfolio P&L ratios, then analyst overrides; a P&L benchmark is never applied by the loader —
it is a suggestion the analyst applies, after which the value reads as an analyst override.

:::

::: card
#### What counts as an override

<!-- source: apps/worker/app/services/engine_runner.py (override equality check: a stored override equal to the resolved value is reported under its source's badge), apps/web/src/lib/overrideNote.ts (exempt keys) -->
An override is recorded only when the saved value differs from the current effective value,
compared in the field's own unit. A stored override equal to what the deal resolves to on its own
is reported under its source's badge. Target levered IRR, target equity multiple and target
stabilized yield on cost are deal columns, not overrides, and need no justification note.

:::

::: card
#### Index Analysis — target units and bands

<!-- source: apps/worker/app/services/index_methodology.py (INDEX_POINTS_RANGE = 25–250, INDEX_RATIO_RANGE = 0.25–2.5) -->
A penetration target is index points from 25 to 250 (100 is parity) or a ratio from 0.25 to 2.5
(1.0 is parity); the two bands do not overlap, so a value names its own unit. A stored target
outside both bands is ignored by the model and the panel says so. A by-year target beats the
all-years target; the all-years target drives Year 1 only. The assumptions feed the Future P&L
only while "Use STR rates in the model" is on.

:::

::: card
#### Comparable sales and the as-of rule

<!-- source: apps/worker/app/engines/comp_sales.py (lookback_years default 5, measured from the underwriting as-of date), apps/worker/app/services/engine_runner.py (exit-cap median: three or more comps with cap rates 4–11%; NOT_KNOWABLE_AS_OF, AS_OF_UNKNOWN) -->
The comparable-sales lookback is five years, measured from the underwriting as-of date when one is
set, otherwise from today. A report published after the as-of date is refused (reason: not knowable
as of); a report whose date cannot be established is used and flagged (reason: as-of unknown). The
exit-cap anchor is the median of three or more comparable sales whose cap rates fall between 4%
and 11%.

:::

::: card
#### The senior origination fee

<!-- source: apps/worker/app/services/engine_runner.py (_seed_defaults loan_costs_pct 0.015), apps/worker/app/engines/capital.py (loan_costs_pct 0–10%; senior_loan_fee = debt_amount × senior_origination_fee_pct ÷ 100) -->
Platform default 1.50% of the senior loan, accepted range 0–10%, editable on Financing. The fee is
one line: senior loan fee = senior loan amount × fee rate, read by Sources & Uses, total uses,
required equity and LTC.

:::

::: card
#### Which statement is the Year-1 basis

<!-- source: apps/worker/app/services/engine_runner.py (primary-statement ranking shared with the Data Room badge: full-year before partial, most recent period first, T-12 ahead of a calendar-year P&L of the same year) -->
One statement supplies Year-1 occupancy, ADR, revenue lines and expense lines: the one the Data
Room badges Primary source. The ranking is full-year statements before partial ones, the most
recent period first, a T-12 ahead of a calendar-year P&L of the same year. Older annual P&Ls never
blend into Year 1.

:::
:::

<!-- section 3: Market-data assumptions — from EngineeringMethodology.tsx lines 361-665 -->
::: engineering {title="Market-data assumptions"}
::: intro
Every assumption surfaced on the Overview carries a provenance badge telling you exactly where the
value came from. The legend below explains each source label and its precedence.
:::

::: card {testid=market-tab-methodology}
#### The Market Comps tab: comp set, the TTM comp-set blend, demand and supply growth

**One comp set, one derivation.** The Market Comps tab's hotel count and its key count are computed
from the same set: the *active* hotels of the STR roster (`ttm_performance.compset.<n>.*`). The
roster is the *union* of every STR report on the deal — a hotel is matched across reports by its STR
id (`ttm_performance.compset.<n>.str_id`) or, failing that, by its name with STR's closed label
stripped — because a closure often appears in only one report (a daily report lists “Closed - Blue
Moon Hotel · 0 rooms” while the newer trend report still lists “Blue Moon Hotel · 75 rooms”). A
hotel is treated as closed only when *any* report says so explicitly — an extracted `status` of
`closed`, or STR's own roster label, which prints a closed competitor as “Closed - \<name\>”. A
zero-room row with neither marker stays an active hotel contributing no keys; nothing is inferred.
Its keys are read from the newest report that lists a positive count. Closed hotels are listed on
the roster with a “closed · excluded” chip and are excluded from *both* the count and the keys, and
the roster note names the marker and the document that carried it — or says that none was found, in
which case every listed hotel is counted. The report's own rollups (`comp_set.comp_set_size`,
`comp_set.total_keys`) are kept for transparency but are never the headline while a roster exists;
they are used only when no roster was extracted, and the tab says so. Which reports count as STR is
decided by the lane each extraction ran in, not by the document's tag: a CoStar market report an
analyst filed under “STR / Comp Set” is read as a market study (below) and never feeds the comp set
or the blend. The same rule governs every other reader of STR data: the `GET
/deals/{id}/market-data` envelope behind Index Analysis (a market report tagged STR lands in
`market_history` as the market's actuals, never in `str_trend` as the subject's own history) and the
STR forward forecast's subject TTM seed and monthly history, which read only extractions that ran in
the STR lane. The envelope lists the roster of ONE report — the newest STR-lane extraction that
carries one, never a merge of two reports by row position — and marks a hotel `closed` under the
same rule as above (an explicit marker in any STR report on the deal); Index Analysis's comp-set
keys sum only the hotels that are not closed. The STR forward forecast's comp-set RevPAR baseline
likewise reads one report's roster, and its penetration indices come from the newest extraction.
Each figure keeps the document and page it was read from.

**“TTM · comp-set blend” is defined on the card.** Market Occupancy, ADR and RevPAR are the comp
set's blended trailing-twelve-month figures, recovered exactly from the STR penetration indices:
comp-set Occupancy = subject TTM Occupancy ÷ MPI, comp-set ADR = subject TTM ADR ÷ ARI, comp-set
RevPAR = subject TTM RevPAR ÷ RGI (each index is subject ÷ comp set, as STR publishes it). The
Submarket Snapshot prints a methodology line composed from the inputs actually used: the STR
report(s) the subject TTM and the indices were read from, the period (the subject's monthly series,
e.g. Jul 2025 – Jun 2026, or the report year when no monthly series was extracted), and the active
comp-set hotels with their keys and any closed hotel excluded. STR's comp-set figures are room-night
totals across those hotels, so larger hotels weigh more; Fondok applies no weighting of its own, and
a metric whose index is missing shows a dash rather than a substitute.

**Demand Growth and Supply Growth read the market study.** A CoStar submarket report (document type
`MARKET_STUDY`, whichever upload lane it arrived through) feeds both tiles, and every figure names
its document and field. Demand Growth is the report's demand (occupied room nights) growth: the
trailing-twelve-month figure first (`market_study.trend.ttm.demand_change_pct`), then the latest
actual year (`market_study.trend.<YYYY>.demand_change_pct`; the CBRE-style
`cbre_horizons.segment_all.<YYYY>.demand_change_pct` rows inside the same document count too). When
the report prints only the demand series, growth is derived as latest ÷ prior − 1 and both rows are
named. Forecast-tagged years are never shown as history and are labelled as forecast when they are
all the report has. Supply Growth is the pipeline over the existing inventory, with under
construction and final planning kept separate: under-construction rooms
(`under_construction.total_rooms`, or the listed projects summed by their status) ÷ existing
submarket rooms (`market_study.supply.existing_rooms`, or the latest actual year of the supply
series), and likewise final-planning rooms ÷ existing. The report's own supply change is shown
alongside, never substituted. A tile the reports cannot populate shows a dash with
{{reason:no_document}} when no market study is on the deal and {{reason:no_source}} (“not in the
uploaded reports”) when the reports are there but the series is not — no growth rate is ever
assumed.

:::

::: card
::: badges
::: badge {source=t12_actual name="T-12 Actual"}
Year-1 anchor extracted from the deal's uploaded T-12. Out-years are grown forward at the configured
expense / revenue growth rate.

:::

::: badge {source=cbre_horizons name="CBRE Horizons"}
Forecast curve extracted from an uploaded CBRE Hotel Horizons report (subject submarket +
chain-scale segment).

:::

::: badge {source=om_comps name="OM Comps"}
Median cap rate derived from the OM's "Comparable Sales" transaction-comps table.

:::

::: badge {source=om_broker name="OM Broker"}
Broker proforma value extracted from the OM. Treat with appropriate skepticism — these are the
broker's pitched numbers.

:::

::: badge {source=pnl_benchmark name="P&L Benchmark"}
Industry benchmark margin from a CBRE / HotStats P&L benchmark report. Since 2026-10-15 the model no
longer applies these by itself, so new runs do not carry this badge. An applied benchmark suggestion
shows as *Analyst override* with its note. The badge can still appear on older runs and exports.

:::

::: badge {source=analyst_override name="Analyst Override"}
Set via an inline editor, and carrying the analyst's own written justification. Wins over every
other source — but only a value that actually changed is recorded as one; see "What counts as an
override" and "Every override carries a justification" below.

:::

::: badge {source=str_subject_ttm name="STR · Subject TTM Actual"}
The subject property's OWN trailing-twelve-month occupancy & ADR as reported by STR — an actual, not
a forecast. This is what the model seeds the Base Year from when the STR basis is on: the forward
forecast's Month-12 point can land far below the subject's current performance, which would
understate the deal. Because the Base Year is a TTM window, Index Analysis shows N/A for the first
forecast year's growth whenever the last historical column is a reported actual (a P&L fiscal year
or an STR calendar year) — the two are not the same period basis.

:::

::: badge {source=str_comp_set name="STR Comp-Set Rates"}
The comp set's blended occupancy & ADR shown on the Market Comps tab, applied as the Year-1 input by
"Use STR rates in the model" (explicit overrides carrying the note "STR comp-set market rates
(Market tab)"). This is the competitive set's performance, not the subject's own — the Market Comps
tab states the comp-set benchmark and the model's Base Year as two separate figures and never claims
the benchmark is the assumption.

:::

::: badge {source=str_forecast name="STR Trend Forecast"}
The BASE STR forward-forecast scenario's Month-12 point — a projection of the subject, used only
when the subject's own TTM is not extracted. P&L → Future P&L shows "Active basis: Market / STR ·
Revert" when the rates carry any of these three tags, and the Market Comps tab's STR card reads the
same tags — "STR / Market basis active", "STR rates unavailable — using T-12 base", or "Pending
re-run" when the worker has not tagged the rates yet — never the request flag alone.

:::

::: badge {source=str_forecast_unavailable name="STR Unavailable"}
STR rates were requested but could not populate (no STR Trend extraction, coverage too low, or a
loader failure). The model stays on the T-12 base and says so — the STR seed is never silently
"active". Reason code: {{reason:str_unavailable}}.

:::

::: badge {source=derived_from_revpar_growth name="Derived from RevPAR Growth"}
An analyst RevPAR-growth override derives ADR growth — (1 + RevPAR growth) ÷ (1 + occupancy growth)
− 1 — with the occupancy path held, so the lever moves operating NOI. Setting ADR growth explicitly
takes direct control.

:::

::: badge {source=index_assumption name="Index Analysis Assumption"}
Index Analysis indexes the subject against one of three benchmarks, chosen on Market Comps → Index
Analysis: the **STR competitive set** (the default — subject STR TTM ÷ the published MPI / ARI, the
same blend Market Overview shows), a **market / chain-scale benchmark** (the market-wide or
chain-scale segment occupancy & ADR in the deal's CoStar / STR market reports), or a **CoStar
Property Analytics comp set** (only when that export has been extracted — otherwise the option is
disabled and says why). Each benchmark figure names its document and page. Four assumptions ride on
the choice — market occupancy growth, market ADR growth, and the MPI / ARI penetration targets —
each showing its document source, the documents a computed default used, or "Your override". They
feed the Future P&L only while "Use STR rates in the model" is on and the STR seed has landed: then
Year-1 occupancy = benchmark occupancy × MPI target, Year-1 ADR = benchmark ADR × ARI target, and
the growth rates replace the model's, each only where you set one and never over an explicit
override of that input. With the toggle off nothing moves. Index Analysis also restates the Future
P&L Base Year (revenue engine Year 1, its source and every step to it) beside its own figures, and
the STR subject TTM it indexes against is named as a different source and period. \
**Targets by
year.** The penetration targets can be set per model year (`index_mpi_target_by_year` /
`index_ari_target_by_year`; a by-year entry beats the all-years target). The all-years target drives
**Year 1 only**: Years 2+ are indexed only where a by-year target is set for that year or the
analyst overrides that metric's market growth; otherwise the year follows the model's own growth and
is flagged `scalar_year1_only`, so an existing deal never changes because a report's default growth
appeared. With the STR basis on, each indexed model year's occupancy = that year's benchmark
occupancy × MPI target ÷ 100 and ADR = that year's benchmark ADR × ARI target ÷ 100, where the
benchmark series follows the selected method: the STR or CoStar comp set is the reported TTM level
in Year 1 grown at the market occupancy / ADR growth assumptions after it; the market benchmark is
the reported level in Year 1 and the CBRE Horizons forecast for that model year's calendar year
after it (the segment table named like the chosen segment when the report carries one, else All
Hotels). RGI is derived — MPI × ARI ÷ 100 — never an input. A year with a target but no benchmark
value (no growth assumption, no CBRE year, no close date to place it) is flagged`
benchmark_unavailable`: the model keeps its own growth from the last driven year and no figure is
invented. Every driven year records the benchmark (value, document, page or growth step), the target
and the resulting occupancy / ADR on the revenue output (`revenue.index_drivers_by_year`) and in the
lineage of that year's rooms revenue; the Future P&L Assumptions sheet carries one row per metric
per year (`index_mpi_target_by_year.3`) so the targets round-trip through Excel with the same
justification rule. \
**Target units.** A target is index points 25–250 (100 = parity with the
benchmark) or a ratio 0.25–2.5 (1.0 = parity) — the two bands do not overlap, so a value names its
own unit: 1.10 is 110 points everywhere (the panel, the by-year grid, the penetration table, the
at-target opportunity and the model), never 1.1. The range is checked on every write *and every
read*: a legacy stored target outside both bands (e.g. 5, written before the check existed) is
ignored by the model and the grid — it never moves Year-1 occupancy or ADR — and the panel shows "—"
sourced "Stored override ignored" (never "Your override") with "Stored all-years MPI target 5 is
outside 25–250 index points … re-enter it"; a by-year entry out of range falls back to the all-years
target and is named the same way.

:::

::: badge {source=index_assumption name="Index Analysis · Columns & Rows"}
The Index Analysis year columns follow the acquisition close calendar — the
same`projection_calendar_years` the Future P&L prints: Year 1 is the calendar year of the first
operating month after close and sits in the first forecast column, the forecast columns are exactly
the model horizon (nothing is carried forward beyond it — no held-flat occupancy or 3% ADR
extension), and the six columns before Year 1 are history. With no close date the forecast columns
read "Year N" with a one-line note; a year is never guessed. Subject history is the STR Trend annual
subject figures (`ttm_performance.subject.annual.<YYYY>`) where the report carries them, else the
P&L fiscal year — a Source row says which per column. The comp-set table (titled **STR Competitive
Set** — it is STR data, never labelled a CoStar market table) is the STR TTM blend (subject ÷ MPI /
ARI / RGI) in the last historical column. Its forecast columns are the same series the STR comp-set
index method runs on: the TTM level as the Year-1 benchmark, grown at the market occupancy / ADR
growth assumption after it (the document default or your override), banded "comp set grown at market
growth (assumption)" with the growth source on hover. STR publishes no comp-set forecast, so this is
an assumption, never an STR figure; with no growth value the out-years stay "—" with the reason, and
the subject's growth is never re-scaled into them. The by-year fair share, variances and Revenue
Opportunity at Par fill from it. A separate Market row carries market-level actuals (market study /
STR market rows) by calendar year and, as its forecast, CBRE Horizons placed by the report's own
calendar year (`cbre_horizons.year_<n>.calendar_year`, pipeline v7) and labelled Market / CBRE
Horizons with document and page — a CBRE forecast is a market forecast, not a comp-set forecast. A
calendar shorter than the horizon is not extended: a model year the engine did not place reads "Year
N" with no calendar year. **Actual vs forecast labels.** Year 1 uses the reported TTM and Year 2
onward the CBRE Horizons forecast (market benchmark), so every benchmark value names its kind,
source and period with a worded chip — never colour alone — and a one-line label: **Actual** "Actual
· STR TTM May 2025 · \<document\>" (the STR TTM window's period end — the last month of the
subject's monthly series, else the report period the STR reader resolved — served as
`subject_period_end` on`GET /market/{id}/index-methodology` and `str_trend.ttm_period_end` on`GET
/deals/{id}/market-data`); **Forecast** "Forecast · CBRE Horizons 2027 · \<document\> p.N" (with the
report's publication date, `cbre_report_date`, on hover); and **Assumption** "Assumption · grown at
market growth" for the comp-set series past Year 1. Market-study and STR annual history, and P&L
fiscal years, are labelled Actual with their year and document. The by-year grid prints the chip and
label under each benchmark value plus one line naming the Year-1 actual and the Year-2+ forecast;
the Index Analysis tables carry the chip in their Source row and the full label in a Basis row. A
value with no source shows "—" and no chip. The labels are display only — the revenue engine's
by-year drivers are unchanged. Penetration rows follow the benchmark selected on Index methodology.
With the STR comp set: the STR-published MPI / ARI / RGI in the TTM anchor column; across the
forecast columns the analyst's MPI / ARI target for that model year where one is set (by-year, else
the all-years target; RGI derived as MPI × ARI ÷ 100) while "Use STR rates in the model" is on — so
the index rows only print a target the Subject rows actually run on — else the published index held
flat, with a one-line note when targets are set but not applied; the hover text says which. With the
market benchmark (or the CoStar comp set) selected, the index rows read "vs \<benchmark\>": the
anchor column is the subject STR TTM ÷ that benchmark × 100 and each forecast column is the subject
÷ that year's benchmark (the same by-year benchmark the model indexes against), or the analyst's
target where one applies — never the STR-published indices, which are against the comp set; the
subject ÷ market indices, occupancy (pts), ADR ($ and %) and RevPAR variances to the comp set, Fair
Share of Supply = subject rooms ÷ (comp-set rooms + subject rooms), fair-share occupancy, actual
share of demand, fair-share rooms revenue, and the signed Revenue Opportunity at Par and at Target,
the latter computed per year against the analyst-set target that applies to that year and priced on
the selected benchmark (target RevPAR = benchmark occupancy × MPI ÷ 100 × benchmark ADR × ARI ÷
100). It is gated exactly like the index rows: only while "Use STR rates in the model" is on,
otherwise "—" with "Targets set but not applied". A document default — the published index itself —
is not a target, so with none set the row is "—" and says so. Every cell shows its formula and
inputs on hover; a cell with no reported input is "—".

:::

::: badge {source=seed name="Seed Default"}
Platform default. Surfaced as a Seed badge with grey tone — no deal-specific data has overridden
this yet. The senior origination fee (1.50% of the senior loan) is one of these: a Fondok seed, not
a document-sourced figure, editable on Financing and the single source for the Sources & Uses
“Senior Loan Origination Fee” line and Overview “Financing Costs”.

:::

::: badge {source=deal_row name="Deal Row"}
Sourced from the deals table (entered on the create-deal wizard or PATCHed via the API). Project
name, city, brand, keys, service (the FON-115 property type — OM-only, fill-only, never guessed; see
services/property_type.py), the reopening / re-flag date — and the **purchase price**: the wizard's
"Purchase / Indicative Price" field is parsed to whole dollars ("$36.4M", "36,400,000"; a range such
as "$120-140M" stores its *midpoint* and is labelled "range midpoint" on the Review step) and saved
as `purchase_price`, which seeds the model's purchase price with this badge rather than "Seed". A
bare figure under $1,000,000 with no unit ("36400", "1,200") and a two-year period ("2024-2025") are
not read as prices — the hint asks for M / MM or the full dollars. Text that does not read as a
price is kept as a note and sends nothing — the model keeps its seed until the analyst sets the
price on CAPEX → Deal Summary, where it stays editable. The Property Name is not a deal-row field —
see below.

:::

:::

:::

::: card
#### What counts as an override

Opening a field to inspect it never changes its lineage. An override is recorded only when the saved
value **differs from the current effective value** after normalisation — the draft you type and the
value the engine stores are compared in the field's own unit, so "6.80" typed over a stored 0.068,
"65" over 0.65, and "$23,660,000" over 23660000 are all the same number and write nothing. Every
editor exits without writing on **Cancel, Esc or a click outside**, and a Save that changed nothing
says so ("No change — provenance unchanged") instead of persisting a duplicate.

The same rule holds server-side, for overrides already stored: a value equal to what the deal
resolves to on its own is reported under **its source's badge** (T-12, OM, benchmark or seed), not
as an analyst override. The stored override is left exactly as the analyst wrote it and the engines
still read it, so no number moves — only the claim about where the number came from. Use **Clear
override** in the provenance popover to remove one.

:::

::: card
#### Every override carries a justification

An override that **changes a number** carries an analyst justification. It is required at the moment
of the edit, stored with the value in the deal's `field_overrides` as `{ value, note }`, and shown
to reviewers in the provenance popover next to the number and on the override's row in the activity
trail. The rule is enforced in the API as well as in the editor: a save that changes an engine input
without a note is rejected (`422 override_note_required`), so there is no path — UI, script or
integration — that can record an unexplained number.

The justification is never written for you. Fondok stores what the analyst typed or nothing at all;
it never fills the field with a generated sentence about which page the edit was made on.

**What is exempt, and why.** The requirement follows one rule — a note is needed when the key routes
into engine input. Edits that change no modelled number need none: the Historical P&L worksheet's
row layout (`worksheet_layout`, presentation only), the **Stabilization Year** (it selects which
projection year the stabilized figures are read from and moves no return), the **Property Name** (a
name, not a number), the Completion Guarantee covenant status (qualitative — no covenant math), the
number of promote tiers in the waterfall (its shape, not a value in it), and the IC memo's prose.
Clearing an override is a revert to source, not an override, and needs no note either.

**Target LIRR (levered IRR), Target MOIC and Target Stabilized Yield on Cost are not overrides.**
They are stored on the deal as stated objectives — the benchmark the returns are read against and
the hurdle the Max Price Solver prices to — not replacements for a value Fondok sourced from a
document. They are editable without a justification, and a value that came from a source is never
behind them. Target Stabilized Yield on Cost is kept in the deal's `field_overrides` as
`target_stabilized_yoc` and is exempt from the note rule on both the browser and the API for the
same reason.

:::

::: card
#### Project Name vs Property Name

**Project Name** is the analyst's confidential deal identifier (e.g. "Project Unicorn") — a deal-row
field you set and rename on the Overview; document extraction never writes it. **Property Name** is
the asset as named in the offering documents (OM first, then the STR subject name) and is never
inferred from the Project Name — it shows "—" until the OM is extracted ({{reason:no_document}}).
The two are stored independently and editing one never changes the other. An analyst may override
the Property Name from its Overview row: the override is stored as
`field_overrides["property_overview.name"]`, the extracted value and its source page are preserved,
and "Restore sourced value" drops the override so the extracted name comes back.

:::

::: card
#### Which documents set the deal's key count, brand and city

Only documents about the subject property — the Offering Memorandum, a T-12, P&Ls, a Hotel Program
document or an Other Property Info sheet — can set the deal's key count, and when more than one of
them states a room count the Offering Memorandum wins. The deal carries two brands: the existing
brand comes from the Offering Memorandum and is filled only while it is empty, and the proposed
brand is the analyst's own choice and is never touched by a document. Market reports, STR reports,
comparable sets, CapEx budgets, insurance documents and leases describe other things, so they never
change the key count, brand or city. The city is filled only while it is empty, and never from a
street address. Until a qualifying document has been extracted the deal header shows "—" for keys
rather than a count of zero.

{testid=brand-positioning-methodology} **Brand and positioning.** Positioning is a deal setting, chosen on the wizard's Brand and
Positioning step and editable on the Overview's Investment Profile card. Choosing a brand *suggests*
that brand's **STR chain scale** (STR's published chain-scale table, February 2026) wherever STR
lists the brand; a brand STR does not cover suggests Fondok's own classification, and a brand with
no single scale (Independent / Unflagged, a multi-scale collection) suggests nothing. A suggestion
is only that: in the wizard the suggested tier card is marked "Suggested from brand" and the tier
changes only when you select a card; on the Overview, while the deal has no tier ("Default"), the
current flag's scale is offered beside the Positioning select with a one-click *Use*, and changing
the flag writes only the brand. A deal created without selecting a tier therefore has none
("Default") until you choose one or the Offering Memorandum suggests one (below). This is what keeps
the recorded source honest — "Analyst" means a tier you actually chose — and leaves the OM able to
suggest; a tier the wizard filled from the brand would have been recorded as yours and would have
blocked the OM for good. Deals created before 17 October 2026 may carry a tier the wizard filled
from the brand; it is stored and labelled as the analyst's. The analyst can set a different tier for
this property at any time, and the stored value is what the model reads: it is the subject's chain
scale for the comparable-sales match and for which portfolio P&L library entries may contribute
ratios. "Default" means no tier was chosen. Then no chain-scale filter is applied to the portfolio
P&L library, and the comparable-sales match uses an approximate scale from the deal's service type
(Luxury → Luxury; Full Service or Resort → Upper Upscale; Select Service → Upper Midscale; Limited
Service → Midscale), or no chain-scale match at all when the service type is blank. **Positioning
from the Offering Memorandum's brand.** When an Offering Memorandum is extracted and the deal has no
tier ("Default" or blank), the brand the OM names is looked up in the same brand catalog the wizard
uses. Only a confident match counts: the brand's full name, one of its listed aliases, or its short
form ("Kimpton" for Kimpton Hotels & Restaurants, "Hampton" for Hampton by Hilton), compared without
regard to case, accents or punctuation; or a brand string that starts with one of those followed
only by a property name ("Kimpton Angler's"). There is no similarity guessing: a name shared by
several brands ("Starwood"), a string that also names a second brand ("Marriott Courtyard"), or a
brand that is not in the catalog suggests nothing. When the matched brand has an STR chain scale,
that scale becomes the deal's positioning; a brand STR does not publish a single scale for
(Independent / Unflagged, multi-scale collections, brands with no STR row) suggests nothing, and
Fondok's own classification is not used here. The Overview shows where the tier came from beside the
Positioning select — "From OM brand Kimpton → STR Upper Upscale (document, page)", or "Analyst" for
a tier chosen in the wizard or on the Overview — and the page reads "page not cited" when the
extraction did not record one. A third chip, "Source not recorded", marks a tier stored before
Fondok began recording where positioning came from (the source columns were added without rewriting
history, so no provenance is asserted that was never recorded); choosing a tier on the Overview
records that choice as the analyst's. A tier the analyst chose, or one already stored, is never
replaced by an OM; a later OM never replaces an OM-filled tier either; choosing another tier
overrides it and is recorded as the analyst's; setting it back to "Default" clears the source, so
the next OM may suggest again. Only the OM suggests positioning — T-12s, P&Ls, market studies and
STR reports never do. The suggestion is applied when the OM is extracted and takes effect on the
next model run; deals extracted earlier keep their stored positioning.

{testid=market-submarket-methodology} **City, market and submarket.** These are three separate deal fields. **City** is the property's
city (optional; filled from the Offering Memorandum only while empty, as above). **Market** and
**Submarket** are the analyst's choice on the wizard's first step, shown — and correctable at any
time — on the Overview's Investment Profile card; no document ever writes them, and no engine reads
them today, so changing them never re-runs the model. How a market is chosen: when STR's own market
/ submarket definition list has been loaded, the Market field suggests STR's markets and the
Submarket field suggests that market's STR submarkets. Until it is loaded — which is the case today
— the Market field suggests a name-only list of STR-defined U.S. market names (shortened to
principal city + state; no STR identifiers), and the Submarket field suggests nothing, because
Fondok has no submarket names of its own. Typing a market or submarket that is not suggested is
always allowed. **What the STR id means:** the deal stores STR's market identifier (`str_market_id`)
only when the market typed or picked matches exactly one market in STR's loaded list; it is STR's
own code for that market, copied from STR's file, and the worker refuses any id that is not in the
loaded list. A typed market, or any market saved while STR's list is not loaded, is stored with no
STR id and the Overview says so (“Analyst-entered · no STR id”). Fondok never invents an STR
identifier or a submarket name. STR's list is shared reference data, identical for every workspace
and holding no deal data; a deal's choice is stored on the deal itself.

:::

::: card
#### Which statement is the Year-1 basis

Year-1 occupancy, ADR, revenue lines and expense lines all come from one statement: the document the
Data Room badges as **Primary source**. The engines and the badge now share one ranking — full-year
statements before partial ones, the most recent period first, and a T-12 ahead of a calendar-year
P&L of the same year. A T-12 whose extraction carries no period label is still treated as a
trailing-twelve, and when it carries no date at all it is taken as the current period, because an
analyst uploads a T-12 as the latest statement. Older annual P&Ls never blend into Year-1: a line is
only corroborated across statements that describe the same period (two uploads of the same T-12, or
a summary and a detailed P&L of one year), so every Year-1 number traces to a single row of the
primary statement. The earlier years still feed the Historical P&L grid and the variance checks.

:::

::: card
#### Hover any number to trace it

Provenance runs on two levels, and every figure on the platform carries one or the other:

- **Inputs — “where did this come from?”** Assumptions (occupancy, ADR, growth rates, LTV, rate, exit
  cap) show a source badge / dotted underline colored by origin: green when grounded in the deal's own
  docs (T-12, OM, CBRE), amber when it's still a seed/benchmark default, violet when an analyst
  overrode it. When it's backed by a document the badge is clickable — a {{icon:Link2}} jumps you to
  that source in the Data Room.
- **Outputs — “how was this computed?”** Modeled values (rooms & total revenue, NOI, GOP, debt
  service, DSCR, equity multiple, gross sale, IRR) hover to show the exact formula plus every named
  input, and each input chains one hop further — back to a source document, a seed/benchmark, an
  analyst override, or another computed value. Follow any number to ground.
- **NOI pin (reconciliation override).** A deal can carry `noi_override_by_year` — an analyst-entered
  per-year NOI schedule (an analyst-entered schedule used to reconcile to a source model) — and
  optionally `terminal_noi_override` for the exit-year reversion NOI. While either is set, the Debt
  and Returns engines read that schedule instead of the operating model, so RevPAR-growth / expense
  edits do not move NOI. P&L → Future P&L shows an "NOI pinned to an analyst schedule" notice whenever
  the pin is present (and says when terminal NOI is also pinned) — the operating model's own figure is
  withheld under {{reason:pin_active}}; **Clear pin** deletes the override(s) from `field_overrides`
  and re-runs the model, after which NOI follows the operating assumptions again.
- **IRR is calculated, and says so.** IRR has no closed form — it's the discount rate that sets the
  NPV of the equity cash flows to zero, solved iteratively (Newton's method, bisection fallback). Its
  hover states that explicitly and lists the year-by-year cash-flow stream the solver ran over.
- **Total Revenue is the statement's own line.** In P&L → Historical P&L the Total Revenue row shows
  the statement's own stated total when it publishes one (`p_and_l_usali.total_revenue_usd`), as an
  extracted cell whose SOURCE panel names that field, page and confidence. Only when the statement
  states no total is the row the sum of the extracted revenue lines (Rooms + F&B + Other), and then it
  carries a *calculated* label. The two can disagree — a statement may state $13.48M while the lines
  it itemises sum to $12.3M — and the **Revenue footing** row under the grid always compares the sum
  of the lines against the stated total and shows the difference; the cell is never footed against
  itself, and no total is invented when neither exists. A stated total adds no "to review" count of
  its own: it is reviewed through the lines that foot to it. Correcting a revenue line at source
  re-sums a derived total; a stated total stays what the statement says, and the footing check carries
  the resulting difference.
- **Every historical column states its period.** A statement is read on one of three bases and the
  column says which: a **full year** ("FY2024"), **year-to-date** through a month end ("YTD Mar
  2025"), or a **trailing twelve months** ending at a month end ("T12 Mar 2025"). The basis comes from
  the statement's own `period_type`, and failing that from its classified document type — the same
  resolution the engines use, so the column header and the number cannot disagree. The Period control
  on P&L → Historical P&L filters the columns by that basis, and the SOURCE panel names the period
  alongside the document ("T-12 ending Mar 31, 2025") so a "2025" column is never ambiguous between a
  year-to-date and a trailing twelve. When the basis cannot be established — no stated period type,
  and a classification that would have to be guessed at — the column renders the **bare year with a
  "basis unknown" chip** under {{reason:period_mismatch}}, never an invented FY or T-12. A period-end
  date that falls outside the column's own year is likewise dropped rather than shown.
- **Primary financial source.** When several statements cover the same periods, Fondok ranks them —
  full-year sources over partial (monthly / YTD), most-recent period, then most-detailed — and badges
  the winner “Primary source” in the Data Room so it's clear which statement drives the historicals.
- **Cross-checked across statements.** When more than one full-year statement is uploaded, Fondok
  doesn't just trust the top-ranked one — it cross-checks each revenue and expense line across all of
  them and grounds Year-1 on the corroborated (median) value. So if a single statement mis-reads one
  line — say F&B revenue comes through an order of magnitude low — the other full-year statements
  outvote it and the model isn't skewed by one bad extraction. A line reported by only one statement
  is used as-is.
- **One review state, Data Room ↔ P&L.** An extracted historical value is "to review" when its
  extraction confidence is below 85% and it hasn't been accepted or corrected — and only if it has a
  cell in P&L → Historical P&L (a value with nowhere to land is never counted). The Data Room's
  per-statement "N to review" badge is exactly the number of red cells in that statement's column; the
  global count is their sum. Every red cell is pinned to its own column's statement, so its SOURCE
  panel names — and its Accept / Edit acts on — that document, never another year's. Clicking a badge
  opens the Historical P&L with that statement's column pinned and the first flagged cell in view;
  accepting or correcting a value clears the cell and both counts at once. Two statements that resolve
  to the same period keep separate columns ("2023", "2023 (2)") and the coverage strip says so.

:::

::: card
#### As-of rule

An underwriting is a claim about what was knowable on a date. When a deal sets an underwriting as-of
date, Fondok will not ground a market assumption on a report published after it: a CBRE Horizons
forecast, an OM comparable-sales table or an STR report dated later is refused, the assumption keeps
the basis it already had, and the badge says *unavailable* with the reason
{{reason:not_knowable_as_of}} — never a silent substitution. The comparison respects how precisely
the report is dated: a document known only to a year is compared year-to-year, a quarter
quarter-to-quarter, so a report from the underwriting year is admitted rather than refused on a
31-December stamp. A report whose date cannot be established is *used*, flagged
{{reason:as_of_unknown}} — a missing date is a caveat, not grounds to withhold a number. The same
date anchors the comparable-sales engine: the five-year lookback and the recency weighting are
measured from the underwriting date rather than from today, so re-opening a deal months later does
not quietly age its comp set out of the window. A deal that has not set an underwriting as-of date
is unaffected in every respect — the acquisition close date is a modelling input and is never read
as a knowledge horizon.

:::

::: card
#### The full ledger

The Sources ledger (linked from every traced figure's tooltip) lists every modeled assumption, its
value, and its origin in one table — filter to the ungrounded set to see exactly what's still riding
on a default, and export the whole ledger to CSV so every figure in the IC memo is defensible.

:::

:::

