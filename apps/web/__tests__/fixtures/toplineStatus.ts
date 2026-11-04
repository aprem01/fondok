/**
 * A `topline_status` block in the worker's exact shape
 * (`apps/worker/app/services/topline.py::_status` + the loader's
 * `data_policy` / `calendar_anchor` / `revenue_basis`). Values are the
 * documents-fixture shape (STR comp set, CoStar forecast) — test inputs only.
 */
import type { ToplineStatus, ToplineMetricGrowth } from '@/lib/api';
import type { DealYearSpan } from '@/lib/dealYears';

/** FON-121 — a Jan–Dec deal year (a 31 Dec close): calendar year ``cal``. */
export const calSpan = (cal: number, year = cal - 2025): DealYearSpan => ({
  year,
  start: `${cal}-01-01`,
  end: `${cal}-12-31`,
  label: `Jan–Dec ${cal}`,
  calendar_months: [{ calendar_year: cal, months: 12 }],
  days: cal % 4 === 0 ? 366 : 365,
});

const g = (value: number | null, source: string, label: string, extra: Partial<ToplineMetricGrowth> = {}): ToplineMetricGrowth => ({
  value,
  source,
  source_id: source,
  label,
  doc_name: source === 'costar_submarket_forecast' ? 'CoStar Miami.pdf' : null,
  page: source === 'costar_submarket_forecast' ? 26 : null,
  report_date: null,
  bucket: source === 'costar_submarket_forecast' ? 'luxury_upper_upscale' : null,
  bucket_label: source === 'costar_submarket_forecast' ? 'Luxury & Upper Upscale' : null,
  reason: null,
  ...extra,
});

export function toplineStatus(over: Partial<ToplineStatus> = {}): ToplineStatus {
  return {
    methodology_version: 4,
    benchmark: null,
    benchmark_level: 'comp_set',
    benchmark_label: 'STR comp set TTM May 2025 · ANG-20250500-USD-E.xlsx',
    benchmark_fallback_reason: null,
    benchmark_by_year: [
      { year: 1, deal_year: calSpan(2026), occupancy: 0.755, adr: 304.56, revpar: 229.94, basis: 'forecast', level: 'comp_set', label: 'STR comp set TTM May 2025 · 2026: -2.0% (CoStar forecast)' },
      { year: 2, deal_year: calSpan(2027), occupancy: 0.751, adr: 310.0, revpar: 232.81, basis: 'forecast', level: 'comp_set', label: '2027: -0.6% (CoStar forecast)' },
      { year: 3, deal_year: calSpan(2028), occupancy: 0.751, adr: 316.2, revpar: 237.47, basis: 'assumption', level: 'comp_set', label: '2028: carried forward' },
    ],
    class_bucket: 'luxury_upper_upscale',
    bucket_label: 'Luxury & Upper Upscale',
    bucket_note: null,
    calendar_known: true,
    growth_state: 'sourced',
    growth_by_year: [
      { year: 1, deal_year: calSpan(2026), applied: true, occupancy: g(0, 'costar_submarket_forecast', '2026: 0.0% (CoStar forecast)'), adr: g(-0.02, 'costar_submarket_forecast', '2026: -2.0% (CoStar forecast)') },
      { year: 2, deal_year: calSpan(2027), applied: true, occupancy: g(-0.006, 'costar_submarket_forecast', '2027: -0.6% (CoStar forecast)'), adr: g(0.018, 'costar_submarket_forecast', '2027: +1.8% (CoStar forecast)') },
      { year: 3, deal_year: calSpan(2028), applied: true, occupancy: g(0, 'carried_forward', 'occupancy held at its 2027 level'), adr: g(0.02, 'carried_forward', '2028: +2.0% (carried forward)') },
    ],
    stabilization_year: 3,
    stabilization_source: 'default_year_3',
    penetration_occ: 100,
    penetration_adr: 100,
    penetration_occ_source: 'default',
    penetration_adr_source: 'default',
    penetration_occ_by_year: [],
    penetration_adr_by_year: [],
    current_mpi: { value: 109.8, label: 'Current MPI 109.8 (STR TTM May 2025)', basis: 'str_published' },
    current_ari: { value: 75.1, label: 'Current ARI 75.1 (STR TTM May 2025)', basis: 'str_published' },
    ramp_by_year: [0.85, 0.95],
    ramp_source: 'default',
    ramp_state: 'defined',
    explicit_metrics: [],
    t12_reference: { occupancy: 0.833, adr: 236.09, revpar: 196.66, period_label: 'T-12 Mar 2025', doc_name: 'T12.xlsx' },
    terminal_revpar_growth: { value: 0.02, source_id: 'costar_submarket_forecast', basis: 'exit_year_adr_growth', label: 'Year-3 ADR growth 2.0%' },
    suggestions: [],
    legacy_flags: [],
    problems: [],
    notes: [],
    reextract_needed: [],
    data_policy: { costar_derived_defaults: 'allow', costar_licensed_defaults: 'withhold' },
    calendar_anchor: { source: 'acquisition_close', date: '2025-12-31', assumed: false, year1: calSpan(2026), label: 'Model calendar from the acquisition close 2025-12-31: Year 1 = Jan–Dec 2026 (each model year is the twelve months from the close).' },
    revenue_basis: { state: 'present', missing: [], message: null },
    ...over,
  };
}

