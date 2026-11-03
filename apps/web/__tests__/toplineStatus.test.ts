/**
 * FON-116 / FON-117 — the topline status, worded (plan §4.2, §6).
 *
 * Every sentence here is built from the worker's `topline_status` block; the
 * browser adds tone and arrangement only — never a number of its own.
 */
import { describe, it, expect } from 'vitest';
import {
  calendarAnchorNote,
  clearLegacyKeysPatch,
  growthBanner,
  legacyKeysToClear,
  rampSummary,
  reextractNotice,
  revenueBasisRefusal,
  toplineBasisClauses,
  toplineMigrationNotice,
  growthPercentList,
  parseGrowthPercentList,
  parseRampPercentList,
  yearRateBadge,
  toplineReferenceRows,
  toplineYearChain,
  type ToplineYearRecord,
} from '@/lib/topline';
import type { ToplineStatus } from '@/lib/api';
import engineRun from './fixtures/engine/toplineRun.json';
import { toplineStatus, unavailableGrowth, SUGGESTION_45 } from './fixtures/toplineStatus';

describe('toplineBasisClauses — the one-line basis', () => {
  it('names the benchmark, the growth source with its class, the stabilized year and the ramp', () => {
    const c = toplineBasisClauses(toplineStatus());
    expect(c.map((x) => x.id)).toEqual(['benchmark', 'growth', 'stabilization', 'ramp']);
    expect(c[0].text).toBe('Benchmark: STR comp set TTM May 2025 · ANG-20250500-USD-E.xlsx');
    expect(c[0].tone).toBe('ok');
    expect(c[1].text).toBe('Growth: CoStar submarket forecast (Luxury & Upper Upscale) · past the forecast (the report’s forecast average for ADR; occupancy held)');
    expect(c[1].tone).toBe('ok');
    expect(c[2].text).toBe('Stabilized Year 3 (Jan–Dec 2028; default) — occupancy flat after it');
    expect(c[3].text).toBe('Ramp 85/95/100 (default)');
  });

  it('a fallback level is warn-toned and carries the worker’s reason', () => {
    const c = toplineBasisClauses(
      toplineStatus({
        benchmark_level: 'submarket',
        benchmark_label: 'CoStar submarket 2025 · Luxury & Upper Upscale',
        benchmark_fallback_reason: 'Competitive set: no STR report on this deal',
      }),
    );
    expect(c[0].tone).toBe('warn');
    expect(c[0].text).toBe('Benchmark: CoStar submarket 2025 · Luxury & Upper Upscale — Competitive set: no STR report on this deal');
  });

  it('no benchmark says so, with the reason, never a level', () => {
    const c = toplineBasisClauses(
      toplineStatus({ benchmark_level: 'none', benchmark_label: '', benchmark_fallback_reason: 'Competitive set: no STR report; Submarket: no CoStar report' }),
    );
    expect(c[0]).toMatchObject({ tone: 'warn', text: 'No benchmark — projecting from the subject’s own rates (Competitive set: no STR report; Submarket: no CoStar report)' });
  });

  it('growth states other than sourced are warn-toned and say what the model does', () => {
    expect(toplineBasisClauses(toplineStatus(unavailableGrowth()))[1]).toMatchObject({
      tone: 'warn',
      text: 'Growth: no market forecast — rates held flat',
    });
    expect(toplineBasisClauses(toplineStatus({ growth_state: 'withheld' }))[1].text).toBe(
      'Growth: CoStar forecast withheld by this workspace’s data policy — rates held flat',
    );
    expect(toplineBasisClauses(toplineStatus({ growth_state: 'override' }))[1]).toMatchObject({ tone: 'ok', text: 'Growth: your override' });
    expect(toplineBasisClauses(toplineStatus({ growth_state: 'partially_sourced' }))[1].tone).toBe('warn');
  });

  it('an analyst-set stabilized year and ramp say so', () => {
    const c = toplineBasisClauses(
      toplineStatus({ stabilization_year: 4, stabilization_source: 'analyst_override', ramp_by_year: [0.8, 0.9, 0.95], ramp_source: 'analyst_override' }),
    );
    expect(c[2].text).toBe('Stabilized Year 4 (your override) — occupancy flat after it');
    expect(c[3].text).toBe('Ramp 80/90/95/100 (your ramp)');
  });

  it('null status → no clauses (an older worker prints nothing)', () => {
    expect(toplineBasisClauses(null)).toEqual([]);
  });
});

