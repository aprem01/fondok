'use client';

/**
 * FON-97 — the deal's P&L benchmark block, fetched once.
 *
 * Both the Future P&L's in-table Benchmark column and the collapsed
 * "Benchmark detail" panel read ``pnl_benchmark`` from
 * ``GET /deals/{id}/market-data``. This hook is the single fetch they share
 * (one request per deal mount, not one per consumer), parsed by
 * ``parseBenchmarkBlock`` so a worker that predates R-067, a failed read or a
 * deal with no benchmark all resolve to ``null`` — the column is then absent,
 * never blank or placeholder-filled.
 *
 * Read-only: nothing here writes an override or feeds an engine.
 */

import { useEffect, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import { parseBenchmarkBlock, type BenchmarkBlock } from '@/components/project/pl/BenchmarkComparison';

export interface DealBenchmarkState {
  /** The parsed block, or null (no benchmark / not live / read failed). */
  block: BenchmarkBlock | null;
  /** True once the read has settled (either way). */
  settled: boolean;
}

export function useDealBenchmark(dealId: string): DealBenchmarkState {
  const [state, setState] = useState<DealBenchmarkState>({ block: null, settled: false });
  // Live worker + a real deal id (the numeric ids are the offline fixtures).
  const liveMode = isWorkerConnected() && !!dealId && !/^\d+$/.test(dealId);

  useEffect(() => {
    const fetchMarketData = api?.market?.data;
    if (!liveMode || typeof fetchMarketData !== 'function') {
      setState({ block: null, settled: true });
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    Promise.resolve(fetchMarketData(dealId, ctrl.signal))
      .then((json) => {
        if (!cancelled) setState({ block: parseBenchmarkBlock(json), settled: true });
      })
      .catch(() => {
        // No benchmark column on a failed read — never a placeholder.
        if (!cancelled) setState({ block: null, settled: true });
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [dealId, liveMode]);

  return state;
}
