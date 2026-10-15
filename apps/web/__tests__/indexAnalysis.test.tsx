/**
 * Index Analysis — what the columns and the forecast cells are allowed to claim.
 *
 *  1. FON-94 — the year columns follow the acquisition close calendar: the
 *     forecast columns are exactly the revenue engine's
 *     ``projection_calendar_years`` (Year 1 in the first forecast column, as
 *     the Future P&L places it), the six columns before them are history, and
 *     nothing is carried forward beyond the engine horizon. With no close date
 *     the forecast columns say "Year N" and a one-line note says why.
 *
 *  2. FON-60 §5 — no `RGI Growth (pts)` row (the indices are held flat).
 *
 *  3. FON-61 §3 — the first forecast year's growth is N/A when the last
 *     historical column is a reported actual on a different period basis.
 *
 *  4. FON-95 — fair share includes the subject in the denominator; occupancy /
 *     ADR / RevPAR variances; a Market row (CBRE Horizons is a MARKET forecast,
 *     never labelled comp set; the comp set has no forecast source and is not
 *     re-scaled off the subject); STR annual subject history before P&L fiscal
 *     years, with the source named per column.
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
function revenueYears(count = 9) {
  const years = [];
  for (let i = 0; i < count; i++) {
    const occupancy = 0.716;
    const adr = 288 * 1.04 ** i;
    years.push({ year: i + 1, occupancy, adr, revpar: occupancy * adr });
  }
  return years;
}

/** Engine outputs with the FON-41 calendar: a 2025 close → Year 1 = 2025. */
function outputs(count = 9, startYear: number | null = 2025): EngineOutputsResponse {
  const years = revenueYears(count);
  return {
    deal_id: DEAL,
    engines: {
      revenue: {
        outputs: {
          years,
          ...(startYear != null ? { projection_calendar_years: years.map((_, i) => startYear + i) } : {}),
        },
      },
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

const CBRE_2025 = {
  submarket: 'Miami Beach',
  doc_name: 'Miami Horizons Q3 2025.xlsx',
  years: [
    { year_index: 1, calendar_year: 2025, occupancy_pct: 0.72, adr_usd: 300, revpar_usd: 216, page: 4 },
    { year_index: 2, calendar_year: 2026, occupancy_pct: 0.73, adr_usd: 312, revpar_usd: 227.8, page: 4 },
  ],
};

/** The cells of the row whose sticky label is `label` (first match). */
function rowCells(label: string, table?: HTMLElement): HTMLElement[] {
  const scope = table ? within(table) : screen;
  const cell = scope.getAllByText(label)[0];
  const tr = cell.closest('tr') as HTMLElement;
  return Array.from(tr.querySelectorAll('td')).slice(1);
}
/** The <table> whose band title is `title`. */
function tableOf(title: string): HTMLElement {
  return screen.getByText(title).closest('table') as HTMLElement;
}
/** The year-header labels of a table (first line of each header cell). */
function yearHeaders(table: HTMLElement): string[] {
  const rows = Array.from(table.querySelectorAll('thead tr'));
  const headerRow = rows[1] as HTMLElement;
  return Array.from(headerRow.querySelectorAll('th'))
    .slice(1)
    .map((th) => (th.firstElementChild?.textContent ?? th.textContent ?? '').trim());
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

const FIRST_FORECAST = 6; // index of 2025 among the 15 year columns (2019–2033)

// ─────────────── FON-94 — columns follow the acquisition calendar ───────────────

describe('Index Analysis — year columns follow the close calendar (FON-94)', () => {
  it('forecast columns are exactly the engine calendar; six historical years precede Year 1', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;
    expect(yearHeaders(table)).toEqual([
      '2019', '2020', '2021', '2022', '2023', '2024',
      '2025', '2026', '2027', '2028', '2029', '2030', '2031', '2032', '2033',
    ]);
    // The first forecast column IS model Year 1 — what Projections heads "Base year (Year 1)".
    expect(within(table).getAllByText('Year 1').length).toBeGreaterThan(0);
    expect(screen.queryByTestId('index-no-close-date-note')).not.toBeInTheDocument();
    expect(screen.getByText('15-Year Series')).toBeInTheDocument();
  });

  it('nothing is carried forward beyond the engine horizon', async () => {
    // A 5-year hold closing in 2025: columns end at 2029 — no invented 2030+.
    mockOutputs = outputs(5, 2025);
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;
    const headers = yearHeaders(table);
    expect(headers[headers.length - 1]).toBe('2029');
    expect(headers).not.toContain('2030');
    expect(headers).toHaveLength(11);
    // Year 5 (2029) is revenue.years[4]: 288 × 1.04⁴ = $336.9 → "$337".
    expect(rowCells('ADR', table)[10]).toHaveTextContent('$337');
    expect(screen.getByText('11-Year Series')).toBeInTheDocument();
  });

  it('the same calendar year sits in the same column: 2026 is years[1], not years[2]', async () => {
    // A 2026 close (Year 1 = 2026): the 2026 column must be the Base Year.
    mockOutputs = outputs(9, 2026);
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;
    const headers = yearHeaders(table);
    expect(headers.slice(0, 7)).toEqual(['2020', '2021', '2022', '2023', '2024', '2025', '2026']);
    expect(rowCells('ADR', table)[6]).toHaveTextContent('$288'); // years[0]
    expect(rowCells('ADR', table)[7]).toHaveTextContent('$300'); // years[1] = 288 × 1.04
  });

  it('with no close date the forecast columns are ordinals and a note says why', async () => {
    mockOutputs = outputs(9, null);
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;
    const headers = yearHeaders(table);
    // History = the years the documents report (the P&L fiscal years); forecast = "Year N".
    expect(headers.slice(0, 3)).toEqual(['2022', '2023', '2024']);
    expect(headers.slice(3)).toEqual(['Year 1', 'Year 2', 'Year 3', 'Year 4', 'Year 5', 'Year 6', 'Year 7', 'Year 8', 'Year 9']);
    expect(headers.some((h) => /^20(2[5-9]|3\d)$/.test(h))).toBe(false); // no guessed year
    expect(screen.getByTestId('index-no-close-date-note')).toHaveTextContent(
      'Set the acquisition date on CAPEX to map years to calendar years',
    );
  });
});

// ─────────────── FON-60 §5 / FON-61 §3 ───────────────

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
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;

    for (const row of ['Occupancy Growth', 'ADR Growth', 'RevPAR Growth']) {
      const cells = rowCells(row, table);
      expect(cells[FIRST_FORECAST], row).toHaveTextContent('N/A');
    }
  });

  it('names the mismatch on hover rather than leaving a bare N/A', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;

    const cell = rowCells('ADR Growth', table)[FIRST_FORECAST];
    const na = within(cell).getByText('N/A');
    expect(na.getAttribute('title')).toMatch(/fiscal year/i);
    expect(na.getAttribute('title')).toMatch(/trailing-twelve-month/i);
    // The note names the real columns, not a hard-coded 2024 / 2025.
    expect(na.getAttribute('title')).toMatch(/the 2024 column/);
    expect(na.getAttribute('title')).toMatch(/2025 is the model's Base Year/);
  });

  it('leaves the SECOND forecast year a real number — only the boundary is refused', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;

    // ADR grows 4% a year in the fixture, forecast over forecast.
    const cells = rowCells('ADR Growth', table);
    expect(cells[FIRST_FORECAST + 1]).toHaveTextContent('4.0%');
    expect(cells[FIRST_FORECAST + 1]).not.toHaveTextContent('N/A');
    expect(cells[FIRST_FORECAST + 2]).toHaveTextContent('4.0%');
  });

  it('with no historical actual the boundary cell is simply unreported — no refusal note', async () => {
    // No multi-year P&L and no STR annual history → the 2024 column is blank,
    // so there is nothing to grow from; the Base Year itself still sits in 2025.
    mockBaseline = null;
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;
    expect(rowCells('ADR', table)[FIRST_FORECAST - 1]).toHaveTextContent('—');
    expect(rowCells('ADR', table)[FIRST_FORECAST]).toHaveTextContent('$288');
    const na = within(rowCells('ADR Growth', table)[FIRST_FORECAST]).getByText('N/A');
    expect(na.getAttribute('title')).toBeNull();
    expect(rowCells('ADR Growth', table)[FIRST_FORECAST + 1]).toHaveTextContent('4.0%');
  });
});

