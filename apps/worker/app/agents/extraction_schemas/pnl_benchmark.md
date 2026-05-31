# PNL_BENCHMARK (CBRE Benchmarker / HotStats USALI 11th P&L)

Real CBRE Benchmarker reports contain a Subject Property column AND
a Comparative Set average column for EVERY USALI line, plus $PAR
(per available room/year) and $POR (per occupied room/day). Emit
values for both columns when present so the variance reader can
compute Subject vs Peer deltas.

## Headers
- `pnl_benchmark.peer_set_size` — number of hotels in the comp set
- `pnl_benchmark.peer_set_avg_keys` — avg rooms in the comp set
- `pnl_benchmark.peer_set_avg_occupancy_pct`
- `pnl_benchmark.peer_set_avg_adr_usd`
- `pnl_benchmark.peer_set_avg_revpar_usd`
- `pnl_benchmark.subject_keys`
- `pnl_benchmark.subject_occupancy_pct`
- `pnl_benchmark.subject_adr_usd`
- `pnl_benchmark.subject_revpar_usd`

## Per-line USALI breakdown

`<column>` ∈ `{peer, subject}`. `<line>` is the USALI line slug.
Emit ALL four metrics for every USALI line that appears (Total $,
Ratio-to-Revenue, $PAR, $POR):

- `pnl_benchmark.<column>.<line>.total_usd`
- `pnl_benchmark.<column>.<line>.ratio_pct`
- `pnl_benchmark.<column>.<line>.par_usd`
- `pnl_benchmark.<column>.<line>.por_usd`

USALI line slugs (use these exact keys):

```
rooms_revenue, fb_revenue, other_operated_revenue, misc_revenue,
total_revenue, rooms_dept_expense, fb_dept_expense,
other_operated_expense, total_dept_expense, total_dept_profit,
a_and_g, it, sales_marketing, maintenance, utilities,
total_undistributed, gop, mgmt_fee, income_before_non_operating,
rent, property_taxes, insurance, other_non_op, total_non_operating,
ebitda
```

## F&B sub-classification (USALI 11th)

Restaurant venues vs room service vs mini-bar vs banquet; food
separate from beverage. One row per column per channel.

- `pnl_benchmark.<column>.fb_revenue.food_venues_usd`
- `pnl_benchmark.<column>.fb_revenue.food_room_service_usd`
- `pnl_benchmark.<column>.fb_revenue.food_mini_bar_usd`
- `pnl_benchmark.<column>.fb_revenue.food_banquet_usd`
- `pnl_benchmark.<column>.fb_revenue.beverage_venues_usd`
- `pnl_benchmark.<column>.fb_revenue.beverage_banquet_usd`
- `pnl_benchmark.<column>.fb_cost.cost_of_food_sales_usd`
- `pnl_benchmark.<column>.fb_cost.cost_of_beverage_sales_usd`

## Utilities sub-classification

- `pnl_benchmark.<column>.utilities.electricity_usd`
- `pnl_benchmark.<column>.utilities.water_sewer_usd`
- `pnl_benchmark.<column>.utilities.steam_usd`
- `pnl_benchmark.<column>.utilities.gas_fuel_usd`
- `pnl_benchmark.<column>.utilities.other_usd`

## Labor by department (USALI 11th breakdown)

Salaries (management / non-management), service-charge distribution,
contract labor, bonuses, payroll-related expenses.

- `<dept>` slug ∈ `{rooms, fb, a_and_g, it, sales_marketing, maintenance}`
- `<line>` ∈ `{salaries_management, salaries_non_management,
  service_charge_distribution, contract_labor, bonuses_incentives,
  unassigned_salaries, payroll_related}`

Emit:

- `pnl_benchmark.<column>.labor.<dept>.<line>_usd`
- `pnl_benchmark.<column>.labor.<dept>.<line>_par`
- `pnl_benchmark.<column>.labor.<dept>.<line>_por`

## Legacy aliases (backwards compat)

Peer-set margins as decimal 0..1:

- `pnl_benchmark.rooms_dept_pct`
- `pnl_benchmark.fb_dept_margin`
- `pnl_benchmark.gop_margin`
- `pnl_benchmark.a_and_g_pct`
- `pnl_benchmark.sales_marketing_pct`
- `pnl_benchmark.utilities_pct`
- `pnl_benchmark.property_taxes_pct`
- `pnl_benchmark.insurance_pct`
- `pnl_benchmark.rooms_revenue_par`
- `pnl_benchmark.total_revenue_par`
- `pnl_benchmark.noi_par`
- `pnl_benchmark.rooms_revenue_por`
- `pnl_benchmark.fb_revenue_por`