export const SUGGESTION_45 = {
  key: 'revpar_growth',
  value: 0.045,
  label: 'Apply platform suggestion 4.5% RevPAR growth (labelled; not a market forecast)',
  note_prefill: 'Platform suggestion 4.5% — no market forecast on file',
};

export const unavailableGrowth = (): Partial<ToplineStatus> => ({
  growth_state: 'unavailable',
  growth_by_year: [1, 2, 3].map((year) => ({
    year,
    deal_year: null,
    applied: true,
    occupancy: g(null, 'unavailable', 'held flat — no figure', { reason: 'no_market_forecast' }),
    adr: g(null, 'unavailable', 'held flat — no figure', { reason: 'no_market_forecast' }),
  })),
  suggestions: [SUGGESTION_45],
});

/**
 * FON-116 staged rollout — the GROWTH-ONLY `topline_status`
 * (`services/topline.py::_growth_only_status`): Year 1 is the T-12, the
 * benchmark / penetration / ramp keys are published empty and not in use.
 */
export function growthOnlyStatus(over: Partial<ToplineStatus> = {}): ToplineStatus {
  return toplineStatus({
    topline_mode: 'growth_only',
    benchmark: null,
    benchmark_level: 'none',
    benchmark_label:
      "Not in use — growth-only topline: Year 1 is the subject's own rates; the benchmark × penetration × ramp method is not switched on in this workspace",
    benchmark_fallback_reason: null,
    benchmark_by_year: [],
    growth_by_year: [
      { year: 1, deal_year: calSpan(2026), applied: false, occupancy: g(-0.012, 'costar_submarket_forecast', '2026: -1.2% (CoStar forecast)'), adr: g(0.018, 'costar_submarket_forecast', '2026: +1.8% (CoStar forecast)') },
      { year: 2, deal_year: calSpan(2027), applied: true, occupancy: g(0, 'costar_submarket_forecast', '2027: 0.0% (CoStar forecast)'), adr: g(-0.006, 'costar_submarket_forecast', '2027: -0.6% (CoStar forecast)') },
      { year: 3, deal_year: calSpan(2028), applied: true, occupancy: g(-0.012, 'costar_submarket_forecast', '2028: -1.2% (CoStar forecast)'), adr: g(0.038, 'costar_submarket_forecast', '2028: +3.8% (CoStar forecast)') },
      { year: 4, deal_year: calSpan(2029), applied: true, occupancy: g(0, 'held_at_stabilization', 'Assumption · occupancy held at its Year 3 (Jan–Dec 2028) stabilized level — occupancy is flat after stabilization; ADR keeps growing'), adr: g(0.028, 'costar_submarket_forecast', '2029: +2.8% (CoStar forecast)') },
    ],
    penetration_occ: null,
    penetration_adr: null,
    penetration_occ_source: 'not_applied_growth_only',
    penetration_adr_source: 'not_applied_growth_only',
    current_mpi: { value: null, label: 'Not in use', basis: null },
    current_ari: { value: null, label: 'Not in use', basis: null },
    ramp_by_year: [],
    ramp_source: 'not_applied_growth_only',
    ramp_state: 'not_applied_growth_only',
    year1_sources: { occupancy: 't12_actual', adr: 't12_actual' },
    ...over,
  });
}
