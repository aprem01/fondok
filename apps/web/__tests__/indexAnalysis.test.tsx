/**
 * Index Analysis — what the forecast columns are allowed to claim.
 *
 * Two Sam findings, both about a number that looked modelled and was not:
 *
 *  1. FON-60 §5 — `RGI Growth (pts)`. STR publishes the penetration indices
 *     for the trailing-twelve-month window only, so the RGI series is held
 *     FLAT across every forecast column. The growth row therefore read "+0.0"
 *     nine times, which is an assumption of zero index movement that nobody
 *     made. The row is gone; the MPI / ARI / RGI LEVEL rows stay.
 *
 *  2. FON-61 §3 — the first forecast year's growth. The 2024 column is a
 *     fiscal-year actual from the multi-year P&L baseline; 2025 is the revenue
 *     engine's forecast, grown off a Base Year that is a trailing-twelve-month
 *     window and is never shown in this table. Dividing one by the other is a
 *     growth rate across two period bases. That one cell is N/A with the
 *     mismatch named on hover; every later year is forecast-over-forecast and
 *     is untouched.
 *
 *  3. FON-60 §4 — the forecast band says WHOSE forecast it is. Absent a CBRE
 *     Horizons report the series is Fondok-derived, which used to be admitted
 *     only in footnote 3.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, within, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, HistoricalBaselineResponse } from '@/lib/api';

const DEAL = '11111111-1111-1111-1111-111111111111';

// The market payload the section fetches. Mutable per test.
let mockMarket: Record<string, unknown> | null = null;
// FON-61 / E-028 — the methodology payload; null keeps the panel hidden.
let mockIndexMethodology: Record<string, unknown> | null = null;
const updateDeal = vi.fn(async (..._args: unknown[]) => ({}));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      market: {
        ...actual.api.market,
        data: vi.fn(async () => mockMarket),
        indexMethodology: vi.fn(async () => mockIndexMethodology),
      },
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateDeal(...a) },
    },
  };
});

let mockOverrides: Record<string, unknown> = {};
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({ deal: { id: DEAL, keys: 132, field_overrides: mockOverrides }, refresh: vi.fn() }),
}));

vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), status: 'idle' }),
}));

// Worker source tags (assumption_sources), keyed by assumption. Empty = none.
let mockSources: Record<string, unknown> = {};
vi.mock('@/lib/hooks/useDealProvenance', () => ({
  useSource: (key: string) => (mockSources[key] as unknown) ?? null,
  useProvenanceState: () => ({ ready: true, settled: true }),
}));

let mockOutputs: EngineOutputsResponse | null = null;
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>(
    '@/lib/hooks/useEngineOutputs',
  );
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: mockOutputs,
      previous: null,
      loading: false,
      lastRunAt: null,
      refresh: vi.fn(),
    }),
  };
});

let mockBaseline: HistoricalBaselineResponse | null = null;
vi.mock('@/lib/hooks/useHistoricalBaseline', () => ({
  useHistoricalBaseline: () => ({ baseline: mockBaseline, loading: false }),
}));

import IndexAnalysisSection from '@/components/project/pl/IndexAnalysisSection';
import { buildReconciliation, type ReconciliationInput } from '@/components/project/pl/IndexMethodology';

/** Revenue-engine years: a Base Year (TTM) + eight forecast years at a steady
 *  +4% ADR / flat occupancy, so every forecast-over-forecast growth cell is a
 *  real, non-zero number. */
function revenueYears() {
  const years = [];
  for (let i = 0; i < 9; i++) {
    const occupancy = 0.716;
    const adr = 288 * 1.04 ** i;
    years.push({ year: i + 1, occupancy, adr, revpar: occupancy * adr });
  }
  return years;
}

function outputs(): EngineOutputsResponse {
  return {
    deal_id: DEAL,
    engines: {
      revenue: { outputs: { years: revenueYears() } },
    },
  } as unknown as EngineOutputsResponse;
}

/** A multi-year P&L baseline — 2024 is a FISCAL YEAR actual, deliberately far
 *  from the TTM Base Year so a leaked growth number would be unmistakable. */
