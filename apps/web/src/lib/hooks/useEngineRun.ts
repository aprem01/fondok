'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  api,
  EngineName,
  EngineOutputResponse,
  EngineStatus,
  isWorkerConnected,
  WorkerError,
} from '@/lib/api';
import { useToast } from '@/components/ui/Toast';

/**
 * Wires the EngineHeader's Run Model button to the worker engine API.
 *
 * Two modes:
 *   - `runMode: 'single'` (default) hits POST /deals/{id}/engines/{name}/run
 *     and the worker returns the persisted row synchronously (engines are
 *     deterministic and run in <1s on Kimpton). No polling needed.
 *   - `runMode: 'all'` hits POST /deals/{id}/engines/run which kicks off
 *     a background task; we then poll GET /deals/{id}/engines/run/{run_id}
 *     every second until every engine row is `complete` or `failed`.
 *
 * When the worker isn't configured (NEXT_PUBLIC_WORKER_URL unset) the hook
 * surfaces an info toast and stays in `idle` so the legacy mock-data UI
 * keeps working — same defensive pattern ExportTab uses.
 */
export interface UseEngineRunOptions {
  runMode?: 'single' | 'all';
  /** Optional headline override; otherwise we use `output.summary`. */
  summaryFor?: (output: EngineOutputResponse) => string;
}

export interface UseEngineRunResult {
  status: EngineStatus | 'idle';
  output: EngineOutputResponse | null;
  /** Compact one-liner (e.g. "IRR 23.0% · Multiple 2.37x"). */
  summary: string;
  error: string | null;
  /** True once Run Model has been clicked and produced a successful output. */
  complete: boolean;
  run: () => Promise<void>;
}

const POLL_INTERVAL_MS = 1000;
const POLL_TIMEOUT_MS = 60_000;

export function useEngineRun(
  dealId: string,
  engineName: EngineName,
  opts: UseEngineRunOptions = {},
): UseEngineRunResult {
  const runMode = opts.runMode ?? 'single';
  const { toast } = useToast();
  const [status, setStatus] = useState<EngineStatus | 'idle'>('idle');
  const [output, setOutput] = useState<EngineOutputResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (safetyTimerRef.current) {
      clearTimeout(safetyTimerRef.current);
      safetyTimerRef.current = null;
    }
  }, []);

  // Always tear down timers on unmount so we don't poll into a closed view.
  useEffect(() => stopPolling, [stopPolling]);

  const handleResult = useCallback(
    (row: EngineOutputResponse) => {
      setOutput(row);
      if (row.status === 'complete') {
        setStatus('complete');
        const headline = opts.summaryFor?.(row) ?? row.summary;
        toast(
          headline ? `${engineName} complete · ${headline}` : `${engineName} complete`,
          { type: 'success' },
        );
      } else if (row.status === 'failed') {
        setStatus('failed');
        const msg = row.error || 'engine failed';
        setError(msg);
        toast(`${engineName} failed: ${msg}`, { type: 'error' });
      } else {
        setStatus(row.status);
      }
    },
    [engineName, opts, toast],
  );

  const pollRun = useCallback(
    (runId: string) => {
      stopPolling();
      pollTimerRef.current = setInterval(async () => {
        try {
          const res = await api.engines.getRun(dealId, runId);
          // Find the row for this engine; use the most recent.
          const row = res.engines
            .filter((e) => e.engine === engineName)
            .pop();
          if (row && (row.status === 'complete' || row.status === 'failed')) {
            stopPolling();
            handleResult(row);
          }
        } catch (e: unknown) {
          // Transient errors: keep polling. Hard 4xx/5xx will surface
          // when the safety timeout fires.
          if (e instanceof WorkerError && e.status === 404) {
            // Run row not yet visible — keep polling.
            return;
          }
        }
      }, POLL_INTERVAL_MS);
      safetyTimerRef.current = setTimeout(() => {
        if (pollTimerRef.current) {
          stopPolling();
          setStatus((s) => (s === 'running' ? 'failed' : s));
          setError('engine run timed out — refresh the page');
          toast(`${engineName} timed out`, { type: 'error' });
        }
      }, POLL_TIMEOUT_MS);
    },
    [dealId, engineName, handleResult, stopPolling, toast],
  );

  const run = useCallback(async () => {
    if (!dealId) {
      toast('Deal id missing — open the deal page first', { type: 'error' });
      return;
    }
    if (!isWorkerConnected()) {
      toast('Worker not connected — engine cannot run', { type: 'info' });
      return;
    }
    setStatus('running');
    setError(null);
    try {
      if (runMode === 'all') {
        const kickoff = await api.engines.runAll(dealId);
        pollRun(kickoff.run_id);
      } else {
        const row = await api.engines.runOne(dealId, engineName);
        handleResult(row);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'unknown error';
      setStatus('failed');
      setError(msg);
      toast(`${engineName} run failed: ${msg}`, { type: 'error' });
    }
  }, [dealId, engineName, handleResult, pollRun, runMode, toast]);

  const summary = output?.summary ?? '';
  return {
    status,
    output,
    summary,
    error,
    complete: status === 'complete',
    run,
  };
}
