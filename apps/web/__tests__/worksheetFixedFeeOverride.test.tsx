/**
 * Review M-1 (2026-11) — the management fee and FF&E reserve are a % of each
 * year's revenue. An analyst's Year-1 dollar override on the worksheet stays a
 * FIXED dollar figure (it does not follow renovation displacement), so the row
 * says so whenever that override is in force — and only then.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { LIVE_DEAL_ID } from './helpers/fon41LiveFixture';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useSearchParams: () => ({ get: () => null }),
}));

const updateSpy = vi.fn(async () => ({}));
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
      deals: { update: (...a: unknown[]) => updateSpy(...(a as [])), get: vi.fn(), status: vi.fn() },
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
    deal: { id: 'e577f547-a3cd-4e78-9ee1-8d761b0c4777', keys: 132, field_overrides: { mgmt_fee: { value: 410_000, note: 'HMA minimum fee' } } },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTrace: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import GroundedWorksheet from '@/components/project/pl/GroundedWorksheet';


beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn() as unknown as typeof Element.prototype.scrollIntoView;
  window.localStorage.clear();
});
afterEach(cleanup);

describe('Worksheet — a Year-1 fee override is labelled a fixed dollar amount', () => {
  it('labels the overridden Management Fee row and leaves FF&E unlabelled', async () => {
    render(<GroundedWorksheet dealId={LIVE_DEAL_ID} />);
    await waitFor(() => expect(document.querySelector('table')).toBeTruthy(), { timeout: 8000 });
    const chips = await screen.findAllByText('Fixed Year-1 $');
    expect(chips).toHaveLength(1);
    expect(chips[0].getAttribute('title')).toMatch(/not a % of revenue/);
    expect(chips[0].getAttribute('title')).toMatch(/does not follow renovation displacement/);
    const row = chips[0].closest('tr') as HTMLElement;
    expect(row.textContent).toMatch(/Management Fee/);
  });
});
