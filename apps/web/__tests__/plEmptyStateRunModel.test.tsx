/**
 * FON-123 (Sam, copy d0bfe816) — the P&L tab's EMPTY-STATE "Run Model" button.
 *
 * "On the new copy, the empty-state 'Run Model' button on P&L did nothing
 * visible. The engine card's 'Run' / 'Re-run' button worked." The button
 * used to pop an info toast ("Nothing is queued yet …") and run nothing.
 *
 * Contracts:
 *  1. The button takes the SAME action as the engine card — the run-all
 *     kickoff through `useEngineRun(dealId, 'expense', { runMode: 'all' })`.
 *  2. It shows visible progress: while the run is in flight the button is
 *     disabled, reads "Running the model…", and a status line is shown.
 *  3. A failed run shows the hook's error under the button.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';

// ── next/navigation ──
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useSearchParams: () => new URLSearchParams(''),
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => '/projects/deal-uuid-1',
}));

// ── no engine outputs yet (the empty state), fetch settled ──
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: null,
      previous: null,
      loading: false,
      settled: true,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({ documents: [], extractions: {}, loading: false, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useSubTab', () => ({
  useSubTab: () => ({ sub: 'historicals', setSub: vi.fn() }),
}));

// ── the run hook: capture every construction, serve a settable status ──
type HookCall = { dealId: string; engine: string; opts: Record<string, unknown> | undefined };
const hookCalls: HookCall[] = [];
const runSpy = vi.fn(async () => {});
let RUN_STATUS: string = 'idle';
let RUN_ERROR: string | null = null;
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: (dealId: string, engine: string, opts?: Record<string, unknown>) => {
    hookCalls.push({ dealId, engine, opts });
    return {
      run: runSpy,
      status: RUN_STATUS,
      error: RUN_ERROR,
      output: null,
      summary: '',
      complete: RUN_STATUS === 'complete',
      activeRunId: null,
    };
  },
}));

// ── neighbours that are not under test ──
vi.mock('@/components/project/EngineHeader', () => ({
  default: () => <div data-testid="engine-header" />,
}));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/project/pl/ProjectionsSection', () => ({ default: () => null }));
vi.mock('@/components/project/pl/GroundedWorksheet', () => ({
  default: () => null,
  fieldMatchesKey: () => false,
}));
vi.mock('@/components/design', () => ({ SubTabNav: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import PLTab from '@/components/project/PLTab';

beforeEach(() => {
  cleanup();
  hookCalls.length = 0;
  runSpy.mockClear();
  RUN_STATUS = 'idle';
  RUN_ERROR = null;
});
afterEach(() => cleanup());

describe('P&L empty state — Run Model', () => {
  it('renders the empty state with an enabled Run Model button', () => {
    render(<PLTab />);
    expect(screen.getByText('No P&L output yet')).toBeTruthy();
    const btn = screen.getByTestId('pl-empty-run-model') as HTMLButtonElement;
    expect(btn.textContent).toContain('Run Model');
    expect(btn.disabled).toBe(false);
  });

  it('takes the same run-all action as the engine card (useEngineRun runMode "all") and runs it', () => {
    render(<PLTab />);
    // PLTab's own instance: the deal, the P&L engine, run-all.
    const own = hookCalls.find((c) => c.engine === 'expense' && c.opts?.runMode === 'all');
    expect(own, 'PLTab must construct useEngineRun(dealId, "expense", { runMode: "all" })').toBeTruthy();
    expect(own!.dealId).toBe('deal-uuid-1');

    fireEvent.click(screen.getByTestId('pl-empty-run-model'));
    expect(runSpy).toHaveBeenCalledTimes(1);
  });

  it('shows progress while the run is in flight: disabled button, running label, status line', () => {
    RUN_STATUS = 'running';
    render(<PLTab />);
    const btn = screen.getByTestId('pl-empty-run-model') as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('Running the model…');
    expect(screen.getByTestId('pl-empty-run-status').textContent).toMatch(/Running every engine/);
    expect(screen.queryByTestId('pl-empty-run-error')).toBeNull();
  });

  it('surfaces a failed run under the button', () => {
    RUN_STATUS = 'failed';
    RUN_ERROR = 'engine run timed out — refresh the page';
    render(<PLTab />);
    expect(screen.getByTestId('pl-empty-run-error').textContent).toBe('engine run timed out — refresh the page');
    const btn = screen.getByTestId('pl-empty-run-model') as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('Run Model');
  });
});
