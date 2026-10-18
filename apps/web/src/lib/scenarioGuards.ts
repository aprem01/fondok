/**
 * Guards for scenario return figures that cannot be real results.
 *
 * The returns engine reports a levered IRR of exactly 0 when its solver did
 * not converge, and a negative equity multiple when the scenario's equity went
 * negative. Both reached the committee memo as "Levered IRR 0.0%" and "Equity
 * Multiple −0.18x" — impossible figures presented as results. Until the worker
 * returns null plus a reason for these cases, the web treats them as
 * unavailable: the cell renders "—" with the reason, and the scenario column
 * is marked as needing a re-run.
 */

export type ScenarioReturnsIssue = 'irr_unsolved' | 'negative_equity';

export const SCENARIO_ISSUE_LABEL: Record<ScenarioReturnsIssue, string> = {
  irr_unsolved: 'Returns did not solve for this scenario',
  negative_equity: 'Equity goes negative in this scenario',
};

export const SCENARIO_NEEDS_RERUN = 'needs re-run';

/**
 * Why a scenario's return figures are not usable, or null when they are.
 *
 * - any equity multiple below zero → negative equity;
 * - an IRR of exactly 0 while the scenario differs from Base (the Base IRR is
 *   not 0, or the multiple differs from Base) → the solver did not converge.
 *
 * A Base case whose own IRR is genuinely 0 with no comparison is left alone:
 * there is no delta to prove it impossible.
 */
export function scenarioReturnsIssue(
  cell: { irr: number | null | undefined; em: number | null | undefined },
  base?: { irr: number | null | undefined; em: number | null | undefined } | null,
): ScenarioReturnsIssue | null {
  if (typeof cell.em === 'number' && Number.isFinite(cell.em) && cell.em < 0) return 'negative_equity';
  if (typeof cell.irr === 'number' && cell.irr === 0) {
    const baseIrr = base?.irr;
    const baseEm = base?.em;
    const irrDiffers = typeof baseIrr === 'number' && baseIrr !== 0;
    const emDiffers =
      typeof baseEm === 'number' && typeof cell.em === 'number' && Math.abs(baseEm - cell.em) > 1e-9;
    if (irrDiffers || emDiffers) return 'irr_unsolved';
  }
  return null;
}
