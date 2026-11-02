/**
 * worksheetFooting — does a historical statement column FOOT, and what did a
 * "correct at source" edit change in it? (E-012 / FON-41: "a $5 increase in
 * Rooms Revenue should raise Total Revenue by $5 and update dependent
 * calculations".)
 *
 * Facts this module is built on (read, not assumed):
 *
 *   • The worker's ``review_extraction_field`` (apps/worker/app/api/
 *     documents.py) replaces ONE extracted field's value and recomputes the
 *     confidence report. It does NOT recompute the statement's other lines:
 *     a stated GOP / NOI / Total Revenue stays exactly what was extracted.
 *   • In a historical column the worksheet's Total Revenue cell is the
 *     statement's OWN stated total when it publishes one (FON-90) and the
 *     CLIENT-SIDE SUM of the extracted lines (rooms + F&B + misc) only when
 *     it does not. The footing below always compares that SUM
 *     (``histDerivedTotalRevenue``) against the stated total — never the
 *     cell against itself. Total Fees & Fixed stays a client-side sum
 *     (``fixed_expenses`` = mgmt fee + FF&E reserve + property tax +
 *     insurance + rent + other non-operating expense at column-build time,
 *     Wave 5). GOP and NOI are the statement's own stated lines
 *     (``HIST_KEY_BY_ROW``); the NOI footing checks GOP − Total Fees & Fixed
 *     + non-operating income against the stated NOI.
 *   • There is no worker footing / variance output for historical
 *     statements (``footing`` exists only as a cash-flow-statement concept),
 *     so every number here is computed in the browser and labelled
 *     "calculated".
 */

import type { HistYear } from '@/components/project/pl/HistoricalsSection';
import { histDerivedTotalRevenue, histValue } from '@/lib/reviewState';
import { fmtCurrency } from '@/lib/format';
import { halfUnitOfValue } from '@/lib/statementLines';

/**
 * Wave 5 (Sam #3) — a footing's tolerance is the precision its figures were
 * read at: the sum, over every line summed and the stated figure, of half a
 * unit of the line's last significant figure (``HistYear.half_units``, from
 * ``lib/statementLines.halfUnitOf``). The extractor reads spreadsheet cells as
 * printed — "1.29402e+07" is 12,940,200 ± 50 — so the real Kimpton statements
 * miss by $1–$17 on revenue, GOP and NOI with no line missing; a flat $1
 * called that a gap. A line the column does not carry still shows: its whole
 * amount is far outside the precision.
 */
export function footingTolerance(h: HistYear, keys: readonly string[], values: readonly (number | null)[]): number {
  let tol = 0;
  keys.forEach((k, i) => {
    const v = values[i];
    const known = h.half_units?.[k];
    tol += known ?? (v == null ? 0 : halfUnitOfValue(v));
  });
  return tol;
}

export interface Footing {
  id: 'total_rev' | 'gop' | 'noi';
  /** The noun after "stated": "total" for revenue, "GOP" for GOP. */
  statedLabel: string;
  /** Σ of the extracted lines (calculated client-side). */
  lines: number;
  /** The statement's own stated figure. */
  stated: number;
  /** lines − stated. */
  difference: number;
  /** The precision of the figures compared (see ``footingTolerance``). */
  tolerance: number;
  foots: boolean;
}

export type FootingFailure = 'no_stated_total' | 'lines_missing' | 'nonop_sign_unresolved';

export type FootingResult =
  | { ok: true; footing: Footing }
  | { ok: false; reason: FootingFailure };

const nOrNull = (x: unknown): number | null =>
  typeof x === 'number' && Number.isFinite(x) ? x : null;

function build(id: Footing['id'], statedLabel: string, lines: number, stated: number, tolerance: number): FootingResult {
  const difference = lines - stated;
  return {
    ok: true,
    footing: { id, statedLabel, lines, stated, difference, tolerance, foots: Math.abs(difference) <= tolerance },
  };
}

const REVENUE_KEYS = ['rooms', 'fb', 'misc'] as const;

/** Rooms + F&B + Misc vs the statement's own Total Revenue line. */
export function footRevenue(h: HistYear): FootingResult {
  const stated = nOrNull(h.total_revenue_stated);
  if (stated == null) return { ok: false, reason: 'no_stated_total' };
  const lines = histDerivedTotalRevenue(h);
  if (lines == null) return { ok: false, reason: 'lines_missing' };
  const tol = footingTolerance(h, [...REVENUE_KEYS, 'total_rev'], [nOrNull(h.rooms), nOrNull(h.fb), nOrNull(h.misc), stated]);
  return build('total_rev', 'total', lines, stated, tol);
}