function baseline(): HistoricalBaselineResponse {
  return {
    deal_id: DEAL,
    years: [2022, 2023, 2024].map((fiscal_year, i) => ({
      fiscal_year,
      occupancy: 0.62 + i * 0.01,
      adr: 240 + i * 5,
      revpar: null,
    })),
    gaps: [],
    look_back_years: 5,
    coverage_pct: 0.6,
    walk: [],
  } as unknown as HistoricalBaselineResponse;
}

const MARKET_NO_CBRE = {
  deal_id: DEAL,
  str_trend: {
    subject_occupancy_pct: 0.716,
    subject_adr_usd: 288,
    subject_revpar_usd: 206.2,
    mpi_occupancy_index: 103.2,
    ari_adr_index: 94.2,
    rgi_revpar_index: 97.2,
    comp_set_size: 5,
    compset: [{ name: 'The Betsy Hotel', keys: 61 }],
  },
};

/** The cells of the row whose sticky label is `label`. */
function rowCells(label: string): HTMLElement[] {
  const cell = screen.getAllByText(label)[0];
  const tr = cell.closest('tr') as HTMLElement;
  return Array.from(tr.querySelectorAll('td')).slice(1);
}

beforeEach(() => {
  mockMarket = MARKET_NO_CBRE;
  mockOutputs = outputs();
  mockBaseline = baseline();
  mockIndexMethodology = null;
  mockOverrides = {};
  mockSources = {};
  updateDeal.mockClear();
});
afterEach(() => cleanup());

const FIRST_FORECAST = 6; // index of 2025 among the 15 year columns

describe('Index Analysis — RGI Growth is gone (FON-60 §5)', () => {
  it('renders no RGI Growth row, and keeps the three index LEVEL rows', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('Penetration Index')).toBeInTheDocument();

    expect(screen.queryByText('RGI Growth (pts)')).not.toBeInTheDocument();
    expect(screen.queryByText(/RGI Growth/)).not.toBeInTheDocument();

    expect(screen.getByText('Occupancy Index (MPI)')).toBeInTheDocument();
    expect(screen.getByText('ADR Index (ARI)')).toBeInTheDocument();
    expect(screen.getByText('RevPAR Index (RGI)')).toBeInTheDocument();
  });

  it('never prints a +0.0 growth cell anywhere in the penetration table', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Penetration Index')).closest('table') as HTMLElement;
    expect(within(table).queryByText('+0.0')).not.toBeInTheDocument();
  });
});

describe('Index Analysis — the first forecast year’s growth (FON-61 §3)', () => {
  it('is N/A when the last historical column and the Base Year are different bases', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('Subject Property')).toBeInTheDocument();

    for (const row of ['Occupancy Growth', 'ADR Growth', 'RevPAR Growth']) {
      const cells = rowCells(row);
      expect(cells[FIRST_FORECAST], row).toHaveTextContent('N/A');
    }
  });

  it('names the mismatch on hover rather than leaving a bare N/A', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('Subject Property')).toBeInTheDocument();

    const cell = rowCells('ADR Growth')[FIRST_FORECAST];
    const na = within(cell).getByText('N/A');
    expect(na.getAttribute('title')).toMatch(/fiscal year/i);
    expect(na.getAttribute('title')).toMatch(/trailing-twelve-month/i);
  });

  it('leaves the SECOND forecast year a real number — only the boundary is refused', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('Subject Property')).toBeInTheDocument();

    // ADR grows 4% a year in the fixture, forecast over forecast.
    const cells = rowCells('ADR Growth');
    expect(cells[FIRST_FORECAST + 1]).toHaveTextContent('4.0%');
    expect(cells[FIRST_FORECAST + 1]).not.toHaveTextContent('N/A');
    expect(cells[FIRST_FORECAST + 2]).toHaveTextContent('4.0%');
  });

  it('shows a real growth number at the boundary when the anchor IS the Base Year', async () => {
    // No multi-year P&L → the anchor column falls back to revenue years[0],
    // so the step is the model's own Base Year → Year 2 growth.
    mockBaseline = null;
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('Subject Property')).toBeInTheDocument();

    const cells = rowCells('ADR Growth');
    expect(cells[FIRST_FORECAST]).toHaveTextContent('4.0%');
    expect(cells[FIRST_FORECAST]).not.toHaveTextContent('N/A');
  });
});

