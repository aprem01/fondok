/**
 * Cash Flow tab — pure view-model helpers over the canonical ``cash_flow``
 * engine output (Move 2, Stage 2b).
 *
 * The worker now emits a reconciled ``cash_flow`` statement (see
 * apps/worker/app/engines/cash_flow.py): line-itemized unlevered / levered
 * statements + a distribution waterfall, with every bottom line tied out to
 * the returns engine to the cent. These helpers are the browser-side READ of
 * that output — they do NOT re-derive the numbers (that was the old
 * ``buildCashFlowFromWorker`` this replaces). They only shape the worker's
 * lines into the sub-tab tables / summary KPIs the tab renders, and expose the
 * per-row provenance ``state`` for the Data Key dot.
 *
 * Kept free of React/recharts so it unit-tests as plain functions.
 */
import type {
  CashFlowStatementLine,
  CashFlowStatementOutput,
  ValueState,
} from '@/lib/api';

export type CashFlowSection = 'unlevered' | 'levered' | 'distributions';

const CENT = 0.01;

/** Mirror of the worker's ``cash_flow._slug`` so a row can find its own
 *  provenance trace (keyed ``{section}.{slug(label)}``). */
export function slugifyLabel(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_+|_+$/g, '');
}

/** The 6-state provenance vocabulary for a row's Data Key dot. Prefer the
 *  worker's provenance sidecar; fall back to the row's linked/calc kind (which
 *  is exactly what the worker derives the state from anyway). */
export function rowState(
  cf: CashFlowStatementOutput,
  section: CashFlowSection,
  line: CashFlowStatementLine,
): ValueState {
  const traced = cf.provenance?.[`${section}.${slugifyLabel(line.label)}`]?.state;
  if (traced) return traced;
  return line.kind === 'linked' ? 'linked' : 'calculated';
}

/** Period count including close (index 0). Levered/unlevered arrays are the
 *  source of truth; ``hold_years`` is the fallback. */
export function periodCount(cf: CashFlowStatementOutput): number {
  const fromArrays = Math.max(
    cf.levered_cash_flow?.length ?? 0,
    cf.unlevered_cash_flow?.length ?? 0,
  );
  return fromArrays > 0 ? fromArrays : (cf.hold_years ?? 0) + 1;
}

/** Column headers — "Close", "Year 1"…"Year N-1", "Year N / Exit". */
export function periodHeaders(cf: CashFlowStatementOutput): string[] {
  const hold = periodCount(cf) - 1;
  const out = ['Close'];
  for (let y = 1; y <= hold; y++) out.push(y === hold ? `Year ${y} / Exit` : `Year ${y}`);
  return out;
}

/** Sum a section's component (``linked``) rows per period — the composed
 *  bottom line that MUST equal the canonical series the worker reconciled to. */
export function footLinked(lines: CashFlowStatementLine[], n: number): number[] {
  const totals = new Array(n).fill(0);
  for (const l of lines) {
    if (l.kind !== 'linked') continue;
    l.values.forEach((v, i) => {
      if (v != null && i < n) totals[i] += v;
    });
  }
  return totals;
}

export function lineByLabel(
  lines: CashFlowStatementLine[],
  label: string,
): CashFlowStatementLine | undefined {
  return lines.find((l) => l.label === label);
}

export function sumLine(line: CashFlowStatementLine | undefined): number {
  if (!line) return 0;
  return line.values.reduce<number>((s, v) => s + (v ?? 0), 0);
}

export function valueAt(line: CashFlowStatementLine | undefined, i: number): number {
  return line?.values?.[i] ?? 0;
}

/** True when the section's component rows foot to the canonical bottom line
 *  (unlevered/levered) or to the ``Total Distributions`` calc row. */
