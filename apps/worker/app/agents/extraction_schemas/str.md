# STR (Smith Travel benchmark report — legacy single-tab format)

A short benchmark report comparing the subject hotel to its comp set
on a single page. Distinct from the multi-tab CoStar Trend Report
(see `str_trend.md`).

## Canonical field-path namespace

### Subject TTM rollup
- `ttm_performance.subject.occupancy_pct`
- `ttm_performance.subject.adr_usd`
- `ttm_performance.subject.revpar_usd`

### Penetration indices (subject vs comp set; 1.00 = parity)
- `ttm_performance.indices.rgi_revpar_index`
- `ttm_performance.indices.ari_adr_index`
- `ttm_performance.indices.mpi_occupancy_index`

### Comp set
- `comp_set.comp_set_size`
- `comp_set.total_keys`
