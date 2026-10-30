/**
 * FON-118 / FON-44 — returns by exit year, read through ONE module.
 *
 * Contracts locked here:
 *
 *  1. `lib/engines/returns.ts` is the only reader of the returns headline and
 *     its exit schedule: Returns hero, Exit Assumptions, Overview and the IC
 *     memo all print what it returns. No component derives a return, an exit
 *     year or a sale month (the old "Exit Year = Year {hold}" is gone — it was
 *     wrong for a month-52 disposition).
 *  2. A `not_available` row renders "—" with its reason's label and a title —
 *     never 0 — and a reason code this bundle's registry predates renders as
 *     its raw code rather than failing the row.
 *  3. The monthly solver's sentinels (10.0 / −0.9999) are never printed as a
 *     percentage, in the table or on the hero.
 *  4. The selected row is marked "Selected exit" and prints the SAME levered
 *     IRR text as the hero tile; the peak row is marked "Peak levered IRR".
 *  5. The hold period badged "Default for development deals — confirm" when
 *     its `assumption_sources` tag is `deal_type_default`.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, ReturnsExitScheduleRow, ScenarioRecord } from '@/lib/api';
import {
  headlineReturns,
  exitSchedule,
  irrValue,
  isIrrSentinel,
  IRR_SENTINEL_NOTE,
  exitRowLabel,
  exitRowCalendarYear,
  exitRowReasonLabel,
  exitRowReasonTitle,
  selectedExitLabel,
  holdDefaultBadge,
  HOLD_DEAL_TYPE_DEFAULT_BADGE,
  EXIT_SCHEDULE_TITLE,
  EXIT_SCHEDULE_SELECTED_BADGE,
  EXIT_SCHEDULE_PEAK_BADGE,
  EXIT_SCHEDULE_MISSING,
  SELECTED_EXIT_MISSING,
} from '@/lib/engines/returns';
import { REASONS } from '@/lib/ontology/reasons.generated';

// ── Fixture: a five-year hold sold at month 60, peak levered IRR in Year 3 ──
const SELECTED_LEVERED = 0.2301;
const SELECTED_UNLEVERED = 0.161;
const SELECTED_EM = 2.37;

const row = (patch: Partial<ReturnsExitScheduleRow> & Pick<ReturnsExitScheduleRow, 'exit_month'>): ReturnsExitScheduleRow => ({
  exit_year: null,
  is_selected: false,
  is_peak_levered: false,
  status: 'computed',
  reason: null,
  reason_detail: null,
  exit_noi_basis: 'forward_12m',
  exit_noi_period_label: '',
  terminal_noi: null,
  gross_sale_price: null,
  selling_costs: null,
  transfer_tax: null,
  loan_payoff: null,
  refi_included: false,
  net_proceeds: null,
  levered_irr: null,
  unlevered_irr: null,
  equity_multiple: null,
  ...patch,
});

function schedule(): ReturnsExitScheduleRow[] {
  return [
    // Year 1 — a known reason code.
    row({
      exit_year: 1, exit_month: 12, status: 'not_available', reason: 'inconclusive',
      reason_detail: 'cash flows have one sign; no IRR', exit_noi_basis: null,
      exit_noi_period_label: 'Forward 12-month NOI (Year 2, projected)',
    }),
    // Year 2 — a code this bundle's registry does not know yet (Task 5 regen).
    row({
      exit_year: 2, exit_month: 24, status: 'not_available', reason: 'under_construction',
      reason_detail: 'construction window runs to month 30', exit_noi_basis: null,
      exit_noi_period_label: 'Forward 12-month NOI (Year 3, projected)',
    }),
    row({
      exit_year: 3, exit_month: 36, is_peak_levered: true,
      exit_noi_period_label: 'Forward 12-month NOI (Year 4, projected)',
      terminal_noi: 3_300_000, gross_sale_price: 47_142_857, selling_costs: 942_857, transfer_tax: 0,
      loan_payoff: 25_000_000, net_proceeds: 21_200_000,
      levered_irr: 0.265, unlevered_irr: 0.18, equity_multiple: 1.9,
    }),
    row({
      exit_year: 4, exit_month: 48,
      exit_noi_period_label: 'Forward 12-month NOI (Year 5, projected)',
      terminal_noi: 3_400_000, gross_sale_price: 48_571_428, selling_costs: 971_428, transfer_tax: 0,
      loan_payoff: 24_500_000, refi_included: true, net_proceeds: 23_100_000,
      levered_irr: 0.248, unlevered_irr: 0.172, equity_multiple: 2.1,
    }),
    row({
      exit_year: 5, exit_month: 60, is_selected: true,
      exit_noi_period_label: 'Forward 12-month NOI (Year 6)',
      terminal_noi: 3_640_000, gross_sale_price: 52_000_000, selling_costs: 1_040_000, transfer_tax: 0,
      loan_payoff: 24_000_000, refi_included: true, net_proceeds: 21_000_000,
      levered_irr: SELECTED_LEVERED, unlevered_irr: SELECTED_UNLEVERED, equity_multiple: SELECTED_EM,
    }),
  ];
}

function returnsOutputs(): Record<string, unknown> {
  return {
    levered_irr: SELECTED_LEVERED,
    unlevered_irr: SELECTED_UNLEVERED,
    equity_multiple: SELECTED_EM,
    year_one_coc: 0.081,
    avg_coc: 0.0812,
    gross_sale_price: 52_000_000,
    net_proceeds: 21_000_000,
    selling_costs: 1_040_000,
    terminal_noi: 3_640_000,
    hold_years: 5,
    exit_cap_rate: 0.07,
    exit_noi_basis: 'forward_12m',
    exit_noi_period_label: 'Forward 12-month NOI (Year 6)',
    noi_by_year: [3_000_000, 3_100_000, 3_200_000, 3_300_000, 3_400_000],
    cash_flows: [-19_000_000, 1_400_000, 1_500_000, 1_600_000, 1_700_000, 22_700_000],
    selected_exit_month: 60,
    selected_exit_year: 5,
    peak_levered_exit_year: 3,
    exit_schedule: schedule(),
  };
}

type EngineRow = { deal_id: string; engine: string; status: string; summary: string; outputs: Record<string, unknown>; inputs: Record<string, unknown>; error: null; runtime_ms: number; started_at: null; completed_at: null; run_id: string };
const engineRow = (engine: string, outputs: Record<string, unknown>, inputs: Record<string, unknown> = {}): EngineRow => ({
  deal_id: 'deal-1', engine, status: 'complete', summary: '', outputs, inputs,
  error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'run-1',
});

function fullRun(returnsPatch: Record<string, unknown> = {}): EngineOutputsResponse {
  const ret = { ...returnsOutputs(), ...returnsPatch };
  for (const [k, v] of Object.entries(returnsPatch)) if (v === undefined) delete ret[k];
  return {
    deal_id: 'deal-1',
    engines: {
      returns: engineRow('returns', ret, {
        assumptions: { exit_cap_rate: 0.07, revpar_growth: 0.045, hold_years: 5, ltv: 0.65, interest_rate: 0.068 },
      }),
      capital: engineRow('capital', {
        purchase_price: 34_000_000, price_per_key: 257_576, total_capital: 44_000_000, total_capital_usd: 44_000_000,
        equity_amount: 19_000_000, debt_amount: 25_000_000,
        uses: [{ label: 'Purchase Price', amount: 34_000_000 }],
        sources: [{ label: 'Senior Loan', amount: 25_000_000 }, { label: 'Equity', amount: 19_000_000 }],
      }),
      expense: engineRow('expense', {
        years: [{ year: 1, total_revenue: 11_900_000, noi: 3_000_000, noi_institutional: 3_400_000 }],
        stabilization: {
          stabilized_year_index: 2, stabilized_year: 3, source: 'default_year_3', anchor: 'acquisition_close',
          stabilized_noi_before_reserve: 3_400_000, stabilized_cash_noi: 3_200_000,
        },
      }),
      revenue: engineRow('revenue', {
        years: [{ year: 1, occupancy: 0.7, adr: 385, revpar: 269.5, total_revenue: 11_900_000 }],
        projection_calendar_years: [2026, 2027, 2028, 2029, 2030],
        projection_start_year: 2026,
      }),
      debt: engineRow('debt', { year_one_dscr: 1.45, interest_rate: 0.068, loan_amount: 25_000_000 }),
    },
  } as unknown as EngineOutputsResponse;
}

// ── Harness ─────────────────────────────────────────────────────────
const nav = vi.hoisted(() => ({ params: new URLSearchParams(''), push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => '/projects/deal-1',
}));

const outputsRef = vi.hoisted(() => ({ value: null as unknown }));
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: outputsRef.value, previous: null, loading: false, settled: true, lastRunAt: null, error: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
const provenance = vi.hoisted(() => ({ sources: {} as Record<string, string> }));
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key?: string) =>
    key && provenance.sources[key] ? { source: provenance.sources[key], value: null, reason: null } : null,
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-1', name: 'Harbor Hotel', keys: 132, city: 'Miami Beach, FL', deal_type: 'acquisition', return_profile: 'value-add', field_overrides: {} },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [], critical: 0, warn: 0, info: 0, note: null, loading: false, error: null }),
}));
const RECORDS = [
  { id: 's-base', deal_id: 'deal-1', name: 'Base Case', is_base: true, in_memo: true, overrides: [] },
] as unknown as ScenarioRecord[];
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: vi.fn(async () => ({ id: 'deal-1' })) },
      market: { ...actual.api.market, overview: vi.fn(async () => ({})) },
      engines: {
        ...actual.api.engines,
        returnsPreview: vi.fn(),
        timeline: vi.fn(async () => ({ events: [], close_date: null, exit_date: null })),
      },
      scenarios: {
        ...actual.api.scenarios,
        list: vi.fn(async () => RECORDS),
        compare: vi.fn(async () => ({ deal_id: 'deal-1', base_scenario_id: 's-base', scenarios: [] })),
      },
    },
  };
});
vi.mock('@/components/project/PricingSensitivityPanel', () => ({ default: () => null }));
vi.mock('@/components/project/MaxPricePanel', () => ({ default: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/components/help/Traced', () => ({
  Traced: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ReturnsTab from '@/components/project/ReturnsTab';
import OverviewTab from '@/components/project/OverviewTab';
import ICMemoTab from '@/components/project/ICMemoTab';
import type { Project } from '@/lib/project';

const PROJECT = { id: 0, name: 'Harbor Hotel' } as unknown as Project;

/** The schedule's data rows, in emitted order. */
const scheduleRows = (root: HTMLElement | Document = document) =>
  Array.from(root.querySelectorAll<HTMLElement>('[data-exit-row]'));
