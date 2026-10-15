'use client';
/**
 * Cache-hit badge on the Analysis tab header: the worker's
 * ``/observability/cache-stats`` over the caller's last 100 model calls.
 *
 * The endpoint needs a signed-in session and scopes to the CALLER's tenant,
 * so the read goes through the authed ``api`` client (Bearer + X-Tenant-Id)
 * — a raw ``fetch`` 401'd in production and left the badge on a permanent
 * "Cache hit: —" (review finding). The figure is therefore this workspace's
 * hit rate, not a fleet-wide one. A 401 renders an explicit "sign in to see"
 * state rather than a blank dash.
 */
import { useEffect, useState } from 'react';
import { Badge } from '@/components/ui/Badge';
import { api, isWorkerConnected, type CacheStatsResponse } from '@/lib/api';

export type CacheBadgeState =
  | { kind: 'loading' }
  | { kind: 'ready'; stats: CacheStatsResponse }
  | { kind: 'signed_out' }
  | { kind: 'error'; message: string };

export const CACHE_SIGN_IN_LABEL = 'Cache hit: sign in to see';
export const CACHE_SIGN_IN_TITLE =
  'Sign in to see the cache hit rate — the observability endpoint needs a signed-in session and shows your workspace’s calls only.';

/** Pure: the fetch outcome → what the badge shows (exported for tests). */
export function cacheBadgeStateFromError(e: unknown): CacheBadgeState | null {
  const err = (e ?? {}) as { name?: string; status?: unknown; message?: string };
  if (err.name === 'AbortError') return null;
  if (err.name === 'WorkerError' && err.status === 401) return { kind: 'signed_out' };
  return { kind: 'error', message: err.message ? String(err.message) : String(e) };
}

export function CacheHitBadge() {
  const [state, setState] = useState<CacheBadgeState>({ kind: 'loading' });

  useEffect(() => {
    if (!isWorkerConnected()) {
      setState({ kind: 'error', message: 'worker url not configured' });
      return;
    }
    const ctrl = new AbortController();
    api.observability
      .cacheStats(100, ctrl.signal)
      .then((stats) => setState({ kind: 'ready', stats }))
      .catch((e: unknown) => {
        const next = cacheBadgeStateFromError(e);
        if (next) setState(next);
      });
    return () => ctrl.abort();
  }, []);

  if (state.kind === 'signed_out') {
    return (
      <span title={CACHE_SIGN_IN_TITLE} className="inline-flex" data-testid="cache-hit-badge-signed-out">
        <Badge tone="gray">{CACHE_SIGN_IN_LABEL}</Badge>
      </span>
    );
  }
  if (state.kind !== 'ready' || state.stats.samples === 0) {
    const title =
      state.kind === 'error' ? state.message : state.kind === 'loading' ? 'loading cache stats…' : 'no cache data yet in this workspace';
    return (
      <span title={title} className="inline-flex" data-testid="cache-hit-badge-empty">
        <Badge tone="gray">Cache hit: —</Badge>
      </span>
    );
  }
  const stats = state.stats;
  const pct = Math.round(stats.cache_hit_rate * 1000) / 10;
  const totals = stats.totals;
  const tooltip =
    `Last ${stats.samples} model calls in your workspace\n` +
    `Cache reads: ${totals.cache_read_tokens.toLocaleString()} tokens\n` +
    `Cache writes: ${totals.cache_creation_tokens.toLocaleString()} tokens\n` +
    `Plain input: ${totals.input_tokens.toLocaleString()} tokens\n` +
    `Output: ${totals.output_tokens.toLocaleString()} tokens\n` +
    `Estimated spend: $${totals.estimated_cost_usd.toFixed(4)}`;
  return (
    <span title={tooltip} className="inline-flex" data-testid="cache-hit-badge">
      <Badge tone={pct >= 80 ? 'green' : pct >= 30 ? 'amber' : 'gray'}>
        Cache hit: {pct.toFixed(1)}%
      </Badge>
    </span>
  );
}
