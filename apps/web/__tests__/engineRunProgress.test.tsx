/**
 * E-024 — the run-all progress strip must say WHICH engine is running, how
 * many are done, and how long it has been — measured, ticking, no ETA. The
 * only reference to duration is the previous run's own ``runtime_ms``.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, act } from '@testing-library/react';
import React from 'react';
import EngineRunProgress from '@/components/project/EngineRunProgress';
import type { EngineName, EngineOutputResponse, EngineStatus } from '@/lib/api';

const T0 = Date.parse('2026-10-07T10:00:00Z');
const EXPECTED: EngineName[] = [
  'revenue', 'fb', 'expense', 'capital', 'debt',
  'returns', 'sensitivity', 'partnership', 'cash_flow',
];

function row(engine: EngineName, status: EngineStatus, runtime_ms: number | null = null): EngineOutputResponse {
  return {
    deal_id: 'deal-1',
    engine,
    status,
    summary: '',
    outputs: null,
    inputs: null,
    error: null,
    runtime_ms,
    started_at: null,
    completed_at: null,
    run_id: 'run-1',
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('EngineRunProgress', () => {
  it('names the running engine, k of n, and a ticking measured clock', () => {
    render(
      <EngineRunProgress
        runId="run-1"
        expectedEngines={EXPECTED}
        rows={[row('revenue', 'complete', 800), row('fb', 'complete', 400), row('expense', 'running')]}
        startedAt={T0 - 42_000}
      />,
    );
    expect(screen.getByText('Running Expense · 2 of 9 complete · 0:42')).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(1_000);
    });
    expect(screen.getByText('Running Expense · 2 of 9 complete · 0:43')).toBeInTheDocument();
  });

  it('falls back to the next queued engine when none is marked running', () => {
    render(
      <EngineRunProgress
        runId="run-1"
        expectedEngines={EXPECTED}
        rows={[row('revenue', 'complete', 800)]}
        startedAt={T0 - 5_000}
      />,
    );
    expect(screen.getByText('Running F&B · 1 of 9 complete · 0:05')).toBeInTheDocument();
  });

  it('shows "last run took" only when a measured runtime was supplied', () => {
    const { rerender } = render(
      <EngineRunProgress
        runId="run-1"
        expectedEngines={EXPECTED}
        rows={[row('revenue', 'running')]}
        startedAt={T0}
        lastRunTookMs={118_000}
      />,
    );
    expect(screen.getByText(/last run took 1m 58s/)).toBeInTheDocument();
    rerender(
      <EngineRunProgress
        runId="run-1"
        expectedEngines={EXPECTED}
        rows={[row('revenue', 'running')]}
        startedAt={T0}
        lastRunTookMs={null}
      />,
    );
    expect(screen.queryByText(/last run took/)).not.toBeInTheDocument();
  });

  it('freezes the clock once every engine is terminal', () => {
    render(
      <EngineRunProgress
        runId="run-1"
        expectedEngines={EXPECTED}
        rows={EXPECTED.map((e) => row(e, 'complete', 1_000))}
        startedAt={T0 - 118_000}
        dismissAfterMs={60_000}
      />,
    );
    expect(screen.getByText('Underwriting complete')).toBeInTheDocument();
    expect(screen.getByText(/1:58 · \$0\.00 spent/)).toBeInTheDocument();
    act(() => {
      vi.advanceTimersByTime(5_000);
    });
    expect(screen.getByText(/1:58 · \$0\.00 spent/)).toBeInTheDocument();
  });
});
