/**
 * Debt tab — source chips + Source legend (FON-63 / R-070).
 *
 * Sam's testers: "Where does the Debt tab's populated information originate?"
 * — they read the seeded numbers as extracted. Contracts locked here:
 *
 *  1. EVERY POPULATED INPUT ROW carries a chip naming its origin in words,
 *     built from the row's existing `source` / `overridden` / `state` data and
 *     the /assumption_sources locator: "Seed · institutional default",
 *     "OM · in-place debt p.N", "Deal record", "Your override".
 *  2. A SEED SAYS SO explicitly; an origin the model did not tag reads
 *     "source not recorded" — never a guess.
 *  3. A SOURCE LEGEND sits at the top of the tab explaining the dot states and
 *     the chip vocabulary.
 *  4. Linked / calculated rows carry no chip (their dot, note and "→" link
 *     already say where they come from).
 *
 * This file mounts a `useSource` mock (the sibling debtTab.test.tsx mounts no
 * provider on purpose), so the document / seed / deal-record paths are
 * exercised rather than only the "not recorded" fallback.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import type { EngineOutputsResponse } from '@/lib/api';
import type { ResolvedSource } from '@/lib/hooks/useDealProvenance';

const nav = vi.hoisted(() => ({
  params: new URLSearchParams(''),
  pathname: '/projects/deal-uuid-1',
  push: vi.fn(),
  replace: vi.fn(),
}));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => nav.pathname,
}));

// The per-key assumption sources the worker would report. Mutable so a test
// can describe a different deal; read at call time by the mocked hook.
const SOURCES: Record<string, ResolvedSource> = {};
vi.mock('@/lib/hooks/useDealProvenance', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useDealProvenance')>(
    '@/lib/hooks/useDealProvenance',
  );
  return {
    ...actual,
    useSource: (key: string | undefined) => (key ? SOURCES[key] ?? null : null),
  };
});

const SENIOR_TRANCHE = {
  kind: 'senior', label: 'Senior Loan', loan_amount: 23_000_000, all_in_rate: 0.0725, rate_type: 'fixed',
  annual_debt_service: 1_667_500, interest_only: false, terms_pending: false, amortization_years: 30,
  io_months: null, benchmark_name: null, benchmark_rate: null, benchmark_is_default: null,
  spread: null, rate_floor: null, rate_cap: null,
};

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
          interest_only_months: 12,
          origination_fee_pct: 0.75,
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
          ],
          monthly_schedule: [],
          debt_stack: {
            tranches: [SENIOR_TRANCHE],
            total_debt: 23_000_000,
            priced_debt: 23_000_000,
            total_annual_debt_service: 1_667_500,
            warnings: [],
          },
          refi_year: null,
          refi_cash_out: 0,
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

let currentOutputs: EngineOutputsResponse = makeOutputs();

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

// STABLE identity (see debtTab.test.tsx) — a fresh object per render would
// re-fire the field_overrides effect forever.
const mockDeal: { id: string; keys: number; field_overrides: Record<string, unknown> } = {
  id: 'deal-uuid-1', keys: 132, field_overrides: {},
};
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: mockDeal, status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, error: null }),
}));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: { ...actual.api, deals: { ...actual.api.deals, update: vi.fn(async () => ({ id: 'deal-uuid-1' })) } },
  };
});
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import DebtTab from '@/components/project/DebtTab';

/** The DebtRow whose label reads exactly `label` (dot · label · [link] · chip · value). */
function row(label: string): HTMLElement {
  return screen.getByText(label).parentElement!.parentElement as HTMLElement;
}
const chipIn = (el: HTMLElement): HTMLElement | null => el.querySelector('[data-source-chip]');

beforeEach(() => {
  cleanup();
  currentOutputs = makeOutputs();
  mockDeal.field_overrides = {};
  for (const k of Object.keys(SOURCES)) delete SOURCES[k];
});