const rowByLabel = (label: string, root: HTMLElement | Document = document) => {
  const r = scheduleRows(root).find((el) => el.getAttribute('data-exit-row') === label);
  if (!r) throw new Error(`no exit-schedule row labelled ${label}`);
  return r;
};
/** The Exit Assumptions "Exit Year" row (label span → left span → row div). */
const exitYearRow = () => screen.getByText('Exit Year').parentElement!.parentElement!;

beforeEach(() => {
  cleanup();
  nav.params = new URLSearchParams('');
  outputsRef.value = fullRun();
  provenance.sources = {};
  window.sessionStorage.clear();
});

// ── lib/engines/returns.ts ───────────────────────────────────────────
describe('lib/engines/returns — the one reader', () => {
  it('headlineReturns reads the published fields and nothing derived', () => {
    const h = headlineReturns(returnsOutputs());
    expect(h.leveredIrr).toBe(SELECTED_LEVERED);
    expect(h.unleveredIrr).toBe(SELECTED_UNLEVERED);
    expect(h.equityMultiple).toBe(SELECTED_EM);
    expect(h.holdYears).toBe(5);
    expect(h.selectedExitMonth).toBe(60);
    expect(h.selectedExitYear).toBe(5);
    expect(h.peakLeveredExitYear).toBe(3);
    expect(h.exitNoiBasis).toBe('forward_12m');
    expect(h.exitNoiPeriodLabel).toBe('Forward 12-month NOI (Year 6)');
    expect(h.exitSchedule).toHaveLength(5);
    expect(h.hasExitSchedule).toBe(true);
  });

  it('a run that predates the schedule publishes NO selected exit — the hold is not a fallback', () => {
    const h = headlineReturns({ levered_irr: 0.2, hold_years: 5 });
    expect(h.selectedExitMonth).toBeNull();
    expect(h.selectedExitYear).toBeNull();
    expect(h.exitSchedule).toEqual([]);
    expect(h.hasExitSchedule).toBe(false);
    expect(selectedExitLabel(h)).toBeNull();
  });

  it('tolerates a missing / malformed returns blob', () => {
    expect(headlineReturns(undefined).leveredIrr).toBeNull();
    expect(headlineReturns(null).exitSchedule).toEqual([]);
    expect(headlineReturns('nope').holdYears).toBeNull();
    expect(exitSchedule({ exit_schedule: 'nope' })).toEqual([]);
    expect(exitSchedule({ exit_schedule: [{ exit_month: 'x' }, null, schedule()[4]] })).toHaveLength(1);
  });

  it('IRR sentinels are never a rate', () => {
    expect(isIrrSentinel(10.0)).toBe(true);
    expect(isIrrSentinel(-0.9999)).toBe(true);
    expect(isIrrSentinel(0.23)).toBe(false);
    expect(irrValue(10.0)).toBeNull();
    expect(irrValue(-0.9999)).toBeNull();
    expect(irrValue(0.23)).toBe(0.23);
    expect(irrValue(null)).toBeNull();
    const h = headlineReturns({ levered_irr: 10.0, unlevered_irr: 0.1 });
    expect(h.leveredIrr).toBeNull();
    expect(h.leveredIrrNote).toBe(IRR_SENTINEL_NOTE);
    expect(h.unleveredIrr).toBe(0.1);
    expect(h.unleveredIrrNote).toBeNull();
    // Absent is absent — not a sentinel note.
    expect(headlineReturns({}).leveredIrrNote).toBeNull();
  });

  it('row labels: year-end rows by year, an off-cycle row by month; calendar year only from the projection calendar', () => {
    expect(exitRowLabel({ exit_year: 3, exit_month: 36 })).toBe('Year 3');
    expect(exitRowLabel({ exit_year: null, exit_month: 52 })).toBe('Month 52');
    expect(exitRowCalendarYear({ exit_year: 3 }, [2026, 2027, 2028, 2029, 2030])).toBe(2028);
    expect(exitRowCalendarYear({ exit_year: 3 }, null)).toBeUndefined();
    expect(exitRowCalendarYear({ exit_year: null }, [2026])).toBeUndefined();
    expect(exitRowCalendarYear({ exit_year: 9 }, [2026])).toBeUndefined();
  });

  it('selected exit: "Year 5 · month 60" at a year-end, "Month 52" off-cycle', () => {
    expect(selectedExitLabel({ selectedExitMonth: 60, selectedExitYear: 5 })).toBe('Year 5 · month 60');
    expect(selectedExitLabel({ selectedExitMonth: 52, selectedExitYear: null })).toBe('Month 52');
    expect(selectedExitLabel({ selectedExitMonth: null, selectedExitYear: null })).toBeNull();
  });

  it('a not_available row names its reason from REASONS; an unknown code renders as itself', () => {
    const known = schedule()[0];
    expect(exitRowReasonLabel(known)).toBe(REASONS.inconclusive.label);
    expect(exitRowReasonTitle(known)).toBe(`${REASONS.inconclusive.label} — cash flows have one sign; no IRR`);
    const unknown = schedule()[1];
    expect(exitRowReasonLabel(unknown)).toBe('under_construction');
    expect(exitRowReasonTitle(unknown)).toBe('under_construction — construction window runs to month 30');
    // No detail → the registry's explanation stands in.
    expect(exitRowReasonTitle({ status: 'not_available', reason: 'inconclusive', reason_detail: null }))
      .toBe(`${REASONS.inconclusive.label} — ${REASONS.inconclusive.explanation}`);
    expect(exitRowReasonLabel(schedule()[4])).toBeNull();
  });

  it('the hold badge appears for the deal-type default and for nothing else', () => {
    expect(holdDefaultBadge('deal_type_default')).toBe(HOLD_DEAL_TYPE_DEFAULT_BADGE);
    expect(holdDefaultBadge('seed')).toBeNull();
    expect(holdDefaultBadge('analyst_override')).toBeNull();
    expect(holdDefaultBadge(null)).toBeNull();
  });
});

