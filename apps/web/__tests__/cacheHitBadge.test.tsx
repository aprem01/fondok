/**
 * CacheHitBadge (Analysis tab header) — reads the worker's cache-stats
 * through the authed ``api`` client and never leaves a 401 as a blank dash.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';

// A plain delegate rather than a vi.fn: in vitest 2.1 a spy whose result is a
// rejected promise surfaces that rejection as an unhandled test error even
// though the component catches it (seen with mockReset / mockResolvedValue in
// the same file). The delegate records calls itself.
const h = vi.hoisted(() => ({
  impl: null as null | ((n?: number, signal?: AbortSignal) => Promise<unknown>),
  calls: [] as unknown[][],
}));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      observability: {
        ...actual.api.observability,
        cacheStats: (n?: number, signal?: AbortSignal) => {
          h.calls.push([n, signal]);
          if (!h.impl) throw new Error('cacheStats impl not set');
          return h.impl(n, signal);
        },
      },
    },
  };
});

import { WorkerError } from '@/lib/api';
import { CacheHitBadge, cacheBadgeStateFromError, CACHE_SIGN_IN_LABEL } from '@/components/project/CacheHitBadge';

beforeEach(() => {
  h.impl = null;
  h.calls = [];
});
afterEach(() => cleanup());

describe('CacheHitBadge', () => {
  it('reads through api.observability.cacheStats (the authed client) and shows the workspace hit rate', async () => {
    h.impl = () =>
      Promise.resolve({
        cache_hit_rate: 0.853,
        samples: 100,
        totals: { input_tokens: 10, cache_read_tokens: 90, cache_creation_tokens: 5, output_tokens: 20, estimated_cost_usd: 0.0123 },
      });
    render(<CacheHitBadge />);
    const badge = await screen.findByTestId('cache-hit-badge');
    expect(badge).toHaveTextContent('Cache hit: 85.3%');
    expect(badge.getAttribute('title')).toMatch(/Last 100 model calls in your workspace/);
    expect(h.calls).toHaveLength(1);
    expect(h.calls[0][0]).toBe(100);
    expect(h.calls[0][1]).toBeInstanceOf(AbortSignal);
  });

  it('a 401 renders an explicit "sign in to see" state, not a blank dash', async () => {
    h.impl = () => Promise.reject(new WorkerError('GET /observability/cache-stats?n=100 → 401', 401, 'Not authenticated'));
    render(<CacheHitBadge />);
    const badge = await screen.findByTestId('cache-hit-badge-signed-out');
    expect(badge).toHaveTextContent(CACHE_SIGN_IN_LABEL);
    expect(badge.getAttribute('title')).toMatch(/needs a signed-in session/);
    expect(screen.queryByText('Cache hit: —')).toBeNull();
  });

  it('other failures keep the dash with the reason on hover; an abort is ignored', async () => {
    h.impl = () => Promise.reject(new WorkerError('GET → 503', 503, ''));
    render(<CacheHitBadge />);
    const badge = await screen.findByTestId('cache-hit-badge-empty');
    expect(badge).toHaveTextContent('Cache hit: —');
    expect(badge.getAttribute('title')).toBe('GET → 503');
    const abort = new Error('aborted');
    abort.name = 'AbortError';
    expect(cacheBadgeStateFromError(abort)).toBeNull();
    expect(cacheBadgeStateFromError(new WorkerError('x', 401, ''))).toEqual({ kind: 'signed_out' });
  });
});
