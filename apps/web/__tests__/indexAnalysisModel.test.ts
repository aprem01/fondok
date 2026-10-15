/**
 * Index Analysis model (FON-94 / FON-95) — the pure functions behind the
 * tables, exercised without rendering: column derivation with and without a
 * close date, the Market series placed by CBRE's own calendar year, the
 * comp set never re-scaled off the subject, fair share with the subject in
 * the denominator, and the signed / at-target revenue opportunity.
 */
import { describe, it, expect } from 'vitest';
import type { EngineOutputsResponse, HistoricalBaselineResponse } from '@/lib/api';
import {
  buildCompSeries,
  compSetKeys,
  buildMarketSeries,
  buildSubjectSeries,
  computePenetration,
  COMP_FORECAST_NONE_REASON,
  daysForColumn,
  deriveIndexColumns,
  emptySeries,
  MARKET_FORECAST_NONE_REASON,
  MARKET_FORECAST_UNPLACED_REASON,
  targetForColumn,
  type IaColumns,
  type StrTrendAPI,
} from '@/components/project/pl/indexAnalysisModel';

const labels = (c: IaColumns) => c.columns.map((x) => x.label);

describe('deriveIndexColumns (FON-94)', () => {
  it('with a calendar: six historical years then exactly the engine horizon', () => {
    const c = deriveIndexColumns({ calendarYears: [2025, 2026, 2027, 2028, 2029], forecastCount: 5, documentYears: [2019, 2024] });
    expect(c.hasCalendar).toBe(true);
    expect(labels(c)).toEqual(['2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026', '2027', '2028', '2029']);
    expect(c.firstForecastIndex).toBe(6);
    expect(c.columns[6]).toMatchObject({ kind: 'forecast', ordinal: 1, calendarYear: 2025 });
    expect(c.columns[10]).toMatchObject({ kind: 'forecast', ordinal: 5, calendarYear: 2029 });
    // Document years are ignored when the calendar is known — the columns
    // are the close calendar, not the documents'.
  });

  it('a calendar shorter than the horizon is not extrapolated — the unlabeled years are "Year N" with no calendar year', () => {
    const c = deriveIndexColumns({ calendarYears: [2025, 2026], forecastCount: 4, documentYears: [] });
    expect(labels(c)).toEqual(['2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026', 'Year 3', 'Year 4']);
    const forecast = c.columns.filter((x) => x.kind === 'forecast');
    expect(forecast.map((x) => x.calendarYear)).toEqual([2025, 2026, null, null]);
    expect(forecast.map((x) => x.ordinal)).toEqual([1, 2, 3, 4]);
    expect(c.hasCalendar).toBe(true);
    // Forecast columns always use the model's flat year, labelled or not.
    expect(daysForColumn(forecast[3])).toBe(365);
  });

  it('a December close moves Year 1 to the next calendar year — the columns follow', () => {
    const c = deriveIndexColumns({ calendarYears: [2026, 2027], forecastCount: 2, documentYears: [] });
    expect(labels(c)).toEqual(['2020', '2021', '2022', '2023', '2024', '2025', '2026', '2027']);
  });

  it('without a close date: document years as history, ordinals as forecast, no guessed year', () => {
    const c = deriveIndexColumns({ calendarYears: [], forecastCount: 3, documentYears: [2024, 2022, 2023, 2022] });
    expect(c.hasCalendar).toBe(false);
    expect(labels(c)).toEqual(['2022', '2023', '2024', 'Year 1', 'Year 2', 'Year 3']);
    expect(c.columns[3]).toMatchObject({ kind: 'forecast', ordinal: 1, calendarYear: null });
    expect(c.firstForecastIndex).toBe(3);
  });

  it('without a close date and more than six document years, keeps the latest six', () => {
    const c = deriveIndexColumns({ calendarYears: null, forecastCount: 1, documentYears: [2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023] });
    expect(labels(c)).toEqual(['2018', '2019', '2020', '2021', '2022', '2023', 'Year 1']);
  });

  it('without any document year, the STR TTM comp anchor still gets one labelled column', () => {
    const c = deriveIndexColumns({ calendarYears: undefined, forecastCount: 2, documentYears: [], hasStrTtm: true });
    expect(labels(c)).toEqual(['STR TTM', 'Year 1', 'Year 2']);
    expect(c.columns[0].calendarYear).toBeNull();
  });

  it('no engine run at all: history only, firstForecastIndex = length', () => {
    const c = deriveIndexColumns({ calendarYears: [], forecastCount: 0, documentYears: [2023] });
    expect(labels(c)).toEqual(['2023']);
    expect(c.firstForecastIndex).toBe(1);
  });

  it('forecast columns use the engine’s 365-day year; historical columns their real day count', () => {
    const c = deriveIndexColumns({ calendarYears: [2025], forecastCount: 1, documentYears: [] });
    const by = Object.fromEntries(c.columns.map((x) => [x.label, daysForColumn(x)]));
    expect(by['2024']).toBe(366);
    expect(by['2023']).toBe(365);
    expect(by['2025']).toBe(365);
  });
});