describe('rampSummary — whichever ramp the worker applied, and why', () => {
  it('a ramp the policy does not apply is named with the policy, not as a default', () => {
    expect(rampSummary(toplineStatus({ ramp_by_year: [], ramp_source: 'not_applied_by_policy', ramp_policy: 'repositioning_only' }))).toEqual({
      text: 'No ramp — the default ramp does not apply to this deal (ramp policy: repositioning only)',
      tone: 'ok',
    });
  });
  it('S ≥ 4 with the default ramp asks for the missing years', () => {
    expect(rampSummary(toplineStatus({ stabilization_year: 5, ramp_state: 'undefined_for_stabilization_year' }))).toEqual({
      text: 'Ramp: enter the ramp for Years 3–4 (the default covers Years 1–2 only; those years run at 100%)',
      tone: 'warn',
    });
  });
  it('stabilization after the hold is a warning', () => {
    expect(rampSummary(toplineStatus({ stabilization_year: 7, ramp_state: 'stabilization_after_hold' })).tone).toBe('warn');
  });
  it('Year-1 stabilization has no ramp', () => {
    expect(rampSummary(toplineStatus({ stabilization_year: 1, ramp_by_year: [] })).text).toBe('No ramp — stabilized from Year 1');
  });
  it('a ramp year left blank reads 100', () => {
    expect(rampSummary(toplineStatus({ ramp_by_year: [0.85, null], ramp_source: 'analyst_override' })).text).toBe('Ramp 85/100/100 (your ramp)');
  });
});

describe('growthBanner — "No market forecast — set growth manually"', () => {
  it('is absent when growth is sourced', () => {
    expect(growthBanner(toplineStatus())).toBeNull();
  });
  it('unavailable: names the state and hands over the labelled 4.5% suggestion', () => {
    const b = growthBanner(toplineStatus(unavailableGrowth()));
    expect(b).toMatchObject({ state: 'unavailable', title: 'No market forecast — set growth manually' });
    expect(b!.suggestion).toEqual(SUGGESTION_45);
    expect(b!.detail).toContain('Occupancy and ADR are held flat');
  });
  it('withheld: names the data policy', () => {
    const b = growthBanner(toplineStatus({ ...unavailableGrowth(), growth_state: 'withheld' }));
    expect(b!.title).toBe('CoStar forecast on file but not applied under this workspace’s data policy — set growth manually');
  });
  it('partially sourced: lists the years with no figure', () => {
    const base = toplineStatus();
    const rows = base.growth_by_year.map((r) =>
      r.year === 3 ? { ...r, adr: { ...r.adr, value: null, source: 'unavailable', label: 'held flat — no figure' } } : r,
    );
    const b = growthBanner(toplineStatus({ growth_state: 'partially_sourced', growth_by_year: rows }));
    expect(b!.title).toBe('The market forecast does not cover every year — set the rest manually');
    expect(b!.detail).toBe('No figure for: Year 3 (Jan–Dec 2028) ADR. Those years are held flat.');
  });
  it('no suggestion is invented when the worker sends none', () => {
    expect(growthBanner(toplineStatus({ ...unavailableGrowth(), suggestions: [] }))!.suggestion).toBeNull();
  });
});

describe('calendar_unknown — a forecast on file that no calendar places (R2-I1)', () => {
  const calUnknown = () =>
    toplineStatus({
      ...unavailableGrowth(),
      calendar_known: false,
      calendar_unknown: true,
      calendar_anchor: { source: null, date: null, assumed: false, year1: null, label: 'x' },
    });
  it('the banner asks for the close date first, the suggestion stays second', () => {
    const b = growthBanner(calUnknown());
    expect(b).toMatchObject({
      state: 'calendar_unknown',
      title: 'Set the acquisition close date to place the market forecast',
      askCloseDate: true,
    });
    expect(b!.suggestion).toEqual(SUGGESTION_45);
  });
  it('the basis line says the forecast is unplaced, not missing', () => {
    expect(toplineBasisClauses(calUnknown())[1]).toMatchObject({
      tone: 'warn',
      text: 'Growth: market forecast on file but not placed — set the acquisition close date',
    });
  });
  it('an ordinary unavailable state never asks for a date', () => {
    expect(growthBanner(toplineStatus(unavailableGrowth()))!.askCloseDate).toBe(false);
  });
});

