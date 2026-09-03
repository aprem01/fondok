/**
 * Debt tab — canonical rebuild (FON-72, design/canonical/Debt Tab.dc.html).
 *
 * Contracts locked here:
 *
 *  1. ENGINE-SOURCED RENDER — every number resolves from a single mocked
 *     `debt` / `capital` / `returns` engine-outputs envelope (the real
 *     `getEngineField` is exercised). No fixtures, no prototype placeholders.
 *
 *  2. NEW BACKEND FIELDS — the FON-72 fee fields (origination + exit),
 *     the covenants[] table (current / signed headroom / pass state) and the
 *     Debt Schedule (Draws + Total Debt Service rows) all render from the
 *     mocked debt envelope.
 *
 *  3. CANONICAL EDIT PATH — editing the origination fee and the Debt-owned LTV
 *     PATCHes field_overrides via api.deals.update (LTV resizes the senior
 *     tranche principal; the fee writes the senior upfront-fee percent).
 *
 *  No provider is mounted — the tab's provenance dots fall back to the
 *  canonical kind when useTraceGraph finds no context.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

// The worker outputs under test — the whole tab reads from these.
const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    debt: {
      deal_id: 'deal-uuid-1',
      engine: 'debt',
      status: 'complete',
      summary: '',
      outputs: {
        loan_amount: 23_000_000,
        year_one_dscr: 1.35,
        year_one_debt_yield: 0.111,
        avg_dscr: 1.42,
        interest_rate: 0.0725,
        term_years: 5,
        amortization_years: 30,
        origination_fee_pct: 0.75, // 0..10 percent convention → "0.75%"
        origination_fee_usd: 172_500,
        exit_fee_pct: 0.5,
        exit_fee_usd: 115_000,
        covenants: [
          { name: 'ltv', label: 'Loan-to-Value', kind: 'max', current: 0.639, threshold: 0.65, headroom: 0.011, passes: true },
          { name: 'ltc', label: 'Loan-to-Cost', kind: 'max', current: 0.535, threshold: 0.75, headroom: 0.215, passes: true },
          { name: 'dscr', label: 'DSCR (Year 1)', kind: 'min', current: 1.35, threshold: 1.25, headroom: 0.1, passes: true },
          { name: 'debt_yield', label: 'Debt Yield (Year 1)', kind: 'min', current: 0.111, threshold: 0.1, headroom: 0.015, passes: true },
        ],
        schedule: [
          { year: 1, interest: 1_667_500, principal: 0, debt_service: 1_667_500, ending_balance: 23_000_000, dscr: 1.35 },
          { year: 2, interest: 1_667_500, principal: 0, debt_service: 1_667_500, ending_balance: 23_000_000, dscr: 1.4 },
        ],
        monthly_schedule: [
          { month: 1, interest: 138_958, principal: 0, payment: 138_958, ending_balance: 23_000_000 },
          { month: 2, interest: 138_958, principal: 0, payment: 138_958, ending_balance: 23_000_000 },
        ],
        debt_stack: {
          tranches: [{ kind: 'senior', label: 'Senior Loan', loan_amount: 23_000_000, all_in_rate: 0.0725, rate_type: 'fixed' }],
        },
        refi_year: 3,
        refi_cash_out: 4_500_000,
        balance_at_exit: 21_000_000,
      },
      inputs: {},
      error: null,
      runtime_ms: 8,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    capital: {
      deal_id: 'deal-uuid-1',
      engine: 'capital',
      status: 'complete',
      summary: '',
      outputs: {
        purchase_price: 36_000_000,
        total_capital_usd: 43_000_000,
        equity_amount: 20_000_000,
        debt_amount: 23_000_000,
        ltv: 0.639,
        ltc: 0.535,
      },
      inputs: {},
      error: null,
      runtime_ms: 6,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
    returns: {
      deal_id: 'deal-uuid-1',
      engine: 'returns',
      status: 'complete',
      summary: '',
      outputs: { levered_irr: 0.221, equity_multiple: 2.34 },
      inputs: {},
      error: null,
      runtime_ms: 5,
      started_at: null,
      completed_at: null,
      run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

const refreshDealSpy = vi.fn();
// STABLE identity — the mock must return the SAME deal (and the same
// field_overrides object) on every render. Returning a fresh `{}` each call
// makes DebtTab's `useEffect(…, [deal?.field_overrides])` re-fire → setOverrides
// → re-render forever (an infinite passive-effect loop that hangs the test at
// low CPU, never tripping React's synchronous max-depth guard).
const mockDeal = { id: 'deal-uuid-1', keys: 132, field_overrides: {} };
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDeal,
    status: null,
    loading: false,
    error: null,
    fromMock: false,
    refresh: refreshDealSpy,
  }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

// api surface — spy on the field_overrides PATCH.
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
    },
  };
});

// Trim the heavy chrome — the test only cares about the sub-tab bodies.
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import DebtTab from '@/components/project/DebtTab';

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
});

describe('DebtTab — canonical sub-tabs', () => {
  it('renders the four canonical sub-tabs', () => {
    render(<DebtTab />);
    expect(screen.getByText('Debt Overview')).toBeInTheDocument();
    expect(screen.getByText('Loan Terms & Covenants')).toBeInTheDocument();
    expect(screen.getByText('Refinance')).toBeInTheDocument();
    expect(screen.getByText('Debt Schedule')).toBeInTheDocument();
  });
});

describe('DebtTab — fees (new BE fields)', () => {
  it('shows the origination fee (pct · usd) from the debt envelope on Debt Overview', () => {
    render(<DebtTab />);
    expect(screen.getByText('0.75% · $172,500')).toBeInTheDocument();
  });

  it('shows the exit fee on Loan Terms & Covenants', () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    expect(screen.getByText('Exit Fee')).toBeInTheDocument();
    expect(screen.getByText('0.50% · $115,000')).toBeInTheDocument();
  });
});

describe('DebtTab — covenants[] (current / headroom / pass)', () => {
  it('renders the engine covenant table with current values and signed headroom', () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));

    // Column headers.
    expect(screen.getByText('Current')).toBeInTheDocument();
    expect(screen.getByText('Headroom')).toBeInTheDocument();

    // A covenant from the engine (LTV) with its current reading + headroom.
    expect(screen.getByText('Loan-to-Value')).toBeInTheDocument();
    expect(screen.getByText('DSCR (Year 1)')).toBeInTheDocument();
    // LTV current 63.9%, headroom +1.1 pts; DSCR current 1.35x, headroom +0.10x.
    expect(screen.getAllByText('63.9%').length).toBeGreaterThan(0);
    expect(screen.getByText('+1.1 pts')).toBeInTheDocument();
    expect(screen.getByText('+0.10x')).toBeInTheDocument();
  });

  it('shows the pass state on Debt Overview credit metrics', () => {
    render(<DebtTab />);
    // All four covenants pass → "Within covenant" status pills.
    expect(screen.getAllByText('Within covenant').length).toBe(4);
  });
});

describe('DebtTab — Financing Impact on Returns', () => {
  it('renders levered IRR / MOIC as Returns outputs with the callout', () => {
    render(<DebtTab />);
    expect(screen.getByText('22.1%')).toBeInTheDocument();  // levered IRR
    expect(screen.getByText('2.34x')).toBeInTheDocument();  // MOIC
    expect(
      screen.getByText(/Levered IRR and MOIC are Returns outputs, not Debt assumptions/i),
    ).toBeInTheDocument();
  });
});

describe('DebtTab — Debt Schedule', () => {
  it('renders the Draws + Total Debt Service rows with an annual/monthly toggle', () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Debt Schedule'));
    expect(screen.getByText('Beginning Balance')).toBeInTheDocument();
    expect(screen.getByText('Draws')).toBeInTheDocument();
    expect(screen.getByText('Total Debt Service')).toBeInTheDocument();

    // Toggle to Monthly and the M1 column header appears.
    fireEvent.click(screen.getByText('Monthly'));
    expect(screen.getByText('M1')).toBeInTheDocument();
  });
});

describe('DebtTab — Refinance', () => {
  it('renders the Included banner and reads refi cash-out / balance from the debt output', () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Refinance'));
    // "Included in the model" shows in both the sub-tab caption and the banner.
    expect(screen.getAllByText('Included in the model').length).toBeGreaterThan(0);
    expect(screen.getByText('Cash-Out to Equity')).toBeInTheDocument();
    expect(screen.getByText('$4,500,000')).toBeInTheDocument(); // refi_cash_out
    expect(screen.getByText('$21,000,000')).toBeInTheDocument(); // balance_at_exit
  });
});

describe('DebtTab — canonical edit path (field_overrides + full run)', () => {
  it('editing the origination fee PATCHes the senior upfront-fee percent', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByTestId('edit-orig-fee'));
    const input = document.querySelector('input[type="number"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    fireEvent.change(input, { target: { value: '1.00' } });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides['debt_stack.tranches.0.upfront_fee_pct']).toBe(1);
  });

  it('editing the Debt-owned LTV resizes the senior tranche principal', async () => {
    render(<DebtTab />);
    // LTV lives in Capital Structure on Debt Overview (the editable one has a testid).
    fireEvent.click(screen.getByTestId('edit-ltv'));
    const input = document.querySelector('input[type="number"]') as HTMLInputElement;
    expect(input).toBeTruthy();
    fireEvent.change(input, { target: { value: '60' } });
    fireEvent.click(screen.getByText('Save'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    // 60% of the $36,000,000 property value → $21,600,000 senior principal.
    expect(body.field_overrides['debt_stack.tranches.0.principal_usd']).toBe(21_600_000);
  });
});
