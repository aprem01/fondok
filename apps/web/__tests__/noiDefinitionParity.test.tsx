/**
 * NOI definition parity — one fixture, every surface that prints an NOI.
 *
 * The bug this locks shut (FON-59 #1 / FON-67 #2): `fieldLabels.ts` mapped
 * BOTH `noi` and `noi_institutional` to the single string 'NOI', and the tabs
 * picked whichever field they liked. On Sam's deal Overview printed
 * $2,001,056 and Investment printed $1,448,443 — under the same label, off the
 * same run, with two different entry cap rates falling out of it.
 *
 * The vocabulary is now fixed (registry v2):
 *
 *   NOI (before FF&E reserve)   = expense.years[].noi_institutional  ($2,001,056)
 *   Cash NOI (after FF&E reserve) = expense.years[].noi              ($1,448,443)
 *
 * …differing by exactly the FF&E reserve ($552,613). This file renders
 * InvestmentTab, PLTab, ProjectionsSection, CashFlowTab and ICMemoTab from ONE
 * engine-outputs fixture and asserts:
 *
 *   (a) every "NOI (before FF&E reserve)" node carries $2,001,056,
 *   (b) every "Cash NOI" node carries $1,448,443,
 *   (c) NO rendered label is the bare, unqualified string "NOI".
 *
 * Each surface formats at its own scale (whole dollars on Investment and Cash
 * Flow, $M on the IC memo, $000s in the P&L statement), so (a)/(b) are
 * asserted in each surface's own format — the VALUE is what must match.
 *
 * OverviewTab's stabilized rows have their own parity guard now that they read
 * the worker's published stabilization block — see stabilizedParity.test.tsx,
 * which pins Overview === IC Memo === Scenario Analysis off one run.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import {
  NOI_BEFORE_RESERVE_LABEL,
  CASH_NOI_LABEL,
  STABILIZED_NOI_LABEL,
} from '@/lib/engines/noi';

// ── The one fixture ────────────────────────────────────────────────────
const NOI_BEFORE = 2_001_056; // expense.years[0].noi_institutional
const CASH_NOI = 1_448_443; // expense.years[0].noi
const FFE_RESERVE = 552_613; // …and the gap between them, to the dollar.

const TOTAL_REVENUE = 13_600_000;
const GOP = 5_400_000;
const MGMT_FEE = 408_000;
const FIXED_TOTAL = GOP - MGMT_FEE - NOI_BEFORE; // foots the institutional identity

const expYear = (year: number) => ({
  year,
  total_revenue: TOTAL_REVENUE,
  dept_expenses: { rooms: 3_000_000, food_beverage: 1_800_000, other_operated: 200_000, total: 5_000_000 },
  undistributed: {
    administrative_general: 900_000,
    information_telecom: 180_000,
    sales_marketing: 800_000,
    property_operations: 760_000,
    utilities: 560_000,
    total: 3_200_000,
  },
  mgmt_fee: MGMT_FEE,
  ffe_reserve: FFE_RESERVE,
  fixed_charges: { property_taxes: FIXED_TOTAL - 300_000, insurance: 300_000, rent: 0, other_fixed: 0, total: FIXED_TOTAL },
  gop: GOP,
  noi: CASH_NOI,
  noi_institutional: NOI_BEFORE,
});

const revYear = (year: number) => ({
  year,
  occupancy: 0.75,
  adr: 300,
  revpar: 225,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});

const fbYear = (year: number) => ({
  year,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  resort_fees: 0,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});

const YEARS = [2025, 2026, 2027, 2028, 2029];

// The cash-flow engine's unlevered statement, exactly as the worker now emits
// it (apps/worker/app/engines/cash_flow.py): the NOI row is the BEFORE-reserve
// basis, with the reserve on its own line below.
const CASH_FLOW_OUTPUT = {
  deal_id: 'deal-uuid-1',
  hold_years: 2,
  unlevered: [
    { label: 'Acquisition Uses at Close', values: [-34_000_000, null, null], kind: 'linked' },
    { label: 'NOI (before FF&E reserve)', values: [null, NOI_BEFORE, NOI_BEFORE], kind: 'linked' },
    { label: 'FF&E Reserve', values: [null, -FFE_RESERVE, -FFE_RESERVE], kind: 'linked' },
    { label: 'Gross Sale Proceeds', values: [null, null, 40_000_000], kind: 'linked' },
    {
      label: 'Unlevered Cash Flow',
      values: [-34_000_000, NOI_BEFORE - FFE_RESERVE, NOI_BEFORE - FFE_RESERVE + 40_000_000],
      kind: 'calc',
    },
  ],
  levered: [
    {
      label: 'Unlevered Cash Flow',
      values: [-34_000_000, NOI_BEFORE - FFE_RESERVE, NOI_BEFORE - FFE_RESERVE + 40_000_000],
      kind: 'linked',
    },
    {
      label: 'Net Cash Flow to Equity',
      values: [-34_000_000, NOI_BEFORE - FFE_RESERVE, NOI_BEFORE - FFE_RESERVE + 40_000_000],
      kind: 'calc',
    },
  ],
  distributions: [],
  unlevered_cash_flow: [-34_000_000, NOI_BEFORE - FFE_RESERVE, NOI_BEFORE - FFE_RESERVE + 40_000_000],
  levered_cash_flow: [-34_000_000, NOI_BEFORE - FFE_RESERVE, NOI_BEFORE - FFE_RESERVE + 40_000_000],
  provenance: {},
};

const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    capital: {
      deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
      outputs: {
        purchase_price: 34_000_000,
        price_per_key: 257_576,
        total_capital_usd: 43_000_000,
        total_capital: 43_000_000,
        total_capital_per_key: 325_758,
        equity_amount: 17_000_000,
        debt_amount: 26_000_000,
        uses: [
          { label: 'Purchase Price', amount: 34_000_000 },
          { label: 'Renovation Budget', amount: 4_620_000 },
        ],
        sources: [
          { label: 'Senior Loan', amount: 26_000_000 },
          { label: 'Equity', amount: 17_000_000 },
        ],
      },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    returns: {
      deal_id: 'deal-uuid-1', engine: 'returns', status: 'complete', summary: '',
      outputs: {
        levered_irr: 0.26, unlevered_irr: 0.13, equity_multiple: 2.5,
        hold_years: 5, gross_sale_price: 52_000_000, exit_cap_rate: 0.07,
        selling_costs: 520_000, terminal_noi: 2_600_000,
        // The after-reserve NOI series. Its last element is the TERMINAL
        // year, which is no longer what any surface calls "stabilized".
        noi_by_year: [1_100_000, 1_250_000, 1_380_000, 1_420_000, CASH_NOI],
      },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: {
        years: YEARS.map(expYear),
        noi_cagr: 0.05,
        // FON-41 / FON-59 #3 — the published stabilized year. Every year in
        // this fixture carries the same figures, so the stabilized NOI is the
        // before-reserve $2,001,056 — NOT the after-reserve $1,448,443 the
        // Scenario Summary used to print under a "stabilized" label.
        stabilization: {
          stabilized_year_index: 1,
          stabilized_year: 2,
          source: 'fondok_derived',
          signal: 'occupancy',
          derived_year: 2,
          stabilized_occupancy: 0.76,
          stabilized_adr: 385,
          stabilized_revenue: TOTAL_REVENUE,
          stabilized_noi_before_reserve: NOI_BEFORE,
          stabilized_cash_noi: CASH_NOI,
          stabilized_noi_margin: NOI_BEFORE / TOTAL_REVENUE,
        },
      },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    revenue: {
      deal_id: 'deal-uuid-1', engine: 'revenue', status: 'complete', summary: '',
      outputs: { years: YEARS.map(revYear), total_revenue_cagr: 0.03 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    fb: {
      deal_id: 'deal-uuid-1', engine: 'fb', status: 'complete', summary: '',
      outputs: { years: YEARS.map(fbYear) },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    debt: {
      deal_id: 'deal-uuid-1', engine: 'debt', status: 'complete', summary: '',
      outputs: { year_one_dscr: 1.59, year_one_debt_yield: 0.11, interest_rate: 0.0766, loan_amount: 26_000_000 },
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
    cash_flow: {
      deal_id: 'deal-uuid-1', engine: 'cash_flow', status: 'complete', summary: '',
      outputs: CASH_FLOW_OUTPUT as unknown as Record<string, unknown>,
      inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
    },
  },
} as unknown as EngineOutputsResponse;

// ── Mocks (the union of what the five tabs need) ───────────────────────
const fx = vi.hoisted(() => ({
  xlsxRows: [] as unknown[][],
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/projects/deal-uuid-1',
}));

vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: OUTPUTS, previous: null, loading: false, settled: true,
      lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', city: 'Miami Beach, FL', keys: 132, brand: 'Kimpton', field_overrides: {} },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }),
}));

vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/lib/hooks/useHistoricalBaseline', () => ({ useHistoricalBaseline: () => ({ baseline: null }) }));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [], extractions: {}, loading: false, settled: true,
    extractionFailures: {}, error: null, uploading: false, refresh: vi.fn(),
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
      engines: { ...actual.api.engines, timeline: vi.fn(async () => null) },
      scenarios: {
        ...actual.api.scenarios,
        list: vi.fn(async () => [{ id: 's1', name: 'Base Case', is_base: true }]),
        compare: vi.fn(async () => ({
          deal_id: 'deal-uuid-1',
          base_scenario_id: 's1',
          scenarios: [
            {
              scenario_id: 's1',
              scenario_name: 'Base Case',
              is_base: true,
              engines: OUTPUTS.engines as unknown as Record<string, unknown>,
            },
          ],
        })),
      },
    },
  };
});

// Trim heavy chrome the NOI labels do not live in.
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/project/HistoricalBaselinePanel', () => ({ default: () => null }));
vi.mock('@/components/project/CapexPlanPanel', () => ({ default: () => null, DEFAULT_CAPEX_PLAN: {} }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/project/pl/GroundedWorksheet', () => ({
  default: () => null,
  fieldMatchesKey: () => false,
}));
vi.mock('@/lib/exportXlsx', () => ({
  downloadXlsx: vi.fn(async (_name: string, sheets: { rows: unknown[][] }[]) => {
    fx.xlsxRows = sheets[0].rows;
  }),
}));

import InvestmentTab from '@/components/project/InvestmentTab';
import PLTab from '@/components/project/PLTab';
import ProjectionsSection from '@/components/project/pl/ProjectionsSection';
import CashFlowTab from '@/components/project/CashFlowTab';
import ICMemoTab from '@/components/project/ICMemoTab';
import type { Project } from '@/lib/mockData';

const PROJECT = { id: 0, name: 'Kimpton Angler' } as unknown as Project;

beforeEach(() => {
  cleanup();
  fx.xlsxRows = [];
});

/** (c) — no element on screen is labelled with the bare, unqualified word
 *  "NOI". A qualified label ("NOI (before FF&E reserve)", "Cash NOI",
 *  "NOI (Y1)", "Stabilized NOI") is fine; a naked "NOI" is the exact
 *  ambiguity that let $2.0M and $1.45M share a name. */
