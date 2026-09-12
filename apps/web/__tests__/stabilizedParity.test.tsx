/**
 * FON-41 / FON-54 #1 / FON-59 #3 — one stabilized year, three surfaces.
 *
 * Overview's Stabilization section, the IC Memo's Scenario Summary and
 * Scenario Analysis' `stab_noi` row each used to compute "stabilized" their
 * own way — the exit reversion, `years[0].noi`, and the last element of
 * `returns.noi_by_year` respectively. All three now read the worker's
 * published block (`expense.stabilization`), so from ONE run they print ONE
 * number, and every stabilized figure comes off the SAME year index.
 *
 * The other half of the contract: with no block, every row is an honest dash.
 * Never a $0, never a figure borrowed from another year.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { ScenarioRecord } from '@/lib/api';
import { STABILIZED_NOI_LABEL } from '@/lib/engines/noi';

// ── The stabilized year: projection Year 2 (index 1) ─────────────────
const STAB_INDEX = 1;
const STAB_OCC = 0.771;
const STAB_ADR = 401.25;
const STAB_REVENUE = 12_840_000;
const STAB_NOI = 4_355_000; // before the FF&E reserve
const STAB_CASH_NOI = 3_802_387;
const STAB_MARGIN = STAB_NOI / STAB_REVENUE;
// Figures from OTHER years that must never be printed as "stabilized".
const YEAR_ONE_NOI = 2_001_056;
const TERMINAL_NOI = 3_640_000; // returns.terminal_noi — the year hold+1 reversion
const TOTAL_CAPITAL = 43_000_000;

const STABILIZATION_BLOCK = {
  stabilized_year_index: STAB_INDEX,
  stabilized_year: STAB_INDEX + 1,
  source: 'fondok_derived',
  signal: 'occupancy',
  derived_year: STAB_INDEX + 1,
  stabilized_occupancy: STAB_OCC,
  stabilized_adr: STAB_ADR,
  stabilized_revenue: STAB_REVENUE,
  stabilized_noi_before_reserve: STAB_NOI,
  stabilized_cash_noi: STAB_CASH_NOI,
  stabilized_noi_margin: STAB_MARGIN,
};

function expenseOutputs(withBlock: boolean) {
  return {
    years: [
      { year: 1, total_revenue: 11_900_000, noi: 1_448_443, noi_institutional: YEAR_ONE_NOI, ffe_reserve: 552_613 },
      { year: 2, total_revenue: STAB_REVENUE, noi: STAB_CASH_NOI, noi_institutional: STAB_NOI, ffe_reserve: 552_613 },
      { year: 3, total_revenue: 13_100_000, noi: 3_900_000, noi_institutional: 4_452_613, ffe_reserve: 552_613 },
    ],
    noi_cagr: 0.05,
    ...(withBlock ? { stabilization: STABILIZATION_BLOCK } : {}),
  };
}

function engines(withBlock: boolean) {
  return {
    capital: {
      status: 'complete',
      outputs: {
        purchase_price: 34_000_000, price_per_key: 257_576,
        total_capital_usd: TOTAL_CAPITAL, total_capital: TOTAL_CAPITAL,
        equity_amount: 17_000_000, debt_amount: 26_000_000,
        uses: [
          { label: 'Purchase Price', amount: 34_000_000 },
          { label: 'Renovation Budget', amount: 4_620_000 },
        ],
        sources: [{ label: 'Senior Loan', amount: 26_000_000 }, { label: 'Equity', amount: 17_000_000 }],
      },
    },
    returns: {
      status: 'complete',
      outputs: {
        levered_irr: 0.198, equity_multiple: 2.1, avg_coc: 0.08, hold_years: 5,
        gross_sale_price: 52_000_000, exit_cap_rate: 0.07, selling_costs: 520_000,
        terminal_noi: TERMINAL_NOI,
        noi_by_year: [1_448_443, STAB_CASH_NOI, 3_900_000, 4_050_000, 4_200_000],
      },
    },
    expense: { status: 'complete', outputs: expenseOutputs(withBlock) },
    revenue: {
      status: 'complete',
      outputs: {
        years: [
          { year: 1, occupancy: 0.70, adr: 385, revpar: 269.5, rooms_revenue: 9_000_000, fb_revenue: 2_400_000, other_revenue: 500_000, total_revenue: 11_900_000 },
          { year: 2, occupancy: STAB_OCC, adr: STAB_ADR, revpar: 309.4, rooms_revenue: 9_900_000, fb_revenue: 2_450_000, other_revenue: 490_000, total_revenue: STAB_REVENUE },
        ],
        projection_calendar_years: [2025, 2026],
        projection_start_year: 2025,
      },
    },
    debt: { status: 'complete', outputs: { avg_dscr: 1.6, year_one_dscr: 1.59, interest_rate: 0.068, loan_amount: 26_000_000 } },
  };
}

let WITH_BLOCK = true;

// Two records — ScenarioComparePanel only builds the KPI table when there is
// something to compare the Base against.
const RECORDS = [
  { id: 's-base', deal_id: 'deal-uuid-1', name: 'Base Case', is_base: true, in_memo: true, overrides: [] },
  { id: 's-up', deal_id: 'deal-uuid-1', name: 'Upside', is_base: false, in_memo: true, overrides: [] },
] as unknown as ScenarioRecord[];

const nav = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
  useRouter: () => ({ push: nav.push, replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => '/projects/deal-uuid-1',
}));

vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: { deal_id: 'deal-uuid-1', engines: engines(WITH_BLOCK) },
      previous: null, loading: false, settled: true, lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: {
      id: 'deal-uuid-1', name: 'Kimpton Angler', keys: 132, city: 'Miami Beach, FL',
      deal_type: 'acquisition', return_profile: 'value-add', field_overrides: {},
    },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
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
      market: { ...actual.api.market, overview: vi.fn(async () => ({})) },
      engines: { ...actual.api.engines, timeline: vi.fn(async () => ({ events: [], close_date: null, exit_date: null })) },
      scenarios: {
        ...actual.api.scenarios,
        list: vi.fn(async () => RECORDS),
        compare: vi.fn(async () => ({
          deal_id: 'deal-uuid-1',
          base_scenario_id: 's-base',
          scenarios: [
            { scenario_id: 's-base', scenario_name: 'Base Case', is_base: true, engines: engines(WITH_BLOCK) },
            { scenario_id: 's-up', scenario_name: 'Upside', is_base: false, engines: engines(WITH_BLOCK) },
          ],
        })),
      },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import OverviewTab from '@/components/project/OverviewTab';
import ICMemoTab from '@/components/project/ICMemoTab';
import ScenarioComparePanel from '@/components/project/ScenarioComparePanel';
import type { Project } from '@/lib/mockData';

const PROJECT = { id: 0, name: 'Kimpton Angler' } as unknown as Project;

/** The Overview row element for a label (label span → left span → row div).
 *  Scoped past the KPI tiles, which repeat some labels in <div>s, and past the
 *  popover, whose header repeats the label too. */
