/**
 * Run lifecycle — the poller, the progress panel and the completion toast
 * finish on the SAME tick, judged against the run's own engine list
 * (Sam, QA of deal b67bfdee, 2026-11-27).
 *
 * The defect: Re-run on the P&L card toasted "Underwriting complete · 3
 * engines" while the floating panel sat on "Running Capital · 3 of 9" with
 * the clock climbing past 35 s, and only moved when another run was
 * started. Root cause: the worker inserts a run's rows one engine at a time
 * (`run_single_engine` → `_persist_status` right before each engine), so a
 * poll that lands between "expense complete" and "capital running" sees
 * three rows, all terminal. `useEngineRun` judged completion by the rows it
 * could see ("every row terminal") and ended the run — toast, stop polling
 * — while `EngineRunProgress` judged by the kickoff's nine-engine list and
 * kept waiting for rows that would never be fetched again.
 *
 * Locked here:
 *   1. Sam's exact sequence — a 9-engine kickoff whose first poll returns
 *      3 terminal rows is STILL RUNNING: no toast, polling continues, the
 *      panel reads "Running Capital · 3 of 9"; the next poll (8 complete +
 *      Partnership not_applicable) finishes everything together: toast
 *      "8 engines · 1 skipped", panel "8 of 9 complete · 1 skipped",
 *      polling stops, the clock freezes.
 *   2. A genuine 3-engine run (the kickoff names 3) lists 3 steps, finishes
 *      at 3 of 3, never mentions Capital, stops polling, and the toast says
 *      "3 engines" — the panel and the toast agree.
 *   3. A later full run still lists 9 steps.
 *   4. `isEngineRunDone` is the shared rule, pinned on its own.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import React, { useState } from 'react';
import type { EngineName, EngineOutputResponse, EngineStatus } from '@/lib/api';

const fx = vi.hoisted(() => ({
  toast: vi.fn(),
  runAll: vi.fn(),
  getRun: vi.fn(),
}));

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: fx.toast }) }));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      engines: { ...actual.api.engines, runAll: fx.runAll, getRun: fx.getRun },
    },
  };
});

import EngineRunProgress from '@/components/project/EngineRunProgress';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { isEngineRunDone } from '@/lib/progress';

const DEAL = 'b67bfdee-0000-4000-8000-000000000000';
const RUN = 'run-1';
const T0 = Date.parse('2026-11-27T10:00:00Z');
const ALL: EngineName[] = [
  'revenue', 'fb', 'expense', 'capital', 'debt', 'returns', 'sensitivity', 'partnership', 'cash_flow',
];
const FIRST_THREE: EngineName[] = ['revenue', 'fb', 'expense'];

function row(engine: EngineName, status: EngineStatus, extra: Partial<EngineOutputResponse> = {}): EngineOutputResponse {
  return {
    deal_id: DEAL,
    engine,
    status,
    summary: '',
    outputs: null,
    inputs: null,
    error: null,
    failure: null,
    not_applicable: null,
    runtime_ms: status === 'complete' ? 12 : null,
    started_at: '2026-11-27T10:00:00Z',
    completed_at: '2026-11-27T10:00:03Z',
    run_id: RUN,
    ...extra,
  };
}

/** What the worker's run-status endpoint serves after the first three
 *  engines have landed and before Capital's `running` row is inserted. */
const THREE_ROWS = FIRST_THREE.map((e) => row(e, 'complete'));

/** The nine rows of a finished full run on a deal with no partnership
 *  terms (pinned worker-side in test_partnership_terms_b5). */
const NINE_ROWS = ALL.map((engine) =>
  engine === 'partnership'
    ? row(engine, 'not_applicable', {
        not_applicable: {
          engine: 'partnership',
          reason: 'not_applicable',
          message: 'no partnership terms on this deal',
          missing: ['split', 'pref_rate', 'tiers'],
          incomplete: [],
        },
      })
    : row(engine, 'complete'),
);

function kickoff(engines: EngineName[]) {
  return {
    deal_id: DEAL,
    run_id: RUN,
    started_at: new Date().toISOString(),
    engines: engines.map((name) => ({ name, status: 'queued' as EngineStatus })),
  };
}

/** The EngineHeader's wiring, reduced to the hook + the strip. */
function Harness({ engine = 'expense' }: { engine?: EngineName }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [rows, setRows] = useState<EngineOutputResponse[]>([]);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [expected, setExpected] = useState<EngineName[]>([]);
  const wired = useEngineRun(DEAL, engine, {
    runMode: 'all',
    onRunAllStarted: (id, engines) => {
      setRunId(id);
      setRows([]);
      setStartedAt(Date.now());
      setExpected(engines);
    },
    onRunAllProgress: setRows,
    onAllComplete: setRows,
  });
  return (
    <>
      <button type="button" onClick={() => void wired.run()}>
        {wired.status === 'running' ? 'Running…' : 'Re-run'}
      </button>
      <EngineRunProgress
        runId={runId}
        expectedEngines={expected}
        rows={rows}
        startedAt={startedAt}
        dismissAfterMs={60_000}
      />
    </>
  );
}

function footerText(container: HTMLElement): string {
  return (container.textContent ?? '').replace(/\s+/g, ' ');
}

function underwritingToasts(): string[] {
  return fx.toast.mock.calls.map((c) => String(c[0])).filter((t) => t.startsWith('Underwriting'));
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  fx.toast.mockReset();
  fx.runAll.mockReset();
  fx.getRun.mockReset();
});

