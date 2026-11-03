---
id: projection
title: "How the Future P&L is built"
decisions: [FON-44, FON-59, FON-67, FON-98, FON-101, FON-102, FON-109, FON-116, FON-117, FON-118]
anchors:
  - benchmark-column-methodology
  - benchmark-suggestions-methodology
  - correction-note-methodology
  - exit-noi-basis-methodology
  - methodology-capex-lanes
  - methodology-displacement-explicit-only
  - methodology-displacement-schedule
  - methodology-displacement-status
  - methodology-exit-basis
  - methodology-future-capex-reference-only
  - methodology-future-pl
  - methodology-incentive-fee
  - methodology-line-methods
  - methodology-mgmt-fee-guidance
  - methodology-renovation-budget-precedence
  - methodology-returns-by-exit-year
  - methodology-stabilization-anchor
  - no-revenue-basis-methodology
  - primary-statement-methodology
  - topline-card-methodology
  - topline-ui-methodology
---
<!-- section 2: How the Future P&L is built — from page.tsx lines 117-415 -->
::: conceptual {title="How the Future P&L is built"}
::: intro
Year 1 is anchored on the deal's own statements whenever they exist; later years grow from an
undisplaced baseline, so a renovation year does not permanently depress the curve. The order of
precedence for each input is below.
:::

::: card
#### How occupancy and ADR are projected

**Today: growth by year on the hotel's own Year 1.** The new topline is being switched on in two
steps. In the first step, which is live, Year 1 is the hotel's own rates and every later year grows
them at the market growth for that year:

::: chain
1. **Year 1** — Your Year-1 occupancy or ADR (entered with a note) is used as typed; else the
   hotel's own T-12; else, when the deal still has the retired "Use STR rates" switch on, the
   hotel's own STR trailing twelve months (the switch is listed with the retired settings, and
   "Clear legacy keys" removes it); else, for ADR only, the first forecast year of a CBRE
   Hotel Horizons report. With none of them the projection refuses and asks for the rates — there
   is no default Year-1 rate.
2. **Market growth, year by year** — the same chain as below (your growth, CBRE, the CoStar
   submarket forecast for the hotel's class), applied from Year 2.
3. **After stabilization** — Occupancy holds at its stabilized-year level; ADR keeps growing. Only an
   occupancy growth you enter for a specific year still moves it.
4. **After the forecast ends** — ADR grows at the average of the forecast's years (the label names
   them, for example "2025–2029 forecast average (+2.4%)"); occupancy holds.

:::

The benchmark, penetration and ramp below are shown on the Market and Index Analysis tabs for
reference but do not set any year until the second step is switched on; the Future P&L says which
step the workspace is on. This two-step switch is temporary.

**When the benchmark method is switched on**, every projected year's occupancy and ADR is the
**benchmark** for that year × the hotel's **penetration** of it. The chain, in order:

::: chain
1. **Your override** — A Year-1 occupancy or ADR you enter yourself (with a note) is used as typed.
   Penetration and the ramp are not applied to it; later years grow it at that year's market growth.
2. **Benchmark** — The competitive set (the STR comp set, else a CoStar comp set); else the hotel's
   class in the CoStar submarket report (its latest actual year); else the market. The Future P&L
   names the level in use, its report and period, and why a higher level was not available. The
   benchmark describes its own period — an STR trailing twelve months to May 2025 describes 2025 —
   and is grown into every projected year, Year 1 included, by the market growth below.
3. **Market growth, year by year** — Your growth for that year; else your all-years growth; else the
   CBRE Hotel Horizons forecast for that calendar year; else the CoStar submarket forecast for the
   hotel's class. Past the last forecast year ADR grows at the average of that forecast's years and
   occupancy holds, both labelled assumptions. After the stabilization year occupancy holds at its
   stabilized level while ADR keeps growing (only your own occupancy growth for a specific year still
   moves it). With no forecast at all the rate is held flat and the Future P&L says
   "No market forecast — set growth manually". A 4.5% RevPAR growth is offered as a labelled
   platform suggestion you can apply; it is never applied for you.
4. **Penetration at stabilization** — 100 (parity with the benchmark) unless you set it; the hotel's
   current STR index (for example an MPI of 110) is printed beside the default so you can raise it.
   A penetration you set for a specific year beats the formula for that year.
5. **Ramp** — Before the stabilization year the hotel runs at a share of its stabilized penetration:
   85% in Year 1 and 95% in Year 2 for a Year-3 stabilization (85% in Year 1 for Year 2). For a later
   stabilization year the years in between run at 100% until you enter them — no ramp is invented.
6. **The T-12** — Shown beside the projection as a reference row, not the basis. (One ramp applies to
   occupancy and ADR together.)

:::

Occupancy stays at or below 95%; a year that would exceed it is held at 95% and says so. With no
benchmark at all the hotel is projected from its own starting occupancy and ADR (the T-12 actual
where one is extracted) and that is stated. A CoStar figure is withheld when the workspace's data
policy says so, and the Future P&L names the policy.

**F&B revenue grows with prices, not only with occupancy.** F&B revenue is F&B dollars per occupied
room × occupied rooms. The Year-1 dollars per occupied room come from the T-12 (F&B revenue ÷
occupied rooms) or the default, and inflate each year at the market ADR growth the benchmark grows
by — not at the penetration ramp, which is the hotel's position in its market rather than a price
change. A year with no ADR growth figure holds the F&B price flat. A renovation lowers occupied
rooms, never the F&B price.

:::

::: card
#### The projection calendar

The model projects operating years — Year 1 through the end of the hold — and places them on the
calendar from one date: the **acquisition close date**. Operating Year 1 is the calendar year of the
first operating month after close, so a September close projects Year 1 in the same year and a
December close projects it in the next. With no close date the statement prints no calendar year at
all; a guessed year would be an invented number.

The first column is labelled **Base year (Year 1)** because that is what it is: the model's first
operating year, the year the returns and the Cash Flow statement call Year 1. The horizon is the
hold period plus one **Exit Year** column, which carries only the forward twelve-month NOI the sale
is valued on.

Every projection year-end is also evaluated as a sale date — the **Returns by exit year** table on
the Returns summary — on this same projection: the operating years are not re-projected for a
shorter hold. Years beyond the hold are not projected, so they are not shown; a longer hold is a
different underwriting, not a row.

:::

::: card {testid=methodology-line-methods}
#### Expense lines and their methods

Eight expense lines — Rooms, F&B and Other Operated departmental expense, and Administrative &
General, Information & Telecom, Sales & Marketing, Property Operations and Utilities — can each be
driven one of four ways from the method chip on their Future P&L row: **Growth** (Year 1 grown each
year), **% of revenue** (the department's own revenue for departmental lines, total revenue for
undistributed lines), **per occupied room** and **per available room**. A method picked without a
value keeps the line's own Year-1 ratio, so no benchmark figure is introduced. A line nobody has
touched runs exactly as before. Every method change needs a justification note, and the Excel export
lists each line's method and value on its Assumptions sheet.

**Departmental expense rides its own department's revenue.** By default Rooms, F&B and Other
Operated expense are each a cost ratio to that department's revenue, fixed in Year 1 and applied
every year: the T-12 expense ÷ the T-12 department revenue, else your override, applied benchmark or
the hotel-type ratio. A renovation-displaced year's departmental expense is displaced with its
revenue. Undistributed lines and fixed charges grow at the expense growth.

**Operating model.** The new-deal wizard records whether the hotel is owner-operated, third-party
managed or brand-managed. It is shown on the Overview and beside the management fee. It moves no
number: the management fee is yours to set, and the check against the usual two to six percent of
revenue applies to every operating model.

{testid=methodology-incentive-fee} **Base and incentive management fee.** The management fee is the base fee — a share of each year's
total revenue, Year 1 included; the management fee on the T-12 is history and does not set Year 1.
A Year-1 fee you enter on the worksheet, with its note, does. The incentive fee beside it is optional and blank by default. When set, each year's
incentive fee is a share of gross operating profit after the base fee, never negative, deducted
before NOI, so it lowers NOI and everything downstream. It appears as its own row only when set, and
travels through the Excel export.

::: box {testid=methodology-mgmt-fee-guidance}
##### Management fee — directional guidance by operating model

*{{slot:mgmt-fee-disclaimer}}* The same text is shown beside the management fee on the Future P&L.

{{slot:mgmt-fee-guidance}}

:::

:::

::: card {testid=methodology-future-pl}
#### Reading the Future P&L

The P&L tab has two views: **Historical P&L** — the normalised actuals, one column per fiscal year —
and **Future P&L**, the forward plan the returns are built on. Every projected column is headed by
its operating year and calendar year; the first column also names the statement period it is
anchored on, exactly as the Historical P&L heads that column.

{testid=primary-statement-methodology} **One statement per year.** When two statements cover the same fiscal year (a detailed and a summary
P&L, say), the Historical P&L shows one column, built from the primary statement, and names the
other as supplemental on that column. The primary is the one that drives the model: the statement
the Data Room marks “Primary source” — a full-year statement ahead of a partial one, then the more
detailed one, then the newer upload, unless the footing check below passes it over. Re-tag a
statement's year in the Data Room to change which is primary. The other statement is never averaged into Year 1. When the primary lacks a full-year rooms
revenue line or its revenue lines do not add up to its own stated total, and the other statement's
do, the other statement drives the year; a statement that does not add up carries a review note
even when it is the only one;
when the two disagree by more than 5% on rooms, F&B, total revenue or NOI, or a line comes from the
other statement, the Year-1 figures carry a review note naming both documents and values.

