'use client';

/**
 * "Last run" footer + run history popover. Renders below the engine tab
 * content so the user always sees: when did the model last run, how long
 * did it take, what was the cost. Click to expand the last 5 runs.
 *
 * Pulls from the in-memory run history maintained by `useEngineOutputs`
 * (populated by `useEngineRun`). On the Kimpton demo deal we synthesize
 * a fake completed run so the footer feels alive without a worker hit.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Clock, ChevronUp, ChevronDown } from 'lucide-react';
import {
  EngineRunRecord,
  recordEngineRun,
  useEngineRunHistory,
} from '@/lib/hooks/useEngineOutputs';
import { cn } from '@/lib/format';

interface Props {
  dealId: string | number;
  /** When true, seed a fake completed run on first mount so the demo
      surfaces "Last run · 3 minutes ago · 8 engines" without a worker. */
  seedDemo?: boolean;
}

export default function EngineRunHistory({ dealId, seedDemo = false }: Props) {
  const history = useEngineRunHistory(dealId);
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const seededRef = useRef(false);

  // Seed demo data exactly once if requested.
  useEffect(() => {
    if (!seedDemo || seededRef.current) return;
    if (history.length > 0) {
      seededRef.current = true;
      return;
    }
    seededRef.current = true;
    const start = new Date(Date.now() - 3 * 60_000); // 3 minutes ago
    recordEngineRun(String(dealId), {
      runId: 'demo-1',
      startedAt: start,
      completedAt: new Date(start.getTime() + 1100),
      engineCount: 8,
      status: 'complete',
      summary: '8 engines · 1.1s',
      costUsd: 0,
    });
  }, [dealId, seedDemo, history.length]);

  // Click-outside to close the popover.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!popoverRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const latest = history[0];
  if (!latest) return null;

  const runNumber = history.length;

  return (
    <div className="relative mt-5" ref={popoverRef}>
      <button
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'w-full flex items-center justify-between gap-3 px-4 py-2.5',
          'rounded-md border border-border bg-white shadow-card-hover',
          'text-[11.5px] text-ink-500 hover:text-ink-900 transition-colors',
          'card-interactive',
        )}
        type="button"
      >
        <span className="flex items-center gap-2">
          <Clock size={12} className="text-ink-500" />
          <span>
            Last run:{' '}
            <span className="font-medium text-ink-900">
              {formatRelative(latest.startedAt)}
            </span>
            <span className="mx-2 text-ink-300">·</span>
            <span className="tabular-nums">{latest.engineCount} engines</span>
            <span className="mx-2 text-ink-300">·</span>
            <span className="tabular-nums">$0.00 cost</span>
            <span className="mx-2 text-ink-300">·</span>
            <span className="tabular-nums">Run #{runNumber}</span>
          </span>
        </span>
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
      </button>

      {open && (
        <div className="absolute bottom-full left-0 right-0 mb-2 rounded-lg border border-border bg-white shadow-premium fade-in-up z-30">
          <div className="px-4 py-2.5 border-b border-border text-[11px] uppercase tracking-wide text-ink-500 font-semibold">
            Run history
          </div>
          <ul>
            {history.slice(0, 5).map((r, i) => (
              <HistoryRow
                key={r.runId}
                run={r}
                index={history.length - i}
                isLatest={i === 0}
              />
            ))}
          </ul>
          <div className="px-4 py-2 border-t border-border text-[10.5px] text-ink-500 italic">
            History persists for the current browser session.
          </div>
        </div>
      )}
    </div>
  );
}

function HistoryRow({
  run,
  index,
  isLatest,
}: {
  run: EngineRunRecord;
  index: number;
  isLatest: boolean;
}) {
  const tone =
    run.status === 'failed'
      ? 'text-danger-700'
      : run.status === 'running'
        ? 'text-brand-700'
        : 'text-success-700';
  return (
    <li className="flex items-center justify-between gap-3 px-4 py-2 text-[12px] border-b border-border/50 last:border-0">
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-medium text-ink-900 tabular-nums">
          #{index}
        </span>
        <span className={cn('text-[10.5px] uppercase tracking-wide font-semibold', tone)}>
          {run.status}
        </span>
        {isLatest && (
          <span className="text-[10px] uppercase tracking-wide bg-brand-50 text-brand-700 px-1.5 py-0.5 rounded">
            latest
          </span>
        )}
      </div>
      <div className="flex items-center gap-3 text-ink-500 tabular-nums">
        <span>{formatTime(run.startedAt)}</span>
        <span className="text-ink-300">·</span>
        <span className="text-ink-700">{run.summary}</span>
      </div>
    </li>
  );
}

function formatRelative(d: Date): string {
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? '' : 's'} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? '' : 's'} ago`;
  const day = Math.floor(hr / 24);
  return `${day} day${day === 1 ? '' : 's'} ago`;
}

function formatTime(d: Date): string {
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
}

// Re-export to avoid module duplication when other files want the
// in-memory store for tests.
export { recordEngineRun, useEngineRunHistory } from '@/lib/hooks/useEngineOutputs';
