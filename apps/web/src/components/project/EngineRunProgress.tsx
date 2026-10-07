'use client';

/**
 * Floating run-progress strip — anchored bottom-right, appears while a
 * `runMode='all'` is in flight (and briefly after, before auto-dismiss).
 *
 * Visualises each engine's status as the worker's run-row table evolves:
 * queued ◯, running ⟳, complete ✓ + summary, failed ✕.
 *
 * Smaller `inline` variant used for single-engine runs is rendered
 * directly above the EngineHeader card by callers when desired.
 */

import { useEffect, useMemo, useState } from 'react';
import { Check, X, Loader2, Circle } from 'lucide-react';
import {
  EngineName,
  EngineOutputResponse,
  EngineStatus,
} from '@/lib/api';
import { cn } from '@/lib/format';
import { useNow } from '@/lib/hooks/useNow';
import {
  deriveEngineStage,
  engineLabel,
  formatElapsed,
  formatLastRunTook,
} from '@/lib/progress';

interface EngineRunProgressProps {
  /** When null, the strip is hidden. */
  runId: string | null;
  /** Engines kicked off, in dependency order. */
  expectedEngines: EngineName[];
  /** Latest polled rows from the worker. */
  rows: EngineOutputResponse[];
  /** Wall-clock when the run kicked off. */
  startedAt: number | null;
  /** Run number for the header label. */
  runNumber?: number;
  /** Auto-dismiss after this many ms post-completion (default 2000). */
  dismissAfterMs?: number;
  onClose?: () => void;
  /** E-024 — measured wall time of the LAST COMPLETED run (sum of the
   *  worker's ``runtime_ms``). Rendered as "last run took 1m 58s" so the
   *  analyst has a real reference point; omitted when nothing was measured.
   *  Never an estimate of the current run. */
  lastRunTookMs?: number | null;
}

