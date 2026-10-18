/**
 * Map internal extraction schema paths to analyst-facing business labels.
 *
 * FON-21: the extraction review surfaced raw object paths like
 * ``p_and_l_usali.rooms.total_rooms_sold_annual`` or
 * ``ttm_summary_per_om.occupancy_pct``. Analysts think in business concepts
 * (Rooms Revenue, ADR, Occupancy, NOI), not backend field keys. This turns a
 * dotted schema path into a human label; the raw path stays available for a
 * "show internal field" affordance / tooltip when debugging.
 */

// Exact leaf-token → label. Matched against the last meaningful segment of
// the dotted path (most specific wins). Keep keys lowercase.
const LEAF_LABELS: Record<string, string> = {
  rooms_revenue: 'Rooms Revenue',
  total_revenue: 'Total Revenue',
  fb_revenue: 'F&B Revenue',
  food_beverage_revenue: 'F&B Revenue',
  other_revenue: 'Other Revenue',
  resort_fees: 'Resort Fees',
  occupancy_pct: 'Occupancy',
  occupancy: 'Occupancy',
  adr: 'ADR',
  revpar: 'RevPAR',
  // Two distinct USALI bases — never the same string (FON-59 #1 / FON-67 #2).
  // Registry: `noi` = concept `noi`, `noi_institutional` = concept `ebitda`.
  noi: 'Cash NOI (after FF&E reserve)',
  noi_institutional: 'NOI (before FF&E reserve)',
  gop: 'Gross Operating Profit',
  total_rooms_sold_annual: 'Rooms Sold',
  rooms_sold: 'Rooms Sold',
  available_rooms_annual: 'Available Rooms',
  available_rooms: 'Available Rooms',
  rooms_available: 'Available Rooms',
  fb_expense: 'F&B Expense',
  rooms_expense: 'Rooms Expense',
  mgmt_fee: 'Management Fee',
  management_fee: 'Management Fee',
  ffe_reserve: 'FF&E Reserve',
  property_taxes: 'Property Taxes',
  insurance: 'Insurance',
  utilities: 'Utilities',
  sales_marketing: 'Sales & Marketing',
  admin_general: 'Administrative & General',
  administrative_general: 'Administrative & General',
  repairs_maintenance: 'Repairs & Maintenance',
  total_expense: 'Total Expenses',
  departmental_expenses: 'Departmental Expenses',
  undistributed_expenses: 'Undistributed Expenses',
  fixed_charges: 'Fixed Charges',
  period_type: 'Period Type',
  fiscal_year: 'Fiscal Year',
  property_name: 'Property Name',
  key_count: 'Keys',
  keys: 'Keys',
  // Partnership / JV terms (FON-66). ``_pct`` is stripped before this
  // lookup, so key the equity shares on the stripped stem too.
  gp_equity_pct: 'GP / Sponsor Ownership',
  gp_equity: 'GP / Sponsor Ownership',
  lp_equity_pct: 'LP / Investor Ownership',
  lp_equity: 'LP / Investor Ownership',
  pref_rate: 'Preferred Return',
  hurdle_rate: 'IRR Hurdle',
  gp_split: 'GP Split',
  lp_split: 'LP Split',
};

// Word-level acronym / casing fixes for the generic fallback. Empty string
// drops the token (units that add no meaning to a label).
const ACRONYMS: Record<string, string> = {
  adr: 'ADR', noi: 'NOI', revpar: 'RevPAR', gop: 'GOP', ttm: 'TTM',
  om: 'OM', usali: 'USALI', fb: 'F&B', ffe: 'FF&E', str: 'STR',
  capex: 'CapEx', y1: 'Y1', y2: 'Y2', ytd: 'YTD', pct: '', usd: '',
};

const STRIP_SUFFIXES = ['_annual', '_monthly', '_ytd', '_usd', '_pct', '_per_om'];

