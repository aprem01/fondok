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
 * FON-122 (Sam, 2026-11-23 / 2026-11-26): every deal's going-in cap is struck on
 * Year 1 at the T-12's own occupancy and ADR (the penetration held at today's
 * index — no ramp toward the stabilized target), NO closure, NO displacement,
 * NOI before the FF&E reserve on Year 1's cost structure — the expense engine's
 * `run_rate_noi_at_entry` (the name is kept for payload compatibility; a deal
 * none of the three moves reads its own Year 1 there) — never on a closed,
 * displaced or ramped Year 1. Sam's QA: the earlier "undisplaced" Year 1 still
 * carried the ramp (ADR $263 against a T-12 of $233). Beside it the in-place
 * cap: the T-12's own NOI before reserve ÷ price, "—" when the basis statement
 * states none. A closed hotel keeps its refusals: a T-12 that ends more than
 * twelve months before the close is out of date ("n/a" with the dated flag),
 * and with no entry NOI at all it has no entry cap; yield on cost is the measure.
 */
export const NOT_YET_OPEN = 'n/a, not yet open';
/**
 * The entry NOI's basis, in plain sight under every row and tile that carries
 * the entry cap — ONE string on this side (the worker's `labels.ENTRY_NOI_BASIS`
 * is the same words).
 */
export const ENTRY_NOI_BASIS = 'Year 1 at T-12 occupancy and ADR · no displacement · before FF&E reserve';
/**
 * Under the Entry Cap row: the formula in brief. The basis sentence itself
 * sits ONCE, on the Entry NOI row above it (review 2026-11-27: the two
 * adjacent rows used to print the same 70-character sentence).
 */
export const ENTRY_CAP_SUB = 'Entry NOI ÷ price';
/**
 * Review 2026-11-27 — what the entry-cap tile / row says instead when the run
 * published no entry NOI and the cap fell back to the projected Year 1 ÷ price
 * (an open hotel's pre-FON-122 run): the basis above is NOT claimed for it.
 * The worker's `labels.ENTRY_CAP_PROJECTED_SUB` is the same words.
 */
export const ENTRY_CAP_PROJECTED_SUB = 'Projected Year 1 NOI ÷ price · re-run the model for the entry basis';
/**
 * Review 2026-11-27 (nit 4) — what the pair is CALLED on that fallback. The
 * exports label it "Entry NOI (Year 1, undisplaced)" / "Entry Cap (Year 1,
 * undisplaced)"; said about the bare projected Year 1 that is a false claim,
 * so every surface names the figure instead. The worker's
 * `labels.ENTRY_NOI_PROJECTED_LABEL` / `ENTRY_CAP_PROJECTED_LABEL` are the same words.
 */
export const ENTRY_NOI_PROJECTED_LABEL = 'Entry NOI (projected Year 1)';
export const ENTRY_CAP_PROJECTED_LABEL = 'Entry Cap (projected Year 1)';
/** Beside the Entry NOI figure itself on that fallback (the worker's `labels.ENTRY_NOI_PROJECTED_NOTE`). */
export const ENTRY_NOI_PROJECTED_NOTE = 'Projected Year 1 NOI · re-run the model for the entry basis';
/** Said beside a cap whose entry NOI is NOT the projected Year 1 (a closure, a displacement or the ramp was taken out). */
export const ON_UNDISPLACED_YEAR_ONE = 'on Year 1 at T-12 occupancy and ADR, without the closure, displacement or ramp';
/** @deprecated the "closing for renovation" wording is retired; the old name maps to the new text. */
export const ON_RUN_RATE_CLOSING = ON_UNDISPLACED_YEAR_ONE;
export const IN_PLACE_CAP_LABEL = 'In-place cap (T-12)';
export const IN_PLACE_CAP_SUB = 'T-12 NOI before reserve ÷ price';
export const IN_PLACE_CAP_MISSING = 'The basis statement states no twelve-month NOI before reserve (or there is no purchase price) — nothing is derived.';

/** The in-place cap: the T-12's stated NOI before reserve ÷ price; undefined without both. */
export function inPlaceCap(
  t12Ebitda: number | null | undefined,
  price: number | null | undefined,
): number | undefined {
  if (typeof t12Ebitda !== 'number' || !Number.isFinite(t12Ebitda)) return undefined;
  if (price == null || !Number.isFinite(price) || price <= 0) return undefined;
  return t12Ebitda / price;
}
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
  /** Year 1 at T-12 occupancy and ADR, no closure, no displacement, before FF&E reserve — the entry NOI the cap is struck on. */
  entryNoi: number | undefined;
  /** Entry NOI ÷ price, when the T-12 is current. */
  cap: number | undefined;
  /** The dated flag when the T-12 ends more than twelve months before the close. */
  outOfDate: string | undefined;
  /** Closed with no entry NOI (or no price) to quote a cap on. */
  notYetOpen: boolean;
  /** What prints instead of the cap, when it does not read; undefined when it does. */
  na: string | undefined;
  /** The entry NOI is not the projected Year 1: a closure, a displacement or the penetration ramp was taken out of it. */
  normalized: boolean;
}

/**
 * The entry basis (FON-122). `runRateNoi` is the engine's entry NOI — Year 1 at
 * the T-12's own occupancy and ADR, unclosed, undisplaced — and is preferred
 * whenever present; a run before this rule
 * published it only for a closed hotel, so an open hotel without it falls back
 * to its own Year 1 on the caller's side (`entryNoi` undefined here). `year1Noi`
 * (the projected Year 1) only says whether the entry NOI was normalized.
 */
export function closedAtEntry(
  closedMonths: number | null | undefined,
  runRateNoi: number | null | undefined,
  price: number | null | undefined,
  t12PeriodEnd?: string | null,
  t12MonthsBeforeClose?: number | null,
  year1Noi?: number | null,
): ClosedAtEntry {
  const closed = (closedMonths ?? 0) > 0;
  const entryNoi = typeof runRateNoi === 'number' && Number.isFinite(runRateNoi) ? runRateNoi : undefined;
  const outOfDate = closed ? t12OutOfDate(t12PeriodEnd, t12MonthsBeforeClose) : undefined;
  const cap = outOfDate === undefined && entryNoi !== undefined && price != null && price > 0 ? entryNoi / price : undefined;
  const notYetOpen = closed && outOfDate === undefined && cap === undefined;
  const na = outOfDate !== undefined ? `n/a, ${outOfDate}` : notYetOpen ? NOT_YET_OPEN : undefined;
  const normalized = entryNoi !== undefined
    && (closed || (typeof year1Noi === 'number' && Number.isFinite(year1Noi) && Math.abs(entryNoi - year1Noi) > 0.5));
  return { closed, entryNoi, cap, outOfDate, notYetOpen, na, normalized };
}

/**
 * The entry NOI on screen is the bare projected Year 1: an open hotel whose
 * run published no entry NOI (pre-FON-122). A closed hotel never falls back
 * (its cap is refused instead), so this is false for it.
 */
export function entryNoiIsProjected(atEntry: Pick<ClosedAtEntry, 'closed' | 'entryNoi'>): boolean {
  return !atEntry.closed && atEntry.entryNoi === undefined;
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
