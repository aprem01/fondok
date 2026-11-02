/**
 * A `topline_status` block in the worker's exact shape
 * (`apps/worker/app/services/topline.py::_status` + the loader's
 * `data_policy` / `calendar_anchor` / `revenue_basis`). Values are the
 * documents-fixture shape (STR comp set, CoStar forecast) — test inputs only.
 */
import type { ToplineStatus, ToplineMetricGrowth } from '@/lib/api';

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
    methodology_version: 2,
    benchmark: null,
    benchmark_level: 'comp_set',
    benchmark_label: 'STR comp set TTM May 2025 · ANG-20250500-USD-E.xlsx',
    benchmark_fallback_reason: null,
    benchmark_by_year: [
      { year: 1, calendar_year: 2026, occupancy: 0.755, adr: 304.56, revpar: 229.94, basis: 'forecast', level: 'comp_set', label: 'STR comp set TTM May 2025 · 2026: -2.0% (CoStar forecast)' },
      { year: 2, calendar_year: 2027, occupancy: 0.751, adr: 310.0, revpar: 232.81, basis: 'forecast', level: 'comp_set', label: '2027: -0.6% (CoStar forecast)' },
      { year: 3, calendar_year: 2028, occupancy: 0.751, adr: 316.2, revpar: 237.47, basis: 'assumption', level: 'comp_set', label: '2028: carried forward' },
    ],
    class_bucket: 'luxury_upper_upscale',
    bucket_label: 'Luxury & Upper Upscale',
    bucket_note: null,
    calendar_known: true,
    growth_state: 'sourced',
    growth_by_year: [
      { year: 1, calendar_year: 2026, applied: true, occupancy: g(0, 'costar_submarket_forecast', '2026: 0.0% (CoStar forecast)'), adr: g(-0.02, 'costar_submarket_forecast', '2026: -2.0% (CoStar forecast)') },
      { year: 2, calendar_year: 2027, applied: true, occupancy: g(-0.006, 'costar_submarket_forecast', '2027: -0.6% (CoStar forecast)'), adr: g(0.018, 'costar_submarket_forecast', '2027: +1.8% (CoStar forecast)') },
      { year: 3, calendar_year: 2028, applied: true, occupancy: g(0, 'carried_forward', 'occupancy held at its 2027 level'), adr: g(0.02, 'carried_forward', '2028: +2.0% (carried forward)') },
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
    ramp_policy: 'all_deals',
    ramp_state: 'defined',
    explicit_metrics: [],
    t12_reference: { occupancy: 0.833, adr: 236.09, revpar: 196.66, period_label: 'T-12 Mar 2025', doc_name: 'T12.xlsx' },
    terminal_revpar_growth: { value: 0.02, source_id: 'costar_submarket_forecast', basis: 'last_year_adr_growth', label: 'Year-3 ADR growth 2.0%' },
    suggestions: [],
    legacy_flags: [],
    problems: [],
    notes: [],
    reextract_needed: [],
    data_policy: { costar_derived_defaults: 'allow', costar_licensed_defaults: 'withhold' },
    calendar_anchor: { source: 'acquisition_close', date: '2025-12-31', assumed: false, first_model_year: 2026, label: 'Model calendar from the acquisition close 2025-12-31: Year 1 = 2026 (the calendar year of the first operating month).' },
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
    calendar_year: null,
    applied: true,
    occupancy: g(null, 'unavailable', 'held flat — no figure', { reason: 'no_market_forecast' }),
    adr: g(null, 'unavailable', 'held flat — no figure', { reason: 'no_market_forecast' }),
  })),
  suggestions: [SUGGESTION_45],
});