// ── Returns section ──────────────────────────────────────────────────
describe('Returns — the exit schedule card', () => {
  it('renders every row; the selected row is marked and prints the hero Levered IRR text', () => {
    render(<ReturnsTab />);
    expect(screen.getByText(EXIT_SCHEDULE_TITLE)).toBeInTheDocument();
    const rows = scheduleRows();
    expect(rows.map((r) => r.getAttribute('data-exit-row'))).toEqual(['Year 1', 'Year 2', 'Year 3', 'Year 4', 'Year 5']);

    const selected = rowByLabel('Year 5');
    expect(selected).toHaveAttribute('data-selected', 'true');
    expect(within(selected).getByText(EXIT_SCHEDULE_SELECTED_BADGE)).toBeInTheDocument();
    // The SAME text as the hero tile — the headline IS this row.
    const heroText = '23.01%';
    expect(within(selected).getByText(heroText)).toBeInTheDocument();
    expect(screen.getAllByText(heroText).length).toBeGreaterThanOrEqual(2);
    expect(within(selected).getByText('2.37x')).toBeInTheDocument();
    expect(within(selected).getByText('16.10%')).toBeInTheDocument();
    expect(within(selected).getByText('$21.00M')).toBeInTheDocument();
    expect(within(selected).getByText('Forward 12-month NOI (Year 6)')).toBeInTheDocument();
  });

  it('the peak row is marked and carries the calendar year from the projection calendar', () => {
    render(<ReturnsTab />);
    const peak = rowByLabel('Year 3');
    expect(peak).toHaveAttribute('data-peak', 'true');
    expect(within(peak).getByText(EXIT_SCHEDULE_PEAK_BADGE)).toBeInTheDocument();
    expect(peak.textContent).toContain('2028');
    expect(within(peak).getByText('26.50%')).toBeInTheDocument();
    expect(rowByLabel('Year 5').textContent).toContain('2030');
    expect(screen.queryAllByText(EXIT_SCHEDULE_PEAK_BADGE)).toHaveLength(1);
    expect(screen.queryAllByText(EXIT_SCHEDULE_SELECTED_BADGE)).toHaveLength(1);
  });

  it('a not_available row renders "—" with the reason label and a title — never 0', () => {
    render(<ReturnsTab />);
    const y1 = rowByLabel('Year 1');
    expect(y1).toHaveAttribute('data-reason', 'inconclusive');
    expect(within(y1).getByText(REASONS.inconclusive.label)).toBeInTheDocument();
    const dashes = within(y1).getAllByText('—');
    expect(dashes.length).toBeGreaterThanOrEqual(6);
    for (const d of dashes) expect(d.closest('[title]')?.getAttribute('title')).toContain('cash flows have one sign; no IRR');
    expect(y1.textContent).not.toMatch(/\$0\b|0\.00%|0\.00x/);
  });

  it('a reason code the registry predates renders as its raw code, not a crash', () => {
    render(<ReturnsTab />);
    const y2 = rowByLabel('Year 2');
    expect(y2).toHaveAttribute('data-reason', 'under_construction');
    expect(within(y2).getByText('under_construction')).toBeInTheDocument();
    expect(within(y2).getAllByText('—').length).toBeGreaterThanOrEqual(6);
  });

  it('an IRR sentinel in a computed row is a dash with the sentinel note, never 1000.00%', () => {
    const rows = schedule();
    rows[3] = { ...rows[3], levered_irr: 10.0 };
    outputsRef.value = fullRun({ exit_schedule: rows });
    render(<ReturnsTab />);
    expect(screen.queryByText('1000.00%')).toBeNull();
    const y4 = rowByLabel('Year 4');
    const dash = within(y4).getByText('—');
    expect(dash.closest('[title]')?.getAttribute('title')).toBe(IRR_SENTINEL_NOTE);
    // Its other figures still print.
    expect(within(y4).getByText('17.20%')).toBeInTheDocument();
  });

  it('a sentinel on the headline itself is withheld on the hero with the note, never 1000.00%', () => {
    outputsRef.value = fullRun({ levered_irr: 10.0 });
    render(<ReturnsTab />);
    expect(screen.queryByText('1000.00%')).toBeNull();
    expect(screen.getAllByText(IRR_SENTINEL_NOTE).length).toBeGreaterThanOrEqual(1);
  });

  it('a run without the schedule shows the card with the missing field named, and no rows', () => {
    outputsRef.value = fullRun({ exit_schedule: undefined, selected_exit_month: undefined, selected_exit_year: undefined, peak_levered_exit_year: undefined });
    render(<ReturnsTab />);
    expect(screen.getByText(EXIT_SCHEDULE_TITLE)).toBeInTheDocument();
    expect(screen.getByText(EXIT_SCHEDULE_MISSING)).toBeInTheDocument();
    expect(scheduleRows()).toHaveLength(0);
  });

  it('the Exit Year row is the selected exit — "Year 5 · month 60" — not "Year {hold}"', () => {
    render(<ReturnsTab />);
    expect(exitYearRow().textContent).toContain('Year 5 · month 60');
  });

  it('an off-cycle disposition reads "Month 52" on the Exit Year row and as its own schedule row', () => {
    const rows = [
      ...schedule().map((r) => ({ ...r, is_selected: false })),
      row({
        exit_year: null, exit_month: 52, is_selected: true, refi_included: true,
        exit_noi_period_label: 'Forward 12-month NOI (Year 5, projected)',
        terminal_noi: 3_400_000, gross_sale_price: 48_571_428, selling_costs: 971_428, transfer_tax: 0,
        loan_payoff: 24_500_000, net_proceeds: 23_000_000, levered_irr: 0.3959, unlevered_irr: 0.19, equity_multiple: 2.2,
      }),
    ];
    outputsRef.value = fullRun({ exit_schedule: rows, selected_exit_month: 52, selected_exit_year: null, levered_irr: 0.3959 });
    render(<ReturnsTab />);
    expect(exitYearRow().textContent).toContain('Month 52');
    expect(exitYearRow().textContent).not.toContain('Year 5');
    const off = rowByLabel('Month 52');
    expect(off).toHaveAttribute('data-selected', 'true');
    expect(within(off).getByText('39.59%')).toBeInTheDocument();
    expect(rowByLabel('Year 5')).not.toHaveAttribute('data-selected', 'true');
  });

  it('a run that predates the selected exit leaves the Exit Year row an honest dash with the field named', () => {
    outputsRef.value = fullRun({ exit_schedule: undefined, selected_exit_month: undefined, selected_exit_year: undefined, peak_levered_exit_year: undefined });
    render(<ReturnsTab />);
    const r = exitYearRow();
    expect(r.textContent).not.toContain('Year 5');
    expect(r.getAttribute('title')).toBe(SELECTED_EXIT_MISSING);
  });

  it('the hold period is badged as the development default when the worker tags it so', () => {
    provenance.sources = { hold_years: 'deal_type_default' };
    render(<ReturnsTab />);
    expect(screen.getByTestId('exit-assumption-source-hold_years')).toHaveTextContent(HOLD_DEAL_TYPE_DEFAULT_BADGE);
    expect(screen.queryByRole('img', { name: 'Linked' })).toBeNull();
    expect(screen.getAllByRole('img', { name: 'Assumption' }).length).toBeGreaterThanOrEqual(1);
  });

  it('any other hold source keeps its own label — no badge', () => {
    provenance.sources = { hold_years: 'analyst_override' };
    render(<ReturnsTab />);
    expect(screen.getByTestId('exit-assumption-source-hold_years')).not.toHaveTextContent(HOLD_DEAL_TYPE_DEFAULT_BADGE);
  });
});

