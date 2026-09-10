/**
 * Debt tab — canonical rebuild (FON-72, design/canonical/Debt Tab.dc.html) +
 * the FON-63 Wave 2 assumptions workspace.
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
 *  3. CANONICAL EDIT PATH — every Loan Terms / Capital Structure / Covenant
 *     input PATCHes field_overrides via api.deals.update with the EXACT key the
 *     worker consumes and the worker's unit convention (fractions for rates,
 *     spreads and covenant ratios; months / years / dollars raw), then triggers
 *     the debounced full run.
 *
 *  4. MISSING INPUTS ARE INPUTS — a pending PACE rate, a floating spread, or
 *     a covenant with no threshold renders the "Enter …" affordance with its
 *     consequence, never a bare "—", and no pass/fail verdict is fabricated.
 *
 *  No provider is mounted — the tab's provenance dots fall back to the
 *  canonical kind when useTraceGraph / useSource find no context.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn() }),
}));

const SENIOR_TRANCHE = {
  kind: 'senior', label: 'Senior Loan', loan_amount: 23_000_000, all_in_rate: 0.0725, rate_type: 'fixed',
  annual_debt_service: 1_667_500, interest_only: false, terms_pending: false, amortization_years: 30,
  io_months: null, benchmark_name: null, benchmark_rate: null, benchmark_is_default: null,
  spread: null, rate_floor: null, rate_cap: null,
};

// The worker outputs under test — the whole tab reads from these.
function makeOutputs(debtPatch: Record<string, unknown> = {}): EngineOutputsResponse {
  return {
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
          interest_only_months: 0,
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
            tranches: [SENIOR_TRANCHE],
            total_debt: 23_000_000,
            priced_debt: 23_000_000,
            total_annual_debt_service: 1_667_500,
            warnings: [],
          },
          refi_year: 3,
          refi_cash_out: 4_500_000,
          balance_at_exit: 21_000_000,
          ...debtPatch,
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
}

// Swappable per test (STABLE identity between renders of one test).
let currentOutputs: EngineOutputsResponse = makeOutputs();

// Keep the REAL getEngineField; only swap the hook to serve our fixture.
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: currentOutputs,
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
const mockDeal: { id: string; keys: number; field_overrides: Record<string, unknown> } = {
  id: 'deal-uuid-1', keys: 132, field_overrides: {},
};
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
  currentOutputs = makeOutputs();
  mockDeal.field_overrides = {};
});

/** Open an inline editor by test id, type a value, press Save, and return the
 *  field_overrides body the tab PATCHed. */