const cols2025 = deriveIndexColumns({ calendarYears: [2025, 2026, 2027], forecastCount: 3, documentYears: [] });

const STR: StrTrendAPI = {
  subject_occupancy_pct: 0.716,
  subject_adr_usd: 288,
  subject_revpar_usd: 206.2,
  mpi_occupancy_index: 103.2,
  ari_adr_index: 94.2,
  rgi_revpar_index: 97.2,
  report_month: '2025-05',
  compset: [{ name: 'A', keys: 61 }],
};

describe('buildCompSeries (FON-95)', () => {
  it('anchors the STR TTM blend on the last historical column and leaves every forecast cell null', () => {
    const r = buildCompSeries(cols2025, STR);
    expect(r.anchorIndex).toBe(5);
    expect(r.series.occupancy[5]).toBeCloseTo(0.716 / 1.032, 6);
    expect(r.series.adr[5]).toBeCloseTo(288 / 0.942, 6);
    expect(r.series.revpar[5]).toBeCloseTo(206.2 / 0.972, 6);
    expect(r.sources[5]?.kind).toBe('str_ttm');
    expect(r.sources[5]?.detail).toMatch(/TTM to 2025-05/);
    for (const k of [6, 7, 8]) {
      expect(r.series.occupancy[k]).toBeNull();
      expect(r.series.adr[k]).toBeNull();
      expect(r.series.revpar[k]).toBeNull();
    }
    expect(r.forecastReason).toBe(COMP_FORECAST_NONE_REASON);
    // Prior-year comp history is not published either.
    expect(r.series.adr.slice(0, 5).every((v) => v == null)).toBe(true);
  });
});

