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
/** Sam, 2026-11-05: a hotel not yet open has no entry cap; yield on cost is the measure. */
export const NOT_YET_OPEN = 'n/a, not yet open';

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
