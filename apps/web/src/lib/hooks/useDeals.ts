'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, isWorkerConnected, WorkerDeal } from '@/lib/api';
import { projects as mockProjects, Project } from '@/lib/mockData';

export interface DealsState {
  deals: WorkerDeal[];
  loading: boolean;
  error: string | null;
  /** True when reading from mockData because the worker isn't configured. */
  fromMock: boolean;
  refresh: () => void;
}

/** Adapt a mockData Project row to the worker's DealSummary shape. */
function projectToDeal(p: Project): WorkerDeal {
  return {
    id: String(p.id),
    tenant_id: 'mock-tenant',
    name: p.name,
    city: p.city,
    keys: p.keys,
    service: p.service,
    status: p.status,
    deal_stage: p.dealStage,
    risk: p.risk,
    ai_confidence: p.aiConfidence / 100,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

const mockDeals: WorkerDeal[] = mockProjects.map(projectToDeal);

export function useDeals(): DealsState {
  const [deals, setDeals] = useState<WorkerDeal[]>(
    isWorkerConnected() ? [] : mockDeals,
  );
  const [loading, setLoading] = useState<boolean>(isWorkerConnected());
  const [error, setError] = useState<string | null>(null);
  const tick = useRef(0);

  const refresh = useCallback(() => {
    if (!isWorkerConnected()) {
      setDeals(mockDeals);
      setLoading(false);
      return;
    }
    const localTick = ++tick.current;
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    api.deals
      .list(ctrl.signal)
      .then((rows) => {
        if (localTick !== tick.current) return;
        setDeals(rows);
      })
      .catch((err: unknown) => {
        if (localTick !== tick.current) return;
        if ((err as { name?: string })?.name === 'AbortError') return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
      })
      .finally(() => {
        if (localTick !== tick.current) return;
        setLoading(false);
      });
    return () => ctrl.abort();
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return {
    deals,
    loading,
    error,
    fromMock: !isWorkerConnected(),
    refresh,
  };
}