describe('buildMarketSeries (FON-95)', () => {
  it('places CBRE forecast years by their calendar year, with doc + page provenance', () => {
    const r = buildMarketSeries(cols2025, {
      deal_id: 'd',
      cbre_horizons: {
        submarket: 'Miami Beach',
        doc_name: 'Horizons.xlsx',
        years: [
          // Year 1 of a 2026-vintage report is 2026 — NOT the first forecast column.
          { year_index: 1, calendar_year: 2026, occupancy_pct: 72, adr_usd: 300, revpar_usd: 216, revpar_growth_pct: null, page: 4 },
          { year_index: 2, calendar_year: 2027, occupancy_pct: 73, adr_usd: 312, revpar_usd: null, revpar_growth_pct: null, page: 5 },
          // Beyond the model horizon — never shown.
          { year_index: 3, calendar_year: 2028, occupancy_pct: 74, adr_usd: 320, revpar_usd: 236.8, revpar_growth_pct: null, page: 5 },
        ],
      },
    });
    expect(r.series.adr[6]).toBeNull(); // 2025
    expect(r.series.adr[7]).toBe(300); // 2026
    expect(r.series.occupancy[7]).toBeCloseTo(0.72, 9);
    expect(r.series.revpar[8]).toBeCloseTo(0.73 * 312, 6); // derived occ × ADR when RevPAR is absent
    expect(r.sources[7]?.kind).toBe('cbre_forecast');
    expect(r.sources[7]?.detail).toMatch(/forecast year 1 = 2026, Miami Beach — Horizons\.xlsx, p\.4/);
    expect(r.forecastProvenance).toBe('CBRE Horizons — Miami Beach (Horizons.xlsx, p.4, 5)');
    expect(r.forecastReason).toBeNull();
    expect(r.series.adr).toHaveLength(9);
  });

  it('refuses to place CBRE years without a calendar year', () => {
    const r = buildMarketSeries(cols2025, {
      deal_id: 'd',
      cbre_horizons: { years: [{ year_index: 1, occupancy_pct: 72, adr_usd: 300, revpar_usd: 216, revpar_growth_pct: null }] },
    });
    expect(r.series.adr.every((v) => v == null)).toBe(true);
    expect(r.forecastReason).toBe(MARKET_FORECAST_UNPLACED_REASON);
    expect(r.forecastProvenance).toBeNull();
  });

  it('with no CBRE report the forecast half is blank with the reason', () => {
    const r = buildMarketSeries(cols2025, { deal_id: 'd' });
    expect(r.forecastReason).toBe(MARKET_FORECAST_NONE_REASON);
  });

  it('historical half: market-wide actuals by calendar year win over a segment; TTM anchors the last column', () => {
    const r = buildMarketSeries(cols2025, {
      deal_id: 'd',
      market_history: [
        { calendar_year: 2023, period: 'annual', segment: null, occupancy_pct: 0.7, adr_usd: 231, doc_type: 'MARKET_STUDY', doc_name: 'Study.pdf', page: 12, fields: ['market_study.trend.2023.adr_usd'] },
        { calendar_year: 2023, period: 'annual', segment: 'luxury', occupancy_pct: 0.8, adr_usd: 500, doc_type: 'STR_TREND' },
        { period: 'ttm', segment: null, occupancy_pct: 71, doc_type: 'STR_TREND', doc_name: 'STR.xlsx', fields: ['ttm_performance.market.occupancy_pct_ttm'] },
      ],
    });
    expect(r.segment).toBeNull();
    expect(r.series.adr[4]).toBe(231); // 2023, market-wide — not the luxury segment
    expect(r.series.occupancy[4]).toBeCloseTo(0.7, 9);
    expect(r.series.revpar[4]).toBeCloseTo(0.7 * 231, 6);
    expect(r.series.occupancy[5]).toBeCloseTo(0.71, 9); // TTM on the 2024 anchor
    expect(r.sources[5]?.detail).toMatch(/trailing twelve months/);
    expect(r.sources[4]?.detail).toMatch(/Study\.pdf, p\.12/);
  });

  it('with only segment rows, reads the single segment and names it', () => {
    const r = buildMarketSeries(cols2025, {
      deal_id: 'd',
      market_history: [{ calendar_year: 2024, period: 'annual', segment: 'upper_upscale', adr_usd: 240, doc_type: 'STR_TREND' }],
    });
    expect(r.segment).toBe('upper_upscale');
    expect(r.series.adr[5]).toBe(240);
    expect(r.sources[5]?.detail).toMatch(/upper upscale segment actual, 2024/);
  });

  it('a reported actual in a historical column is never overwritten by a CBRE forecast for that year', () => {
    const r = buildMarketSeries(cols2025, {
      deal_id: 'd',
      market_history: [{ calendar_year: 2024, period: 'annual', segment: null, adr_usd: 240, doc_type: 'MARKET_STUDY' }],
      cbre_horizons: { years: [{ year_index: 1, calendar_year: 2024, occupancy_pct: 72, adr_usd: 300, revpar_usd: 216, revpar_growth_pct: null }] },
    });
    expect(r.series.adr[5]).toBe(240);
    expect(r.sources[5]?.kind).toBe('market_actual');
  });
});