/** Total Revenue − departmental expenses − undistributed vs the stated GOP. */
export function footGop(h: HistYear): FootingResult {
  const stated = nOrNull(h.gop);
  if (stated == null) return { ok: false, reason: 'no_stated_total' };
  // The lines-side of the check: the SUM of the revenue lines, so a stated
  // total that the lines do not reproduce surfaces here as well as above.
  const totalRev = histDerivedTotalRevenue(h);
  const parts = [h.rooms_dept_expense, h.fb_dept_expense, h.other_dept_expense, h.undistributed].map(nOrNull);
  if (totalRev == null || parts.some((p) => p == null)) return { ok: false, reason: 'lines_missing' };
  const lines = totalRev - (parts as number[]).reduce((a, b) => a + b, 0);
  const tol = footingTolerance(
    h,
    [...REVENUE_KEYS, 'rooms_dept', 'fb_dept', 'other_dept', 'undistributed', 'gop'],
    [nOrNull(h.rooms), nOrNull(h.fb), nOrNull(h.misc), ...parts, stated],
  );
  return build('gop', 'GOP', lines, stated, tol);
}

/**
 * Wave 5 (Sam #3) — stated GOP − Total Fees & Fixed + non-operating income vs
 * the statement's stated NOI. Total Fees & Fixed is mgmt fee + FF&E reserve +
 * property tax + insurance + rent + other non-operating expense over the lines
 * the statement states (``fixed_expenses``); non-operating income is signed by
 * the statement's own convention (``nonop_income_basis``). A statement that
 * states non-operating income whose sign its figures do not settle is not
 * footed at all (``nonop_sign_unresolved``) — that is not a source gap.
 */
export function footNoi(h: HistYear): FootingResult {
  const stated = nOrNull(h.noi);
  const gop = nOrNull(h.gop);
  if (stated == null || gop == null) return { ok: false, reason: 'no_stated_total' };
  if (h.nonop_income_basis === 'unresolved') return { ok: false, reason: 'nonop_sign_unresolved' };
  const fixed = nOrNull(h.fixed_expenses);
  if (fixed == null) return { ok: false, reason: 'lines_missing' };
  const nonop = nOrNull(h.nonop_income);
  const lines = gop - fixed + (nonop ?? 0);
  const tol = footingTolerance(
    h,
    ['gop', 'mgmt_fee', 'ffe', 'property_tax', 'insurance', 'rent', 'other_nonop', 'nonop_income', 'noi'],
    [gop, nOrNull(h.mgmt_fee), nOrNull(h.ffe_reserve), nOrNull(h.property_tax), nOrNull(h.insurance),
      nOrNull(h.rent), nOrNull(h.other_nonop_expense), nonop, stated],
  );
  return build('noi', 'NOI', lines, stated, tol);
}

/** The NOI footing cell: "✓ foots", or the gap named as a source gap. */
export function footingGapText(f: Footing): string {
  if (f.foots) return '✓ foots';
  return `doesn\u2019t foot by ${fmtCurrency(Math.abs(f.difference))} — source gap`;
}

/** "+$5" / "−$5" / "$0". */
export function fmtSignedCurrency(n: number): string {
  if (Math.abs(n) < 0.5) return '$0';
  return `${n < 0 ? '−' : '+'}${fmtCurrency(Math.abs(n))}`;
}

/** "Lines sum to $13,000,005 · stated total $13,000,000 · difference +$5" */
export function footingSentence(f: Footing): string {
  return `Lines sum to ${fmtCurrency(f.lines)} · stated ${f.statedLabel} ${fmtCurrency(f.stated)} · difference ${fmtSignedCurrency(f.difference)}`;
}

export const FOOTING_REASON_TEXT: Record<FootingFailure, string> = {
  no_stated_total: 'This statement publishes no stated total for this line, so there is nothing to foot against.',
  lines_missing: 'Not every line this total is built from was extracted, so the sum cannot be formed.',
  nonop_sign_unresolved:
    'The statement states non-operating income, but its figures do not settle whether the line is printed as income or as a deduction, so NOI is not footed.',
};

// ───────────────────── what an edit changed in a column ─────────────────────

export interface CellChange {
  rowId: string;
  before: number | null;
  after: number | null;
  /** after − before, treating a missing side as 0. */
  delta: number;
}

/**
 * Every worksheet row whose historical value moved between two snapshots of
 * the same column — the edited line itself AND every client-derived total
 * that re-summed from it (Total Fees & Fixed; Total Revenue only when the
 * statement states no total of its own). Stated lines (GOP, NOI, a stated
 * Total Revenue) only appear here when the edit was to that line.
 */
export function diffHistColumn(
  before: Readonly<Record<string, number | null>>,
  after: HistYear,
  rowIds: readonly string[],
): CellChange[] {
  const out: CellChange[] = [];
  for (const rowId of rowIds) {
    const b = nOrNull(before[rowId]);
    const a = histValue(rowId, after);
    if (b == null && a == null) continue;
    const delta = (a ?? 0) - (b ?? 0);
    if (Math.abs(delta) < 0.005) continue;
    out.push({ rowId, before: b, after: a, delta });
  }
  return out;
}
