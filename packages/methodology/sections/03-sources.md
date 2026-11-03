---
id: sources
title: "Where market assumptions come from"
decisions: [FON-61, FON-74, FON-96, FON-109]
anchors:
  - brand-positioning-methodology
  - implied-value-methodology
  - market-submarket-methodology
  - market-data-policy-methodology
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
not a forecast. Runs before 2 November 2026 could seed the Base Year from it through the retired
"Use STR rates" switch; it is now the reference the current STR index is computed from.

:::

::: badge {source=str_comp_set name="STR Comp-Set Rates"}
The comp set's blended occupancy and ADR shown on the Market Comps tab — the competitive set's
performance, not the subject's own. Since topline methodology v2 it is the model's benchmark by
default: every projected year is this level grown at the market growth × the hotel's penetration.

:::

::: badge {source=str_forecast name="STR Trend Forecast"}
The forward-forecast scenario's month-twelve point — a projection of the subject. Retired as a model
input on 2 November 2026 (the STR forecast chart on Market Comps stays); the badge remains on runs
saved before then.

:::

::: badge {source=str_forecast_unavailable name="STR Unavailable"}
STR rates were requested but could not populate (runs before 2 November 2026, when the retired
"Use STR rates" switch existed). The model then stayed on the T-12 base and said so.

:::

::: badge {source=topline_benchmark name="Benchmark × penetration"}
The year's occupancy or ADR is the benchmark for that year × the hotel's penetration ÷ 100, with
the pre-stabilization ramp (topline methodology v2). The benchmark's own badge, report and period
are named beside it.

:::

::: badge {source=costar_comp_set name="CoStar Comp Set"}
A CoStar Property Analytics competitive set uploaded to the deal — the benchmark when no STR comp set
is on file.

:::

::: badge {source=costar_submarket name="CoStar Submarket"}
The latest actual year of the deal's CoStar submarket report for the hotel's class (the whole
submarket, labelled, when the class is not in the report or no positioning tier is set) — the
benchmark when no competitive set is on file.

:::

::: badge {source=costar_market name="CoStar Market"}
Market-wide occupancy and ADR from the deal's CoStar market report — the benchmark when neither a
competitive set nor a submarket series is on file.

:::

::: badge {source=market_benchmark name="Market Benchmark"}
Market-wide occupancy and ADR from another uploaded market report — the benchmark when neither a
competitive set nor a submarket series is on file.

:::

::: badge {source=costar_submarket_forecast name="CoStar Submarket Forecast"}
The year's occupancy or ADR change from the deal's CoStar submarket report for the hotel's class,
with the report, page and class named — a market forecast, not the hotel's own.

:::

::: badge {source=growth_carried_forward name="Past the Forecast"}
The year is past the last year of the market forecast: ADR grows at the average of that forecast's
years (the label names them, for example "2025–2029 forecast average") and occupancy holds at its
last forecast level — an assumption, labelled with the report it extends.

:::

::: badge {source=occupancy_held_at_stabilization name="Held After Stabilization"}
Occupancy is flat after the stabilization year: it holds at the stabilized year's level while ADR
keeps growing. Only an occupancy growth you enter for that specific year moves it.

:::

::: badge {source=market_forecast_unavailable name="No Market Forecast"}
No market forecast and no figure of yours covers the year: the rate is held flat (growth 0 is the
absence of growth, not a rate) and the Future P&L asks you to set growth. The 4.5% figure is offered
as a labelled suggestion and is applied only if you choose it.

:::

::: badge {source=costar_withheld_by_policy name="Withheld by Data Policy"}
A CoStar forecast or benchmark is on file, but the workspace's data policy withholds CoStar-derived
defaults. A client's own uploaded CoStar report is allowed by default; data Fondok licenses is
withheld until the licence covers the workspace. Nothing is shared between workspaces.

:::

::: badge {source=penetration_default name="Penetration Default"}
Parity with the benchmark (index 100) at stabilization, with the 85% / 95% ramp in Years 1 and 2 for
a Year-3 stabilization — a labelled suggestion; the hotel's current STR index is shown beside it.

:::

::: badge {source=derived_from_revpar_growth name="Derived from RevPAR Growth"}
A RevPAR-growth override derives ADR growth with the occupancy path held, so the lever moves
operating NOI. Setting ADR growth explicitly takes direct control.

:::

::: badge {source=str_segmentation name="STR Segmentation"}
A demand-segment figure the deal's STR Segmentation report states: a segment's share of room nights,
the transient channel split when the report's channel-mix block carries it, or a segment ADR.

:::

::: badge {source=platform_default_segmentation name="Platform default — no segmentation data on file"}
A segment-model figure the STR Segmentation report does not state, filled by a platform default:
every channel cost (BAR 2%, OTA 20%, corporate 8%, group 5%, contract 2%), the 60/30/10 BAR / OTA /
corporate split of transient demand when the report has no channel-mix block, and a segment ADR at
its ratio to the property ADR (corporate 90%, group 92%, contract 65%). A suggestion, never labelled
as STR data; the Revenue Mix sheet and the memo name these figures under the table. Runs saved
before 30 October 2026 carry the older “Segment default” badge on every segment figure.