async function editAndSave(testId: string, value: string): Promise<Record<string, unknown>> {
  fireEvent.click(screen.getByTestId(testId));
  const input = document.querySelector('input[type="number"]') as HTMLInputElement;
  expect(input).toBeTruthy();
  fireEvent.change(input, { target: { value } });
  fireEvent.click(screen.getByText('Save'));
  await waitFor(() => expect(updateSpy).toHaveBeenCalled());
  const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
  return body.field_overrides;
}

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

  it('shows the exit fee on Loan Terms & Covenants as display-only', () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    expect(screen.getByText('Exit Fee')).toBeInTheDocument();
    expect(screen.getByText('0.50% · $115,000')).toBeInTheDocument();
    expect(screen.getByText(/Display only — not modeled in the schedule/i)).toBeInTheDocument();
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

  it('a covenant with no entered threshold shows the Enter threshold input and no verdict', () => {
    currentOutputs = makeOutputs({
      covenants: [
        { name: 'ltv', label: 'Loan-to-Value', kind: 'max', current: 0.639, threshold: 0.65, headroom: 0.011, passes: true },
        { name: 'ltc', label: 'Loan-to-Cost', kind: 'max', current: 0.535, threshold: 0.75, headroom: 0.215, passes: true },
        { name: 'dscr', label: 'DSCR (Year 1)', kind: 'min', current: 1.35, threshold: null, headroom: null, passes: null },
        { name: 'debt_yield', label: 'Debt Yield (Year 1)', kind: 'min', current: 0.111, threshold: 0.1, headroom: 0.015, passes: true },
      ],
    });
    render(<DebtTab />);
    // Debt Overview: the DSCR cards carry no verdict, only the input to provide.
    expect(screen.getAllByText('No threshold set').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Enter threshold →').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Within covenant').length).toBe(3);

    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    expect(screen.getByTestId('edit-cov-dscr')).toHaveTextContent('Enter threshold');
    expect(screen.getByText(/No threshold entered — no pass \/ fail until you enter the lender’s floor/i)).toBeInTheDocument();
    // The live current reading is still shown (1.35x), never hidden.
    expect(screen.getAllByText('1.35x').length).toBeGreaterThan(0);
  });

  it('editing a covenant threshold writes the exact debt_stack.covenant_* key (ratio for DSCR, fraction for LTV)', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    const body = await editAndSave('edit-cov-dscr', '1.30');
    expect(body['debt_stack.covenant_min_dscr']).toBeCloseTo(1.3);

    cleanup();
    updateSpy.mockClear();
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    const body2 = await editAndSave('edit-cov-ltv', '60');
    expect(body2['debt_stack.covenant_max_ltv']).toBeCloseTo(0.6);
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

  it('a priced PACE tranche adds its debt service to the schedule total', () => {
    currentOutputs = makeOutputs({
      loan_amount: 28_000_000,
      debt_stack: {
        tranches: [
          SENIOR_TRANCHE,
          { ...SENIOR_TRANCHE, kind: 'pace', label: 'PACE Loan', loan_amount: 5_000_000, all_in_rate: 0.06,
            annual_debt_service: 300_000, interest_only: true, terms_pending: false, amortization_years: null },
        ],
        total_debt: 28_000_000, priced_debt: 28_000_000, total_annual_debt_service: 1_967_500, warnings: [],
      },
    });
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Debt Schedule'));
    expect(screen.getByText('PACE Debt Service')).toBeInTheDocument();
    // Senior 1,667,500 + PACE 300,000 per year.
    expect(screen.getAllByText('$1,967,500').length).toBeGreaterThan(0);
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
    const body = await editAndSave('edit-orig-fee', '1.00');
    expect(body['debt_stack.tranches.0.upfront_fee_pct']).toBe(1);
  });

  it('editing the Debt-owned LTV resizes the senior tranche principal', async () => {
    render(<DebtTab />);
    // LTV lives in Capital Structure on Debt Overview (the editable one has a testid).
    const body = await editAndSave('edit-ltv', '60');
    // 60% of the $36,000,000 property value → $21,600,000 senior principal.
    expect(body['debt_stack.tranches.0.principal_usd']).toBe(21_600_000);
  });

  it('editing the senior loan amount writes principal_usd in dollars', async () => {
    render(<DebtTab />);
    const body = await editAndSave('edit-senior-amount', '24000000');
    expect(body['debt_stack.tranches.0.principal_usd']).toBe(24_000_000);
  });

  it('editing the fixed rate writes rate_pct as a FRACTION (7.50 → 0.075)', async () => {
    render(<DebtTab />);
    const body = await editAndSave('edit-rate', '7.50');
    expect(body['debt_stack.tranches.0.rate_pct']).toBeCloseTo(0.075);
  });

  it('editing amortization writes amortization_months (years × 12; 0 = interest-only)', async () => {
    render(<DebtTab />);
    const body = await editAndSave('edit-amort', '25');
    expect(body['debt_stack.tranches.0.amortization_months']).toBe(300);
  });

  it('editing the interest-only period writes io_period_months', async () => {
    render(<DebtTab />);
    const body = await editAndSave('edit-io', '24');
    expect(body['debt_stack.tranches.0.io_period_months']).toBe(24);
  });

  it('editing maturity writes the top-level term_years the schedule runs on', async () => {
    render(<DebtTab />);
    const body = await editAndSave('edit-term', '7');
    expect(body['term_years']).toBe(7);
  });

  it('every edit schedules the debounced full run', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(<DebtTab />);
      await editAndSave('edit-rate', '7.00');
      await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
      expect(engineRunSpy).toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it('switching to Floating asks for the spread and writes rate_type + spread_pct together', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Floating'));
    const input = screen.getByTestId('rate-basis-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '3.00' } });
    fireEvent.click(screen.getByTestId('rate-basis-save'));
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides['debt_stack.tranches.0.rate_type']).toBe('floating');
    expect(body.field_overrides['debt_stack.tranches.0.spread_pct']).toBeCloseTo(0.03);
  });

  it('an overridden term carries the analyst-override provenance badge', () => {
    mockDeal.field_overrides = { 'debt_stack.tranches.0.rate_pct': 0.0725 };
    render(<DebtTab />);
    expect(screen.getAllByText('Override').length).toBeGreaterThan(0);
  });
});

