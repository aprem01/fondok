/**
 * Financials tab — Projections "Assumptions" panel (canonical rebuild).
 *
 * Contracts locked here (design/canonical/Financials Tab.dc.html):
 *
 *  1. ASSUMPTIONS PANEL RENDERS from mocked engine outputs — the three cards
 *     (Growth / Resort fee revenue / Deal economics) and every driver field
 *     the canonical shows are present, keyed off the worker projection years.
 *
 *  2. CANONICAL EDIT PATH — editing an assumption (Management fee) PATCHes the
 *     deal's ``field_overrides`` via api.deals.update and re-runs the model
 *     (the same path the driver cells use). pct fields persist as a fraction.
 *
 *  3. EXIT CAP IS INVESTMENT-OWNED — it is shown here linked / read-only (a
 *     "sourced from Investment →" reference), NOT as a second editable owner:
 *     its row carries no input.
 *
 * Write-only — not part of the run set for this change.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

// One base year + two forecast years of worker revenue/fb/expense output —
// enough for buildFromWorker() to produce a non-null `years` (the render gate).
const revYear = (year: number) => ({
  year,
  occupancy: 0.75,
  adr: 300,
  revpar: 225,
  rooms_revenue: 10_000_000,
  fb_revenue: 2_000_000,
  other_revenue: 500_000,
  total_revenue: 12_500_000,
});
const fbYear = (year: number) => ({
  year,
  rooms_revenue: 10_000_000,
  fb_revenue: 2_000_000,
  resort_fees: 0,
  other_revenue: 500_000,
  total_revenue: 12_500_000,
});
const expYear = (year: number) => ({
  year,
  total_revenue: 12_500_000,
  dept_expenses: { rooms: 2_500_000, food_beverage: 1_500_000, other_operated: 250_000, total: 4_250_000 },
  undistributed: {
    administrative_general: 900_000,
    information_telecom: 180_000,
    sales_marketing: 800_000,
    property_operations: 500_000,
    utilities: 560_000,
    total: 2_940_000,
  },
  mgmt_fee: 375_000,
  ffe_reserve: 500_000,
  fixed_charges: { property_taxes: 700_000, insurance: 200_000, rent: 0, other_fixed: 0, total: 900_000 },
  gop: 5_310_000,
  // The two NOI bases must DIFFER in every fixture — an equal pair is exactly
  // what let the Investment/Overview label collision survive (FON-59 #1).
  // gop 5,310,000 - mgmt 375,000 - fixed 900,000 = 4,035,000 before reserve;
  // less the 500,000 FF&E reserve = 3,535,000 Cash NOI.
  noi: 3_535_000,
  noi_institutional: 4_035_000,
});

const OUTPUTS = {
  deal_id: 'deal-uuid-1',
  engines: {
    revenue: {
      deal_id: 'deal-uuid-1', engine: 'revenue', status: 'complete', summary: '',
      outputs: { years: [2025, 2026, 2027].map(revYear) },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
    fb: {
      deal_id: 'deal-uuid-1', engine: 'fb', status: 'complete', summary: '',
      outputs: { years: [2025, 2026, 2027].map(fbYear) },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
    },
    expense: {
      deal_id: 'deal-uuid-1', engine: 'expense', status: 'complete', summary: '',
      outputs: { years: [2025, 2026, 2027].map(expYear) },
      inputs: {}, error: null, runtime_ms: 5, started_at: null, completed_at: null, run_id: 'run-1',
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
      outputs: OUTPUTS_OVERRIDE === undefined ? OUTPUTS : OUTPUTS_OVERRIDE,
      previous: null,
      loading: false,
      settled: SETTLED,
      lastRunAt: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});

const refreshDealSpy = vi.fn();
// Read at render time by the hoisted useEngineOutputs mock: `settled` gates
// PLTab's loading skeleton; OUTPUTS_OVERRIDE lets a test serve null outputs.
let SETTLED = true;
let OUTPUTS_OVERRIDE: EngineOutputsResponse | null | undefined = undefined;
void SETTLED; void OUTPUTS_OVERRIDE;
// Settable per-test (read at render time — the factory itself is hoisted) so
// the FON-61 Revert can start from a deal that carries the STR seed.
let mockFieldOverrides: Record<string, unknown> = {};
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-uuid-1', keys: 132, field_overrides: mockFieldOverrides },
    status: null,
    loading: false,
    error: null,
    fromMock: false,
    refresh: refreshDealSpy,
  }),
}));

const engineRunSpy = vi.fn(async () => {});
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: engineRunSpy, status: 'idle', error: null }),
}));

// Exit cap has no resolvable source in this fixture → panel falls back to the
// engine default (7.0%). The reference stays linked / read-only regardless.
// Settable per-test so the FON-61 Year-1 basis chip (driven by the worker's
// source tags, never the flag alone) can be exercised.
let mockSources: Record<string, string> = {};
// Phase 4.4 — the worker's machine-readable refusal codes ride the SAME
// assumption_sources payload as the source tags (`reasons[key]`, a bare
// ReasonCode). EMPTY unless a test sets one, so every pre-existing
// expectation below sees exactly the object it saw before.
let mockReasons: Record<string, string> = {};
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key: string | undefined) =>
    key && (mockSources[key] || mockReasons[key])
      ? { source: mockSources[key] ?? '', value: null, reason: mockReasons[key] ?? null }
      : null,
}));

// api surface — spy on the field_overrides PATCH (the canonical edit path).
const updateSpy = vi.fn(async (_id: string, _body: unknown) => ({ id: 'deal-uuid-1' }));
// E-015 — what GET /deals/{id}/lineage serves (null = nothing recorded).
const lineageSpy = vi.fn(async (_id: string): Promise<unknown> => null);
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      // Lazy-wrapped: a direct `update: updateSpy` is read when the hoisted
      // vi.mock factory builds this object (before `updateSpy`'s const is
      // initialized) → "Cannot access 'updateSpy' before initialization".
      deals: {
        ...actual.api.deals,
        update: (...a: unknown[]) => updateSpy(...(a as Parameters<typeof updateSpy>)),
        // E-015 — the lineage endpoint. Serves whatever a test parks here; the
        // default (null) is the "no lineage recorded" state.
        lineage: async (id: string) => lineageSpy(id),
      },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ProjectionsSection, {
  NO_CLOSE_DATE_NOTE,
  NO_TRACE_MESSAGE,
  PROJECTED_COLUMN_BASIS,
} from '@/components/project/pl/ProjectionsSection';
import { LINEAGE_OPEN_EVENT, LineageDrawerHost, type LineageOpenDetail } from '@/components/project/LineageDrawer';
import { clearLineageCache } from '@/lib/hooks/useLineage';
import { CASH_NOI_LABEL, NOI_BEFORE_RESERVE_LABEL } from '@/lib/engines/noi';
import { STR_MARKET_OVERRIDE_NOTE } from '@/lib/provenance';

beforeEach(() => {
  cleanup();
  mockSources = {};
  mockReasons = {};
  mockFieldOverrides = {};
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
});

describe('Financials · Projections — Assumptions panel renders from engine output', () => {
  it('shows the panel intro + every canonical driver field', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByText('Assumptions')).toBeInTheDocument();
    expect(
      screen.getByText(/These drive every projected year below/i),
    ).toBeInTheDocument();

    // Growth card.
    // FON-69 — relabeled: the lever drives ADR growth with the occupancy path held.
    expect(screen.getByText('RevPAR growth (drives ADR; occupancy path held)')).toBeInTheDocument();
    expect(screen.getByText('Dept. expense inflation')).toBeInTheDocument();
    expect(screen.getByText('Other expense inflation')).toBeInTheDocument();
    // Resort fee card.
    expect(screen.getByText('Resort fee revenue')).toBeInTheDocument();
    expect(screen.getByText('Resort fee')).toBeInTheDocument();
    // FON-41 — each capture input names the COLUMN it moves, and the column
    // names moved in Wave 3: index 0 IS operating Year 1, so it heads
    // "Base Year (Year 1)" and every later label is shifted up one. The
    // capture labels and their explanatory note move WITH the header, or they
    // contradict it (Sam, 2026-09-11).
    expect(screen.getByText('Capture — Base year (Year 1)')).toBeInTheDocument();
    expect(screen.getByText('Capture — Year 2')).toBeInTheDocument();
    expect(screen.getByText('Capture — Year 3+')).toBeInTheDocument();
    expect(
      screen.getByText(/Base year \(Year 1\) is the model's first operating year/),
    ).toBeInTheDocument();
    // Every capture label names a column header the statement actually renders.
    for (const name of ['Base year \\(Year 1\\)', 'Year 2', 'Year 3']) {
      expect(screen.getAllByText(new RegExp(`^${name}`)).length).toBeGreaterThan(0);
    }
    // The Stabilization Year editor (FON-59 #3) lives here too.
    expect(screen.getByText('Stabilization')).toBeInTheDocument();
    expect(screen.getByText('Stabilization Year')).toBeInTheDocument();
    // Deal economics card.
    expect(screen.getByText('Deal economics')).toBeInTheDocument();
    expect(screen.getByText('Management fee')).toBeInTheDocument();
    expect(screen.getByText('Exit cap rate')).toBeInTheDocument();
  });
});

describe('Financials · Projections — canonical edit path (field_overrides + re-run)', () => {
  it('editing Management fee PATCHes field_overrides (as a fraction) and re-runs', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    const mgmtRow = screen.getByText('Management fee').parentElement as HTMLElement;
    const mgmtInput = mgmtRow.querySelector('input[type="number"]') as HTMLInputElement;
    expect(mgmtInput).toBeTruthy();
    expect(mgmtInput.value).toBe('3'); // default 0.03 → 3.0%

    fireEvent.change(mgmtInput, { target: { value: '5' } });
    fireEvent.blur(mgmtInput);

    // FON-74 — the management fee is engine input, so the change parks until
    // the analyst says why. Nothing is written and nothing re-runs yet.
    const note = await screen.findByTestId('assumption-panel-note-mgmt_fee_pct');
    expect(updateSpy).not.toHaveBeenCalled();
    expect(engineRunSpy).not.toHaveBeenCalled();

    fireEvent.change(note, { target: { value: 'Operator agreement 9/12' } });
    fireEvent.click(screen.getByTestId('assumption-panel-save-mgmt_fee_pct'));

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const lastCall = updateSpy.mock.calls.at(-1) as [string, { field_overrides: Record<string, unknown> }];
    const entry = lastCall[1].field_overrides.mgmt_fee_pct as { value: number; note: string };
    expect(entry.value).toBeCloseTo(0.05); // pct persisted as a fraction
    expect(entry.note).toBe('Operator agreement 9/12');
    expect(engineRunSpy).toHaveBeenCalled(); // full re-run
  });

  it('FON-74 — an assumption re-typed to its own value never asks why', async () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    const mgmtRow = screen.getByText('Management fee').parentElement as HTMLElement;
    const mgmtInput = mgmtRow.querySelector('input[type="number"]') as HTMLInputElement;
    fireEvent.change(mgmtInput, { target: { value: '3' } }); // the value on screen
    fireEvent.blur(mgmtInput);

    expect(screen.queryByTestId('assumption-panel-note-mgmt_fee_pct')).not.toBeInTheDocument();
    expect(updateSpy).not.toHaveBeenCalled();
  });
});

describe('Financials · Projections — Exit cap is Investment-owned (read-only)', () => {
  it('renders exit cap linked with an Investment reference and NO editable input', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    const exitRow = screen.getByText('Exit cap rate').parentElement as HTMLElement;
    // No input in the exit-cap row — it is not a second editable owner.
    expect(exitRow.querySelector('input')).toBeNull();

    // A "sourced from Investment →" reference deep-links to the Investment tab.
    // Scoped to the exit-cap row: FON-41 added a second Investment reference on
    // the control bar (the derived Base year names its owner the same way).
    const ref = within(exitRow).getByText('CAPEX →');
    expect(ref).toBeInTheDocument();
    expect(ref.closest('a')?.getAttribute('href')).toContain('tab=investment');
  });
});

// FON-61 (D4) — the Year-1 basis chip reads the WORKER's source tags. It is
// never inferred from the flag alone, so the STR seed can't be shown as
// "active" when it did not populate.
describe('Financials · Projections — Year-1 basis chip is driven by worker source tags', () => {
  it('shows "Active basis: Market / STR · Revert" on str_forecast; Revert drops the flag + STR-noted keys only', async () => {
    mockSources = {
      starting_occupancy: 'str_forecast',
      starting_adr: 'str_forecast',
      revenue_seed_from_str_forecast: 'str_forecast',
    };
    mockFieldOverrides = {
      revenue_seed_from_str_forecast: { value: true, note: 'STR market rates enabled from the Market tab' },
      starting_occupancy: { value: 0.692, note: STR_MARKET_OVERRIDE_NOTE },
      starting_adr: { value: 295, note: STR_MARKET_OVERRIDE_NOTE },
      mgmt_fee_pct: { value: 0.03, note: 'Analyst' }, // unrelated — must survive
    };
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByTestId('str-basis-chip')).toHaveTextContent('Active basis: Market / STR');
    expect(screen.queryByTestId('str-basis-unavailable')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Revert' }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [, body] = updateSpy.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides).toEqual({ mgmt_fee_pct: { value: 0.03, note: 'Analyst' } });
    expect(engineRunSpy).toHaveBeenCalled(); // re-modeled
  });

  it('shows the honest "STR rates unavailable — using T-12 base" state on str_forecast_unavailable', () => {
    mockSources = {
      revenue_seed_from_str_forecast: 'str_forecast_unavailable',
      starting_occupancy: 't12_actual',
      starting_adr: 't12_actual',
    };
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByTestId('str-basis-unavailable')).toHaveTextContent('STR rates unavailable — using T-12 base');
    expect(screen.queryByTestId('str-basis-chip')).not.toBeInTheDocument();
  });

  it('renders no basis chip for analyst / seed sources', () => {
    mockSources = { starting_occupancy: 'analyst_override', starting_adr: 'seed' };
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.queryByTestId('str-basis-chip')).not.toBeInTheDocument();
    expect(screen.queryByTestId('str-basis-unavailable')).not.toBeInTheDocument();
  });
});

// FON-67 — ``noi_override_by_year`` pins the operating NOI path the worker
// feeds the debt + returns engines, so operating-assumption edits don't move
// NOI while it is set. The section must SAY so and offer a clear.
describe('Financials · Projections — NOI pin notice (FON-67 reconciliation lever)', () => {
  const NOTICE =
    "NOI pinned to an analyst schedule — operating assumption edits won't move NOI until the pin is cleared";

  it('renders no notice when the deal carries no pin', () => {
    mockFieldOverrides = { mgmt_fee_pct: { value: 0.03, note: 'Analyst' } };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(screen.queryByTestId('noi-pin-notice')).not.toBeInTheDocument();
  });

  it('shows the notice for a {value: [...]} pin; Clear pin confirms, deletes ONLY the pin, and re-runs', async () => {
    mockFieldOverrides = {
      noi_override_by_year: { value: [4_100_000, 4_300_000, 4_500_000], note: "Sam's model NOI" },
      mgmt_fee_pct: { value: 0.03, note: 'Analyst' }, // unrelated — must survive
    };
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    const notice = screen.getByTestId('noi-pin-notice');
    expect(notice).toHaveTextContent(NOTICE);
    expect(notice).not.toHaveTextContent('Terminal NOI is also pinned');

    fireEvent.click(screen.getByRole('button', { name: 'Clear pin' }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [, body] = updateSpy.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides).toEqual({ mgmt_fee_pct: { value: 0.03, note: 'Analyst' } });
    expect(refreshDealSpy).toHaveBeenCalled();
    expect(engineRunSpy).toHaveBeenCalled(); // re-modeled so NOI follows the assumptions again
    confirmSpy.mockRestore();
  });

  it('says when terminal NOI is also pinned and clears both overrides together', async () => {
    mockFieldOverrides = {
      noi_override_by_year: [4_100_000, 4_300_000], // legacy raw-list shape
      terminal_noi_override: { value: 4_650_000, note: 'Reversion NOI' },
      exit_cap_rate: { value: 0.07, note: 'Analyst' },
    };
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByTestId('noi-pin-notice')).toHaveTextContent('Terminal NOI is also pinned');

    fireEvent.click(screen.getByRole('button', { name: 'Clear pin' }));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const [, body] = updateSpy.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(body.field_overrides).toEqual({ exit_cap_rate: { value: 0.07, note: 'Analyst' } });
    confirmSpy.mockRestore();
  });

  it('cancelling the confirm leaves the pin untouched (no PATCH, no re-run)', async () => {
    mockFieldOverrides = { noi_override_by_year: { value: [4_100_000], note: 'pin' } };
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    fireEvent.click(screen.getByRole('button', { name: 'Clear pin' }));
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    await act(async () => {
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(updateSpy).not.toHaveBeenCalled();
    expect(engineRunSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('noi-pin-notice')).toBeInTheDocument();
    confirmSpy.mockRestore();
  });

  it('an empty list is not a pin', () => {
    mockFieldOverrides = { noi_override_by_year: { value: [], note: 'cleared' } };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(screen.queryByTestId('noi-pin-notice')).not.toBeInTheDocument();
  });

  // Phase 4.4 — the pin is now a typed refusal: the worker's `pin_active`
  // code on either pin key is read FIRST, with the field_overrides
  // inspection as the fallback. Both paths render the identical notice.
  it('a worker `pin_active` code raises the same notice with NO local override present', () => {
    mockFieldOverrides = {}; // nothing in field_overrides at all
    mockReasons = { noi_override_by_year: 'pin_active' };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const notice = screen.getByTestId('noi-pin-notice');
    expect(notice).toHaveTextContent(NOTICE);
    expect(notice).not.toHaveTextContent('Terminal NOI is also pinned');
    expect(screen.getByRole('button', { name: 'Clear pin' })).toBeInTheDocument();
  });

  it('a worker `pin_active` code on the terminal key adds the terminal sentence', () => {
    mockFieldOverrides = { noi_override_by_year: { value: [4_100_000], note: 'pin' } };
    mockReasons = { terminal_noi_override: 'pin_active' };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(screen.getByTestId('noi-pin-notice')).toHaveTextContent('Terminal NOI is also pinned');
  });

  it('with the code ABSENT the local field_overrides check is unchanged', () => {
    mockReasons = {}; // every worker build today
    mockFieldOverrides = { noi_override_by_year: { value: [4_100_000], note: 'pin' } };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(screen.getByTestId('noi-pin-notice')).toHaveTextContent(NOTICE);
  });

  it('an unrelated reason code on a pin key does NOT raise the notice', () => {
    mockFieldOverrides = {};
    mockReasons = { noi_override_by_year: 'needs_review' };
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(screen.queryByTestId('noi-pin-notice')).not.toBeInTheDocument();
  });
});

// ── FON-41 / FON-59 #3 — the editable Stabilization Year ─────────────
// Sam: "Financials → Projections should own an explicit editable Stabilization
// Year." It writes the deal's field_overrides and re-runs the WHOLE model (a
// single-engine re-run would re-fragment the canonical snapshot), and it goes
// through the shared inline-edit contract: Esc / click-away discard, and
// re-saving the seeded value unchanged writes nothing.
describe('Financials · Projections — the Stabilization Year', () => {
  const withBlock = (source: 'fondok_derived' | 'analyst_override' = 'fondok_derived') => ({
    ...OUTPUTS,
    engines: {
      ...OUTPUTS.engines,
      revenue: {
        ...OUTPUTS.engines.revenue,
        outputs: {
          ...(OUTPUTS.engines.revenue as { outputs: Record<string, unknown> }).outputs,
          projection_calendar_years: [2025, 2026, 2027],
          projection_start_year: 2025,
        },
      },
      expense: {
        ...OUTPUTS.engines.expense,
        outputs: {
          ...(OUTPUTS.engines.expense as { outputs: Record<string, unknown> }).outputs,
          stabilization: {
            stabilized_year_index: 1,
            stabilized_year: 2,
            source,
            signal: 'occupancy',
            derived_year: 2,
            stabilized_occupancy: 0.75,
            stabilized_adr: 300,
            stabilized_revenue: 12_500_000,
            stabilized_noi_before_reserve: 4_035_000,
            stabilized_cash_noi: 3_535_000,
            stabilized_noi_margin: 4_035_000 / 12_500_000,
          },
        },
      },
    },
  }) as unknown as EngineOutputsResponse;

  it('shows the seeded year with its calendar year and the Default badge', () => {
    OUTPUTS_OVERRIDE = withBlock();
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByTestId('stabilization-year-value')).toHaveTextContent('Year 2 — 2026');
    expect(screen.getByTestId('stabilization-year-badge')).toHaveTextContent('Default — confirm');
    // …and the selected column carries the STABILIZED badge.
    expect(screen.getByTestId('stabilized-badge')).toBeInTheDocument();
    OUTPUTS_OVERRIDE = undefined;
  });

  it('a change PATCHes field_overrides.stabilization_year and re-runs the whole model', async () => {
    OUTPUTS_OVERRIDE = withBlock();
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    fireEvent.click(screen.getByTestId('stabilization-year-value'));
    const input = screen.getByLabelText('Stabilization Year') as HTMLInputElement;
    expect(input.value).toBe('2');
    fireEvent.change(input, { target: { value: '3' } });
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    });

    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const body = updateSpy.mock.calls[0][1] as { field_overrides: Record<string, unknown> };
    // FON-74 — the Stabilization Year is DISPLAY-ONLY (it selects which
    // projection year the stabilized figures are read from and moves no
    // return), so it needs no justification — and the software-authored note it
    // used to carry is gone. A blank note, never an invented one.
    expect(body.field_overrides.stabilization_year).toEqual({ value: 3 });
    expect(JSON.stringify(body)).not.toContain('note');
    expect(engineRunSpy).toHaveBeenCalled();
    OUTPUTS_OVERRIDE = undefined;
  });

  it('FON-74 — and it never asks the analyst to justify it', async () => {
    OUTPUTS_OVERRIDE = withBlock();
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    fireEvent.click(screen.getByTestId('stabilization-year-value'));
    fireEvent.change(screen.getByLabelText('Stabilization Year'), { target: { value: '3' } });
    expect(screen.queryByLabelText('Override justification')).not.toBeInTheDocument();
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    OUTPUTS_OVERRIDE = undefined;
  });

  it('re-saving the seeded year unchanged writes nothing — it stays Default', async () => {
    OUTPUTS_OVERRIDE = withBlock();
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    fireEvent.click(screen.getByTestId('stabilization-year-value'));
    // No edit at all — just Save, the way an analyst confirms a seed.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    });

    expect(updateSpy).not.toHaveBeenCalled();
    expect(engineRunSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('stabilization-year-badge')).toHaveTextContent('Default — confirm');
    OUTPUTS_OVERRIDE = undefined;
  });

  it('Escape discards without writing', async () => {
    OUTPUTS_OVERRIDE = withBlock();
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    fireEvent.click(screen.getByTestId('stabilization-year-value'));
    const input = screen.getByLabelText('Stabilization Year') as HTMLInputElement;
    fireEvent.change(input, { target: { value: '3' } });
    fireEvent.keyDown(input, { key: 'Escape' });

    expect(updateSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('stabilization-year-value')).toHaveTextContent('Year 2 — 2026');
    OUTPUTS_OVERRIDE = undefined;
  });

  it('an analyst-selected year is badged as an override, not as derived', () => {
    OUTPUTS_OVERRIDE = withBlock('analyst_override');
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(screen.getByTestId('stabilization-year-badge')).toHaveTextContent('Analyst override');
    OUTPUTS_OVERRIDE = undefined;
  });

  it('with no published block the editor is an inert dash — never a guessed year', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const value = screen.getByTestId('stabilization-year-value');
    expect(value).toHaveTextContent('—');
    expect(value).toBeDisabled();
    expect(screen.queryByTestId('stabilization-year-badge')).not.toBeInTheDocument();
    expect(screen.queryByTestId('stabilized-badge')).not.toBeInTheDocument();
  });
});

// ════════════════════════════════════════════════════════════════════
// FON-41 — external tester round on the P&L tab (Linear FON-41).
//
//  E-014  every projected column header names its calendar year
//         ("Year 2 · 2026"), the base column is explicit and names the
//         primary statement's period, and a one-line note says where the
//         acquisition date is set when there is none.
//  E-015  every projected Occupancy / ADR / RevPAR / revenue cell opens the
//         lineage drawer for ITS engine field; an uncovered cell says "No
//         trace available for this cell." — never a fabricated formula.
//  E-025  no scientific notation anywhere; ADR / RevPAR to the cent; every
//         amount cell carries its full value in a title.
//  E-026  the statement sits in a persistent, discoverable scroll container.
//  E-030  the waterfall runs GOP → Management Fees → EBITDA → Fixed Charges
//         → NOI (before FF&E reserve) → FF&E Reserve → Cash NOI (after).
//  R-068  "% Rev" beside every amount — % of Total Revenue, departmental
//         expenses as % of their own department (USALI) — behind a "Show %"
//         switch that defaults on.
// ════════════════════════════════════════════════════════════════════

type RevPatch = Partial<ReturnType<typeof revYear>>;
type ExpPatch = Partial<ReturnType<typeof expYear>>;

/** The base fixture with the projection calendar and/or a per-year patch. */
function withProjection(patch: { calendar?: number[]; rev?: RevPatch; exp?: ExpPatch } = {}): EngineOutputsResponse {
  const base = OUTPUTS as unknown as {
    engines: Record<string, { outputs: Record<string, unknown> } & Record<string, unknown>>;
  };
  return {
    ...(OUTPUTS as unknown as Record<string, unknown>),
    engines: {
      ...base.engines,
      revenue: {
        ...base.engines.revenue,
        outputs: {
          years: [2025, 2026, 2027].map((y) => ({ ...revYear(y), ...(patch.rev ?? {}) })),
          projection_calendar_years: patch.calendar ?? [],
          projection_start_year: patch.calendar?.[0] ?? null,
        },
      },
      expense: {
        ...base.engines.expense,
        outputs: {
          years: [2025, 2026, 2027].map((y) => ({ ...expYear(y), ...(patch.exp ?? {}) })),
        },
      },
    },
  } as unknown as EngineOutputsResponse;
}

