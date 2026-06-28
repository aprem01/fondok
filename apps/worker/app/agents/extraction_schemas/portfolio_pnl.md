# PORTFOLIO_PNL (Analyst's in-house portfolio P&L benchmark)

A PORTFOLIO_PNL document is the analyst's firm-internal peer-set
roll-up: aggregated USALI line ratios across the hotels the firm
already operates, rolled up by chain scale and/or market. These
ratios are MORE credible than the generic ``PNL_BENCHMARK``
(HostStats-style) and ``CBRE_HORIZONS`` benchmark for the same
chain scale because the analyst owns the underlying P&Ls and knows
the data is clean.

Sam's June 2026 ask: "Wants op-ratios extracted from CBRE/in-house
portfolio P&Ls (not HOST defaults)." This schema is what the
Extractor uses to drag those ratios out of the firm's portfolio
roll-up so the precedence resolver (op_ratio_precedence.py) can
prefer them over HostStats / CBRE for op-ratio assignments.

## Headers

- `portfolio_pnl.firm_name` — analyst firm name (e.g. `"Sam Capital Partners"`)
- `portfolio_pnl.asset_count` — number of hotels in the roll-up (≥ 1)
- `portfolio_pnl.total_rooms_modeled` — sum of keys across the portfolio
- `portfolio_pnl.vintage_year` — fiscal year the roll-up covers (e.g. `2024`)
- `portfolio_pnl.chain_scales_covered` — list of chain scales represented
  (e.g. `"Upper Upscale, Upscale, Upper Midscale"`). Emit as a comma-
  separated string in a single field — the resolver splits on commas.
- `portfolio_pnl.markets_covered` — optional comma-separated market list
  (e.g. `"Atlanta, Nashville, Charlotte"`).

## Per-chain-scale segment headers

When the roll-up breaks ratios out per chain scale, emit
``portfolio_pnl.segment_<slug>.*`` instead of ``portfolio_pnl.*``.
``<slug>`` is the chain-scale name lowercased with underscores
(e.g. ``upper_upscale``, ``upscale``, ``upper_midscale``).

The precedence resolver MATCHES the subject deal's chain scale to a
segment when present; it falls back to the top-level ``portfolio_pnl.*``
roll-up when no per-segment match exists.

## Expense ratios (the core P2.7 payload)

Emit as 0..1 decimals. Each ratio is the line's share of the
applicable revenue base — departmental lines as a % of their own
departmental revenue, undistributed / fixed lines as a % of total
revenue.

- `portfolio_pnl.rooms_dept_pct` — Rooms Department Expense / Rooms Revenue
- `portfolio_pnl.fb_dept_pct` — F&B Department Expense / F&B Revenue
- `portfolio_pnl.other_ops_dept_pct` — Other Operated Departments Expense / Other Revenue
- `portfolio_pnl.admin_pct` — Administrative & General / Total Revenue
- `portfolio_pnl.sales_pct` — Sales & Marketing / Total Revenue
- `portfolio_pnl.prop_ops_pct` — Property Operations & Maintenance / Total Revenue
- `portfolio_pnl.utilities_pct` — Utilities / Total Revenue
- `portfolio_pnl.marketing_pct` — Marketing (when separately reported) / Total Revenue
- `portfolio_pnl.management_fee_pct` — Management Fee / Total Revenue
- `portfolio_pnl.property_tax_pct` — Property Tax / Total Revenue
- `portfolio_pnl.insurance_pct` — Insurance / Total Revenue
- `portfolio_pnl.ffe_reserve_pct` — FF&E Reserve / Total Revenue (when reported)
- `portfolio_pnl.gop_margin` — GOP / Total Revenue
- `portfolio_pnl.noi_margin` — NOI / Total Revenue

## Revenue mix benchmarks

- `portfolio_pnl.rooms_revenue_pct` — Rooms Revenue / Total Revenue
- `portfolio_pnl.fb_revenue_pct` — F&B Revenue / Total Revenue
- `portfolio_pnl.other_revenue_pct` — Other Revenue / Total Revenue

## Per-chain-scale variant

Emit any of the above on the segmented path when the roll-up provides
per-chain-scale ratios:

- `portfolio_pnl.segment_<slug>.rooms_dept_pct`
- `portfolio_pnl.segment_<slug>.admin_pct`
- `portfolio_pnl.segment_<slug>.utilities_pct`
- (etc. for every ratio above)

Only emit chain-scale-segmented values when the report makes them
explicit. Otherwise emit the top-level ``portfolio_pnl.<ratio>``
roll-up and let the resolver pick.

## Notes

- Always emit ratios as 0..1 decimals. ``0.28`` (NOT ``28``).
- Skip lines the report doesn't cover — partial coverage is fine;
  the resolver falls through to the next-lower tier (CBRE → HOST →
  seed) for missing ratios.
- The Router emits PORTFOLIO_PNL when the filename contains "portfolio",
  "in-house benchmark", "peer set roll-up", or when the content sample
  describes an aggregated multi-hotel P&L without a Subject column.
