/**
 * Financials → Historicals — the Period control is a REAL filter (FON-41 #4).
 *
 * Sam, 2026-09-11 (hit live): "the current Historicals period controls do not
 * change the table as expected." The control offered Full Year / Trailing 12 /
 * Monthly and changed nothing — the component said so itself: "Period +
 * Granularity are the design's view chrome … presentational".
 *
 * It now filters the columns by their resolved `periodBasis`:
 *   • Full Year hides the trailing-twelve column, and vice versa.
 *   • A basis with no columns is DISABLED — never a blank grid.
 *   • "All periods" is the default, so a statement the Data Room is still
 *     badging is never filtered out from under the analyst.
 *
 * Drives the real loaders over the live-shaped fixture (one T-12 + three
 * annual P&Ls); only the api is mocked.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, waitFor, fireEvent } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { LIVE_DEAL_ID } from './helpers/fon41LiveFixture';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

vi.mock('@/lib/api', async () => {
  const fx = await import('./helpers/fon41LiveFixture');
  return {
    isWorkerConnected: () => true,
    workerUrl: () => 'http://worker.test',
    api: {
      documents: {
        list: async () => fx.LIVE_DOCS,
        extraction: async (_deal: string, docId: string) => {
          const r = fx.LIVE_EXTRACTIONS[docId];
          if (!r) throw new Error(`no extraction for ${docId}`);
          return r;
        },
        reviewField: vi.fn(async () => ({})),
      },
      deals: { update: vi.fn(async () => ({})), get: vi.fn(), status: vi.fn() },
    },
  };
});

const OUTPUTS = {
  deal_id: LIVE_DEAL_ID,
  engines: {
    expense: {
      deal_id: LIVE_DEAL_ID, engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 2025, total_revenue: 12_500_000, gop: 5_000_000, noi: 4_000_000 }] },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r',
    },
  },
} as unknown as EngineOutputsResponse;
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: OUTPUTS, previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});
vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle', error: null }) }));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777', keys: 132, field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';

const headers = () => screen.getAllByRole('columnheader').map((th) => th.textContent?.trim() ?? '');
const periodSelect = () => screen.getByLabelText('Period basis') as HTMLSelectElement;

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.localStorage.clear();
});
afterEach(cleanup);

describe('Historicals — the Period control filters the columns by basis', () => {
  it('shows every period by default, then hides the T-12 when Full Year is picked', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(headers()).toContain('T12 Mar 2025'), { timeout: 8000 });
    // Default: nothing filtered — the annual statements AND the T-12 are here.
    expect(headers()).toContain('FY2024');
    expect(periodSelect().value).toBe('ALL');

    fireEvent.change(periodSelect(), { target: { value: 'FY' } });
    await waitFor(() => expect(headers()).not.toContain('T12 Mar 2025'));
    expect(headers()).toContain('FY2024');
    expect(headers()).toContain('FY2019');
  }, 12000);

  it('hides the full-year columns when Trailing 12 is picked', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(headers()).toContain('T12 Mar 2025'), { timeout: 8000 });

    fireEvent.change(periodSelect(), { target: { value: 'T12' } });
    await waitFor(() => expect(headers()).not.toContain('FY2024'));
    expect(headers()).toContain('T12 Mar 2025');
    expect(headers()).not.toContain('FY2019');
  }, 12000);

  it('disables a basis with no columns instead of blanking the grid', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(headers()).toContain('T12 Mar 2025'), { timeout: 8000 });

    const options = Array.from(periodSelect().options);
    const byValue = (v: string) => options.find((o) => o.value === v)!;
    // This deal has no YTD statement — the option is present, counted 0, and
    // cannot be selected.
    expect(byValue('YTD').disabled).toBe(true);
    expect(byValue('YTD').textContent).toContain('· 0');
    expect(byValue('FY').disabled).toBe(false);
    expect(byValue('T12').disabled).toBe(false);
    // Every option carries its column count, so nothing is silently filtered.
    expect(byValue('FY').textContent).toContain('· 3');
    expect(byValue('T12').textContent).toContain('· 1');
    // A basis with no columns is never even reachable, so the grid stands.
    expect(headers().length).toBeGreaterThan(1);
  }, 12000);
});