// ─────────────── FON-95 — Market row vs comp set labelling ───────────────

describe('Index Analysis — the forecast band names its origin (FON-60 §4 / FON-95)', () => {
  it('with no CBRE report: subject = underwriting, comp set and market say they have no forecast source', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText(/Forecast — Fondok underwriting/)).toBeInTheDocument();
    expect(screen.getByText(/Forecast — no comp-set forecast source/)).toBeInTheDocument();
    expect(screen.getByText(/Forecast — no market forecast source/)).toBeInTheDocument();
    expect(screen.queryByText(/CBRE Horizons/, { selector: 'th' })).not.toBeInTheDocument();
    // …and says why, in one line, above the tables.
    expect(screen.getByText(/No CBRE Horizons report is on this deal/)).toBeInTheDocument();
    // The comp-set forecast is "—", NOT the subject's growth re-scaled.
    const comp = tableOf('STR Competitive Set');
    expect(rowCells('ADR', comp)[FIRST_FORECAST]).toHaveTextContent('—');
    expect(rowCells('ADR', comp)[FIRST_FORECAST + 1]).toHaveTextContent('—');
    // The TTM blend still anchors the last historical column: 288 ÷ 0.942 = $306.
    expect(rowCells('ADR', comp)[FIRST_FORECAST - 1]).toHaveTextContent('$306');
  });

  it('a CBRE Horizons projection is the MARKET forecast, placed by its calendar year, with doc + page', async () => {
    mockMarket = { ...MARKET_NO_CBRE, cbre_horizons: CBRE_2025 };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const band = await screen.findByText(/Forecast — Market \/ CBRE Horizons — Miami Beach \(Miami Horizons Q3 2025\.xlsx, p\.4\)/);
    expect(band.closest('table')).toBe(tableOf('Market'));
    // The comp set is never labelled with the CBRE forecast.
    expect(screen.getByText(/Forecast — no comp-set forecast source/)).toBeInTheDocument();
    const comp = tableOf('STR Competitive Set');
    expect(rowCells('ADR', comp)[FIRST_FORECAST]).toHaveTextContent('—');
    // The Market row carries the CBRE figures in 2025 / 2026 and nothing after.
    const market = tableOf('Market');
    expect(rowCells('ADR', market)[FIRST_FORECAST]).toHaveTextContent('$300');
    expect(rowCells('ADR', market)[FIRST_FORECAST + 1]).toHaveTextContent('$312');
    expect(rowCells('ADR', market)[FIRST_FORECAST + 2]).toHaveTextContent('—');
    expect(rowCells('Source', market)[FIRST_FORECAST]).toHaveTextContent('CBRE');
  });

  it('places CBRE Year 1 by ITS calendar year, not by assuming it is the first forecast column', async () => {
    // A Q3 2026-vintage report: Year 1 = 2026. It must land in the 2026 column.
    mockMarket = {
      ...MARKET_NO_CBRE,
      cbre_horizons: {
        years: [{ year_index: 1, calendar_year: 2026, occupancy_pct: 0.72, adr_usd: 300, revpar_usd: 216 }],
      },
    };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const market = (await screen.findByText('Market')).closest('table') as HTMLElement;
    expect(rowCells('ADR', market)[FIRST_FORECAST]).toHaveTextContent('—');
    expect(rowCells('ADR', market)[FIRST_FORECAST + 1]).toHaveTextContent('$300');
  });

  it('refuses to place CBRE years that carry no calendar year, and says why', async () => {
    mockMarket = {
      ...MARKET_NO_CBRE,
      cbre_horizons: { years: [{ year_index: 1, occupancy_pct: 0.72, adr_usd: 300, revpar_usd: 216 }] },
    };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const band = await screen.findByText(/Forecast — no market forecast source/);
    expect(band.getAttribute('title')).toMatch(/no calendar year/);
    const market = tableOf('Market');
    expect(rowCells('ADR', market)[FIRST_FORECAST]).toHaveTextContent('—');
  });

  it('historical market actuals come from the market-level rows by calendar year', async () => {
    mockMarket = {
      ...MARKET_NO_CBRE,
      market_history: [
        { calendar_year: 2023, period: 'annual', segment: null, occupancy_pct: 0.70, adr_usd: 231, doc_type: 'MARKET_STUDY', doc_name: 'CoStar Submarket.pdf', page: 12, fields: ['market_study.trend.2023.adr_usd'] },
      ],
    };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const market = (await screen.findByText('Market')).closest('table') as HTMLElement;
    expect(rowCells('ADR', market)[4]).toHaveTextContent('$231'); // 2023
    expect(rowCells('Occupancy', market)[4]).toHaveTextContent('70.0%');
    const src = rowCells('Source', market)[4];
    expect(src).toHaveTextContent('Market study');
    expect(within(src).getByText('Market study').getAttribute('title')).toMatch(/CoStar Submarket\.pdf, p\.12/);
  });

  it('the band is not the bare word "Forecast" any more', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('Subject Property')).toBeInTheDocument();
    const bands = screen.queryAllByText('Forecast');
    expect(bands).toHaveLength(0);
  });
});