:::

::: badge {source=platform_default_renovation_split name="Platform default split"}
The renovation budget's breakdown into hard costs, soft costs and professional fees at the platform
default 75% / 15% / 10% — no itemized renovation budget is on file.

:::

::: badge {source=index_assumption name="Index Analysis Assumption"}
Runs before 2 November 2026: a rate set by the Index Analysis assumptions while the retired "Use STR
rates in the model" switch was on. Since topline methodology v2 the penetration targets are the
model's penetration at stabilization (all years) and per year, applied on every deal with no switch;
a per-year entry beats the stabilized target for that year, and the ramp applies before the
stabilization year. The market growth assumptions became the per-year growth of the Future P&L; a
value stored under the old names is read as your all-years growth and says so. \
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
price is kept as a note and sends nothing. There is no default price: until a price is set, the
engines that need it (capital, debt, returns, sensitivity, partnership and cash flow) do not run and
say so. Enter the price at any time on the Overview (Entry Valuation) or on CAPEX → Deal Summary —
the CAPEX tab shows the Purchase Price and Keys inputs even when the model could not run, pre-filled
from the OM's stated asking price when it has one (it fills in once the OM has been read), or saying
the OM does not state one; it never claims there is no OM when the Data Room could not be read. Your
entry is saved as an Analyst Override with its note and the model re-runs. The order is your entry,
then the deal record, then the OM's asking price; a price is never inferred from a broker cap rate.

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
row layout, the Property Name, the completion-guarantee status, the number of promote tiers, and the
IC memo's prose. The Stabilization Year, the topline benchmark level and the reopening date DO need
one: they move where the penetration ramp ends or which benchmark Year 1 is set from. Clearing an
override is a return to source and needs no note either.

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

{testid=market-submarket-methodology} **City, market and submarket.** These are three separate deal
fields. City is the property's city, filled from the OM only while empty. Market and Submarket are
chosen on the wizard's first step and correctable on the Overview; no model reads them, so changing
them never re-runs the model. When the CoStar market list is available to your workspace, the Market
field suggests CoStar's markets — metro markets named by city and state, regional markets named in
words — and the Submarket field suggests that market's CoStar submarkets, each shown with its market
so two submarkets sharing a name stay distinguishable. A pick is recorded as a CoStar market or
submarket; typing a market or submarket that is not suggested is always allowed and is recorded as
analyst-entered. Whether the list is available depends on CoStar's licence: until it is confirmed,
other workspaces see a public list of STR market names and no submarket suggestions. An Offering
Memorandum may fill an empty Market and Submarket only when the city and state it states match
exactly one metro market in the list, and its submarket phrase exactly one of that market's
submarkets; a regional market is never inferred from a city, two possible matches fill nothing, and
a value you entered is never replaced. The Overview says where the market came from. Fondok never
invents a market, a submarket or an identifier, and never shows you a vendor identifier.

{testid=market-data-policy-methodology} **Market defaults may be withheld for a workspace.** A market default derived from a
licensed vendor's report (a CoStar submarket forecast, for instance) may be withheld for a workspace
until that licensing is confirmed for it; a withheld default is shown as not applied with its reason,
never replaced by a number, and the workspace's own uploaded figures are unaffected.

:::

::: card
#### Which statement is the Year-1 basis

Year-1 occupancy, ADR, revenue lines and expense lines all come from one statement: the document the
Data Room badges as **Primary source**. The model and the badge share one ranking — full-year
statements before partial ones, the most recent period first, a T-12 ahead of a calendar-year P&L of
the same year, then the more detailed statement. Two statements are never averaged: every Year-1
number traces to a single row of one statement. Earlier years still feed the Historical P&L and the
variance checks.

Statements count as the same period only when they cover the same span ending on the same date; a
monthly or year-to-date statement never competes with a full year. Between statements of one
period, the one whose revenue lines add up to its own stated total revenue is preferred: if the
top-ranked one lacks a full-year rooms revenue line or does not add up, and another adds up, the
other supplies the whole year. A statement that states no total cannot be checked, and is not
passed over for that. A line the chosen statement does not carry is read from another statement of
the same period first, then from the most recent statement covering twelve months; a monthly or
year-to-date figure is never scaled up, and a statement more than two years older than Year 1 is
not used — the platform default is, with a note. The figure is marked **Needs review** with a note
naming the documents and values when statements of one period disagree by more than 5% on rooms
revenue, F&B revenue, total revenue or NOI, when a line comes from another statement, or when the
statement's own revenue lines do not add up to its total. The document link on each figure opens
the statement the model used.

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
- {testid=implied-value-methodology} **Implied Value is priced like the sale.** The Future P&L's
  Implied Value row is each year's Cash NOI — after property taxes, insurance and the FF&E reserve —
  ÷ the exit cap rate the returns model ran on; the Exit Year column shows the model's own gross sale
  price, the figure IRR is computed from. It never capitalises EBITDA, and until the returns model has
  run it is a dash with the reason — no default cap rate stands in.
