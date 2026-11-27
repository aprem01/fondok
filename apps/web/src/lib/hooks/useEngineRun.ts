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
  formatRunCompletionToast,
  formatRunHistorySummary,
  isEngineRunDone,
  tallyEngineRows,
} from '@/lib/progress';
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
 *     every 750 ms until EVERY engine the kickoff scheduled has a terminal
 *     row (`complete`, `failed` or `not_applicable`). The worker inserts a
 *     run's rows one engine at a time, so "every row we can see is
 *     terminal" is NOT the same as "the run is finished" — see
 *     `isEngineRunDone` for the stall this caused (Sam, deal b67bfdee).
 *
 * When the worker isn't configured (NEXT_PUBLIC_WORKER_URL unset) the hook
 * surfaces an info toast and stays in `idle` so the legacy mock-data UI
 * keeps working — the same defensive pattern the export cards use.
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
  // The engines the kickoff scheduled for the run being polled — the list
  // the terminal check is judged against. Empty when the worker named none
  // (then the run's own rows define it).
  const expectedEnginesRef = useRef<EngineName[]>([]);
  // Bumped on every pollRun()/stopPolling() so a tick whose request resolves
  // after polling stopped (or after a newer run started) is discarded
  // instead of completing a run twice or completing the wrong run.
  const pollGenerationRef = useRef(0);
  // Keep latest callbacks/options on a ref so the polling loop sees fresh
  // values without resetting the interval on every render.
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const stopPolling = useCallback(() => {
    pollGenerationRef.current += 1;
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
      } else if (row.status === 'not_applicable') {
        // Terminal and not a failure: the engine does not apply to the deal
        // as configured (Partnership without terms). One neutral line, the
        // worker's own sentence; the tab renders the neutral state.
        setStatus('not_applicable');
        const why = row.not_applicable?.message;
        toast(why ? `${engineName} not applicable · ${why}` : `${engineName} not applicable to this deal`, { type: 'info' });
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
    (runId: string, expectedEngines: EngineName[]) => {
      stopPolling();
      expectedEnginesRef.current = expectedEngines;
      const generation = pollGenerationRef.current;
      // Slightly tighter than 1s — engine deltas land sub-second so 750ms
      // makes the progress strip feel reactive without thrashing the API.
      pollTimerRef.current = setInterval(async () => {
        try {
          const res = await api.engines.getRun(dealId, runId);
          // Polling stopped (completion, timeout, unmount) or a newer run
          // started while this request was in flight: its rows are stale.
          if (pollGenerationRef.current !== generation) return;
          // Stream every row to subscribers (the floating progress strip
          // morphs spinners → checks as rows complete).
          optsRef.current.onRunAllProgress?.(res.engines);

          // Done only when EVERY engine the kickoff scheduled has a terminal
          // row — `complete`, `failed`, or `not_applicable` (skipped by
          // reason, not a failure; without it a run whose Partnership has no
          // terms would poll forever). Rows alone are not enough: the worker
          // writes them one engine at a time, and a tick that lands between
          // "expense complete" and "capital running" sees 3 terminal rows of
          // a 9-engine run. Judged by rows, that ended the run here ("3
          // engines", polling stopped) while the panel, judging by the
          // kickoff list, sat on "Running Capital · 3 of 9" for ever.
          const allDone = isEngineRunDone(expectedEnginesRef.current, res.engines);
          if (allDone) {
            stopPolling();
            const runtimeMs = Date.now() - runStartedRef.current;
            // FON-125 — one tally (ran / failed / skipped) feeds the history
            // record and the toast, so both agree with the progress panel:
            // a `not_applicable` engine is counted as skipped, never dropped
            // ("8 engines · 1 skipped" ↔ the panel's "8 of 9 complete · 1 skipped").
            const tally = tallyEngineRows(res.engines);

            // Mark the per-deal run history record complete.
            recordEngineRun(dealId, {
              runId,
              startedAt: new Date(runStartedRef.current),
              completedAt: new Date(),
              engineCount: res.engines.length,
              status: tally.failed > 0 ? 'failed' : 'complete',
              summary: formatRunHistorySummary(tally, runtimeMs),
              costUsd: 0,
            });

            // Pop one summary toast for the entire run-all (cleaner than
            // 8 individual toasts firing in sequence).
            const done = formatRunCompletionToast(tally, runtimeMs);
            toast(done.text, { type: done.type });

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
    if (!dealId || /^\d+$/.test(dealId) || !isWorkerConnected()) {
      // No deal to run on: a route without a deal id, the worker-less demo
      // build's sample deal, or no service configured. One honest sentence.
      toast('The model can only run on a saved deal. Open a deal from Projects to run it.', { type: 'info' });
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
        // One engine list for the strip AND the poller's terminal check, so
        // they finish on the same tick.
        const expected = kickoff.engines.map((e) => e.name);
        optsRef.current.onRunAllStarted?.(kickoff.run_id, expected);
        pollRun(kickoff.run_id, expected);
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
