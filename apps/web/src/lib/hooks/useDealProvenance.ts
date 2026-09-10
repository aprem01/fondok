'use client';

/**
 * Deal provenance context — loads GET /deals/{id}/assumption_sources ONCE per
 * deal and exposes a synchronous getSource(key) so any <Sourced sourceKey=…>
 * on any screen can show where a value came from with zero extra fetches.
 */

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { createElement } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import type { AssumptionSourcesResponse } from '@/lib/api';
import { isReasonCode, type ReasonCode } from '@/lib/ontology/reasons.generated';

export interface ResolvedSource {
  /**
   * SOURCE_* label. Empty string on the reason-only entry described in
   * ``get`` below — falsy, exactly like the `null` those callers used to
   * receive, so every `resolved?.source ? …` guard behaves as it did.
   */
  source: string;
  value: number | string | boolean | null;
  docId?: string;
  /**
   * Phase 4.4 — why this key has no usable value, when the worker says so
   * (`assumption_sources.reasons`, a bare ``ReasonCode``). Absent on older
   * worker builds; `useRefusal` in `components/help/Refused` reads it.
   */
  reason?: ReasonCode | null;
}

interface ProvCtx {
  get: (key: string) => ResolvedSource | null;
  /** True once the assumption_sources payload has loaded. */
  ready: boolean;
  /** True once the fetch has finished (success OR failure), or when there is
   *  nothing to fetch (no worker / mock deal). Lets a consumer tell "still
   *  checking" from "the worker returned no tag for this key". */
  settled: boolean;
}

const Ctx = createContext<ProvCtx>({ get: () => null, ready: false, settled: false });

export function ProvenanceProvider({
  dealId,
  children,
}: {
  dealId: string;
  children: ReactNode;
}) {
  const [data, setData] = useState<AssumptionSourcesResponse | null>(null);
  const [settled, setSettled] = useState(false);

  useEffect(() => {
    if (!isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) {
      setSettled(true);
      return;
    }
    const ac = new AbortController();
    setSettled(false);
    api.deals
      .assumptionSources(dealId, ac.signal)
      .then(setData)
      .catch(() => {})
      .finally(() => {
        if (!ac.signal.aborted) setSettled(true);
      });
    return () => ac.abort();
  }, [dealId]);

  const value = useMemo<ProvCtx>(
    () => ({
      ready: !!data,
      settled,
      get: (key: string) => {
        if (!data) return null;
        const src = data.sources?.[key];
        const raw = data.reasons?.[key];
        const reason = isReasonCode(raw) ? raw : null;
        // A key the worker refused but never tagged with a source still has
        // something to say, so it resolves — with an EMPTY source label,
        // which is falsy and therefore invisible to every existing
        // `resolved?.source` guard. No source and no reason stays null.
        if (src == null && reason == null) return null;
        return {
          source: src == null ? '' : typeof src === 'string' ? src : String(src),
          value: data.values?.[key] ?? null,
          docId: data.source_documents?.[key],
          reason,
        };
      },
    }),
    [data, settled],
  );

  return createElement(Ctx.Provider, { value }, children);
}

/** Load state of the deal's provenance map — see ``ProvCtx.settled``. */
export function useProvenanceState(): { ready: boolean; settled: boolean } {
  const { ready, settled } = useContext(Ctx);
  return { ready, settled };
}

/** Resolve one assumption key's source, or null when unknown / no provider. */
export function useSource(key: string | undefined): ResolvedSource | null {
  const ctx = useContext(Ctx);
  return key ? ctx.get(key) : null;
}