- **Other Revenue is both lines.** The Historical P&L's Other Revenue row is Other Operated
  Departments plus Miscellaneous Income, each as the statement states it — the same pool the Future
  P&L projects. A statement that states neither shows a dash.
- **NOI foots to GOP.** Below GOP the Historical P&L carries every line a statement states: management
  fee, FF&E reserve, property taxes, insurance, rent and other non-operating expense (together, Total
  Fees & Fixed), then non-operating income. The NOI footing row under the grid checks GOP − Total Fees
  & Fixed + non-operating income against the statement's NOI, and where a year does not foot it says
  so with the amount — "doesn't foot by $X — source gap". Non-operating income takes the sign the
  statement's own non-operating total implies, and rounding in the figures as read is never a gap.
- **Every historical column states its period.** A statement is read as a full year, a year to date
  through a month end, or a trailing twelve months ending at a month end, and the column header says
  which. When the basis cannot be established the column shows the bare year with a “basis unknown”
  chip, never an invented period.
- **Cross-checked across statements, never blended.** When more than one statement covers the same
  period, Year 1 takes its lines from one of them — the one whose revenue lines add up to its stated
  total, else the top-ranked — and the others only check it or fill a line it lacks, with a review
  note. Two statements are never averaged. A line is read from its own section of the statement,
  so a cost line labelled with a revenue name is never read as revenue.
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
index_assumption, deal_type_default, management_agreement — and the badge text comes from the
concept registry. The loader starts every key as a platform default with a reason (no document of
the needed type, or no field that resolves to the concept), then lets grounded sources overwrite
it, then the firm's portfolio P&L ratios, then the hotel management agreement's fee terms (base fee
% and FF&E reserve %, from an extracted LEASES / CONTRACT document), then analyst overrides; a P&L benchmark is never applied by the loader —
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
on every deal (topline methodology v2): the all-years target is the stabilized penetration and a
by-year target sets that year.

:::

::: card
#### Comparable sales and the as-of rule

<!-- source: apps/worker/app/engines/comp_sales.py (select_comps — the one comp selection: analyst exclusions, COMP_LOOKBACK_YEARS 5 measured from the lookback anchor; exit_cap_from_comps — the one exit-cap derivation: three or more comps with cap rates 4–11%, median), apps/worker/app/services/engine_runner.py (_load_om_transaction_comps_cap_rate and _build_comp_sales_set share _load_comp_transactions, comp_lookback_anchor — underwriting_as_of, else comp_report_as_of, else run_date — select_comps and exit_cap_from_comps; NOT_KNOWABLE_AS_OF, AS_OF_UNKNOWN) -->
The comparable-sales lookback is five years, measured from the underwriting as-of date when one is
set; otherwise from the comp table's own date (the offering memorandum's report date), and from the
run date only when neither exists. The anchor date and where it came from are recorded on the exit
cap's provenance and with the run's inputs, so a re-run that moves the exit cap says why. A report published after the as-of date is refused (reason: not knowable
as of); a report whose date cannot be established is used and flagged (reason: as-of unknown). The
exit-cap anchor is the median of three or more comparable sales whose cap rates fall between 4%
and 11%. The Comparable Sales tab and the model read the same comp set through one selection and
one derivation, so the tab's median and the model's exit cap cannot disagree: a comp you exclude on
the tab, a comp sold more than five years before that anchor date, and a comp outside the
4–11% band leave both, each with a note on the tab; with fewer than three plausible comps neither
derives a cap rate and the model stays on its labelled default.

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

<!-- source: apps/worker/app/services/engine_runner.py (primary-statement ranking shared with the Data Room badge: full-year before partial, most recent period first, T-12 ahead of a calendar-year P&L of the same year; _choose_period_statements: one statement per period, _SAME_PERIOD_CONFLICT_TOLERANCE 0.05, _SAME_PERIOD_OUTLIER_TOLERANCE 0.25, three or more statements; _resolve_statement_lines: exact alias first, no tail match through the opposite section; _loader_notices: needs_review) -->
One statement supplies Year-1 occupancy, ADR, revenue lines and expense lines: the one the Data
Room badges Primary source. A document whose extraction holds only loan terms and no statement line
(a debt template) is never that statement, even under an analyst's T-12 or P&L tag. The ranking is full-year statements before partial ones, the most
recent period first, a T-12 ahead of a calendar-year P&L of the same year, then the more complete
extraction. Two statements are never averaged. A line the chosen statement does not carry is read
from the next statement of the same period, then from the most recent statement whose line covers
twelve months (a full year, or a trailing twelve: a monthly package whose dated T12 sheet the
extraction typed as one, or — for extractions made before that — an unlabelled statement whose
monthly block spans twelve months), never from a monthly or year-to-date slice and
never from a statement whose period ends more than two years before the Year-1 basis period; then
the labelled default. Every line not read from the chosen statement, and every line left on the
default for those reasons, is marked {{reason:needs_review}} with its source document and period.
Every statement that supplies Year-1 revenue lines is footed on its own; one with no full-year
rooms line, or whose lines miss its stated total, marks the revenue-derived assumptions it grounds
{{reason:needs_review}} with the gap.

