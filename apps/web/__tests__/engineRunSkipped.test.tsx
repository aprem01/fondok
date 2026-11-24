/**
 * FON-125 — a skipped engine in the run progress panel and the completion
 * toast (Sam, QA of deal b67bfdee).
 *
 * The defect: a re-run from the P&L tab listed 9 steps in the progress panel
 * while the toast said "Underwriting complete · 8 engines · 3.5s". Partnership
 * was not run — the worker wrote an explicit `not_applicable` row with its own
 * reason ("no partnership terms on this deal") — but the panel showed only a
 * grey "not applicable to this deal" and the toast dropped the step.
 *
 * Locked here:
 *   1. the panel marks the step skipped WITH the worker's sentence and its
 *      footer counts it: "8 of 9 complete · 1 skipped";
 *   2. the completion toast from the run hook counts the same way:
 *      "Underwriting complete · 8 engines · 1 skipped · <t>s · $0.00";
 *   3. the panel prints each complete engine's worker headline verbatim — the
 *      FON-124 "Y1 NOI" figure is the worker's, never rebuilt in the web.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, act, renderHook } from '@testing-library/react';
import React from 'react';
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

const DEAL = 'b67bfdee-0000-4000-8000-000000000000';
const RUN = 'run-1';
const ORDER: EngineName[] = [
  'revenue', 'fb', 'expense', 'capital', 'debt', 'returns', 'sensitivity', 'partnership', 'cash_flow',
];

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
    started_at: '2026-11-23T10:00:00Z',
    completed_at: '2026-11-23T10:00:03Z',
    run_id: RUN,
    ...extra,
  };
}

/** The nine rows the worker serves for a full run on a deal with no
 *  partnership terms: eight complete, Partnership `not_applicable` with the
 *  engine's own sentence (pinned worker-side in test_partnership_terms_b5). */
const ROWS: EngineOutputResponse[] = ORDER.map((engine) => {
  if (engine === 'partnership') {
    return row(engine, 'not_applicable', {
      not_applicable: {
        engine: 'partnership',
        reason: 'not_applicable',
        message: 'no partnership terms on this deal',
        missing: ['split', 'pref_rate', 'tiers'],
        incomplete: [],
      },
    });
  }
  // The worker's headline for the expense engine (FON-124): Y1 NOI before
  // the FF&E reserve. The web prints it verbatim.
  return row(engine, 'complete', engine === 'expense' ? { summary: 'Y1 NOI $1.65M' } : {});
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  fx.toast.mockReset();
  fx.runAll.mockReset();
  fx.getRun.mockReset();
});

describe('EngineRunProgress — a skipped step', () => {
  it('marks Partnership skipped with the worker reason and counts it in the footer', () => {
    const { container } = render(
      <EngineRunProgress
        runId={RUN}
        expectedEngines={ORDER}
        rows={ROWS}
        startedAt={Date.now() - 3_500}
        dismissAfterMs={60_000}
      />,
    );

    // Nine steps listed, Partnership among them.
    expect(container.querySelectorAll('li')).toHaveLength(9);
    expect(screen.getByText('Partnership')).toBeInTheDocument();

    // The step is skipped, by the worker's own sentence — not a bare
    // "not applicable", not a failure.
    expect(screen.getByText('skipped · no partnership terms on this deal')).toBeInTheDocument();
    expect(screen.getByLabelText('skipped')).toBeInTheDocument();
    expect(screen.queryByText(/failed/)).toBeNull();

    // Header: complete, not "finished with errors".
    expect(screen.getByText(/^Underwriting complete/)).toBeInTheDocument();

    // Footer: ran + skipped = steps.
    const text = (container.textContent ?? '').replace(/\s+/g, ' ');
    expect(text).toMatch(/8 of 9 complete\s*· 1 skipped/);

    // FON-124 — the expense step shows the worker's headline verbatim.
    expect(screen.getByText('Y1 NOI $1.65M')).toBeInTheDocument();
  });

  it('shows no skipped count when every engine ran', () => {
    const allComplete = ORDER.map((engine) => row(engine, 'complete'));
    const { container } = render(
      <EngineRunProgress
        runId={RUN}
        expectedEngines={ORDER}
        rows={allComplete}
        startedAt={Date.now() - 1_000}
        dismissAfterMs={60_000}
      />,
    );
    const text = (container.textContent ?? '').replace(/\s+/g, ' ');
    expect(text).toMatch(/9 of 9 complete/);
    expect(text).not.toContain('skipped');
  });
});

describe('useEngineRun — run-all completion toast', () => {
  it('counts the skipped engine so the toast agrees with the panel', async () => {
    vi.useFakeTimers();
    fx.runAll.mockResolvedValue({
      deal_id: DEAL,
      run_id: RUN,
      started_at: new Date().toISOString(),
      engines: ORDER.map((name) => ({ name, status: 'queued' as EngineStatus })),
    });
    fx.getRun.mockResolvedValue({ deal_id: DEAL, run_id: RUN, engines: ROWS, failures: [] });

    const onAllComplete = vi.fn();
    const { result } = renderHook(() => useEngineRun(DEAL, 'expense', { runMode: 'all', onAllComplete }));

    await act(async () => {
      await result.current.run();
    });
    // One poll tick (750 ms) lands the terminal rows.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });

    expect(onAllComplete).toHaveBeenCalledTimes(1);
    const texts = fx.toast.mock.calls.map((c) => String(c[0]));
    const done = texts.find((t) => t.startsWith('Underwriting'));
    expect(done).toMatch(/^Underwriting complete · 8 engines · 1 skipped · \d+\.\ds · \$0\.00$/);
    expect(fx.toast).toHaveBeenCalledWith(done, { type: 'success' });
    // Nothing read as a failure.
    expect(texts.some((t) => /partially|failed/.test(t))).toBe(false);

    // The hook's own engine (expense) is complete with the worker headline.
    expect(result.current.status).toBe('complete');
    expect(result.current.summary).toBe('Y1 NOI $1.65M');
  });
});