describe('reextractNotice — v8 CoStar extractions', () => {
  it('says re-extract, with the worker’s lines', () => {
    const lines = ['CoStar report on file (Miami.pdf) but extracted before class forecasts were read — re-extract it to read the Luxury & Upper Upscale series.'];
    expect(reextractNotice(toplineStatus({ reextract_needed: lines }))).toEqual({
      title: 'CoStar report extracted before class forecasts — re-extract',
      lines,
    });
    expect(reextractNotice(toplineStatus())).toBeNull();
  });
});

describe('calendarAnchorNote — which anchor placed the forecast', () => {
  it('the close date: stated, no ask', () => {
    expect(calendarAnchorNote(toplineStatus())).toEqual({
      source: 'acquisition_close',
      text: 'Forecast placed from the acquisition close (2025-12-31) — Year 1 = Jan–Dec 2026',
      assumed: false,
      askCloseDate: false,
    });
  });
  it('the as-of date: assumed, so it asks for the close date', () => {
    const n = calendarAnchorNote(toplineStatus({ calendar_anchor: { source: 'underwriting_as_of', date: '2025-06-30', assumed: true, year1: { year: 1, start: '2025-07-01', end: '2026-06-30', label: 'Jul 2025–Jun 2026', days: 365 }, label: 'x' } }));
    expect(n).toMatchObject({ assumed: true, askCloseDate: true, text: 'Forecast placed from the underwriting as-of date (2025-06-30) — Year 1 = Jul 2025–Jun 2026 (assumed: no close date)' });
  });
  it('a status from before deal years still names its Year 1 (review M8)', () => {
    const n = calendarAnchorNote(toplineStatus({ calendar_anchor: { source: 'acquisition_close', date: '2025-09-30', assumed: false, year1: null, first_model_year: 2025, label: 'x' } }));
    expect(n?.text).toBe('Forecast placed from the acquisition close (2025-09-30) — Year 1 = 2025');
  });
  it('no anchor → nothing', () => {
    expect(calendarAnchorNote(toplineStatus({ calendar_anchor: { source: null, date: null, assumed: false, year1: null, label: 'x' } }))).toBeNull();
    expect(calendarAnchorNote(toplineStatus({ calendar_anchor: null }))).toBeNull();
  });
});

describe('toplineMigrationNotice — runs that predate topline v2', () => {
  it('a revenue run with no version, or v1, predates it', () => {
    expect(toplineMigrationNotice({ hasRevenueRun: true, version: undefined })).toBe(
      'The topline method changed; this run predates it — re-run, then compare runs.',
    );
    expect(toplineMigrationNotice({ hasRevenueRun: true, version: 1 })).not.toBeNull();
  });
  it('v3 (deal years, FON-121) or no run at all → nothing; a v2 run predates deal years', () => {
    expect(toplineMigrationNotice({ hasRevenueRun: true, version: 3 })).toBeNull();
    expect(toplineMigrationNotice({ hasRevenueRun: true, version: 2 })).not.toBeNull();
    expect(toplineMigrationNotice({ hasRevenueRun: false, version: undefined })).toBeNull();
  });
});

describe('legacy keys — translated with reasons, cleared in one audited PATCH', () => {
  const flags = [
    { key: 'revenue_seed_from_str_forecast', value: true, treatment: 'ignored', reason: 'legacy_key_translated', detail: 'ignored' },
    { key: 'index_market_adr_growth', value: 0.03, treatment: 'read_as_all_years_growth', reason: 'legacy_key_translated', detail: 'read' },
    { key: 'starting_occupancy', value: 0.7, treatment: 'treated_as_legacy_seed', reason: 'legacy_key_translated', detail: 'seed' },
    { key: 'starting_adr', value: 300, treatment: 'kept_no_benchmark', reason: 'legacy_key_translated', detail: 'kept' },
    { key: 'index_methodology', value: 'market_benchmark', treatment: 'read_as_market_level', reason: 'legacy_key_translated', detail: 'level' },
  ];
  it('every flagged key is cleared except a legacy Year-1 rate the deal still runs on', () => {
    expect(legacyKeysToClear(toplineStatus({ legacy_flags: flags }))).toEqual([
      'revenue_seed_from_str_forecast',
      'index_market_adr_growth',
      'starting_occupancy',
      'index_methodology',
    ]);
  });
  it('the patch nulls exactly those keys', () => {
    expect(clearLegacyKeysPatch(toplineStatus({ legacy_flags: flags }))).toEqual({
      revenue_seed_from_str_forecast: null,
      index_market_adr_growth: null,
      starting_occupancy: null,
      index_methodology: null,
    });
  });
});