<!-- source: apps/worker/app/services/engine_runner.py (_resolve_statement_lines, _COST_SECTION_SEGMENTS / _REVENUE_SECTION_SEGMENTS; _choose_period_statements, _HEADLINE_LINES, _SAME_PERIOD_CONFLICT_TOLERANCE 0.05, _SAME_PERIOD_OUTLIER_TOLERANCE 0.25; _loader_notices) -->
A line resolves by its exact field path first; only when no exact path exists does a field whose
last segment names the line count, and never one under a cost or expense section for a revenue line
(or a revenue section for an expense line). Insurance and property taxes are read wherever an
annual statement files them — under fixed charges or under non-operating expenses, with or without
a `_usd` suffix — but never from a prior-year, budget or per-key row. A period is a full year identified by its stated period
end (else its fiscal year; an undated T-12 is the current trailing twelve months), or a partial
period identified by a stated end and its period type; a partial statement with no stated end stands
alone, and no date is ever inferred into a note. A fiscal year known only by its year is the
calendar year ending Dec 31 only when the deal's dated annual statements all end Dec 31 or its
monthly block runs January to December. Within a period the chosen statement is: the best-ranked
one whose rooms + F&B + other + miscellaneous + resort-fee revenue foots to its stated total
revenue (to the half-units the figures were read at, as on the Historical P&L) when the top-ranked
one fails — no full-year rooms line, or lines that miss the total; a statement with no stated total
is unverifiable and is never passed over for that; else, with three or more statements, the next one when the top-ranked
is more than 25% off the median on a headline line; else the top-ranked. Statements are compared on
rooms revenue, F&B revenue, total revenue and NOI; a difference above 5% of the larger value marks
every assumption grounded on the chosen statement {{reason:needs_review}}, naming both documents,
both values, the statement used, why, and which lines came from which other statement.

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
a forecast. Runs before 2 November 2026 could seed the Base Year from it through the retired "Use
STR rates" switch (`revenue_seed_from_str_forecast`, now ignored and reported in
`topline_status.legacy_flags`); since topline v2 it is the numerator of the comp-set blend and of
the current MPI / ARI printed beside the penetration default. Because the Base Year is a TTM window, Index Analysis shows N/A for the first
forecast year's growth whenever the last historical column is a reported actual (a P&L fiscal year
or an STR calendar year) — the two are not the same period basis.

:::

::: badge {source=str_comp_set name="STR Comp-Set Rates"}
The comp set's blended occupancy & ADR shown on the Market Comps tab (subject STR TTM ÷ MPI / ARI from
one report, `market_comp_set.derive_ttm_blend`). Since topline v2 it is the `comp_set` benchmark level
(`services/topline_benchmark.py`, `topline_status.benchmark.source_id = str_comp_set`). Explicit
`starting_*` overrides carrying the retired note "STR comp-set market rates (Market tab)" are treated
as a legacy seed (`treated_as_legacy_seed`): the benchmark × penetration × ramp sets Year 1.

:::

::: badge {source=str_forecast name="STR Trend Forecast"}
The BASE STR forward-forecast scenario's Month-12 point — a projection of the subject. Retired as a
model input on 2 November 2026 (`_load_str_forecast_for_seed` removed); the 24-month STR forecast
engine stays for the Market Comps chart, and the label remains on runs saved before then.

:::

::: badge {source=str_forecast_unavailable name="STR Unavailable"}
Runs before 2 November 2026 only: STR rates were requested through the retired switch but could not
populate; the model stayed on the T-12 base and said so. Reason code: {{reason:str_unavailable}}.

:::

::: badge {source=topline_benchmark name="Benchmark × penetration"}
`starting_occupancy` / `starting_adr` set by the topline (`services/topline.py::build_topline`):
Year-1 benchmark × penetration ÷ 100 (per-year override, else stabilized penetration × the Year-1
ramp), occupancy capped at 0.95. The engine evaluates every year the same way
(`engines/revenue.py::_plan_topline`, `RevenueEngineInput.topline`) and echoes one
`revenue.topline_by_year[]` record per year (benchmark level / value / label, applied penetration,
`ramp_pct`, `ramp_state`, growth source and label, T-12 reference, `capped_95`, status `driven` /
`benchmark_unavailable` / `flat_no_growth` / `explicit_value`); `revenue.topline_methodology_version`
is 2.

