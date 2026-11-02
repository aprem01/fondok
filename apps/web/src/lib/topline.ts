/**
 * FON-116 / FON-117 — the topline status, worded (plan §4.2, §6).
 *
 * The worker resolves the topline once, after every override
 * (`apps/worker/app/services/topline.py`): benchmark level × penetration ×
 * ramp, market growth by year with a first-class "unavailable", the calendar
 * anchor, legacy keys translated with their reasons, and the Year-1 revenue
 * basis. It publishes that on `assumption_sources.topline_status`.
 *
 * This module turns that block into the sentences the Future P&L, Overview and
 * Index Analysis print. It adds tone and arrangement only: every number and
 * every label comes from the worker. In particular the 4.5% RevPAR figure
 * appears here only if the worker sends it as a labelled suggestion, and the
 * ramp shown is whichever ramp the worker applied — the browser assumes no
 * default of its own.
 */

import type { EngineFailure, ToplineGrowthRow, ToplineStatus, ToplineSuggestion } from '@/lib/api';

/** The topline method this web build explains (worker `TOPLINE_METHODOLOGY_VERSION`). */
export const TOPLINE_METHODOLOGY_VERSION = 2;

/** Field-override keys the topline reads (worker `services/topline.py`). */
export const TOPLINE_KEYS = {
  level: 'topline_benchmark_level',
  occGrowthByYear: 'occupancy_growth_by_year',
  adrGrowthByYear: 'adr_growth_by_year',
  rampByYear: 'penetration_ramp_by_year',
  mpiTarget: 'index_mpi_target',
  ariTarget: 'index_ari_target',
  mpiByYear: 'index_mpi_target_by_year',
  ariByYear: 'index_ari_target_by_year',
  stabilizationYear: 'stabilization_year',
} as const;

export const BENCHMARK_LEVEL_OPTIONS: { value: 'auto' | 'comp_set' | 'submarket' | 'market'; label: string }[] = [
  { value: 'auto', label: 'Automatic (comp set, else submarket, else market)' },
  { value: 'comp_set', label: 'Competitive set' },
  { value: 'submarket', label: 'Submarket' },
  { value: 'market', label: 'Market' },
];

/** One model year's topline record (`revenue.topline_by_year[i]`, schemas-ts `IndexDriverYear`). */
export interface ToplineYearRecord {
  year: number;
  calendar_year?: number | null;
  method?: string | null;
  benchmark_level?: string | null;
  benchmark_occupancy?: number | null;
  benchmark_adr?: number | null;
  benchmark_occupancy_source?: string | null;
  benchmark_adr_source?: string | null;
  mpi_target?: number | null;
  ari_target?: number | null;
  ramp_pct?: number | null;
  ramp_state?: string | null;
  occupancy?: number | null;
  adr?: number | null;
  occupancy_status?: string | null;
  adr_status?: string | null;
  growth_label?: string | null;
  t12_occupancy?: number | null;
  t12_adr?: number | null;
  capped_95?: boolean;
  reason?: string | null;
}

export type ClauseTone = 'ok' | 'warn';

export interface ToplineClause {
  id: 'benchmark' | 'growth' | 'stabilization' | 'ramp';
  text: string;
  tone: ClauseTone;
  /** Hover text — the worker's full labels. */
  title?: string;
}

const GROWTH_SOURCE_TEXT: Record<string, string> = {
  analyst_override: 'your override',
  cbre_horizons: 'CBRE Hotel Horizons forecast',
  costar_submarket_forecast: 'CoStar submarket forecast',
  carried_forward: 'carried forward',
};

/** The growth rows the projection actually applies (Year 1 only when the benchmark is grown into it). */
function appliedRows(s: ToplineStatus): ToplineGrowthRow[] {
  return (s.growth_by_year ?? []).filter((r) => r.applied);
}

/** "CoStar submarket forecast (Luxury & Upper Upscale) · carried forward". */
function growthSourcesText(s: ToplineStatus): string {
  const seen: string[] = [];
  for (const row of appliedRows(s)) {
    for (const g of [row.occupancy, row.adr]) {
      const base = GROWTH_SOURCE_TEXT[g?.source ?? ''];
      if (!base) continue;
      const text = g.source === 'costar_submarket_forecast' && g.bucket_label ? `${base} (${g.bucket_label})` : base;
      if (!seen.includes(text)) seen.push(text);
    }
  }
  return seen.join(' · ');
}