describe('Index Analysis — the forecast band names its origin (FON-60 §4)', () => {
  it('says Fondok-derived when no CBRE Horizons report is on the deal', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect((await screen.findAllByText(/Forecast — Fondok-derived/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Forecast — CBRE Horizons/)).not.toBeInTheDocument();
    // …and says why, in one line, above the tables.
    expect(
      screen.getByText(/no CBRE Horizons report is on this deal/),
    ).toBeInTheDocument();
  });

  it('says CBRE Horizons when a real projection was uploaded', async () => {
    mockMarket = {
      ...MARKET_NO_CBRE,
      cbre_horizons: {
        years: [
          { year_index: 1, occupancy_pct: 0.72, adr_usd: 300, revpar_usd: 216 },
          { year_index: 2, occupancy_pct: 0.73, adr_usd: 312, revpar_usd: 227.8 },
        ],
      },
    };
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect((await screen.findAllByText(/Forecast — CBRE Horizons/)).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Forecast — Fondok-derived/)).not.toBeInTheDocument();
  });

  it('the band is not the bare word "Forecast" any more', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('Subject Property')).toBeInTheDocument();
    const bands = screen.queryAllByText('Forecast');
    expect(bands).toHaveLength(0);
  });
});


// ─────────────── FON-41 E-027 — Index Analysis reconciles to Projections ───────────────

/** The tester's deal, in miniature: a T-12 Base Year of 83.9% / $245 and the
 *  model's seed growth (+0.8% occupancy, +4.0% ADR). Index Analysis' 2025
 *  column is ``years[1]`` = 84.6% / $254.80 — the reported mismatch. */
function testerOutputs(): EngineOutputsResponse {
  const years = [];
  for (let i = 0; i < 9; i++) {
    const occupancy = 0.839 * 1.008 ** i;
    const adr = 245 * 1.04 ** i;
    years.push({ year: i + 1, occupancy, adr, revpar: occupancy * adr });
  }
  return {
    deal_id: DEAL,
    engines: {
      revenue: {
        outputs: { years, projection_calendar_years: years.map((_, i) => 2025 + i) },
      },
    },
  } as unknown as EngineOutputsResponse;
}

const T12_FIELD = { filename: 'T12 Mar 2025.xlsx', page: 1, as_of: '2025-03-31', scope: 'ttm' };
const T12_SOURCES = {
  starting_occupancy: { source: 't12_actual', value: 0.839, field: T12_FIELD },
  starting_adr: { source: 't12_actual', value: 245, field: T12_FIELD },
  occupancy_growth: { source: 'seed', value: 0.008 },
  adr_growth: { source: 'seed', value: 0.04 },
};