:::

::: badge {source=costar_comp_set name="CoStar Comp Set"}
Benchmark level `comp_set` read from `costar_comp_set.(occupancy|adr)*` rows when no STR comp set
is available; skipped (with the reason) under a `costar_derived_defaults: withhold` policy.

:::

::: badge {source=costar_submarket name="CoStar Submarket"}
Benchmark level `submarket`: `market_study.class.<bucket>.<YYYY>.(occupancy_pct|adr_usd)` for the
latest year tagged `actual` (bucket from `deals.positioning`: luxury / upper upscale →
`luxury_upper_upscale`, upscale / upper midscale → `upscale_upper_midscale`, midscale / economy →
`midscale_economy`, anything else → `all`, the overall `market_study.trend` series, labelled).

:::

::: badge {source=costar_market name="CoStar Market"}
Benchmark level `market` from CoStar-vendor market rows (`market_study.market.*`,
`pnl_benchmark.market.*` in the MARKET_STUDY lane).

:::

::: badge {source=market_benchmark name="Market Benchmark"}
Benchmark level `market` from a non-CoStar market report (`ttm_performance.market.*`,
`pnl_benchmark.market.*`); CBRE Hotel Horizons All Hotels levels carry `cbre_horizons`.

:::

::: badge {source=costar_submarket_forecast name="CoStar Submarket Forecast"}
Growth INTO a calendar year from `market_study.class.<bucket>.<YYYY>.(occupancy|adr)_change_pct`
(`services/topline_growth.py`), behind the analyst's figures and CBRE Hotel Horizons
(`cbre_horizons.segment_all.<YYYY>.*_change_pct`). A bucket year missing inside the series is
unavailable, never patched from another bucket.

:::

::: badge {source=growth_carried_forward name="Past the Forecast"}
A calendar year past `series_last_year` of the document series the previous year used
(`source = carried_forward`): ADR = the arithmetic mean of that series' forecast-tagged ADR change
years (`forecast_average`, `forecast_span` on the label); occupancy = 0 (held). Never extended from a
withheld or unavailable year; a series with no forecast-tagged year leaves ADR unavailable.

:::

::: badge {source=occupancy_held_at_stabilization name="Held After Stabilization"}
Occupancy growth INTO model year y > S (the stabilization year) is 0 with
`source = held_at_stabilization` (`resolve_growth_by_year(stabilization_year=S)`), above every
document, carry-forward and all-years figure; only `occupancy_growth_by_year[y − 1]` beats it. Both
topline modes. Excluded from `growth_state`.

:::

::: badge {source=market_forecast_unavailable name="No Market Forecast"}
No growth figure for the year (`growth = None`): the engine holds the metric flat and the year is
`flat_no_growth`; `revpar_growth` for Returns is 0.0 with this badge when the exit-NOI year (the year after the
hold) has no figure. Reason {{reason:no_market_forecast}}; `topline_status.suggestions` offers the labelled
4.5% RevPAR suggestion, never applied by the loader.

:::

::: badge {source=costar_withheld_by_policy name="Withheld by Data Policy"}
A CoStar-derived candidate under `tenants.data_policies.costar_derived_defaults = withhold` (own
uploads; default allow) or `costar_licensed_defaults = withhold` (Fondok-licensed data; default
withhold): `source = withheld_by_policy`, reason {{reason:license_withheld}}. The policy is read once
per load (`base["__data_policy__"]`) and is part of the read-cache fingerprint.

:::

::: badge {source=penetration_default name="Penetration Default"}
`penetration_occ` / `penetration_adr` = 100 with source `default` when the analyst set no
`index_mpi_target` / `index_ari_target`; default ramp `[0.85, 0.95]` for S = 3, `[0.85]` for S = 2,
undefined for Years 3..S−1 when S ≥ 4 (`ramp_state = undefined_for_stabilization_year`).
`topline_status.current_mpi` / `current_ari` print the STR-published index beside it.

:::

::: badge {source=derived_from_revpar_growth name="Derived from RevPAR Growth"}
An analyst RevPAR-growth override derives ADR growth — (1 + RevPAR growth) ÷ (1 + occupancy growth)
− 1 — with the occupancy path held, so the lever moves operating NOI. Setting ADR growth explicitly
takes direct control.

:::

::: badge {source=str_segmentation name="STR Segmentation"}
A demand-segment figure the deal's STR Segmentation report states: a segment's share of room nights,
the transient channel split when the report's channel-mix block carries it, or a segment ADR.

:::

