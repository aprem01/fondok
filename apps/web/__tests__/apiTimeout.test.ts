/**
 * Wave 4 reliability fix — Bug #3 regression suite (web).
 *
 * The ``request<T>()`` helper now wraps every fetch in an
 * AbortController with a per-method timeout (20s GET, 60s POST/upload).
 * On timeout it throws a typed ``TimeoutError`` so hooks + pages can
 * render a "worker is busy" affordance instead of an infinite skeleton.
 *
 * This suite exercises the timeout contract directly:
 *  1. A GET that never resolves rejects with a ``TimeoutError`` after
 *     the configured timeout fires.
 *  2. A caller-provided ``AbortSignal`` is composable with the
 *     timeout signal — aborting the caller signal cancels the fetch
 *     before the timeout fires.
 *  3. A fast-resolving fetch is unaffected (no spurious timeout).
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Pre-configure the worker URL so the request helper actually issues
// the fetch instead of bailing out via the "worker not connected" guard.
process.env.NEXT_PUBLIC_WORKER_URL = 'http://test-worker.local';

// Auth helper returns null org by default; we don't care about the
// X-Tenant-Id header in these tests.
vi.mock('@/lib/auth', () => ({
  getCurrentOrgId: () => null,
}));

import { api, TimeoutError } from '@/lib/api';

describe('Bug #3 — request() client-side timeout', () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    // Drain any pending timers (e.g. a request-timeout that hadn't fired
    // yet at the time of caller-driven abort) so they don't leak into the
    // next test as an unhandled-rejection cloud. We swallow the rejected
    // promise rather than re-throwing — by this point the test body has
    // already asserted what it cared about.
    vi.clearAllTimers();
    vi.useRealTimers();
    global.fetch = originalFetch;
  });

  it('rejects with a TimeoutError when fetch never resolves', async () => {
    // Hanging fetch — resolves never; only the AbortSignal can break it.
    global.fetch = vi.fn((_url: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_, reject) => {
        init?.signal?.addEventListener('abort', () => {
          // Browsers reject hung fetches with a DOMException of name
          // "AbortError" — mirror that here so the request() helper's
          // catch branch runs.
          const err = new Error('aborted');
          (err as { name: string }).name = 'AbortError';
          reject(err);
        });
      }),
    ) as unknown as typeof fetch;

    const p = api.deals.get('11111111-1111-1111-1111-111111111111');
    // Attach a no-op .catch IMMEDIATELY so Vitest's unhandled-rejection
    // hook doesn't trip on the in-flight promise while we advance fake
    // timers (the rejection settles synchronously inside
    // advanceTimersByTimeAsync, before the test body can await it).
    p.catch(() => {});

    // 20s default for GETs — advance time to fire the timeout.
    await vi.advanceTimersByTimeAsync(20_001);

    let caught: unknown = null;
    try {
      await p;
    } catch (e) {
      caught = e;
    }

    expect(caught).toBeInstanceOf(TimeoutError);
    const t = caught as TimeoutError;
    expect(t.name).toBe('TimeoutError');
    expect(t.method).toBe('GET');
    expect(t.timeoutMs).toBe(20_000);
  });

  it('honors a caller-provided AbortSignal alongside the timeout signal', async () => {
    let abortedReason: unknown = null;
    global.fetch = vi.fn((_url, init?: RequestInit) =>
      new Promise<Response>((_, reject) => {
        init?.signal?.addEventListener('abort', () => {
          abortedReason = (init?.signal as AbortSignal & { reason?: unknown })
            ?.reason;
          const err = new Error('aborted');
          (err as { name: string }).name = 'AbortError';
          reject(err);
        });
      }),
    ) as unknown as typeof fetch;

    const ctrl = new AbortController();
    const p = api.deals.get(
      '22222222-2222-2222-2222-222222222222',
      ctrl.signal,
    );

    // Caller aborts BEFORE the timeout fires.
    ctrl.abort(new Error('user-cancelled'));

    let caught: unknown = null;
    try {
      await p;
    } catch (e) {
      caught = e;
    }

    // Caller-driven abort surfaces as an AbortError, NOT a TimeoutError.
    // The timeout would not have fired yet — only 0ms elapsed.
    expect((caught as { name?: string })?.name).toBe('AbortError');
    expect(abortedReason).toBeTruthy();
  });

  it('does not fire a spurious TimeoutError when the response resolves quickly', async () => {
    global.fetch = vi.fn(async () =>
      new Response(JSON.stringify({ id: 'ok' }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
    ) as unknown as typeof fetch;

    const result = await api.deals.get(
      '33333333-3333-3333-3333-333333333333',
    );
    // No fake-timer advance — the response landed within the same tick.
    expect(result).toEqual({ id: 'ok' });
  });
});