{testid=correction-note-methodology} **Correcting an extracted value.** A statement figure can be corrected where it is read — from a
flagged cell on the Historical P&L, from the Data Room review list, or by re-importing the exported
workbook. Every correction asks why, and the note is kept with the corrected value and in the audit
trail, the same as a model override: a corrected statement value is model input.

**Click any number.** Every projected occupancy, ADR, RevPAR and statement amount opens a trace of
the inputs behind it — starting value, growth, index or penetration where present — down to the
source rows. When no trace was recorded for a cell the drawer says so; Fondok never prints a formula
it did not record.

{testid=benchmark-column-methodology} **Benchmark column.** When the deal carries a P&L benchmark report (CBRE or HotStats), the Base-year
column gains a Benchmark sub-column showing the comparable set's ratio for thirteen lines on the
same basis as the model's own ratio, and the difference in points. Colour marks direction only,
never a verdict. A line the report did not publish shows a dash with the reason. The fuller
comparison sits under the collapsed Benchmark detail below the table, and the Excel export carries
the ratios as a read-only column.

{testid=benchmark-suggestions-methodology} **Benchmarks are suggestions.** A benchmark never flows into the model on its own. For the Rooms and
F&B departmental ratios, the management fee and the FF&E reserve, Fondok turns the report into a
suggestion that names the value, the benchmark line, the peer-set size, the document and page, and
the value the model is on now. *Apply benchmark* opens a prefilled justification note you can edit;
saving writes an ordinary analyst override and re-runs the model. *Reset to source* removes it
again. Until you act, each assumption keeps its existing source: your portfolio ratio when one is on
the deal, else the platform default, labelled as such.

{testid=topline-ui-methodology} **The topline on screen.** Under the Future P&L header one line names what the
occupancy and ADR run on: the benchmark (level, report and period, and why a higher level was not
used), the market growth source with the hotel's class, the stabilized year and the ramp actually
applied — "Ramp 85/95/100 (default)", your own ramp, or "the default ramp does not apply to this
deal" with the ramp policy. Each projected Occupancy and ADR column carries a chip saying whether
that year's benchmark is an Actual, a Forecast or an Assumption; hover shows the model's chain:
benchmark × penetration × ramp = the rate before displacement, then any renovation displacement as
its own step to the figure in the column (a penetration you set for one year runs with no ramp). Two reference rows sit under RevPAR: the benchmark for
each year, and the hotel's own T-12 repeated with its period — neither moves a number. When growth
is missing the Future P&L says "No market forecast — set growth manually" (or that a CoStar forecast
is withheld by the workspace's data policy, or that the forecast covers only some years) and offers
the labelled 4.5% RevPAR suggestion with a prefilled note you can edit; nothing is applied until you
save. When a market forecast is on file but the deal has no acquisition close date, the same banner
asks for the close date first, with the date entry beside it (the same field as the CAPEX tab's
Acquisition Date), ranked above the suggestion. A CoStar report read by an older extraction says
"CoStar report extracted before class forecasts — re-extract" and links to the Data Room's
re-extract action. Settings written by retired controls are listed with how they are read now, and
"Clear legacy keys" removes them in one audited save. A run made before this method existed shows a
one-time notice — "The topline method changed; this run predates it — re-run, then compare runs" —
and the run comparison labels the method change.

{testid=topline-card-methodology} **The Topline card.** The Assumptions panel's Topline card holds the benchmark level (automatic,
or forced to the competitive set, submarket or market), the stabilization year, the penetration at
stabilization for occupancy and ADR (100 by default, with the hotel's current STR index printed
beside it) and the ramp for each year before stabilization. The Growth card shows the market growth
into each year with its source, editable year by year; RevPAR growth stays as an all-years
shorthand and shows only your own value — blank until you set one. The growth the exit year is
valued on when you set none (the source's long-run average, else the last year's ADR growth, else
0%) is shown on its own labelled line under the growth table, never in that field. Every change asks for a
justification and re-runs the model. The Excel export's Assumptions sheet carries the same inputs —
growth for each year, the ramp for each year before stabilization, the two penetrations and the
benchmark level — and re-imports them under the same justification rule.

{testid=no-revenue-basis-methodology} **No Year-1 rates, no projection.** When a deal has no T-12, no STR or market benchmark and no
Year-1 occupancy or ADR of yours, the revenue model does not run and the Future P&L and Overview say
so in the model's own words: "Upload a T-12, or enter Year-1 occupancy and ADR" (while the benchmark
topline is switched on it also offers "or STR report", since an STR comp set then sets Year 1). The
entry is right there; your rates are saved with a note and the model re-runs. No placeholder rate is
ever substituted.

**The waterfall below Total Revenue** runs gross operating profit, then management fees (and the
incentive fee when one is modelled), then fixed charges — property taxes, insurance, rent, other —
to **NOI (before FF&E reserve)**, then the FF&E reserve, to **Cash NOI (after FF&E reserve)**. Each
line is labelled with its basis, and the two NOI figures differ by exactly the reserve. Where the
product says simply “NOI” it means the before-reserve figure, the numerator of the entry cap rate.
The after-reserve figure is what debt coverage and the exit valuation use.

:::

::: card
#### The Stabilization Year

“Stabilized” names one projection year, and every stabilized figure on every screen — occupancy,
ADR, revenue, NOI, NOI margin, yield on cost, the IC memo's scenario summary and Scenario Analysis'
comparison row — is read from that year, published once so two screens cannot disagree.

The stabilization year defaults to **Year 3 of operations** (badged “Default — confirm”); on a
shorter hold it is the last projected year, and the card says so. You can override it on the Future
P&L's Assumptions panel. It selects which year the stabilized figures are read from, and it is
also the year occupancy stops growing: from the year after it, occupancy holds at the stabilized
year's level while ADR keeps growing. With the benchmark method switched on it is also the year the
hotel reaches its stabilized penetration of the benchmark (the ramp runs in the years before it).
Changing it therefore moves the projected occupancy of the later years (and, with the benchmark
method, the ramp years) and everything built on them, and an override carries a justification note.

{testid=methodology-stabilization-anchor} **What Year 3 counts from.** With no reopening date the default counts from the acquisition close,
and the Overview reads “Year 3 after close”. For a material repositioning or re-flag, set the
**reopening date** on the Overview's Investment Profile card: the default then counts Year 3 from
the first full operating year after reopening, and every stabilized figure says so (“Year 3 after
reopening”). A reopening Fondok cannot place — no close date, or a Year 3 that falls before the
projection starts — keeps the close anchor and states the exception in plain sight wherever the
anchor is shown. Stabilized NOI stays a separate figure from the exit NOI the sale is valued on; the
two are never printed under one label.

:::

::: card {testid=methodology-exit-basis}
#### What the exit is valued on