describe('revenueBasisRefusal — no Year-1 basis (review I-5)', () => {
  const message =
    'The deal has no Year-1 occupancy or Year-1 ADR basis — no T-12, no STR or market benchmark and no analyst value sets it. Upload a T-12 or STR report, or enter Year-1 occupancy and ADR on the Future P&L, and run the model again — the model never substitutes a placeholder rate.';
  it('reads the status block first', () => {
    expect(
      revenueBasisRefusal(toplineStatus({ revenue_basis: { state: 'missing', missing: ['starting_adr', 'starting_occupancy'], message } }), null),
    ).toEqual({ message, missing: ['starting_adr', 'starting_occupancy'] });
  });
  it('else the revenue engine’s typed failure', () => {
    expect(
      revenueBasisRefusal(null, [{ engine: 'revenue', reason: 'no_revenue_basis', message, missing: ['starting_adr'] }]),
    ).toEqual({ message, missing: ['starting_adr'] });
  });
  it('present / other failures → null', () => {
    expect(revenueBasisRefusal(toplineStatus(), [{ engine: 'debt', reason: 'engine_error', message: 'x' }])).toBeNull();
  });
});

describe('per-year list editors — percent text ⇄ the worker’s lists', () => {
  it('growth: blanks are null (unset), percents become fractions', () => {
    expect(parseGrowthPercentList(['2', '', '-1.5'])).toEqual({ ok: true, value: [0.02, null, -0.015] });
    expect(parseGrowthPercentList(['', ''])).toEqual({ ok: true, value: null });
    expect(parseGrowthPercentList(['60'])).toMatchObject({ ok: false });
    expect(parseGrowthPercentList(['abc'])).toMatchObject({ ok: false });
  });
  it('ramp: 0 < r ≤ 150%', () => {
    expect(parseRampPercentList(['85', '95'])).toEqual({ ok: true, value: [0.85, 0.95] });
    expect(parseRampPercentList(['0'])).toMatchObject({ ok: false });
    expect(parseRampPercentList(['', ''])).toEqual({ ok: true, value: null });
  });
  it('a stored list renders as percent text', () => {
    expect(growthPercentList([0.02, null, -0.015], 4)).toEqual(['2', '', '-1.5', '']);
  });
});

describe('yearRateBadge / reference rows — per-column basis on Occupancy / ADR / RevPAR', () => {
  // A REAL engine run (apps/worker run_all_engines on the Kimpton documents
  // fixture: defaults 100 × 85/95/100, a Year-2 ADR penetration of 98, a
  // Year-1 displacement of 10% occupancy / 6.67% ADR) — never a hand-made
  // record: the engine's mpi_target already INCLUDES the ramp, and its
  // occupancy / ADR are the figures that ran, AFTER displacement.
  const run = engineRun as unknown as {
    revenue: { topline_by_year: ToplineYearRecord[] };
    topline_status: ToplineStatus;
  };
  const recs = run.revenue.topline_by_year;
  const st = run.topline_status;

  it('Year 1: benchmark × stabilized penetration × ramp = the pre-displacement rate, then displacement as its own step', () => {
    const c = toplineYearChain(st, recs, 0, 'occupancy')!;
    // 74.59% × 100 × 85% = 63.40%; the 10% displacement → 57.06% (what ran).
    expect(c.pre).toBeCloseTo(0.745899492 * 0.85, 9);
    expect(c.final).toBeCloseTo(recs[0].occupancy!, 12);
    expect(c.displaced).toBe(true);
    expect(c.text).toBe(
      'Benchmark 74.6% × penetration 100 × ramp 85% = 63.4% before displacement; Year-1 displacement → 57.1%',
    );
    // The ramp is applied ONCE: the engine's 85 effective MPI is not multiplied by 85% again.
    expect(c.text).not.toContain('× MPI 85');
    const adr = toplineYearChain(st, recs, 0, 'adr')!;
    expect(adr.text).toBe(
      'Benchmark $316.37 × penetration 100 × ramp 85% = $268.91 before displacement; Year-1 displacement → $250.98',
    );
  });

  it('a per-year penetration runs with NO ramp, and says it is your value', () => {
    const c = toplineYearChain(st, recs, 1, 'adr')!;
    expect(c.rampApplied).toBe(false);
    expect(c.text).toBe('Benchmark $314.47 × penetration 98 (your Year 2 value — no ramp) = $308.18');
    // …while occupancy in the same year still ramps.
    expect(toplineYearChain(st, recs, 1, 'occupancy')!.text).toBe('Benchmark 74.6% × penetration 100 × ramp 95% = 70.9%');
  });

  it('a stabilized year has no ramp and no displacement step', () => {
    expect(toplineYearChain(st, recs, 2, 'occupancy')!.text).toBe('Benchmark 73.7% × penetration 100 = 73.7%');
  });

  it('the column chip carries the benchmark year’s basis and the chain as its title', () => {
    const b = yearRateBadge(st, recs, 0, 'occupancy')!;
    expect(b.basis).toBe('forecast');
    expect(b.title).toContain('= 63.4% before displacement; Year-1 displacement → 57.1%');
    expect(yearRateBadge(st, recs, 4, 'occupancy')!.basis).toBe('assumption');
  });

  it('no chip for an explicit Year-1 value, no benchmark, or a missing year', () => {
    expect(yearRateBadge({ ...st, explicit_metrics: ['occupancy'] }, recs, 0, 'occupancy')).toBeNull();
    expect(yearRateBadge({ ...st, benchmark_level: 'none', benchmark_by_year: [] }, recs, 0, 'occupancy')).toBeNull();
    expect(yearRateBadge(st, recs, 7, 'occupancy')).toBeNull();
  });

  it('the reference rows: benchmark per year (labelled with its level) and the T-12 repeated with its period', () => {
    const r = toplineReferenceRows(toplineStatus(), 3);
    expect(r.benchmark!.label).toBe('Benchmark (competitive set)');
    expect(r.benchmark!.occupancy).toEqual([0.755, 0.751, 0.751]);
    expect(r.t12!.label).toBe('T-12 actual (reference) · T-12 Mar 2025');
    expect(r.t12!.adr).toEqual([236.09, 236.09, 236.09]);
    expect(toplineReferenceRows(toplineStatus({ t12_reference: null, benchmark_level: 'none', benchmark_by_year: [] }), 3)).toEqual({ benchmark: null, t12: null });
  });
});

