'use client';

/**
 * Computed-value provenance context — loads GET /deals/{id}/provenance ONCE
 * per deal and exposes a synchronous getTrace(engine, path) so any
 * <Traced engine=… path=…> on any screen can show the formula + inputs
 * behind a modeled number (NOI, GOP, DSCR, equity multiple, …) with zero
 * extra fetches. Sibling to useDealProvenance (which covers INPUT
 * assumptions); this covers computed OUTPUTS. FON-25 / FON-27.
 */

import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import type { DealProvenanceResponse, ValueTrace } from '@/lib/api';

interface TraceCtx {
  get: (engine: string, path: string) => ValueTrace | null;
  ready: boolean;
}

const Ctx = createContext<TraceCtx>({ get: () => null, ready: false });

export function ValueTraceProvider({
  dealId,
  children,
}: {
  dealId: string;
  children: ReactNode;
}) {
  const [data, setData] = useState<DealProvenanceResponse | null>(null);

  useEffect(() => {
    if (!isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) return;
    const ac = new AbortController();
    api.deals
      .provenance(dealId, ac.signal)
      .then(setData)
      .catch(() => {});
    return () => ac.abort();
  }, [dealId]);

  const value = useMemo<TraceCtx>(
    () => ({
      ready: !!data,
      get: (engine: string, path: string) => {
        const eng = data?.engines?.[engine];
        if (!eng) return null;
        return eng[path] ?? null;
      },
    }),
    [data],
  );

  return createElement(Ctx.Provider, { value }, children);
}

/** Resolve one computed value's trace, or null when unknown / no provider. */
export function useTrace(
  engine: string | undefined,
  path: string | undefined,
): ValueTrace | null {
  const ctx = useContext(Ctx);
  return engine && path ? ctx.get(engine, path) : null;
}

/** Resolve a trace AND the full engine map, so a tooltip can follow
 *  `traces_to` chains one hop for a nested input's own formula. */
export function useTraceGraph(engine: string | undefined): {
  get: (path: string) => ValueTrace | null;
} {
  const ctx = useContext(Ctx);
  return {
    get: (path: string) => (engine ? ctx.get(engine, path) : null),
  };
}