The sale capitalises the **forward twelve-month Cash NOI** — the after-reserve basis — at the exit
cap rate. That forward figure is the last hold year's Cash NOI grown at the RevPAR growth rate; the
Exit Year column shows this and nothing else. On CAPEX → Exit you can switch the basis to
**Stabilized NOI** (the stabilized year's Cash NOI grown to the exit) or to **Sale-year NOI** — the
exit year's own Cash NOI, not grown — and an analyst-entered terminal NOI beats any basis. Wherever
the exit NOI is printed its label names the basis and period it was taken on.

In the Returns by exit year table the forward twelve-month basis follows one rule for the interim
years: the exit NOI is the **next projected year's Cash NOI** where the projection has it, and the
final year is grown at the RevPAR growth rate because no later year is projected. A pinned
(analyst-entered) terminal NOI applies to the selected exit only; every row names its own basis and
period beside its exit NOI.

The entry cap rate is struck on NOI *before* the reserve while the exit is struck on Cash NOI
*after* it. Both are defensible conventions; Fondok keeps both and names each one wherever it prints
it, never a bare “NOI”.

:::

::: card {testid=methodology-returns-by-exit-year}
#### Returns by exit year

The Returns summary carries one row for every projection year-end, each answering “what if the deal
were sold then?” on the same projection. A row sells at that year-end on the basis above, applies
the same selling costs and transfer tax, pays off the loan balance at that date, and includes the
refinance only if it precedes the sale. Each row shows the exit NOI with its basis, the gross sale,
the payoff, the net proceeds to equity, the unlevered and levered IRR and the equity multiple. The
row with the highest levered IRR is marked **Peak levered IRR**; the deal's own exit — the hold, or
the disposition month when one is set — is marked **Selected exit**, and the headline figures above
the table are that row's figures.

A row that cannot be a sale shows a dash with its reason, never a zero: *capital still deploying*
(an equity draw or deferred capital lands after that year-end, so a sale before completion is not
modelled); the *construction window* you stated on a development deal has not closed; the *exit NOI
is not positive*, so there is no sale value; or the cash flows have one sign and *no IRR* exists. A
development deal that states no construction window and schedules no draws has no construction in
the model, and its rows are computed. LP and GP returns are reported at the selected exit only.

:::

::: card
#### Renovation and Year-1 displacement

{testid=methodology-displacement-explicit-only} **Only explicit assumptions displace Year 1.** A renovation budget is a capital cost: it flows into
Sources & Uses and the equity requirement, and it never reduces Year-1 occupancy or ADR on its own.
Year 1 is displaced only when you set an assumption on CAPEX → Initial Renovation: a Year-1
rooms-offline and rate displacement (each up to half, with a justification note), a renovation
displacement schedule, or a structured closure schedule. All default to off, and no document sets
them. Years the renovation does not fall in grow from the undisplaced baseline.

{testid=methodology-displacement-schedule} **The renovation displacement schedule** is a list of periods, each with a first and last month
counted from close, the share of rooms offline and the rate discount on the rooms that keep selling.
Fondok turns it into one occupancy factor and one ADR factor per model year — every month weighs one
twelfth, rooms offline across overlapping periods add up, and the rate discount applies only to the
rooms that actually sold. Six months at a quarter of rooms offline and ten percent off therefore
give Year 1 occupancy times 0.875 and ADR times 0.957. When a schedule is set it is the only
renovation impact applied, so nothing is counted twice.

{testid=methodology-displacement-status} **The model says what it decided.** The CAPEX tab's renovation row and the Future P&L's Year-1 basis
line print the displacement the model actually applied — “No Year-1 renovation impact”, or the
percentages it used, or the schedule it ran — so a reader never has to infer it.

:::

::: card {testid=methodology-renovation-budget-precedence}
#### Renovation budget — which document the model reads

::: chain
1. **Your override** — A budget you set — including one you selected with "Use this" on CAPEX →
   Renovation budget sources — wins, with its justification note.
2. **PIP or capital plan — latest plan date** — A separate PIP, owner budget, approved capital plan or
   estimate filed in the PIP / Renovation Budget lane that states a total. It is the more detailed
   source, so it outranks the OM; among several, the newest plan date wins.
3. **OM broker pro forma** — The renovation budget the offering memorandum states, when no PIP or
   capital plan does.
4. **Platform default** — Used only when no document states a budget, with the reason shown. No budget,
   whatever its source, displaces Year 1 on its own.

:::

Every candidate stays visible. CAPEX → Initial Renovation → **Renovation budget sources** lists the
OM figure and each PIP or capital-plan figure with its page, plan date, status and preparer, marks
the one the model is on, and says why each other one is not. A PIP whose stated total is more than
two percent away from the sum of its own scope items is flagged for review; the value is never
altered.

{testid=methodology-capex-lanes} **Three CapEx lanes, one of which feeds the model.** Every capital document is filed in a lane:
**PIP / Renovation Budget** is the day-one plan and the only lane the renovation budget reads;
**Historic CapEx** is spend already incurred; **Future CapEx** is a forward capital plan. Historic
and Future CapEx are listed as “Reference only — not in the model” and never enter the renovation
budget. A capital file dropped without a lane is filed under PIP / Renovation Budget so that is
visible; move it to take it out.

:::

::: card
#### Exit cap rate

::: chain
1. **Your override** — An inline-edited value wins.
2. **OM comparable sales** — When the OM carries three or more comparable sales with cap rates in a
   sensible band, their median is the anchor.
3. **Platform default of 7.0%** — Used when no comparable sales are extracted, badged as a default.

:::

:::

::: card
#### Where expense lines come from

Departmental, undistributed and fixed-charge lines come from the T-12 first. A zero on the statement
is treated as “not stated” rather than a fact, and a line the statement does not carry runs on a
ratio: your override first, else your portfolio's ratio when one is on the deal, else the platform
default for the hotel type. An uploaded benchmark report changes nothing by itself; its ratio
becomes an assumption only when you apply it.

When a precedence chain ends at a platform default, the default carries the reason — no document of
the needed type on the deal, a document that does not state the figure, or market rates that could
not be read — in the vocabulary of Section 7. Each grounded value carries the opposite record: the
document, page and line behind it.

:::

:::


::: detailed
::: intro
The formulas, defaults and precedence the projection actually runs. Every default below is a
labelled suggestion: the model badges it as a platform default with the reason it was reached,
and an analyst override replaces it.
:::

::: card
#### Occupancy and ADR — topline methodology v2

<!-- source: apps/worker/app/config.py (TOPLINE_BENCHMARK_ENABLED), services/topline.py (topline_mode, build_growth_only, _growth_only_status), services/engine_runner.py (_apply_topline, _apply_growth_only_str_seed, CBRE Year-1 ADR), DEPLOY.md (Topline rollout switch) -->
Staged rollout (temporary). The worker runs one of two topline modes, reported on the health check
and on the topline status. Growth-only (the default, live): no benchmark, penetration or ramp; Year 1
= your value → the T-12 actual → the STR subject trailing twelve months when the retired "Use STR
rates" switch is on (no synthetic STR forecast point) → CBRE Hotel Horizons' first forecast year's
ADR; none → {{reason:no_revenue_basis}}. Year 1 is not grown; growth applies into Years 2..hold from
the resolver below. Benchmark (switched on per environment): the full method below. The switch is
removed once the benchmark method is live everywhere.

<!-- source: apps/worker/app/services/topline.py (DEFAULT_RAMP_POLICY, default_ramp_applies), topline_benchmark.py, topline_growth.py; apps/worker/app/engines/revenue.py (_plan_topline, _ramp_for_year) -->
For each projected year y with stabilization year S, benchmark B, stabilized penetration P (index
points, default 100), ramp r and a per-year penetration V you may set:
penetration = V when set; P × r(y) when y \< S and the ramp defines the year; P otherwise.
Occupancy = min(95%, B occupancy × penetration ÷ 100); ADR = B ADR × penetration ÷ 100. Renovation
displacement multiplies on top, unchanged. Default ramp: 85% / 95% for S = 3, 85% for S = 2;
undefined (100%, stated) for Years 3 to S − 1 when S ≥ 4. The default ramp applies to every deal (ramp policy
"all deals"); your own ramp always wins. Under another policy a deal outside it runs the stabilized
penetration from Year 1 and each of its ramp years says so.

<!-- source: apps/worker/app/services/topline_benchmark.py (resolve_benchmark), services/topline.py (build_topline) -->
Benchmark level, first available unless you force one: competitive set (STR TTM blend = subject
TTM ÷ MPI / ARI from one report; else a CoStar comp set) → CoStar submarket, the hotel's class, the
latest actual year (overall series, labelled, when the class is not in the report or no positioning
tier is set) → market (market-wide report figures, else CBRE Hotel Horizons All Hotels). The level is
grown from its own calendar year into each projected year: B(y) = level × Π(1 + growth) over the
calendar years after the level's period up to year y's. A step with no growth figure holds B flat.

<!-- source: apps/worker/app/services/topline_growth.py (resolve_growth_by_year), services/topline.py (read_topline_inputs), services/market_study_reader.py (CLASS_SERIES_PIPELINE_VERSION, predates_class_series) -->
Growth per year and metric, first hit: your per-year growth → your all-years growth (your RevPAR
growth alone sets ADR growth = (1 + RevPAR growth) ÷ (1 + occupancy growth) − 1 for each year) →
CBRE Hotel Horizons All Hotels change for that calendar year → CoStar submarket change for the
hotel's class → past the last year of the series the previous year used: ADR at the arithmetic
average of that series' forecast-tagged ADR years, occupancy held (assumptions, the label names the
years averaged) → none (held flat, {{reason:no_market_forecast}}). Occupancy into every year after
the stabilization year S is 0 (held at the Year-S level, "held after stabilization") whatever the
document, carry-forward or all-years figure says; only your per-year occupancy growth for that
year applies. ADR is unaffected. Held years do not count as a market figure in the growth state. A CoStar figure under a withhold
data policy is {{reason:license_withheld}}. A CoStar report extracted before the class series were
read (pipeline before v9, no class table) is never "not in the report": the class's years are
{{reason:reextract_needed}} ("report on file but extracted before class forecasts were read —
re-extract") and the overall series is not substituted.

<!-- source: apps/worker/app/engines/revenue.py (projection_start_year), services/topline.py (calendar_anchor, CalendarAnchor), services/engine_runner.py (_apply_topline) -->
Model years sit on the calendar from the acquisition close: Year 1 is the calendar year of the first
operating month (close + 1 month), so a 30 September 2025 close puts Year 1 on 2025 and a
31 December 2025 close puts it on 2026. A benchmark whose own period is Year 1's calendar year is
used as reported; growth applies from the next year. With no close date a document forecast is
still used when the deal has an underwriting as-of date: it is placed on a calendar assumed from
that date, and the status names the anchor and asks for the close date. With neither date the
forecast is not placed on a guessed calendar — never the comp report's date, never the run date,
so a deal does not move with the clock: every year shows {{reason:calendar_unknown}} ("Set the
acquisition close date to place the market forecast"), ranked above the labelled 4.5% suggestion.
Only when there is no forecast at all does "no market forecast" show. Retired settings are read with {{reason:legacy_key_translated}}: the Index
Analysis market growth reads as your all-years growth, the "Use STR rates" switch is ignored, and a
Year-1 rate that switch wrote is treated as a seed (the benchmark sets the year).

<!-- source: apps/worker/app/services/engine_runner.py (_apply_topline: revpar_growth, _long_run_growth), services/topline.py (_terminal_growth), services/topline_growth.py (carried_forward) -->
Returns grows the exit-year NOI at your RevPAR growth when you set one. Otherwise it uses the
source's long-run average (CBRE Hotel Horizons long-run RevPAR change, else its long-run ADR change)
when one is on file, else the ADR growth of the year the exit NOI covers — the year after the hold
(a 5-year hold closing 2025-09-30 sells on 2030's NOI) — with occupancy held at its stabilized
level; a single forecast year's occupancy gain (a recovery year) is never compounded in perpetuity.
Your own ADR growth for the hold's last year carries to the exit when you set no figure for the
year after. 0% (badged No forecast) when there is no figure. The status names the year and the
convention used. After a forecast series ends, ADR grows at the series' forecast average (labelled
assumption) and occupancy holds at its last level, so an exit NOI year past the series uses that
average; the 95% occupancy cap still applies.

<!-- source: apps/worker/app/services/engine_runner.py (REVENUE_BASIS_KEYS, _apply_revenue_basis, _revenue_basis_failure) -->
There is no default Year-1 occupancy or ADR. Year 1 comes from your value, else (benchmark mode)
the benchmark × penetration × ramp or (growth-only mode) the Year-1 chain above, else the T-12. With none of them the revenue model and every model built on it
refuse with {{reason:no_revenue_basis}}: "Upload a T-12, or enter Year-1 occupancy and ADR" (benchmark
mode: "Upload a T-12 or STR report, or enter Year-1 occupancy and ADR"). The capital stack still runs.

<!-- source: apps/worker/app/services/engine_runner.py (_default_assumptions) -->
Defaults that remain labelled suggestions: expense growth 3.5%, F&B revenue $88.00 per occupied
room, other revenue 6.5% of rooms revenue. Each is badged Seed until replaced. There is no growth
default.

<!-- source: apps/worker/app/engines/revenue.py (fb_price_index) -->
F&B revenue = occupied rooms × F&B $ per occupied room × Π(1 + ADR growth) since Year 1, where the
ADR growth is the resolved per-year market growth (a year without a figure holds flat).

<!-- source: apps/worker/app/services/engine_runner.py (_load_engine_inputs: fb_revenue_per_occupied_room, other_revenue_pct_of_rooms, OTHER_REVENUE_PCT_CAP 0.30, uncapped_ratio / cap on the source field, _loader_notices) -->
When the primary statement carries the lines, the two revenue-mix anchors come from it: F&B revenue
÷ (Year-1 occupancy × keys × 365) per occupied room, and (other operated + miscellaneous revenue) ÷
rooms revenue. The ratio is capped at 30%; a statement that reads higher is clipped with a
{{reason:needs_review}} note giving its own ratio, and the source row records the uncapped ratio
and the cap — a clip is never silent.

:::

::: card
#### The projection calendar

<!-- source: apps/worker/app/engines/revenue.py (projection_calendar_years), apps/worker/app/engines/stabilization.py -->
Operating Year 1 is the calendar year containing the first operating month, which is the month
after the acquisition close. Every later column is that year plus one. With no close date no
calendar year is printed. The horizon is the hold period plus one Exit Year column.

:::

::: card
#### Expense lines — the four methods and the ratio defaults

<!-- source: apps/worker/app/engines/expense.py (PROJECTION_METHOD_LINES, hotel-type ratio table), packages/schemas-py/fondok_schemas/underwriting.py -->
Growth: Year-1 amount × (1 + rate) each year; a blank rate uses the model's expense growth. % of
revenue: departmental lines against their own department's revenue, undistributed lines against
total revenue; a method with no value keeps the line's own Year-1 ratio. Per occupied room and per
available room need the key count; without it the line keeps its default and says why.

<!-- source: apps/worker/app/engines/expense.py (_resolve_dept_ratio), apps/worker/app/services/engine_runner.py (t12_dept_revenue) -->
Departmental default: ratio × that year's (displaced) department revenue. Ratio = T-12 expense ÷ the
same statement's department revenue (other = other + misc), else ÷ undisplaced projected Year-1
revenue, else the ratio below. A T-12 ratio outside rooms 5–75%, F&B 20–150%, other 0–150% is
rejected and flagged Needs review.

<!-- source: apps/worker/app/services/engine_runner.py (ratio precedence: analyst_override > t12_actual > portfolio_pnl > seed), apps/worker/app/engines/expense.py (hotel-type ratio table) -->
A line the statement does not carry runs on a ratio resolved in this order: analyst override, the
T-12 actual, the firm's portfolio P&L ratio when one is on the deal, then the platform default
for the hotel type. The platform defaults by hotel type (share of the department's revenue for
departmental lines, share of total revenue for undistributed and fixed lines):

<!-- source: apps/worker/app/engines/expense.py (hotel-type ratio table: select, full, lifestyle, luxury) -->
| Hotel type | Rooms dept. | F&B dept. | Other dept. | Undistributed | Fixed charges |
| --- | --- | --- | --- | --- | --- |
| Select service | 27% | 65% | 45% | 22% | 6% |
| Full service | 30% | 75% | 50% | 26% | 7% |
| Lifestyle | 30% | 75% | 50% | 26% | 7% |
| Luxury | 32% | 78% | 55% | 30% | 8% |

<!-- source: apps/worker/app/engines/expense.py (ExpenseEngineOutput.synthesized_lines, SynthesizedLine source seed / ratio_override, reason), apps/worker/app/export/live_payload.py (_build_op_ratio_provenance synthesized_lines), apps/worker/app/export/excel.py (_build_assumptions, _build_op_ratio_provenance), apps/worker/app/export/memo_pdf.py (_render_op_ratio_provenance) -->
Every Year-1 line synthesized at a ratio is listed on the Excel Assumptions and Op-Ratio
Provenance sheets and in the memo, badged Seed with its reason (“no T-12 line for Utilities;
hotel-type default 5.50% of total revenue (select)”).

:::

::: card
#### Management fee, incentive fee and the waterfall

<!-- source: apps/worker/app/engines/expense.py (mgmt_fee_pct, ffe_reserve_pct, incentive_mgmt_fee_pct_of_gop), evals/golden-set/usali-rules.csv (MGMT_FEE_RANGE, INCENTIVE_MGMT_FEE_RANGE) -->
Base management fee and FF&E reserve: a share of each year's total revenue, Year 1 included (never
the T-12 dollars; an analyst's Year-1 override wins as a fixed dollar amount); the management
agreement's stated % outranks a portfolio ratio; defaults 3.0% / 4.0%. Both accept 0–10%. The USALI check warns
when the base fee is outside 2–6% of total revenue; it is a warning, not a correction, and applies
to every operating model. Incentive fee: optional, 0–50% of gross operating profit after the base
fee, computed each year as max(0, GOP − base fee) × rate; the USALI check notes an incentive fee
above 20% of GOP.

<!-- source: apps/worker/app/engines/expense.py (noi_institutional, noi) -->
NOI (before FF&E reserve) = GOP − base management fee − incentive fee − fixed charges. Cash NOI
(after FF&E reserve) = NOI (before reserve) − FF&E reserve. The entry cap rate is struck on the
before-reserve figure; debt coverage and the exit valuation use the after-reserve figure.

:::

::: card
#### The Stabilization Year

<!-- source: apps/worker/app/engines/stabilization.py (DEFAULT_STABILIZATION_YEAR = 3, DEFAULT_STABILIZATION_ANCHOR = acquisition_close) -->
Default: operating Year 3 counted from the acquisition close; on a hold shorter than three years
it is clamped to the last projected year and reported as clamped. With a reopening date set, Year
3 is counted from the first full operating year after reopening; a reopening the calendar cannot
place (no close date, or a Year 3 before the first projected year) falls back to the close anchor
and states the exception. The analyst's stabilization year wins over either anchor. It selects
the year the stabilized figures are read from, and occupancy growth into every later year is 0
(held after stabilization, both topline modes); with the benchmark method it also ends the ramp.

:::

::: card
#### Exit NOI — the three bases

<!-- source: apps/worker/app/engines/returns.py (ExitNoiBasis, terminal NOI resolution) -->
Forward twelve-month (default): the next projected year's Cash NOI where the projection has it;
for the final hold year, the last hold year's Cash NOI × (1 + RevPAR growth). Stabilized: the
stabilized year's Cash NOI × (1 + RevPAR growth) ^ (exit year − stabilized year); when the
stabilized year falls after the exit, the exit year's own Cash NOI is used and the label says so.
Sale-year: the exit year's own Cash NOI, not grown. An analyst-entered terminal NOI beats every
basis and applies to the selected exit only. The label printed beside the exit NOI names the
basis and period actually used.

:::

::: card
#### Exit cap rate — precedence

<!-- source: apps/worker/app/services/engine_runner.py (_seed_defaults exit_cap_rate 0.07; OM comparable-sales median with the 4–11% admission band and the three-comp minimum) -->
1. Analyst override.
2. OM comparable sales: when three or more comparable sales carry cap rates between 4% and 11%,
   their median. Fewer than three, or none inside the band, resolves to a dash reason (no
   document, no matching field, or not knowable as of the underwriting date), never to a cap rate.
3. Platform default 7.0%, badged Seed.

:::

::: card
#### Returns by exit year — what a row is, and when it is a dash

<!-- source: apps/worker/app/engines/returns.py (exit-year cases; ReasonCode.NO_SALE_VALUE, UNDER_CONSTRUCTION, CAPITAL_DEPLOYING; "no IRR" when the cash flows have one sign), apps/worker/app/services/engine_runner.py (_seed_defaults selling_costs_pct 0.02, transfer_tax_pct 0.0) -->
For a sale at month m: gross sale = exit NOI on the basis above ÷ exit cap rate; selling costs =
gross sale × selling costs (default 2.0%); transfer tax = gross sale × transfer tax rate (default
0 until set); the loan balance at that month is repaid; a refinancing is included only when it
precedes the sale. A row is withheld with its reason, never zero, when: the exit NOI is not
positive (no sale value); the sale month is inside a development deal's stated construction
window (under construction); an equity draw or deferred capital lands after that month (capital
still deploying); or the equity cash flows have one sign, so no IRR exists. The headline figures
are the selected exit's row — the hold, or the disposition month when one is set.

:::

::: card
#### Hold period defaults

<!-- source: apps/worker/app/services/engine_runner.py (_seed_defaults hold_years 5), apps/worker/app/services/deal_type.py (DEV_HOLD_DEFAULT: development 7, adaptive_reuse 6) -->
Acquisition: 5 years (platform default, badged Seed). Ground-up development: 7 years; adaptive
reuse: 6 years — both badged "Default for development deals — confirm". An analyst hold period
replaces any of them.

:::

::: card
#### Renovation displacement — bounds and arithmetic

<!-- source: packages/schemas-py/fondok_schemas/underwriting.py (y1_occupancy_displacement_pct, y1_adr_displacement_pct 0–0.50), apps/worker/app/engines/revenue.py (renovation_displacement_factors) -->
Year-1 rooms-offline and rate displacement: each 0–50%, default 0, justification note required.
Displacement schedule: for each model year y (months 12(y−1)+1 to 12y), every month weighs 1/12;
rooms offline across overlapping periods add up (capped at 100%), as do rate discounts; occupancy
factor = the twelve-month average of (1 − rooms offline); ADR factor = the open-room-weighted
average of (1 − rate discount). Six months at 25% offline and a 10% discount give occupancy ×
0.875 and ADR × 0.957. When a schedule is set, the Year-1 percentages and any closure schedule
are not applied on top.

:::

::: card
#### Renovation budget — precedence and checks

<!-- source: apps/worker/app/services/renovation_budget.py, apps/worker/app/agents/extraction_schemas/capex.md (2% scope-sum tolerance), apps/worker/app/services/engine_runner.py (_seed_defaults renovation_budget, working_capital, closing_costs_pct) -->
1. Analyst override, including a candidate selected with "Use this".
2. PIP or capital plan filed in the PIP / Renovation Budget lane that states a total — the newest
   plan date wins (plan date, else report date, else upload date).
3. The offering memorandum's broker pro forma renovation budget.
4. Platform default, badged Seed with its reason (no document of the needed type, or no field
   that states the figure): $5,280,000. Two other dollar defaults sit beside it in the same
   table — working capital $500,000 and closing costs 2.0% of the purchase price. Each is used
   only when no document states the figure, and each carries the Seed badge and its reason
   wherever it is printed, so a reader never mistakes it for the deal's own figure.

<!-- source: apps/worker/app/agents/extraction_schemas/capex.md (2% scope-sum tolerance), apps/worker/app/services/renovation_budget.py -->
A PIP whose stated total differs from the sum of its own scope items by more than 2% is flagged
for review at extraction; the value is never altered. No budget, whatever its source, displaces
Year 1 on its own.

:::

::: card
#### Identity inputs — when the model refuses to run

<!-- source: apps/worker/app/services/engine_runner.py (IDENTITY_INPUT_KEYS, MissingIdentityInputsError), packages/schemas-py/fondok_schemas/reasons.py (missing_identity_inputs) -->
The key count and the purchase price have no default. A deal missing either does not run: the
run is recorded as refused under the reason "missing identity inputs", naming the inputs, and
every figure that depends on the model shows a dash with that reason. No placeholder property is
ever substituted. The Year-1 occupancy and ADR have no default either: with no T-12, no STR or
market benchmark and no analyst rate the revenue model and everything built on it are refused under
"no Year-1 occupancy / ADR" ({{reason:no_revenue_basis}}), and the Future P&L and Overview take the
missing rate in place.

:::
:::

<!-- section 2: Projection methodology — from EngineeringMethodology.tsx lines 162-358 -->
::: engineering {title="Projection methodology"}
::: intro
Year-1 anchors source from the deal's actual extracted data when available; out-years compound from
the un-displaced baseline so a heavy PIP doesn't permanently depress the curve. The full precedence
chain by metric:
:::

::: card
#### Occupancy + ADR by year (topline methodology v2)

<!-- source: apps/worker/app/services/engine_runner.py (_apply_topline), services/topline.py, topline_benchmark.py, topline_growth.py, engines/revenue.py (_plan_topline) -->
::: chain
1. **Analyst override** — an explicit `starting_occupancy` / `starting_adr` is that metric's Year 1
   (`topline.explicit_year1`, status `explicit_value`): not penetrated, grown at the resolved growth.
2. **Benchmark × penetration × ramp** — `benchmark_by_year[y] × pen(y) ÷ 100`, the benchmark from
   `resolve_benchmark` (comp set → submarket → market → none; `topline_benchmark_level` forces one)
   grown from its own period by the resolved per-year growth; `starting_*` carries the Year-1 value,
   badged `topline_benchmark`.
3. **Subject's own level** — with no benchmark for a metric, the T-12 actual (else the labelled seed)
   × the resolved growth (`benchmark_unavailable`, or `flat_no_growth` for a year with no figure).

:::

Growth lists (`occupancy_growth_by_year` / `adr_growth_by_year` on the revenue input) are the
RESOLVED figures; index 0 is the growth that took the benchmark into Year 1 when it was applied. The
CBRE Hotel Horizons forecast-window CAGR and its Year-1 anchors no longer feed the model (the
broker-vs-market variance flag still compares against the CAGR).

**F&B price inflation.** `fb_revenue = occupied_rooms × fb_revenue_per_occupied_room ×
fb_price_index`, where `fb_price_index` compounds the resolved per-year ADR growth
(`adr_growth_by_year`, `None` held flat) from Year 1 and is 1 in Year 1. Under the topline that is
the market ADR growth the benchmark grows by — deliberately not the penetration ramp, which moves the
hotel's position in its market, not its prices. The trace on
`revenue.years[].fb_revenue` carries the index. Before 2026-11 the F&B $ per occupied room was held
flat for the hold, so F&B revenue grew only with occupancy (about 0.8% a year on the Kimpton
fixture against 4% ADR growth). Renovation displacement moves occupied rooms, never the index.

:::

::: card
#### The projection calendar, and what "Base Year" means

The revenue engine projects **ordinals** — operating year 1 through the hold period — not calendar
years. The calendar is derived in one place: the **acquisition close date** on the deal, the same
date the transaction timeline is built from. Operating Year 1 is the calendar year containing the
first operating month (close + one month), so a 30 September 2025 close projects Year 1 in 2025 and
a 15 December 2025 close projects it in 2026. Every later column is that year plus one.

**With no acquisition close date the statement prints no year at all** — the column shows its label
and a dash. It never falls back to the wall clock or to the year a document happened to be extracted
from; a guessed year is a fabricated number.

The first column is labelled **Base year (Year 1)** because that is exactly what it is:
`revenue.years[0]` IS the model's first operating year. It is the value the returns engine consumes
as `year_one_noi` and the value the Cash Flow statement labels "Year 1". The P&L used to call it
"Base Year" and then label the NEXT column "Year 1", which put every column a year ahead of its own
label. The labels were corrected; the engine was not re-indexed, so no figure moved. There is no
separate pre-close base year in the model today.

The horizon is **hold period + 1** columns. The extra column is the **Exit Year**, and it is
display-only: it carries the forward 12-month NOI the reversion is valued on and a dash on every
other line. Year hold+1 is deliberately NOT run through the expense waterfall — see the
exit-valuation note below.

:::

::: card {testid=methodology-line-methods}
#### Per-line expense projection methods

Eight expense lines can be re-driven one at a time from the method chip on their Future P&L row:
Rooms, F&B and Other Operated departmental expense, and A&G, IT & Telecom, Sales & Marketing,
Property Operations and Utilities. Management fee, FF&E reserve and fixed charges keep their own
drivers. The four methods are **Growth** (Year 1 grown each year; blank = the model expense
inflation), **% of revenue** (the department's own revenue for departmental lines, total revenue for
undistributed lines), **POR** (dollars per occupied room) and **PAR** (dollars per available room).

**Default for departmental lines.** With no method picked, Rooms, F&B and Other Operated expense
are `% of revenue` of their own department at a ratio fixed in Year 1 (`expense.line_methods`
reports it). The ratio is always on undisplaced revenue, and the first basis inside
`DEPT_RATIO_BAND` (rooms 5–75%, F&B 20–150%, other 0–150%, defined once in `engines/expense.py`)
wins: (1) T-12 departmental expense ÷ the T-12 revenue of the same department from the same
statement and period (`t12_dept_revenue`; the loader publishes a department only when the expense
row and every revenue row share one `extraction_result_id` and scope, no row is a sub-period slice,
the expense is not an analyst override, and rooms revenue is within 25% of the statement's occupancy
× ADR × keys × 365); (2) the T-12 expense ÷ projected Year-1 department revenue with the Year-1
displacement divided back out (`fb.years[0].*_displacement_factor`); (3) the ratio override /
applied benchmark / hotel-type ratio. A T-12 basis the band rejects marks every year of the line
`needs_review` (trace reason, with the rejected figure in the note and on `line_methods`). Every
year, Year 1 included, is ratio × that year's projected department revenue, so a renovation-displaced
year's departmental expense is displaced with its revenue (before 2026-11 the T-12 dollars sat against displaced revenue and later
years grew the dollars at the expense growth, which put F&B expense at 130% of F&B revenue on the
Kimpton Angler). Undistributed and fixed lines keep their dollar growth.

A method picked without a value holds the line's own Year-1 ratio, so no benchmark number is
introduced. POR and PAR need the key count; without it the line stays on its default and says why. A
line nobody has touched runs exactly as before, and the expense engine reports the active method and
value for every line (`expense.line_methods`). A method change needs a justification note like any
other override. The Future P&L Excel export lists every line's method and value on its Assumptions
sheet, and an edited workbook re-imports through the same preview and note rule. Changing a method
there without a new value drops the old value, because its units belonged to the old method.

**Operating model.** The new-deal wizard records whether the hotel is owner-operated, third-party
managed or brand-managed. The Overview shows it and the Future P&L shows it next to the management
fee. It moves no number: the fee check is the general USALI 2–6% of total revenue range for every
operating model, and nothing below changes the management fee assumption — the analyst sets it.

**Fee terms from the management agreement.** A LEASES / CONTRACT document counts as the hotel
management agreement only with evidence — its filename (HMA / management agreement), a row naming
the agreement, or a row's own text; any other lease sets no fee term. The agreement sets
`mgmt_fee_pct` from its base-fee % and `ffe_reserve_pct` from its FF&E reserve %, badged
`management_agreement`; the provenance shows the file and the agreement's own words ("Base Fee:
3.25% of Hotel Gross Revenues and $76,000 in Lieu Fee"), not the extractor path. They outrank a
portfolio P&L ratio and the seed, and an analyst override still wins. A stated **fee in lieu** is
its own *Management fee in lieu* line every year (`expense.years[].mgmt_fee_in_lieu`, deducted with
the base fee before NOI and in the incentive base), escalated only at a rate the agreement states,
else flat — the trace says which. Its incentive fee is modelled (`incentive_mgmt_fee_pct_of_gop`)
only when the agreement states the basis as gross operating profit with no hurdle; otherwise the
assumption carries `awaiting_analyst` quoting the agreement (the Angler: "15% of Hotel Net
Operating Profit above 10% of Total Hotel Cost") and saying why it is not modelled.

{testid=methodology-incentive-fee} **Base vs incentive management fee.** The management fee (`mgmt_fee_pct`) is the *base* fee: a share
of each year's total revenue, Year 1 included. The T-12 management-fee dollars are history: they
never drive the projection (before 2026-11 they were carried into Year 1, which put the Angler's
Year-1 fee at 4.8% and 3.9% of its renovation-displaced revenue). An analyst who enters a Year-1
fee on the worksheet's Management Fees row, with a note, sets Year 1 to that figure (trace
assumption `mgmt_fee`); an override equal to the resolved figure states nothing and is ignored. That
figure is a fixed dollar amount, not a % of revenue: it does not follow renovation displacement, and
the worksheet row shows a "Fixed Year-1 $" chip while it is in force. The FF&E reserve follows the
same rule (`ffe_reserve_pct` × revenue every year; review I-3, 2026-11: Year 1 used to carry the
T-12 reserve dollars, 3.98% / 4.21% of the Angler's displaced revenue). The
*Incentive fee* beside it on the Future P&L Assumptions panel (`incentive_mgmt_fee_pct_of_gop`) is
opt-in and blank by default — blank means no incentive fee is modelled and every number is exactly
what it was without it. When set, each year's incentive fee is `max(0, GOP − base fee) × rate`: a
share of GOP after the base fee, never negative, so a year whose GOP does not cover the base fee
earns the operator nothing. No owner's-priority hurdle is modelled; the rate applies to all GOP
after the base fee. The fee is deducted between the base fee and NOI, so it lowers both NOI bases
and everything downstream (debt coverage, the exit value, returns). It appears as its own *Incentive
management fee* row on the Future P&L (only when set), with its own trace
(`expense.years[].incentive_mgmt_fee`), and on the Excel export's Assumptions sheet, which
re-imports it. Setting it needs a justification note like any other override. It applies mostly to
third-party and brand-managed hotels.

::: box {testid=methodology-mgmt-fee-guidance}
##### Management fee — directional guidance by operating model

*{{slot:mgmt-fee-disclaimer}}* The same text is shown beside the management fee on the Future P&L.

{{slot:mgmt-fee-guidance}}

:::

:::

::: card {testid=methodology-future-pl}
#### Reading the Future P&L

The tab is **P&L**, with two views: **Historical P&L** — the normalized operating actuals, one
column per extracted statement — and **Future P&L**, the forward business plan the returns are built
on. The Overview carries a compact *Future P&L · Years 1–5* card (Total Revenue and NOI before the
FF&E reserve per year, read from `revenue.years` and `expense.years`) with a "→ P&L" link; it shows
a dash until the engines have run.

**Column headers.** Every projected column reads `Year N · 2026` — the ordinal and the calendar year
the revenue engine derived from the acquisition close date. With no close date the header is the
bare `Year N` and a one-line note says to set the acquisition date on CAPEX; no year is ever
guessed. The first column reads `Base year (Year 1) · 2025`, and its basis line names the period of
the primary financial statement ("T12 Mar 2025", exactly as the Historical P&L column heads it) when
the Data Room has flagged one and its extraction states the period; otherwise that line is a dash.

**Click-through.** Every projected Occupancy, ADR, RevPAR and statement amount opens the
value-lineage drawer for its engine field (`revenue.years[i].adr`,
`expense.years[i].noi_institutional`, …), which walks the inputs the engine asserted — starting
value, growth, index or penetration where present — down to the source rows. When the lineage record
carries no walk for a cell the drawer says "No trace available for this cell."; the app never prints
a formula it did not record.

**Implied Value row.** `IMPLIED_VALUE_LABEL` "Implied Value (Cash NOI ÷ exit cap)", below the Forward
12-Month Cash NOI row (`lib/impliedExitValue.ts`). Operating year *i* = `expense.years[i].noi` ÷ the
exit cap the returns engine ran on (`returns.exit_cap_rate`; until the returns engine has run the
row dashes — nothing stands in). The Cash NOI row above it is the same `expense.years[i].noi` (a run
that predates `noi_institutional` used to have the reserve deducted twice). The Exit Year cell = `returns.gross_sale_price` (= `terminal_noi ÷
exit_cap_rate`), as published. Each cell's tooltip states its basis; a missing cap rate, Cash NOI or
sale price renders "—" with the reason, and no hard-coded default cap rate is used. Until Wave 5 the
row (then "Implied Exit Value") capitalised GOP − management fee, about 50% above the engine's sale
price on the Kimpton deals.

**Ratios.** The "% Rev" sub-column (the *Show %* switch, on by default) is each line as a share of
that year's Total Revenue. Departmental expense lines follow the USALI convention instead — Rooms
expense ÷ Rooms revenue, F&B expense ÷ F&B revenue, Other Operated expense ÷ Other Operated revenue
— and are marked *dept*. Every ratio is computed from the same engine year values the Amount cells
print.

{testid=benchmark-column-methodology} **Benchmark column.** When the deal carries a P&L benchmark report (CBRE Benchmarker / "Trends in
the Hotel Industry" / HotStats, uploaded in the Comp Set / Market slot), the Base-year column group
gains a *Benchmark* sub-column (the *Benchmark* switch, on by default and remembered per device).
Thirteen lines are mapped — Rooms, F&B and Other Operated departmental expense; A&G, IT, Sales &
Marketing, Property Operations, Utilities; GOP; Management Fees; Property Taxes, Insurance and Rent.
Each prints the comp-set ratio on the row's own basis (departmental expense ÷ that department's
revenue, everything else ÷ total revenue — the same basis as its % Rev cell, served by `GET
/deals/{id}/market-data · pnl_benchmark.categories`) and the model's Year-1 ratio minus it, in
points. The colour marks direction only — model above or below the benchmark — never a verdict; no
threshold is invented. A line the report did not publish, or a row with no benchmark mapping, shows
a dash with the reason. Its tooltip names the benchmark line, the peer-set size, POR / PAR, the
source document and how the ratio was derived (line total ÷ revenue total, PAR ÷ PAR, as printed, or
the legacy summary). Without a benchmark the column does not exist. The fuller comparison (model vs
benchmark, POR, PAR for every mapped line) stays under the collapsed *Benchmark detail* disclosure
below the table, and the Excel export carries the same ratios as a read-only *Benchmark %* column
that import ignores.

{testid=benchmark-suggestions-methodology} **Benchmarks are suggestions.** A CBRE / HotStats benchmark never flows into the model on its own.
For four assumptions the worker turns the report into a suggestion (`GET
/deals/{id}/benchmark-suggestions`): the Rooms and F&B departmental ratios (the same ratio the
Benchmark column prints for that row, or the report's summary `rooms_dept_pct` / 1 −
`fb_dept_margin` when it has no line detail), the management fee (the Management Fees line, else
`mgmt_fee_pct`) and the FF&E reserve (`ffe_reserve_pct`). Each suggestion lists the value, the
benchmark line, the peer-set size, the document and page, and the value the model is on now with its
source. A departmental ratio outside 0–100%, or a fee or reserve outside 0–10%, is not suggested.
Until the analyst acts, each assumption keeps its existing source: your portfolio P&L ratio when one
is on the deal, else the platform default (the hotel-type departmental ratio, a 3% management fee, a
4% FF&E reserve), labelled as such. *Apply benchmark* sits on the Rooms, F&B and Management Fees
rows of the Benchmark column and beside the *Management fee* and *FF&E reserve* inputs in the
Assumptions panel's Deal economics card (the reserve input shows the value the model is on — your
override, else the loader's value — and takes an override with a note like the fee). It opens a
justification note prefilled as "Applied CBRE benchmark \<line\> \<value\> from \<document\>
p.\<page\>", which you can edit. Saving writes an ordinary analyst override with that note and
re-runs the model; if the save fails the note stays open for you to retry. From then on the value
reads as an analyst override, and the note travels with it like any other override. Approval is
reversible: once an override exists on the management fee or the FF&E reserve, *Reset to source*
beside that input removes it, so the portfolio P&L ratio or the platform default drives the model
again, and re-runs. Precedence is otherwise unchanged. A Year-1 amount the T-12 states is still used
as stated, and a departmental line the T-12 states runs on its T-12 cost ratio. The applied ratio
drives what the T-12 does not state: a departmental line the T-12 leaves out (that ratio × the
department's revenue, every year), and the management fee and FF&E reserve in every year, Year 1
included, where each year is that year's total revenue × the ratio.

**The waterfall below Total Revenue** runs GOP → Management Fees (→ Incentive management fee, when
one is modelled) → EBITDA → Fixed Charges (property taxes, insurance, equipment lease / rent, other)
→ **NOI (before FF&E reserve)** → FF&E Reserve → **Cash NOI (after FF&E reserve)**, each line
labelled with its basis. The reserve sits between the two NOI lines because that is where the
expense engine deducts it (`noi_institutional = gop − mgmt_fee − incentive_mgmt_fee − fixed`, the
incentive term zero when none is modelled; `noi = noi_institutional − ffe_reserve`).

**Numbers.** Nothing on the tab renders in scientific notation; ADR and RevPAR print to the cent;
every amount cell carries its full value — thousands separators and cents — in its tooltip. Year
columns are separated by a rule and an alternating tint, and both views scroll horizontally behind a
persistent scrollbar with a "N more years →" cue whenever columns sit off-screen.

:::

::: card
#### The Stabilization Year

"Stabilized" names one projection year, and every stabilized figure on every screen — Stabilized
Occupancy, ADR, Revenue, NOI, NOI Margin, Yield on Cost, the IC memo's scenario summary and Scenario
Analysis' comparison row — is read from *that* year. It is published as a single block by the
expense engine, so two surfaces cannot resolve it differently.

The stabilization year defaults to **Year 3 of operations** (badged "Default — confirm"); on a hold
that ends sooner it is the last projected year, and the card says so. The analyst can override it in
P&L → Future P&L → Assumptions (badged "Analyst override"; re-saving the default unchanged is not an
override), and the same default year drives the Financing tab's stabilized DSCR and debt yield. The
year in which occupancy first reaches the stabilized assumption (or NOI growth settles) is still
shown beneath it as a *model-detected* hint, but it never selects the year — and stabilized NOI
(that year's operating NOI) is a separate figure from the exit NOI the sale is capitalised on.

{testid=methodology-stabilization-anchor} **What Year 3 counts from.** With no reopening date the default counts from the **acquisition
close**: projection Year 1 is the first operating year after close, so the default is projection
Year 3 and the Overview reads "Year 3 after close". For a **material repositioning or re-flag** the
analyst sets the **reopening / re-flag date** on the Overview's Investment Profile card (stored on
the deal as `reopening_date`; a change on an underwritten deal carries a justification note, since it
moves where the default penetration ramp ends). The default then counts
Year 3 from the *first full operating year after reopening* — a hotel reopening in March 2027 has
2028 as its first full year, so Year 3 is 2030 — placed on the projection through the same calendar
the Future P&L column headers use, and the Overview and Future P&L read "Year 3 after reopening (Mar
2027)" (source `default_year_3_reopening`). A reopening date Fondok cannot place — no acquisition
close date to derive the calendar, or a reopening whose Year 3 precedes the projection — keeps the
close anchor and says why; it never guesses. The second case is Sam's MVP fallback (decision #7,
2026-10-15): the stabilization year is measured from the acquisition close, and the exception is
stated in plain sight wherever the anchor is shown — a notice on the Overview's Stabilization
section, the Stabilization Anchor row, the reopening-date field, and the Future P&L Stabilization
Year note all read "Exception: Year 3 after the reopening date (Jun 2019) falls before the first
projected year, so stabilization is measured from the acquisition close instead." The anchor caption
— the stabilized year with the anchor it counts from, "Year 3 after close (default)" or "Year 6 —
Year 3 after reopening (Mar 2027) (default)", with that exception note when the fallback applies —
appears wherever a stabilized figure is printed: the Overview's Stabilization section, the Future
P&L's Stabilization Year note, CAPEX's Exit NOI basis row when the basis is Stabilized NOI,
Financing's stabilized Debt Yield and DSCR cards, and Returns' Yield on Cost. Stabilized NOI stays a
separate figure from exit NOI: the Overview prints "Stabilized NOI" (the stabilized year's NOI
before the FF&E reserve) in its Stabilization section and "Exit NOI (\<basis and period\>)" in its
Exit section, and Returns' Yield on Cost names "Stabilized NOI (Year N, before FF&E reserve)" while
its Exit Value names the exit NOI it capitalises ("Exit NOI (\<period\>)", or forward NOI on an
older run) — never one number under both labels. The analyst's override wins over either anchor, and
the debt engine's stabilized DSCR and debt yield follow the same resolved year.

The Stabilization Year is a **reporting** assumption: it selects which projected year the stabilized
figures are read from. It moves no cash flow. Setting or changing it leaves the gross sale price,
the levered and unlevered IRRs, the equity multiple and the terminal NOI bit-identical — there is a
regression test that asserts exactly that. When no year resolves, every stabilized figure renders as
a dash with a reason; none of them falls back to another year.

:::

::: card
#### What the exit is valued on

The reversion capitalises the **forward 12-month Cash NOI** — the after-FF&E-reserve basis — at the
exit cap rate. That forward figure is the last modelled hold year's Cash NOI grown at the RevPAR
growth rate; it is an extrapolation, not an operating year projected through the expense waterfall,
and the Exit Year column says so by showing its formula and nothing else. An analyst can switch the
exit basis (`exit_noi_basis`) to **stabilized**: the stabilized year's Cash NOI (the analyst's
stabilization year, else the derived one) grown at the same rate to the exit year, or the exit
year's own NOI when stabilization falls after the exit. Returns names the basis it ran on — e.g.
"Forward 12-month NOI (Year 6)" or "Stabilized NOI (Year 3, grown to Year 5)" — and a
`terminal_noi_override` still beats either basis.

