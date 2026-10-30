'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, isWorkerConnected, userMessage, WorkerDeal } from '@/lib/api';

export interface DealsState {
  deals: WorkerDeal[];
  loading: boolean;
  error: string | null;
  /**
   * False when `NEXT_PUBLIC_WORKER_URL` is unset. There is then nothing to
   * list — the page renders `WorkerNotConfigured`, never a sample deal.
   */
  workerConfigured: boolean;
  refresh: () => void;
}

export function useDeals(): DealsState {
  const workerConfigured = isWorkerConnected();
  const [deals, setDeals] = useState<WorkerDeal[]>([]);
  const [loading, setLoading] = useState<boolean>(workerConfigured);
  const [error, setError] = useState<string | null>(null);
  const tick = useRef(0);

  const refresh = useCallback(() => {
    if (!isWorkerConnected()) {
      setDeals([]);
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
        const msg = userMessage(err);
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
    workerConfigured,
    refresh,
  };
}
