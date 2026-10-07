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
 *   • In a historical column the worksheet's Total Revenue cell is a
 *     CLIENT-SIDE SUM of the extracted lines (``histValue('total_rev')`` =
 *     rooms + F&B + misc), as is Total Fees & Fixed (``fixed_expenses`` =
 *     mgmt fee + property tax + insurance at column-build time). GOP and NOI
 *     are the statement's own stated lines (``HIST_KEY_BY_ROW``).
 *   • There is no worker footing / variance output for historical
 *     statements (``footing`` exists only as a cash-flow-statement concept),
 *     so every number here is computed in the browser and labelled
 *     "calculated".
 */

import type { HistYear } from '@/components/project/pl/HistoricalsSection';
import { histValue } from '@/lib/reviewState';
import { fmtCurrency } from '@/lib/format';

/** Sub-dollar differences are rounding, not a footing failure. */
export const FOOTING_TOLERANCE_USD = 1;

export interface Footing {
  id: 'total_rev' | 'gop';
  /** The noun after "stated": "total" for revenue, "GOP" for GOP. */
  statedLabel: string;
  /** Σ of the extracted lines (calculated client-side). */
  lines: number;
  /** The statement's own stated figure. */
  stated: number;
  /** lines − stated. */
  difference: number;
  foots: boolean;
}

export type FootingResult =
  | { ok: true; footing: Footing }
  | { ok: false; reason: 'no_stated_total' | 'lines_missing' };

const nOrNull = (x: unknown): number | null =>
  typeof x === 'number' && Number.isFinite(x) ? x : null;

function build(id: Footing['id'], statedLabel: string, lines: number, stated: number): FootingResult {
  const difference = lines - stated;
  return {
    ok: true,
    footing: { id, statedLabel, lines, stated, difference, foots: Math.abs(difference) < FOOTING_TOLERANCE_USD },
  };
}

/** Rooms + F&B + Misc vs the statement's own Total Revenue line. */
export function footRevenue(h: HistYear): FootingResult {
  const stated = nOrNull(h.total_revenue_stated);
  if (stated == null) return { ok: false, reason: 'no_stated_total' };
  const lines = histValue('total_rev', h);
  if (lines == null) return { ok: false, reason: 'lines_missing' };
  return build('total_rev', 'total', lines, stated);
}

/** Total Revenue − departmental expenses − undistributed vs the stated GOP. */
export function footGop(h: HistYear): FootingResult {
  const stated = nOrNull(h.gop);
  if (stated == null) return { ok: false, reason: 'no_stated_total' };
  const totalRev = histValue('total_rev', h);
  const parts = [h.rooms_dept_expense, h.fb_dept_expense, h.other_dept_expense, h.undistributed].map(nOrNull);
  if (totalRev == null || parts.some((p) => p == null)) return { ok: false, reason: 'lines_missing' };
  const lines = totalRev - (parts as number[]).reduce((a, b) => a + b, 0);
  return build('gop', 'GOP', lines, stated);
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

export const FOOTING_REASON_TEXT: Record<'no_stated_total' | 'lines_missing', string> = {
  no_stated_total: 'This statement publishes no stated total for this line, so there is nothing to foot against.',
  lines_missing: 'Not every line this total is built from was extracted, so the sum cannot be formed.',
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
 * that re-summed from it (Total Revenue, Total Fees & Fixed). Stated lines
 * (GOP, NOI) only appear here when the edit was to that line.
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
