# OM (Offering Memorandum)

The broker's sales teaser for a hotel deal. Carries: a Year-1 proforma
(NOI, occupancy, ADR, revenue, expense), asking price, property
overview (keys, brand, year built, address, GBA, submarket), in-place
debt block, market overview, and almost always a "Comparable Sales"
table of recent hotel transactions.

## Canonical field-path namespaces

### `broker_proforma.<line>` — Year-1 broker projections
- `broker_proforma.noi_usd`
- `broker_proforma.rooms_revenue_usd`
- `broker_proforma.occupancy_pct`
- `broker_proforma.adr_usd`
- `broker_proforma.renovation_budget_usd` (the broker's published PIP
  / capex budget)
- `broker_proforma.entry_cap_rate` / `broker_proforma.cap_rate`

Year-vintage numbers can co-exist:
- `broker_proforma.noi_year_1_usd`
- `broker_proforma.noi_stabilized_usd`

### `ttm_summary_per_om.<line>` — T-12 / TTM historical figures the OM cites
The broker labels these as actual. Same line items as broker_proforma.

### `asking_price.*`
- `asking_price.headline_price_usd` — the OM's published asking price
- `asking_price.price_per_key_usd` — broker's $/key

### `property_overview.*` — property metadata
- `property_overview.keys` — guest room count
- `property_overview.brand` — flag (e.g. "Marriott", "Hilton")
- `property_overview.year_built`
- `property_overview.year_renovated`
- `property_overview.address`
- `property_overview.gba_sf` — gross building area in square feet
- `property_overview.meeting_space_sf`
- `property_overview.parking_spaces`
- `property_overview.fb_outlets`
- `property_overview.property_type` — service level / segment
- `property_overview.submarket`

### `in_place_debt.*` — broker's quote of the seller's existing financing
- `in_place_debt.loan_balance_usd`
- `in_place_debt.interest_rate_pct`
- `in_place_debt.amortization_years`
- `in_place_debt.term_years`
- `in_place_debt.ltv_pct`
- `in_place_debt.maturity_date` — ISO date when known

### `market_overview_per_om.*` — broker's market commentary
- `market_overview_per_om.compset_revpar_usd`
- `market_overview_per_om.compset_occupancy_pct`
- (any other comp-set headline number the OM cites)

### `transaction_comps.<n>.*` — Comparable Sales table
Most OMs ship a "Comparable Sales" table with 3-7 recent hotel sales.
Number them `1`..`N` in the order they appear. This is critical
coverage — the median cap rate from these comps anchors the analyst's
exit-cap conversation. For each comp:

- `transaction_comps.<n>.name` — hotel name
- `transaction_comps.<n>.market` — city / submarket
- `transaction_comps.<n>.sale_date` — ISO date if known
- `transaction_comps.<n>.keys` — room count
- `transaction_comps.<n>.sale_price_usd` — total transaction $
- `transaction_comps.<n>.price_per_key_usd` — $/key
- `transaction_comps.<n>.cap_rate_pct` — going-in cap rate
- `transaction_comps.<n>.buyer_name`
- `transaction_comps.<n>.buyer_type` — one of: REIT, PE Fund,
  Institutional, Private, Owner Operator, Sovereign Wealth,
  Family Office, Other.

## Tie-breaker rules

If a number could be either broker-projected or historical and the
source doesn't clearly label it, prefer `broker_proforma.*` (the
broker pitched it; treat it as forward-looking unless proven actual).