This matters because the entry cap rate is struck on NOI *before* the reserve while the exit is
struck on Cash NOI *after* it. Both are defensible conventions and Fondok keeps both, but it names
each one wherever it prints it — the Overview's Exit row reads "Exit NOI (forward 12-month, after
FF&E reserve)" and the Future P&L row "Forward 12-Month Cash NOI (after FF&E reserve)", never a bare
"NOI". Modelling a real year hold+1 through the waterfall instead would move the gross sale price,
both IRRs and the equity multiple on every persisted deal; that is a deliberate post-MVP decision,
not an oversight.

{testid=exit-noi-basis-methodology} **Exit NOI basis.** Forward 12-month NOI is the default. An analyst can switch the basis to
**Stabilized NOI** on CAPEX → Exit / Reversion: the reversion then capitalises the stabilized year's
NOI grown to exit instead. An analyst-entered terminal NOI override beats both bases. Wherever the
Exit NOI is printed, its label names the basis and the period it was taken on, as the run reports
them (`returns.exit_noi_period_label`).

:::

::: card
#### Year-1 renovation displacement

{testid=methodology-displacement-explicit-only} **Only explicit assumptions displace Year 1.** A renovation budget is a capital cost: it flows into
Sources & Uses and the equity requirement, and it never reduces Year-1 occupancy or ADR on its own —
whether the analyst typed it in, the OM's broker pro forma states it, or a PIP / CapEx document
does. Year 1 is displaced only when the analyst sets an explicit assumption: **Y1 Rooms Offline
(Occupancy Displacement)** (`y1_occupancy_displacement_pct`) and **Y1 Rate (ADR) Displacement**
(`y1_adr_displacement_pct`) on CAPEX → Initial Renovation / PIP, each 0–50% with a justification
note; a **renovation displacement schedule** (below — explicit timing, rooms offline and rate
discount per period, the lever Sam asked for); or a structured PIP closure schedule (closure
strategy, % of rooms offline by month, brand recovery curve). All default to off. No extraction
schema reads a rooms-offline or rate-displacement figure off a document today — a PIP / CapEx
document's spend timing is dollars by year, not rooms out of service — so nothing extracted sets
them. Years the renovation does not fall in compound from the un-displaced baseline, so a renovation
affects only the year it is modelled in.

