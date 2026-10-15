/**
 * Review finding — the worker's ``/observability/cache-stats`` and
 * ``/observability/agent-costs`` require a signed-in session. Two web callers
 * (the Analysis tab's CacheHitBadge and /diag) used a bare ``fetch`` with no
 * Bearer token and got 401 in production: a permanent "Cache hit: —" and an
 * "HTTP 401" on /diag.
 *
 * Both now go through ``api.observability.*`` → ``request()``, which attaches
 * ``Authorization: Bearer <clerk jwt>`` and ``X-Tenant-Id``. This suite pins
 * that the fetch carries the Authorization header (and the caller's tenant),
 * and that a 401 surfaces as a typed ``WorkerError`` the UI can map to a
 * "sign in to see this" state.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

process.env.NEXT_PUBLIC_WORKER_URL = 'http://test-worker.local';

vi.mock('@/lib/auth', () => ({
  getCurrentOrgId: () => 'org_fondok',
  getClerkSessionToken: async () => 'jwt.session.token',
  waitForClerkTokenFn: async () => {},
}));

import { api, WorkerError } from '@/lib/api';

type FetchMock = ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('api.observability — authed worker reads', () => {
  const originalFetch = global.fetch;
  let fetchMock: FetchMock;

  beforeEach(() => {
    fetchMock = vi.fn(async () => jsonResponse({ cache_hit_rate: 0.85, samples: 100, totals: {} }));
    global.fetch = fetchMock as unknown as typeof fetch;
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('cacheStats hits /observability/cache-stats with the Bearer session token and the tenant header', async () => {
    const stats = await api.observability.cacheStats(100);
    expect(stats.cache_hit_rate).toBe(0.85);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://test-worker.local/observability/cache-stats?n=100');
    const headers = init.headers as Record<string, string>;
    expect(headers.Authorization).toBe('Bearer jwt.session.token');
    expect(headers['X-Tenant-Id']).toBe('org_fondok');
    expect(init.method).toBe('GET');
  });

  it('agentCosts hits /observability/agent-costs?days=N with the same auth headers', async () => {
    fetchMock.mockImplementationOnce(async () => jsonResponse({ samples: 3, total_cost_usd: 0.42, by_agent: [] }));
    const costs = await api.observability.agentCosts(1);
    expect(costs.samples).toBe(3);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://test-worker.local/observability/agent-costs?days=1');
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer jwt.session.token');
  });

  it('a 401 rejects with a WorkerError carrying status 401 (the UI maps it to "sign in to see this")', async () => {
    fetchMock.mockImplementationOnce(async () => jsonResponse({ detail: 'Not authenticated' }, 401));
    const err = await api.observability.cacheStats(100).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(WorkerError);
    expect((err as WorkerError).status).toBe(401);
    expect((err as WorkerError).name).toBe('WorkerError');
  });
});