::: badge {source=platform_default_segmentation name="Platform default — no segmentation data on file"}
A segment-model figure the STR Segmentation report does not state, filled by a platform default:
every channel cost (BAR 2%, OTA 20%, corporate 8%, group 5%, contract 2%), the 60/30/10 BAR / OTA /
corporate split of transient demand when the report has no channel-mix block, and a segment ADR at
its ratio to the property ADR (corporate 90%, group 92%, contract 65%). A suggestion, never labelled
as STR data; the Revenue Mix sheet and the memo name these figures under the table. Runs saved
before 30 October 2026 carry the older “Segment default” badge on every segment figure.

:::

::: badge {source=platform_default_renovation_split name="Platform default split"}
The renovation budget's breakdown into hard costs, soft costs and professional fees at the platform
default 75% / 15% / 10% — no itemized renovation budget is on file.

:::

::: badge {source=index_assumption name="Index Analysis Assumption"}
Since topline methodology v2 (2 November 2026) Index Analysis shows and edits the topline the model
runs on — there is no method selector and no switch. Its Topline panel is the same as the Future
P&L's Topline card: the benchmark level in use (automatic — the competitive set, else the CoStar
submarket for the hotel's class, else the market — or one you force), the penetration at
stabilization for occupancy (MPI) and ADR (ARI), 100 by default with the hotel's current STR index
beside it, and the ramp for each year before stabilization. Below it a year-by-year table shows the
model's own chain: the benchmark (with its Actual / Forecast / Assumption chip and label) × the
penetration at stabilization × that year's ramp = the rate before displacement, then any renovation
displacement as its own step, then the occupancy and ADR the model ran on. A penetration can be set
for any single year there; that year runs on it with no ramp. Every change carries a justification and re-runs the model. Runs before 2 November 2026: a
rate set by the retired Index Analysis assumptions while the "Use STR rates" switch was on carried
this badge; the old market-growth and method settings are now read as your all-years growth and a
forced level, listed on the Future P&L as settings from retired controls. \
**Target units.** A penetration is index points 25–250 (100 = parity with the benchmark) or a ratio
0.25–2.5 (1.0 = parity) — the two bands do not overlap, so a value names its own unit: 1.10 is 110
points everywhere, never 1.1. The range is checked on every write and every read: a stored value
outside both bands is ignored by the model, never moves a number, and the Future P&L lists it among
the values it ignored.

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
ARI / RGI) in the last historical column. When the model's benchmark is the competitive set its
forecast columns are that benchmark by year — the level grown by each year's market growth (the CBRE
or CoStar forecast, your override, or carried forward), each column labelled with its basis; when
the model runs on the submarket or market level the comp-set forecast is blank with the reason and
the penetration rows index against that level instead. STR publishes no comp-set forecast, so a
grown year is never presented as an STR figure, and the subject's growth is never re-scaled into
it. The by-year fair share, variances and Revenue
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
by-year drivers are unchanged. Penetration rows follow the benchmark the model runs on.
With the STR comp set: the STR-published MPI / ARI / RGI in the TTM anchor column; across the
forecast columns the penetration the model applied that year — your per-year value (which runs with
no ramp), else the stabilized penetration (100 by default) × that year's ramp, so 85 / 95 / 100 under
the default ramp; RGI derived as MPI × ARI ÷ 100 — always what the Subject rows run on; the hover
text says which. With the submarket or market level, the index rows read
"vs \<benchmark\>": the
anchor column is the subject STR TTM ÷ that benchmark × 100 and each forecast column is the subject
÷ that year's benchmark (the same by-year benchmark the model indexes against), or the analyst's
target where one applies — never the STR-published indices, which are against the comp set; the
subject ÷ market indices, occupancy (pts), ADR ($ and %) and RevPAR variances to the comp set, Fair
Share of Supply = subject rooms ÷ (comp-set rooms + subject rooms), fair-share occupancy, actual
share of demand, fair-share rooms revenue, and the signed Revenue Opportunity at Par and at Target,
the latter computed per year against the analyst-set target that applies to that year and priced on
the selected benchmark (target RevPAR = benchmark occupancy × MPI ÷ 100 × benchmark ADR × ARI ÷
100). It uses exactly the penetration the index rows print. A document default — the published index itself —
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
price is kept as a note and sends nothing. There is no seed price (`IDENTITY_INPUT_KEYS` have no
default): with no price the price-dependent engines are refused with `missing_identity_inputs`, and
the message names where to enter it. The analyst enters it at any time on Overview → Entry Valuation
or CAPEX → Deal Summary (one shared editor, `PurchasePriceField`); the CAPEX tab renders the
Purchase Price / Keys inputs instead of an empty state when capital failed for a missing identity
input, pre-filled from the OM asking-price row (`asking_price.headline_price_usd`,
`asking_price.purchase_price` or `headline_price_usd`) when one was extracted, or stating that the
OM does not give one — or that it is still being read while an OM extracts, or that the Data Room
could not be loaded (with a retry); the sources re-read after each run and when an OM finishes
extracting, so the pre-fill needs no reload. Run buttons there start the real run and stay disabled
("Running…") until it reaches a terminal state, not just its kickoff; while a price save's run is in
flight the Deal Summary's Entry Cap Rate and Price / Key read "re-running…", and "pending run" if
the saved price is not the one the last run used. The entry is written as
`field_overrides.purchase_price` `{value, note}` and triggers a run. Precedence: analyst override >
deal row > OM asking price; the loader never derives a price from a broker cap rate or a price per
key. The Property Name is not a deal-row field — see
below.

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
row layout (`worksheet_layout`, presentation only), the **Property Name** (a name, not a number), the
Completion Guarantee covenant status (qualitative — no covenant math), the number of promote tiers in
the waterfall (its shape, not a value in it), the market-segment choice (`index_market_segment`,
which moves nothing since topline v2), and the IC memo's prose. Since topline v2 the **Stabilization
Year** (it ends the default penetration ramp), the **topline benchmark level**
(`topline_benchmark_level`, the level Year 1 is penetrated against), the retired Index Analysis
method key (read as a forced level) and the deal's **reopening date** (sent as
`reopening_date_note` beside it; not on a draft) move numbers and need a note. Clearing an override
is a revert to source, not an override, and needs no note either.

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

