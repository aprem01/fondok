/**
 * W5 "price" — a deal with no purchase price never dead-ends on CAPEX.
 *
 * When the capital engine was refused for a missing identity input
 * (`missing_identity_inputs`), the CAPEX tab renders the Deal Summary inputs
 * (Purchase Price, and Keys when it is missing too) — pre-filled from the OM's
 * stated asking price with its source badge, or saying the OM states none —
 * instead of the "Upload an OM" card. Saving writes the analyst override and
 * runs the model. The "Upload an OM" card appears only when the deal has no
 * OM document, and its Run button really runs the model.
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

const PRICE_MESSAGE =
  "The deal has no purchase price. Enter the purchase price on the deal's Overview or CAPEX tab and run the model again — the model never substitutes a placeholder property.";

function failedCapital(missing: string[]): EngineOutputsResponse {
  return {
    deal_id: 'deal-uuid-1',
    engines: {
      capital: {
        deal_id: 'deal-uuid-1', engine: 'capital', status: 'failed', summary: '',
        outputs: null, inputs: null, error: PRICE_MESSAGE,
        failure: { engine: 'capital', reason: 'missing_identity_inputs', message: PRICE_MESSAGE, missing },
        runtime_ms: null, started_at: null, completed_at: null, run_id: 'run-1',
      },
    },
  } as unknown as EngineOutputsResponse;
}

const NEVER_RUN = { deal_id: 'deal-uuid-1', engines: {} } as unknown as EngineOutputsResponse;

let mockOutputs: EngineOutputsResponse = failedCapital(['purchase_price']);
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

const refreshDealSpy = vi.fn();
let mockDeal: Record<string, unknown> = { id: 'deal-uuid-1', keys: 132, field_overrides: {} };
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDeal, status: null, loading: false, error: null, workerConfigured: true, refresh: refreshDealSpy,
  }),
}));

let mockDocs: Array<{ id: string; doc_type: string | null; filename: string; status: string }> = [];
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({ documents: mockDocs, extractions: {}, settled: true, loading: false, error: null }),
}));

vi.mock('@/lib/hooks/useHistoricalBaseline', () => ({ useHistoricalBaseline: () => ({ baseline: null }) }));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      engines: { ...actual.api.engines, timeline: async () => null },
      market: { ...actual.api.market, transactionComps: async () => null },
    },
  };
});

vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

let mockSource: { source: string; value: unknown; reason: string | null; field: Record<string, unknown> | null } | null = null;
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key: string | undefined) => (key === 'purchase_price' ? mockSource : null),
}));
vi.mock('@/lib/hooks/useValueTrace', () => ({ useTraceGraph: () => ({ get: () => null }) }));

import InvestmentTab from '@/components/project/InvestmentTab';

const OM_DOC = { id: 'om-1', doc_type: 'OM', filename: 'Angler OM.pdf', status: 'EXTRACTED' };

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
  mockOutputs = failedCapital(['purchase_price']);
  mockDeal = { id: 'deal-uuid-1', keys: 132, field_overrides: {} };
  mockDocs = [OM_DOC];
  mockSource = { source: '', value: null, reason: 'missing_identity_inputs', field: null };
});

describe('CAPEX — capital refused for a missing purchase price', () => {
  it('renders the Deal Summary inputs, not the "Upload an OM" card', () => {
    render(<InvestmentTab />);
    expect(screen.getByTestId('identity-inputs-card')).toBeInTheDocument();
    expect(screen.queryByText(/Investment Engine unavailable/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Upload an/)).not.toBeInTheDocument();
    expect(screen.getByTestId('purchase-price')).toBeInTheDocument();
    // Keys is present on the deal → no keys input in the card.
    expect(screen.queryByLabelText('Override room count')).not.toBeInTheDocument();
  });

  it('says the OM states no price when the extracted OM carries none', () => {
    render(<InvestmentTab />);
    expect(screen.getByText("The OM doesn't state a purchase price.")).toBeInTheDocument();
    expect(screen.getByTestId('purchase-price')).toHaveTextContent('Enter purchase price');
  });

  it('pre-fills the OM asking price with its OM badge and page', () => {
    mockSource = {
      source: 'om_broker', value: 40_000_000, reason: null,
      field: { field: 'asking_price.headline_price_usd', page: 4, document_id: 'om-1', filename: 'Angler OM.pdf' },
    };
    render(<InvestmentTab />);
    expect(screen.getByTestId('purchase-price')).toHaveTextContent('$40,000,000');
    const badge = screen.getByTestId('purchase-price-source');
    expect(badge).toHaveTextContent('OM');
    expect(badge).toHaveTextContent('p. 4');
    expect(screen.queryByText("The OM doesn't state a purchase price.")).not.toBeInTheDocument();
  });

  it('saving writes the analyst override {value, note} and runs the model', async () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByTestId('purchase-price'));
    fireEvent.change(screen.getByLabelText('Purchase price'), { target: { value: '36400000' } });
    fireEvent.change(screen.getByLabelText('Override justification'), {
      target: { value: "Sam's model price (FON-67)" },
    });
    fireEvent.click(screen.getByLabelText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [id, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(id).toBe('deal-uuid-1');
    expect(body.field_overrides.purchase_price).toEqual({ value: 36_400_000, note: "Sam's model price (FON-67)" });
    await waitFor(() => expect(engineRunSpy).toHaveBeenCalledTimes(1));
    expect(refreshDealSpy).toHaveBeenCalled();
  });

  it('refuses a save with no justification — nothing written, nothing run', async () => {
    render(<InvestmentTab />);
    fireEvent.click(screen.getByTestId('purchase-price'));
    fireEvent.change(screen.getByLabelText('Purchase price'), { target: { value: '36400000' } });
    fireEvent.click(screen.getByLabelText('Save'));
    await new Promise((r) => setTimeout(r, 0));
    expect(updateSpy).not.toHaveBeenCalled();
    expect(engineRunSpy).not.toHaveBeenCalled();
  });

  it('shows the Keys input too when the key count is missing', () => {
    mockOutputs = failedCapital(['keys', 'purchase_price']);
    mockDeal = { id: 'deal-uuid-1', keys: null, field_overrides: {} };
    render(<InvestmentTab />);
    expect(screen.getByLabelText('Override room count')).toBeInTheDocument();
  });

  it('with no OM at all it still renders the inputs (and says no OM is uploaded)', () => {
    mockDocs = [];
    render(<InvestmentTab />);
    expect(screen.getByTestId('identity-inputs-card')).toBeInTheDocument();
    expect(screen.queryByText(/Investment Engine unavailable/)).not.toBeInTheDocument();
    expect(screen.getByText(/No Offering Memorandum is in the Data Room/)).toBeInTheDocument();
  });
});

describe('CAPEX — capital has no output for another reason', () => {
  it('shows the "Upload an OM" card only when the deal has no OM document', () => {
    mockOutputs = NEVER_RUN;
    mockDocs = [];
    render(<InvestmentTab />);
    expect(screen.getByText(/Upload an/)).toBeInTheDocument();
    cleanup();

    mockDocs = [OM_DOC];
    render(<InvestmentTab />);
    expect(screen.queryByText(/Upload an/)).not.toBeInTheDocument();
    expect(screen.getByTestId('capital-not-run-card')).toBeInTheDocument();
  });

  it('"Run Investment Engine" actually runs the model', async () => {
    mockOutputs = NEVER_RUN;
    mockDocs = [];
    render(<InvestmentTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Run Investment Engine' }));
    await waitFor(() => expect(engineRunSpy).toHaveBeenCalledTimes(1));
  });

  it('with an OM on file, the card names the failure and its Run button runs', async () => {
    mockOutputs = NEVER_RUN;
    render(<InvestmentTab />);
    fireEvent.click(screen.getByRole('button', { name: 'Run Investment Engine' }));
    await waitFor(() => expect(engineRunSpy).toHaveBeenCalledTimes(1));
  });
});
