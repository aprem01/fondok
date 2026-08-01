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

export interface ResolvedSource {
  source: string;
  value: number | string | boolean | null;
  docId?: string;
}

interface ProvCtx {
  get: (key: string) => ResolvedSource | null;
  ready: boolean;
}

const Ctx = createContext<ProvCtx>({ get: () => null, ready: false });

export function ProvenanceProvider({
  dealId,
  children,
}: {
  dealId: string;
  children: ReactNode;
}) {
  const [data, setData] = useState<AssumptionSourcesResponse | null>(null);

  useEffect(() => {
    if (!isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) return;
    const ac = new AbortController();
    api.deals
      .assumptionSources(dealId, ac.signal)
      .then(setData)
      .catch(() => {});
    return () => ac.abort();
  }, [dealId]);

  const value = useMemo<ProvCtx>(
    () => ({
      ready: !!data,
      get: (key: string) => {
        if (!data) return null;
        const src = data.sources?.[key];
        if (src == null) return null;
        return {
          source: typeof src === 'string' ? src : String(src),
          value: data.values?.[key] ?? null,
          docId: data.source_documents?.[key],
        };
      },
    }),
    [data],
  );

  return createElement(Ctx.Provider, { value }, children);
}

/** Resolve one assumption key's source, or null when unknown / no provider. */
export function useSource(key: string | undefined): ResolvedSource | null {
  const ctx = useContext(Ctx);
  return key ? ctx.get(key) : null;
}
