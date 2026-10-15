'use client';
/**
 * Sam #6 — Market / Submarket suggestions for the New Deal wizard and the
 * Overview editor.
 *
 * Probes `GET /reference/str-markets` once. When STR's market list is loaded
 * (rows come back) Market suggests STR's markets — a pick carries STR's
 * market id — and Submarket suggests STR's submarkets (narrowed to the picked
 * market). When nothing is loaded, the worker is unreachable, or the call
 * fails, Market falls back to the name-only STR market names in lib/markets
 * (no id) and Submarket has no suggestions. Typing is always allowed.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import {
  MARKET_SOURCE_LABEL, marketOptions, resolveMarket, submarketOptions,
  type MarketOption, type StrMarketRow,
} from '@/lib/markets';

const SEARCH_LIMIT = 200;
const DEBOUNCE_MS = 200;

function fetchRows(
  params: { q?: string; str_market_id?: string; limit?: number },
  signal?: AbortSignal,
): Promise<StrMarketRow[]> {
  // `Promise.resolve().then` so a missing client (tests, older bundles)
  // rejects into the fallback instead of throwing during render.
  return Promise.resolve()
    .then(() => api.reference.strMarkets(params, signal))
    .then((rows) => (Array.isArray(rows) ? rows : []));
}

export interface StrMarketSuggestions {
  /** null while probing; true when STR's list is loaded; false = fallback. */
  referenceLoaded: boolean | null;
  marketOptions: MarketOption[];
  submarketOptions: string[];
  /** Where the Market suggestions come from, in words. */
  marketSourceLabel: string;
  /** Resolve a typed Market to STR's spelling + id (exact, unique match
   *  only); otherwise the tidied text with no id. */
  resolve: (typed: string) => Promise<{ market: string; strMarketId: string | null }>;
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
      fetchRows({ q: marketText, limit: SEARCH_LIMIT }, ctrl.signal)
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
        { q: submarketText, str_market_id: strMarketId ?? undefined, limit: SEARCH_LIMIT },
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
      return resolveMarket(typed, await fetchRows({ q: typed, limit: SEARCH_LIMIT }));
    } catch {
      return resolveMarket(typed, []);
    }
  }, [referenceLoaded]);

  return useMemo(() => ({
    referenceLoaded,
    // Loaded → STR's markets only (possibly none for this text); otherwise
    // the name-only fallback list.
    marketOptions: referenceLoaded
      ? (marketRows.length > 0 ? marketOptions(marketRows) : [])
      : marketOptions([]),
    submarketOptions: referenceLoaded ? submarketOptions(subRows, strMarketId) : [],
    marketSourceLabel: referenceLoaded ? MARKET_SOURCE_LABEL.reference : MARKET_SOURCE_LABEL.fallback,
    resolve,
  }), [referenceLoaded, marketRows, subRows, strMarketId, resolve]);
}