export default function EngineRunProgress({
  runId,
  expectedEngines,
  rows,
  startedAt,
  runNumber,
  dismissAfterMs = 2000,
  onClose,
  lastRunTookMs = null,
}: EngineRunProgressProps) {
  const [hidden, setHidden] = useState(false);

  // Reset visibility whenever a new run starts.
  useEffect(() => {
    if (runId) setHidden(false);
  }, [runId]);

  const rowByEngine = useMemo(() => {
    const m = new Map<EngineName, EngineOutputResponse>();
    rows.forEach((r) => m.set(r.engine, r));
    return m;
  }, [rows]);

  // E-024 — stage, k-of-n and elapsed are derived from the polled rows and
  // a 1 Hz wall-clock sample that ticks only while the run is in flight
  // (the poll itself re-renders every 750 ms, but a stalled poll must not
  // freeze the clock). Once everything is terminal the sample stops and
  // the final elapsed stays put.
  const terminal = rows.filter(
    (r) => r.status === 'complete' || r.status === 'failed',
  ).length;
  const inFlight =
    Boolean(runId) &&
    !hidden &&
    !(expectedEngines.length > 0 && terminal >= expectedEngines.length);
  const now = useNow(inFlight);
  const stage = deriveEngineStage({ expected: expectedEngines, rows, startedAt, now });
  const { completed, failed, total, allDone } = stage;
  const lastRunTook = formatLastRunTook(lastRunTookMs);

  // Auto-dismiss 2 seconds after completion.
  useEffect(() => {
    if (!allDone) return;
    const t = setTimeout(() => {
      setHidden(true);
      onClose?.();
    }, dismissAfterMs);
    return () => clearTimeout(t);
  }, [allDone, dismissAfterMs, onClose]);

  if (!runId || hidden) return null;

  return (
    <div
      className="strip-in fixed bottom-6 right-6 z-50 w-[420px] max-w-[calc(100vw-3rem)]
                 rounded-xl border border-border bg-white shadow-premium
                 overflow-hidden"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-center justify-between px-4 py-3 border-b border-border bg-card-luxe">
        <div className="flex items-center gap-2">
          {!allDone && (
            <Loader2 size={14} className="text-brand-700 animate-spin" />
          )}
          {allDone && failed === 0 && (
            <Check size={14} className="text-success-700" />
          )}
          {allDone && failed > 0 && (
            <X size={14} className="text-danger-700" />
          )}
          <div className="text-[12.5px] font-semibold text-ink-900">
            {/* E-024 — "Running Revenue · 2 of 9 complete · 0:42": the
                engine the worker is on, how many are done, and a measured
                clock. No ETA. */}
            {allDone
              ? failed > 0
                ? 'Underwriting finished with errors'
                : 'Underwriting complete'
              : stage.copy}
            {runNumber ? (
              <span className="ml-1 text-ink-500 font-medium">· Run #{runNumber}</span>
            ) : null}
          </div>
        </div>
        <button
          onClick={() => {
            setHidden(true);
            onClose?.();
          }}
          className="text-ink-500 hover:text-ink-900 transition-colors"
          aria-label="Dismiss progress"
          type="button"
        >
          <X size={14} />
        </button>
      </div>

      <ul className="px-2 py-2 space-y-0.5 max-h-[60vh] overflow-y-auto scrollbar-thin">
        {expectedEngines.map((name) => {
          const row = rowByEngine.get(name);
          const status: EngineStatus | 'queued' = row?.status ?? 'queued';
          return <EngineRow key={name} name={name} status={status} row={row} />;
        })}
      </ul>

      <div className="px-4 py-2.5 border-t border-border bg-bg/40 flex items-center justify-between text-[11px] text-ink-500">
        <span>
          <span className="font-semibold text-ink-900 tabular-nums">
            {completed}
          </span>{' '}
          of <span className="tabular-nums">{total}</span> complete
          {failed > 0 && (
            <span className="text-danger-700 ml-1 font-semibold">
              · {failed} failed
            </span>
          )}
        </span>
        <span className="tabular-nums">
          {formatElapsed(stage.elapsedSeconds)} · $0.00 spent
          {lastRunTook && (
            // Measured from the previous run's runtime_ms — a reference
            // point, not a prediction.
            <span className="ml-1 text-ink-400">· {lastRunTook}</span>
          )}
        </span>
      </div>
    </div>
  );
}

function EngineRow({
  name,
  status,
  row,
}: {
  name: EngineName;
  status: EngineStatus | 'queued';
  row: EngineOutputResponse | undefined;
}) {
  const label = engineLabel(name);
  const summary = row?.summary || '';
  const runtime = row?.runtime_ms;

  return (
    <li
      className={cn(
        'flex items-center gap-2 px-2 py-1.5 rounded-md text-[12px]',
        status === 'running' && 'bg-brand-50/50',
      )}
    >
      <span className="w-4 h-4 flex items-center justify-center flex-shrink-0">
        {status === 'queued' && (
          <Circle size={12} className="text-ink-300" />
        )}
        {status === 'running' && (
          <Loader2 size={12} className="text-brand-700 animate-spin" />
        )}
        {status === 'complete' && (
          <Check size={12} className="text-success-700 check-pop" />
        )}
        {status === 'failed' && (
          <X size={12} className="text-danger-700 check-pop" />
        )}
      </span>
      <span
        className={cn(
          'font-medium w-24 flex-shrink-0',
          status === 'queued' ? 'text-ink-500' : 'text-ink-900',
        )}
      >
        {label}
      </span>
      <span
        className={cn(
          'flex-1 min-w-0 truncate transition-opacity duration-300',
          status === 'complete' ? 'text-ink-700 opacity-100' : 'opacity-70',
        )}
      >
        {status === 'complete' && (summary || 'done')}
        {status === 'running' && '…running'}
        {status === 'queued' && '(queued)'}
        {status === 'failed' && (row?.error || 'failed')}
      </span>
      {status === 'complete' && runtime != null && (
        <span className="text-[10.5px] text-ink-500 tabular-nums flex-shrink-0">
          {runtime}ms
        </span>
      )}
    </li>
  );
}
