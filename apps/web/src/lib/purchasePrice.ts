/**
 * W5 "price" — the purchase price is an analyst INPUT the model reads, never a
 * figure the model derives. One place for the rules every surface that shows
 * or edits it shares (Overview → Entry Valuation, CAPEX → Deal Summary, and
 * the CAPEX inputs card shown when the capital engine refused to run):
 *
 *  - where the price on screen came from (analyst override > deal record > the
 *    OM's stated asking price — the worker loader's precedence);
 *  - whether the capital engine refused for a missing identity input;
 *  - the Entry Cap Rate, which is DERIVED from the price (Entry NOI ÷ Purchase
 *    Price) and never the other way round. A price is never inferred from a
 *    broker cap rate.
 */
import type { EngineOutputResponse } from '@/lib/api';
import type { ResolvedSource } from '@/lib/hooks/useDealProvenance';
import { noteOf } from '@/lib/overrideNote';
import { isReasonCode, type ReasonCode } from '@/lib/ontology/reasons.generated';

/** The `field_overrides` key the price editor writes (`{value, note}`). */
export const PURCHASE_PRICE_KEY = 'purchase_price';

/** The worker reason code a price-less / key-less deal is refused with. */
export const MISSING_IDENTITY_INPUTS = 'missing_identity_inputs';

/** The one formula the Entry Cap Rate is calculated by. */
export const ENTRY_CAP_FORMULA = 'Entry NOI ÷ Purchase Price';

/** Where the purchase price is entered — the provenance "from" line. */
export const PURCHASE_PRICE_INPUT_FROM = 'Input · Overview / CAPEX';

/** The sources a purchase price may carry, in precedence order. */
export type PurchasePriceSourceId = 'analyst_override' | 'deal_row' | 'om_broker';

export interface PurchasePriceInput {
  /** The price to show / pre-fill, or undefined (rendered "—"). */
  value: number | undefined;
  /** Where it came from; null when there is no price. */
  source: PurchasePriceSourceId | null;
  /** The analyst's justification, on an override. */
  note: string | null;
  /** The OM row behind an `om_broker` price. */
  page: number | null;
  documentId: string | null;
  filename: string | null;
}

const num = (v: unknown): number | undefined =>
  typeof v === 'number' && Number.isFinite(v) && v > 0 ? v : undefined;

/** The scalar behind a `{value, note}` override envelope (or a bare value). */
function overrideValue(entry: unknown): number | undefined {
  if (entry && typeof entry === 'object' && 'value' in entry) {
    return num((entry as { value: unknown }).value);
  }
  return num(entry);
}

/**
 * The purchase price on screen and its source, from what the deal actually
 * carries: the analyst's saved override first (so a just-saved price shows
 * before the re-run lands), then the value the worker's loader resolves today
 * (`assumption_sources`: deal record or the OM's asking price — the value the
 * NEXT run will use, with its own badge and page), then the capital engine's
 * price on the last run with no badge (its source at run time is not known
 * here, and pairing it with today's source could mis-attribute it). Nothing
 * else — no default, no derivation.
 */
export function resolvePurchasePrice({
  overrides,
  enginePrice,
  resolved,
}: {
  overrides: Record<string, unknown> | null | undefined;
  enginePrice: number | undefined;
  resolved: ResolvedSource | null | undefined;
}): PurchasePriceInput {
  const empty: PurchasePriceInput = {
    value: undefined, source: null, note: null, page: null, documentId: null, filename: null,
  };
  const entry = overrides?.[PURCHASE_PRICE_KEY];
  const overridden = overrideValue(entry);
  if (overridden !== undefined) {
    return { ...empty, value: overridden, source: 'analyst_override', note: noteOf(entry) };
  }
  const src = resolved?.source;
  const known: PurchasePriceSourceId | null =
    src === 'deal_row' || src === 'om_broker' ? src : null;
  const current = known ? num(resolved?.value) : undefined;
  if (known && current !== undefined) {
    return {
      ...empty,
      value: current,
      source: known,
      page: known === 'om_broker' ? (resolved?.field?.page ?? null) : null,
      documentId: known === 'om_broker' ? (resolved?.field?.document_id ?? resolved?.docId ?? null) : null,
      filename: known === 'om_broker' ? (resolved?.field?.filename ?? null) : null,
    };
  }
  return enginePrice !== undefined ? { ...empty, value: enginePrice } : empty;
}

/**
 * The identity inputs (`keys` / `purchase_price`) an engine row was refused
 * for, or null when the row did not fail for that reason.
 */
export function missingIdentityInputs(
  row: EngineOutputResponse | null | undefined,
): string[] | null {
  if (!row || row.status !== 'failed') return null;
  const f = row.failure;
  if (!f || f.reason !== MISSING_IDENTITY_INPUTS) return null;
  return Array.isArray(f.missing) ? f.missing : [];
}

/** Parse the analyst's typed price ("36400000", "$36,400,000"); null if not a positive amount. */
export function parsePurchasePrice(s: string): number | null {
  const n = parseFloat(s.replace(/[,$\s]/g, ''));
  return Number.isFinite(n) && n > 0 ? n : null;
}

