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
import { render, screen, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse, HistoricalBaselineResponse } from '@/lib/api';

const DEAL = '11111111-1111-1111-1111-111111111111';

// The market payload the section fetches. Mutable per test.
let mockMarket: Record<string, unknown> | null = null;
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      market: { ...actual.api.market, data: vi.fn(async () => mockMarket) },
    },
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({ deal: { id: DEAL, keys: 132 }, refresh: vi.fn() }),
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
