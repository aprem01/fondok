/**
 * The returns engine's headline and its schedule by exit year — ONE reader
 * (FON-118 / FON-44).
 *
 * The headline IRR / equity multiple IS one row of the exit schedule: the
 * worker evaluates "the deal sold at month m" for every projection year-end
 * and for the deal's own exit (`hold × 12`, or the disposition month when one
 * is set), and publishes the selected row's figures as `levered_irr`,
 * `unlevered_irr`, `equity_multiple` exactly as before. Every surface that
 * prints a return — the Returns hero, its Exit Assumptions card, the Overview
 * KPI tiles and Exit section, the IC memo — reads it through this module, so
 * no component derives a return, an exit year, or a sale month on its own.
 *
 * Standing rule (no invented data): a figure the engine did not publish is
 * `null` here and renders as "—" with the missing field named, never 0. A
 * `not_available` schedule row carries its reason code and renders as a dash
 * with that reason's label. The monthly IRR solver's sentinels (10.0 for a
 * stream with no outflow, −0.9999 for one with no inflow) are never printed as
 * a percentage.
 */

import type { ExitNoiBasis, ReturnsExitScheduleRow } from '@/lib/api';
import { REASONS } from '@/lib/ontology/reasons.generated';

const isFiniteNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const isInt = (v: unknown): v is number => isFiniteNum(v) && Number.isInteger(v);

// ────────────────────────────────────────────────────────────────────
// IRR sentinels
// ────────────────────────────────────────────────────────────────────

/** `monthly_cashflow.xirr` returns 10.0 when every flow is an inflow and
 *  −0.9999 when every flow is an outflow, rather than 0. Neither is an IRR. */
export function isIrrSentinel(v: number): boolean {
  return v >= 10.0 || v <= -0.9999;
}

/** The sentence a withheld sentinel carries (tile sub-label / cell title). */
export const IRR_SENTINEL_NOTE = 'No IRR — the cash flows have one sign, so the solver returned a sentinel, not a rate';

/** A finite, non-sentinel IRR, or `null`. */
export function irrValue(v: unknown): number | null {
  return isFiniteNum(v) && !isIrrSentinel(v) ? v : null;
}

/** A finite number, or `null`. */
function numOrNull(v: unknown): number | null {
  return isFiniteNum(v) ? v : null;
}

// ────────────────────────────────────────────────────────────────────
// Headline
// ────────────────────────────────────────────────────────────────────

const EXIT_NOI_BASES: readonly ExitNoiBasis[] = ['forward_12m', 'stabilized', 'sale_year', 'override'];

function exitNoiBasis(v: unknown): ExitNoiBasis | null {
  return typeof v === 'string' && (EXIT_NOI_BASES as readonly string[]).includes(v) ? (v as ExitNoiBasis) : null;
}

/** The returns engine's headline, as published — nothing derived. */
export interface HeadlineReturns {
  /** `levered_irr`, sentinel-free. Null when absent OR when the engine
   *  published a sentinel (then `leveredIrrNote` says so). */
  leveredIrr: number | null;
  leveredIrrNote: string | null;
  unleveredIrr: number | null;
  unleveredIrrNote: string | null;
  equityMultiple: number | null;
  avgCoc: number | null;
  yearOneCoc: number | null;
  holdYears: number | null;
  /** The headline's exit — the month from close the deal is sold in. Null on
   *  a run that predates the schedule (then nothing stands in for it). */
  selectedExitMonth: number | null;
  /** The projection year that month closes; null when the selected exit is
   *  off-cycle (a mid-year disposition) or the run predates the schedule. */
  selectedExitYear: number | null;
  /** The exit year with the highest levered IRR among computed rows. */
  peakLeveredExitYear: number | null;
  exitNoiBasis: ExitNoiBasis | null;
  exitNoiPeriodLabel: string | null;
  terminalNoi: number | null;
  grossSalePrice: number | null;
  sellingCosts: number | null;
  netProceeds: number | null;
  exitCapRate: number | null;
  cashFlows: number[] | null;
  /** The schedule, validated (see `exitSchedule`). Empty when absent. */
  exitSchedule: ExitScheduleRow[];
  /** True when the run carries an `exit_schedule` field at all. */
  hasExitSchedule: boolean;
}

/**
 * Read the headline off the returns engine's `outputs` object (what
 * `getEngineField(outputs, 'returns')` returns). Tolerates `null` /
 * `undefined` / a non-object — every field is then `null`.
 */