describe('Index Analysis — reconciliation to Projections (FON-41 E-027)', () => {
  it('names both sources and the transformation in one line', async () => {
    mockOutputs = testerOutputs();
    mockSources = T12_SOURCES;
    mockMarket = {
      ...MARKET_NO_CBRE,
      str_trend: {
        ...MARKET_NO_CBRE.str_trend,
        subject_occupancy_pct: 84.6,
        subject_adr_usd: 255,
        report_month: '2025-05',
      },
    };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const line = await screen.findByTestId('index-reconciliation-line');
    const text = line.textContent ?? '';
    // Projections' own Base Year, its calendar year and its source document.
    expect(text).toMatch(/Projections use 2025 Base year \(Year 1\) 83\.9% \/ \$245\.00/);
    expect(text).toMatch(/T-12 actual to 2025-03 — T12 Mar 2025\.xlsx, p\.1/);
    // What Index Analysis shows for 2025, and why it differs.
    expect(text).toMatch(/Index Analysis's 2025 column shows 84\.6% \/ \$254\.80/);
    expect(text).toMatch(
      /model Year 2 \(revenue\.years\[1\]\) — the Base Year grown 1 year at \+0\.8% occupancy \/ \+4\.0% ADR/,
    );
    // The STR TTM is named as a different source and period.
    expect(text).toMatch(/STR subject TTM to 2025-05 84\.6% \/ \$255\.00 is a different source and period/);
  });

  it('lists every step from the source to the Base Year, including displacement', async () => {
    mockOutputs = testerOutputs();
    mockSources = { ...T12_SOURCES, y1_occupancy_displacement_pct: { source: 'seed', value: 0.05 } };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const card = await screen.findByTestId('index-projections-reconciliation');
    expect(within(card).getByText(/Stabilized baseline 83\.9% \/ \$245\.00/)).toBeInTheDocument();
    expect(within(card).getByText(/Year-1 renovation displacement −5\.0% occupancy/)).toBeInTheDocument();
    expect(within(card).getByText(/Revenue engine Year 1 \(revenue\.years\[0\]\)/)).toBeInTheDocument();
  });

  it('says "the same" when the Index Analysis column IS the Base Year', () => {
    const input: ReconciliationInput = {
      baseYear: { occupancy: 0.83, adr: 232.77 },
      baseCalendarYear: 2024,
      startingOccupancy: 0.83,
      startingAdr: 232.77,
      occSource: { source: 't12_actual', filename: 'T12.xlsx', page: 2 },
      adrSource: { source: 't12_actual' },
      y1OccDisplacement: 0,
      y1AdrDisplacement: 0,
      occupancyGrowth: 0.008,
      adrGrowth: 0.04,
      iaColumnYear: 2024,
      iaOccupancy: 0.83,
      iaAdr: 232.77,
      iaOrigin: { kind: 'engine', yearIndex: 0 },
      strOccupancy: null,
      strAdr: null,
      strPeriodLabel: null,
    };
    const rec = buildReconciliation(input)!;
    expect(rec.matches).toBe(true);
    expect(rec.line).toMatch(/Index Analysis shows the same 83\.0% \/ \$232\.77 in its 2024 column/);
  });
});

// ─────────────── FON-41 E-028 — methodology selector + editable assumptions ───────────────

function figure(value: number | null, source: string | null, inputs: unknown[] = [], detail: string | null = null) {
  return { value, source, inputs, detail, period_label: null };
}
const STR_REF = {
  field_name: 'ttm_performance.indices.mpi_occupancy_index',
  value: 103.2,
  doc_name: 'STR Trend May 2025.xlsx',
  page: 4,
};
const MKT_REF = {
  field_name: 'pnl_benchmark.market.adr_change_2026_forecast',
  value: 0.031,
  doc_name: 'Submarket Report.pdf',
  page: 6,
};
function methodology(extra: Record<string, unknown> = {}) {
  return {
    deal_id: DEAL,
    selected: 'str_comp_set',
    selected_source: 'default',
    toggle_on: false,
    subject_occupancy: figure(0.716, 'document'),
    subject_adr: figure(288, 'document'),
    subject_period_label: 'TTM to 2025-05',
    methods: [
      {
        method: 'str_comp_set', label: 'STR competitive set', available: true, disabled_reason: null,
        occupancy: figure(0.694, 'computed', [STR_REF], 'subject TTM occupancy ÷ MPI'),
        adr: figure(305.7, 'computed', [STR_REF]),
        documents: ['STR Trend May 2025.xlsx'], segment: null, segments_available: [],
      },
      {
        method: 'market_benchmark', label: 'Market / chain-scale benchmark', available: true, disabled_reason: null,
        occupancy: figure(0.712, 'document', [MKT_REF]), adr: figure(231, 'document', [MKT_REF]),
        documents: ['Submarket Report.pdf'], segment: null, segments_available: [],
      },
      {
        method: 'costar_comp_set', label: 'CoStar Property Analytics comp set', available: false,
        disabled_reason: 'No CoStar Property Analytics comp-set extraction on this deal.',
        occupancy: figure(null, null), adr: figure(null, null), documents: [], segment: null, segments_available: [],
      },
    ],
    assumptions: {
      index_market_occupancy_growth: figure(
        null, null, [], 'No market occupancy growth forecast in the uploaded reports — enter your own.',
      ),
      index_market_adr_growth: figure(0.031, 'document', [MKT_REF]),
      index_mpi_target: figure(103.2, 'document', [STR_REF]),
      index_ari_target: figure(105, 'override', [], 'Post-PIP repositioning'),
    },
    ...extra,
  };
}

describe('Index Analysis — methodology selector (FON-41 E-028)', () => {
  it('shows the three methods; the disabled one says why', async () => {
    mockIndexMethodology = methodology();
    render(<IndexAnalysisSection dealId={DEAL} />);
    const costar = await screen.findByTestId('index-method-costar_comp_set');
    expect(costar).toBeDisabled();
    expect(costar.getAttribute('title')).toMatch(/No CoStar Property Analytics comp-set extraction/);
    expect(screen.getByTestId('index-method-reason-costar_comp_set')).toHaveTextContent(
      /CoStar Property Analytics comp set unavailable: No CoStar Property Analytics comp-set extraction/,
    );
    expect(screen.getByTestId('index-method-str_comp_set')).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByTestId('index-method-market_benchmark')).not.toBeDisabled();
  });

  it('saves the selected method as the index_methodology override (no note — a method choice)', async () => {
    mockIndexMethodology = methodology();
    mockOverrides = { exit_cap_rate: { value: 0.07, note: 'Broker' } };
    render(<IndexAnalysisSection dealId={DEAL} />);
    fireEvent.click(await screen.findByTestId('index-method-market_benchmark'));
    await waitFor(() => expect(updateDeal).toHaveBeenCalledTimes(1));
    const [dealId, body] = updateDeal.mock.calls[0] as [string, { field_overrides: Record<string, unknown> }];
    expect(dealId).toBe(DEAL);
    expect(body.field_overrides.index_methodology).toBe('market_benchmark');
    // The rest of the blob survives the PATCH.
    expect(body.field_overrides.exit_cap_rate).toEqual({ value: 0.07, note: 'Broker' });
  });

  it('shows each assumption with its document + page, a reason, or "Your override"', async () => {
    mockIndexMethodology = methodology();
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByTestId('index-assumption-source-index_market_adr_growth')).toHaveTextContent(
      'Submarket Report.pdf, p.6 (pnl_benchmark.market.adr_change_2026_forecast)',
    );
    expect(screen.getByTestId('index-assumption-source-index_mpi_target')).toHaveTextContent(
      'STR Trend May 2025.xlsx, p.4',
    );
    expect(screen.getByTestId('index-assumption-source-index_ari_target')).toHaveTextContent(
      'Your override — Post-PIP repositioning',
    );
    expect(screen.getByTestId('index-assumption-source-index_market_occupancy_growth')).toHaveTextContent(
      /enter your own/,
    );
    // Toggle off → the panel says the assumptions do not feed Projections yet.
    expect(screen.getByTestId('index-feeds-note')).toHaveTextContent(/do not feed Projections yet/);
  });

  it('an assumption edit needs a note and is written with it', async () => {
    mockIndexMethodology = methodology({ toggle_on: true });
    render(<IndexAnalysisSection dealId={DEAL} />);
    const row = await screen.findByTestId('index-assumption-index_market_adr_growth');
    fireEvent.click(within(row).getByText('Edit'));
    fireEvent.change(within(row).getByLabelText('Market ADR growth value'), { target: { value: '3.5' } });
    fireEvent.click(within(row).getByText('Save'));
    expect(updateDeal).not.toHaveBeenCalled(); // no note yet
    fireEvent.change(within(row).getByLabelText('Market ADR growth note'), {
      target: { value: 'CBRE Q3 outlook' },
    });
    fireEvent.click(within(row).getByText('Save'));
    await waitFor(() => expect(updateDeal).toHaveBeenCalledTimes(1));
    const body = updateDeal.mock.calls[0][1] as {
      field_overrides: Record<string, { value: number; note: string }>;
    };
    expect(body.field_overrides.index_market_adr_growth.value).toBeCloseTo(0.035);
    expect(body.field_overrides.index_market_adr_growth.note).toBe('CBRE Q3 outlook');
    expect(screen.getByTestId('index-feeds-note')).toHaveTextContent(/feed Projections now/);
  });
});