{testid=market-submarket-methodology} **City, market and submarket.** These are three separate deal
fields. **City** is the property's city (optional; filled from the Offering Memorandum only while
empty, as above). **Market** and **Submarket** are chosen on the wizard's first step, shown — and
correctable at any time — on the Overview's Investment Profile card; no engine reads them today, so
changing them never re-runs the model. **The taxonomy list:** the CoStar geography export (markets
and submarkets, no vendor ids), loaded by `scripts/load_str_markets.py --format costar-geography
--version <v>` (dry run by default; `--commit` is refused on any validation problem;
`--report-orphans` lists deals whose stored key left the list). Fondok mints deterministic taxonomy
keys from the names — `costar:<slug of market>` for a market, `<market key>/<slug of submarket>` for
a submarket — because submarket names repeat across markets (the key is (Market, Submarket)); the
loader refuses a file in which two distinct names collide on one key. The deal stores the keys in
`str_market_id` and `submarket_id` (the first column name predates the CoStar list and was kept
rather than renamed — a rename is a wider migration; it holds a Fondok key, not a vendor id;
decision of 30 October 2026, FON-96) and the keys are hidden everywhere — the UI says “CoStar
market” / “CoStar submarket of \<market\>”. Each market is `metro` (named “City - ST USA”) or
`regional` (named in words), recorded in `str_markets.market_type`; `reference_datasets` records the
loaded version, SHA-256, row counts and the licence flag, and `/admin/health` reports them in its
`reference` block. **Selection:** a typed market or submarket takes a key only when it matches
exactly one entry (case- and punctuation-insensitive, “Chicago, IL” ≡ “Chicago - IL USA”); the
worker refuses a key that is not in the loaded list, and a submarket key that is not under the
market key (`submarket_not_in_market`); a market change without a submarket key clears the submarket
key. Typed values are stored with null keys as analyst-entered (`market_source = analyst`).
**Licensing:** the list is served to an internal workspace always, and to an external workspace only
when both the loaded dataset's licence flag (`--licensed-external` at load time) and the
`MARKET_TAXONOMY_SERVE_EXTERNAL` setting are on; otherwise that workspace sees the public 62-name
STR market list and no submarket suggestions, and the worker refuses a new key from it
(`market_list_unavailable`) without saying whether the key exists. **Fill from the OM:** after the
metadata sync, a deal with no market may receive one only when an OM location phrase, normalised,
equals exactly one metro market name — never a regional market, never two candidates — and a
submarket only within that market on the same exact rule; recorded as `market_source =
om_exact_match` (mirrors `positioning_source`) with the document, page and phrase, and audited as
`deal.market_matched_from_extraction`; a workspace the list is withheld from is never filled. An
analyst's value is never overwritten. Fondok never invents a market, a submarket or a key, and the
key is never shown as a vendor identifier.

:::

::: card
#### Which statement is the Year-1 basis