// ── Overview and IC Memo read the same headline ──────────────────────
describe('Overview and IC Memo read headlineReturns', () => {
  it('Overview KPI prints the headline levered IRR, and withholds a sentinel', () => {
    const { unmount } = render(<OverviewTab projectId="deal-1" />);
    expect(screen.getByTestId('overview-kpi-levered-irr').textContent).toContain('23.0%');
    expect(screen.getByTestId('overview-kpi-unlevered-irr').textContent).toContain('16.1%');
    unmount();
    cleanup();
    outputsRef.value = fullRun({ levered_irr: 10.0 });
    render(<OverviewTab projectId="deal-1" />);
    expect(screen.getByTestId('overview-kpi-levered-irr').textContent).not.toContain('1000');
    expect(screen.getByTestId('overview-kpi-levered-irr').textContent).toContain('—');
  });

  it('IC Memo prints the headline and the compact schedule with the same selected row', async () => {
    render(<ICMemoTab project={PROJECT} />);
    const strip = await screen.findByTestId('ic-memo-exit-schedule');
    expect(within(strip).getByText(EXIT_SCHEDULE_TITLE)).toBeInTheDocument();
    const rows = scheduleRows(strip);
    expect(rows.map((r) => r.getAttribute('data-exit-row'))).toEqual(['Year 1', 'Year 2', 'Year 3', 'Year 4', 'Year 5']);
    const selected = rowByLabel('Year 5', strip);
    expect(within(selected).getByText(EXIT_SCHEDULE_SELECTED_BADGE)).toBeInTheDocument();
    expect(within(selected).getByText('23.01%')).toBeInTheDocument();
    expect(within(rowByLabel('Year 1', strip)).getByText(REASONS.inconclusive.label)).toBeInTheDocument();
    // Headline unchanged: the Returns group's Levered IRR is the same figure.
    expect(screen.getAllByText('23.0%').length).toBeGreaterThanOrEqual(1);
  });

  it('IC Memo without the schedule names the missing field, never a borrowed table', async () => {
    outputsRef.value = fullRun({ exit_schedule: undefined, selected_exit_month: undefined, selected_exit_year: undefined });
    render(<ICMemoTab project={PROJECT} />);
    const strip = await screen.findByTestId('ic-memo-exit-schedule');
    expect(within(strip).getByText(EXIT_SCHEDULE_MISSING)).toBeInTheDocument();
    expect(scheduleRows(strip)).toHaveLength(0);
  });
});
