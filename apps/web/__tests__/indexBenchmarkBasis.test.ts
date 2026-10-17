/**
 * Sam decision #6 (2026-10-15) — "using reported TTM for Year 1 and CBRE
 * forecast data for Year 2 onward … make sure the source and reporting period
 * are clearly labeled so analysts understand the distinction between actual
 * historical data and projected benchmarks."
 *
 * The pure label builders behind the Index Analysis tables, mirroring the
 * worker's (apps/worker/app/services/index_methodology.py actual_label /
 * forecast_label / ASSUMPTION_GROWN_LABEL) so both surfaces read the same.
 */
import { describe, it, expect } from 'vitest';
import type { IndexCompSetYear, IndexFigure } from '@/lib/api';
import {
  ASSUMPTION_GROWN_BASIS_LABEL,
  actualBasisLabel,
  buildCompSeries,
  buildMarketSeries,
  cbreForecastBasisLabel,
  deriveIndexColumns,
  figureBasis,
  periodEndText,
  strPeriodLabel,
  strTtmBasisLabel,
  type StrTrendAPI,
} from '@/components/project/pl/indexAnalysisModel';
import { byYearBasisNote } from '@/components/project/pl/IndexMethodology';

const STR_DOC = 'STR Trend May 2025.xlsx';
const STR: StrTrendAPI = {
  subject_occupancy_pct: 0.716,
  subject_adr_usd: 288,
  mpi_occupancy_index: 103.2,
  ari_adr_index: 94.2,
  rgi_revpar_index: 97.2,
  report_year: 2025,
  ttm_period_end: '2025-05',
  doc_name: STR_DOC,
};
const cols = deriveIndexColumns({ calendarYears: [2025, 2026, 2027], forecastCount: 3, documentYears: [] });
const fig = (value: number | null, extra: Partial<IndexFigure> = {}): IndexFigure => ({
  value, source: value == null ? null : 'computed', inputs: [], detail: null, period_label: null, ...extra,
});

describe('label builders', () => {
  it('periodEndText reads month precision and keeps a bare year', () => {
    expect(periodEndText('2025-05')).toBe('May 2025');
    expect(periodEndText('2025-05-31')).toBe('May 2025');
    expect(periodEndText('2025')).toBe('2025');
    expect(periodEndText(null)).toBeNull();
    expect(periodEndText('garbage')).toBeNull();
  });

  it('Actual / Forecast / Assumption labels read exactly as Sam asked', () => {
    expect(strTtmBasisLabel(STR)).toBe(`Actual · STR TTM May 2025 · ${STR_DOC}`);
    expect(cbreForecastBasisLabel(2027, 'Horizons.pdf', 4)).toBe('Forecast · CBRE Horizons 2027 · Horizons.pdf p.4');
    expect(cbreForecastBasisLabel(2027, null, null)).toBe('Forecast · CBRE Horizons 2027');
    expect(ASSUMPTION_GROWN_BASIS_LABEL).toBe('Assumption · grown at market growth');
    expect(actualBasisLabel('Market study', '2024', null)).toBe('Actual · Market study 2024');
  });

  it('no TTM period end → the report year, never an invented month; no year → no period', () => {
    expect(strTtmBasisLabel({ ...STR, ttm_period_end: null })).toBe(`Actual · STR TTM 2025 · ${STR_DOC}`);
    expect(strTtmBasisLabel({ ...STR, ttm_period_end: null, report_year: null })).toBe(`Actual · STR TTM · ${STR_DOC}`);
  });

  it('strPeriodLabel names the resolved TTM month when the worker sends it', () => {
    expect(strPeriodLabel(STR)).toBe('TTM to May 2025');
    expect(strPeriodLabel({ ...STR, ttm_period_end: '2025' })).toBe('STR 2025 TTM');
  });

  it('figureBasis reads the worker label; no value or no basis → null', () => {
    expect(figureBasis(fig(0.7, { basis: 'forecast', basis_label: 'Forecast · CBRE Horizons 2026 · x.pdf p.4' }))).toEqual({
      basis: 'forecast', label: 'Forecast · CBRE Horizons 2026 · x.pdf p.4',
    });
    expect(figureBasis(fig(null, { basis: 'actual', basis_label: 'Actual · STR TTM' }))).toBeNull();
    expect(figureBasis(fig(0.7))).toBeNull();
  });
});

