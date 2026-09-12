/**
 * Inline edit integrity — FON-63, FON-66 §1, FON-65.
 *
 * Sam, 2026-09-11:
 *   *"there is no Cancel or other way to exit without saving… To exit without
 *   changing/saving, I had to refresh the page."*
 *   *"clicking Save still causes Fondok to treat the value as an analyst
 *   Override, even though the value itself was unchanged… simply opening a field
 *   to inspect it should never change the model's data lineage."*
 *
 * Every inline editor now runs on one primitive (`useInlineEdit` +
 * `isNoOpEdit`), so the contract is pinned ONCE, table-driven across the tabs:
 *
 *  1. Save on an unchanged value makes NO `api.deals.update` call.
 *  2. Partnership's complement key is derived only for a real change — an
 *     unchanged GP ownership writes neither `gp_equity_pct` nor `lp_equity_pct`.
 *  3. Cancel restores the pre-edit display and calls nothing.
 *  4. Esc and click-outside both cancel.
 *  5. A genuinely changed value still PATCHes the EXACT worker key (the guard
 *     must not over-suppress).
 *  6. Display-unit spellings collapse: "6.80" over a stored 0.068, "65" over
 *     0.65.
 *
 * Reads exclusively from mocked engine envelopes — no fixtures, no prototype
 * numbers.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import type { EngineOutputsResponse, TimelineResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/projects/deal-uuid-1',
}));

// ── One envelope that serves Debt, Investment and Partnership ────────────
// The senior is priced at 6.80% on a 65.0% LTV so the two normalisation cases
// ("6.80" over 0.068, "65" over 0.65) are the tab's real displayed values.
const SENIOR_TRANCHE = {
  kind: 'senior', label: 'Senior Loan', loan_amount: 23_400_000, all_in_rate: 0.068, rate_type: 'fixed',
  annual_debt_service: 1_591_200, interest_only: false, terms_pending: false, amortization_years: 30,
  io_months: null, benchmark_name: null, benchmark_rate: null, benchmark_is_default: null,
  spread: null, rate_floor: null, rate_cap: null,
};

const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    debt: {
      deal_id: 'deal-uuid-1', engine: 'debt', status: 'complete', summary: '',
      outputs: {
        loan_amount: 23_400_000,
        year_one_dscr: 1.35,
        year_one_debt_yield: 0.111,
        avg_dscr: 1.42,
        interest_rate: 0.068,
        term_years: 5,
        amortization_years: 30,
        interest_only_months: 0,
        origination_fee_pct: 1.5,       // 0..10 percent convention → "1.50%"
        origination_fee_usd: 351_000,
        exit_fee_pct: 0.5,
        exit_fee_usd: 117_000,
        covenants: [
          { name: 'ltv', label: 'Loan-to-Value', kind: 'max', current: 0.65, threshold: 0.7, headroom: 0.05, passes: true },
          { name: 'ltc', label: 'Loan-to-Cost', kind: 'max', current: 0.54, threshold: 0.75, headroom: 0.21, passes: true },
          { name: 'dscr', label: 'DSCR (Year 1)', kind: 'min', current: 1.35, threshold: 1.25, headroom: 0.1, passes: true },
          { name: 'debt_yield', label: 'Debt Yield (Year 1)', kind: 'min', current: 0.111, threshold: 0.1, headroom: 0.011, passes: true },
        ],
        schedule: [{ year: 1, interest: 1_591_200, principal: 0, debt_service: 1_591_200, ending_balance: 23_400_000, dscr: 1.35 }],
        monthly_schedule: [{ month: 1, interest: 132_600, principal: 0, payment: 132_600, ending_balance: 23_400_000 }],
        debt_stack: {
          tranches: [SENIOR_TRANCHE],
          total_debt: 23_400_000,
          priced_debt: 23_400_000,
          total_annual_debt_service: 1_591_200,
          warnings: [],
        },
        balance_at_exit: 21_000_000,
      },
      inputs: {}, error: null, runtime_ms: 8, started_at: null, completed_at: null, run_id: 'run-1',
    },
    capital: {
      deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
      outputs: {
        purchase_price: 36_000_000,
        price_per_key: 272_727,
        entry_cap_rate: 0.075,
        total_capital_usd: 43_000_000,
        total_capital_per_key: 325_758,
        equity_amount: 19_600_000,
        debt_amount: 23_400_000,
        ltv: 0.65,
        ltc: 0.54,
        uses: [
          { label: 'Purchase Price', amount: 36_000_000, pct: 0.84 },
          { label: 'Total Uses', amount: 43_000_000, pct: 1, is_total: true },
        ],
        sources: [
          { label: 'Senior Loan', amount: 23_400_000, pct: 0.54 },
          { label: 'Equity', amount: 19_600_000, pct: 0.46 },
          { label: 'Total Sources', amount: 43_000_000, pct: 1, is_total: true },
        ],
      },
      inputs: {}, error: null, runtime_ms: 6, started_at: null, completed_at: null, run_id: 'run-1',
    },
    returns: {
      deal_id: 'deal-uuid-1', engine: 'returns', status: 'complete', summary: '',
      outputs: {
        levered_irr: 0.221, equity_multiple: 2.34,
        gross_sale_price: 52_000_000, exit_cap_rate: 0.07, terminal_noi: 3_640_000,
        selling_costs: 520_000, hold_years: 5,
      },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [{ year: 1, noi: 2_550_000 }] },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
    partnership: {
      deal_id: 'deal-uuid-1', engine: 'partnership', status: 'complete', summary: '',
      outputs: {
        // 10% / 90% exactly — the ownership row reads "10%".
        gp: { partner: 'GP', contributed_equity: 1_883_668, distributions: 4_583_668, irr: 0.284, equity_multiple: 2.43 },
        lp: { partner: 'LP', contributed_equity: 16_953_008, distributions: 26_753_008, irr: 0.176, equity_multiple: 1.58 },
        lp_pref_pct: 0.08,
        promote_amount: 3_000_000,
        promote_earned: 3_000_000,
        gp_cash_flows: [100_000, 3_583_668],
        lp_cash_flows: [500_000, 24_153_008],
        tier_allocations: [
          { label: 'Return of Capital', kind: 'return_of_capital', gp_amount: 1_883_668, lp_amount: 16_953_008, total_amount: 18_836_676 },
        ],
        total_distributable: 31_336_676,
        reconciles: true,
      },
      inputs: {}, error: null, runtime_ms: 12, started_at: null, completed_at: null, run_id: 'run-1',
    },
  },
} as unknown as EngineOutputsResponse;

const TIMELINE = {
  deal_id: 'deal-uuid-1',
  close_date: '2027-03-31',
  exit_date: '2032-03-31',
  stabilization_date: '2029-06-30',
  events: [{ event: 'Hotel Purchase', start: '2027-03-31', duration_months: 0, finish: '2027-03-31', basis: 'derived' }],
} as unknown as TimelineResponse;

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS, previous: null, loading: false, lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});

// STABLE identity — a fresh `{}` per render re-fires the tabs'
// `useEffect(…, [deal?.field_overrides])` forever (see debtTab.test.tsx).
const mockDeal: { id: string; keys: number; field_overrides: Record<string, unknown> } = {
  id: 'deal-uuid-1', keys: 132, field_overrides: {},
};
const refreshDealSpy = vi.fn();
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDeal, status: null, loading: false, error: null, fromMock: false, refresh: refreshDealSpy,
  }),
}));
vi.mock('@/lib/hooks/useHistoricalBaseline', () => ({ useHistoricalBaseline: () => ({ baseline: null }) }));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, running: false, error: null }),
}));

// THE spy under test — every assertion below is about whether this fires.
const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
const timelineSpy = vi.fn(async () => TIMELINE);
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      engines: { ...actual.api.engines, timeline: (...a: unknown[]) => timelineSpy(...(a as [])) },
      market: { ...actual.api.market, overview: async () => ({}) },
    },
  };
});

// Trim the heavy chrome — these tests only touch editors.
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/project/CapexPlanPanel', () => ({ default: () => null, DEFAULT_CAPEX_PLAN: {} }));
vi.mock('@/components/project/HistoricalBaselinePanel', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));

const toastSpy = vi.fn();
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: toastSpy }) }));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));

import DebtTab from '@/components/project/DebtTab';
import InvestmentTab from '@/components/project/InvestmentTab';
import PartnershipTab from '@/components/project/PartnershipTab';
import { NO_OP_EDIT_MESSAGE } from '@/components/design';

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
  toastSpy.mockClear();
  mockDeal.field_overrides = {};
});

// ── helpers ─────────────────────────────────────────────────────────────
const saveBtn = () => screen.getByRole('button', { name: 'Save' });
const cancelBtn = () => screen.getByRole('button', { name: 'Cancel' });
const openEditor = (testId: string): HTMLInputElement => {
  fireEvent.click(screen.getByTestId(testId));
  const input = document.querySelector('input[type="number"]') as HTMLInputElement;
  expect(input).toBeTruthy();
  return input;
};
/** Open a Debt/Investment editor, type `value`, press Save. */
async function editAndSave(testId: string, value: string): Promise<void> {
  const input = openEditor(testId);
  fireEvent.change(input, { target: { value } });
  fireEvent.click(saveBtn());
  await waitFor(() => expect(input).not.toBeInTheDocument());
}
/** The KeyRow / statement row carrying a label (label span → left span → row). */
const rowFor = (label: string): HTMLElement =>
  screen.getByText(label).parentElement!.parentElement! as HTMLElement;
