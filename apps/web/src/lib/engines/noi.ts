/**
 * The two NOI bases, and the selectors that read them.
 *
 * The bare word "NOI" means NOI **before** the FF&E replacement reserve
 * (founder decision — FON-59 #1 / FON-67 #2). That is the engine field
 * `expense.years[].noi_institutional` and the registry concept `ebitda`.
 * The after-reserve figure `expense.years[].noi` (registry concept `noi`) is
 * "Cash NOI"; it is what the Debt engine uses for DSCR / debt yield and what
 * the Returns engine capitalises at the exit cap.
 *
 * Every label below is the canonical display string. Nothing here changes an
 * engine value — these are display selectors only.
 */

/** NOI before the FF&E reserve — `expense.years[].noi_institutional`. */
export const NOI_BEFORE_RESERVE_LABEL = 'NOI (before FF&E reserve)';

/** Cash NOI — `expense.years[].noi`, i.e. net of the FF&E reserve. */
export const CASH_NOI_LABEL = 'Cash NOI (after FF&E reserve)';

/**
 * Pre-upgrade `engine_outputs` rows persist `noi_institutional: null`
 * (see apps/worker/app/engines/expense.py). The value falls back to `noi`,
 * but the label must NOT then assert "before FF&E reserve" about a number
 * that is actually after it.
 */
export const NOI_BASIS_UNCONFIRMED_LABEL = 'NOI (basis unconfirmed — pre-upgrade run)';

/** The label to print for a before-reserve NOI read, given whether the run
 *  actually carried `noi_institutional`. */
export function noiBeforeReserveLabel(basisConfirmed: boolean): string {
  return basisConfirmed ? NOI_BEFORE_RESERVE_LABEL : NOI_BASIS_UNCONFIRMED_LABEL;
}

/** Minimal shape of one expense-engine year (apps/worker/app/engines/expense.py). */
export interface ExpenseYearNoi {
  noi?: number | null;
  noi_institutional?: number | null;
}

export interface NoiBeforeReserve {
  /** `noi_institutional` when the run carried it, else the legacy `noi`. */
  value: number | undefined;
  /** False on a pre-upgrade run — the value is an after-reserve number. */
  basisConfirmed: boolean;
  /** The label to print next to `value`. */
  label: string;
}

/**
 * Read NOI before the FF&E reserve off one expense-engine year, keeping the
 * legacy `?? noi` fallback but reporting honestly which basis it is.
 */
export function noiBeforeReserve(year: ExpenseYearNoi | null | undefined): NoiBeforeReserve {
  const inst = typeof year?.noi_institutional === 'number' ? year.noi_institutional : undefined;
  const legacy = typeof year?.noi === 'number' ? year.noi : undefined;
  const basisConfirmed = inst != null;
  return {
    value: inst ?? legacy,
    basisConfirmed,
    label: noiBeforeReserveLabel(basisConfirmed),
  };
}

// ────────────────────────────────────────────────────────────────────
// Stabilized Cash NOI — the single selector IC Memo and Scenario
// Analysis share, so the two panels cannot drift again (FON-54 #1).
// ────────────────────────────────────────────────────────────────────

/** One scenario's engine result as the `scenarios.compare` endpoint returns it. */
export interface EngineOutputsLike {
  outputs?: unknown;
}

/** Last finite number in a numeric array field on an engine output. */
function lastNumInArray(obj: unknown, key: string): number | null {
  if (!obj || typeof obj !== 'object') return null;
  const arr = (obj as Record<string, unknown>)[key];
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const v = arr[arr.length - 1];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Field on the last element of an array-of-objects — e.g. expense.years[last].noi. */
function lastYearField(obj: unknown, arrKey: string, field: string): number | null {
  if (!obj || typeof obj !== 'object') return null;
  const arr = (obj as Record<string, unknown>)[arrKey];
  if (!Array.isArray(arr) || arr.length === 0) return null;
  const last = arr[arr.length - 1];
  if (!last || typeof last !== 'object') return null;
  const v = (last as Record<string, unknown>)[field];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/**
 * Terminal-year (stabilized) Cash NOI for one scenario: the last element of
 * `returns.noi_by_year`, falling back to the last expense-engine operating
 * year's `noi`. Both series are net of the FF&E reserve, hence "Cash NOI".
 *
 * `engines` is the per-scenario map the compare endpoint returns
 * (`{ returns: { outputs }, expense: { outputs }, … }`).
 */
export function stabilizedCashNoi(
  engines: Record<string, EngineOutputsLike | undefined> | null | undefined,
): number | null {
  if (!engines) return null;
  return (
    lastNumInArray(engines.returns?.outputs, 'noi_by_year') ??
    lastYearField(engines.expense?.outputs, 'years', 'noi')
  );
}

/** Display label for the stabilized figure above. */
export const STABILIZED_CASH_NOI_LABEL = 'Stabilized Cash NOI';