const header = (i: number) => screen.getByTestId(`projection-col-header-${i}`);
const basis = (i: number) => screen.getByTestId(`projection-col-basis-${i}`);
/** The statement row whose label cell reads exactly `label` (nth match). */
function rowLabelled(label: string, nth = 0): HTMLTableRowElement {
  const table = screen.getByTestId('projections-table');
  const rows = Array.from(table.querySelectorAll<HTMLTableRowElement>('tbody tr')).filter(
    (r) => (r.cells[0]?.textContent ?? '').trim() === label,
  );
  const row = rows[nth];
  if (!row) throw new Error(`no statement row labelled "${label}" (#${nth})`);
  return row;
}
/** The order of statement rows, by their label cell. */
function rowLabels(): string[] {
  const table = screen.getByTestId('projections-table');
  return Array.from(table.querySelectorAll<HTMLTableRowElement>('tbody tr')).map(
    (r) => (r.cells[0]?.textContent ?? '').trim(),
  );
}

describe('P&L · Future P&L — column headings carry the calendar year (E-014)', () => {
  afterEach(() => { OUTPUTS_OVERRIDE = undefined; });

  it('with an acquisition close date every header reads "Year N · <calendar>", and the base column names its period', () => {
    OUTPUTS_OVERRIDE = withProjection({ calendar: [2026, 2027, 2028] });
    mockFieldOverrides = { acquisition_close_date: { value: '2025-11-15', note: 'PSA' } };
    render(<ProjectionsSection dealId="deal-uuid-1" basePeriodLabel="T12 Mar 2025" />);

    expect(header(0)).toHaveTextContent('Base year (Year 1) · 2026');
    expect(header(1)).toHaveTextContent('Year 2 · 2027');
    expect(header(2)).toHaveTextContent('Year 3 · 2028');
    expect(screen.getByTestId('projection-col-header-exit')).toHaveTextContent('Exit Year · 2029');
    // The base column's basis line is the primary statement's period, exactly
    // as Historical P&L heads that column; projected columns say so.
    expect(basis(0)).toHaveTextContent('T12 Mar 2025');
    expect(basis(1)).toHaveTextContent(PROJECTED_COLUMN_BASIS);
    expect(basis(2)).toHaveTextContent(PROJECTED_COLUMN_BASIS);
    // No "set the acquisition date" note when there IS one; the help line always.
    expect(screen.queryByTestId('projection-no-close-date-note')).toBeNull();
    expect(screen.getByTestId('projection-calendar-help')).toHaveTextContent(
      /acquisition close date sets the year mapping/i,
    );
  });

  it('without a close date the headers are bare "Year N", the base basis is a dash, and the note says where to set it', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(header(0).textContent?.trim()).toBe('Base year (Year 1)');
    expect(header(1).textContent?.trim()).toBe('Year 2');
    expect(header(2).textContent?.trim()).toBe('Year 3');
    // No period exposed → a dash, never an inferred one.
    expect(basis(0).textContent?.trim()).toBe('—');
    expect(screen.getByTestId('projection-no-close-date-note')).toHaveTextContent(NO_CLOSE_DATE_NOTE);
    // Never the wall-clock year.
    expect(screen.queryByText(String(new Date().getFullYear()))).toBeNull();
  });

  it('a blank period label still renders the plain base-year heading (never "undefined")', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" basePeriodLabel="   " />);
    expect(header(0).textContent?.trim()).toBe('Base year (Year 1)');
    expect(basis(0).textContent?.trim()).toBe('—');
    expect(document.body.textContent).not.toContain('undefined');
  });
});

