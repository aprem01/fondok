/**
 * Human labels for every document type the worker can assign.
 *
 * Typed as ``Record<DocType, string>`` over the GENERATED ``DocType`` union
 * (``lib/ontology/concepts.generated.ts``), so a new document type cannot ship
 * without a label: the build fails here until it has one. ``docTypeLabel``
 * is the one fallback every chip and badge should use — Title Case with
 * underscores replaced, never the raw enum token.
 */
import type { DocType } from '@/lib/ontology/concepts.generated';

export const DOC_TYPE_LABEL: Record<DocType, string> = {
  OM: 'OM',
  T12: 'T-12',
  PNL: 'P&L',
  PNL_MONTHLY: 'Monthly P&L',
  PNL_YTD: 'YTD P&L',
  PNL_BENCHMARK: 'P&L Benchmark',
  PORTFOLIO_PNL: 'Portfolio P&L',
  STR: 'STR',
  STR_TREND: 'STR',
  STR_SEGMENTATION: 'STR Segmentation',
  CBRE_HORIZONS: 'CBRE',
  DEBT: 'Debt',
  PARTNERSHIP: 'Partnership / JV',
  OTHER: 'Other',
  INSURANCE: 'Insurance',
  PROPERTY_TAX: 'Prop. Tax',
  CAPEX: 'CapEx',
  PROPERTY_INFO: 'Other Property Info',
  LEASES: 'Leases',
  CONTRACT: 'Contract',
  SURVEYS: 'Due Diligence',
  ROOM_MIX: 'Hotel Program',
  RENT_ROLL: 'Rent Roll',
  MARKET_STUDY: 'Market Study',
};

/** Legacy / worker-side tokens that are not in the DocType union. */
const EXTRA_LABELS: Record<string, string> = {
  BUDGET: 'Budget',
  UNKNOWN: 'Uncategorized',
};

/** Title Case with underscores as spaces — the last-resort fallback. */
export function titleCaseToken(token: string): string {
  return token
    .toLowerCase()
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

/** The display label for a document-type token; never the raw token. */
export function docTypeLabel(token: string | null | undefined): string {
  if (!token) return 'Uncategorized';
  const upper = token.toUpperCase();
  return (DOC_TYPE_LABEL as Record<string, string>)[upper] ?? EXTRA_LABELS[upper] ?? titleCaseToken(upper);
}