function rowFor(label: string): HTMLElement {
  const span = screen
    .getAllByText(label)
    .find(
      (el) =>
        !el.closest('[role="dialog"]') &&
        el.tagName === 'SPAN' &&
        el.parentElement?.tagName === 'SPAN',
    );
  if (!span) throw new Error(`no Overview row labelled ${label}`);
  return span.parentElement!.parentElement!;
}
function rowValue(label: string): string {
  const right = rowFor(label).lastElementChild as HTMLElement;
  return (right.lastElementChild as HTMLElement).textContent ?? '';
}
/** The Base-column value beside a scenario-grid / table row label. */
function baseCellText(labelEl: HTMLElement): string {
  const row = labelEl.closest('tr') ?? labelEl.parentElement!;
  const cells = Array.from(row.children).filter((c) => c !== labelEl);
  return (cells[0]?.textContent ?? '').trim();
}

beforeEach(() => {
  cleanup();
  WITH_BLOCK = true;
  nav.push.mockClear();
});

// ── parity ───────────────────────────────────────────────────────────
describe('one stabilized NOI across Overview, IC Memo and Scenario Analysis', () => {
  it('all three print the published block, from one run', async () => {
    // Overview — the Stabilization section row.
    const overview = render(<OverviewTab projectId="deal-uuid-1" />);
    const sectionRow = rowFor(STABILIZED_NOI_LABEL);
    expect(sectionRow.textContent).toContain('$4,355,000');
    // …and NOT the year-1 NOI or the exit-year reversion.
    expect(sectionRow!.textContent).not.toContain('$2,001,056');
    expect(sectionRow!.textContent).not.toContain('$3,640,000');
    overview.unmount();
    cleanup();

    // IC Memo — the Scenario Summary row.
    const memo = render(<ICMemoTab project={PROJECT} />);
    const memoValue = baseCellText(await screen.findByText(STABILIZED_NOI_LABEL));
    expect(memoValue).toContain('$4.36M'); // 4,355,000 → $4.36M
    memo.unmount();
    cleanup();

    // Scenario Analysis — the `stab_noi` row.
    render(<ScenarioComparePanel dealId="deal-uuid-1" scenarios={RECORDS} />);
    const panelValue = baseCellText(await screen.findByText(STABILIZED_NOI_LABEL));
    expect(panelValue).toContain('$4.36M');
    expect(panelValue).toBe(memoValue);
  });

  it('every Overview stabilized figure comes off the SAME year index', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);
    // Year 2 of the projection, named with its calendar year.
    expect(rowValue('Stabilization Year')).toContain('Year 2');
    expect(rowValue('Stabilization Year')).toContain('2026');
    expect(rowValue('Stabilized Occupancy')).toBe('77.1%');
    expect(rowValue('Stabilized ADR')).toBe('$401');
    expect(rowValue('Stabilized Revenue')).toBe('$12,840,000');
    expect(rowValue(STABILIZED_NOI_LABEL)).toBe('$4,355,000');
  });

  it('the margin is stabilized NOI ÷ stabilized revenue, same index', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);
    const shown = rowValue('Stabilized NOI Margin');
    const expected = `${(STAB_MARGIN * 100).toFixed(1)}%`;
    expect(shown).toBe(expected);
    // Cross-check against the two rows actually on screen — not against a
    // ratio of one year's NOI to another year's revenue.
    const noi = Number(rowValue(STABILIZED_NOI_LABEL).replace(/[^\d.]/g, ''));
    const rev = Number(rowValue('Stabilized Revenue').replace(/[^\d.]/g, ''));
    expect(`${((noi / rev) * 100).toFixed(1)}%`).toBe(shown);
  });

  it('the KPI tile reads the block, not the reversion', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);
    expect(screen.getByText('$4.36M')).toBeInTheDocument();
    expect(screen.getByText(/projection Year 2/)).toBeInTheDocument();
    expect(screen.queryByText('stabilization year not set')).toBeNull();
  });

  it('Renovation Impact is gone from the Stabilization section (Sam, MVP)', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);
    expect(screen.queryByText('Renovation Impact')).toBeNull();
  });
});