// ─────────────── FON-95 — subject history: STR annual before P&L fiscal year ───────────────

describe('Index Analysis — subject history source precedence (FON-95)', () => {
  it('STR annual subject figures win over the P&L fiscal year, and the Source row says which', async () => {
    mockMarket = {
      ...MARKET_NO_CBRE,
      str_trend: {
        ...MARKET_NO_CBRE.str_trend,
        doc_name: 'STR Trend May 2025.xlsx',
        subject_annual: [{ calendar_year: 2024, occupancy_pct: 70.1, adr_usd: 275, page: 3 }],
      },
    };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const table = (await screen.findByText('Subject Property')).closest('table') as HTMLElement;
    // 2024 — STR annual (70.1% / $275), NOT the baseline's 64.0% / $250.
    expect(rowCells('Occupancy', table)[5]).toHaveTextContent('70.1%');
    expect(rowCells('ADR', table)[5]).toHaveTextContent('$275');
    // 2023 — only the P&L has it.
    expect(rowCells('ADR', table)[4]).toHaveTextContent('$245');
    const source = rowCells('Source', table);
    expect(source[5]).toHaveTextContent('STR annual');
    expect(within(source[5]).getByText('STR annual').getAttribute('title')).toMatch(/STR Trend May 2025\.xlsx, p\.3/);
    expect(source[4]).toHaveTextContent('P&L FY');
    expect(source[FIRST_FORECAST]).toHaveTextContent('Model Y1');
    expect(source[3]).toHaveTextContent('P&L FY'); // 2022
    expect(source[0]).toHaveTextContent('—'); // 2019 — nothing reported
  });
});

// ─────────────── FON-95 — fair share, variances, opportunity ───────────────