export function headlineReturns(returnsOutputs: unknown): HeadlineReturns {
  const o = (returnsOutputs && typeof returnsOutputs === 'object' ? returnsOutputs : {}) as Record<string, unknown>;
  const rawLevered = o.levered_irr;
  const rawUnlevered = o.unlevered_irr;
  const levered = irrValue(rawLevered);
  const unlevered = irrValue(rawUnlevered);
  const periodLabel = typeof o.exit_noi_period_label === 'string' && o.exit_noi_period_label.trim()
    ? o.exit_noi_period_label.trim()
    : null;
  const flows = Array.isArray(o.cash_flows) && o.cash_flows.every(isFiniteNum) ? (o.cash_flows as number[]) : null;
  return {
    leveredIrr: levered,
    leveredIrrNote: levered == null && isFiniteNum(rawLevered) ? IRR_SENTINEL_NOTE : null,
    unleveredIrr: unlevered,
    unleveredIrrNote: unlevered == null && isFiniteNum(rawUnlevered) ? IRR_SENTINEL_NOTE : null,
    equityMultiple: numOrNull(o.equity_multiple),
    avgCoc: numOrNull(o.avg_coc),
    yearOneCoc: numOrNull(o.year_one_coc),
    holdYears: numOrNull(o.hold_years),
    selectedExitMonth: isInt(o.selected_exit_month) ? o.selected_exit_month : null,
    selectedExitYear: isInt(o.selected_exit_year) ? o.selected_exit_year : null,
    peakLeveredExitYear: isInt(o.peak_levered_exit_year) ? o.peak_levered_exit_year : null,
    exitNoiBasis: exitNoiBasis(o.exit_noi_basis),
    exitNoiPeriodLabel: periodLabel,
    terminalNoi: numOrNull(o.terminal_noi_usd) ?? numOrNull(o.terminal_noi),
    grossSalePrice: numOrNull(o.gross_sale_price),
    sellingCosts: numOrNull(o.selling_costs),
    netProceeds: numOrNull(o.net_proceeds),
    exitCapRate: numOrNull(o.exit_cap_rate),
    cashFlows: flows,
    exitSchedule: exitSchedule(returnsOutputs),
    hasExitSchedule: Array.isArray(o.exit_schedule),
  };
}

// ────────────────────────────────────────────────────────────────────
// Exit schedule
// ────────────────────────────────────────────────────────────────────

/**
 * One schedule row plus its position in the worker's `exit_schedule` array.
 * The position — not the position after validation — is `i` in the provenance
 * key `exit_schedule[i].levered_irr`, so a trace always names the row the
 * worker traced, even if a malformed entry were ever dropped before it.
 */
export interface ExitScheduleRow extends ReturnsExitScheduleRow {
  /** 0-based index in the worker's array. */
  index: number;
}

/** True when `v` is shaped like one `ExitYearReturn` row. */
function isScheduleRow(v: unknown): v is ReturnsExitScheduleRow {
  if (!v || typeof v !== 'object') return false;
  const r = v as Record<string, unknown>;
  if (!isInt(r.exit_month)) return false;
  if (r.status !== 'computed' && r.status !== 'not_available') return false;
  if (r.exit_year != null && !isInt(r.exit_year)) return false;
  return true;
}

/**
 * The typed exit schedule off the returns engine's `outputs`, in the order
 * the worker emitted it, each row carrying its original `index` (the `i` in
 * the provenance key `exit_schedule[i].levered_irr`). Non-row entries (never
 * expected) are dropped without shifting the indexes that follow. Empty when
 * the run carries no schedule.
 */
export function exitSchedule(returnsOutputs: unknown): ExitScheduleRow[] {
  if (!returnsOutputs || typeof returnsOutputs !== 'object') return [];
  return exitScheduleRows((returnsOutputs as Record<string, unknown>).exit_schedule);
}

/** The reader for a preview response or any raw `exit_schedule` array. */
export function exitScheduleRows(rows: unknown): ExitScheduleRow[] {
  if (!Array.isArray(rows)) return [];
  const out: ExitScheduleRow[] = [];
  rows.forEach((r, index) => {
    if (isScheduleRow(r)) out.push({ ...r, index });
  });
  return out;
}

/** "Year 3" for a year-end row; "Month 52" for an off-cycle selected row. */
export function exitRowLabel(row: Pick<ReturnsExitScheduleRow, 'exit_year' | 'exit_month'>): string {
  return row.exit_year != null ? `Year ${row.exit_year}` : `Month ${row.exit_month}`;
}

/** What an off-cycle row is, under its label. Null for a year-end row. */
export const OFF_CYCLE_EXIT_NOTE = 'off-cycle disposition — not a projection year-end';

/**
 * The calendar year a year-end row closes in, from the revenue engine's
 * `projection_calendar_years` (set only when the deal carries an acquisition
 * close date). Undefined otherwise — never a guessed year.
 */