{testid=methodology-displacement-schedule} **The renovation displacement schedule.** CAPEX → Initial Renovation / PIP → **Renovation
displacement schedule** takes a list of periods (`renovation_displacement_schedule`, one override
with one justification note; also rows on the Future P&L Excel Assumptions sheet), each with a first
and last month counted from the acquisition close (1 = the first operating month, inclusive), the
share of rooms offline and the rate discount on the rooms that keep selling. The revenue engine
turns it into one occupancy factor and one ADR factor per model year: model year *y* is months
12(*y*−1)+1 to 12* y* and every month weighs 1/12 (calendar day counts are ignored); in each month
the rooms offline across the periods covering it add up (capped at 100%), as do the rate discounts;
**occupancy factor** = the 12-month average of (1 − rooms offline), applied to the year's occupancy
on the full inventory, so taking rooms offline reads as lower occupancy and RevPAR still equals
rooms revenue ÷ rooms available; **ADR factor** = the open-room-weighted average of (1 − rate
discount), applied to the year's ADR, so the discount only touches the rooms that actually sold. Six
months at 25% offline and 10% off therefore give Year 1 occupancy × 0.875 and ADR × 0.957 — the same
as summing twelve months of open share × discounted rate, one calculation applied to the two lines
rather than two deductions. A period in months 13–18 displaces Year 2 the same way while Year 2's
base still compounds from the un-displaced Year 1. When a schedule is set it is the **only**
renovation impact applied: the Y1 displacement percentages and any PIP closure schedule are not
applied on top (the status line says so), so nothing is ever counted twice. With no period entered —
the default — every deal runs exactly as before, and the renovation budget alone never moves a
single operating line.