export type EntryCapRate =
  | { value: number; reason: null; detail: null }
  | { value: undefined; reason: ReasonCode; detail: string };

/**
 * Entry Cap Rate = Entry NOI ÷ Purchase Price — or a dash with the TRUE
 * reason when an input is missing. Never computed the other way round.
 *
 *  - no price → `missing_identity_inputs` (the deal has none to divide by);
 *  - no Entry NOI and the expense engine never ran → `stale_run` (run the
 *    model to compute it);
 *  - no Entry NOI and the expense row failed → that row's own reason;
 *  - otherwise (it completed without a Year-1 NOI) → `engine_skipped`.
 */
/**
 * FON-122 (Sam, 2026-11-05): a hotel closed at purchase quotes its going-in cap on
 * the same normalized entry / run-rate NOI an open hotel does — Year 1 priced
 * without the closure (the expense engine's `run_rate_noi_at_entry`) — never on
 * its closed Year 1. A T-12 that ends more than twelve months before the close is
 * out of date: "n/a" with the dated flag. With no entry NOI at all it has no
 * entry cap; yield on cost is the measure.
 */
export const NOT_YET_OPEN = 'n/a, not yet open';
export const ON_RUN_RATE_CLOSING = 'on run-rate NOI (closing for renovation)';
/** Exactly twelve months is still current — the rule is "more than twelve". */
export const T12_MAX_MONTHS_BEFORE_CLOSE = 12;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * The dated flag for a T-12 older than a year at the close
 * ("T-12 ends Mar 2025, 14 months before close"); undefined when it is current
 * or either date is unknown. Mirrors the worker's `t12_out_of_date`.
 */
export function t12OutOfDate(
  periodEnd: string | null | undefined,
  monthsBeforeClose: number | null | undefined,
): string | undefined {
  if (typeof monthsBeforeClose !== 'number' || !Number.isFinite(monthsBeforeClose)) return undefined;
  if (monthsBeforeClose <= T12_MAX_MONTHS_BEFORE_CLOSE) return undefined;
  const m = typeof periodEnd === 'string' ? /^(\d{4})-(\d{2})/.exec(periodEnd) : null;
  const month = m ? MONTHS[Number(m[2]) - 1] : undefined;
  const ends = month ? `ends ${month} ${m![1]}` : 'ends';
  return `T-12 ${ends}, ${monthsBeforeClose} months before close`;
}

export interface ClosedAtEntry {
  /** The hotel is closed at purchase. */
  closed: boolean;
  /** Year 1 priced without the closure — the entry NOI the cap is struck on. */
  entryNoi: number | undefined;
  /** Entry NOI ÷ price, when the T-12 is current. */
  cap: number | undefined;
  /** The dated flag when the T-12 ends more than twelve months before the close. */
  outOfDate: string | undefined;
  /** Closed with no entry NOI (or no price) to quote a cap on. */
  notYetOpen: boolean;
  /** What prints instead of the cap, when it does not read; undefined when it does. */
  na: string | undefined;
}

export function closedAtEntry(
  closedMonths: number | null | undefined,
  runRateNoi: number | null | undefined,
  price: number | null | undefined,
  t12PeriodEnd?: string | null,
  t12MonthsBeforeClose?: number | null,
): ClosedAtEntry {
  const closed = (closedMonths ?? 0) > 0;
  const entryNoi = closed && typeof runRateNoi === 'number' && Number.isFinite(runRateNoi) ? runRateNoi : undefined;
  const outOfDate = closed ? t12OutOfDate(t12PeriodEnd, t12MonthsBeforeClose) : undefined;
  const cap = outOfDate === undefined && entryNoi !== undefined && price != null && price > 0 ? entryNoi / price : undefined;
  const notYetOpen = closed && outOfDate === undefined && cap === undefined;
  const na = outOfDate !== undefined ? `n/a, ${outOfDate}` : notYetOpen ? NOT_YET_OPEN : undefined;
  return { closed, entryNoi, cap, outOfDate, notYetOpen, na };
}

export function entryCapRate(
  entryNoi: number | undefined,
  price: number | undefined,
  expenseRow?: EngineOutputResponse | null,
): EntryCapRate {
  if (price === undefined || !Number.isFinite(price) || price <= 0) {
    return {
      value: undefined,
      reason: 'missing_identity_inputs',
      detail: 'No purchase price — enter it on the Overview or CAPEX tab.',
    };
  }
  if (entryNoi !== undefined && Number.isFinite(entryNoi)) {
    return { value: entryNoi / price, reason: null, detail: null };
  }
  if (!expenseRow) {
    return {
      value: undefined,
      reason: 'stale_run',
      detail: 'The model has not run on this deal yet — run it to compute the Entry NOI.',
    };
  }
  if (expenseRow.status === 'failed') {
    const r = expenseRow.failure?.reason;
    return {
      value: undefined,
      reason: isReasonCode(r) ? r : 'engine_skipped',
      detail: expenseRow.failure?.message ?? expenseRow.error ?? 'The P&L (expense) engine did not complete.',
    };
  }
  return {
    value: undefined,
    reason: 'engine_skipped',
    detail: 'No Entry NOI — the P&L engine produced no Year-1 NOI.',
  };
}
