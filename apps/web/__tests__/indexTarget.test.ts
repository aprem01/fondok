/**
 * Sam #3 (b) — one web reading of a penetration target, identical to the
 * worker's ``validate_index_target`` / ``_index_points``: a ratio 0.25–2.5 is
 * × 100, points 25–250 as is, anything else refused. The panel parser, the
 * penetration table and the at-target opportunity all go through it.
 */
import { describe, it, expect } from 'vitest';
import { INDEX_POINTS_RANGE, INDEX_RATIO_RANGE, toIndexPoints, validateIndexTarget } from '@/lib/indexTarget';
import { parseIndexPoints, fmtAssumption, figureSourceText } from '@/components/project/pl/IndexMethodology';
import {
  buildCompSeries,
  COMP_FORECAST_NONE_REASON,
  deriveIndexColumns,
  indexPoints,
  targetForColumn,
} from '@/components/project/pl/indexAnalysisModel';

describe('indexTarget — mirrors the worker bands', () => {
  it('bands match apps/worker INDEX_POINTS_RANGE / INDEX_RATIO_RANGE', () => {
    expect(INDEX_POINTS_RANGE).toEqual([25, 250]);
    expect(INDEX_RATIO_RANGE).toEqual([0.25, 2.5]);
  });

  it('reads ratios as points, points as points, and refuses the gap and beyond', () => {
    expect(toIndexPoints(1.1)).toBeCloseTo(110);
    expect(toIndexPoints('1.05')).toBeCloseTo(105);
    expect(toIndexPoints(110)).toBe(110);
    expect(toIndexPoints(25)).toBe(25);
    expect(toIndexPoints(250)).toBe(250);
    expect(toIndexPoints(0.25)).toBe(25);
    expect(toIndexPoints(2.5)).toBe(250);
    for (const bad of [5, 12, 3, 24.9, 300, 0.2, 2.6, -5, 0, null, undefined, 'x', '']) {
      expect(toIndexPoints(bad)).toBeNull();
    }
    expect(validateIndexTarget(5).reason).toMatch(/5 is outside the accepted range — index points 25–250/);
  });

  it('the panel parser, the formatter and the penetration helpers all agree', () => {
    expect(parseIndexPoints('1.10')).toBeCloseTo(110);
    expect(parseIndexPoints('')).toBeNull();
    expect(parseIndexPoints('5')).toBeUndefined(); // refused, never 5 points
    expect(parseIndexPoints('3')).toBeUndefined(); // the old "≤ 3 is a ratio" rule would have made this 300
    expect(fmtAssumption(1.1, 'index')).toBe('110.0');
    expect(fmtAssumption(110, 'index')).toBe('110.0');
    expect(fmtAssumption(5, 'index')).toBe('—');
    expect(indexPoints(1.032)).toBeCloseTo(103.2);
    const col = { key: 'f-1', kind: 'forecast' as const, calendarYear: 2025, ordinal: 1, label: '2025' };
    expect(targetForColumn(col, 'mpi', { mpi: 1.1, ari: null }, null)).toEqual({ value: expect.closeTo(110), source: 'scalar' });
    expect(targetForColumn(col, 'mpi', { mpi: 5, ari: null }, null)).toBeNull();
    expect(
      targetForColumn(col, 'mpi', null, { mpi: [{ value: 1.2, source: 'by_year' }], ari: [] }),
    ).toEqual({ value: expect.closeTo(120), source: 'by_year' });
  });
});

describe('buildCompSeries — the comp-set forecast (Sam #3 c)', () => {
  const cols = deriveIndexColumns({ calendarYears: [2025, 2026, 2027], forecastCount: 3, documentYears: [] });
  const STR = {
    subject_occupancy_pct: 0.716,
    subject_adr_usd: 288,
    subject_revpar_usd: 206.2,
    mpi_occupancy_index: 103.2,
    ari_adr_index: 94.2,
    rgi_revpar_index: 97.2,
  };
  const fig = (value: number | null, detail: string, doc?: string) => ({
    value,
    source: value == null ? null : ('computed' as const),
    inputs: doc ? [{ field_name: 'x', doc_name: doc, page: 6 }] : [],
    detail,
  });

  it('without a worker series the forecast stays "—" with the reason', () => {
    const r = buildCompSeries(cols, STR, null);
    expect(r.series.adr.slice(6).every((v) => v == null)).toBe(true);
    expect(r.forecastReason).toBe(COMP_FORECAST_NONE_REASON);
    expect(r.forecastFilled).toBe(false);
  });

  it('fills each forecast year from the grown series, names the source, and keeps the reason for blank years', () => {
    const r = buildCompSeries(cols, STR, [
      { year: 1, occupancy: fig(0.694, 'TTM occupancy'), adr: fig(305.7, 'TTM ADR') },
      { year: 2, occupancy: fig(0.69747, 'grown 1 year'), adr: fig(315.18, 'grown 1 year at +3.10%', 'Submarket.pdf') },
      { year: 3, occupancy: fig(null, 'No market occupancy growth assumption'), adr: fig(null, 'No market ADR growth assumption') },
    ]);
    expect(r.forecastFilled).toBe(true);
    expect(r.series.adr[6]).toBeCloseTo(305.7);
    expect(r.series.adr[7]).toBeCloseTo(315.18);
    expect(r.series.revpar[7]).toBeCloseTo(0.69747 * 315.18);
    expect(r.series.adr[8]).toBeNull();
    expect(r.sources[7]?.kind).toBe('comp_grown');
    expect(r.sources[7]?.label).toBe('Grown (assumption)');
    expect(r.sources[7]?.detail).toMatch(/Submarket\.pdf, p\.6/);
    expect(r.forecastReason).toMatch(/No market occupancy growth assumption/);
  });
});

describe('figureSourceText — an ignored stored target is never "Your override"', () => {
  it('names the stored value as ignored and gives the reason', () => {
    const reason =
      'Stored all-years MPI target 5 is outside 25–250 index points (or 0.25–2.5 as a ratio) — re-enter it. It is ignored, not applied.';
    const text = figureSourceText({ value: null, source: 'ignored_override', inputs: [], detail: reason });
    expect(text).toBe(`Stored override ignored — ${reason}`);
    expect(text).not.toMatch(/Your override/);
  });
  it('an active override still reads "Your override"', () => {
    expect(figureSourceText({ value: 110, source: 'override', inputs: [], detail: 'Hold share' })).toBe(
      'Your override — Hold share',
    );
  });
});
