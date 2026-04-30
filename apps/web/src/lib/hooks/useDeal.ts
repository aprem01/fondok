'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  api,
  isWorkerConnected,
  WorkerDeal,
  WorkerDealStatus,
} from '@/lib/api';
import { projects as mockProjects, Project } from '@/lib/mockData';

const POLL_MS = 3000;
const POLL_STATUSES = new Set([
  'extracting',
  'processing',
  'EXTRACTING',
  'CLASSIFYING',
  'PROCESSING',
]);

function projectToDeal(p: Project): WorkerDeal {
  // The mock `Project` shape doesn't carry a brand — for the Kimpton
  // Angler demo (id=7) we fill it in from the project name so the
  // Overview tab can render "Kimpton (Upper Upscale)" without leaking
  // through the rest of the demo data.
  const brandFromName = p.id === 7 ? 'Kimpton' : null;
  return {
    id: String(p.id),
    tenant_id: 'mock-tenant',
    name: p.name,
    city: p.city,
    keys: p.keys,
    service: p.service,
    brand: brandFromName,
    status: p.status,
    deal_stage: p.dealStage,
    risk: p.risk,
    ai_confidence: p.aiConfidence / 100,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  };
}

export interface DealState {
  deal: WorkerDeal | null;
  status: WorkerDealStatus | null;
  loading: boolean;
  error: string | null;
  fromMock: boolean;
  refresh: () => void;
}

/**
 * Fetches a single deal by id. Polls /status every 3s while the deal is
 * actively being extracted/processed.
 *
 * Falls back to mockData for the Kimpton Angler deal (id=7) when the worker
 * isn't reachable, so the demo deal still renders without a backend.
 */
export function useDeal(id: string | number | null | undefined): DealState {
  const [deal, setDeal] = useState<WorkerDeal | null>(null);
  const [status, setStatus] = useState<WorkerDealStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const tick = useRef(0);

  const idStr = id == null ? '' : String(id);
  const fromMock = !isWorkerConnected() || /^\d+$/.test(idStr);

  const fetchOnce = useCallback(
    async (signal?: AbortSignal) => {
      if (!idStr) return;
      if (!isWorkerConnected()) {
        const mock = mockProjects.find((p) => String(p.id) === idStr);
        if (mock) setDeal(projectToDeal(mock));
        else setDeal(null);
        setLoading(false);
        return;
      }
      // Numeric ids belong to mockData; the worker only knows UUIDs.
      if (/^\d+$/.test(idStr)) {
        const mock = mockProjects.find((p) => String(p.id) === idStr);
        if (mock) setDeal(projectToDeal(mock));
        setLoading(false);
        return;
      }
      try {
        const [d, s] = await Promise.all([
          api.deals.get(idStr, signal),
          api.deals.status(idStr, signal).catch(() => null),
        ]);
        setDeal(d);
        setStatus(s);
        setError(null);
      } catch (err: unknown) {
        if ((err as { name?: string })?.name === 'AbortError') return;
        const msg = err instanceof Error ? err.message : String(err);
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [idStr],
  );

  const refresh = useCallback(() => {
    const localTick = ++tick.current;
    const ctrl = new AbortController();
    setLoading(true);
    void fetchOnce(ctrl.signal).then(() => {
      if (localTick !== tick.current) ctrl.abort();
    });
    return () => ctrl.abort();
  }, [fetchOnce]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Poll status while deal is processing/extracting.
  useEffect(() => {
    if (!isWorkerConnected() || !idStr || /^\d+$/.test(idStr)) return;
    const dealStatus = status?.status ?? deal?.status;
    if (!dealStatus || !POLL_STATUSES.has(dealStatus)) return;
    const ctrl = new AbortController();
    const t = setInterval(() => {
      api.deals
        .status(idStr, ctrl.signal)
        .then((s) => setStatus(s))
        .catch(() => {});
    }, POLL_MS);
    return () => {
      clearInterval(t);
      ctrl.abort();
    };
  }, [idStr, status?.status, deal?.status]);

  return { deal, status, loading, error, fromMock, refresh };
}
