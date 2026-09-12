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
// One scenario's engine result, as `scenarios.compare` returns it.
// ────────────────────────────────────────────────────────────────────

/** One engine's slot in the per-scenario map (`{ expense: { outputs }, … }`). */
export interface EngineOutputsLike {
  outputs?: unknown;
}

// ────────────────────────────────────────────────────────────────────
// The stabilized YEAR — one block, one year index, every surface
// (FON-41 / FON-59 #3).
//
// Before Wave 3 four different "stabilized" numbers shipped: the last element
// of `returns.noi_by_year` (Scenario Analysis), `expense.years[0].noi` (IC
// Memo), `returns.terminal_noi` — the year hold+1 reversion — (Overview), and
// the debt engine's own occupancy/plateau signal. The worker now publishes ONE
// block on the expense engine (`apps/worker/app/engines/stabilization.py`) and
// every consumer reads it, so they cannot drift again.
//
// Nothing here falls back to another year. A run with no resolvable
// stabilization year returns `null`, and the caller renders a dash with a
// reason — never a zero, never a figure borrowed from the exit.
// ────────────────────────────────────────────────────────────────────

/** Who owns the stabilization year on a published block. */
export type StabilizationSource = 'fondok_derived' | 'analyst_override';
/** Which signal produced the Fondok-derived seed. */
export type StabilizationSignal = 'occupancy' | 'noi_plateau';

/** `expense.stabilization` as the worker emits it. */
export interface StabilizedYearBlock {
  stabilized_year_index: number;
  /** 1-based model year — what the analyst reads ("Year 2"). */
  stabilized_year: number;
  source: StabilizationSource;
  signal?: StabilizationSignal | null;
  /** The Fondok-derived seed, kept alongside an analyst override. */
  derived_year?: number | null;
  stabilized_occupancy?: number | null;
  stabilized_adr?: number | null;
  stabilized_revenue?: number | null;
  /** NOI before the FF&E reserve — the bare word "NOI" in Fondok. */
  stabilized_noi_before_reserve?: number | null;
  /** Cash NOI (after the FF&E reserve) of the SAME year. */
  stabilized_cash_noi?: number | null;
  stabilized_noi_margin?: number | null;
}

const isFiniteNum = (v: unknown): v is number =>
  typeof v === 'number' && Number.isFinite(v);

/** Read the published block off one engine-outputs object, or `null`. */
export function stabilizedYearBlock(
  expenseOutputs: unknown,
): StabilizedYearBlock | null {
  if (!expenseOutputs || typeof expenseOutputs !== 'object') return null;
  const raw = (expenseOutputs as Record<string, unknown>).stabilization;
  if (!raw || typeof raw !== 'object') return null;
  const block = raw as Record<string, unknown>;
  if (!isFiniteNum(block.stabilized_year_index)) return null;
  if (!isFiniteNum(block.stabilized_year)) return null;
  return block as unknown as StabilizedYearBlock;
}

/**
 * The stabilized block for one scenario's engine map (`{ expense: { outputs } }`
 * — the shape `scenarios.compare` returns), or `null`.
 */
export function stabilizedYearFromEngines(
  engines: Record<string, EngineOutputsLike | undefined> | null | undefined,
): StabilizedYearBlock | null {
  if (!engines) return null;
  return stabilizedYearBlock(engines.expense?.outputs);
}

/**
 * Stabilized NOI **before** the FF&E reserve, from the stabilized year — the
 * figure Overview, the IC memo and Scenario Analysis all print. `null` when the
 * run published no stabilization block, or published one whose projection
 * never carried `noi_institutional`.
 */
export function stabilizedNoiBeforeReserve(
  engines: Record<string, EngineOutputsLike | undefined> | null | undefined,
): number | null {
  const block = stabilizedYearFromEngines(engines);
  const v = block?.stabilized_noi_before_reserve;
  return isFiniteNum(v) ? v : null;
}

/** Display label for the figure above. "NOI" unqualified = before the reserve. */
export const STABILIZED_NOI_LABEL = 'Stabilized NOI';

/**
 * How the stabilized year was arrived at, for the badge next to it.
 * "Fondok-derived — confirm" until the analyst has moved it.
 */
export const STABILIZATION_DERIVED_BADGE = 'Fondok-derived — confirm';
export const STABILIZATION_ANALYST_BADGE = 'Analyst override';

export function stabilizationBadge(block: StabilizedYearBlock | null): string | null {
  if (!block) return null;
  return block.source === 'analyst_override'
    ? STABILIZATION_ANALYST_BADGE
    : STABILIZATION_DERIVED_BADGE;
}

/** One-line explanation of where the derived seed came from. */
export function stabilizationSignalNote(block: StabilizedYearBlock | null): string | null {
  if (!block) return null;
  if (block.source === 'analyst_override') {
    return block.derived_year != null
      ? `Analyst-selected. Fondok's signal points at Year ${block.derived_year}.`
      : 'Analyst-selected.';
  }
  if (block.signal === 'occupancy') {
    return 'Derived — the first projected year occupancy reaches the stabilized assumption. Confirm or change it.';
  }
  if (block.signal === 'noi_plateau') {
    return 'Derived — the first projected year NOI growth settles to its terminal rate. Confirm or change it.';
  }
  return 'Derived from the projection. Confirm or change it.';
}
