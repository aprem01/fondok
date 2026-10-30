# MARKET_STUDY (CoStar submarket / market report, feasibility study)

A third-party market report for the subject's submarket: existing room
inventory, the supply pipeline (under construction / final planning /
planned), and a supply-and-demand trend table by year with year-over-year
changes. CoStar Hospitality submarket reports are the common case; a
feasibility study's market section is the other.

Distinct from `STR_TREND` (the subject's own STR report with its comp set)
and from `CBRE_HORIZONS` (CBRE's forward forecast). A CoStar report that
is uploaded under "STR / Comp Set" still belongs here when it describes
the submarket rather than a named comp set.

The Market tab reads the paths below for **Demand Growth** and **Supply
Growth** (FON-61 E-008); the future P&L reads the **performance by class**
series (FON-116) for market occupancy / ADR growth. Percentages are 0..1
decimals; room counts carry `unit` `rooms`.

## Canonical field-path namespace

### Header
- `market_study.market` — metro / market name
- `market_study.submarket` — submarket name
- `market_study.report_date` — ISO date of the report
- `market_study.period_label` — the trend-table period as printed
  (e.g. "12 Mo Ending Jun 2026")

### Inventory + pipeline rollups (rooms)
- `market_study.supply.existing_rooms` — the submarket's current room
  inventory. **Required** for Supply Growth: pipeline rooms are divided
  by this figure.
- `market_study.supply.under_construction_rooms`
- `market_study.supply.final_planning_rooms`
- `market_study.supply.planned_rooms` — proposed / early planning /
  unentitled
- `market_study.supply.delivered_12mo_rooms`

### Pipeline projects (one row per listed project; `<n>` 1-indexed in report order)
- `under_construction.<n>.name`
- `under_construction.<n>.rooms`
- `under_construction.<n>.status` — one of `under_construction`,
  `final_planning`, `planned`
- `under_construction.<n>.expected_open` — ISO date or quarter as printed
- `under_construction.<n>.developer`
- `under_construction.total_rooms` — the report's under-construction total

When the report gives only the project list, the Market tab sums the
`rooms` of the rows whose `status` is not a planning stage.

### Supply & demand trend (`<YYYY>` calendar year; the trailing twelve months as `ttm`)
- `market_study.trend.<YYYY>.supply_rooms`
- `market_study.trend.<YYYY>.supply_change_pct`
- `market_study.trend.<YYYY>.demand_room_nights` — occupied room nights
- `market_study.trend.<YYYY>.demand_change_pct` — **Demand Growth** reads
  this (TTM first, then the latest actual year)
- `market_study.trend.<YYYY>.occupancy_pct`
- `market_study.trend.<YYYY>.occupancy_change_pct` — YoY occupancy change
  as a fraction (`-0.008` for -0.8%)
- `market_study.trend.<YYYY>.adr_usd`
- `market_study.trend.<YYYY>.adr_change_pct`
- `market_study.trend.<YYYY>.revpar_usd`
- `market_study.trend.<YYYY>.revpar_change_pct`
- `market_study.trend.<YYYY>.period` — `actual` or `forecast`. Forecast
  years are never shown as history; emit the tag for every year (see the
  YTD rule under "Performance by class").
- `market_study.trend.ttm.demand_change_pct`
- `market_study.trend.ttm.supply_change_pct`

When the report prints the demand series but no YoY column, emit the
series — the Market tab derives growth as latest ÷ prior − 1 and names
both rows.

### Performance by class (the appendix "<CLASS> PERFORMANCE" and "<CLASS> SUPPLY & DEMAND" tables)

A CoStar submarket report ends with an appendix that repeats the overall
supply & demand table and the overall performance table once per class
bucket. The future P&L applies the bucket that matches the deal's
positioning, so emit EVERY class table and EVERY year with these exact
paths. `<bucket>` is one of:

- `luxury_upper_upscale` — the tables titled "Luxury & Upper Upscale"
- `upscale_upper_midscale` — "Upscale & Upper Midscale"
- `midscale_economy` — "Midscale & Economy"

Performance table (columns: Occupancy percent and % change, ADR per room
and % change, RevPAR per room and % change):

- `market_study.class.<bucket>.<YYYY>.occupancy_pct` — 0..1 decimal
  (`0.681` for 68.1%)
- `market_study.class.<bucket>.<YYYY>.occupancy_change_pct` — the YoY
  change as a fraction with the sign as printed (`-0.012` for -1.2%)
- `market_study.class.<bucket>.<YYYY>.adr_usd`
- `market_study.class.<bucket>.<YYYY>.adr_change_pct`
- `market_study.class.<bucket>.<YYYY>.revpar_usd`
- `market_study.class.<bucket>.<YYYY>.revpar_change_pct`

Supply & demand table for the same bucket (its two "% Change" columns):

- `market_study.class.<bucket>.<YYYY>.supply_change_pct` — available rooms
- `market_study.class.<bucket>.<YYYY>.demand_change_pct` — occupied rooms

Period tag — MANDATORY, one row per bucket and year (and per year of the
overall `market_study.trend` series):

- `market_study.class.<bucket>.<YYYY>.period` — `actual` or `forecast`.
  The tables list years newest-first with a `YTD` row in the middle: every
  year ABOVE the YTD row (the current year's year-end projection and the
  years after it) is `forecast`; every year below it is `actual`. Never
  emit the YTD row as a year.

Forecast window (once per report):

- `market_study.forecast.first_year` — the first forecast calendar year
  (the year printed just above the YTD row)
- `market_study.forecast.last_year` — the last year printed

Class inventory (the overview's KEY INDICATORS table, when present):

- `market_study.class.<bucket>.rooms` — existing rooms in the bucket
  (`unit` `rooms`)

Rules for every class and trend row:

- `source_page` is the page the TABLE is printed on — the appendix pages,
  not the overview page that summarises them.
- A blank cell is omitted. Never emit `0` for a missing change or level.
- Every change column is a fraction; the overall table's columns go under
  `market_study.trend.<YYYY>.*` with the same leaf names.

### Demand drivers (when present)
- `market_study.demand_drivers.<n>.name`
- `market_study.demand_drivers.<n>.type` — e.g. `convention`, `airport`,
  `university`, `corporate`, `leisure`
- `market_study.demand_drivers.<n>.note`
