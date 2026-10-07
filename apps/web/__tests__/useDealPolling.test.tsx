/**
 * E-003 (FON-59) — the header said "0 keys" while Overview said 132.
 *
 * The create-deal wizard sends keys as null; the worker fills keys / brand /
 * city from the OM extraction a minute later. `useDeal` polled /status every
 * 3s but never re-read the deal row, so the header kept the creation-time
 * nulls for the life of the page. These tests pin the two refetch rules:
 *
 *   1. the moment the polled status leaves the extracting / processing set,
 *      the deal row is fetched again (a second `api.deals.get`);
 *   2. while extraction keeps running, the deal row is re-read about every
 *      15s so a long extraction still updates the header as documents land;
 *
 * and the two non-rules: a deal that loads already settled is fetched once,
 * and numeric (mock) ids never touch the worker.
 *
 * (`useDeal.test.tsx` is the page-level load-gate suite and mocks the hook
 * itself; this file exercises the real hook.)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import type { WorkerDeal, WorkerDealStatus } from '@/lib/api';

const getSpy = vi.fn();
const statusSpy = vi.fn();
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: {
        ...actual.api.deals,
        get: (...a: unknown[]) => getSpy(...a),
        status: (...a: unknown[]) => statusSpy(...a),
      },
    },
  };
});

import { useDeal } from '@/lib/hooks/useDeal';

const ID = 'fff00000-0000-0000-0000-000000000aaa';

function dealRow(over: Partial<WorkerDeal>): WorkerDeal {
  return {
    id: ID,
    tenant_id: 'tenant-1',
    name: 'Project Unicorn',
    city: null,
    keys: null,
    service: null,
    brand: null,
    status: 'extracting',
    deal_stage: null,
    risk: null,
    ai_confidence: null,
    created_at: '2026-10-07T00:00:00Z',
    updated_at: '2026-10-07T00:00:00Z',
    ...over,
  };
}
function statusRow(status: string): WorkerDealStatus {
  return { id: ID, status, deal_stage: null, last_event: null };
}

/** Advance the fake clock and let every settled promise and effect run. */
async function tick(ms: number): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
  getSpy.mockReset();
  statusSpy.mockReset();
});
afterEach(() => {
  vi.useRealTimers();
});

describe('useDeal — refetches the deal row as extraction finishes (E-003 / FON-59)', () => {
  it('fetches the deal again the moment /status leaves the polling set', async () => {
    getSpy
      .mockResolvedValueOnce(dealRow({ status: 'extracting', keys: null }))
      .mockResolvedValueOnce(
        dealRow({ status: 'ready', keys: 132, city: 'Miami Beach, FL', brand: 'Kimpton' }),
      );
    statusSpy
      .mockResolvedValueOnce(statusRow('extracting')) // initial load
      .mockResolvedValueOnce(statusRow('extracting')) // poll @3s
      .mockResolvedValue(statusRow('ready')); // poll @6s and after

    const { result } = renderHook(() => useDeal(ID));
    await tick(0);
    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(result.current.deal?.keys).toBeNull();

    await tick(3000); // still extracting — status only, no deal refetch
    expect(getSpy).toHaveBeenCalledTimes(1);

    await tick(3000); // ready — the deal row is read again
    await tick(0);
    expect(getSpy).toHaveBeenCalledTimes(2);
    expect(result.current.deal?.keys).toBe(132);
    expect(result.current.deal?.city).toBe('Miami Beach, FL');
    expect(result.current.deal?.brand).toBe('Kimpton');
    expect(result.current.status?.status).toBe('ready');
  });

  it('stops polling, and does not refetch again, once the deal has settled', async () => {
    getSpy.mockResolvedValue(dealRow({ status: 'ready', keys: 132 }));
    statusSpy
      .mockResolvedValueOnce(statusRow('extracting')) // initial load → polling
      .mockResolvedValue(statusRow('ready')); // first poll settles it

    renderHook(() => useDeal(ID));
    await tick(0);
    await tick(3000);
    await tick(0);
    expect(getSpy).toHaveBeenCalledTimes(2); // initial + transition

    const statusCalls = statusSpy.mock.calls.length;
    await tick(60_000);
    expect(getSpy).toHaveBeenCalledTimes(2);
    expect(statusSpy.mock.calls.length).toBe(statusCalls);
  });

  it('re-reads the deal row about every 15s while extraction keeps running', async () => {
    getSpy.mockResolvedValue(dealRow({ status: 'extracting' }));
    statusSpy.mockResolvedValue(statusRow('extracting'));

    renderHook(() => useDeal(ID));
    await tick(0);
    expect(getSpy).toHaveBeenCalledTimes(1);

    await tick(12_000); // four status polls, no deal refetch yet
    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(statusSpy.mock.calls.length).toBeGreaterThanOrEqual(5);

    await tick(3000); // 15s since the last deal read
    expect(getSpy).toHaveBeenCalledTimes(2);

    await tick(15_000);
    expect(getSpy).toHaveBeenCalledTimes(3);
  });

  it('a deal that loads already settled is fetched exactly once', async () => {
    getSpy.mockResolvedValue(dealRow({ status: 'ready', keys: 132 }));
    statusSpy.mockResolvedValue(statusRow('ready'));

    renderHook(() => useDeal(ID));
    await tick(0);
    await tick(60_000);
    expect(getSpy).toHaveBeenCalledTimes(1);
    expect(statusSpy).toHaveBeenCalledTimes(1);
  });

  it('numeric (mock) ids never hit the worker', async () => {
    const { result } = renderHook(() => useDeal('7'));
    await tick(0);
    await tick(60_000);
    expect(getSpy).not.toHaveBeenCalled();
    expect(statusSpy).not.toHaveBeenCalled();
    expect(result.current.fromMock).toBe(true);
  });
});