function humanizeWord(w: string): string {
  const lower = w.toLowerCase();
  if (lower in ACRONYMS) return ACRONYMS[lower];
  return w.charAt(0).toUpperCase() + w.slice(1).toLowerCase();
}

/** Turn an internal field path into an analyst-facing label. */
export function humanizeFieldName(raw: string): string {
  if (!raw) return '';
  const segs = raw.split('.').filter(Boolean);
  const leaf = (segs[segs.length - 1] || raw).toLowerCase();

  // 1. Exact leaf match.
  if (LEAF_LABELS[leaf]) return LEAF_LABELS[leaf];

  // 2. Leaf with a trailing unit/period suffix stripped.
  let base = leaf;
  for (const suf of STRIP_SUFFIXES) {
    if (base.endsWith(suf)) {
      base = base.slice(0, -suf.length);
      break;
    }
  }
  if (LEAF_LABELS[base]) return LEAF_LABELS[base];

  // 3. Generic aggregation / department leaf → borrow the parent segment for
  //    context so a bare ``…undistributed.total`` reads "Total Undistributed"
  //    (not an ambiguous "Total") and ``…rooms.revenue_usd`` reads "Rooms
  //    Revenue". Aggregation words lead the parent; department words trail it.
  if (segs.length >= 2) {
    const parent = segs[segs.length - 2].toLowerCase();
    const parentLabel = LEAF_LABELS[parent] ?? humanizeWord(parent);
    if (base === 'total' || base === 'subtotal' || base === 'net' || base === 'gross') {
      return `${humanizeWord(base)} ${parentLabel}`.trim();
    }
    const DEPT: Record<string, string> = {
      revenue: 'Revenue', expense: 'Expense', expenses: 'Expenses',
      profit: 'Profit', income: 'Income',
    };
    if (DEPT[base]) return `${parentLabel} ${DEPT[base]}`.trim();
  }

  // 4. Fallback: split into words, expand acronyms, title-case.
  const words = base.split(/[_\s]+/).map(humanizeWord).filter(Boolean);
  return words.join(' ').trim() || raw;
}

// ─────────────────────────────────────────────────────────────────────────
// Engine output blocks → the product surface a reader recognises.
//
// The worker reports a stale run as ``missing_blocks: { engine: [path, …] }``
// (dotted engine paths). A client never sees those paths: every block maps
// to the tab that shows it, and — where it helps — to the section name.
// ─────────────────────────────────────────────────────────────────────────

/** Engine name → the product tab that renders its output. */
export const ENGINE_TAB_LABEL: Record<string, string> = {
  revenue: 'P&L',
  expense: 'P&L',
  fb: 'P&L',
  capital: 'CAPEX',
  debt: 'Financing',
  returns: 'Returns',
  partnership: 'Partnership',
  sensitivity: 'Scenario Analysis',
  cashflow: 'Cash Flow',
  cash_flow: 'Cash Flow',
};

/** Engine name → the model's human name ("Revenue model"). */
export const ENGINE_MODEL_LABEL: Record<string, string> = {
  revenue: 'Revenue',
  expense: 'Expense / NOI',
  fb: 'F&B revenue',
  capital: 'Sources & Uses',
  debt: 'Debt',
  returns: 'Returns',
  partnership: 'Partnership waterfall',
  sensitivity: 'Sensitivity',
  cashflow: 'Cash flow',
  cash_flow: 'Cash flow',
};

/**
 * Dotted ``engine.block`` → the section heading a reader recognises. Every
 * top-level block the engines publish today is named; a nested path falls
 * back to its parent block, then to ``<Engine> model``. Never a dotted path.
 */
