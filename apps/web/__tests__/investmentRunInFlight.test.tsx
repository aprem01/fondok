/**
 * W5 "price" (re-review N1) — CAPEX Run buttons stay disabled for the WHOLE
 * run, not just its kickoff. Drives the REAL `useEngineRun` (run-all: POST
 * kickoff, then a poll of GET …/engines/run/{id}) against a mocked worker
 * whose run stays `running` until the test lets it finish.
 *
 * Also: the Deal Summary's Entry Cap Rate and Price / Key rows say
 * "re-running…" while a run is in flight, and "pending run" when the saved
 * price differs from the price the last run used.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(''),
  usePathname: () => '/projects/deal-uuid-1',
}));

const NEVER_RUN = { deal_id: 'deal-uuid-1', engines: {} } as unknown as EngineOutputsResponse;
const COMPLETE = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
      outputs: {
        price_per_key: 250_000,
        uses: [{ label: 'Purchase Price', amount: 33_000_000 }],
      },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, noi: 2_000_000, noi_institutional: 2_310_000 }] },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

let mockOutputs: EngineOutputsResponse = NEVER_RUN;
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: mockOutputs, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});

let mockDeal: Record<string, unknown> = { id: 'deal-uuid-1', keys: 132, field_overrides: {} };
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDeal, status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({ documents: [], extractions: {}, settled: true, loading: false, error: null, refresh: vi.fn() }),
}));
vi.mock('@/lib/hooks/useHistoricalBaseline', () => ({ useHistoricalBaseline: () => ({ baseline: null }) }));

// The worker's run: kickoff returns at once; the run stays `running` until
// `finishRun()` flips the poll to terminal rows.
let runDone = false;
const runAllSpy = vi.fn(async () => ({
  deal_id: 'deal-uuid-1', run_id: 'run-2', started_at: '2026-11-01T00:00:00Z',
  engines: [{ name: 'capital', status: 'queued' }],
}));
const row = (status: string) => ({
  deal_id: 'deal-uuid-1', engine: 'capital', status, summary: '', outputs: null, inputs: null,
  error: null, runtime_ms: null, started_at: null, completed_at: null, run_id: 'run-2',
});
const getRunSpy = vi.fn(async () => ({
  deal_id: 'deal-uuid-1', run_id: 'run-2', engines: [row(runDone ? 'complete' : 'running')], failures: [],
}));
const finishRun = () => { runDone = true; };
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      engines: {
        ...actual.api.engines,
        timeline: async () => null,
        runAll: (...a: unknown[]) => runAllSpy(...(a as [])),
        getRun: (...a: unknown[]) => getRunSpy(...(a as [])),
      },
      market: { ...actual.api.market, transactionComps: async () => null },
    },
  };
});

vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/project/CapexPlanPanel', () => ({ default: () => null, DEFAULT_CAPEX_PLAN: {} }));
vi.mock('@/components/project/HistoricalBaselinePanel', () => ({ default: () => null }));
vi.mock('@/components/project/RenovationBudgetSources', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: () => null,
  useProvenanceRefresh: () => () => {},
}));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTraceGraph: () => ({ get: () => null }) }));

import InvestmentTab from '@/components/project/InvestmentTab';

beforeEach(() => {
  cleanup();
  runDone = false;
  runAllSpy.mockClear();
  getRunSpy.mockClear();
  updateSpy.mockClear();
  mockOutputs = NEVER_RUN;
  mockDeal = { id: 'deal-uuid-1', keys: 132, field_overrides: {} };
});

describe('CAPEX Run buttons — disabled until the run reaches a terminal state', () => {
  it('stays "Running…" after the kickoff while the poll still reports running', async () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Run Investment Engine' }));
    await waitFor(() => expect(runAllSpy).toHaveBeenCalledTimes(1));
    // The kickoff has resolved and the poll has seen the run in progress.
    await waitFor(() => expect(getRunSpy).toHaveBeenCalled(), { timeout: 3000 });
    const busy = screen.getByRole('button', { name: 'Running…' });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(runAllSpy).toHaveBeenCalledTimes(1);

    finishRun();
    await waitFor(
      () => expect(screen.getByRole('button', { name: 'Run Investment Engine' })).not.toBeDisabled(),
      { timeout: 3000 },
    );
    expect(runAllSpy).toHaveBeenCalledTimes(1);
  });
});

describe('CAPEX Deal Summary — Entry Cap and Price / Key never pass for current mid-run', () => {
  const ROW_ID: Record<string, string> = { 'Entry Cap Rate': 'entryCap', 'Purchase Price / Key': 'ppk' };
  function rowText(label: string): string {
    return screen.queryByTestId(`row-note-${ROW_ID[label]}`)?.textContent ?? '';
  }

  it('saving the price marks both rows "re-running…" until the run completes', async () => {
    mockOutputs = COMPLETE;
    render(<InvestmentTab />);
    expect(rowText('Entry Cap Rate')).not.toMatch(/re-running/);
    fireEvent.click(screen.getByTestId('purchase-price'));
    fireEvent.change(screen.getByLabelText('Purchase price'), { target: { value: '36400000' } });
    fireEvent.change(screen.getByLabelText('Override justification'), { target: { value: 'Our bid' } });
    fireEvent.click(screen.getByLabelText('Save'));
    await waitFor(() => expect(runAllSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(rowText('Entry Cap Rate')).toMatch(/re-running…/));
    expect(rowText('Purchase Price / Key')).toMatch(/re-running…/);

    finishRun();
    await waitFor(() => expect(rowText('Entry Cap Rate')).not.toMatch(/re-running/), { timeout: 3000 });
  });

  it('a saved price the last run did not use reads "pending run" (e.g. the kickoff failed)', () => {
    mockOutputs = COMPLETE; // last run: 250,000 × 132 = $33.0M
    mockDeal = { ...mockDeal, field_overrides: { purchase_price: { value: 36_400_000, note: 'Our bid' } } };
    render(<InvestmentTab />);
    expect(rowText('Entry Cap Rate')).toMatch(/pending run/);
    expect(rowText('Purchase Price / Key')).toMatch(/pending run/);
  });
});