{testid=methodology-displacement-status} **The model says what it decided.** The worker reports a displacement status
(`assumption_sources.displacement_status`) once every override is applied, and the CAPEX tab's
Renovation Budget row and the Future P&L Year-1 basis line print it verbatim — the Future P&L line
links to the CAPEX inputs. With no explicit assumption it reads "No Year-1 renovation impact — set
rooms offline / rate displacement assumptions to model one" (adding that the renovation budget is a
capital cost only when one is set); with one it reads "Year-1 renovation impact on — analyst-set
displacement assumptions (occupancy −10%, ADR −5%)", the percentages being the ones the revenue
engine applies; with a renovation displacement schedule it reads "Renovation impact on — analyst-set
displacement schedule (months 1–6: 25% rooms offline, 10% rate discount). Year 1: occupancy ×0.875,
ADR ×0.957" and, when the Y1 percentages are also set, adds that the schedule wins and they are not
applied on top; or it names the analyst-set PIP closure schedule. Until October 2026 a deal-specific
budget over $5,000 per key automatically cut Year-1 occupancy 15% and ADR 8%; that budget-amount
heuristic has been removed.

:::

::: card {testid=methodology-renovation-budget-precedence}
#### Renovation budget — which document the model reads

::: chain
1. **Analyst override** — A budget you set — including one you selected with "Use this" on CAPEX →
   Renovation budget sources — wins, with its justification note. An override equal to the selected
   document's figure keeps that document's badge.