describe('P&L · Future P&L — number rendering (E-025)', () => {
  afterEach(() => { OUTPUTS_OVERRIDE = undefined; });

  it('never renders scientific notation — a $1.2B total prints with thousands separators', () => {
    OUTPUTS_OVERRIDE = withProjection({
      rev: { total_revenue: 1_200_000_000, rooms_revenue: 1_000_000_000 },
      exp: { total_revenue: 1_200_000_000 },
    });
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const table = screen.getByTestId('projections-table');
    expect(table.textContent).not.toMatch(/\d[eE][+-]?\d/);
    const total = rowLabelled('Total Revenue');
    expect(within(total).getAllByText('$1,200,000,000')).toHaveLength(3);
    // …and the title carries the full value with cents.
    expect(total.cells[2].getAttribute('title')).toBe('$1,200,000,000.00');
  });

  it('ADR and RevPAR print to the cent', () => {
    OUTPUTS_OVERRIDE = withProjection({ rev: { adr: 287.456, revpar: 215.592 } });
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(within(rowLabelled('Average Rate')).getAllByText('$287.46')).toHaveLength(3);
    expect(within(rowLabelled('RevPAR')).getAllByText('$215.59')).toHaveLength(3);
    // The fixture's round $300 ADR still shows its cents.
    cleanup();
    OUTPUTS_OVERRIDE = undefined;
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    expect(within(rowLabelled('Average Rate')).getAllByText('$300.00')).toHaveLength(3);
    expect(within(rowLabelled('RevPAR')).getAllByText('$225.00')).toHaveLength(3);
  });

  it('the statement sits in a persistent, discoverable horizontal scroll container (E-026)', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const scroller = screen.getByTestId('projections-hscroll-scroller');
    expect(scroller.className).toContain('fondok-hscroll');
    expect(scroller.contains(screen.getByTestId('projections-table'))).toBe(true);
    // Every year column header opts into the "N more years →" count.
    expect(scroller.querySelectorAll('[data-year-col]').length).toBe(4); // 3 modelled + Exit
  });
});

