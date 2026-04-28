'use client';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Play, Download, Clock, RotateCcw } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import {
  EngineName,
  EngineOutputResponse,
} from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import EngineRunProgress from './EngineRunProgress';
import { cn } from '@/lib/format';

// Browsers don't expose .env to client without the NEXT_PUBLIC_ prefix.
// Same gating ExportTab uses — when unset we surface a toast instead of
// hitting a non-existent worker.
const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL ?? '';

const ENGINE_ORDER: EngineName[] = [
  'revenue',
  'fb',
  'expense',
  'capital',
  'debt',
  'returns',
  'sensitivity',
  'partnership',
];

export type EngineRunMode = 'single' | 'all';

export default function EngineHeader({
  name,
  desc,
  outputs,
  dependsOn,
  complete: initialComplete = false,
  dealId,
  engineName,
  runMode = 'single',
  onRun,
  onExport,
  onRunComplete,
  onRunStart,
}: {
  name: string;
  desc: string;
  outputs: string[];
  dependsOn: string | null;
  /** Optional pre-completed flag (e.g. when the tab is showing seeded mock data). */
  complete?: boolean;
  /** Required when no `onExport` is provided so the default handler can build the worker URL. */
  dealId?: string;
  /** When set, the Run button is wired to the worker engine API. */
  engineName?: EngineName;
  /** `'single'` runs just this engine; `'all'` kicks off the full chain. */
  runMode?: EngineRunMode;
  onRun?: () => void;
  onExport?: () => void;
  /** Called whenever any run finishes — tabs use this to drive the
      "What just happened" panel without coupling to internal state. */
  onRunComplete?: (output: EngineOutputResponse | null) => void;
  /** Called when the user kicks off a run — tabs use this to dim
      content / disable interaction while computing. */
  onRunStart?: () => void;
}) {
  const { toast } = useToast();
  // Stub spinner used only when neither `engineName` nor `onRun` is supplied.
  const [stubRunning, setStubRunning] = useState(false);

  // Run-all progress streaming state.
  const [runId, setRunId] = useState<string | null>(null);
  const [runRows, setRunRows] = useState<EngineOutputResponse[]>([]);
  const [runStartedAt, setRunStartedAt] = useState<number | null>(null);
  const [expectedEngines, setExpectedEngines] = useState<EngineName[]>([]);
  const [runNumber, setRunNumber] = useState(1);
  const [glowing, setGlowing] = useState(false);
  const [confettiKey, setConfettiKey] = useState<number | null>(null);

  // The hook is always called (Rules of Hooks) — it's a no-op when
  // `engineName` is missing because we only ever read `run`/`status`
  // when it was supplied.
  const wired = useEngineRun(dealId ?? '', engineName ?? 'returns', {
    runMode,
    onComplete: (output) => {
      // Single-engine completion → flash the success badge briefly.
      if (output.status === 'complete') {
        setGlowing(true);
        setConfettiKey(Date.now());
        window.setTimeout(() => setGlowing(false), 5000);
      }
      onRunComplete?.(output);
    },
    onRunAllStarted: (id, engines) => {
      setRunId(id);
      setRunRows([]);
      setRunStartedAt(Date.now());
      setExpectedEngines(engines.length > 0 ? engines : ENGINE_ORDER);
      setRunNumber((n) => n + 1);
    },
    onRunAllProgress: (rows) => {
      setRunRows(rows);
    },
    onAllComplete: (rows) => {
      setRunRows(rows);
      const okCount = rows.filter((r) => r.status === 'complete').length;
      if (okCount > 0) {
        setGlowing(true);
        setConfettiKey(Date.now());
        window.setTimeout(() => setGlowing(false), 5000);
      }
      // Bubble the most-relevant row to the parent tab.
      const ours =
        rows.find((r) => r.engine === engineName) ?? rows[rows.length - 1] ?? null;
      onRunComplete?.(ours);
    },
  });
  const isWired = Boolean(engineName && dealId);

  const running = isWired ? wired.status === 'running' : stubRunning;
  const justFailed = isWired && wired.status === 'failed';
  const complete = isWired
    ? wired.complete || initialComplete
    : initialComplete;
  // Prefer the live engine summary when we have one; fall back to the
  // tab's seeded "Model complete" badge otherwise.
  const summary = isWired ? wired.summary : '';

  // Track how long we've been running for the "Last run" relative text.
  const lastRunAtRef = useRef<Date | null>(initialComplete ? new Date(Date.now() - 3 * 60_000) : null);
  useEffect(() => {
    if (wired.complete) lastRunAtRef.current = new Date();
  }, [wired.complete]);
  const lastRunLabel = useMemo(
    () => (lastRunAtRef.current ? formatRelative(lastRunAtRef.current) : null),
    // Re-evaluate on every render — relative timestamps drift.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [wired.complete, runId],
  );

  const handleRun = () => {
    if (onRun) {
      onRun();
      return;
    }
    if (isWired) {
      onRunStart?.();
      void wired.run();
      return;
    }
    // Pure stub fallback — preserves the original click affordance for
    // tabs that haven't been threaded yet.
    setStubRunning(true);
    toast('Engine queued — check back shortly', { type: 'info' });
    window.setTimeout(() => setStubRunning(false), 2000);
  };

  const handleExport = () => {
    if (onExport) {
      onExport();
      return;
    }
    if (!WORKER_URL) {
      toast('Available after model run', { type: 'info' });
      return;
    }
    if (!dealId) {
      toast('Available after model run', { type: 'info' });
      return;
    }
    // Worker streams the file via Content-Disposition; navigating triggers
    // the browser download without a popup.
    window.location.href = `${WORKER_URL}/deals/${dealId}/export/excel`;
  };

  return (
    <>
      <Card tone={complete ? 'default' : 'luxe'} className="p-5 mb-5">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <div className="eyebrow mb-1.5">
              Engine ·{' '}
              {complete ? 'Complete' : running ? 'Running' : 'Ready to run'}
              {lastRunLabel && (
                <span className="ml-2 inline-flex items-center gap-1 text-ink-500 normal-case tracking-normal">
                  <Clock size={10} />
                  Last run {lastRunLabel}
                </span>
              )}
            </div>
            <h2 className="font-display text-[18px] font-semibold text-ink-900 tracking-[-0.014em] leading-tight">
              {name}
            </h2>
            <p className="text-body-sm text-ink-500 mt-1.5 max-w-2xl">{desc}</p>

            <div className="flex items-center gap-2 mt-4 flex-wrap">
              <span className="eyebrow">Outputs</span>
              {outputs.map((o) => (
                <Badge key={o} tone="blue" dot>
                  {o}
                </Badge>
              ))}
            </div>

            {dependsOn && (
              <div className="text-[11px] text-ink-500 mt-2.5">
                Depends on:{' '}
                <span className="text-brand-700 font-semibold">{dependsOn}</span>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <div className="relative">
              {complete && summary ? (
                <Badge
                  tone="green"
                  dot
                  uppercase
                  className={cn(glowing && 'pulse-success rounded-full')}
                >{`Complete · ${summary}`}</Badge>
              ) : complete ? (
                <Badge
                  tone="green"
                  dot
                  uppercase
                  className={cn(glowing && 'pulse-success rounded-full')}
                >
                  Model complete
                </Badge>
              ) : null}
              {confettiKey && complete && (
                <ConfettiBurst key={confettiKey} />
              )}
            </div>
            {justFailed && (
              <Badge tone="red" dot uppercase>
                Failed
              </Badge>
            )}
            <Button
              variant="secondary"
              size="sm"
              onClick={handleExport}
              type="button"
            >
              <Download size={12} /> Export to Excel
            </Button>
            <Button
              variant={complete ? 'primary' : 'premium'}
              size="sm"
              onClick={handleRun}
              loading={running}
              type="button"
            >
              {!running && (complete ? <RotateCcw size={12} /> : <Play size={12} />)}{' '}
              {running ? 'Running…' : complete ? 'Run Again' : 'Run Model'}
            </Button>
          </div>
        </div>

        {/* Inline mini progress for single-engine runs — sits inside the
            EngineHeader card so the user gets feedback without shifting
            their eyes to the bottom-right strip. */}
        {isWired && runMode === 'single' && running && (
          <div className="mt-4 pt-4 border-t border-border/60 flex items-center gap-2 text-[11.5px] text-ink-500">
            <span className="inline-block w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse" />
            Running {engineName}… recalculating outputs
          </div>
        )}
      </Card>

      {/* Floating progress strip for run-all chains. */}
      {runMode === 'all' && (
        <EngineRunProgress
          runId={runId}
          expectedEngines={expectedEngines}
          rows={runRows}
          startedAt={runStartedAt}
          runNumber={runNumber}
          onClose={() => setRunId(null)}
        />
      )}
    </>
  );
}

function formatRelative(d: Date): string {
  const sec = Math.floor((Date.now() - d.getTime()) / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min} min${min === 1 ? '' : 's'} ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return d.toLocaleDateString();
}

/** Three gold sparks burst from the success badge — pure CSS, no library. */
function ConfettiBurst() {
  const sparks = [
    { x: 22, y: -18, color: '#A68850' },
    { x: -16, y: -22, color: '#D4AF37' },
    { x: 4, y: -28, color: '#A68850' },
  ];
  return (
    <span className="absolute inset-0 pointer-events-none">
      {sparks.map((s, i) => (
        <span
          key={i}
          className="confetti-spark"
          style={{
            top: '50%',
            left: '50%',
            background: s.color,
            // CSS custom property used by the confetti-burst keyframe.
            ['--confetti-end' as string]: `translate(${s.x}px, ${s.y}px) scale(1.2)`,
            animationDelay: `${i * 60}ms`,
          }}
        />
      ))}
    </span>
  );
}