describe('the tables label each benchmark column', () => {
  it('comp set: STR TTM anchor = actual; forecast Year 1 actual, Year 2+ assumption (worker label wins)', () => {
    const byYear: IndexCompSetYear[] = [
      { year: 1, calendar_year: 2025, occupancy: fig(0.694, { basis: 'actual', basis_label: `Actual · STR TTM May 2025 · ${STR_DOC}` }), adr: fig(305.7) },
      { year: 2, calendar_year: 2026, occupancy: fig(0.70, { basis: 'assumption', basis_label: 'Assumption · grown at market growth' }), adr: fig(315) },
      // An older worker: no basis on the figure — the ordinal says it.
      { year: 3, calendar_year: 2027, occupancy: fig(0.71), adr: fig(325) },
    ];
    const r = buildCompSeries(cols, STR, byYear);
    const anchor = r.sources[cols.firstForecastIndex - 1]!;
    expect(anchor).toMatchObject({ basis: 'actual', basisLabel: `Actual · STR TTM May 2025 · ${STR_DOC}` });
    const f = cols.firstForecastIndex;
    expect(r.sources[f]).toMatchObject({ basis: 'actual' });
    expect(r.sources[f + 1]).toMatchObject({ basis: 'assumption', basisLabel: 'Assumption · grown at market growth' });
    expect(r.sources[f + 2]).toMatchObject({ basis: 'assumption', basisLabel: ASSUMPTION_GROWN_BASIS_LABEL });
  });

  it('market: actuals by year are Actual; CBRE years are Forecast with document + page', () => {
    const r = buildMarketSeries(cols, {
      deal_id: 'd',
      market_history: [{ calendar_year: 2024, period: 'annual', occupancy_pct: 0.7, adr_usd: 230, doc_type: 'MARKET_STUDY', doc_name: 'Sub.pdf', page: 5 }],
      cbre_horizons: {
        doc_name: 'Horizons.pdf', publication_date: 'September 2025',
        years: [{ year_index: 2, calendar_year: 2026, occupancy_pct: 0.73, adr_usd: 312, revpar_usd: null, revpar_growth_pct: null, page: 4 }],
      },
    });
    const f = cols.firstForecastIndex;
    expect(r.sources[f - 1]).toMatchObject({ basis: 'actual', basisLabel: 'Actual · Market study 2024 · Sub.pdf' });
    expect(r.sources[f + 1]).toMatchObject({ basis: 'forecast', basisLabel: 'Forecast · CBRE Horizons 2026 · Horizons.pdf p.4' });
    expect(r.sources[f + 1]!.detail).toMatch(/report dated September 2025/);
  });
});

describe('byYearBasisNote', () => {
  const row = (year: number, occ: IndexFigure) => ({ year, benchmark_occupancy: occ, benchmark_adr: fig(null) });
  it('names the Year-1 actual and the Year-2+ forecast with the CBRE report date', () => {
    const note = byYearBasisNote({
      by_year: [
        row(1, fig(0.7, { basis: 'actual', basis_label: `Actual · STR TTM May 2025 · ${STR_DOC}` })),
        row(2, fig(0.72, { basis: 'forecast', basis_label: 'Forecast · CBRE Horizons 2026 · H.pdf p.4' })),
      ],
      subject_period_end: '2025-05',
      cbre_report_date: 'September 2025',
    } as never);
    expect(note).toBe(
      `Year 1 benchmark: Actual · STR TTM May 2025 · ${STR_DOC} — reported historical data. ` +
        'Year 2 onward: CBRE Horizons forecast (report dated September 2025) — a projected benchmark, not history.',
    );
  });
  it('null with no labelled figure (older worker / no values)', () => {
    expect(byYearBasisNote({ by_year: [row(1, fig(0.7))] } as never)).toBeNull();
    expect(byYearBasisNote({ by_year: [] } as never)).toBeNull();
  });
});
