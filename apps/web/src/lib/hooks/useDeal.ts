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
// E-003 (FON-59) — while a deal is still extracting, re-read the deal row
// (not just /status) this often, so keys / brand / city the worker fills in
// from the OM reach the header before extraction finishes.
const DEAL_REFRESH_MS = 15_000;
const POLL_STATUSES = new Set([
  'extracting',
  'processing',
  'EXTRACTING',
  'CLASSIFYING',
  'PROCESSING',
]);

function projectToDeal(p: Project): WorkerDeal {
  // The mock `Project` shape doesn't carry a brand.
  return {
    id: String(p.id),
    tenant_id: 'mock-tenant',
    name: p.name,
    city: p.city,
    keys: p.keys,
    service: p.service,
    brand: null,
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
  // Wall-clock time of the last deal-row read — gates the in-flight refresh.
  const lastDealFetch = useRef(0);

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
        lastDealFetch.current = Date.now();
        setDeal(d);
        setStatus(s);
        setError(null);
      } catch (err: unknown) {
        const errName = (err as { name?: string })?.name;
        if (errName === 'AbortError') return;
        // Wave 4 reliability fix (Bug #3) — map worker-timeout into a
        // friendly, actionable message instead of the raw stack text.
        // The deal page reads this string and renders it in the error
        // card (UUID load gate). Keep ``TimeoutError`` as the prefix
        // so downstream callers can still pattern-match on it.
        if (errName === 'TimeoutError') {
          setError(
            'TimeoutError: The worker is busy — your upload is still extracting. Try again in 30 seconds.',
          );
        } else {
          const msg = err instanceof Error ? err.message : String(err);
          setError(msg);
        }
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
  //
  // E-003 (FON-59) — the wizard creates the deal with keys / brand / city
  // unset and the worker fills them from the OM extraction a minute later.
  // Status polling alone never re-read the deal row, so the header kept the
  // creation-time nulls ("0 keys") while Overview, reading engine output,
  // showed 132. Two refetch rules now: the deal row is re-read every
  // DEAL_REFRESH_MS while extraction runs, and once more the moment the
  // status leaves the polling set. Mock / numeric ids are untouched.
  const pollable = isWorkerConnected() && !!idStr && !/^\d+$/.test(idStr);
  const dealStatus = status?.status ?? deal?.status;
  const polling = pollable && !!dealStatus && POLL_STATUSES.has(dealStatus);

  useEffect(() => {
    if (!polling) return;
    const ctrl = new AbortController();
    const t = setInterval(() => {
      api.deals
        .status(idStr, ctrl.signal)
        .then((s) => setStatus(s))
        .catch(() => {});
      if (Date.now() - lastDealFetch.current >= DEAL_REFRESH_MS) {
        lastDealFetch.current = Date.now();
        api.deals
          .get(idStr, ctrl.signal)
          .then((d) => setDeal(d))
          .catch(() => {});
      }
    }, POLL_MS);
    return () => {
      clearInterval(t);
      ctrl.abort();
    };
  }, [idStr, polling]);

  // Leaving the polling set → one more full fetch, so keys, brand and city
  // reflect what the worker extracted. A deal that loads already settled
  // never passes through here, and a status-only poll cannot carry those
  // fields, so this is the only place the header's final values can land.
  const wasPolling = useRef(false);
  useEffect(() => {
    if (wasPolling.current && !polling && pollable) {
      wasPolling.current = false;
      const ctrl = new AbortController();
      void fetchOnce(ctrl.signal);
      return () => ctrl.abort();
    }
    wasPolling.current = polling;
  }, [polling, pollable, fetchOnce]);

  return { deal, status, loading, error, fromMock, refresh };
}