describe('DebtTab — floating build-up (index + spread, floor / cap)', () => {
  const FLOATING = {
    ...SENIOR_TRANCHE, rate_type: 'floating', all_in_rate: 0.073,
    benchmark_name: 'SOFR', benchmark_rate: 0.043, benchmark_is_default: true, spread: 0.03,
  };

  it('renders the default-benchmark note, the spread editor and the calculated all-in', () => {
    currentOutputs = makeOutputs({ interest_rate: 0.073, debt_stack: { tranches: [FLOATING], total_debt: 23_000_000 } });
    render(<DebtTab />);
    expect(screen.getByTestId('edit-benchmark')).toHaveTextContent('SOFR · 4.30%');
    expect(screen.getAllByText(/Fondok’s flat SOFR assumption, not market data/i).length).toBeGreaterThan(0);
    expect(screen.getByTestId('edit-spread')).toHaveTextContent('3.00%');
    expect(screen.getByText('Underwritten All-In Rate')).toBeInTheDocument();
    expect(screen.getByText('7.30%')).toBeInTheDocument();
    // Optional floor / cap render as "None" inputs, not dashes.
    expect(screen.getByTestId('edit-floor')).toHaveTextContent('None');
    expect(screen.getByTestId('edit-cap')).toHaveTextContent('None');
  });

  it('editing the spread / index / floor writes fractions on the senior tranche keys', async () => {
    currentOutputs = makeOutputs({ interest_rate: 0.073, debt_stack: { tranches: [FLOATING], total_debt: 23_000_000 } });
    render(<DebtTab />);
    const body = await editAndSave('edit-spread', '3.50');
    expect(body['debt_stack.tranches.0.spread_pct']).toBeCloseTo(0.035);

    cleanup(); updateSpy.mockClear();
    render(<DebtTab />);
    const body2 = await editAndSave('edit-benchmark', '4.00');
    expect(body2['debt_stack.tranches.0.index_rate_pct']).toBeCloseTo(0.04);

    cleanup(); updateSpy.mockClear();
    render(<DebtTab />);
    const body3 = await editAndSave('edit-floor', '2.50');
    expect(body3['debt_stack.tranches.0.rate_floor_pct']).toBeCloseTo(0.025);
  });

  it('a floating senior with no spread shows Enter spread with the fallback consequence', () => {
    currentOutputs = makeOutputs({
      interest_rate: 0.0725,
      debt_stack: { tranches: [{ ...FLOATING, spread: null, all_in_rate: null, terms_pending: true }], total_debt: 23_000_000 },
    });
    render(<DebtTab />);
    expect(screen.getByTestId('edit-spread')).toHaveTextContent('Enter spread');
    expect(screen.getByText(/until entered the schedule runs at the fixed rate on file \(7\.25%\)/i)).toBeInTheDocument();
  });
});

describe('DebtTab — PACE tranche (index 1): missing inputs are inputs', () => {
  it('an unfunded PACE row offers Enter amount and funding it writes tranches.1.principal_usd', async () => {
    render(<DebtTab />);
    expect(screen.getByTestId('edit-pace-amount')).toHaveTextContent('Enter amount');
    expect(screen.getByText(/Not funded — enter an amount to add a PACE tranche/i)).toBeInTheDocument();
    const body = await editAndSave('edit-pace-amount', '5000000');
    expect(body['debt_stack.tranches.1.principal_usd']).toBe(5_000_000);
  });

  it('a funded PACE with no rate shows Enter rate with the pending consequence and no debt service', async () => {
    currentOutputs = makeOutputs({
      loan_amount: 28_000_000,
      debt_stack: {
        tranches: [
          SENIOR_TRANCHE,
          { ...SENIOR_TRANCHE, kind: 'pace', label: 'PACE Loan', loan_amount: 5_000_000, all_in_rate: null,
            annual_debt_service: null, interest_only: true, terms_pending: true, amortization_years: null },
        ],
        total_debt: 28_000_000, priced_debt: 23_000_000, total_annual_debt_service: 1_667_500,
        warnings: ['PACE Loan: terms not specified — excluded from debt service.'],
      },
    });
    render(<DebtTab />);
    // Debt Overview: Total Debt includes PACE; the row says why DSCR excludes it.
    expect(screen.getByText('$28,000,000')).toBeInTheDocument();
    expect(screen.getAllByText(/Terms pending — counts in Total Debt, LTV, LTC and debt yield; excluded from debt service and DSCR until a rate is entered/i).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    expect(screen.getByText('PACE Loan · Tranche 2')).toBeInTheDocument();
    expect(screen.getByTestId('edit-pace-rate')).toHaveTextContent('Enter rate');
    expect(screen.getByText('Excluded until a rate is entered')).toBeInTheDocument();

    const body = await editAndSave('edit-pace-rate', '6.00');
    expect(body['debt_stack.tranches.1.rate_pct']).toBeCloseTo(0.06);
  });
});
