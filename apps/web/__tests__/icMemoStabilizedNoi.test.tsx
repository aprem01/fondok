/**
 * FON-54 #1 — IC Memo and Scenario Analysis must print the SAME stabilized NOI.
 *
 * They didn't. IC Memo's Scenario Summary read `expense.years[0].noi` (year
 * ONE, after the FF&E reserve → $1.45M) while Scenario Analysis read the last
 * element of `returns.noi_by_year` (the terminal year → $2.60M), and both
 * rows were labelled "Stabilized NOI". Same deal, same compare endpoint, two
 * numbers.
 *
 * Both now call `stabilizedCashNoi()` from `@/lib/engines/noi` and print
 * "Stabilized Cash NOI" (the series is net of the FF&E reserve). This test
 * feeds ONE `scenarios.compare` payload to both components and asserts the
 * Base column agrees.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { ScenarioRecord } from '@/lib/api';
import { STABILIZED_CASH_NOI_LABEL } from '@/lib/engines/noi';

// The terminal-year (stabilized) Cash NOI both panels must land on.
const STABILIZED = 2_604_118;
// Year-1 Cash NOI — what IC Memo used to show under the same label.
const YEAR_ONE_CASH_NOI = 1_448_443;

const BASE_ENGINES = {
  returns: {
    status: 'complete',
    outputs: {
      levered_irr: 0.26,
      equity_multiple: 2.5,
      avg_coc: 0.09,
      gross_sale_price: 52_000_000,
      noi_by_year: [1_448_443, 1_900_000, 2_200_000, 2_400_000, STABILIZED],
      cash_flows: [-17_000_000, 1_000_000, 1_200_000],
    },
  },
  expense: {
    status: 'complete',
    outputs: {
      years: [
        { year: 1, noi: YEAR_ONE_CASH_NOI, noi_institutional: 2_001_056, ffe_reserve: 552_613 },
        { year: 2, noi: 1_900_000, noi_institutional: 2_460_000, ffe_reserve: 560_000 },
      ],
    },
  },
  debt: { status: 'complete', outputs: { avg_dscr: 1.6, year_one_dscr: 1.59 } },
};

const UPSIDE_ENGINES = {
  ...BASE_ENGINES,
  returns: {
    status: 'complete',
    outputs: {
      ...BASE_ENGINES.returns.outputs,
      noi_by_year: [1_500_000, 2_000_000, 2_300_000, 2_500_000, 2_800_000],
    },
  },
};

// ONE payload, served to both components.
const COMPARE = {
  deal_id: 'deal-uuid-1',
  base_scenario_id: 's-base',
  scenarios: [
    { scenario_id: 's-base', scenario_name: 'Base Case', is_base: true, engines: BASE_ENGINES },
    { scenario_id: 's-up', scenario_name: 'Upside', is_base: false, engines: UPSIDE_ENGINES },
  ],
};

const RECORDS = [
  { id: 's-base', deal_id: 'deal-uuid-1', name: 'Base Case', is_base: true, in_memo: true, overrides: [] },
  { id: 's-up', deal_id: 'deal-uuid-1', name: 'Upside', is_base: false, in_memo: true, overrides: [] },
] as unknown as ScenarioRecord[];

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: { deal_id: 'deal-uuid-1', engines: BASE_ENGINES },
      previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', keys: 132, field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [], critical: 0, warn: 0, info: 0, note: null, loading: false, error: null }),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: vi.fn(async () => ({ id: 'deal-uuid-1' })) },
      scenarios: {
        ...actual.api.scenarios,
        list: vi.fn(async () => RECORDS),
        compare: vi.fn(async () => COMPARE),
      },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ICMemoTab from '@/components/project/ICMemoTab';
import ScenarioComparePanel from '@/components/project/ScenarioComparePanel';
import type { Project } from '@/lib/mockData';

const PROJECT = { id: 0, name: 'Kimpton Angler' } as unknown as Project;

/** The Base-column value printed beside the stabilized-NOI row label. */
function baseCellText(labelEl: HTMLElement): string {
  const row = labelEl.closest('tr') ?? labelEl.parentElement!;
  // The label is the first cell; the Base scenario is the first value cell.
  const cells = Array.from(row.children).filter((c) => c !== labelEl);
  return (cells[0]?.textContent ?? '').trim();
}

beforeEach(() => cleanup());

describe('FON-54 #1 — one stabilized-NOI definition across IC Memo and Scenario Analysis', () => {
  it('both read the same selector and print the same Base value from one compare payload', async () => {
    const { unmount } = render(<ICMemoTab project={PROJECT} />);
    const memoLabel = await screen.findByText(STABILIZED_CASH_NOI_LABEL);
    const memoValue = baseCellText(memoLabel);
    // $2.60M — the TERMINAL year, not year one.
    expect(memoValue).toContain('$2.60M');
    expect(memoValue).not.toContain('$1.45M');
    unmount();
    cleanup();

    render(<ScenarioComparePanel dealId="deal-uuid-1" scenarios={RECORDS} />);
    const panelLabel = await screen.findByText(STABILIZED_CASH_NOI_LABEL);
    const panelValue = baseCellText(panelLabel);

    expect(panelValue).toContain('$2.60M');
    // The whole point: identical, off one payload.
    expect(memoValue).toBe(panelValue);
  });

  it('the shared selector returns the terminal-year Cash NOI, not year one', async () => {
    const { stabilizedCashNoi } = await import('@/lib/engines/noi');
    expect(stabilizedCashNoi(BASE_ENGINES)).toBe(STABILIZED);
    expect(stabilizedCashNoi(BASE_ENGINES)).not.toBe(YEAR_ONE_CASH_NOI);
    // Falls back to the last expense year when returns published no series.
    expect(stabilizedCashNoi({ expense: BASE_ENGINES.expense })).toBe(1_900_000);
    expect(stabilizedCashNoi({})).toBeNull();
  });
});
