/**
 * IC Memo — the entry cap tile shows the NOI it is struck on (FON-122, Sam's QA
 * 2026-11-26 on deal b67bfdee).
 *
 * Sam read the memo tile "Going-In Cap Rate 8.32%" beside "NOI (Y1) $1.65M" as
 * an error: 8.32% of the $36.4M price is $3.03M, not $1.65M. The cap is struck
 * on the ENTRY NOI (Year 1 at T-12 occupancy and ADR, no displacement, before
 * FF&E reserve — `expense.run_rate_noi_at_entry`), which the tile now prints
 * under the cap with that basis; the in-place cap (T-12) sits beside it with the
 * T-12 NOI behind it. Anything the run did not publish is "—", never derived.
 * The thesis names both figures and takes the article the number is read with
 * ("an 8.3%", never "a 8.3%").
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-b67bfdee' }),
}));

const row = (engine: string, outputs: Record<string, unknown>) => ({
  deal_id: 'deal-b67bfdee', engine, status: 'complete', summary: '', outputs,
  inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
});

/** Sam's deal: price $36.4M, projected Year 1 NOI $1.65M (displaced), entry NOI $3,028,877 → 8.32%; T-12 NOI $2,347,800 → 6.45%. */
function outputs(expense: Record<string, unknown>): EngineOutputsResponse {
  return {
    deal_id: 'deal-b67bfdee',
    engines: {
      capital: row('capital', {
        purchase_price: 36_400_000, price_per_key: 275_758, entry_cap_rate: 0.0832,
        total_capital: 45_000_000, equity_amount: 18_000_000, debt_amount: 27_000_000,
        uses: [{ label: 'Purchase Price', amount: 36_400_000 }, { label: 'Renovation Budget', amount: 4_620_000 }],
      }),
      returns: row('returns', { levered_irr: 0.16, unlevered_irr: 0.10, equity_multiple: 1.9, hold_years: 5, gross_sale_price: 52_000_000 }),
      expense: row('expense', expense),
      revenue: row('revenue', {
        years: [{ year: 1, revpar: 178.77, adr: 254.04, occupancy: 0.7037, total_revenue: 11_000_000 }],
        total_revenue_cagr: 0.08,
      }),
      debt: row('debt', { year_one_dscr: 1.2, year_one_debt_yield: 0.09, interest_rate: 0.0766 }),
    },
  } as unknown as EngineOutputsResponse;
}

const fx = vi.hoisted(() => ({
  outputs: null as unknown,
}));

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({ outputs: fx.outputs, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}) }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-b67bfdee', name: 'Kimpton Angler', city: 'Miami Beach, FL', keys: 132, brand: 'Kimpton', status: 'In Progress', field_overrides: {} },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [], critical: 0, warn: 0, info: 0, note: null, loading: false, error: null }),
}));

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: vi.fn(async () => ({ id: 'deal-b67bfdee' })), setStatus: vi.fn(async () => ({ id: 'deal-b67bfdee' })) },
      scenarios: { ...actual.api.scenarios, list: vi.fn(async () => []), compare: vi.fn(async () => ({ deal_id: 'deal-b67bfdee', base_scenario_id: null, scenarios: [] })) },
    },
  };
});

import ICMemoTab from '@/components/project/ICMemoTab';
import { ENTRY_NOI_BASIS, IN_PLACE_CAP_LABEL, IN_PLACE_CAP_SUB } from '@/lib/purchasePrice';
import type { Project } from '@/lib/project';

const PROJECT = { id: 0, name: 'Kimpton Hotel' } as unknown as Project;

/** The Deal-snapshot tile for a label (the button whose aria-label traces it). */
function tile(label: string): HTMLElement {
  return screen.getByRole('button', { name: `Trace ${label} to source` });
}

beforeEach(() => {
  cleanup();
});

describe('ICMemoTab — the entry cap shows the entry NOI it is struck on (FON-122, b67bfdee)', () => {
  it('prints 8.32% over the $3.03M entry NOI with its basis, the $1.65M Year-1 NOI stays its own tile, and the T-12 cap sits beside it', () => {
    fx.outputs = outputs({
      years: [{ year: 1, noi: 1_210_000, noi_institutional: 1_650_000 }],
      run_rate_noi_at_entry: 3_028_877,
      t12_ebitda: 2_347_800,
    });
    render(<ICMemoTab project={PROJECT} />);

    const cap = tile('Going-In Cap Rate');
    expect(within(cap).getByText('8.32%')).toBeInTheDocument(); // 3,028,877 ÷ 36,400,000
    expect(within(cap).getByText('Entry NOI $3.03M')).toBeInTheDocument();
    expect(within(cap).getByText(ENTRY_NOI_BASIS)).toBeInTheDocument();
    expect(ENTRY_NOI_BASIS).toBe('Year 1 at T-12 occupancy and ADR · no displacement · before FF&E reserve');

    // The projected Year 1 is a different figure and keeps its own tile — no longer read as a contradiction.
    expect(within(tile('NOI (Y1)')).getByText('$1.65M')).toBeInTheDocument();

    const inPlace = tile(IN_PLACE_CAP_LABEL);
    expect(within(inPlace).getByText('6.45%')).toBeInTheDocument(); // 2,347,800 ÷ 36,400,000
    expect(within(inPlace).getByText('T-12 NOI $2.35M')).toBeInTheDocument();
    expect(within(inPlace).getByText(IN_PLACE_CAP_SUB)).toBeInTheDocument();

    // The underwriting summary carries the same two rows.
    expect(screen.getByText('8.32% · Entry NOI $3.03M')).toBeInTheDocument();
    expect(screen.getByText('6.45% · T-12 NOI $2.35M')).toBeInTheDocument();

    // The thesis names both figures, with the article the number is read with.
    const thesis = screen.getByText(/entering on an 8\.3% going-in cap/).textContent ?? '';
    expect(thesis).toContain(`against $3.0M of entry NOI (${ENTRY_NOI_BASIS})`);
    expect(thesis).toMatch(/not the \$1\.[67]M projected Year-1 NOI/);
    expect(thesis).not.toMatch(/\ba 8\.3%/);
    // ...and so does the revenue-CAGR risk ("an 8.0%", never "a 8.0%").
    expect(screen.getByText(/Pro forma assumes an 8\.0% revenue CAGR/)).toBeInTheDocument();
    expect(screen.queryByText(/\ba 8\.0% revenue CAGR/)).not.toBeInTheDocument();
  });

  it('renders "—" for the entry NOI and the T-12 cap when the run published neither, and never derives them', () => {
    fx.outputs = outputs({ years: [{ year: 1, noi: 1_210_000, noi_institutional: 1_650_000 }] });
    render(<ICMemoTab project={PROJECT} />);

    // No entry NOI on the run: the cap falls back to Year 1 ÷ price (4.53%) and says the entry NOI is unsourced.
    const cap = tile('Going-In Cap Rate');
    expect(within(cap).getByText('4.53%')).toBeInTheDocument();
    expect(within(cap).getByText('Entry NOI —')).toBeInTheDocument();

    const inPlace = tile(IN_PLACE_CAP_LABEL);
    expect(within(inPlace).getByText('—')).toBeInTheDocument();
    expect(within(inPlace).getByText('T-12 NOI —')).toBeInTheDocument();
    expect(screen.getByText('— · T-12 NOI —')).toBeInTheDocument();
    // The thesis quotes the Year-1 NOI the cap is actually on, with the right article.
    expect(screen.getByText(/entering on a 4\.5% going-in cap against \$1\.[67]M of Year-1 NOI/)).toBeInTheDocument();
  });
});