describe('DebtTab — source chips (FON-63 / R-070)', () => {
  it('a seeded term says so explicitly: "Seed · institutional default"', () => {
    SOURCES.term_years = { source: 'seed', value: 5 };
    render(<DebtTab />);
    const chip = chipIn(row('Maturity'));
    expect(chip).toHaveTextContent('Seed · institutional default');
    expect(chip).toHaveAttribute('data-source-chip', 'seed');
    expect(chip?.getAttribute('title')).toMatch(/NOT extracted/);
  });

  it('a term read from the OM names the document and page: "OM · in-place debt p.N"', () => {
    SOURCES.interest_rate = {
      source: 'om_broker',
      value: 0.0725,
      docId: 'doc-om-1',
      field: { field: 'In-place debt — interest rate', page: 14, document_id: 'doc-om-1', doc_type: 'OM' },
    };
    render(<DebtTab />);
    const chip = chipIn(row('Fixed Interest Rate'));
    expect(chip).toHaveTextContent('OM · in-place debt p.14');
    expect(chip).toHaveAttribute('data-source-chip', 'document');
  });

  it('a document source with no page locator still names the document', () => {
    SOURCES.interest_rate = { source: 'om_broker', value: 0.0725 };
    render(<DebtTab />);
    expect(chipIn(row('Fixed Interest Rate'))).toHaveTextContent('OM · in-place debt');
    expect(chipIn(row('Fixed Interest Rate'))?.textContent).not.toMatch(/p\./);
  });

  it('a term from the deal record reads "Deal record"', () => {
    SOURCES.amortization_years = { source: 'deal_row', value: 30 };
    render(<DebtTab />);
    const chip = chipIn(row('Amortization'));
    expect(chip).toHaveTextContent('Deal record');
    expect(chip).toHaveAttribute('data-source-chip', 'deal');
  });

  it('an analyst override reads "Your override" regardless of the tagged source', () => {
    SOURCES.term_years = { source: 'seed', value: 5 };
    mockDeal.field_overrides = { term_years: { value: 7, note: 'Term sheet 9/12' } };
    render(<DebtTab />);
    const chip = chipIn(row('Maturity'));
    expect(chip).toHaveTextContent('Your override');
    expect(chip).toHaveAttribute('data-source-chip', 'override');
  });

  it('a populated input the model did not tag reads "source not recorded" — never a guess', () => {
    render(<DebtTab />);
    expect(chipIn(row('LTV'))).toHaveTextContent('source not recorded');
    expect(chipIn(row('LTV'))).toHaveAttribute('data-source-chip', 'unknown');
    expect(chipIn(row('Senior Loan Amount'))).toHaveTextContent('source not recorded');
    expect(chipIn(row('Maturity'))).toHaveTextContent('source not recorded');
  });

  it('the senior amount sized from the LTV carries the LTV origin, and says via LTV', () => {
    SOURCES.ltv = { source: 'seed', value: 0.65 };
    render(<DebtTab />);
    expect(chipIn(row('LTV'))).toHaveTextContent('Seed · institutional default');
    expect(chipIn(row('Senior Loan Amount'))).toHaveTextContent('Seed · institutional default · via LTV');
  });

  it('the origination fee chips as a seed until overridden (the worker tags it SOURCE_SEED)', () => {
    render(<DebtTab />);
    expect(chipIn(row('Origination Fee'))).toHaveTextContent('Seed · institutional default');
    cleanup();
    mockDeal.field_overrides = { 'debt_stack.tranches.0.upfront_fee_pct': { value: 1.0, note: 'Lender quote' } };
    render(<DebtTab />);
    expect(chipIn(row('Origination Fee'))).toHaveTextContent('Your override');
  });

  it('linked and calculated rows carry no chip — their dot, note and → link already say', () => {
    render(<DebtTab />);
    expect(chipIn(row('Purchase Price / Property Value'))).toBeNull();
    expect(chipIn(row('Total Debt'))).toBeNull();
    expect(chipIn(row('LTC'))).toBeNull();
  });

  it('an input still awaiting its value carries no chip (the "Enter …" affordance is the message)', () => {
    render(<DebtTab />);
    // PACE is not funded → "Enter amount", no origin to name.
    expect(chipIn(row('PACE Loan Amount'))).toBeNull();
  });
});

describe('DebtTab — Source legend (FON-63 / R-070)', () => {
  it('sits at the top of the tab and explains the dot states and the chip vocabulary', () => {
    render(<DebtTab />);
    const legend = screen.getByTestId('debt-source-legend');
    // Dot states.
    for (const label of ['Document sourced', 'Linked', 'Assumption', 'Calculated', 'Awaiting data', 'Needs review']) {
      expect(within(legend).getByText(label)).toBeInTheDocument();
    }
    expect(legend).toHaveTextContent(/a Fondok seed, the deal record or your override; the chip says which/);
    // Chip vocabulary, each with its meaning.
    expect(within(legend).getByText('Seed · institutional default')).toBeInTheDocument();
    expect(legend).toHaveTextContent(/not extracted from your documents/);
    expect(within(legend).getByText('OM · in-place debt p.N')).toBeInTheDocument();
    expect(within(legend).getByText('Deal record')).toBeInTheDocument();
    expect(within(legend).getByText('Your override')).toBeInTheDocument();
    expect(within(legend).getByText('source not recorded')).toBeInTheDocument();

    // Above the sub-tab bar.
    const tabs = screen.getByRole('tab', { name: 'Debt Overview' });
    // eslint-disable-next-line no-bitwise
    expect(legend.compareDocumentPosition(tabs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
