# STR_TREND (STR / CoStar Trend Report)

Multi-tab Excel workbook covering the subject property's monthly Occ /
ADR / RevPAR / Supply / Demand history, plus the comp set. Distinct
from the legacy single-page `STR` benchmark: the trend report fans
out across the comp set AND across time, and has tabs named "By
Measure" / "Classic" / "Day of Week" / "Custom Trend".

## Canonical field-path namespace

### Subject hotel identity + TTM rollup
- `ttm_performance.subject.name` — full subject hotel name (extract
  from the "Custom Trend: <name>" header line at the top of the file).
- `ttm_performance.subject.occupancy_pct`
- `ttm_performance.subject.adr_usd`
- `ttm_performance.subject.revpar_usd`

### Subject monthly history (most-recent 12 months on the "By Measure" or "Classic" tab)
Key by year + month: `<YYYY_MM>`, e.g. `2025_03`.

- `ttm_performance.subject.monthly.<YYYY_MM>.occupancy_pct`
- `ttm_performance.subject.monthly.<YYYY_MM>.adr_usd`
- `ttm_performance.subject.monthly.<YYYY_MM>.revpar_usd`
- `ttm_performance.subject.monthly.<YYYY_MM>.supply_rooms`
- `ttm_performance.subject.monthly.<YYYY_MM>.demand_rooms`

### Annual roll-ups ("Total Year" rows on the By Measure tab)
- `ttm_performance.subject.annual.<YYYY>.occupancy_pct`
- `ttm_performance.subject.annual.<YYYY>.adr_usd`
- `ttm_performance.subject.annual.<YYYY>.revpar_usd`

### Day-of-week breakdown (Day of Week tab — Mon..Sun)
`<dow>` ∈ `{mon, tue, wed, thu, fri, sat, sun}`.

- `ttm_performance.subject.day_of_week.<dow>.occupancy_pct`
- `ttm_performance.subject.day_of_week.<dow>.adr_usd`
- `ttm_performance.subject.day_of_week.<dow>.revpar_usd`

### Comp set (per-competitor)
Number them `1`..`7` in the order they appear in the report.

- `ttm_performance.compset.<n>.name`
- `ttm_performance.compset.<n>.keys`
- `ttm_performance.compset.<n>.occupancy_pct`
- `ttm_performance.compset.<n>.adr_usd`
- `ttm_performance.compset.<n>.revpar_usd`

### Penetration indices (subject vs comp set; 1.00 = parity)
- `ttm_performance.indices.rgi_revpar_index`
- `ttm_performance.indices.ari_adr_index`
- `ttm_performance.indices.mpi_occupancy_index`

### Comp-set rollups
- `comp_set.comp_set_size`
- `comp_set.total_keys`