describe('Index Analysis — penetration rows (FON-95)', () => {
  it('fair share of supply includes the subject in the denominator', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    const row = await screen.findByTestId('index-penetration-fair-share');
    const cells = Array.from(row.querySelectorAll('td')).slice(1);
    // 132 ÷ (61 + 132) = 68.39% — not 132 ÷ 61.
    expect(cells[0]).toHaveTextContent('68.39%');
    expect(within(cells[0]).getByText('68.39%').getAttribute('title')).toMatch(/132 ÷ \(comp-set rooms 61 \+ subject rooms 132\)/);
  });

  it('shows fair-share occupancy, actual share of demand and fair-share rooms revenue with their inputs', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    // Anchor column (2024): subject FY 64.0% / $250 ; comp = 0.716 ÷ 1.032 = 69.38%, 288 ÷ 0.942 = $305.73.
    const fso = at('index-penetration-fair-share-occ')[5];
    expect(fso).toHaveTextContent('65.7%'); // (132×0.64 + 61×0.6938) ÷ 193
    expect(within(fso).getByText('65.7%').getAttribute('title')).toMatch(/subject rooms 132 × subject occupancy 64\.0%/);
    const share = at('index-penetration-actual-share')[5];
    expect(share).toHaveTextContent('66.62%'); // 84.48 ÷ (84.48 + 42.32)
    const rev = at('index-penetration-fair-share-rev')[5];
    expect(within(rev).getByText(/^\$[\d,]+$/).getAttribute('title')).toMatch(/132 × 366 days × blended RevPAR/);
    // Forecast columns have no comp set → "—", never a number.
    expect(at('index-penetration-fair-share-occ')[FIRST_FORECAST]).toHaveTextContent('—');
  });

  it('adds occupancy (pts) and ADR ($ and %) variances beside the RevPAR variance', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    expect(at('index-penetration-occ-var')[5]).toHaveTextContent('−5.4 pts'); // 64.0 − 69.38
    expect(at('index-penetration-adr-var')[5]).toHaveTextContent('−$56'); // 250 − 305.73
    expect(at('index-penetration-adr-var-pct')[5]).toHaveTextContent('−18.2%'); // 250 ÷ 305.73 − 1
    expect(at('index-penetration-revpar-var')[5]).toHaveTextContent('−$52'); // 160 − 212.14
  });

  it('revenue opportunity is signed at par, and also shown at the analyst’s MPI / ARI targets', async () => {
    // Both targets ANALYST-SET (source 'override'); the fixture's MPI default
    // is the document's published index, which is not a target (see below).
    // "Use STR rates in the model" on — the at-target row is gated exactly
    // like the index rows (see the toggle-off test below).
    mockIndexMethodology = methodology({
      toggle_on: true,
      by_year_applies: true,
      assumptions: {
        ...methodology().assumptions,
        index_mpi_target: figure(103.2, 'override', [], 'Hold share'),
      },
    });
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    // At par: (212.14 − 160) × 132 × 366 = $2,518,…  (positive: the subject is below par).
    const par = at('index-penetration-oppty-par')[5];
    expect(par).toHaveTextContent(/^\$2,5\d\d,\d{3}$/);
    expect(within(par).getByText(/^\$/).getAttribute('title')).toMatch(/signed: negative means the subject already out-earns par/);
    // At target: MPI 103.2 / ARI 105 → target RevPAR = 0.6938×1.032 × 305.73×1.05 = $229.86 → (229.86 − 160) × 132 × 366.
    const target = at('index-penetration-oppty-target')[5];
    expect(target).toHaveTextContent(/^\$3,3\d\d,\d{3}$/);
    expect(within(target).getByText(/^\$/).getAttribute('title')).toMatch(/MPI target 103\.2/);
    expect(within(target).getByText(/^\$/).getAttribute('title')).toMatch(/ARI target 105\.0/);
  });

  it('a subject that out-earns par shows a NEGATIVE opportunity, not a dash', async () => {
    mockBaseline = {
      ...baseline(),
      years: [{ fiscal_year: 2024, occupancy: 0.80, adr: 400, revpar: null }],
    } as unknown as HistoricalBaselineResponse;
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const cells = Array.from(screen.getByTestId('index-penetration-oppty-par').querySelectorAll('td')).slice(1);
    expect(cells[5]).toHaveTextContent(/^−\$[\d,]+$/);
  });

  it('with no target set the at-target row is "—" and says so on the label', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const row = screen.getByTestId('index-penetration-oppty-target');
    expect(Array.from(row.querySelectorAll('td')).slice(1).every((td) => td.textContent === '—')).toBe(true);
    expect(within(row).getByText('Revenue Opportunity at Target').getAttribute('title')).toMatch(/No MPI \/ ARI penetration target set/);
  });

  it('a document-default index is NOT an analyst target: with only the ARI override set, the at-target row is "—" with the reason', async () => {
    // The fixture: index_mpi_target = 103.2 from the STR document (the
    // published MPI itself), index_ari_target = 105 analyst override.
    mockIndexMethodology = methodology({ toggle_on: true, by_year_applies: true });
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const row = screen.getByTestId('index-penetration-oppty-target');
    expect(Array.from(row.querySelectorAll('td')).slice(1).every((td) => td.textContent === '—')).toBe(true);
    expect(within(row).getByText('Revenue Opportunity at Target').getAttribute('title')).toMatch(/No MPI \/ ARI penetration target set/);
    // At par is unaffected — it needs no target.
    const par = Array.from(screen.getByTestId('index-penetration-oppty-par').querySelectorAll('td')).slice(1);
    expect(par[5]).toHaveTextContent(/^\$2,5\d\d,\d{3}$/);
  });
});

// ─────────────── FON-41 E-027 — Index Analysis reconciles to Projections ───────────────