describe('P&L · Future P&L — ratios beside every amount (R-068)', () => {
  it('shows % of Total Revenue, departmental expenses as % of their own department, computed from the engine years', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    // Rooms revenue 10,000,000 ÷ Total 12,500,000 = 80.0% (× 3 years).
    const roomsRevenue = rowLabelled('Rooms', 0);
    expect(within(roomsRevenue).getAllByText('80.0%')).toHaveLength(3);
    expect(roomsRevenue.querySelectorAll('[data-ratio-basis="total_revenue"]')).toHaveLength(3);
    expect(within(rowLabelled('Total Revenue')).getAllByText('100.0%')).toHaveLength(3);
    // GOP 5,310,000 ÷ 12,500,000 = 42.5%.
    expect(within(rowLabelled('Gross Operating Profit')).getAllByText('42.5%')).toHaveLength(3);

    // Rooms DEPARTMENTAL expense 2,500,000 ÷ Rooms revenue 10,000,000 = 25.0%
    // — the USALI departmental ratio, marked "dept", NOT 20.0% of total.
    const roomsDept = rowLabelled('Rooms', 1);
    const deptCells = Array.from(roomsDept.querySelectorAll<HTMLElement>('[data-ratio-basis="department"]'));
    expect(deptCells).toHaveLength(3);
    for (const c of deptCells) {
      expect(c.textContent).toContain('25.0%');
      expect(c.textContent).toContain('dept');
      expect(c.getAttribute('title')).toMatch(/of Rooms revenue/);
    }
    expect(within(roomsDept).queryByText('20.0%')).toBeNull();
    // F&B dept 1,500,000 ÷ F&B revenue 2,000,000 = 75.0%.
    const fbDept = rowLabelled('Food & Beverage', 1);
    expect(Array.from(fbDept.querySelectorAll('[data-ratio-basis="department"]')).map((c) => c.textContent)).toEqual(
      ['75.0%dept', '75.0%dept', '75.0%dept'],
    );
  });

  it('the "Show %" switch defaults on and hides / restores the % Rev sub-column', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const toggle = screen.getByTestId('projection-show-pct') as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    expect(screen.getAllByText('% Rev').length).toBeGreaterThan(0);
    expect(document.querySelectorAll('[data-ratio-basis]').length).toBeGreaterThan(0);

    fireEvent.click(toggle);
    expect(toggle.checked).toBe(false);
    expect(screen.queryAllByText('% Rev')).toHaveLength(0);
    expect(document.querySelectorAll('[data-ratio-basis]')).toHaveLength(0);
    // The amounts are untouched by the view switch.
    expect(within(rowLabelled('Total Revenue')).getAllByText('$12,500,000')).toHaveLength(3);

    fireEvent.click(toggle);
    expect(screen.getAllByText('% Rev').length).toBeGreaterThan(0);
  });
});