// ── the honest-dash half ─────────────────────────────────────────────
describe('with no stabilization block every row is a dash, and no row is $0', () => {
  beforeEach(() => { WITH_BLOCK = false; });

  it('Overview renders dashes, never zeros or a borrowed year', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);

    for (const label of [
      'Stabilization Year',
      'Stabilized Occupancy',
      'Stabilized ADR',
      'Stabilized Revenue',
      STABILIZED_NOI_LABEL,
      'Stabilized NOI Margin',
    ]) {
      const value = rowValue(label);
      expect(value, `${label} must be a dash`).toBe('—');
      expect(value).not.toBe('$0');
      expect(value).not.toBe('0.0%');
    }
    // The exit-year reversion is still shown where it belongs (the Exit
    // section) and ONLY there — it is never borrowed as "stabilized".
    expect(screen.getAllByText('$3,640,000')).toHaveLength(1);
    // The KPI tile says why it is empty.
    expect(screen.getByText('stabilization year not set')).toBeInTheDocument();
  });

  it('the Stabilization row explains its dash with a reason code', () => {
    render(<OverviewTab projectId="deal-uuid-1" />);
    const stabRow = rowFor(STABILIZED_NOI_LABEL);
    expect(stabRow.querySelector('[data-refused="awaiting_analyst"]')).not.toBeNull();
  });

  it('IC Memo and Scenario Analysis render a dash rather than another year', async () => {
    const memo = render(<ICMemoTab project={PROJECT} />);
    const memoValue = baseCellText(await screen.findByText(STABILIZED_NOI_LABEL));
    expect(memoValue).not.toContain('$1.45M');
    expect(memoValue).not.toContain('$3.64M');
    expect(memoValue).toContain('—');
    memo.unmount();
    cleanup();

    render(<ScenarioComparePanel dealId="deal-uuid-1" scenarios={RECORDS} />);
    const panelValue = baseCellText(await screen.findByText(STABILIZED_NOI_LABEL));
    expect(panelValue).not.toContain('$3.64M');
    expect(within(screen.getByText(STABILIZED_NOI_LABEL).closest('tr') ?? document.body)
      .queryByText('$0.00M')).toBeNull();
  });
});
