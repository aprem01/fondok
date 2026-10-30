/**
 * useEngineOutputs — `settled` contract (Sam QA 9/9 follow-up).
 *
 * PLTab holds a loading skeleton until the first outputs fetch settles, so
 * "No P&L output yet" is never shown while the payload is still in flight.
 * `settled` must flip true on success, on failure, and immediately when the
 * worker is not connected (so nothing waits forever).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, waitFor } from '@testing-library/react';

const getAll = vi.fn();
let connected = true;

vi.mock('@/lib/api', () => ({
  api: { engines: { getAll: (...a: unknown[]) => getAll(...a) } },
  isWorkerConnected: () => connected,
  userMessage: (e: unknown) => (e instanceof Error ? e.message : String(e)),
}));

import { useEngineOutputs } from '@/lib/hooks/useEngineOutputs';

beforeEach(() => {
  getAll.mockReset();
  connected = true;
});

describe('useEngineOutputs · settled', () => {
  it('starts unsettled, settles with outputs after a successful fetch', async () => {
    let resolve!: (v: unknown) => void;
    getAll.mockReturnValue(new Promise((r) => { resolve = r; }));
    const { result } = renderHook(() => useEngineOutputs('deal-1'));
    expect(result.current.settled).toBe(false);
    expect(result.current.outputs).toBeNull();
    resolve({ deal_id: 'deal-1', engines: {} });
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.outputs).toEqual({ deal_id: 'deal-1', engines: {} });
    expect(result.current.loading).toBe(false);
  });

  it('settles (with null outputs) when the fetch fails — the empty state is then truthful', async () => {
    getAll.mockRejectedValue(new Error('worker down'));
    const { result } = renderHook(() => useEngineOutputs('deal-1'));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.outputs).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it('a failed fetch lands in `error` — distinct from "model not run" (null outputs, no error)', async () => {
    getAll.mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useEngineOutputs('deal-1'));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.error).toEqual(expect.any(String));
    expect(result.current.outputs).toBeNull();
  });

  it('a later successful refresh clears the error and keeps the outputs', async () => {
    getAll.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useEngineOutputs('deal-1'));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    getAll.mockResolvedValue({ deal_id: 'deal-1', engines: {} });
    await result.current.refresh();
    await waitFor(() => expect(result.current.error).toBeNull());
    expect(result.current.outputs).toEqual({ deal_id: 'deal-1', engines: {} });
  });

  it('a healthy fetch that returns nothing has NO error — that is "model not run"', async () => {
    getAll.mockResolvedValue({ deal_id: 'deal-1', engines: {} });
    const { result } = renderHook(() => useEngineOutputs('deal-1'));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(result.current.error).toBeNull();
  });

  it('settles immediately when the worker is not connected (never waits forever)', async () => {
    connected = false;
    const { result } = renderHook(() => useEngineOutputs('deal-1'));
    await waitFor(() => expect(result.current.settled).toBe(true));
    expect(getAll).not.toHaveBeenCalled();
  });
});