export function sectionFoots(cf: CashFlowStatementOutput, section: CashFlowSection): boolean {
  const lines = cf[section];
  const n = periodCount(cf);
  if (section !== 'distributions') {
    const canonical = section === 'levered' ? cf.levered_cash_flow : cf.unlevered_cash_flow;
    const composed = footLinked(lines, n);
    return composed.every((c, i) => Math.abs(c - (canonical?.[i] ?? 0)) <= CENT);
  }
  const total = lineByLabel(lines, 'Total Distributions');
  if (!total) return true;
  const lp = lineByLabel(lines, 'LP Distributions');
  const gp = lineByLabel(lines, 'GP Distributions');
  return total.values.every(
    (v, i) => Math.abs((v ?? 0) - (valueAt(lp, i) + valueAt(gp, i))) <= CENT,
  );
}

export interface CashFlowKpi {
  label: string;
  /** Dollar amount; ``null`` when the deal lacks the line (renders em-dash). */
  value: number | null;
  sub: string;
}

export interface CashFlowBridgeRow {
  label: string;
  values: number[];
  bold?: boolean;
}

export interface CashFlowSummary {
  kpis: CashFlowKpi[];
  bridge: CashFlowBridgeRow[];
  /** True when property CF + financing == net CF to equity, every column. */
  foots: boolean;
}

/**
 * Summary sub-tab: the "Cash Flow Bridge" (property → financing → equity) plus
 * the headline KPI cards, all decomposed from the worker's line items so they
 * tie back to the canonical series. Faithful to design/canonical/Cash Flow
 * Tab.dc.html without re-running any math.
 */
export function buildSummary(cf: CashFlowStatementOutput): CashFlowSummary {
  const n = periodCount(cf);
  const hold = n - 1;
  const unlev = cf.unlevered_cash_flow ?? [];
  const lev = cf.levered_cash_flow ?? [];

  const financing: number[] = [];
  for (let i = 0; i < n; i++) financing.push((lev[i] ?? 0) - (unlev[i] ?? 0));

  const equity = -(lev[0] ?? 0);
  const totalToEquity = lev.slice(1).reduce<number>((s, v) => s + v, 0);

  const refiLine = lineByLabel(cf.levered, 'Net Refinance Cash-Out');
  const netRefi = sumLine(refiLine);
  const gross = valueAt(lineByLabel(cf.unlevered, 'Gross Sale Proceeds'), hold);
  const selling = valueAt(lineByLabel(cf.unlevered, 'Selling & Disposition Costs'), hold);
  const exitPayoff = valueAt(lineByLabel(cf.levered, 'Exit Debt Payoff'), hold);
  const netExit = gross + selling + exitPayoff;
  const operating = totalToEquity - netRefi - netExit;
  const em = equity !== 0 ? totalToEquity / equity : 0;

  const kpis: CashFlowKpi[] = [
    { label: 'Total Equity Invested', value: equity, sub: 'Funded in full at close' },
    { label: 'Operating Cash Flow to Equity', value: operating, sub: `Years 1–${hold}, after debt service` },
    { label: 'Net Refinance Proceeds', value: refiLine ? netRefi : null, sub: 'Cash-out, net of payoff & fees' },
    { label: 'Net Exit Proceeds', value: netExit, sub: 'Net sale less debt repaid' },
    { label: 'Total Cash Returned to Equity', value: totalToEquity, sub: `${em.toFixed(2)}x on equity invested` },
  ];

  const bridge: CashFlowBridgeRow[] = [
    { label: 'Property / Unlevered Cash Flow', values: unlev.slice(0, n) },
    { label: 'Financing', values: financing },
    { label: 'Net Cash Flow to Equity', values: lev.slice(0, n), bold: true },
  ];

  const foots = bridge[2].values.every(
    (v, i) => Math.abs((unlev[i] ?? 0) + financing[i] - v) <= CENT,
  );

  return { kpis, bridge, foots };
}

/** Fallback guard — a deal whose canonical run predates the cash_flow engine
 *  has no usable statement, so the tab shows the "Run Model" placeholder. */
export function hasCashFlowStatement(
  cf: CashFlowStatementOutput | null | undefined,
): cf is CashFlowStatementOutput {
  return (
    !!cf &&
    Array.isArray(cf.levered) &&
    Array.isArray(cf.unlevered) &&
    Array.isArray(cf.levered_cash_flow) &&
    cf.levered_cash_flow.length >= 2
  );
}
