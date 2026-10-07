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
Growth** (FON-61 E-008). Percentages are 0..1 decimals; room counts carry
`unit` `rooms`.

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
- `market_study.trend.<YYYY>.adr_usd`
- `market_study.trend.<YYYY>.revpar_usd`
- `market_study.trend.<YYYY>.revpar_change_pct`
- `market_study.trend.<YYYY>.period` — `actual` or `forecast`. Forecast
  years are never shown as history; emit the tag for every forecast row.
- `market_study.trend.ttm.demand_change_pct`
- `market_study.trend.ttm.supply_change_pct`

When the report prints the demand series but no YoY column, emit the
series — the Market tab derives growth as latest ÷ prior − 1 and names
both rows.

### Demand drivers (when present)
- `market_study.demand_drivers.<n>.name`
- `market_study.demand_drivers.<n>.type` — e.g. `convention`, `airport`,
  `university`, `corporate`, `leisure`
- `market_study.demand_drivers.<n>.note`