function baselineOf(rows: { fiscal_year: number; occupancy: number | null; adr: number | null }[]): HistoricalBaselineResponse {
  return { deal_id: 'd', years: rows.map((r) => ({ ...r, revpar: null })), gaps: [], look_back_years: 5, coverage_pct: 1, walk: [] } as unknown as HistoricalBaselineResponse;
}
function outputsOf(years: { occupancy: number; adr: number }[]): EngineOutputsResponse {
  return {
    deal_id: 'd',
    engines: { revenue: { outputs: { years: years.map((y, i) => ({ year: i + 1, ...y, revpar: y.occupancy * y.adr })) } } },
  } as unknown as EngineOutputsResponse;
}

describe('buildSubjectSeries (FON-95 — STR annual before P&L fiscal year)', () => {
  it('fills history from STR annual first, P&L fiscal year second, and forecast from the engine ordinal', () => {
    const r = buildSubjectSeries(
      cols2025,
      outputsOf([{ occupancy: 0.716, adr: 288 }, { occupancy: 0.72, adr: 300 }, { occupancy: 0.73, adr: 312 }]),
      baselineOf([{ fiscal_year: 2023, occupancy: 0.63, adr: 245 }, { fiscal_year: 2024, occupancy: 0.64, adr: 250 }]),
      { ...STR, doc_name: 'STR.xlsx', subject_annual: [{ calendar_year: 2024, occupancy_pct: 70.1, adr_usd: 275, page: 3 }] },
    );
    expect(r.series.adr[4]).toBe(245); // 2023 ← P&L
    expect(r.sources[4]?.kind).toBe('pnl_fiscal_year');
    expect(r.series.adr[5]).toBe(275); // 2024 ← STR annual wins
    expect(r.series.occupancy[5]).toBeCloseTo(0.701, 9);
    expect(r.sources[5]).toMatchObject({ kind: 'str_annual', label: 'STR annual' });
    expect(r.sources[5]?.detail).toMatch(/STR\.xlsx, p\.3/);
    expect(r.series.adr[6]).toBe(288); // Year 1 = 2025 ← revenue.years[0]
    expect(r.sources[6]).toMatchObject({ kind: 'engine', yearIndex: 0, label: 'Model Y1' });
    expect(r.series.adr[8]).toBe(312);
    expect(r.sources[8]).toMatchObject({ kind: 'engine', yearIndex: 2 });
    // The boundary is refused: the 2024 actual is an STR calendar year, 2025 the TTM-based Base Year.
    expect(r.boundary.comparable).toBe(false);
    if (!r.boundary.comparable) expect(r.boundary.note).toMatch(/the 2024 column is the STR calendar-year actual while 2025 is the model's Base Year/);
  });

  it('with no historical actual next to Year 1 the boundary is simply comparable (nothing to refuse)', () => {
    const r = buildSubjectSeries(cols2025, outputsOf([{ occupancy: 0.716, adr: 288 }]), null, STR);
    expect(r.series.adr[5]).toBeNull();
    expect(r.boundary.comparable).toBe(true);
  });
});

describe('computePenetration (FON-95)', () => {
  const subject = emptySeries(9);
  const comp = emptySeries(9);
  const market = emptySeries(9);
  // 2024 anchor: subject 64% / $250 / $160 vs comp 69.38% / $305.73 / $212.14; market $231.
  subject.occupancy[5] = 0.64; subject.adr[5] = 250; subject.revpar[5] = 160;
  comp.occupancy[5] = 0.716 / 1.032; comp.adr[5] = 288 / 0.942; comp.revpar[5] = 206.2 / 0.972;
  market.occupancy[5] = 0.7; market.adr[5] = 231; market.revpar[5] = 161.7;
  // 2025 forecast: subject only (comp has no forecast source), market from CBRE.
  subject.occupancy[6] = 0.716; subject.adr[6] = 288; subject.revpar[6] = 206.2;
  market.occupancy[6] = 0.72; market.adr[6] = 300; market.revpar[6] = 216;

  const base = {
    cols: cols2025, subject, comp, market,
    indices: { mpi: 103.2, ari: 94.2, rgi: 97.2 },
    subjectKeys: 132, compKeys: 61, targets: null,
  };
  const row = (rows: ReturnType<typeof computePenetration>, key: string) => rows.find((r) => r.key === key)!;

  it('fair share of supply = subject ÷ (comp + subject), constant across columns, with the inputs in the trace', () => {
    const rows = computePenetration(base);
    const fs = row(rows, 'fair-share');
    expect(fs.cells[0].value).toBeCloseTo(132 / 193, 9);
    expect(fs.cells[0].text).toBe('68.39%');
    expect(fs.cells[0].trace).toBe('subject rooms 132 ÷ (comp-set rooms 61 + subject rooms 132) = 132 ÷ 193');
    expect(fs.cells[8].text).toBe('68.39%');
  });

  it('fair-share occupancy, actual share of demand and fair-share rooms revenue — only where both sides are reported', () => {
    const rows = computePenetration(base);
    expect(row(rows, 'fair-share-occ').cells[5].value).toBeCloseTo((132 * 0.64 + 61 * (0.716 / 1.032)) / 193, 9);
    expect(row(rows, 'actual-share').cells[5].value).toBeCloseTo((132 * 0.64) / (132 * 0.64 + 61 * (0.716 / 1.032)), 9);
    const blended = (132 * 160 + 61 * (206.2 / 0.972)) / 193;
    expect(row(rows, 'fair-share-rev').cells[5].value).toBeCloseTo(132 * 366 * blended, 3);
    expect(row(rows, 'fair-share-rev').cells[5].trace).toMatch(/132 × 366 days × blended RevPAR/);
    // 2025: no comp set → "—".
    expect(row(rows, 'fair-share-occ').cells[6].text).toBe('—');
    expect(row(rows, 'actual-share').cells[6].text).toBe('—');
  });

  it('variances: occupancy in points, ADR in $ and %, RevPAR in $ — signed', () => {
    const rows = computePenetration(base);
    expect(row(rows, 'occ-var').cells[5].value).toBeCloseTo((0.64 - 0.716 / 1.032) * 100, 9);
    expect(row(rows, 'occ-var').cells[5].text).toBe('−5.4 pts');
    expect(row(rows, 'adr-var').cells[5].text).toBe('−$56');
    expect(row(rows, 'adr-var-pct').cells[5].value).toBeCloseTo(250 / (288 / 0.942) - 1, 9);
    expect(row(rows, 'adr-var-pct').cells[5].text).toBe('−18.2%');
    expect(row(rows, 'revpar-var').cells[5].text).toBe('−$52');
  });

  it('revenue opportunity at par is signed; at target uses the MPI / ARI targets', () => {
    const rows = computePenetration({ ...base, targets: { mpi: 103.2, ari: 105 } });
    const par = row(rows, 'oppty-par').cells[5];
    expect(par.value).toBeCloseTo((206.2 / 0.972 - 160) * 132 * 366, 3);
    expect(par.text).toMatch(/^\$2,5\d\d,\d{3}$/);
    const target = row(rows, 'oppty-target').cells[5];
    const targetRevpar = (0.716 / 1.032) * 1.032 * (288 / 0.942) * 1.05;
    expect(target.value).toBeCloseTo((targetRevpar - 160) * 132 * 366, 3);
    expect(target.trace).toMatch(/MPI target 103\.2/);
    expect(target.trace).toMatch(/ARI target 105\.0/);

    // A subject above par: negative, printed with the sign — not hidden.
    const above = emptySeries(9);
    above.occupancy[5] = 0.8; above.adr[5] = 400; above.revpar[5] = 320;
    const neg = row(computePenetration({ ...base, subject: above }), 'oppty-par').cells[5];
    expect(neg.value).toBeLessThan(0);
    expect(neg.text).toMatch(/^−\$/);
  });

  it('with no target the at-target row is "—" everywhere and carries the reason', () => {
    const rows = computePenetration(base);
    const t = row(rows, 'oppty-target');
    expect(t.cells.every((c) => c.text === '—')).toBe(true);
    expect(t.reason).toMatch(/No MPI \/ ARI penetration target set/);
  });

  it('FON-95 pt 2 — forecast index cells show the per-year target where set, hold the STR index elsewhere, derive RGI', () => {
    const all105 = { value: 105, source: 'scalar' as const };
    const rows = computePenetration({
      ...base,
      targets: null,
      targetsByYear: { mpi: [null, { value: 115, source: 'by_year' }, null], ari: [all105, all105, all105] },
    });
    const mpi = row(rows, 'mpi');
    // Year 1 (2025): no MPI target → the published index held flat, as before.
    expect(mpi.cells[6].text).toBe('103.2');
    expect(mpi.cells[6].trace).toMatch(/held flat/);
    // Year 2 (2026): the by-year target, named as such.
    expect(mpi.cells[7].text).toBe('115.0');
    expect(mpi.cells[7].trace).toMatch(/MPI target 115\.0 \(set for Year 2\)/);
    expect(mpi.cells[7].trace).toMatch(/drives Year 2 occupancy/);
    // Historical columns never read a target.
    expect(mpi.cells[5].text).toBe('103.2');
    expect(mpi.cells[4].text).toBe('—');
    const ari = row(rows, 'ari');
    expect(ari.cells[6].text).toBe('105.0');
    expect(ari.cells[6].trace).toMatch(/all-years target/);
    // RGI is derived from the two targets — never an input; held flat where only one exists.
    const rgi = row(rows, 'rgi');
    expect(rgi.cells[7].value).toBeCloseTo((115 * 105) / 100, 9);
    expect(rgi.cells[7].text).toBe('120.8');
    expect(rgi.cells[7].trace).toMatch(/RGI derived = MPI target 115\.0 \(set for Year 2\) × ARI target 105\.0 \(all-years target\) ÷ 100/);
    expect(rgi.cells[6].text).toBe('97.2');
    expect(rgi.cells[6].trace).toMatch(/held flat/);
  });

  it('FON-95 pt 2 — revenue opportunity at target is priced per year against that year’s target', () => {
    const comp2 = emptySeries(9);
    comp2.occupancy[5] = comp.occupancy[5]; comp2.adr[5] = comp.adr[5]; comp2.revpar[5] = comp.revpar[5];
    comp2.occupancy[7] = 0.7; comp2.adr[7] = 310; comp2.revpar[7] = 217;
    const subj2 = emptySeries(9);
    subj2.occupancy[5] = 0.64; subj2.adr[5] = 250; subj2.revpar[5] = 160;
    subj2.occupancy[7] = 0.75; subj2.adr[7] = 280; subj2.revpar[7] = 210;
    const rows = computePenetration({
      ...base,
      subject: subj2,
      comp: comp2,
      targets: { mpi: 103.2, ari: 105 },
      targetsByYear: {
        mpi: [null, { value: 115, source: 'by_year' }, null],
        ari: [null, { value: 100, source: 'by_year' }, null],
      },
    });
    const t = row(rows, 'oppty-target');
    // Year 2: by-year MPI 115 / ARI 100 on the forecast 365-day year.
    const targetRevpar = 0.7 * 1.15 * 310 * 1.0;
    expect(t.cells[7].value).toBeCloseTo((targetRevpar - 210) * 132 * 365, 3);
    expect(t.cells[7].trace).toMatch(/MPI target 115\.0, set for this year/);
    expect(t.cells[7].trace).toMatch(/ARI target 100\.0, set for this year/);
    // The historical anchor still prices at the all-years figures.
    expect(t.cells[5].trace).toMatch(/MPI target 103\.2, all-years/);
    expect(t.reason).toBeNull();
    // Without any target at all the reason names both routes.
    expect(row(computePenetration({ ...base, targets: null, targetsByYear: { mpi: [null], ari: [null] } }), 'oppty-target').reason)
      .toMatch(/all years, or by year/);
  });

  it('FON-95 pt 2 — targetForColumn: by-year for a forecast column, else the all-years scalar', () => {
    const hist = cols2025.columns[5];
    const y2 = cols2025.columns[7];
    const byYear = { mpi: [null, { value: 115, source: 'by_year' as const }, null], ari: [null, null, null] };
    expect(targetForColumn(y2, 'mpi', { mpi: 103.2, ari: null }, byYear)).toEqual({ value: 115, source: 'by_year' });
    expect(targetForColumn(y2, 'ari', { mpi: 103.2, ari: 105 }, byYear)).toEqual({ value: 105, source: 'scalar' });
    expect(targetForColumn(hist, 'mpi', { mpi: 103.2, ari: null }, byYear)).toEqual({ value: 103.2, source: 'scalar' });
    expect(targetForColumn(y2, 'ari', null, byYear)).toBeNull();
    // The worker's per-year effective scalar keeps its "all-years" label.
    const scalarYear = { mpi: [null, { value: 110, source: 'scalar' as const }], ari: [null, null] };
    expect(targetForColumn(y2, 'mpi', null, scalarYear)).toEqual({ value: 110, source: 'scalar' });
  });

  it('indices vs the comp set are the STR-published values held flat where the subject is projected; vs market computed', () => {
    const rows = computePenetration(base);
    const mpi = row(rows, 'mpi');
    expect(mpi.cells[4].text).toBe('—'); // no prior-year comp set
    expect(mpi.cells[5].text).toBe('103.2');
    expect(mpi.cells[6].text).toBe('103.2');
    expect(mpi.cells[6].trace).toMatch(/held flat/);
    expect(mpi.cells[8].text).toBe('—'); // no subject projection in that column
    const vsMarket = row(rows, 'ari-market');
    expect(vsMarket.cells[5].value).toBeCloseTo((250 / 231) * 100, 9);
    expect(vsMarket.cells[6].value).toBeCloseTo((288 / 300) * 100, 9);
    expect(vsMarket.cells[7].text).toBe('—');
  });

  it('unknown room counts: every share row is "—" with the reason, never a zero', () => {
    const rows = computePenetration({ ...base, compKeys: null });
    for (const key of ['fair-share', 'fair-share-occ', 'actual-share', 'fair-share-rev']) {
      const r = row(rows, key);
      expect(r.cells.every((c) => c.text === '—')).toBe(true);
      expect(r.reason).toMatch(/Comp-set room count unknown/);
    }
  });
});

describe('compSetKeys — active roster keys only', () => {
  it('excludes a hotel the worker flags closed', () => {
    expect(
      compSetKeys({
        compset: [
          { name: 'The Betsy', keys: 130, status: 'active' },
          { name: 'Blue Moon Hotel', keys: 75, status: 'closed' },
          { name: 'The Tony', keys: 214 },
        ],
        total_keys: 419,
      }),
    ).toBe(344);
  });
  it('falls back to the rollup only when the roster yields nothing', () => {
    expect(compSetKeys({ compset: [], total_keys: 419 })).toBe(419);
    expect(compSetKeys({ compset: [{ name: 'X', keys: 0 }], total_keys: null })).toBeNull();
    expect(compSetKeys(null)).toBeNull();
  });
});