describe('P&L · Future P&L — the NOI waterfall is complete and in order (E-030)', () => {
  it('runs GOP → Management Fees → EBITDA → Fixed Charges → NOI (before FF&E reserve) → FF&E Reserve → Cash NOI (after)', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    const labels = rowLabels();
    const order = [
      'Gross Operating Profit',
      'Management Fees',
      'EBITDA',
      'Fixed Charges',
      'Property Taxes',
      'Insurance',
      'Total Fixed Charges',
      'Net Operating Income',
      NOI_BEFORE_RESERVE_LABEL,
      'FF&E Reserve',
      CASH_NOI_LABEL,
    ];
    const idx = order.map((l) => labels.indexOf(l));
    expect(idx.every((i) => i >= 0), `missing rows: ${order.filter((_, k) => idx[k] < 0).join(', ')}`).toBe(true);
    for (let k = 1; k < idx.length; k++) expect(idx[k]).toBeGreaterThan(idx[k - 1]);

    // The figures are the engine's: gop 5,310,000 − mgmt 375,000 = EBITDA
    // 4,935,000; − fixed 900,000 = 4,035,000 before reserve; − FF&E 500,000 =
    // 3,535,000 Cash NOI.
    expect(within(rowLabelled('EBITDA')).getAllByText('$4,935,000')).toHaveLength(3);
    expect(within(rowLabelled('Total Fixed Charges')).getAllByText('$900,000')).toHaveLength(3);
    expect(within(rowLabelled(NOI_BEFORE_RESERVE_LABEL)).getAllByText('$4,035,000')).toHaveLength(3);
    expect(within(rowLabelled('FF&E Reserve')).getAllByText('$500,000')).toHaveLength(3);
    expect(within(rowLabelled(CASH_NOI_LABEL)).getAllByText('$3,535,000')).toHaveLength(3);
    // No element is labelled with the bare, unqualified word "NOI".
    const bare = screen.queryAllByText((_c, el) => (el?.textContent ?? '').trim() === 'NOI');
    expect(bare).toEqual([]);
  });
});