/** Open the Partnership percent editor on a labelled row. */
function openPartnershipEditor(label: string): HTMLInputElement {
  fireEvent.click(within(rowFor(label)).getByText(/%$/));
  return screen.getByLabelText('percent') as HTMLInputElement;
}
/** Open an Investment assumption editor by the value on screen. */
function openInvestmentEditor(shown: string): HTMLInputElement {
  fireEvent.click(screen.getAllByText(shown)[0]);
  const input = document.querySelector('input[type="number"]') as HTMLInputElement;
  expect(input).toBeTruthy();
  return input;
}

/** The `field_overrides` body of the first PATCH. */
const patchedOverrides = (): Record<string, unknown> => {
  const [, body] = updateSpy.mock.calls[0] as unknown as [string, { field_overrides: Record<string, unknown> }];
  return body.field_overrides;
};

// ─────────────────────────────────────────────────────────────────────────
// 1. Save on an unchanged value writes nothing.
// ─────────────────────────────────────────────────────────────────────────
describe('Save on an unchanged value makes no api.deals.update call', () => {
  const debtCases: [string, string, string][] = [
    // [what the analyst is looking at, its test id, exactly what is on screen]
    ['senior loan amount', 'edit-senior-amount', '23400000'],
    ['LTV', 'edit-ltv', '65.0'],
    ['origination fee', 'edit-orig-fee', '1.50'],
    ['fixed interest rate', 'edit-rate', '6.80'],
    ['amortization', 'edit-amort', '30'],
    ['maturity', 'edit-term', '5'],
  ];

  it.each(debtCases)('Debt — re-saving the %s writes nothing', async (_label, testId, shown) => {
    render(<DebtTab />);
    if (testId === 'edit-rate' || testId === 'edit-amort' || testId === 'edit-term') {
      fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    }
    const input = openEditor(testId);
    // The editor opens pre-filled with the value on screen…
    expect(input.value).toBe(shown);
    fireEvent.click(saveBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    // …and saving it back changes nothing, so nothing is written.
    expect(updateSpy).not.toHaveBeenCalled();
    expect(toastSpy).toHaveBeenCalledWith(NO_OP_EDIT_MESSAGE, { type: 'info' });
  });

  const investmentCases: [string, string, string][] = [
    // [what it is, the value on screen, the draft it opens with]
    ['purchase price', '$36,000,000', '36000000'],
    ['exit cap rate', '7.00%', '7.00'],
    ['hold period', '5 years', '5'],
  ];

  it.each(investmentCases)('Investment — re-saving the %s writes nothing', async (_label, shown, draft) => {
    render(<InvestmentTab />);
    const input = openInvestmentEditor(shown);
    expect(input.value).toBe(draft);
    fireEvent.click(saveBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('Partnership — re-saving GP ownership writes nothing', async () => {
    render(<PartnershipTab />);
    const input = openPartnershipEditor('GP / Sponsor Ownership');
    // The engine's split is 9.99999…% — the editor opens on what the row shows
    // and the guard still reads it as unchanged (1e-6 fraction precision).
    expect(input.value).toBe('10.0');
    fireEvent.click(saveBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 2. The complement-key regression (Partnership wrote TWO phantom overrides).
// ─────────────────────────────────────────────────────────────────────────
describe('Partnership complement key', () => {
  it('an unchanged GP ownership writes neither gp nor lp', async () => {
    render(<PartnershipTab />);
    const input = openPartnershipEditor('GP / Sponsor Ownership');
    fireEvent.click(saveBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
    expect(JSON.stringify(updateSpy.mock.calls)).not.toContain('gp_equity_pct');
    expect(JSON.stringify(updateSpy.mock.calls)).not.toContain('lp_equity_pct');
  });

  it('a CHANGED GP ownership still writes both sides together', async () => {
    render(<PartnershipTab />);
    const input = openPartnershipEditor('GP / Sponsor Ownership');
    fireEvent.change(input, { target: { value: '12' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const body = patchedOverrides();
    expect(body['gp_equity_pct']).toBeCloseTo(0.12);
    expect(body['lp_equity_pct']).toBeCloseTo(0.88);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 3-4. Cancel / Esc / click-outside — exit without writing.
// ─────────────────────────────────────────────────────────────────────────
describe('leaving an editor without saving', () => {
  it('Cancel restores the pre-edit display and calls nothing', async () => {
    render(<DebtTab />);
    const input = openEditor('edit-senior-amount');
    fireEvent.change(input, { target: { value: '99000000' } });
    fireEvent.click(cancelBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
    // The row is back to the engine's number, not the abandoned draft.
    expect(screen.getByTestId('edit-senior-amount')).toHaveTextContent('$23,400,000');
    // Re-opening shows the engine value again — the draft did not survive.
    expect(openEditor('edit-senior-amount').value).toBe('23400000');
  });

  it('Esc cancels', async () => {
    render(<DebtTab />);
    const input = openEditor('edit-ltv');
    fireEvent.change(input, { target: { value: '70' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('edit-ltv')).toHaveTextContent('65.0%');
  });

  it('a click outside cancels', async () => {
    render(<DebtTab />);
    const input = openEditor('edit-ltv');
    fireEvent.change(input, { target: { value: '70' } });
    fireEvent.pointerDown(document.body);
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('edit-ltv')).toHaveTextContent('65.0%');
  });

  it('Partnership — Cancel and Esc both leave the overrides untouched', async () => {
    render(<PartnershipTab />);
    let input = openPartnershipEditor('GP / Sponsor Ownership');
    fireEvent.change(input, { target: { value: '25' } });
    fireEvent.click(cancelBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());

    input = openPartnershipEditor('GP / Sponsor Ownership');
    fireEvent.change(input, { target: { value: '25' } });
    fireEvent.keyDown(input, { key: 'Escape' });
    await waitFor(() => expect(input).not.toBeInTheDocument());

    expect(updateSpy).not.toHaveBeenCalled();
    // The row still reads the engine's ownership split.
    expect(within(rowFor('GP / Sponsor Ownership')).getByText('10%')).toBeInTheDocument();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 5. The guard must not over-suppress: a real change still PATCHes.
// ─────────────────────────────────────────────────────────────────────────
describe('a genuinely changed value still PATCHes the exact worker key', () => {
  it('Debt — the senior principal, in dollars', async () => {
    render(<DebtTab />);
    await editAndSave('edit-senior-amount', '24000000');
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(patchedOverrides()['debt_stack.tranches.0.principal_usd']).toBe(24_000_000);
  });

  it('Debt — LTV resizes the senior principal off the purchase price', async () => {
    render(<DebtTab />);
    await editAndSave('edit-ltv', '60');
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(patchedOverrides()['debt_stack.tranches.0.principal_usd']).toBe(21_600_000);
  });

  it('Debt — the fixed rate, as a fraction', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    await editAndSave('edit-rate', '7.25');
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(patchedOverrides()['debt_stack.tranches.0.rate_pct']).toBeCloseTo(0.0725);
  });

  it('Investment — the purchase price', async () => {
    render(<InvestmentTab />);
    const input = openInvestmentEditor('$36,000,000');
    fireEvent.change(input, { target: { value: '37000000' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    expect(patchedOverrides()['purchase_price']).toBe(37_000_000);
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 6. Display units vs persisted units.
// ─────────────────────────────────────────────────────────────────────────
describe('display-unit spellings collapse to the stored value', () => {
  it('"6.80" saved against a stored 0.068 is a no-op', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    const input = openEditor('edit-rate');
    fireEvent.change(input, { target: { value: '6.80' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('"6.8" (the same rate, fewer digits) is also a no-op', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Loan Terms & Covenants'));
    const input = openEditor('edit-rate');
    fireEvent.change(input, { target: { value: '6.8' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
  });

  it('"65" saved against a stored 0.65 LTV is a no-op', async () => {
    render(<DebtTab />);
    const input = openEditor('edit-ltv');
    fireEvent.change(input, { target: { value: '65' } });
    fireEvent.click(saveBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 7. The rate-basis toggle keeps its own contract (the BASIS is the change).
// ─────────────────────────────────────────────────────────────────────────
describe('the rate-basis toggle', () => {
  it('switching to Floating asks for the spread and writes rate_type + spread_pct together', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Floating'));
    const input = screen.getByTestId('rate-basis-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '3.00' } });
    fireEvent.click(screen.getByTestId('rate-basis-save'));
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const body = patchedOverrides();
    expect(body['debt_stack.tranches.0.rate_type']).toBe('floating');
    expect(body['debt_stack.tranches.0.spread_pct']).toBeCloseTo(0.03);
  });

  it('Cancel on the basis switch writes nothing and keeps Fixed', async () => {
    render(<DebtTab />);
    fireEvent.click(screen.getByText('Floating'));
    const input = screen.getByTestId('rate-basis-input') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '3.00' } });
    fireEvent.click(cancelBtn());
    await waitFor(() => expect(input).not.toBeInTheDocument());
    expect(updateSpy).not.toHaveBeenCalled();
  });
});

// ─────────────────────────────────────────────────────────────────────────
// 8. The shared Save · Cancel pair exists wherever an editor is open.
// ─────────────────────────────────────────────────────────────────────────
describe('every editor offers the canonical Save · Cancel pair', () => {
  it('Debt', () => {
    render(<DebtTab />);
    openEditor('edit-senior-amount');
    expect(saveBtn()).toBeInTheDocument();
    expect(cancelBtn()).toBeInTheDocument();
  });

  it('Investment', () => {
    render(<InvestmentTab />);
    openInvestmentEditor('$36,000,000');
    expect(saveBtn()).toBeInTheDocument();
    expect(cancelBtn()).toBeInTheDocument();
  });

  it('Partnership', () => {
    render(<PartnershipTab />);
    openPartnershipEditor('GP / Sponsor Ownership');
    expect(saveBtn()).toBeInTheDocument();
    expect(cancelBtn()).toBeInTheDocument();
  });
});