describe('final review minors', () => {
  const lines = ['CoStar report on file (Miami.pdf) but extracted before class forecasts were read — re-extract it to read the Luxury & Upper Upscale series.'];

  it('M-1: a stale CoStar extraction headlines the banner as re-extract, ranked above the 4.5% chip', () => {
    const b = growthBanner(toplineStatus({ ...unavailableGrowth(), reextract_needed: lines }))!;
    expect(b.state).toBe('reextract');
    expect(b.title).toBe('CoStar report on file but extracted before class forecasts — re-extract it to read the forecast');
    expect(b.detail).toBe(lines[0]);
    expect(b.suggestion).toEqual(SUGGESTION_45);
    expect(toplineBasisClauses(toplineStatus({ ...unavailableGrowth(), reextract_needed: lines }))[1].text).toBe(
      'Growth: CoStar report on file but extracted before class forecasts — re-extract',
    );
  });

  it('M-1: calendar_unknown and withheld still outrank re-extract (the worker’s order)', () => {
    expect(growthBanner(toplineStatus({ ...unavailableGrowth(), reextract_needed: lines, calendar_unknown: true }))!.state).toBe('calendar_unknown');
    expect(growthBanner(toplineStatus({ ...unavailableGrowth(), reextract_needed: lines, growth_state: 'withheld' }))!.state).toBe('withheld');
  });

  it('M-4: the close-date anchor line prints only when a market forecast was placed on it', () => {
    expect(calendarAnchorNote(toplineStatus(unavailableGrowth()))).toBeNull();
    expect(calendarAnchorNote(toplineStatus())).not.toBeNull();
  });

  it('M-8: a bare "T-12" period is not repeated in the reference label', () => {
    const r = toplineReferenceRows(toplineStatus({ t12_reference: { occupancy: 0.8, adr: 240, revpar: 192, period_label: 'T-12', doc_name: null } }), 2);
    expect(r.t12!.label).toBe('T-12 actual (reference)');
    expect(r.t12!.title).toBe("The subject's own T-12 — shown for reference; it sets no projected year.");
  });
});

