/**
 * provenance.ts — shared source-of-truth classification + formatting.
 *
 * One place to answer "where did this number come from, and how do I show
 * it?" — used by the Provenance Ledger, the <Sourced> hover primitive, and
 * (rolling out) every screen. Kinds map to the SourcedValue color system:
 *   grounded  🟢  from THIS deal's documents / entry
 *   benchmark 🟡  a market / seed default — NOT this deal's data
 *   override  🟣  analyst-set
 */

export type SourceKind = 'grounded' | 'benchmark' | 'override';

const GROUNDED_SOURCES = new Set([
  't12_actual',
  'deal_row',
  'om_comps',
  'om_broker',
  'portfolio_pnl',
  'str_forecast',
]);
const OVERRIDE_SOURCES = new Set(['analyst_override']);

export function sourceKind(source: string): SourceKind {
  if (OVERRIDE_SOURCES.has(source)) return 'override';
  if (GROUNDED_SOURCES.has(source)) return 'grounded';
  return 'benchmark'; // seed / cbre_horizons / pnl_benchmark / *_default
}

export const SOURCE_LABEL: Record<string, string> = {
  seed: 'Seed default',
  deal_row: 'Deal entry',
  t12_actual: 'T-12 actual',
  cbre_horizons: 'CBRE benchmark',
  pnl_benchmark: 'Industry benchmark',
  portfolio_pnl: 'Portfolio P&L',
  om_comps: 'OM comps',
  om_broker: 'OM broker',
  analyst_override: 'Analyst override',
  str_forecast: 'STR forecast',
};

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source.replace(/_/g, ' ');
}

/** One-line, human explanation of what a source means — the hover body. */
export function sourceExplanation(source: string): string {
  switch (source) {
    case 't12_actual':
      return 'Extracted from the deal’s T-12 actuals.';
    case 'deal_row':
      return 'Entered on the deal record.';
    case 'om_comps':
      return 'From the offering memorandum’s comparable set.';
    case 'om_broker':
      return 'From the broker’s pro forma in the OM.';
    case 'portfolio_pnl':
      return 'From your portfolio P&L library.';
    case 'str_forecast':
      return 'From the STR / comp-set forecast.';
    case 'cbre_horizons':
      return 'CBRE Horizons market benchmark — not this deal’s own data.';
    case 'pnl_benchmark':
      return 'Industry (USALI/HOST) benchmark — not this deal’s own data.';
    case 'analyst_override':
      return 'Set by an analyst with a justification note.';
    case 'seed':
    default:
      return 'A default assumption — no deal-specific data has grounded this yet. Upload a T-12 / OM / STR to ground it.';
  }
}

export const KIND_TONE: Record<SourceKind, { text: string; bg: string; dot: string }> = {
  grounded: { text: 'text-success-700', bg: 'bg-success-50', dot: 'bg-success-500' },
  benchmark: { text: 'text-warn-700', bg: 'bg-warn-50', dot: 'bg-warn-500' },
  override: { text: 'text-brand-700', bg: 'bg-brand-50', dot: 'bg-brand-500' },
};

/** Humanize an assumption key: strip the USALI prefix, expand _pct/_usd,
 *  title-case, and keep known acronyms uppercased. */
const ACRONYMS = new Set(['adr', 'noi', 'revpar', 'irr', 'ltv', 'dscr', 'gp', 'lp', 'fb', 'coc', 'ffe', 'str', 'om', 'usali', 'ytd', 'ttm', 'pip']);
export function humanizeAssumptionKey(k: string): string {
  const base = k.replace(/^p_and_l_usali\./, '').replace(/^property_overview\./, '');
  return base
    .replace(/_pct$/, '_%')
    .replace(/_usd$/, '')
    .split(/[._]/)
    .filter(Boolean)
    .map((s) =>
      s === '%'
        ? '%'
        : ACRONYMS.has(s.toLowerCase())
          ? s.toUpperCase()
          : s.charAt(0).toUpperCase() + s.slice(1),
    )
    .join(' ');
}

// Keys whose values are ratios stored as fractions (0.65 → 65%).
const RATIO_KEY = /(_pct|growth|occupancy|cap_rate|ltv|rate|ratio|margin|_pc$|equity_)/;
// Keys that are plain counts / labels — never currency.
const COUNT_KEY = /(keys|year|years|outlets|spaces|_sf$|space_sf|count|per_occupied|per_room|_ratio$)/;

/** Format an assumption value for display, inferring % vs $ vs count. */
export function formatAssumptionValue(
  key: string,
  v: number | string | boolean | null | undefined,
): string {
  if (v == null) return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'string') return v;
  const k = key.toLowerCase();

  if (RATIO_KEY.test(k) && !COUNT_KEY.test(k)) {
    const pct = Math.abs(v) <= 1 ? v * 100 : v;
    return `${pct.toFixed(1)}%`;
  }
  if (COUNT_KEY.test(k)) {
    // counts / SF / per-room ratios: no currency sign, tidy decimals
    return Number.isInteger(v) ? String(v) : v.toFixed(2);
  }
  // currency-ish
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `$${Math.round(v / 1_000)}K`;
  if (abs >= 100) return `$${Math.round(v)}`;
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(2);
}