describe("Sam's sequence — a 9-engine run whose first poll sees 3 terminal rows", () => {
  it('keeps running until all nine have landed, then toast, panel and poller finish together', async () => {
    fx.runAll.mockResolvedValue(kickoff(ALL));
    fx.getRun
      .mockResolvedValueOnce({ deal_id: DEAL, run_id: RUN, engines: THREE_ROWS, failures: [] })
      .mockResolvedValue({ deal_id: DEAL, run_id: RUN, engines: NINE_ROWS, failures: [] });

    const { container } = render(<Harness />);
    await act(async () => {
      screen.getByText('Re-run').click();
    });
    expect(screen.getByText('Running…')).toBeInTheDocument();

    // Poll 1 (750 ms): three rows, all terminal — the race window.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(fx.getRun).toHaveBeenCalledTimes(1);
    // The run is NOT over: no toast, the button is still running, and the
    // panel reports the next step honestly.
    expect(underwritingToasts()).toEqual([]);
    expect(screen.getByText('Running…')).toBeInTheDocument();
    expect(screen.getByText(/^Running Capital · 3 of 9 complete · 0:0\d$/)).toBeInTheDocument();
    expect(container.querySelectorAll('li')).toHaveLength(9);

    // Poll 2: the worker has finished — eight complete, Partnership skipped.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(750);
    });
    expect(fx.getRun).toHaveBeenCalledTimes(2);
    const toasts = underwritingToasts();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toMatch(/^Underwriting complete · 8 engines · 1 skipped · \d+\.\ds · \$0\.00$/);
    expect(screen.getByText(/^Underwriting complete/)).toBeInTheDocument();
    expect(footerText(container)).toMatch(/8 of 9 complete\s*· 1 skipped/);
    expect(screen.getByText('skipped · no partnership terms on this deal')).toBeInTheDocument();
    expect(screen.queryByText(/Running Capital/)).toBeNull();
    expect(screen.getByText('Re-run')).toBeInTheDocument();

    // Polling has stopped and the clock is frozen.
    const elapsed = footerText(container).match(/(\d+:\d\d) · \$0\.00 spent/)?.[1];
    expect(elapsed).toBeDefined();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fx.getRun).toHaveBeenCalledTimes(2);
    expect(footerText(container)).toContain(`${elapsed} · $0.00 spent`);
  });
});

describe('a genuine 3-engine run', () => {
  it('lists 3 steps, finishes at 3 of 3, stops the clock and polling, and the toast says 3 engines', async () => {
    fx.runAll.mockResolvedValue(kickoff(FIRST_THREE));
    fx.getRun.mockResolvedValue({ deal_id: DEAL, run_id: RUN, engines: THREE_ROWS, failures: [] });

    const { container } = render(<Harness />);
    await act(async () => {
      screen.getByText('Re-run').click();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    // Three steps, none of them Capital.
    expect(container.querySelectorAll('li')).toHaveLength(3);
    expect(screen.getByText('Revenue')).toBeInTheDocument();
    expect(screen.getByText('F&B')).toBeInTheDocument();
    expect(screen.getByText('Expense')).toBeInTheDocument();
    expect(screen.queryByText('Capital')).toBeNull();
    expect(screen.queryByText(/Running Capital/)).toBeNull();

    // Finished at 3 of 3; the toast counts the same three.
    expect(screen.getByText(/^Underwriting complete/)).toBeInTheDocument();
    expect(footerText(container)).toMatch(/3 of 3 complete/);
    expect(footerText(container)).not.toContain('skipped');
    const toasts = underwritingToasts();
    expect(toasts).toHaveLength(1);
    expect(toasts[0]).toMatch(/^Underwriting complete · 3 engines · \d+\.\ds · \$0\.00$/);

    // Timer stopped, poller stopped.
    const elapsed = footerText(container).match(/(\d+:\d\d) · \$0\.00 spent/)?.[1];
    expect(elapsed).toBeDefined();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fx.getRun).toHaveBeenCalledTimes(1);
    expect(footerText(container)).toContain(`${elapsed} · $0.00 spent`);
  });
});

describe('a later full run', () => {
  it('still lists all nine steps', async () => {
    fx.runAll.mockResolvedValue(kickoff(ALL));
    fx.getRun.mockResolvedValue({ deal_id: DEAL, run_id: RUN, engines: [row('revenue', 'running')], failures: [] });

    const { container } = render(<Harness />);
    await act(async () => {
      screen.getByText('Re-run').click();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(container.querySelectorAll('li')).toHaveLength(9);
    expect(screen.getByText(/^Running Revenue · 0 of 9 complete/)).toBeInTheDocument();
    expect(underwritingToasts()).toEqual([]);
  });
});

describe('isEngineRunDone — the one rule the poller and the panel share', () => {
  it('is false while an engine of the run has no terminal row, even if every visible row is terminal', () => {
    expect(isEngineRunDone(ALL, THREE_ROWS)).toBe(false);
    expect(isEngineRunDone(ALL, [...THREE_ROWS, row('capital', 'running')])).toBe(false);
  });

  it('is true once every engine of the run is complete, failed or not_applicable', () => {
    expect(isEngineRunDone(ALL, NINE_ROWS)).toBe(true);
    expect(isEngineRunDone(FIRST_THREE, THREE_ROWS)).toBe(true);
    expect(isEngineRunDone(FIRST_THREE, [row('revenue', 'complete'), row('fb', 'failed'), row('expense', 'complete')])).toBe(true);
  });

  it('falls back to the rows when the kickoff named no engines, and is never true on nothing', () => {
    expect(isEngineRunDone([], THREE_ROWS)).toBe(true);
    expect(isEngineRunDone([], [row('revenue', 'running')])).toBe(false);
    expect(isEngineRunDone([], [])).toBe(false);
    expect(isEngineRunDone(ALL, [])).toBe(false);
  });
});
