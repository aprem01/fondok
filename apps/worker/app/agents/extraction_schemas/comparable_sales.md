# Comparable Sales (Recent Hotel Transactions)

Pulled OUT of the OM as a structured sub-extraction so the Comp Sales
engine (Wave 3 W3.1) can derive an exit-cap-rate anchor with full
source-tagged provenance. OMs ship this table under various labels:
"Comparable Sales", "Recent Hotel Transactions", "Hotel Sales
Activity", "Market Trades". Treat all of them as the same schema.

5–15 historical sales is the typical range; 3 is the floor below which
the engine refuses to derive a weighted cap rate. Each row carries the
property name, location, sale date, sale price, room count, NOI, cap
rate, sale price per key, and brand / chain-scale.

USALI 11th edition: NOI on these comp tables is reported as the seller's
representation — broker-published, not GAAP-audited. The engine
preserves it for the table view but the derived cap rate comes off
``cap_rate_pct`` directly (don't try to back-solve cap from NOI/price
when the broker already published the cap; their cap is the trade
truth, NOI is the broker's repackaging).

## Canonical field-path namespace

`comparable_sales.<n>.*` where `<n>` is the comp's 1-based position in
the OM table. The Extractor agent numbers them in order of appearance.

### Per-comp fields

- `comparable_sales.<n>.property_name` — hotel name
- `comparable_sales.<n>.city` — city / submarket the comp sits in
- `comparable_sales.<n>.state` — 2-letter state code preferred
  (e.g. `TX`, `CA`)
- `comparable_sales.<n>.sale_date` — ISO `YYYY-MM-DD` when published
- `comparable_sales.<n>.keys` — guest-room count (positive integer)
- `comparable_sales.<n>.sale_price_usd` — total transaction $ (whole
  dollars, no commas)
- `comparable_sales.<n>.sale_price_per_key_usd` — broker's $/key.
  Sometimes the only price representation published.
- `comparable_sales.<n>.noi_usd` — broker-published NOI at sale.
  Often missing — extract only when explicitly labeled.
- `comparable_sales.<n>.cap_rate_pct` — going-in cap rate as 0..100
  percent (e.g. `7.25` for 7.25%). This is the critical column —
  median + weighted derivation both key off it.
- `comparable_sales.<n>.chain_scale` — STR chain-scale label
  (`luxury`, `upper-upscale`, `upscale`, `upper-midscale`, `midscale`,
  `economy`, `independent`). Normalize to lowercase with dashes.
- `comparable_sales.<n>.brand_family` — parent brand (`Marriott`,
  `Hilton`, `Hyatt`, `IHG`, `Independent`, etc.).
- `comparable_sales.<n>.flag` — specific flag (`Courtyard by Marriott`,
  `Hilton Garden Inn`, `Hampton Inn`, etc.). Distinct from
  `brand_family` because a single family has multiple flags.
- `comparable_sales.<n>.note` — any qualifying broker commentary
  ("portfolio sale", "off-market", "redevelopment basis", etc.).

## Tie-breaker rules

- If the OM publishes both a "Comparable Sales" table and a smaller
  "Recent Trades" callout, extract the LARGER set under
  `comparable_sales.*` and treat the callout as redundant.
- Cap rate is published as a percent (e.g. `7.25%`); emit as `7.25`,
  not `0.0725`. The engine converts to a fraction only when wiring the
  derived value to `exit_cap_rate` at the engine_runner boundary.
- When the broker lists sale price in millions (`$42.5M`), expand to
  the underlying integer dollar amount (`42500000`).
- When a row is the subject property itself (broker sometimes puts it
  in the table for context with "Subject" label), skip it — comps are
  *other* hotels.

## Example rows

```
comparable_sales.1.property_name = "Marriott Memphis Downtown"
comparable_sales.1.city = "Memphis"
comparable_sales.1.state = "TN"
comparable_sales.1.sale_date = "2024-08-15"
comparable_sales.1.keys = 600
comparable_sales.1.sale_price_usd = 96000000
comparable_sales.1.sale_price_per_key_usd = 160000
comparable_sales.1.noi_usd = 7200000
comparable_sales.1.cap_rate_pct = 7.5
comparable_sales.1.chain_scale = "upper-upscale"
comparable_sales.1.brand_family = "Marriott"
comparable_sales.1.flag = "Marriott"

comparable_sales.2.property_name = "Hyatt Regency Houston Galleria"
comparable_sales.2.city = "Houston"
comparable_sales.2.state = "TX"
comparable_sales.2.sale_date = "2025-02-03"
comparable_sales.2.keys = 325
comparable_sales.2.sale_price_usd = 78000000
comparable_sales.2.sale_price_per_key_usd = 240000
comparable_sales.2.cap_rate_pct = 6.85
comparable_sales.2.chain_scale = "upper-upscale"
comparable_sales.2.brand_family = "Hyatt"
comparable_sales.2.flag = "Hyatt Regency"

comparable_sales.3.property_name = "Hilton Austin"
comparable_sales.3.city = "Austin"
comparable_sales.3.state = "TX"
comparable_sales.3.sale_date = "2023-11-30"
comparable_sales.3.keys = 800
comparable_sales.3.sale_price_usd = 175000000
comparable_sales.3.sale_price_per_key_usd = 218750
comparable_sales.3.noi_usd = 12250000
comparable_sales.3.cap_rate_pct = 7.0
comparable_sales.3.chain_scale = "upper-upscale"
comparable_sales.3.brand_family = "Hilton"
comparable_sales.3.flag = "Hilton"

comparable_sales.4.property_name = "Sheraton Dallas Downtown"
comparable_sales.4.city = "Dallas"
comparable_sales.4.state = "TX"
comparable_sales.4.sale_date = "2024-04-10"
comparable_sales.4.keys = 1840
comparable_sales.4.sale_price_usd = 220000000
comparable_sales.4.sale_price_per_key_usd = 119565
comparable_sales.4.cap_rate_pct = 8.1
comparable_sales.4.chain_scale = "upper-upscale"
comparable_sales.4.brand_family = "Marriott"
comparable_sales.4.flag = "Sheraton"

comparable_sales.5.property_name = "The Whitley, A Luxury Collection Hotel"
comparable_sales.5.city = "Atlanta"
comparable_sales.5.state = "GA"
comparable_sales.5.sale_date = "2025-06-22"
comparable_sales.5.keys = 507
comparable_sales.5.sale_price_usd = 142000000
comparable_sales.5.sale_price_per_key_usd = 280079
comparable_sales.5.cap_rate_pct = 6.5
comparable_sales.5.chain_scale = "luxury"
comparable_sales.5.brand_family = "Marriott"
comparable_sales.5.flag = "Luxury Collection"

comparable_sales.6.property_name = "Renaissance Phoenix Downtown"
comparable_sales.6.city = "Phoenix"
comparable_sales.6.state = "AZ"
comparable_sales.6.sale_date = "2022-09-14"
comparable_sales.6.keys = 532
comparable_sales.6.sale_price_usd = 87000000
comparable_sales.6.sale_price_per_key_usd = 163533
comparable_sales.6.cap_rate_pct = 7.85
comparable_sales.6.chain_scale = "upper-upscale"
comparable_sales.6.brand_family = "Marriott"
comparable_sales.6.flag = "Renaissance"
```

## Relationship to legacy `transaction_comps.*`

The OM extractor today still emits `transaction_comps.<n>.cap_rate_pct`
(see `om.md`) — that legacy path stays in place for backward compat.
The Comp Sales engine reads BOTH path families and reconciles by
preferring the richer `comparable_sales.*` namespace (more fields per
comp) when a single comp is published under both.
