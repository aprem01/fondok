/**
 * FON-41 #2 — the Projections statement's periods.
 *
 * Four things are pinned here, all of them things Sam reported:
 *
 *  1. THE CALENDAR. `revenue.years[].year` is an ORDINAL (1..hold_years). The
 *     statement used to print it under its own column header, producing
 *     *"Base Year 1, Year 1 2, Year 2 3"*. The calendar now comes from
 *     `revenue.projection_calendar_years`, anchored on the acquisition close
 *     date — and with NO close date the column prints its label alone, never a
 *     guessed year.
 *  2. THE OFF-BY-ONE. `revenue.years[0]` IS model Year 1: the Cash Flow tab
 *     labels the same NOI "Year 1" while Financials called it "Base Year" and
 *     then labelled index 1 "Year 1". The first column is now
 *     "Base Year (Year 1)" and the rest shift up — so the two tabs agree.
 *  3. THE HORIZON. hold_years + 1 columns, the last being the Exit Year:
 *     display-only, carrying the forward 12-month Cash NOI the reversion is
 *     valued on and dashes everywhere else.
 *  4. HOTEL DELIVERY. It used to render a literal "9/30/1" — `'9/30/' + the
 *     ordinal`. It now reads the deal's acquisition close date, or an em dash.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

// ── One fixture, five modelled years, a DIFFERENT NOI in each ────────
const HOLD_YEARS = 5;
const CALENDAR = [2025, 2026, 2027, 2028, 2029];
// Year 1's NOI — the number Cash Flow labels "Year 1" and Financials must
// print under "Base Year (Year 1)". Distinct per year so an off-by-one shows.
const NOI_BEFORE = [2_001_056, 2_240_000, 2_390_000, 2_510_000, 2_640_000];
const CASH_NOI = NOI_BEFORE.map((n) => n - 552_613);
const TERMINAL_NOI = 2_759_000; // returns.terminal_noi — the forward 12-month NOI
const TOTAL_REVENUE = 13_600_000;

const revYear = (i: number) => ({
  // The ENGINE emits an ordinal here. The fixture says so on purpose.
  year: i + 1,
  occupancy: 0.75,
  adr: 300,
  revpar: 225,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});
const fbYear = (i: number) => ({
  year: i + 1,
  rooms_revenue: 10_000_000,
  fb_revenue: 3_000_000,
  resort_fees: 0,
  other_revenue: 600_000,
  total_revenue: TOTAL_REVENUE,
});
const expYear = (i: number) => ({
  year: i + 1,
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
  mgmt_fee: 408_000,
  ffe_reserve: 552_613,
  fixed_charges: { property_taxes: 700_000, insurance: 300_000, rent: 0, other_fixed: 0, total: 1_000_000 },
  gop: 5_400_000,
  noi: CASH_NOI[i],
  noi_institutional: NOI_BEFORE[i],
});

// The cash-flow engine's unlevered statement: Close + 5 hold years.
const CASH_FLOW_OUTPUT = {
  deal_id: 'deal-uuid-1',
  hold_years: HOLD_YEARS,
  unlevered: [
    { label: 'Acquisition Uses at Close', values: [-34_000_000, null, null, null, null, null], kind: 'linked' },
    { label: 'NOI (before FF&E reserve)', values: [null, ...NOI_BEFORE], kind: 'linked' },
    { label: 'FF&E Reserve', values: [null, ...NOI_BEFORE.map(() => -552_613)], kind: 'linked' },
    {
      label: 'Unlevered Cash Flow',
      values: [-34_000_000, ...CASH_NOI],
      kind: 'calc',
    },
  ],
  levered: [],
  distributions: [],
  unlevered_cash_flow: [-34_000_000, ...CASH_NOI],
  levered_cash_flow: [-34_000_000, ...CASH_NOI],
  provenance: {},
};

/** Built per test so the calendar (and the close date behind it) can vary. */
function buildOutputs(calendar: number[] | undefined): EngineOutputsResponse {
  return {
    deal_id: 'deal-uuid-1',
    engines: {
      revenue: {
        deal_id: 'deal-uuid-1', engine: 'revenue', status: 'complete', summary: '',
        outputs: {
          years: NOI_BEFORE.map((_, i) => revYear(i)),
          total_revenue_cagr: 0.03,
          projection_start_year: calendar ? calendar[0] : null,
          projection_calendar_years: calendar ?? [],
        },
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
      },
      fb: {
        deal_id: 'deal-uuid-1', engine: 'fb', status: 'complete', summary: '',
        outputs: { years: NOI_BEFORE.map((_, i) => fbYear(i)) },
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
      },
      expense: {
        deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
        outputs: { years: NOI_BEFORE.map((_, i) => expYear(i)), noi_cagr: 0.05 },
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
      },
      returns: {
        deal_id: 'deal-uuid-1', engine: 'returns', status: 'complete', summary: '',
        outputs: {
          hold_years: HOLD_YEARS, terminal_noi: TERMINAL_NOI, exit_cap_rate: 0.07,
          revpar_growth: 0.045, gross_sale_price: 39_414_285, levered_irr: 0.198,
        },
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
      },
      cash_flow: {
        deal_id: 'deal-uuid-1', engine: 'cash_flow', status: 'complete', summary: '',
        outputs: CASH_FLOW_OUTPUT as unknown as Record<string, unknown>,
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
      },
      debt: {
        deal_id: 'deal-uuid-1', engine: 'debt', status: 'complete', summary: '',
        outputs: { year_one_dscr: 1.59, interest_rate: 0.068, loan_amount: 26_000_000 },
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
      },
      capital: {
        deal_id: 'deal-uuid-1', engine: 'capital', status: 'complete', summary: '',
        outputs: { purchase_price: 34_000_000, equity_amount: 17_000_000, uses: [], sources: [] },
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'r1',
      },
    },
  } as unknown as EngineOutputsResponse;
}