function growthLabels(s: ToplineStatus): string {
  return appliedRows(s)
    .map((r) => `Year ${r.year}${r.calendar_year ? ` (${r.calendar_year})` : ''}: occupancy ${r.occupancy.label}; ADR ${r.adr.label}`)
    .join('\n');
}

const RAMP_POLICY_TEXT = (p: string | null | undefined): string => (p ?? 'not stated').replace(/_/g, ' ');

/** The ramp the worker APPLIED, and why — never a ramp the browser assumes. */
export function rampSummary(s: ToplineStatus): { text: string; tone: ClauseTone } {
  const S = s.stabilization_year;
  if (s.ramp_source === 'not_applied_by_policy') {
    return {
      text: `No ramp — the default ramp does not apply to this deal (ramp policy: ${RAMP_POLICY_TEXT(s.ramp_policy)})`,
      tone: 'ok',
    };
  }
  if (s.ramp_state === 'stabilization_after_hold') {
    return {
      text: `Stabilized Year ${S} is after the hold — the projection never reaches full penetration`,
      tone: 'warn',
    };
  }
  if (s.ramp_state === 'undefined_for_stabilization_year') {
    return {
      text: `Ramp: enter the ramp for Years 3–${S - 1} (the default covers Years 1–2 only; those years run at 100%)`,
      tone: 'warn',
    };
  }
  if (S <= 1 || !s.ramp_by_year?.length) {
    return { text: 'No ramp — stabilized from Year 1', tone: 'ok' };
  }
  const steps = [...s.ramp_by_year.map((r) => (r == null ? '100' : fmtPctWhole(r))), '100'];
  const who = s.ramp_source === 'analyst_override' ? 'your ramp' : 'default';
  return { text: `Ramp ${steps.join('/')} (${who})`, tone: 'ok' };
}

