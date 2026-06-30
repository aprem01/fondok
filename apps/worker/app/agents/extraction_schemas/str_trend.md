# STR_TREND (STR / CoStar Trend Report)

Multi-tab Excel workbook covering the subject property's monthly Occ /
ADR / RevPAR / Supply / Demand history, plus the comp set. Distinct
from the legacy single-page `STR` benchmark: the trend report fans
out across the comp set AND across time, and has tabs named "By
Measure" / "Classic" / "Day of Week" / "Custom Trend".

## Canonical field-path namespace

### Document-level metadata
- `str_trend.report_year` — integer year the STR report covers.
  Extract from the report date / period-ending field on the cover
  page or "By Measure" header. When the report covers a TTM that
  spans two calendar years, use the year of the period_ending date
  (e.g. TTM ending Mar-2025 → `2025`). Required for multi-year
  comp-set drift detection (Wave 1 item #8): the drift service
  sorts STR_TREND extractions by this field so consecutive-year
  diffs can surface "Hilton South Beach replaced by W South Beach"
  side-notes on the Market tab.

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

The authoritative property roster — names + room counts — lives on the
**Response** tab (Tab 22 / "Response Report"). That tab has a "Monthly
Data" and "Segmentation Data" block with columns `STR#`, `Name`,
`City, State`, `Zip`, `Phone`, `Rooms`. The row whose `STR#` matches
the subject property is the subject itself; skip it. The remaining
rows are the named competitors — extract `Name` and `Rooms` for each.
The Summary / Glance tabs aggregate the comp-set but do not list the
underlying properties, so the Response tab is the only ground-truth
source for `keys`.

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