Year-1 occupancy, ADR, revenue lines and expense lines all come from one statement: the document the
Data Room badges as **Primary source**. The engines and the badge now share one ranking — full-year
statements before partial ones, the most recent period first, and a T-12 ahead of a calendar-year
P&L of the same year. A T-12 whose extraction carries no period label is still treated as a
trailing-twelve, and when it carries no date at all it is taken as the current period, because an
analyst uploads a T-12 as the latest statement. Older annual P&Ls never blend into Year-1, and
neither does a second statement of the same period (two uploads of one T-12, or a summary and a
detailed P&L of one year): `_choose_period_statements` groups by exact period
(`_statement_period`: full year by stated end, else fiscal year — calendar only when the deal's
dated annuals all end Dec 31 or the monthly block is Jan–Dec — else current T-12; partial only by
stated end + period type) and picks one statement per period — the footing one
(`_revenue_footing`: `fails` / `foots` / `unverifiable`, `statement_lines.half_unit` tolerance)
when the top-ranked fails, else the 3+-statement median outlier switch, else the top-ranked.
`_year_one_lines` reads each line from the chosen statement, then same-period siblings, then the
most recent twelve-month statement (`_covers_twelve_months`) within `_FILL_MAX_AGE_YEARS` (2) of
the basis period, reporting `_LineFill` / `_LineSkip` / `_FootingNote`. These and conflicts become `needs_review` entries in `__reasons__`
(`_loader_notices`; an earlier reason is kept under `also`), and the detail reaches the lineage
drawer's Unresolved list on the assumption step. `assumption_sources.source_documents` follows
`source_fields` wherever the runner named a row. The earlier years still feed the Historical P&L
grid and the variance checks.

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
- **Other Revenue sums both concepts.** The Historical P&L's Other Revenue row
  (`other_revenue.bindings.worksheet`, hist key `misc`) is `other_revenue` (Other Operated
  Departments) + `misc_revenue` (Miscellaneous Income), each resolved on its own from the statement
  and a field counted once (`buildHistYear`, `OTHER_REVENUE_PART_ALIASES`). It used to read the union
  of both alias lists with one first-match lookup, which showed one line and dropped the other (the
  Kimpton FY2023 column was short by exactly its $1,004,790 of Misc. Income). A summed cell's SOURCE
  panel lists both lines; a correction edits one line, not the sum.
- **NOI footing.** The historical fixed block reads `ffe_reserve`, `rent_expense` and
  `other_nonop_expense` (worksheet rows `ffe`, `rent`, `other_nonop`) beside management fee, property
  taxes and insurance; `HistYear.fixed_expenses` (Total Fees & Fixed) is their sum over the lines the
  statement states. `nonop_income` is its own row, added back. The **NOI footing** row compares stated
  GOP − Total Fees & Fixed + non-operating income with the stated NOI and prints "✓ foots" or "doesn't
  foot by $X — source gap"; a dash, with the reason, when the statement states no GOP or NOI.
- **Non-operating income sign.** Statements print the line two ways: income-positive (total
  non-operating = rent + taxes + insurance + other − income; the Kimpton T-12, +21,923.6) or inside an
  expense-signed section where income is a negative deduction (total = the printed lines summed; the
  Kimpton FY2023 P&L prints −9,638.44). The extractor transcribes the print, so the reader settles the
  sign against the stated `fixed_charges` total(s) (`statementLines.normalizeNonopIncome` /
  `services.statement_lines.normalize_nonop_income`); when no stated total agrees under either
  convention the sign is `unresolved`: the row is "—" and the NOI footing says the sign is unsettled
  instead of reporting a source gap.
- **Footing tolerance = read precision.** Every footing (revenue, GOP, NOI) tolerates the sum of half
  a unit of the last significant figure each summed line and the stated figure were read at
  (`halfUnitOf`: the raw token, e.g. "1.29402e+07" → ±50; without one, the value's own digits). The
  Kimpton statements miss by $1–$17 on 6-significant-figure reads with no line missing; a line
  genuinely absent is far outside that.
- **Exports read the statement the same way.** The worker's historical baseline
  (`engines/historical_baseline.py` → `GET /deals/{id}/historical-baseline`, the export workbook's
  Historical Baseline sheet and the memo walk) carries Other Revenue = Other Operated + Misc. Income,
  Fees & Fixed with FF&E reserve, rent and other non-operating expense, and the signed non-operating
  income — Kimpton FY2023 Other Revenue is 1,020,675.6 on screen and in the exports.
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
- **Cross-checked across statements, never blended.** When more than one statement covers the same
  period, Year-1 takes its lines from one statement: the footing one when the top-ranked does not
  foot to its stated total (deal 7a9928e0: a $96,528 F&B misread), else the median outlier switch
  with three or more statements, else the top-ranked. Others fill only lines it lacks, same period
  first, each with a `needs_review` note; a disagreement above 5% on rooms, F&B, total revenue or
  NOI is a note naming both documents and values. Lines
  resolve exact path first (`_resolve_statement_lines`); a tail match never crosses into a cost
  section for revenue or a revenue section for an expense, so
  `cost_of_services.rooms_revenue_usd` is not rooms revenue.
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
not quietly age its comp set out of the window. Without an underwriting date the lookback is
measured from the offering memorandum's own report date, and from the run date only when that is
unknown. A deal that has not set an underwriting as-of date is otherwise unaffected — the acquisition close date is a modelling input and is never read
as a knowledge horizon.

:::

::: card
#### The full ledger

The Sources ledger (linked from every traced figure's tooltip) lists every modeled assumption, its
value, and its origin in one table — filter to the ungrounded set to see exactly what's still riding
on a default, and export the whole ledger to CSV so every figure in the IC memo is defensible.

:::

:::