function fmtPctWhole(fraction: number): string {
  const v = Math.round(fraction * 1000) / 10;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

/** The Topline basis line (plan §4.2): benchmark · growth · stabilized year · ramp. */
export function toplineBasisClauses(s: ToplineStatus | null | undefined): ToplineClause[] {
  if (!s) return [];
  const out: ToplineClause[] = [];
  if (s.benchmark_level === 'none') {
    out.push({
      id: 'benchmark',
      tone: 'warn',
      text: `No benchmark — projecting from the subject’s own rates${s.benchmark_fallback_reason ? ` (${s.benchmark_fallback_reason})` : ''}`,
    });
  } else {
    out.push({
      id: 'benchmark',
      tone: s.benchmark_fallback_reason ? 'warn' : 'ok',
      text: `Benchmark: ${s.benchmark_label}${s.benchmark_fallback_reason ? ` — ${s.benchmark_fallback_reason}` : ''}`,
      title: s.bucket_note ?? undefined,
    });
  }
  const sources = growthSourcesText(s);
  const growthText: Record<string, { text: string; tone: ClauseTone }> = {
    sourced: { text: `Growth: ${sources || 'market forecast'}`, tone: 'ok' },
    override: { text: 'Growth: your override', tone: 'ok' },
    partially_sourced: {
      text: `Growth: ${sources ? `${sources} — ` : ''}some years have no market forecast (held flat)`,
      tone: 'warn',
    },
    unavailable: { text: 'Growth: no market forecast — rates held flat', tone: 'warn' },
    withheld: {
      text: 'Growth: CoStar forecast withheld by this workspace’s data policy — rates held flat',
      tone: 'warn',
    },
  };
  const g = s.calendar_unknown && (s.growth_state === 'unavailable' || s.growth_state === 'partially_sourced')
    ? { text: 'Growth: market forecast on file but not placed — set the acquisition close date', tone: 'warn' as ClauseTone }
    : growthText[s.growth_state] ?? { text: `Growth: ${s.growth_state}`, tone: 'warn' as ClauseTone };
  out.push({ id: 'growth', ...g, title: growthLabels(s) || undefined });
  const stabWho =
    s.stabilization_source === 'analyst_override'
      ? 'your override'
      : s.stabilization_source === 'default_year_3_reopening'
        ? 'default — Year 3 after reopening'
        : 'default';
  out.push({ id: 'stabilization', tone: 'ok', text: `Stabilized Year ${s.stabilization_year} (${stabWho})` });
  out.push({ id: 'ramp', ...rampSummary(s) });
  return out;
}

export interface GrowthBanner {
  state: 'calendar_unknown' | 'unavailable' | 'withheld' | 'partially_sourced';
  title: string;
  detail: string;
  /** R2-I1 — the fix is the close date: show its entry first, above the suggestion. */
  askCloseDate: boolean;
  /** The worker's labelled suggestion (the only place 4.5% may appear), or null. */
  suggestion: ToplineSuggestion | null;
}

/** "No market forecast — set growth manually" (plan §4.2), or null when growth is sourced. */
export function growthBanner(s: ToplineStatus | null | undefined): GrowthBanner | null {
  if (!s) return null;
  const state = s.growth_state;
  if (state !== 'unavailable' && state !== 'withheld' && state !== 'partially_sourced') return null;
  const suggestion = (s.suggestions ?? []).find((x) => x.key === 'revpar_growth') ?? null;
  if (s.calendar_unknown && state !== 'withheld') {
    return {
      state: 'calendar_unknown',
      title: 'Set the acquisition close date to place the market forecast',
      detail:
        'A market forecast is on file, but the deal has no acquisition close date (and no underwriting as-of date), so its years cannot be placed — the model never guesses a calendar. Until then occupancy and ADR are held flat.',
      askCloseDate: true,
      suggestion,
    };
  }
  if (state === 'partially_sourced') {
    const gaps: string[] = [];
    for (const r of appliedRows(s)) {
      const metrics = [
        r.occupancy?.value == null ? 'occupancy' : null,
        r.adr?.value == null ? 'ADR' : null,
      ].filter(Boolean);
      if (metrics.length) gaps.push(`Year ${r.year}${r.calendar_year ? ` (${r.calendar_year})` : ''} ${metrics.join(' and ')}`);
    }
    return {
      state,
      title: 'The market forecast does not cover every year — set the rest manually',
      detail: `No figure for: ${gaps.join('; ')}. Those years are held flat.`,
      askCloseDate: false,
      suggestion,
    };
  }
  return {
    state,
    title:
      state === 'withheld'
        ? 'CoStar forecast on file but not applied under this workspace’s data policy — set growth manually'
        : 'No market forecast — set growth manually',
    detail:
      'Occupancy and ADR are held flat after Year 1 — growth 0 is the absence of a forecast, not a forecast. Enter growth by year below, or apply the labelled platform suggestion.',
    askCloseDate: false,
    suggestion,
  };
}

/** Plan §6 — a CoStar report extracted under v8 carries no class forecasts. */
export function reextractNotice(s: ToplineStatus | null | undefined): { title: string; lines: string[] } | null {
  const lines = (s?.reextract_needed ?? []).filter((l) => typeof l === 'string' && l.trim());
  if (!lines.length) return null;
  return { title: 'CoStar report extracted before class forecasts — re-extract', lines };
}

const ANCHOR_TEXT: Record<string, string> = {
  acquisition_close: 'the acquisition close',
  underwriting_as_of: 'the underwriting as-of date',
};

/**
 * Review I-4 — which anchor placed the forecast. Since R2-I1 only the close
 * date or the underwriting as-of date can; an assumed (as-of) anchor asks for
 * the close date. With neither, `growthBanner` carries the ask.
 */
export function calendarAnchorNote(
  s: ToplineStatus | null | undefined,
): { source: string; text: string; assumed: boolean; askCloseDate: boolean } | null {
  const a = s?.calendar_anchor;
  if (!a || !a.source || !a.date) return null;
  const where = ANCHOR_TEXT[a.source] ?? a.source.replace(/_/g, ' ');
  const year = a.first_model_year != null ? ` — Year 1 = ${a.first_model_year}` : '';
  return {
    source: a.source,
    text: `Forecast placed from ${where} (${a.date})${year}${a.assumed ? ' (assumed: no close date)' : ''}`,
    assumed: a.assumed,
    askCloseDate: a.assumed,
  };
}

/** Plan §6 — the one-time notice on a run that predates topline v2. */
export function toplineMigrationNotice({
  hasRevenueRun,
  version,
}: {
  hasRevenueRun: boolean;
  version: number | null | undefined;
}): string | null {
  if (!hasRevenueRun) return null;
  if (typeof version === 'number' && version >= TOPLINE_METHODOLOGY_VERSION) return null;
  return 'The topline method changed; this run predates it — re-run, then compare runs.';
}

/**
 * The legacy keys "Clear legacy keys" removes: every flagged key except a
 * legacy Year-1 rate the deal still runs on (`kept_no_benchmark` — with no
 * benchmark it is the deal's only Year-1 basis, and clearing it would refuse
 * the revenue chain).
 */
export function legacyKeysToClear(s: ToplineStatus | null | undefined): string[] {
  const keys: string[] = [];
  for (const f of s?.legacy_flags ?? []) {
    if (!f?.key || f.treatment === 'kept_no_benchmark') continue;
    if (!keys.includes(f.key)) keys.push(f.key);
  }
  return keys;
}

export function clearLegacyKeysPatch(s: ToplineStatus | null | undefined): Record<string, null> {
  return Object.fromEntries(legacyKeysToClear(s).map((k) => [k, null]));
}

/** Review I-5 — the deal has no Year-1 occupancy / ADR basis; the worker's sentence, verbatim. */
export function revenueBasisRefusal(
  s: ToplineStatus | null | undefined,
  failures: EngineFailure[] | null | undefined,
): { message: string; missing: string[] } | null {
  const rb = s?.revenue_basis;
  if (rb && rb.state === 'missing' && rb.message) return { message: rb.message, missing: [...(rb.missing ?? [])] };
  const f = (failures ?? []).find((x) => x?.reason === 'no_revenue_basis' && x.engine === 'revenue')
    ?? (failures ?? []).find((x) => x?.reason === 'no_revenue_basis');
  if (f && f.message) return { message: f.message, missing: [...(f.missing ?? [])] };
  return null;
}

// ── per-year list editors ──────────────────────────────────────────────

export type ParsedList = { ok: true; value: (number | null)[] | null } | { ok: false; error: string };

function parsePercentList(
  cells: string[],
  check: (fraction: number) => boolean,
  rangeText: string,
): ParsedList {
  const out: (number | null)[] = [];
  for (const raw of cells) {
    const t = raw.replace(/[%\s]/g, '');
    if (t === '') { out.push(null); continue; }
    const n = Number(t);
    if (!Number.isFinite(n)) return { ok: false, error: `“${raw}” is not a number.` };
    const f = n / 100;
    if (!check(f)) return { ok: false, error: `${raw}% is outside ${rangeText}.` };
    out.push(Math.round(f * 1e6) / 1e6);
  }
  return { ok: true, value: out.some((v) => v != null) ? out : null };
}

/** Growth by year, typed as percents: −50%..+50% (the worker's 422 band); blank = unset. */
export function parseGrowthPercentList(cells: string[]): ParsedList {
  return parsePercentList(cells, (f) => f >= -0.5 && f <= 0.5, '−50% to +50%');
}

/** Ramp by year, typed as percents: 0 < r ≤ 150%; blank = 100%. */
export function parseRampPercentList(cells: string[]): ParsedList {
  return parsePercentList(cells, (f) => f > 0 && f <= 1.5, '0–150% (above 0)');
}

/** A stored fraction list → percent text cells, padded to `length`. */
export function growthPercentList(list: (number | null)[] | null | undefined, length: number): string[] {
  return Array.from({ length }, (_, i) => {
    const v = list?.[i];
    if (v == null || !Number.isFinite(v)) return '';
    const p = Math.round(v * 100000) / 1000;
    return String(p);
  });
}

/** A `field_overrides` list entry (bare or `{value, note}`), or null. */
export function overrideList(overrides: Record<string, unknown>, key: string): (number | null)[] | null {
  const raw = overrides[key];
  const v = raw != null && typeof raw === 'object' && !Array.isArray(raw) && 'value' in (raw as object)
    ? (raw as { value?: unknown }).value
    : raw;
  if (!Array.isArray(v)) return null;
  return v.map((x) => (typeof x === 'number' && Number.isFinite(x) ? x : null));
}

// ── per-column basis and the reference rows (plan §2.4 / §4.2) ─────────

const LEVEL_TEXT: Record<string, string> = {
  comp_set: 'competitive set',
  submarket: 'submarket',
  market: 'market',
};

/**
 * The basis chip on one projected Occupancy / ADR column: the benchmark
 * year's own basis (actual / forecast / assumption), titled with the
 * engine's chain — benchmark × penetration ÷ 100 × ramp = the year's rate.
 * Null where the topline did not set the figure (an explicit analyst Year-1
 * value, no benchmark, or no record for the year).
 */
export function yearRateBadge(
  s: ToplineStatus | null | undefined,
  records: ToplineYearRecord[] | null | undefined,
  index: number,
  metric: 'occupancy' | 'adr',
): { basis: 'actual' | 'forecast' | 'assumption'; title: string } | null {
  if (!s || s.benchmark_level === 'none' || s.explicit_metrics?.includes(metric)) return null;
  const b = s.benchmark_by_year?.[index];
  const r = records?.[index];
  if (!b || !b.basis || !r) return null;
  const level = metric === 'occupancy' ? b.occupancy : b.adr;
  const pen = metric === 'occupancy' ? r.mpi_target : r.ari_target;
  const out = metric === 'occupancy' ? r.occupancy : r.adr;
  if (level == null || pen == null || out == null) return null;
  const fmt = (v: number) => (metric === 'occupancy' ? `${(v * 100).toFixed(1)}%` : `$${v.toFixed(2)}`);
  const src = (metric === 'occupancy' ? r.benchmark_occupancy_source : r.benchmark_adr_source) ?? null;
  const label = b.label || src || '';
  const ramp = r.ramp_pct != null ? ` × ramp ${fmtPctWhole(r.ramp_pct)}%` : '';
  const idx = metric === 'occupancy' ? 'MPI' : 'ARI';
  const cap = metric === 'occupancy' && r.capped_95 ? ' (capped at 95%)' : '';
  return {
    basis: b.basis,
    title: `Benchmark ${fmt(level)}${label ? ` (${label})` : ''} × ${idx} ${Math.round(pen * 10) / 10} ÷ 100${ramp} = ${fmt(out)}${cap}`,
  };
}

export interface ReferenceRow {
  label: string;
  occupancy: (number | null)[];
  adr: (number | null)[];
  revpar: (number | null)[];
  title?: string;
}

/**
 * The two reference rows under Occupancy / Average Rate / RevPAR: the
 * benchmark per year ("Benchmark (<level>)") and the subject's T-12 repeated
 * with its period ("T-12 actual (reference)"). Neither moves a number.
 */
export function toplineReferenceRows(
  s: ToplineStatus | null | undefined,
  years: number,
): { benchmark: ReferenceRow | null; t12: ReferenceRow | null } {
  if (!s) return { benchmark: null, t12: null };
  const by = s.benchmark_by_year ?? [];
  const benchmark: ReferenceRow | null =
    s.benchmark_level !== 'none' && by.length
      ? {
          label: `Benchmark (${LEVEL_TEXT[s.benchmark_level] ?? s.benchmark_level})`,
          occupancy: Array.from({ length: years }, (_, i) => by[i]?.occupancy ?? null),
          adr: Array.from({ length: years }, (_, i) => by[i]?.adr ?? null),
          revpar: Array.from({ length: years }, (_, i) => by[i]?.revpar ?? null),
          title: s.benchmark_label,
        }
      : null;
  const t = s.t12_reference;
  const t12: ReferenceRow | null =
    t && (t.occupancy != null || t.adr != null)
      ? {
          label: `T-12 actual (reference)${t.period_label ? ` · ${t.period_label}` : ''}`,
          occupancy: Array.from({ length: years }, () => t.occupancy ?? null),
          adr: Array.from({ length: years }, () => t.adr ?? null),
          revpar: Array.from({ length: years }, () => t.revpar ?? null),
          title: t.doc_name ? `The subject's own T-12 (${t.doc_name}) — shown for reference; it sets no projected year.` : undefined,
        }
      : null;
  return { benchmark, t12 };
}
