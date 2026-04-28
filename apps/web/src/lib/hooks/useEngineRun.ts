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
import {
  notifyEngineOutputsChanged,
  recordEngineRun,
} from './useEngineOutputs';

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
  /** Called once a single-engine run completes (success or failure). */
  onComplete?: (output: EngineOutputResponse) => void;
  /** Called once a run-all kickoff finishes — receives the final run rows. */
  onAllComplete?: (rows: EngineOutputResponse[], runId: string, runtimeMs: number) => void;
  /** Called immediately when run-all kicks off (used for the floating progress strip). */
  onRunAllStarted?: (runId: string, engines: EngineName[]) => void;
  /** Streaming poll updates during a run-all. */
  onRunAllProgress?: (rows: EngineOutputResponse[]) => void;
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
  /** Live run id when a run-all is in flight; null otherwise. */
  activeRunId: string | null;
}

const POLL_INTERVAL_MS = 750;
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
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const safetyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const runStartedRef = useRef<number>(0);
  // Keep latest callbacks/options on a ref so the polling loop sees fresh
  // values without resetting the interval on every render.
  const optsRef = useRef(opts);
  optsRef.current = opts;

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
        const headline = optsRef.current.summaryFor?.(row) ?? row.summary;
        toast(
          headline ? `${engineName} complete · ${headline}` : `${engineName} complete`,
          { type: 'success' },
        );
        // Notify any mounted `useEngineOutputs(dealId)` consumers so the
        // page numbers refresh without a manual reload.
        notifyEngineOutputsChanged(dealId);
        optsRef.current.onComplete?.(row);
      } else if (row.status === 'failed') {
        setStatus('failed');
        const msg = row.error || 'engine failed';
        setError(msg);
        toast(`${engineName} failed · check logs`, { type: 'error' });
        optsRef.current.onComplete?.(row);
      } else {
        setStatus(row.status);
      }
    },
    [dealId, engineName, toast],
  );

  const pollRun = useCallback(
    (runId: string) => {
      stopPolling();
      // Slightly tighter than 1s — engine deltas land sub-second so 750ms
      // makes the progress strip feel reactive without thrashing the API.
      pollTimerRef.current = setInterval(async () => {
        try {
          const res = await api.engines.getRun(dealId, runId);
          // Stream every row to subscribers (the floating progress strip
          // morphs spinners → checks as rows complete).
          optsRef.current.onRunAllProgress?.(res.engines);

          const allDone =
            res.engines.length > 0 &&
            res.engines.every(
              (e) => e.status === 'complete' || e.status === 'failed',
            );
          if (allDone) {
            stopPolling();
            const runtimeMs = Date.now() - runStartedRef.current;
            const completeCount = res.engines.filter(
              (e) => e.status === 'complete',
            ).length;
            const failedCount = res.engines.length - completeCount;

            // Mark the per-deal run history record complete.
            recordEngineRun(dealId, {
              runId,
              startedAt: new Date(runStartedRef.current),
              completedAt: new Date(),
              engineCount: res.engines.length,
              status: failedCount > 0 ? 'failed' : 'complete',
              summary: failedCount > 0
                ? `${completeCount} of ${res.engines.length} complete · ${failedCount} failed`
                : `${completeCount} engines · ${(runtimeMs / 1000).toFixed(1)}s`,
              costUsd: 0,
            });

            // Pop one summary toast for the entire run-all (cleaner than
            // 8 individual toasts firing in sequence).
            if (failedCount === 0) {
              toast(
                `Underwriting complete · ${completeCount} engines · ${(runtimeMs / 1000).toFixed(1)}s · $0.00`,
                { type: 'success' },
              );
            } else {
              toast(
                `Underwriting partially complete · ${completeCount} of ${res.engines.length}`,
                { type: 'error' },
              );
            }

            // Find the row for the engine this hook is wired to, fall
            // back to the last row otherwise.
            const ours =
              res.engines
                .filter((e) => e.engine === engineName)
                .pop() ?? res.engines[res.engines.length - 1];
            if (ours) {
              setOutput(ours);
              setStatus(ours.status);
              if (ours.status === 'failed') {
                setError(ours.error || 'engine failed');
              }
            }
            setActiveRunId(null);
            notifyEngineOutputsChanged(dealId);
            optsRef.current.onAllComplete?.(res.engines, runId, runtimeMs);
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
          setActiveRunId(null);
          toast(`${engineName} timed out`, { type: 'error' });
        }
      }, POLL_TIMEOUT_MS);
    },
    [dealId, engineName, stopPolling, toast],
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
    runStartedRef.current = Date.now();
    try {
      if (runMode === 'all') {
        const kickoff = await api.engines.runAll(dealId);
        setActiveRunId(kickoff.run_id);
        // Seed the in-memory run history with a "running" record so the
        // floating progress strip + footer can render immediately.
        recordEngineRun(dealId, {
          runId: kickoff.run_id,
          startedAt: new Date(runStartedRef.current),
          completedAt: null,
          engineCount: kickoff.engines.length,
          status: 'running',
          summary: 'running…',
          costUsd: 0,
        });
        optsRef.current.onRunAllStarted?.(
          kickoff.run_id,
          kickoff.engines.map((e) => e.name),
        );
        pollRun(kickoff.run_id);
      } else {
        const row = await api.engines.runOne(dealId, engineName);
        handleResult(row);
      }
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'unknown error';
      setStatus('failed');
      setError(msg);
      setActiveRunId(null);
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
    activeRunId,
  };
}