// ── Mocks ────────────────────────────────────────────────────────────
const fx = vi.hoisted(() => ({ xlsxRows: [] as unknown[][] }));

let CALENDAR_YEARS: number[] | undefined = CALENDAR;
let FIELD_OVERRIDES: Record<string, unknown> = {};

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
      outputs: buildOutputs(CALENDAR_YEARS),
      previous: null, loading: false, settled: true, lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', name: 'Kimpton Angler', keys: 132, field_overrides: FIELD_OVERRIDES },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [], critical: 0, warn: 0, info: 0, note: null, loading: false, error: null }),
}));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [], extractions: {}, loading: false, settled: true,
    extractionFailures: {}, error: null, uploading: false, refresh: vi.fn(),
  }),
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/project/EngineHeader', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRightRail', () => ({ default: () => null }));
vi.mock('@/components/project/EngineRunHistory', () => ({ default: () => null }));
vi.mock('@/components/project/WhatJustHappened', () => ({ default: () => null }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/lib/exportXlsx', () => ({
  downloadXlsx: vi.fn(async (_name: string, sheets: { rows: unknown[][] }[]) => {
    fx.xlsxRows = sheets[0].rows;
  }),
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: vi.fn(async () => ({ id: 'deal-uuid-1' })) },
    },
  };
});

import ProjectionsSection, {
  projectionColumnLabel,
  projectionColumnSubtitle,
  EXIT_COLUMN_LABEL,
  FORWARD_NOI_LABEL,
} from '@/components/project/pl/ProjectionsSection';
import CashFlowTab from '@/components/project/CashFlowTab';

beforeEach(() => {
  cleanup();
  fx.xlsxRows = [];
  CALENDAR_YEARS = CALENDAR;
  FIELD_OVERRIDES = { acquisition_close_date: { value: '2025-09-30', note: 'PSA' } };
});

