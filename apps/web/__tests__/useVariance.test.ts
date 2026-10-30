/**
 * Go-live Wave 0 (D1 / C4) — `useVariance` tells a failed fetch from "no
 * flags yet" and from "no worker to ask".
 *
 *  • a rejected fetch → `error` set, `flags` still null, `refresh` re-fetches;
 *  • a successful fetch clears `error`;
 *  • no worker URL → `workerConfigured: false`, nothing fetched, flags null.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';

const variance = vi.fn();
let connected = true;

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    api: { analysis: { variance: (...a: unknown[]) => variance(...a) } },
    isWorkerConnected: () => connected,
  };
});

import { useVariance } from '@/lib/hooks/useVariance';

const REPORT = { deal_id: 'deal-1', flags: [], critical_count: 0, warn_count: 0, info_count: 0, note: 'Upload a T-12.' };

beforeEach(() => {
  variance.mockReset();
  connected = true;
});

describe('useVariance · error vs empty vs not configured', () => {
  it('a failed fetch lands in `error` with flags still null — never an empty report', async () => {
    variance.mockRejectedValue(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useVariance('deal-1'));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    expect(result.current.flags).toBeNull();
    expect(result.current.loading).toBe(false);
    expect(result.current.workerConfigured).toBe(true);
  });

  it('refresh() re-fetches and a success clears the error', async () => {
    variance.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    const { result } = renderHook(() => useVariance('deal-1'));
    await waitFor(() => expect(result.current.error).not.toBeNull());
    variance.mockResolvedValue(REPORT);
    act(() => result.current.refresh());
    await waitFor(() => expect(result.current.flags).toEqual([]));
    expect(result.current.error).toBeNull();
    expect(result.current.note).toBe('Upload a T-12.');
  });

  it('with no worker URL nothing is fetched and the state says so', async () => {
    connected = false;
    const { result } = renderHook(() => useVariance('deal-1'));
    expect(result.current.workerConfigured).toBe(false);
    expect(result.current.flags).toBeNull();
    expect(result.current.error).toBeNull();
    await new Promise((r) => setTimeout(r, 0));
    expect(variance).not.toHaveBeenCalled();
  });
});