function expectNoBareNoiLabel(): void {
  const bare = screen.queryAllByText((_content, el) => (el?.textContent ?? '').trim() === 'NOI');
  expect(bare.map((el) => el.outerHTML)).toEqual([]);
}

describe('Investment — Entry / Run-Rate NOI is the before-reserve basis', () => {
  it('labels the basis and prints $2,001,056 (not the $1,448,443 Cash NOI)', () => {
    render(<InvestmentTab />);

    const label = screen.getByText(`Entry / Run-Rate ${NOI_BEFORE_RESERVE_LABEL}`);
    const row = label.closest('tr') ?? label.parentElement!.parentElement!;
    expect(within(row as HTMLElement).getByText('$2,001,056')).toBeInTheDocument();

    // The after-reserve figure must not be what Entry NOI shows.
    expect(screen.queryByText('$1,448,443')).not.toBeInTheDocument();
    expectNoBareNoiLabel();
  });

  it('derives the entry cap rate off the SAME basis (2,001,056 ÷ 34,000,000)', () => {
    render(<InvestmentTab />);
    const label = screen.getByText('Entry Cap Rate');
    const row = label.closest('tr') ?? label.parentElement!.parentElement!;
    // 2,001,056 / 34,000,000 = 5.89% (the old after-reserve read gave 4.26%).
    expect(within(row as HTMLElement).getByText('5.89%')).toBeInTheDocument();
  });
});