/** The two header rows of the projections table: labels, then subtitles. */
function headerRows(): { labels: string[]; subtitles: string[] } {
  const table = document.querySelector('table') as HTMLTableElement;
  const rows = Array.from(table.tHead!.rows);
  // Row 0 carries the two rowSpan=3 stubs (Index, $/%) plus one <th> per column.
  const labels = Array.from(rows[0].cells).slice(2).map((c) => (c.textContent ?? '').trim());
  const subtitles = Array.from(rows[1].cells).map((c) => (c.textContent ?? '').trim());
  return { labels, subtitles };
}

// ── 1. the calendar ──────────────────────────────────────────────────
describe('Projections — the column calendar', () => {
  it('renders "Year N" over its calendar year, with Base Year named as Year 1', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const { labels, subtitles } = headerRows();

    expect(labels[0]).toContain('Base Year (Year 1)');
    expect(labels[1]).toBe('Year 2');
    expect(labels[2]).toBe('Year 3');
    expect(labels[3]).toBe('Year 4');
    expect(labels[4]).toBe('Year 5');
    expect(labels[5]).toBe(EXIT_COLUMN_LABEL);

    expect(subtitles.slice(0, 5)).toEqual(['2025', '2026', '2027', '2028', '2029']);
    // The Exit Year is the year AFTER the last modelled year.
    expect(subtitles[5]).toBe('2030');
  });

  it('NO column subtitle is a bare ordinal — this is the "Base Year 1" regression', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const { subtitles } = headerRows();
    for (const sub of subtitles) {
      expect(sub).not.toMatch(/^\d{1,3}$/); // 1, 2, 3… — an ordinal, not a year
      expect(sub === '—' || /^\d{4}$/.test(sub)).toBe(true);
    }
  });

  it('with no close date the columns keep their labels and show NO year', () => {
    CALENDAR_YEARS = undefined;
    FIELD_OVERRIDES = {};
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const { labels, subtitles } = headerRows();

    expect(labels[0]).toContain('Base Year (Year 1)');
    expect(labels[1]).toBe('Year 2');
    // Never the wall-clock year, never the ordinal — nothing.
    for (const sub of subtitles) expect(sub).toBe('—');
    expect(screen.queryByText(String(new Date().getFullYear()))).toBeNull();
  });

  it('the subtitle helper never prints an ordinal', () => {
    expect(projectionColumnSubtitle(2026)).toBe('2026');
    expect(projectionColumnSubtitle(undefined)).toBe('—');
    expect(projectionColumnLabel(0)).toBe('Base Year (Year 1)');
    expect(projectionColumnLabel(1)).toBe('Year 2');
    expect(projectionColumnLabel(4)).toBe('Year 5');
  });
});

// ── 2. Hotel Delivery ────────────────────────────────────────────────
describe('Projections — Hotel Delivery is a real date or a dash', () => {
  it('renders the deal acquisition close date', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const row = screen.getByText('Hotel Delivery').closest('tr') as HTMLTableRowElement;
    expect(within(row).getByText('9/30/2025')).toBeInTheDocument();
  });

  it('renders a dash — never the fabricated "9/30/1" — with no close date', () => {
    CALENDAR_YEARS = undefined;
    FIELD_OVERRIDES = {};
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const row = screen.getByText('Hotel Delivery').closest('tr') as HTMLTableRowElement;
    expect(row.cells[1].textContent).toBe('—');
    expect(screen.queryByText('9/30/1')).toBeNull();
  });
});

// ── 3. the horizon ───────────────────────────────────────────────────
describe('Projections — the horizon is derived from the hold, not capped at 6', () => {
  it('renders hold_years + 1 columns, the last marked Exit Year', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const { labels } = headerRows();
    expect(labels).toHaveLength(HOLD_YEARS + 1);
    expect(labels[labels.length - 1]).toBe(EXIT_COLUMN_LABEL);
  });

  it('the Exit Year column carries returns.terminal_noi and nothing else', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const row = screen.getByTestId('forward-noi-row') as HTMLTableRowElement;
    expect(within(row).getByText(FORWARD_NOI_LABEL)).toBeInTheDocument();
    // Every modelled year is a dash on this row: year hold+1 is NOT modelled.
    const cells = Array.from(row.cells).slice(2);
    expect(cells).toHaveLength(HOLD_YEARS + 1);
    for (const c of cells.slice(0, HOLD_YEARS)) expect(c.textContent?.trim()).toBe('—');
    expect(cells[HOLD_YEARS].textContent).toContain('2,759,000');
  });

  it('the exit column is display-only — no operating row prints a number in it', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const revenueRow = screen.getByText('Total Revenue').closest('tr') as HTMLTableRowElement;
    const cells = Array.from(revenueRow.cells);
    // Last cell = the Exit Year column (it spans all four sub-columns).
    expect(cells[cells.length - 1].textContent?.trim()).toBe('—');
  });
});

