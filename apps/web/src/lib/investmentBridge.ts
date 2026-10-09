/**
 * R-073 — Investment Bridge layout helpers (pure; no React).
 *
 * The worker computes the legs (`GET /deals/{id}/engines/investment-bridge`);
 * this module only lays them out as a waterfall: a start bar (equity
 * invested), one floating bar per leg from the running total, and an end bar
 * (equity returned). An unavailable / not-applicable leg is a "—" row that
 * does not move the running total — it is never drawn as a zero bar.
 */
import type { InvestmentBridgeLeg, InvestmentBridgeResponse } from '@/lib/api';

export interface BridgeStep {
  key: string;
  label: string;
  kind: 'start' | 'leg' | 'end';
  /** Signed value of the bar; null → "—" row (no bar). */
  value: number | null;
  /** Running total before / after this bar (equal for a "—" row). */
  from: number;
  to: number;
  leg?: InvestmentBridgeLeg;
}

export interface BridgeLayout {
  steps: BridgeStep[];
  /** Axis domain covering every running total and 0. */
  min: number;
  max: number;
  /** equity invested + Σ available legs. */
  computedTotal: number | null;
  /** Σ returns.cash_flows[1:] (the engine's own total). */
  equityReturned: number | null;
  /** True only when every leg is available and the bars foot to the total. */
  foots: boolean;
}

export const FOOT_TOLERANCE_USD = 1;

export function layoutBridge(b: InvestmentBridgeResponse): BridgeLayout | null {
  if (!b.available || b.equity_invested == null) return null;
  const steps: BridgeStep[] = [];
  let run = b.equity_invested;
  steps.push({ key: 'start', label: 'Equity invested', kind: 'start', value: b.equity_invested, from: 0, to: run });
  for (const leg of b.legs) {
    if (leg.value == null) {
      steps.push({ key: leg.key, label: leg.label, kind: 'leg', value: null, from: run, to: run, leg });
      continue;
    }
    const next = run + leg.value;
    steps.push({ key: leg.key, label: leg.label, kind: 'leg', value: leg.value, from: run, to: next, leg });
    run = next;
  }
  const anyUnavailable = b.legs.some((l) => l.status === 'unavailable');
  const equityReturned = b.equity_returned;
  if (equityReturned != null) {
    steps.push({ key: 'end', label: 'Equity returned', kind: 'end', value: equityReturned, from: 0, to: equityReturned });
  }
  const points = [0, ...steps.flatMap((s) => [s.from, s.to])];
  const computedTotal = anyUnavailable ? null : run;
  const foots =
    computedTotal != null && equityReturned != null && Math.abs(computedTotal - equityReturned) < FOOT_TOLERANCE_USD;
  return {
    steps,
    min: Math.min(...points),
    max: Math.max(...points),
    computedTotal,
    equityReturned,
    foots,
  };
}
