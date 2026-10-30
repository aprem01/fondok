/**
 * /diag prints which build is serving on both sides (2026-10-30): the web
 * bundle's sha + env from GET /api/version and the worker's build_sha /
 * build_time from /health. Unknown values render as an em dash, never 0 or
 * a placeholder.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';

// The page reads the flag at module load, so set it before the hoisted import.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_SHOW_DIAG = 'true';
});

vi.mock('next/navigation', () => ({ notFound: () => { throw new Error('notFound'); } }));

const health = vi.fn();
vi.mock('@/lib/api', () => ({
  workerUrl: () => 'http://test-worker.local',
  isWorkerConnected: () => true,
  api: {
    health: () => health(),
    deals: { list: async () => [] },
    observability: { agentCosts: async () => ({ samples: 0, total_cost_usd: 0, by_agent: [] }) },
  },
}));

import DiagPage from '@/app/diag/page';

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

describe('/diag build versions', () => {
  const originalFetch = global.fetch;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(async () => jsonResponse({ sha: 'f'.repeat(40), env: 'production' }));
    global.fetch = fetchMock as unknown as typeof fetch;
    health.mockResolvedValue({
      status: 'ok', version: '0.6.1', db: 'ok', db_migrations_dsn: 'owner',
      build_sha: 'w'.repeat(40), build_time: '2026-10-30T14:02:11Z',
    });
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('shows the web sha/env from /api/version and the worker build_sha/build_time from /health', async () => {
    render(<DiagPage />);
    expect(await screen.findByText('f'.repeat(40))).toBeInTheDocument();
    expect(screen.getByText('production')).toBeInTheDocument();
    expect(await screen.findByText('w'.repeat(40))).toBeInTheDocument();
    expect(screen.getByText('2026-10-30T14:02:11Z')).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/api/version');
    expect(init.cache).toBe('no-store');
  });

  it('renders an em dash for unknown values and surfaces a failed version read', async () => {
    fetchMock.mockImplementationOnce(async () => jsonResponse({ error: 'nope' }, 503));
    health.mockResolvedValue({ status: 'ok', version: '0.6.1', db: 'ok', build_sha: null, build_time: null });
    render(<DiagPage />);
    expect(await screen.findByTestId('diag-web-version-error')).toHaveTextContent('HTTP 503');
    const dashes = await screen.findAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(2);
  });
});