export function exitRowCalendarYear(
  row: Pick<ReturnsExitScheduleRow, 'exit_year'>,
  calendarYears: readonly number[] | null | undefined,
): number | undefined {
  if (row.exit_year == null || !Array.isArray(calendarYears)) return undefined;
  const y = calendarYears[row.exit_year - 1];
  return isInt(y) ? y : undefined;
}

/** The reason's label from the registry, or the raw code when this bundle's
 *  registry predates it (a regenerated registry carries the label). */
export function exitRowReasonLabel(row: Pick<ReturnsExitScheduleRow, 'status' | 'reason'>): string | null {
  if (row.status !== 'not_available') return null;
  const code = row.reason;
  if (!code) return 'Not available';
  const meta = (REASONS as Record<string, { label: string } | undefined>)[code];
  return meta?.label ?? code;
}

/** The hover text for a dashed row: the reason's label, then the engine's
 *  own detail (or the registry's explanation when there is none). */
export function exitRowReasonTitle(row: Pick<ReturnsExitScheduleRow, 'status' | 'reason' | 'reason_detail'>): string | null {
  const label = exitRowReasonLabel(row);
  if (label == null) return null;
  const detail = row.reason_detail?.trim();
  if (detail) return `${label} — ${detail}`;
  const meta = row.reason ? (REASONS as Record<string, { explanation: string } | undefined>)[row.reason] : undefined;
  return meta ? `${label} — ${meta.explanation}` : label;
}

export const EXIT_SCHEDULE_TITLE = 'Returns by exit year';
export const EXIT_SCHEDULE_SELECTED_BADGE = 'Selected exit';
export const EXIT_SCHEDULE_PEAK_BADGE = 'Peak levered IRR';
/** The card body when the run carries no schedule (standing dash rule). */
export const EXIT_SCHEDULE_MISSING = 'Awaiting model run — returns.exit_schedule is not on this run';
/** The card body while a sandbox preview from an older worker build is showing. */
export const EXIT_SCHEDULE_SANDBOX_MISSING =
  'Sandbox case — the preview returned no returns by exit year, and the canonical schedule is not shown under a sandbox case; reset to base to see it';

// ────────────────────────────────────────────────────────────────────
// The selected exit (Exit Assumptions "Exit Year")
// ────────────────────────────────────────────────────────────────────

/** The Exit Year row when the run predates the schedule — never "Year {hold}". */
export const SELECTED_EXIT_MISSING = 'Awaiting model run — returns.selected_exit_month is not on this run';

/**
 * "Year 5 · month 60" for a year-end exit, "Month 52" for an off-cycle one,
 * `null` when the run published no selected exit. The hold is NOT a fallback:
 * "Exit Year = Year {hold}" is wrong for a mid-year disposition.
 */
export function selectedExitLabel(h: Pick<HeadlineReturns, 'selectedExitMonth' | 'selectedExitYear'>): string | null {
  if (h.selectedExitMonth == null) return null;
  return h.selectedExitYear != null
    ? `Year ${h.selectedExitYear} · month ${h.selectedExitMonth}`
    : `Month ${h.selectedExitMonth}`;
}

/** The hover for the Exit Year row. */
export function selectedExitTitle(h: Pick<HeadlineReturns, 'selectedExitMonth' | 'selectedExitYear'>): string {
  if (h.selectedExitMonth == null) return SELECTED_EXIT_MISSING;
  return h.selectedExitYear != null
    ? 'The projection year-end the deal is sold at — the headline row of the returns by exit year'
    : `Month ${h.selectedExitMonth} from close — an ${OFF_CYCLE_EXIT_NOTE}; the headline row of the returns by exit year`;
}

// ────────────────────────────────────────────────────────────────────
// Hold-period default by deal type (FON-44 §3.2)
// ────────────────────────────────────────────────────────────────────

/** The `assumption_sources` tag the runner stamps on `hold_years` when the
 *  deal type set the default (7 years for development, 6 for adaptive reuse).
 *  Mirrors `SOURCE_DEAL_TYPE_DEFAULT` in engine_runner.py. */
export const HOLD_DEAL_TYPE_DEFAULT_SOURCE = 'deal_type_default';

/** Pattern: `STABILIZATION_DEFAULT_BADGE` in lib/engines/noi.ts. */
export const HOLD_DEAL_TYPE_DEFAULT_BADGE = 'Default for development deals — confirm';

/** The badge for the hold period, or `null` when its source is anything else. */
export function holdDefaultBadge(source: string | null | undefined): string | null {
  return source === HOLD_DEAL_TYPE_DEFAULT_SOURCE ? HOLD_DEAL_TYPE_DEFAULT_BADGE : null;
}

/** Hover copy for the badge. */
export const HOLD_DEAL_TYPE_DEFAULT_EXPLANATION =
  'A labelled default set by the deal type — 7 years for a ground-up development, 6 for an adaptive reuse — not a figure from the deal. Your hold period override replaces it.';