// ── 4. the off-by-one, closed ────────────────────────────────────────
describe('Projections — Base Year IS Year 1, and Cash Flow agrees', () => {
  it("Financials' Year-1 NOI equals the Cash Flow tab's Year-1 NOI from one fixture", async () => {
    // (a) Cash Flow: the "Year 1" column of the NOI row.
    const { unmount } = render(<CashFlowTab />);
    fireEvent.click(screen.getByRole('tab', { name: 'Unlevered' }));
    const noiLabel = screen
      .getAllByText('NOI (before FF&E reserve)')
      .find((el) => el.closest('[role="rowheader"]'))!;
    const rowWrapper = noiLabel.closest('[role="rowheader"]')!.parentElement!;
    const headerCells = Array.from(
      document.querySelectorAll('[role="columnheader"]'),
    ).map((el) => (el.textContent ?? '').trim());
    const yearOneCol = headerCells.indexOf('Year 1');
    expect(yearOneCol).toBeGreaterThan(0);
    // The row's cells exclude the rowheader, and the column headers include the
    // "LINE ITEM" stub — so the two indexes line up after dropping that stub.
    const cfCells = Array.from(rowWrapper.querySelectorAll('[role="cell"]'));
    const cashFlowYearOne = (cfCells[yearOneCol - 1].textContent ?? '').trim();
    expect(cashFlowYearOne).toContain('2,001,056');
    unmount();
    cleanup();

    // (b) Financials → Projections: the FIRST column, which is now named
    //     "Base Year (Year 1)" and therefore claims the same year.
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByRole('button', { name: /export/i }));
    await waitFor(() => expect(fx.xlsxRows.length).toBeGreaterThan(0));

    const headers = fx.xlsxRows[0].map((c) => String(c));
    expect(headers[1]).toBe('Base Year (Year 1) 2025 Amount');
    expect(headers[5]).toBe('Year 2 2026 Amount');
    // No column claims to be "Year 1" twice.
    expect(headers.filter((h) => h.startsWith('Year 1 '))).toHaveLength(0);

    const noiRow = fx.xlsxRows.find((r) =>
      String(r[0]).startsWith('NOI (before FF&E reserve)'),
    )!;
    expect(noiRow[1]).toBe(NOI_BEFORE[0]);
    // Identical to the Cash Flow tab's Year 1 — the off-by-one is closed.
    expect(`$${Number(noiRow[1]).toLocaleString('en-US')}`).toContain(
      cashFlowYearOne.replace(/[^\d,]/g, ''),
    );
    // …and the SECOND Financials column is Year 2's NOI, not Year 1's.
    expect(noiRow[5]).toBe(NOI_BEFORE[1]);
  });

  it('the export header carries the Exit Year column too', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByRole('button', { name: /export/i }));
    await waitFor(() => expect(fx.xlsxRows.length).toBeGreaterThan(0));
    const headers = fx.xlsxRows[0].map((c) => String(c));
    expect(headers).toHaveLength(1 + (HOLD_YEARS + 1) * 4);
    expect(headers[headers.length - 4]).toBe('Exit Year 2030 Amount');
    const fwd = fx.xlsxRows.find((r) => String(r[0]) === FORWARD_NOI_LABEL)!;
    expect(fwd[1 + HOLD_YEARS * 4]).toBe(TERMINAL_NOI);
  });
});
