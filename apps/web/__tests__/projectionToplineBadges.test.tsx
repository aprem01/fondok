/**
 * FON-116 / FON-117 — the Future P&L on the topline (plan §4.2, §5.2
 * "projectionToplineBadges"): basis line, per-column basis chips, the two
 * reference rows, the growth banner and its labelled suggestion, the
 * calendar_unknown close-date entry (R2-I1), the Topline card's note-gated
 * saves (review I-6), "Clear legacy keys", the run-predates-v2 notice and the
 * no-revenue-basis entry (review I-5) — end to end through the deal PATCH.
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
    workerConfigured: true,
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
  const { withQuietWorker } = await import('./helpers/quietWorker');
  const actual = withQuietWorker(await vi.importActual<typeof import('@/lib/api')>('@/lib/api'));
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

import ProjectionsSection from '@/components/project/pl/ProjectionsSection';
import { ToplineStatusContext } from '@/lib/hooks/useToplineStatus';
import type { ToplineStatus } from '@/lib/api';
import { toplineStatus, unavailableGrowth } from './fixtures/toplineStatus';
import engineRun from './fixtures/engine/toplineRun.json';


// The engine's own records (apps/worker run_all_engines on the Kimpton
// documents fixture — see fixtures/engine/toplineRun.json): mpi_target already
// includes the ramp; occupancy / adr are after the Year-1 displacement.
const RUN = engineRun as unknown as { revenue: { topline_by_year: unknown[] }; topline_status: ToplineStatus };
const TOPLINE_BY_YEAR = RUN.revenue.topline_by_year;

function withRevenue(extra: Record<string, unknown>, top: Record<string, unknown> = {}): EngineOutputsResponse {
  const base = OUTPUTS as unknown as { engines: Record<string, { outputs: Record<string, unknown> }> };
  return {
    ...OUTPUTS,
    ...top,
    engines: {
      ...base.engines,
      revenue: { ...base.engines.revenue, outputs: { ...base.engines.revenue.outputs, ...extra } },
    },
  } as unknown as EngineOutputsResponse;
}

function renderWith(status: ToplineStatus | null) {
  return render(
    <ToplineStatusContext.Provider value={status}>
      <ProjectionsSection dealId="deal-uuid-1" />
    </ToplineStatusContext.Provider>,
  );
}

beforeEach(() => {
  cleanup();
  mockSources = {};
  mockReasons = {};
  mockFieldOverrides = {};
  OUTPUTS_OVERRIDE = withRevenue({ topline_by_year: TOPLINE_BY_YEAR, topline_methodology_version: 2 });
  updateSpy.mockClear();
  engineRunSpy.mockClear();
  refreshDealSpy.mockClear();
});

describe('Future P&L — the topline basis line and badges (plan §4.2)', () => {
  it('prints the basis line from topline_status', () => {
    renderWith(toplineStatus());
    expect(screen.getByTestId('topline-basis-line')).toHaveTextContent(
      'Benchmark: STR comp set TTM May 2025 · ANG-20250500-USD-E.xlsx',
    );
    expect(screen.getByTestId('topline-clause-ramp')).toHaveTextContent('Ramp 85/95/100 (default)');
    // Final review I-2 — the exit-year growth is on its own labelled line, not in the RevPAR field.
    expect(screen.getByTestId('topline-terminal-growth')).toHaveTextContent('Exit-year growth: +2.0%');
    expect((screen.getByTestId('assumption-panel-input-revpar_growth') as HTMLInputElement).value).toBe('');
  });

  it('every projected Occupancy / ADR column carries the benchmark year’s basis chip, titled with the engine chain (review I-1)', () => {
    renderWith(RUN.topline_status);
    expect(screen.getByTestId('topline-basis-occupancy-0')).toHaveAttribute('data-basis', 'forecast');
    const t0 = screen.getByTestId('topline-chip-occupancy-0').getAttribute('title') ?? '';
    // benchmark × stabilized penetration × ramp = pre-displacement, then displacement — the ramp once.
    expect(t0).toContain('Benchmark 74.6% × penetration 100 × ramp 85% = 63.4% before displacement; Year-1 displacement → 57.1%');
    expect(t0).not.toContain('MPI 85');
    // Year 2 ADR ran on your 98 with no ramp.
    expect(screen.getByTestId('topline-chip-adr-1').getAttribute('title')).toContain('penetration 98 (your Year 2 value — no ramp) = $308.18');
  });

  it('two reference rows: the benchmark per year and the T-12 with its period', () => {
    renderWith(toplineStatus());
    const bench = screen.getByTestId('topline-ref-benchmark');
    expect(bench).toHaveTextContent('Benchmark (competitive set)');
    expect(bench).toHaveTextContent('75.5% · $304.56');
    const t12 = screen.getByTestId('topline-ref-t12');
    expect(t12).toHaveTextContent('T-12 actual (reference) · T-12 Mar 2025');
    expect(within(t12).getAllByText(/83\.3% · \$236\.09/)).toHaveLength(3);
  });

  it('an older worker (no status) prints none of it', () => {
    renderWith(null);
    expect(screen.queryByTestId('topline-basis-line')).toBeNull();
    expect(screen.queryByTestId('topline-ref-benchmark')).toBeNull();
    expect(screen.queryByTestId('topline-card')).toBeNull();
    // …and the Stabilization card stands alone, as before.
    expect(screen.getByText('Stabilization')).toBeInTheDocument();
  });
});

describe('Future P&L — growth unavailable and the labelled suggestion', () => {
  it('shows the banner; applying the suggestion PATCHes revpar_growth with the prefilled note and re-runs', async () => {
    renderWith(toplineStatus(unavailableGrowth()));
    expect(screen.getByTestId('topline-growth-unavailable')).toHaveTextContent('No market forecast — set growth manually');
    fireEvent.click(screen.getByTestId('topline-suggestion-apply'));
    await act(async () => {
      fireEvent.click(screen.getByTestId('topline-suggestion-save'));
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const body = updateSpy.mock.calls[0][1] as { field_overrides: Record<string, unknown> };
    expect(body.field_overrides.revpar_growth).toEqual({
      value: 0.045,
      note: 'Platform suggestion 4.5% — no market forecast on file',
    });
    expect(engineRunSpy).toHaveBeenCalled();
  });

  it('calendar_unknown: the close date saves acquisition_close_date with its note (the shared save path)', async () => {
    renderWith(toplineStatus({ ...unavailableGrowth(), calendar_known: false, calendar_unknown: true }));
    fireEvent.click(screen.getByTestId('topline-close-date'));
    fireEvent.change(screen.getByLabelText('Acquisition close date'), { target: { value: '2025-12-31' } });
    const entry = screen.getByTestId('topline-close-date-entry');
    fireEvent.change(within(entry).getByLabelText('Override justification'), { target: { value: 'PSA' } });
    await act(async () => {
      fireEvent.click(within(entry).getByLabelText('Save'));
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const body = updateSpy.mock.calls[0][1] as { field_overrides: Record<string, unknown> };
    expect(body.field_overrides.acquisition_close_date).toEqual({ value: '2025-12-31', note: 'PSA' });
    expect(engineRunSpy).toHaveBeenCalled();
  });
});

describe('Future P&L — the Topline card saves end to end (review I-6)', () => {
  it('the stabilized year moved into the Topline card and still saves with its note', async () => {
    renderWith(toplineStatus());
    const card = screen.getByTestId('topline-card');
    expect(within(card).getByText('Stabilization Year')).toBeInTheDocument();
    fireEvent.change(within(card).getByLabelText('Benchmark level'), { target: { value: 'market' } });
    fireEvent.change(within(card).getByLabelText('Benchmark level — override justification'), { target: { value: 'CBRE only' } });
    await act(async () => {
      fireEvent.click(within(card).getByTestId('topline-level-save'));
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const body = updateSpy.mock.calls[0][1] as { field_overrides: Record<string, unknown> };
    expect(body.field_overrides.topline_benchmark_level).toEqual({ value: 'market', note: 'CBRE only' });
  });

  it('"Clear legacy keys" nulls the flagged keys in one PATCH and keeps every other override', async () => {
    mockFieldOverrides = {
      revenue_seed_from_str_forecast: { value: true, note: 'x' },
      index_market_adr_growth: { value: 0.03, note: 'y' },
      mgmt_fee_pct: { value: 0.03, note: 'Analyst' },
    };
    renderWith(
      toplineStatus({
        legacy_flags: [
          { key: 'revenue_seed_from_str_forecast', value: true, treatment: 'ignored', reason: 'legacy_key_translated', detail: 'ignored' },
          { key: 'index_market_adr_growth', value: 0.03, treatment: 'read_as_all_years_growth', reason: 'legacy_key_translated', detail: 'read' },
        ],
      }),
    );
    await act(async () => {
      fireEvent.click(screen.getByTestId('topline-clear-legacy'));
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const body = updateSpy.mock.calls[0][1] as { field_overrides: Record<string, unknown> };
    expect(body.field_overrides).toEqual({ mgmt_fee_pct: { value: 0.03, note: 'Analyst' } });
  });
});

describe('Future P&L — migration notice and the no-revenue-basis state', () => {
  it('a run with no topline method version (predates v2) gets the one-time notice', () => {
    OUTPUTS_OVERRIDE = withRevenue({});
    renderWith(toplineStatus());
    expect(screen.getByTestId('topline-migration-notice')).toHaveTextContent(
      'The topline method changed; this run predates it — re-run, then compare runs.',
    );
  });

  it('a v2 run gets no notice', () => {
    renderWith(toplineStatus());
    expect(screen.queryByTestId('topline-migration-notice')).toBeNull();
  });

  it('no_revenue_basis: the worker’s message and the Year-1 entry, which saves both rates with one note', async () => {
    const message =
      'The deal has no Year-1 occupancy or Year-1 ADR basis — no T-12, no STR or market benchmark and no analyst value sets it. Upload a T-12 or STR report, or enter Year-1 occupancy and ADR on the Future P&L, and run the model again — the model never substitutes a placeholder rate.';
    OUTPUTS_OVERRIDE = {
      deal_id: 'deal-uuid-1',
      engines: {},
      failures: [{ engine: 'revenue', reason: 'no_revenue_basis', message, missing: ['starting_adr', 'starting_occupancy'] }],
    } as unknown as EngineOutputsResponse;
    renderWith(toplineStatus({ revenue_basis: { state: 'missing', missing: ['starting_adr', 'starting_occupancy'], message } }));
    expect(screen.getByTestId('revenue-basis-message')).toHaveTextContent('Upload a T-12 or STR report, or enter Year-1 occupancy and ADR');
    expect(screen.queryByText('No projections yet')).toBeNull();
    fireEvent.change(screen.getByLabelText('Year-1 occupancy (%)'), { target: { value: '70' } });
    fireEvent.change(screen.getByLabelText('Year-1 ADR ($)'), { target: { value: '250' } });
    fireEvent.change(screen.getByLabelText('Year-1 occupancy and ADR — override justification'), { target: { value: 'Broker' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('revenue-basis-save'));
    });
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());
    const body = updateSpy.mock.calls[0][1] as { field_overrides: Record<string, unknown> };
    expect(body.field_overrides.starting_occupancy).toEqual({ value: 0.7, note: 'Broker' });
    expect(body.field_overrides.starting_adr).toEqual({ value: 250, note: 'Broker' });
    expect(engineRunSpy).toHaveBeenCalled();
  });
});
