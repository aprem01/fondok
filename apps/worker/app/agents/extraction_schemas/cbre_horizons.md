# CBRE_HORIZONS (CBRE Hotel Horizons forward forecast)

Real CBRE reports carry FOUR forecast tables (All Hotels + three
price tiers — Upper-Priced / Mid-Priced / Lower-Priced), Guest-Paid
ADR, source-of-business mix, length of stay, and short-term-rental
supply via an AirDNA addendum.

Emit every grounded field — the forward projection engine picks the
right segment based on the deal's positioning tier.

## Headers (MANDATORY)

- `cbre_horizons.market` — metro area (e.g. `"Seattle, WA"`)
- `cbre_horizons.submarket` — submarket name when present
- `cbre_horizons.chain_scale` — chain scale segment (e.g. `"Upper Upscale"`)
- `cbre_horizons.publication_date` — quarter + year (e.g. `"Q3 2024"`)
  or ISO date

## Annual forecast by segment

`<scope>` ∈ `{all, upper_priced, mid_priced, lower_priced}`.
`<n>` is the calendar year (e.g. `2024`, `2025`, `2028`).

Emit every row that appears in the source — historical AND forecast.
Mark forecast years by setting `period` to `forecast`; actuals get
`actual`.

- `cbre_horizons.segment_<scope>.<n>.occupancy_pct`
- `cbre_horizons.segment_<scope>.<n>.occupancy_change_pct`
- `cbre_horizons.segment_<scope>.<n>.adr_usd`
- `cbre_horizons.segment_<scope>.<n>.adr_change_pct`
- `cbre_horizons.segment_<scope>.<n>.revpar_usd`
- `cbre_horizons.segment_<scope>.<n>.revpar_change_pct`
- `cbre_horizons.segment_<scope>.<n>.supply_change_pct`
- `cbre_horizons.segment_<scope>.<n>.demand_change_pct`
- `cbre_horizons.segment_<scope>.<n>.period` — `actual` or `forecast`

For backwards compatibility, the All-Hotels segment may also be
emitted on the legacy `cbre_horizons.year_<i>.*` paths where `<i>` is
the 1-indexed forecast year (Year-1 of the forecast, regardless of
calendar year).

## Long-run averages
The next-4-quarters anchor block — printed near the top as e.g.
"Occupancy: 67.4%, ADR Change: 2.7%, RevPAR Change: 5.8%".

- `cbre_horizons.long_run_avg.occupancy_pct`
- `cbre_horizons.long_run_avg.adr_change_pct`
- `cbre_horizons.long_run_avg.revpar_change_pct`
- `cbre_horizons.long_run_avg.supply_change_pct`
- `cbre_horizons.long_run_avg.demand_change_pct`

## Guest-Paid ADR
Net of distribution costs; separate from advertised ADR. One row per
scope per year.

- `cbre_horizons.guest_paid_adr.<scope>.<n>.adr_usd`
- `cbre_horizons.guest_paid_adr.<scope>.<n>.change_pct`

## Source-of-business mix
Channel slugs: `brand_com`, `property_direct`, `voice`,
`internal_discounts`, `gds`, `fit_wholesale`, `ota`, `group`. Emit
room-night share + ADR, ideally for the most recent year and the
prior year.

- `cbre_horizons.source_mix.<scope>.<channel>.room_nights_pct_<YYYY>`
- `cbre_horizons.source_mix.<scope>.<channel>.adr_usd_<YYYY>`

## Length of Stay (nights)
- `cbre_horizons.length_of_stay.<scope>.nights_<YYYY>`
- `cbre_horizons.length_of_stay.<scope>.nights_<YYYY>_ytd`

## AirDNA short-term rental supply
When the report carries the AirDNA addendum:

- `cbre_horizons.short_term_rental.active_units`
- `cbre_horizons.short_term_rental.available_supply`
- `cbre_horizons.short_term_rental.units_sold`
- `cbre_horizons.short_term_rental.total_revenue_usd`
- `cbre_horizons.short_term_rental.adr_usd`
- `cbre_horizons.short_term_rental.revpar_usd`
- `cbre_horizons.short_term_rental.occupancy_pct`
- `cbre_horizons.short_term_rental.units_sold_change_pct`