describe('P&L · Future P&L — click-through to the lineage drawer (E-015)', () => {
  const seen: LineageOpenDetail[] = [];
  const onOpen = (e: Event) => { seen.push((e as CustomEvent<LineageOpenDetail>).detail); };
  beforeEach(() => {
    seen.length = 0;
    clearLineageCache();
    lineageSpy.mockReset();
    lineageSpy.mockResolvedValue(null);
    window.addEventListener(LINEAGE_OPEN_EVENT, onOpen);
  });
  afterEach(() => { window.removeEventListener(LINEAGE_OPEN_EVENT, onOpen); });

  it('a projected ADR cell asks the drawer for revenue.years[i].adr — and every metric resolves to its own field', () => {
    render(<ProjectionsSection dealId="deal-uuid-1" />);
    fireEvent.click(screen.getByTestId('lineage-cell-revenue.years[1].adr'));

    expect(seen).toHaveLength(1);
    expect(seen[0].dealId).toBe('deal-uuid-1');
    expect(seen[0].rootId).toEqual(['kpi:revenue.years[1].adr', 'engine:revenue.years[1].adr']);
    expect(seen[0].title).toBe('Average Rate — Year 2');
    expect(seen[0].subtitle).toContain('revenue.years[1].adr');
    expect(seen[0].emptyMessage).toBe(NO_TRACE_MESSAGE);

    fireEvent.click(screen.getByTestId('lineage-cell-revenue.years[2].occupancy'));
    fireEvent.click(screen.getByTestId('lineage-cell-revenue.years[1].revpar'));
    fireEvent.click(screen.getByTestId('lineage-cell-revenue.years[2].rooms_revenue'));
    fireEvent.click(screen.getByTestId('lineage-cell-revenue.years[1].total_revenue'));
    fireEvent.click(screen.getByTestId('lineage-cell-expense.years[1].noi_institutional'));
    expect(seen.slice(1).map((d) => (d.rootId as string[])[1])).toEqual([
      'engine:revenue.years[2].occupancy',
      'engine:revenue.years[1].revpar',
      'engine:revenue.years[2].rooms_revenue',
      'engine:revenue.years[1].total_revenue',
      'engine:expense.years[1].noi_institutional',
    ]);
    // The column label travels with the request so the drawer says WHICH year.
    expect(seen[1].title).toBe('Occupancy — Year 3');
  });

  it('a cell the lineage record does not cover opens the drawer saying "No trace available for this cell." — no invented formula', async () => {
    render(
      <>
        <LineageDrawerHost />
        <ProjectionsSection dealId="deal-uuid-1" />
      </>,
    );
    fireEvent.click(screen.getByTestId('lineage-cell-revenue.years[1].adr'));

    const dialog = await screen.findByRole('dialog', { name: 'Value lineage' });
    expect(within(dialog).getByRole('heading', { level: 2 })).toHaveTextContent('Average Rate — Year 2');
    expect(within(dialog).getByTestId('lineage-subtitle')).toHaveTextContent('revenue.years[1].adr');
    await waitFor(() =>
      expect(within(dialog).getByTestId('lineage-empty')).toHaveTextContent(NO_TRACE_MESSAGE),
    );
    expect(lineageSpy).toHaveBeenCalledWith('deal-uuid-1');
    // Nothing that reads like a formula was made up for the empty state.
    expect(dialog.textContent).not.toMatch(/[=×÷]/);
  });

  it('a covered cell renders the recorded walk — the engine value, its inputs and the source page', async () => {
    const ROOT = 'engine:revenue.years[1].rooms_revenue';
    const node = (id: string, kind: string, label: string, value: number | null, unit: string | null, source: string | null, state: string | null, meta: Record<string, unknown> = {}) =>
      ({ id, kind, label, value, unit, concept: null, source, state, reason: null, meta });
    lineageSpy.mockResolvedValue({
      deal_id: 'deal-uuid-1', run_id: 'run-1', registry_version: 3, pipeline_version: '2026.10', generated_at: '2026-10-07T00:00:00Z',
      roots: [ROOT],
      nodes: [
        node(ROOT, 'engine_value', 'revenue.years[1].rooms_revenue', 10_000_000, 'USD', null, 'calculated'),
        node('assumption:starting_adr', 'assumption', 'Starting ADR', 300, 'USD', 't12_actual', 'document_sourced'),
        node('assumption:revpar_growth', 'assumption', 'RevPAR growth', 0.045, 'ratio', null, 'assumption'),
        node('page:doc-1:4', 'page', 'Statement of Operations', null, null, 't12_actual', 'document_sourced', { filename: 'T12.pdf', page: 4 }),
      ],
      edges: [
        { src: ROOT, dst: 'assumption:starting_adr', rel: 'computed_from', formula: 'rooms_revenue = occupancy × ADR × available rooms' },
        { src: ROOT, dst: 'assumption:revpar_growth', rel: 'computed_from', formula: null },
        { src: 'assumption:starting_adr', dst: 'page:doc-1:4', rel: 'located_on', formula: null },
      ],
      unresolved: [],
      stale: false,
    });
    render(
      <>
        <LineageDrawerHost />
        <ProjectionsSection dealId="deal-uuid-1" />
      </>,
    );
    fireEvent.click(screen.getByTestId('lineage-cell-revenue.years[1].rooms_revenue'));

    const dialog = await screen.findByRole('dialog', { name: 'Value lineage' });
    await within(dialog).findByTestId(`lineage-step-${ROOT}`);
    // Inputs (starting value, growth) and the source row, in walk order.
    const steps = Array.from(dialog.querySelectorAll('[data-testid^="lineage-step-"]')).map(
      (el) => el.getAttribute('data-testid'),
    );
    expect(steps).toEqual([
      `lineage-step-${ROOT}`,
      'lineage-step-assumption:starting_adr',
      'lineage-step-page:doc-1:4',
      'lineage-step-assumption:revpar_growth',
    ]);
    // The formula shown is the RECORDED one, verbatim.
    expect(within(dialog).getByText('rooms_revenue = occupancy × ADR × available rooms')).toBeInTheDocument();
    expect(within(dialog).getByText('T12.pdf')).toBeInTheDocument();
    expect(within(dialog).queryByText(NO_TRACE_MESSAGE)).toBeNull();
  });
});
