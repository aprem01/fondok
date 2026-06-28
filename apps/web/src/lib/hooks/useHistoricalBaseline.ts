'use client';

import { useEffect, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import type { HistoricalBaselineResponse } from '@/lib/api';

/**
 * Fetch the 3-5 year historical baseline for one deal — Wave 2 P2.6.
 *
 * Returns ``null`` on:
 *   * Numeric deal ids (mock-data only — the worker only knows UUIDs).
 *   * Worker not connected (env-flagged off, or no NEXT_PUBLIC_WORKER_URL).
 *   * Any fetch error (logged to console — the panel is non-blocking).
 *
 * The HistoricalBaselinePanel component already hides itself when
 * ``coverage_pct === 0`` so we don't need to filter at the hook level.
 */
export function useHistoricalBaseline(
  dealId: string | null | undefined,
): {
  baseline: HistoricalBaselineResponse | null;
  loading: boolean;
} {
  const [baseline, setBaseline] = useState<HistoricalBaselineResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!dealId) {
      setBaseline(null);
      return;
    }
    if (!isWorkerConnected()) {
      setBaseline(null);
      return;
    }
    // Numeric ids are mock projects — skip the worker call.
    if (/^\d+$/.test(dealId)) {
      setBaseline(null);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    api.validation
      .historicalBaseline(dealId, ctrl.signal)
      .then((b: HistoricalBaselineResponse) => {
        setBaseline(b);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return;
        // Non-blocking — the panel hides on null. Log so a real
        // worker fault still surfaces in the console.
        console.warn('useHistoricalBaseline:', err);
        setBaseline(null);
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [dealId]);

  return { baseline, loading };
}