2. **PIP / CapEx document — latest plan date** — A separate PIP, owner budget, approved capital plan or
   estimate filed in the PIP / Renovation Budget lane (document type CAPEX, doc_subtype = pip — or left
   untagged, which the classifier decides) that states a PIP total (capex_plan.pip.total_usd). It is
   the more detailed source, so it outranks the OM. Among several, the newest plan date
   (capex_plan.document.plan_date, else the report date, else the upload date) wins — a newer approved
   capital plan supersedes an older brand PIP.
3. **OM broker pro forma** — broker_proforma.renovation_budget_usd off the offering memorandum (badged
   "OM Broker") when no PIP / CapEx document states a budget.
4. **Seed** — The Fondok default, with a reason (no_document / no_source). No budget, whatever its
   source, displaces Year 1 — see Year-1 renovation displacement.

:::

Every candidate stays visible. CAPEX → Initial Renovation / PIP → **Renovation budget sources**
lists the OM figure and each PIP / CapEx document's figure with its page, plan date, status
(brand-issued PIP · owner budget · approved capital plan · estimate) and preparer, marks the one the
model is on, and says why each other one is not (superseded by a newer plan, outranked by a PIP, no
PIP total extracted). The list is the worker's (`GET /deals/{id}/renovation-budget-candidates`) and
reports the same resolution the engines ran on; nothing is re-derived in the browser. A PIP whose
stated total is more than 2% away from the sum of its own scope items is flagged for review at
extraction — the value is never altered.

