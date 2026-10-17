/**
 * Run-compare API client paths (regression baselines, 2026-10-16): the panel's
 * calls reach the worker routes GET /deals/{id}/engines/runs,
 * /engines/compare and /engines/compare/xlsx with the right query strings.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';

process.env.NEXT_PUBLIC_WORKER_URL = 'http://test-worker.local';

vi.mock('@/lib/auth', () => ({
  getCurrentOrgId: () => null,
  getClerkSessionToken: async () => null,
  waitForClerkTokenFn: async () => {},
}));

import { api } from '@/lib/api';
import { fmtDeltaAbs, fmtValue, groupFields } from '@/lib/runCompare';

describe('run compare api', () => {
  const originalFetch = global.fetch;
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it('hits the runs, compare and xlsx routes', async () => {
    const urls: string[] = [];
    global.fetch = vi.fn(async (url: RequestInfo | URL) => {
      urls.push(String(url));
      if (String(url).includes('/compare/xlsx')) {
        return new Response(new Blob(['xlsx']), { status: 200 });
      }
      return new Response(JSON.stringify({ runs: [], fields: [] }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }) as typeof fetch;
    const createObjectURL = vi.fn(() => 'blob:x');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {});

    await api.engines.runs('d1', '2026-10-15T21:40:00Z');
    await api.engines.compare('d1', 'run-a', 'latest', { all: true });
    await api.engines.downloadCompareXlsx('d1', 'run-a', 'run-b', 'x.xlsx');

    expect(urls[0]).toBe(
      'http://test-worker.local/deals/d1/engines/runs?before=2026-10-15T21%3A40%3A00Z',
    );
    expect(urls[1]).toBe(
      'http://test-worker.local/deals/d1/engines/compare?base=run-a&head=latest&all=1',
    );
    expect(urls[2]).toBe(
      'http://test-worker.local/deals/d1/engines/compare/xlsx?base=run-a&head=run-b',
    );
    expect(createObjectURL).toHaveBeenCalled();
    expect(click).toHaveBeenCalled();
    click.mockRestore();
  });

  it('formats values without inventing any', () => {
    expect(fmtValue(null, 'usd')).toBe('—');
    expect(fmtValue(36_400_000, 'usd')).toBe('$36,400,000');
    expect(fmtValue(385, 'usd')).toBe('$385.00');
    expect(fmtValue(2.0787, 'x')).toBe('2.08x');
    expect(fmtDeltaAbs(null, 'pct')).toBe('—');
    expect(fmtDeltaAbs(-0.0049, 'pct')).toBe('-0.49 pts');
    const g = groupFields([
      { group: 'Revenue', key: 'a' },
      { group: 'Revenue', key: 'b' },
      { group: 'Debt', key: 'c' },
    ] as never);
    expect(g.map((x) => [x.group, x.rows.length])).toEqual([
      ['Revenue', 2],
      ['Debt', 1],
    ]);
  });
});