describe('Financials — the P&L / Projections surfaces name the basis', () => {
  it('PLTab renders the fixture with no bare "NOI" label anywhere', () => {
    render(<PLTab />);
    expectNoBareNoiLabel();
  });

  it('the Projections export labels the NOI row "NOI (before FF&E reserve)" at $2,001,056', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByRole('button', { name: /export/i }));

    await waitFor(() => expect(fx.xlsxRows.length).toBeGreaterThan(0));
    const labels = fx.xlsxRows.map((r) => String((r[0] as { v?: unknown } | string)?.valueOf?.() ?? r[0]));
    const noiIdx = fx.xlsxRows.findIndex((r) => {
      const first = r[0] as { v?: unknown } | string | undefined;
      const text = typeof first === 'object' && first !== null && 'v' in first ? String(first.v) : String(first);
      return text === NOI_BEFORE_RESERVE_LABEL;
    });
    expect(noiIdx, `no "${NOI_BEFORE_RESERVE_LABEL}" row in ${JSON.stringify(labels)}`).toBeGreaterThan(-1);
    const noiRow = fx.xlsxRows[noiIdx];
    const values = noiRow.slice(1).map((c) => (typeof c === 'object' && c !== null && 'v' in (c as object) ? (c as { v: unknown }).v : c));
    expect(values).toContain(NOI_BEFORE);
    expect(values).not.toContain(CASH_NOI);
    expectNoBareNoiLabel();
  });
});

