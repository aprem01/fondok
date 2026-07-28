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
  noi: 'NOI',
  noi_institutional: 'NOI',
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

  // 3. Generic revenue/expense leaf → borrow the parent segment for context
  //    so ``p_and_l_usali.rooms.revenue_usd`` reads "Rooms Revenue".
  if ((base === 'revenue' || base === 'expense') && segs.length >= 2) {
    const parent = segs[segs.length - 2].toLowerCase();
    const parentLabel = LEAF_LABELS[parent] ?? humanizeWord(parent);
    const suffix = base === 'revenue' ? 'Revenue' : 'Expense';
    return `${parentLabel} ${suffix}`.trim();
  }

  // 4. Fallback: split into words, expand acronyms, title-case.
  const words = base.split(/[_\s]+/).map(humanizeWord).filter(Boolean);
  return words.join(' ').trim() || raw;
}