{testid=methodology-capex-lanes} **Three CapEx lanes, one of which feeds the model.** Every CapEx upload carries a lane
(`doc_subtype`), set by the wizard slot it was dropped in or by the lane select in the Data Room:
**PIP / Renovation Budget** (`pip`) is the day-one PIP, owner budget or approved capital plan and is
the only lane the renovation budget reads; **Historic CapEx** (`historic`) is spend already
incurred; **Future CapEx** (`future`) is a forward hold-period capital plan. A CapEx file with no
lane (bulk drop, legacy upload) is classifier-assigned and may ground the model like a PIP — the
Data Room files it under PIP / Renovation Budget so that is visible; re-lane it to take it out.

{testid=methodology-future-capex-reference-only} **Historic and Future CapEx are reference only.** A document filed under Historic CapEx or Future
CapEx is listed separately as "Reference only — not in the model" — only its own namespace is read
off it (`historic_capex.*` items and annual totals, or `future_capex.*` items by year) — and it is
never incorporated into the renovation budget or any other engine input, even when the extractor
also labelled rows in it `capex_plan.pip.*` or it prints a PIP total. The lane the analyst filed it
under wins over the extractor's namespace. When such a document does state a PIP total, the list
says so with the figure and a hint to re-tag it as PIP / Renovation Budget in the Data Room if it is
in fact the day-one PIP.

:::

::: card
#### Exit cap rate

::: chain
1. **Analyst override** — Inline-edited value wins.
2. **OM transaction-comps median** — When the OM carries 3+ comparable sales with cap rates in the
   3–15% sanity band, the median is the anchor.
3. **Platform default 7.0% exit cap** — Default when no comps are extracted.

:::

:::

::: card
#### Expense waterfall

Departmental, undistributed, and fixed-charge lines source from the T-12 first. Zero-valued
extractor rows are treated as "not present" rather than authoritative — those gaps run on a ratio:
an analyst override of that ratio first, else your portfolio P&L's ratio when one is on the deal,
else the platform's hotel-type default. An uploaded P&L benchmark (CBRE Benchmarker / HotStats) is
not one of those sources by itself. Its ratio only becomes an analyst override when you apply it
(see the Benchmark column below).

When a P&L benchmark is uploaded (Comp Set / Market slot → "P&L Benchmark (CBRE / HotStats)"), its
comp-set cost lines are mapped to the model's expense categories and shown on the Future P&L as a
**Benchmark** column beside the model's Year-1 ratios — departmental expense as a % of that
department's revenue, every other line as a % of total revenue, plus the benchmark's POR and PAR.
The ratio is computed from the report's own line and revenue totals where printed, else from its PAR
figures, else taken as printed. Lines the report does not publish show "—". The benchmark is a
suggestion: it changes the model only where the analyst applies it, as described under *Benchmark
column* above.

The waterfall produces **two** distinct NOI figures, and every screen names which one it is showing:

- **NOI (before FF&E reserve)** — GOP minus management fee (base, plus the incentive fee when one is
  modelled) minus fixed charges, *excluding* the FF&E reserve, matching the US cap-rate convention.
  Engine field `expense.years[].noi_institutional` (registry concept `ebitda`). This is the headline
  figure and the numerator of the entry cap rate. Where the product says simply "NOI", it means this.
- **Cash NOI (after FF&E reserve)** — the same waterfall less the FF&E reserve, i.e. cash flow after
  reserves. Engine field `expense.years[].noi` (registry concept `noi`). The Debt engine divides by it
  for DSCR and debt yield, and the Returns engine capitalises it at the exit cap rate to derive the
  reversion — so the exit value is a Cash-NOI valuation, not a before-reserve one.

The two differ by exactly the FF&E reserve. The reserve sits between them in the waterfall and
contributes to Net Cash Flow. A pre-upgrade engine run that never persisted `noi_institutional` is
labelled *"NOI (basis unconfirmed — pre-upgrade run)"* rather than claiming a basis it cannot prove.

:::

::: card
#### When a chain falls through to the seed

Every precedence chain above can end at the platform seed, and when it does the seed now carries a
machine-readable reason rather than an unexplained default — one of the codes in Section 7:
`no_document` when the deal carries no document of the type that would ground it, `no_source` when
the document is there but none of its extracted fields resolves to that assumption, and
`str_unavailable` on runs before 2 November 2026 when STR rates were requested but could not
populate. Growth never falls through to a seed: with no figure it is `no_market_forecast` (held
flat) or `license_withheld`. Each grounded value carries
the opposite record: the exact extraction row behind it — document, field path, page and the concept
it resolved to — so "which line of which statement is this?" is answered from data, not inference. A
figure derived from several rows (a growth CAGR, a corroborated median across statements) names its
document and says so instead of pointing at a row that does not carry the number.

:::

:::

