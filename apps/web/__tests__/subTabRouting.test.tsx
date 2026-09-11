/**
 * Sub-tab routing convention — `?tab=<tab>&sub=<subtab>` (FON-59 #4, FON-61 §3).
 *
 * Contracts locked here (all against the shared `useSubTab` hook, exercised
 * through its first host, Financials):
 *
 *  1. DEEP LINK LANDS ON THE NAMED SUB-TAB. `?sub=projections` opens
 *     Financials → Projections, not Historicals (the default).
 *
 *  2. A PARAM CHANGE UNDER A MOUNTED COMPONENT IS HONOURED. The old
 *     `useState`-initializer read happened once, so a same-tab link (Overview /
 *     Market → Financials while Financials was already open) was silently
 *     ignored. The hook re-syncs on every `searchParams` change.
 *
 *  3. `fin=` IS AN ACCEPTED ALIAS FOR ONE RELEASE, so the Data Room's existing
 *     deep links and any bookmarks keep working.
 *
 *  4. `setSub` PRESERVES EVERY OTHER QUERY PARAM (`doc`, `focus`, …) — it
 *     `router.replace`s the same URL with `sub` added, dropping only the legacy
 *     alias it just superseded.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

// Mutable routing state — `params` is a REAL URLSearchParams (what Next's
// ReadonlyURLSearchParams behaves like), so the hook's `toString()` round-trip
// is exercised rather than stubbed.
const nav = vi.hoisted(() => ({
  params: new URLSearchParams(''),
  pathname: '/projects/deal-uuid-1',
  push: vi.fn(),
  replace: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  usePathname: () => nav.pathname,
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
  useSearchParams: () => nav.params,
}));

// The two sub-tab panels are stubbed — this suite is about which one routing
// selects, not what either renders.
vi.mock('@/components/project/pl/GroundedWorksheet', async () => {
  const react = await import('react');
  return {
    default: () => react.createElement('div', { 'data-testid': 'historicals-panel' }),
    fieldMatchesKey: () => false,
  };
});
vi.mock('@/components/project/pl/ProjectionsSection', async () => {
  const react = await import('react');
  return { default: () => react.createElement('div', { 'data-testid': 'projections-panel' }) };
});
// Hoisted with the mocks that use it (vi.mock calls are lifted above the file).
const stub = vi.hoisted(() => (testid: string) => async () => {
  const react = await import('react');
  return { default: () => react.createElement('div', { 'data-testid': testid }) };
});
vi.mock('@/components/project/EngineHeader', stub('engine-header'));
vi.mock('@/components/project/EngineRightRail', stub('engine-right-rail'));
vi.mock('@/components/project/EngineRunHistory', stub('engine-run-history'));
vi.mock('@/components/project/WhatJustHappened', stub('what-just-happened'));

// A single worker expense year is enough for PLTab to build a statement and
// render the real sub-tab nav (the builder is null-safe on every other field).
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, total_revenue: 10_000_000, gop: 4_000_000, noi: 2_500_000 }] },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(),
    }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({ deal: { id: 'deal-uuid-1', keys: 132 }, loading: false, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({ documents: [], extractions: {}, loading: false, refresh: vi.fn() }),
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import PLTab from '@/components/project/PLTab';

const tab = (name: string) => screen.getByRole('tab', { name });

beforeEach(() => {
  cleanup();
  nav.params = new URLSearchParams('');
  nav.push.mockClear();
  nav.replace.mockClear();
});

describe('useSubTab — `?tab=pl&sub=<subtab>` deep links', () => {
  it('lands on Historicals with no sub param (the declared fallback)', () => {
    render(<PLTab />);
    expect(tab('Historicals')).toHaveAttribute('aria-selected', 'true');
    expect(tab('Projections')).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByTestId('historicals-panel')).toBeInTheDocument();
  });

  it('opens Projections on ?sub=projections (FON-59 #4 / FON-61 §3)', () => {
    nav.params = new URLSearchParams('tab=pl&sub=projections');
    render(<PLTab />);
    expect(tab('Projections')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('projections-panel')).toBeInTheDocument();
    expect(screen.queryByTestId('historicals-panel')).not.toBeInTheDocument();
  });

  it('ignores an unknown sub value and falls back to Historicals', () => {
    nav.params = new URLSearchParams('tab=pl&sub=not-a-sub-tab');
    render(<PLTab />);
    expect(tab('Historicals')).toHaveAttribute('aria-selected', 'true');
  });

  it('follows a param change while already mounted (Defect 3 regression)', () => {
    nav.params = new URLSearchParams('tab=pl&sub=projections');
    const { rerender } = render(<PLTab />);
    expect(tab('Projections')).toHaveAttribute('aria-selected', 'true');

    // Same-tab link followed without a remount — the old `useState` read
    // happened once, so this used to stay on Projections.
    nav.params = new URLSearchParams('tab=pl&sub=historicals');
    rerender(<PLTab />);
    expect(tab('Historicals')).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('historicals-panel')).toBeInTheDocument();
  });

  it('accepts the legacy ?fin= alias for one release (Data Room bookmarks)', () => {
    nav.params = new URLSearchParams('tab=pl&fin=projections');
    render(<PLTab />);
    expect(tab('Projections')).toHaveAttribute('aria-selected', 'true');
  });
});

describe('useSubTab — setSub reflects the click back into the URL', () => {
  it('replaces with sub= while preserving every other param (doc, focus)', () => {
    nav.params = new URLSearchParams('tab=pl&fin=historicals&doc=doc-9&focus=noi');
    render(<PLTab />);
    expect(tab('Historicals')).toHaveAttribute('aria-selected', 'true');

    fireEvent.click(tab('Projections'));

    expect(nav.replace).toHaveBeenCalledTimes(1);
    const [url, opts] = nav.replace.mock.calls[0] as [string, { scroll: boolean }];
    expect(opts).toEqual({ scroll: false });
    const [path, query] = url.split('?');
    expect(path).toBe('/projects/deal-uuid-1');
    const written = new URLSearchParams(query);
    expect(written.get('sub')).toBe('projections');
    expect(written.get('doc')).toBe('doc-9');
    expect(written.get('focus')).toBe('noi');
    expect(written.get('tab')).toBe('pl');
    // The alias it just superseded is dropped so the two can never disagree.
    expect(written.get('fin')).toBeNull();

    // …and the click is reflected in the UI without waiting for the router.
    expect(tab('Projections')).toHaveAttribute('aria-selected', 'true');
  });
});