describe('FON-116 staged rollout — the growth-only topline', () => {
  it('the basis line names the Year-1 source and the growth, never a benchmark or a ramp', async () => {
    const { growthOnlyStatus } = await import('./fixtures/toplineStatus');
    const s = growthOnlyStatus();
    const c = toplineBasisClauses(s);
    expect(c.map((x) => x.id)).toEqual(['year1', 'growth', 'stabilization']);
    expect(c[0].text).toBe('Year 1: T-12 actual');
    expect(c[0].tone).toBe('ok');
    expect(c[1].text).toBe('Growth: CoStar submarket forecast (Luxury & Upper Upscale) · occupancy flat after stabilization');
    expect(c[2].text).toBe('Stabilized Year 3 (Jan–Dec 2028; default) — occupancy flat after it');
    expect(JSON.stringify(c)).not.toMatch(/Benchmark:|Ramp|penetration 100/);
  });

  it('mixed Year-1 sources are named per metric; none at all asks for the rates', async () => {
    const { growthOnlyStatus } = await import('./fixtures/toplineStatus');
    const mixed = toplineBasisClauses(growthOnlyStatus({ year1_sources: { occupancy: 'analyst_override', adr: 'cbre_horizons' } }));
    expect(mixed[0].text).toMatch(/^Year 1: occupancy .+ · ADR .+$/);
    const none = toplineBasisClauses(growthOnlyStatus({ year1_sources: { occupancy: null, adr: null } }));
    expect(none[0]).toMatchObject({ tone: 'warn', text: 'Year 1: no rates — enter Year-1 occupancy and ADR' });
  });

  it('no per-year benchmark chain and no reference rows (the T-12 is the basis, not a reference)', async () => {
    const { growthOnlyStatus } = await import('./fixtures/toplineStatus');
    const s = growthOnlyStatus();
    expect(toplineReferenceRows(s, 5)).toEqual({ benchmark: null, t12: null });
    expect(yearRateBadge(s, [], 0, 'occupancy')).toBeNull();
    expect(toplineYearChain(s, [], 0, 'adr')).toBeNull();
  });

  it('the growth banner and the migration notice work as in the benchmark topline', async () => {
    const { growthOnlyStatus } = await import('./fixtures/toplineStatus');
    expect(growthBanner(growthOnlyStatus())).toBeNull();
    expect(growthBanner(growthOnlyStatus({ growth_state: 'unavailable', suggestions: [SUGGESTION_45] }))?.suggestion).toEqual(SUGGESTION_45);
    expect(toplineMigrationNotice({ hasRevenueRun: true, version: 3 })).toBeNull();
  });
});

describe('FON-116 staged rollout — a REAL growth-only engine run (fixtures/engine/toplineRunGrowthOnly.json)', () => {
  it('the worker’s own growth-only status reads as growth-only', async () => {
    const run = (await import('./fixtures/engine/toplineRunGrowthOnly.json')).default as unknown as {
      revenue: { topline_mode: string; topline_methodology_version: number; topline_by_year: ToplineYearRecord[] };
      topline_status: ToplineStatus;
    };
    const s = run.topline_status;
    expect(run.revenue.topline_mode).toBe('growth_only');
    expect(run.revenue.topline_methodology_version).toBe(3);
    const c = toplineBasisClauses(s);
    expect(c.map((x) => x.id)).toEqual(['year1', 'growth', 'stabilization']);
    expect(c[0].text).toBe('Year 1: T-12 actual');
    expect(c[1].text).toContain('occupancy flat after stabilization');
    expect(c[1].text).toContain('past the forecast');
    // No benchmark chain on any column, no reference rows.
    for (let i = 0; i < run.revenue.topline_by_year.length; i++) {
      expect(yearRateBadge(s, run.revenue.topline_by_year, i, 'occupancy')).toBeNull();
    }
    expect(toplineReferenceRows(s, 5)).toEqual({ benchmark: null, t12: null });
    const y5 = s.growth_by_year.find((r) => r.year === 5)!;
    // FON-121 decision 2/3 — the report's published average, with its page chip.
    expect(y5.adr.label).toContain('CoStar forecast average (as published) +2.5%');
    expect(y5.adr.chip).toBe('CoStar p.4');
    expect(y5.deal_year?.label).toBe('Jan–Dec 2030');
    expect(y5.occupancy.source).toBe('held_at_stabilization');
  });
});

describe('FON-116 staged rollout — the retired STR switch under growth-only', () => {
  it('is still honoured by the worker but clearable (its control is gone)', async () => {
    const { growthOnlyStatus } = await import('./fixtures/toplineStatus');
    const s = growthOnlyStatus({
      legacy_flags: [
        { key: 'revenue_seed_from_str_forecast', value: true, treatment: 'applied_growth_only', reason: 'legacy_key_translated', detail: 'x' },
      ],
    } as Partial<ToplineStatus>);
    expect(legacyKeysToClear(s)).toEqual(['revenue_seed_from_str_forecast']);
    expect(clearLegacyKeysPatch(s)).toEqual({ revenue_seed_from_str_forecast: null });
  });
});
