/**
 * Wave 3 (B5) — `not_applicable` is a terminal, non-failure engine status
 * (Partnership on a deal with no partnership terms). The run hook must:
 *   - run-all: stop polling once every row is terminal, count it as neither
 *     complete nor failed, toast success and call `onAllComplete` — never
 *     poll on into the 60 s safety timeout;
 *   - single engine: settle on `not_applicable` with one neutral toast and
 *     `onComplete`, never the error branch.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';

const fx = vi.hoisted(() => ({
  toast: vi.fn(),
  runAll: vi.fn(),
  getRun: vi.fn(),
  runOne: vi.fn(),
}));

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: fx.toast }) }));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      engines: { ...actual.api.engines, runAll: fx.runAll, getRun: fx.getRun, runOne: fx.runOne },
    },
  };
});

import { useEngineRun } from '@/lib/hooks/useEngineRun';

const NOT_APPLICABLE = {
  engine: 'partnership',
  reason: 'not_applicable',
  message: 'no partnership terms on this deal',
  missing: ['split', 'pref_rate', 'tiers'],
  incomplete: [],
};
const row = (engine: string, status: string, extra: Record<string, unknown> = {}) => ({
  deal_id: 'deal-uuid-1', engine, status, summary: '', outputs: null, inputs: null, error: null,
  runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1', ...extra,
});

beforeEach(() => {
  vi.useFakeTimers();
  for (const f of [fx.toast, fx.runAll, fx.getRun, fx.runOne]) f.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useEngineRun — run-all with a not_applicable Partnership', () => {
  it('stops polling, toasts success (no timeout) and calls onAllComplete', async () => {
    fx.runAll.mockResolvedValue({ run_id: 'run-1', engines: [{ name: 'returns' }, { name: 'partnership' }] });
    fx.getRun.mockResolvedValue({
      run_id: 'run-1',
      engines: [
        row('returns', 'complete'),
        row('partnership', 'not_applicable', { not_applicable: NOT_APPLICABLE }),
      ],
    });
    const onAllComplete = vi.fn();
    const { result } = renderHook(() =>
      useEngineRun('deal-uuid-1', 'partnership', { runMode: 'all', onAllComplete }),
    );

    await act(async () => { await result.current.run(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(800); });

    expect(onAllComplete).toHaveBeenCalledTimes(1);
    const pollsAtDone = fx.getRun.mock.calls.length;
    // Polling has stopped: no further reads, and no timeout toast after 60 s.
    await act(async () => { await vi.advanceTimersByTimeAsync(61_000); });
    expect(fx.getRun.mock.calls.length).toBe(pollsAtDone);

    const toasts: Array<[string, string | undefined]> = fx.toast.mock.calls.map((c) => [String(c[0]), (c[1] as { type?: string } | undefined)?.type]);
    expect(toasts.some(([msg, type]) => msg.startsWith('Underwriting complete') && type === 'success'), JSON.stringify(toasts)).toBe(true);
    expect(toasts.some(([msg]) => /timed out|partially complete/.test(msg)), JSON.stringify(toasts)).toBe(false);
    expect(toasts.some(([, type]) => type === 'error')).toBe(false);
    expect(result.current.status).toBe('not_applicable');
    expect(result.current.error).toBeNull();
  });
});

describe('useEngineRun — single engine settles on not_applicable', () => {
  it('neutral toast with the worker message, onComplete, never the error branch', async () => {
    fx.runOne.mockResolvedValue(row('partnership', 'not_applicable', { not_applicable: NOT_APPLICABLE }));
    const onComplete = vi.fn();
    const { result } = renderHook(() => useEngineRun('deal-uuid-1', 'partnership', { onComplete }));

    await act(async () => { await result.current.run(); });

    expect(result.current.status).toBe('not_applicable');
    expect(result.current.error).toBeNull();
    expect(onComplete).toHaveBeenCalledTimes(1);
    expect(fx.toast).toHaveBeenCalledTimes(1);
    const [msg, opts] = fx.toast.mock.calls[0] as [string, { type?: string }];
    expect(msg).toContain('no partnership terms on this deal');
    expect(opts.type).toBe('info');
  });
});