/** The tester's deal, in miniature: a T-12 Base Year of 83.9% / $245 and the
 *  model's seed growth (+0.8% occupancy, +4.0% ADR). FON-94 puts Year 1 in the
 *  2025 column, so Index Analysis now shows the same 83.9% / $245 there. */
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
  it('names both sources and says the 2025 column IS the Projections Base Year', async () => {
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
    expect(text).toMatch(/Future P&L uses 2025 Base year \(Year 1\) 83\.9% \/ \$245\.00/);
    expect(text).toMatch(/T-12 actual to 2025-03 — T12 Mar 2025\.xlsx, p\.1/);
    // FON-94: Index Analysis shows the same figure in the same calendar column.
    expect(text).toMatch(/Index Analysis shows the same 83\.9% \/ \$245\.00 in its 2025 column/);
    expect(text).not.toMatch(/model Year 2/);
    // The STR TTM is named as a different source and period.
    expect(text).toMatch(/STR subject TTM to 2025-05 84\.6% \/ \$255\.00 is a different source and period/);
    // The subject table's 2025 cell agrees with the line.
    const table = tableOf('Subject Property');
    expect(rowCells('ADR', table)[FIRST_FORECAST]).toHaveTextContent('$245');
    expect(rowCells('Occupancy', table)[FIRST_FORECAST]).toHaveTextContent('83.9%');
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

  it('with no close date the card names the "Year 1" column rather than a guessed year', async () => {
    mockOutputs = outputs(9, null);
    mockSources = T12_SOURCES;
    render(<IndexAnalysisSection dealId={DEAL} />);
    const line = await screen.findByTestId('index-reconciliation-line');
    expect(line.textContent).toMatch(/in its Year 1 column/);
    expect(line.textContent).not.toMatch(/2025 column/);
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

// ─────────────── FON-95 part 2 — penetration targets by year ───────────────

/** The worker's by-year grid for a 3-year hold on a 2025 close: the STR comp
 *  set grown at +0.5% occupancy / +3.1% ADR, an optional Year-2 MPI target and
 *  an optional all-years ARI target (what the analyst actually set). */
function byYearRows(opts: { mpiYear2?: number; ariScalar?: number } = {}) {
  return [2025, 2026, 2027].map((cal, i) => {
    const year = i + 1;
    const benchOcc = 0.694 * 1.005 ** i;
    const benchAdr = 305.7 * 1.031 ** i;
    const mpi =
      year === 2 && opts.mpiYear2 != null
        ? { value: opts.mpiYear2, source: 'by_year', note: 'Post-PIP share gain' }
        : { value: null, source: null, note: null };
    const ari =
      opts.ariScalar != null
        ? { value: opts.ariScalar, source: 'scalar', note: 'Post-PIP repositioning' }
        : { value: null, source: null, note: null };
    const occ = mpi.value != null ? (benchOcc * mpi.value) / 100 : null;
    const adr = ari.value != null ? (benchAdr * ari.value) / 100 : null;
    return {
      year,
      calendar_year: cal,
      benchmark_occupancy: figure(
        benchOcc, 'computed', [STR_REF],
        i === 0 ? 'STR competitive set TTM occupancy' : `STR competitive set TTM occupancy grown ${i} year(s) at +0.50% market occupancy growth`,
      ),
      benchmark_adr: figure(benchAdr, 'computed', [STR_REF]),
      mpi_target: mpi,
      ari_target: ari,
      rgi_derived: mpi.value != null && ari.value != null ? (mpi.value * ari.value) / 100 : null,
      occupancy: occ,
      adr,
      occupancy_status: occ != null ? 'driven' : 'no_target',
      adr_status: adr != null ? 'driven' : 'no_target',
      reason: null,
    };
  });
}
const BY_YEAR_OFF_REASON =
  'Targets by year do not feed the Future P&L yet: turn on "Use STR rates in the model" in Market Overview. Until then every year stays on its current basis.';

describe('Index Analysis — penetration targets by year (FON-95 part 2)', () => {
  it('renders one row per model year with the benchmark + source, the derived RGI and the status; disabled with the reason when the toggle is off', async () => {
    mockIndexMethodology = methodology({
      by_year: byYearRows({ mpiYear2: 115, ariScalar: 105 }),
      hold_years: 3, calendar_known: true,
      by_year_applies: false, by_year_disabled_reason: BY_YEAR_OFF_REASON,
    });
    render(<IndexAnalysisSection dealId={DEAL} />);
    const grid = await screen.findByTestId('index-by-year-grid');
    expect(within(grid).getByTestId('index-by-year-row-1')).toHaveTextContent('Base year (Year 1) · 2025');
    expect(within(grid).getByTestId('index-by-year-row-3')).toHaveTextContent('Year 3 · 2027');
    expect(within(grid).queryByTestId('index-by-year-row-4')).toBeNull();
    // Benchmark with its source (document + page, or the growth step).
    expect(within(grid).getByTestId('index-by-year-bench-occ-src-1')).toHaveTextContent('STR Trend May 2025.xlsx, p.4');
    expect(within(grid).getByTestId('index-by-year-bench-occ-src-3')).toHaveTextContent(/grown 2 year/);
    const row2 = within(grid).getByTestId('index-by-year-row-2');
    expect(row2).toHaveTextContent('69.7%'); // 0.694 × 1.005
    expect(row2).toHaveTextContent('115.0');
    expect(within(grid).getByTestId('index-by-year-rgi-2')).toHaveTextContent('120.8'); // 115 × 105 ÷ 100 — derived
    expect(within(grid).getByTestId('index-by-year-rgi-1')).toHaveTextContent('—'); // only one target → no RGI
    expect(within(grid).getByTestId('index-by-year-status-2')).toHaveTextContent('occ indexed · ADR indexed');
    expect(within(grid).getByTestId('index-by-year-status-1')).toHaveTextContent('occ model growth · ADR indexed');
    expect(grid).toHaveTextContent('RGI is derived — MPI × ARI ÷ 100 — not an input.');
    // Toggle off → editing is disabled, and the reason is on screen.
    expect(within(grid).getByTestId('index-by-year-edit')).toBeDisabled();
    expect(within(grid).getByTestId('index-by-year-disabled')).toHaveTextContent(/turn on "Use STR rates in the model"/);
  });

  it('editing a year needs a note and writes the by-year lists (a blank list clears its key)', async () => {
    mockIndexMethodology = methodology({
      toggle_on: true,
      by_year: byYearRows(), hold_years: 3, calendar_known: true, by_year_applies: true, by_year_disabled_reason: null,
    });
    mockOverrides = { exit_cap_rate: { value: 0.07, note: 'Broker' } };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const grid = await screen.findByTestId('index-by-year-grid');
    expect(within(grid).queryByTestId('index-by-year-disabled')).toBeNull();
    fireEvent.click(within(grid).getByTestId('index-by-year-edit'));
    fireEvent.change(within(grid).getByLabelText('Year 2 MPI target'), { target: { value: '115' } });
    fireEvent.click(within(grid).getByTestId('index-by-year-save'));
    expect(updateDeal).not.toHaveBeenCalled(); // no note yet
    expect(within(grid).getByTestId('index-by-year-error')).toHaveTextContent(/Add a short note/);
    fireEvent.change(within(grid).getByLabelText('Targets by year note'), { target: { value: 'Post-PIP share gain' } });
    fireEvent.click(within(grid).getByTestId('index-by-year-save'));
    await waitFor(() => expect(updateDeal).toHaveBeenCalledTimes(1));
    const body = updateDeal.mock.calls[0][1] as { field_overrides: Record<string, unknown> };
    expect(body.field_overrides.index_mpi_target_by_year).toEqual({ value: [null, 115, null], note: 'Post-PIP share gain' });
    expect('index_ari_target_by_year' in body.field_overrides).toBe(false); // nothing set → cleared, not invented
    expect(body.field_overrides.exit_cap_rate).toEqual({ value: 0.07, note: 'Broker' });
  });

  it('an invalid target is refused with the units, and a ratio reads as index points', async () => {
    mockIndexMethodology = methodology({
      toggle_on: true,
      by_year: byYearRows(), hold_years: 3, by_year_applies: true, by_year_disabled_reason: null,
    });
    render(<IndexAnalysisSection dealId={DEAL} />);
    const grid = await screen.findByTestId('index-by-year-grid');
    fireEvent.click(within(grid).getByTestId('index-by-year-edit'));
    fireEvent.change(within(grid).getByLabelText('Year 1 ARI target'), { target: { value: '-5' } });
    fireEvent.change(within(grid).getByLabelText('Targets by year note'), { target: { value: 'n' } });
    fireEvent.click(within(grid).getByTestId('index-by-year-save'));
    expect(updateDeal).not.toHaveBeenCalled();
    expect(within(grid).getByTestId('index-by-year-error')).toHaveTextContent(/index points 25–250 \(100 = parity/);
    fireEvent.change(within(grid).getByLabelText('Year 1 ARI target'), { target: { value: '1.05' } });
    fireEvent.click(within(grid).getByTestId('index-by-year-save'));
    await waitFor(() => expect(updateDeal).toHaveBeenCalledTimes(1));
    const body = updateDeal.mock.calls[0][1] as { field_overrides: Record<string, { value: unknown }> };
    expect(body.field_overrides.index_ari_target_by_year.value).toEqual([105, null, null]);
  });

  it('the penetration table shows the per-year target in the forecast columns and derives RGI, holding the STR index where no target exists', async () => {
    mockIndexMethodology = methodology({
      toggle_on: true,
      by_year: byYearRows({ mpiYear2: 115, ariScalar: 105 }), hold_years: 3, by_year_applies: true, by_year_disabled_reason: null,
    });
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    const mpi = at('index-penetration-mpi');
    expect(mpi[FIRST_FORECAST]).toHaveTextContent('103.2'); // Year 1: no MPI target → held flat
    expect(within(mpi[FIRST_FORECAST]).getByText('103.2').getAttribute('title')).toMatch(/held flat/);
    expect(mpi[FIRST_FORECAST + 1]).toHaveTextContent('115.0'); // Year 2: the by-year target
    expect(within(mpi[FIRST_FORECAST + 1]).getByText('115.0').getAttribute('title')).toMatch(/set for Year 2/);
    const ari = at('index-penetration-ari');
    expect(ari[FIRST_FORECAST]).toHaveTextContent('105.0'); // the analyst's all-years ARI
    expect(within(ari[FIRST_FORECAST]).getByText('105.0').getAttribute('title')).toMatch(/all-years target/);
    const rgi = at('index-penetration-rgi');
    expect(rgi[FIRST_FORECAST + 1]).toHaveTextContent('120.8');
    expect(within(rgi[FIRST_FORECAST + 1]).getByText('120.8').getAttribute('title')).toMatch(/RGI derived/);
    expect(rgi[FIRST_FORECAST]).toHaveTextContent('97.2');
    // The band says so, and there is no "not applied" note.
    expect(screen.getByText(/indices at the analyst’s MPI \/ ARI targets per year \(RGI derived\)/)).toBeTruthy();
    expect(screen.queryByTestId('index-penetration-targets-not-applied')).toBeNull();
  });

  it('with the STR toggle OFF (by_year_applies false) the index rows hold the published index flat — the targets the Subject rows do not run on are not printed — and a note says so', async () => {
    mockIndexMethodology = methodology({
      toggle_on: false,
      by_year: byYearRows({ mpiYear2: 115, ariScalar: 105 }), hold_years: 3, calendar_known: true,
      by_year_applies: false, by_year_disabled_reason: BY_YEAR_OFF_REASON,
    });
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    const mpi = at('index-penetration-mpi');
    expect(mpi[FIRST_FORECAST + 1]).toHaveTextContent('103.2'); // NOT the 115 by-year target
    expect(within(mpi[FIRST_FORECAST + 1]).getByText('103.2').getAttribute('title')).toMatch(/held flat/);
    const ari = at('index-penetration-ari');
    expect(ari[FIRST_FORECAST]).toHaveTextContent('94.2'); // NOT the 105 all-years target
    const rgi = at('index-penetration-rgi');
    expect(rgi[FIRST_FORECAST + 1]).toHaveTextContent('97.2');
    expect(screen.getByTestId('index-penetration-targets-not-applied')).toHaveTextContent(
      'Targets set but not applied — turn on ‘Use STR rates in the model’',
    );
    expect(screen.getByText(/indices held at STR TTM; shares and variances/)).toBeTruthy();
    expect(screen.queryByText(/indices at the analyst’s MPI \/ ARI targets per year/)).toBeNull();
  });

  it('without by-year rows (older worker) the table is exactly as before', async () => {
    mockIndexMethodology = methodology();
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    expect(screen.queryByTestId('index-by-year-grid')).toBeNull();
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    expect(within(at('index-penetration-mpi')[FIRST_FORECAST]).getByText('103.2').getAttribute('title')).toMatch(/held flat/);
    expect(screen.getByText(/indices held at STR TTM; shares and variances/)).toBeTruthy();
  });
});

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
    expect(screen.getByTestId('index-feeds-note')).toHaveTextContent(/do not feed the Future P&L yet/);
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
    expect(screen.getByTestId('index-feeds-note')).toHaveTextContent(/feed the Future P&L now/);
  });
});

// ─────────────── Sam #3 — index targets, comp-set forecast, selected benchmark ───────────────

const LEGACY_REASON =
  'Stored all-years MPI target 5 is outside 25–250 index points (or 0.25–2.5 as a ratio) — re-enter it. It is ignored, not applied.';

describe('Index Analysis — Sam #3', () => {
  it('Revenue Opportunity at Target is gated on by_year_applies like the index rows', async () => {
    mockIndexMethodology = methodology({
      toggle_on: false,
      by_year_applies: false,
      by_year_disabled_reason: BY_YEAR_OFF_REASON,
      assumptions: { ...methodology().assumptions, index_mpi_target: figure(103.2, 'override', [], 'Hold share') },
    });
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    const row = screen.getByTestId('index-penetration-oppty-target');
    expect(Array.from(row.querySelectorAll('td')).slice(1).every((td) => td.textContent === '—')).toBe(true);
    expect(within(row).getByText('Revenue Opportunity at Target').getAttribute('title')).toMatch(
      /Targets set but not applied — Targets by year do not feed Projections yet/,
    );
  });

  it('a target stored as a ratio (1.032 / 1.05) reads as index points in the panel and prices the opportunity like 103.2 / 105', async () => {
    mockIndexMethodology = methodology({
      toggle_on: true,
      by_year_applies: true,
      assumptions: {
        ...methodology().assumptions,
        index_mpi_target: figure(1.032, 'override', [], 'ratio'),
        index_ari_target: figure(1.05, 'override', [], 'ratio'),
      },
    });
    render(<IndexAnalysisSection dealId={DEAL} />);
    const panelRow = await screen.findByTestId('index-assumption-index_mpi_target');
    expect(panelRow).toHaveTextContent('103.2');
    expect(screen.getByTestId('index-assumption-index_ari_target')).toHaveTextContent('105.0');
    const target = Array.from(screen.getByTestId('index-penetration-oppty-target').querySelectorAll('td')).slice(1)[5];
    // Same as the 103.2 / 105 points case — not 100× off.
    expect(target).toHaveTextContent(/^\$3,3\d\d,\d{3}$/);
    expect(within(target).getByText(/^\$/).getAttribute('title')).toMatch(/MPI target 103\.2/);
  });

  it('a legacy out-of-range stored target shows no value, the reason, and never reaches the penetration rows', async () => {
    const rows = byYearRows().map((r) => ({
      ...r,
      mpi_target: { value: null, source: null, note: null, problem: LEGACY_REASON },
      reason: `Occupancy: ${LEGACY_REASON}`,
    }));
    mockIndexMethodology = methodology({
      toggle_on: true,
      by_year_applies: true,
      by_year: rows,
      hold_years: 3,
      assumptions: { ...methodology().assumptions, index_mpi_target: figure(null, null, [], LEGACY_REASON) },
    });
    mockOverrides = { index_mpi_target: { value: 5, note: 'old' } };
    render(<IndexAnalysisSection dealId={DEAL} />);
    const row = await screen.findByTestId('index-assumption-index_mpi_target');
    expect(within(row).getAllByText('—').length).toBeGreaterThan(0);
    expect(screen.getByTestId('index-assumption-source-index_mpi_target')).toHaveTextContent(/5 is outside 25–250.*re-enter it/);
    expect(screen.getByTestId('index-by-year-problems')).toHaveTextContent(/re-enter it/);
    expect(screen.getAllByTestId('index-by-year-target-problem').length).toBe(3);
    const mpi = Array.from(screen.getByTestId('index-penetration-mpi').querySelectorAll('td')).slice(1);
    expect(mpi[FIRST_FORECAST]).toHaveTextContent('103.2'); // the published index, not "5"
  });

  it('fills the comp-set forecast from the worker’s grown series, labelled as an assumption, and the by-year rows follow', async () => {
    const compByYear = [
      {
        year: 1,
        calendar_year: 2025,
        occupancy: figure(0.694, 'computed', [STR_REF], 'STR competitive set TTM to 2025-05 occupancy'),
        adr: figure(305.7, 'computed', [STR_REF], 'STR competitive set TTM to 2025-05 ADR'),
      },
      {
        year: 2,
        calendar_year: 2026,
        occupancy: figure(0.694 * 1.005, 'computed', [STR_REF], 'STR competitive set TTM to 2025-05 occupancy grown 1 year at +0.50% market occupancy growth (your override)'),
        adr: figure(305.7 * 1.031, 'computed', [STR_REF, MKT_REF], 'STR competitive set TTM to 2025-05 ADR grown 1 year at +3.10% market ADR growth (2026)'),
      },
      {
        year: 3,
        calendar_year: 2027,
        occupancy: figure(null, null, [], 'No market occupancy growth assumption to carry the str competitive set past Year 1.'),
        adr: figure(null, null, [], 'No market ADR growth assumption to carry the str competitive set past Year 1.'),
      },
    ];
    mockIndexMethodology = methodology({ comp_set_by_year: compByYear });
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText(/Forecast — comp set grown at market growth \(assumption\)/)).toBeInTheDocument();
    expect(screen.queryByText(/Forecast — no comp-set forecast source/)).toBeNull();
    const comp = tableOf('STR Competitive Set');
    expect(rowCells('ADR', comp)[FIRST_FORECAST]).toHaveTextContent('$306');
    expect(rowCells('ADR', comp)[FIRST_FORECAST + 1]).toHaveTextContent('$315');
    expect(rowCells('ADR', comp)[FIRST_FORECAST + 2]).toHaveTextContent('—');
    expect(rowCells('Source', comp)[FIRST_FORECAST + 1]).toHaveTextContent('Grown (assumption)');
    const src = within(rowCells('Source', comp)[FIRST_FORECAST + 1]).getByText('Grown (assumption)');
    expect(src.getAttribute('title')).toMatch(/Submarket Report\.pdf, p\.6/);
    // The by-year rows now fill: variances, fair share, opportunity at par.
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    expect(at('index-penetration-adr-var')[FIRST_FORECAST + 1]).toHaveTextContent('−$16'); // 299.52 − 315.18
    expect(at('index-penetration-fair-share-occ')[FIRST_FORECAST + 1].textContent).not.toBe('—');
    expect(at('index-penetration-oppty-par')[FIRST_FORECAST + 1].textContent).not.toBe('—');
    expect(at('index-penetration-adr-var')[FIRST_FORECAST + 2]).toHaveTextContent('—');
  });

  it('the penetration rows follow the selected benchmark (market), not the STR-published indices', async () => {
    const rows = byYearRows().map((r, i) => ({
      ...r,
      benchmark_occupancy: figure(i === 0 ? 0.712 : null, i === 0 ? 'document' : null, [MKT_REF], i === 0 ? 'Market benchmark' : 'No CBRE'),
      benchmark_adr: figure(i === 0 ? 231 : null, i === 0 ? 'document' : null, [MKT_REF]),
    }));
    mockIndexMethodology = methodology({ selected: 'market_benchmark', selected_source: 'override', by_year: rows, hold_years: 3 });
    render(<IndexAnalysisSection dealId={DEAL} />);
    await screen.findByText('Penetration Index');
    expect(screen.getByText('Occupancy Index (MPI) vs Market / chain-scale benchmark')).toBeInTheDocument();
    const at = (id: string) => Array.from(screen.getByTestId(id).querySelectorAll('td')).slice(1);
    const mpi = at('index-penetration-mpi');
    // Anchor: subject STR TTM 71.6% ÷ market 71.2% × 100 — not the published 103.2.
    expect(mpi[FIRST_FORECAST - 1]).toHaveTextContent('100.6');
    expect(mpi[FIRST_FORECAST]).toHaveTextContent('100.6'); // engine Y1 71.6% ÷ 71.2%
    expect(mpi[FIRST_FORECAST + 1]).toHaveTextContent('—'); // no market benchmark that year
    expect(at('index-penetration-ari')[FIRST_FORECAST]).toHaveTextContent('124.7'); // 288 ÷ 231
    expect(screen.queryByText('Occupancy Index (MPI)')).toBeNull();
  });

  it('the comp table is titled as the STR comp set, never "CoStar Market"', async () => {
    render(<IndexAnalysisSection dealId={DEAL} />);
    expect(await screen.findByText('STR Competitive Set')).toBeInTheDocument();
    expect(screen.queryByText(/CoStar Market — Competitive Set/)).toBeNull();
  });
});