export const ENGINE_SECTION_LABEL: Record<string, string> = {
  // revenue
  'revenue.years': 'Future P&L',
  'revenue.projection_start_year': 'Projection calendar',
  'revenue.projection_calendar_years': 'Projection calendar',
  'revenue.renovation_displacement_by_year': 'Renovation displacement',
  'revenue.index_drivers_by_year': 'Index Analysis',
  'revenue.assumption_sources': 'Assumption sources',
  'revenue.inputs': 'Revenue assumptions',
  // F&B
  'fb.years': 'F&B revenue',
  'fb.provenance': 'F&B revenue sources',
  // expense
  'expense.years': 'Future P&L',
  'expense.stabilization': 'Stabilization',
  'expense.line_methods': 'Expense methods',
  'expense.benchmark': 'Benchmark column',
  'expense.inputs': 'Expense assumptions',
  // capital
  'capital.sources': 'Sources & Uses',
  'capital.uses': 'Sources & Uses',
  'capital.renovation_breakdown': 'Renovation / CapEx',
  'capital.renovation_base_usd': 'Renovation budget',
  'capital.renovation_budget_source': 'Renovation budget sources',
  'capital.senior_loan_fee_usd': 'Senior loan origination fee',
  'capital.equity_amount': 'Equity',
  'capital.total_capital_usd': 'Total capitalization',
  'capital.purchase_price': 'Purchase price',
  // debt
  'debt.debt_stack': 'Capital structure',
  'debt.schedule': 'Debt schedule',
  'debt.monthly_schedule': 'Debt schedule',
  'debt.refi_fee_pct': 'Refinancing',
  'debt.refi_debt_yield_min': 'Refinancing',
  'debt.refi': 'Refinancing',
  'debt.covenants': 'Covenants',
  'debt.stabilized_dscr': 'Stabilized DSCR',
  'debt.stabilized_debt_yield': 'Stabilized debt yield',
  'debt.dscr_by_year': 'DSCR by year',
  'debt.negative_noi_years': 'Debt coverage warnings',
  // returns
  'returns.levered_monthly': 'Monthly cash flows',
  'returns.cash_flows': 'Cash flows to equity',
  'returns.noi_by_year': 'NOI by year',
  'returns.exit_noi_basis': 'Exit NOI basis',
  'returns.exit_noi_period_label': 'Exit NOI basis',
  'returns.terminal_noi_usd': 'Exit NOI',
  'returns.gross_sale_price': 'Exit value',
  'returns.avg_coc': 'Average cash-on-cash',
  'returns.inputs': 'Returns assumptions',
  // partnership
  'partnership.catch_up_amount': 'Waterfall catch-up',
  'partnership.gp_additional_contributions': 'Additional contributions',
  'partnership.lp_additional_contributions': 'Additional contributions',
  'partnership.additional_contributions_by_year': 'Additional contributions',
  'partnership.tiers': 'Promote tiers',
  'partnership.waterfall': 'Partnership waterfall',
  // sensitivity
  'sensitivity.grids': 'Sensitivity grids',
  'sensitivity.matrix': 'Sensitivity grids',
  // cash flow
  'cash_flow.levered': 'Levered cash flow',
  'cash_flow.unlevered': 'Unlevered cash flow',
  'cash_flow.bridge': 'Cash Flow Bridge',
};

/** The tab a stale engine block belongs to ("P&L"); the engine's model name when unmapped. */
export function engineTabLabel(engine: string): string {
  return ENGINE_TAB_LABEL[engine] ?? `${ENGINE_MODEL_LABEL[engine] ?? humanizeWord(engine)} model`;
}

/**
 * The section a stale ``engine.path`` belongs to. Never returns a dotted path
 * or a snake_case token: unmapped blocks read as ``<Engine> model``.
 */
export function engineSectionLabel(engine: string, path: string): string {
  const dotted = `${engine}.${path}`;
  const direct = ENGINE_SECTION_LABEL[dotted];
  if (direct) return direct;
  const head = path.split('.')[0]?.replace(/\[\]$/, '');
  if (head) {
    const parent = ENGINE_SECTION_LABEL[`${engine}.${head}`];
    if (parent) return parent;
  }
  return `${ENGINE_MODEL_LABEL[engine] ?? humanizeWord(engine)} model`;
}
