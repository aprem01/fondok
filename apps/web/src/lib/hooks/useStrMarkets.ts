'use client';
/**
 * Sam #6 / FON-96 — Market / Submarket suggestions for the New Deal wizard
 * and the Overview editor.
 *
 * Probes `GET /reference/str-markets` once. When the taxonomy list is
 * available to this workspace (rows come back) Market suggests CoStar's
 * markets — a pick carries the market's taxonomy key — and Submarket
 * suggests CoStar's submarkets (narrowed to the picked market; otherwise
 * shown as "{submarket} · {market}"). When nothing is loaded, the list is
 * withheld (licensing), the worker is unreachable, or the call fails, Market
 * falls back to the public name-only STR market names in lib/markets (no
 * key) and Submarket has no suggestions. Typing is always allowed.
 *
 * The FON-96 response fields (`kind`, `market_key`, `market_type`, `label`)
 * are feature-detected: an older worker ignores the new query params and
 * omits the new fields, and everything here still works on the old ones.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import {
  MARKET_SOURCE_LABEL, marketOptions, resolveMarket, resolveSubmarket, submarketOptions,
  type MarketOption, type StrMarketRow, type SubmarketOption, type SubmarketPick,
} from '@/lib/markets';

const SEARCH_LIMIT = 200;
const DEBOUNCE_MS = 200;

type Params = {
  q?: string; str_market_id?: string; market_key?: string;
  kind?: 'market' | 'submarket'; limit?: number;
};

function fetchRows(params: Params, signal?: AbortSignal): Promise<StrMarketRow[]> {
  // `Promise.resolve().then` so a missing client (tests, older bundles)
  // rejects into the fallback instead of throwing during render.
  return Promise.resolve()
    .then(() => api.reference.strMarkets(params, signal))
    .then((rows) => (Array.isArray(rows) ? rows : []));
}

/** The search term for a typed market: the city part of "City, ST" / "City -
 *  ST USA", so the worker's name search returns the CoStar row ("Northhaven -
 *  TS USA") for "Northhaven, TS" and `resolveMarket` can do the exact match. */
function marketQuery(text: string): string {
  return text.split(/,| - /)[0].trim();
}

/** The submarket part of a typed "{submarket} · {market}" option. */
function submarketQuery(text: string): string {
  const sep = text.lastIndexOf(' · ');
  return (sep > 0 ? text.slice(0, sep) : text).trim();
}

export interface StrMarketSuggestions {
  /** null while probing; true when the list is available; false = fallback. */
  referenceLoaded: boolean | null;
  marketOptions: MarketOption[];
  submarketOptions: SubmarketOption[];
  /** Where the Market suggestions come from, in words. */
  marketSourceLabel: string;
  /** Resolve a typed Market to the list's spelling + key (exact, unique match
   *  only); otherwise the tidied text with no key. */
  resolve: (typed: string) => Promise<{ market: string; strMarketId: string | null }>;
  /** Resolve a typed Submarket the same way, within `marketKey` (defaults to
   *  the hook's current market key). */
  resolveSubmarket: (typed: string, marketKey?: string | null) => Promise<SubmarketPick>;
}

export function useStrMarkets(
  marketText: string,
  strMarketId: string | null,
  submarketText: string,
): StrMarketSuggestions {
  const [referenceLoaded, setReferenceLoaded] = useState<boolean | null>(null);
  const [marketRows, setMarketRows] = useState<StrMarketRow[]>([]);
  const [subRows, setSubRows] = useState<StrMarketRow[]>([]);

  useEffect(() => {
    if (!isWorkerConnected()) { setReferenceLoaded(false); return; }
    const ctrl = new AbortController();
    fetchRows({ limit: 1 }, ctrl.signal)
      .then((rows) => { if (!ctrl.signal.aborted) setReferenceLoaded(rows.length > 0); })
      .catch(() => { if (!ctrl.signal.aborted) setReferenceLoaded(false); });
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    if (!referenceLoaded) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      fetchRows({ q: marketQuery(marketText), kind: 'market', limit: SEARCH_LIMIT }, ctrl.signal)
        .then((rows) => { if (!ctrl.signal.aborted) setMarketRows(rows); })
        .catch(() => { /* suggestions are best-effort */ });
    }, DEBOUNCE_MS);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [referenceLoaded, marketText]);

  useEffect(() => {
    if (!referenceLoaded) return;
    const ctrl = new AbortController();
    const t = setTimeout(() => {
      fetchRows(
        {
          q: submarketQuery(submarketText), kind: 'submarket',
          market_key: strMarketId ?? undefined, limit: SEARCH_LIMIT,
        },
        ctrl.signal,
      )
        .then((rows) => { if (!ctrl.signal.aborted) setSubRows(rows); })
        .catch(() => { /* suggestions are best-effort */ });
    }, DEBOUNCE_MS);
    return () => { clearTimeout(t); ctrl.abort(); };
  }, [referenceLoaded, strMarketId, submarketText]);

  const resolve = useCallback(async (typed: string) => {
    if (!typed.trim() || referenceLoaded === false || !isWorkerConnected()) {
      return resolveMarket(typed, []);
    }
    try {
      return resolveMarket(typed, await fetchRows({ q: marketQuery(typed), kind: 'market', limit: SEARCH_LIMIT }));
    } catch {
      return resolveMarket(typed, []);
    }
  }, [referenceLoaded]);

  const resolveSub = useCallback(async (typed: string, marketKey?: string | null) => {
    const key = marketKey === undefined ? strMarketId : marketKey;
    if (!typed.trim() || referenceLoaded === false || !isWorkerConnected()) {
      return resolveSubmarket(typed, [], key);
    }
    try {
      const rows = await fetchRows({
        q: submarketQuery(typed), kind: 'submarket', market_key: key ?? undefined, limit: SEARCH_LIMIT,
      });
      return resolveSubmarket(typed, rows, key);
    } catch {
      return resolveSubmarket(typed, [], key);
    }
  }, [referenceLoaded, strMarketId]);

  return useMemo(() => ({
    referenceLoaded,
    // Loaded → the list's markets only (possibly none for this text);
    // otherwise the name-only fallback list.
    marketOptions: referenceLoaded
      ? (marketRows.length > 0 ? marketOptions(marketRows) : [])
      : marketOptions([]),
    submarketOptions: referenceLoaded ? submarketOptions(subRows, strMarketId) : [],
    marketSourceLabel: referenceLoaded ? MARKET_SOURCE_LABEL.reference : MARKET_SOURCE_LABEL.fallback,
    resolve,
    resolveSubmarket: resolveSub,
  }), [referenceLoaded, marketRows, subRows, strMarketId, resolve, resolveSub]);
}