describe('Cash Flow — the unlevered statement keeps the qualifier', () => {
  it('shows "NOI (before FF&E reserve)" at $2,001,056 and the reserve as FF&E Reserve / CapEx', () => {
    render(<CashFlowTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Unlevered' }));

    expect(screen.getByText(NOI_BEFORE_RESERVE_LABEL)).toBeInTheDocument();
    expect(screen.getAllByText('$2,001,056').length).toBeGreaterThan(0);
    // FON-67 #3 — the reserve row names both what it is and what it funds.
    expect(screen.getByText('FF&E Reserve / CapEx')).toBeInTheDocument();
    expect(screen.queryByText('CapEx')).not.toBeInTheDocument();
    expectNoBareNoiLabel();
  });
});

describe('IC Memo — the stabilized figures name their basis', () => {
  it('prints the deal-snapshot NOI before the reserve ($2.00M)', async () => {
    render(<ICMemoTab project={PROJECT} />);
    // Two surfaces carry it: the Deal-snapshot tile and the Operating summary
    // row. Both must show the SAME before-reserve figure.
    const labels = await screen.findAllByText('NOI (Y1)');
    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels) {
      const row = label.parentElement!;
      expect(within(row).getByText('$2.00M')).toBeInTheDocument();
      expect(within(row).queryByText('$1.45M')).not.toBeInTheDocument();
    }
    expectNoBareNoiLabel();
  });

  it('prints the Scenario Summary row from the published stabilization block', async () => {
    render(<ICMemoTab project={PROJECT} />);
    const label = await screen.findByText(STABILIZED_NOI_LABEL);
    // The Scenario Summary is a CSS grid, not a table — the row is the
    // label's parent.
    const row = label.parentElement!;
    // The stabilized YEAR's NOI before the reserve — not the last element of
    // returns.noi_by_year ($1.45M), which is a different year on a different
    // basis (FON-41 / FON-59 #3).
    expect(within(row).getByText('$2.00M')).toBeInTheDocument();
    expect(within(row).queryByText('$1.45M')).not.toBeInTheDocument();
    expectNoBareNoiLabel();
  });
});

describe('the vocabulary itself', () => {
  it('the two bases differ by exactly the FF&E reserve, and their labels differ', () => {
    expect(NOI_BEFORE - CASH_NOI).toBe(FFE_RESERVE);
    expect(NOI_BEFORE_RESERVE_LABEL).not.toBe(CASH_NOI_LABEL);
    expect(NOI_BEFORE_RESERVE_LABEL).toContain('FF&E');
    expect(CASH_NOI_LABEL).toContain('FF&E');
  });
});
