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
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, act } from '@testing-library/react';
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
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as Parameters<typeof updateSpy>)) },
    },
  };
});

vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';
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
    expect(screen.getByText('Capture — Base Year (Year 1)')).toBeInTheDocument();
    expect(screen.getByText('Capture — Year 2')).toBeInTheDocument();
    expect(screen.getByText('Capture — Year 3+')).toBeInTheDocument();
    expect(
      screen.getByText(/Base Year \(Year 1\) is the model's first operating year/),
    ).toBeInTheDocument();
    // Every capture label names a column header the statement actually renders.
    for (const name of ['Base Year \\(Year 1\\)', 'Year 2', 'Year 3']) {
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
    const ref = screen.getByText('Investment →');
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

  it('shows the seeded year with its calendar year and the Fondok-derived badge', () => {
    OUTPUTS_OVERRIDE = withBlock();
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    expect(screen.getByTestId('stabilization-year-value')).toHaveTextContent('Year 2 — 2026');
    expect(screen.getByTestId('stabilization-year-badge')).toHaveTextContent('Fondok-derived — confirm');
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

  it('re-saving the seeded year unchanged writes nothing — it stays Fondok-derived', async () => {
    OUTPUTS_OVERRIDE = withBlock();
    render(<ProjectionsSection dealId="deal-uuid-1" />);

    fireEvent.click(screen.getByTestId('stabilization-year-value'));
    // No edit at all — just Save, the way an analyst confirms a seed.
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    });

    expect(updateSpy).not.toHaveBeenCalled();
    expect(engineRunSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId('stabilization-year-badge')).toHaveTextContent('Fondok-derived — confirm');
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
