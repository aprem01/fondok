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
import { yearWithSpan, type DealYearSpan } from '@/lib/dealYears';
import { SOURCE_LABEL_FROM_REGISTRY } from '@/lib/ontology/adapters';

/**
 * FON-116 staged rollout (temporary — removed once FON-117 is on in
 * production): the worker runs the growth-only topline — Year 1 is the
 * subject's own rates and only the market growth by year applies; benchmark,
 * penetration and ramp are not in use. An older worker (no field) is the
 * benchmark topline.
 */
export function isGrowthOnly(s: ToplineStatus | null | undefined): boolean {
  return s?.topline_mode === 'growth_only';
}

/** The topline method this web build explains (worker `TOPLINE_METHODOLOGY_VERSION`). */
export const TOPLINE_METHODOLOGY_VERSION = 7;

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
  /** FON-121 — the deal year's span (twelve months from the close). */
  deal_year?: DealYearSpan | null;
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
  mpi_start_index?: number | null;
  ari_start_index?: number | null;
  occupancy?: number | null;
  adr?: number | null;
  occupancy_status?: string | null;
  adr_status?: string | null;
  growth_label?: string | null;
  t12_occupancy?: number | null;
  t12_adr?: number | null;
  capped_95?: boolean;
  reason?: string | null;
  /** `by_year` = your per-year penetration (no ramp); `scalar` = the stabilized penetration × ramp. */
  mpi_target_source?: string | null;
  ari_target_source?: string | null;
}

export type ClauseTone = 'ok' | 'warn';

export interface ToplineClause {
  id: 'benchmark' | 'year1' | 'growth' | 'stabilization' | 'ramp' | 'closed';
  text: string;
  tone: ClauseTone;
  /** Hover text — the worker's full labels. */
  title?: string;
}

const GROWTH_SOURCE_TEXT: Record<string, string> = {
  analyst_override: 'your override',
  cbre_horizons: 'CBRE Hotel Horizons forecast',
  costar_submarket_forecast: 'CoStar submarket forecast',
  carried_forward: 'past the forecast (the report’s forecast average for ADR; occupancy held)',
  held_at_stabilization: 'occupancy flat after stabilization',
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
    .map((r) => `${yearWithSpan(r.year, r.deal_year)}: occupancy ${r.occupancy.label}; ADR ${r.adr.label}`)
    .join('\n');
}

/** Which default ramp applies to this deal (Sam, 2026-11-04). */
export function rampModeLabel(s: ToplineStatus): string | null {
  if (s.benchmark_level === 'none') return null;
  if (s.ramp_mode === 'operating') return 'Operating hotel';
  if (s.ramp_mode === 'new_or_reopened') return 'New or reopened hotel';
  return null;
}

/** The ramp the worker APPLIED, and why — never a ramp the browser assumes. */
export function rampSummary(s: ToplineStatus): { text: string; tone: ClauseTone } {
  const S = s.stabilization_year;
  if (s.benchmark_level === 'none') {
    return { text: 'No ramp — no benchmark to penetrate', tone: 'ok' };
  }
  if (s.ramp_state === 'stabilization_after_hold') {
    return {
      text: `Stabilized Year ${S} is after the hold — the projection never reaches full penetration`,
      tone: 'warn',
    };
  }
  if (s.ramp_state === 'undefined_for_stabilization_year') {
    const gap = s.ramp_by_year?.findIndex((r) => r == null) ?? -1;
    const first = gap >= 0 ? gap + 1 : 3;
    if (first === 1) {
      return { text: `Ramp: the default ramp has finished; enter the ramp for Years 1–${S - 1} or they run at 100%`, tone: 'warn' };
    }
    return {
      text: `Ramp: enter the ramp for Years ${first}–${S - 1} (the default covers ${first === 2 ? 'Year 1' : `Years 1–${first - 1}`} only; those years run at 100%)`,
      tone: 'warn',
    };
  }
  if (s.ramp_source === 'by_year') {
    return { text: 'Your by-year penetration — no ramp', tone: 'ok' };
  }
  if (S <= 1) {
    return { text: 'No ramp — stabilized from Year 1', tone: 'ok' };
  }
  if (s.ramp_source === 'straight_line') {
    if (holdsTodaysIndex(s)) {
      const classAverage = s.benchmark_level === 'submarket' || s.benchmark_level === 'market';
      return { text: `Holding today's index${classAverage ? ' (benchmark is a class average)' : ''}`, tone: 'ok' };
    }
    return { text: `Straight line from today's index to the target by Year ${S}`, tone: 'ok' };
  }
  if (S <= 1 || !s.ramp_by_year?.length) {
    return { text: 'No ramp — stabilized from Year 1', tone: 'ok' };
  }
  const steps = [...s.ramp_by_year.map((r) => (r == null ? '100' : fmtPctWhole(r))), '100'];
  const who = s.ramp_source === 'analyst_override' ? 'your ramp' : 'default';
  return { text: `Ramp ${steps.join('/')} (${who})`, tone: 'ok' };
}

/** The target is today's index, so the line has nowhere to go. */
function holdsTodaysIndex(s: ToplineStatus): boolean {
  const flat = (start?: number | null, target?: number | null) =>
    start == null || (target != null && Math.abs(start - target) < 0.05);
  const live = [
    !s.explicit_metrics.includes('occupancy') && s.start_index_occ != null,
    !s.explicit_metrics.includes('adr') && s.start_index_adr != null,
  ];
  return live.some(Boolean)
    && (!live[0] || flat(s.start_index_occ, s.penetration_occ))
    && (!live[1] || flat(s.start_index_adr, s.penetration_adr));
}

function fmtPctWhole(fraction: number): string {
  const v = Math.round(fraction * 1000) / 10;
  return Number.isInteger(v) ? String(v) : v.toFixed(1);
}

/** The Topline basis line (plan §4.2): benchmark · growth · stabilized year · ramp. */
export function toplineBasisClauses(s: ToplineStatus | null | undefined): ToplineClause[] {
  if (!s) return [];
  const out: ToplineClause[] = [];
  if (isGrowthOnly(s)) {
    out.push(year1Clause(s));
  } else if (s.benchmark_level === 'none') {
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
  const gap = s.growth_state === 'unavailable' || s.growth_state === 'partially_sourced';
  const g = s.calendar_unknown && gap
    ? { text: 'Growth: market forecast on file but not placed — set the acquisition close date', tone: 'warn' as ClauseTone }
    : gap && (s.reextract_needed ?? []).length > 0
      ? { text: 'Growth: CoStar report on file but extracted before class forecasts — re-extract', tone: 'warn' as ClauseTone }
      : growthText[s.growth_state] ?? { text: `Growth: ${s.growth_state}`, tone: 'warn' as ClauseTone };
  out.push({ id: 'growth', ...g, title: growthLabels(s) || undefined });
  const stabWho =
    s.stabilization_source === 'analyst_override'
      ? 'your override'
      : s.stabilization_source === 'default_year_3_reopening'
        ? 'default — Year 3 after reopening'
        : 'default';
  // Decision 1b (2026-11-03): occupancy holds at its stabilized level; ADR keeps growing.
  // FON-121 — the stabilized year names its deal-year span.
  const stabSpan = (s.growth_by_year ?? []).find((r) => r.year === s.stabilization_year)?.deal_year?.label;
  out.push({
    id: 'stabilization',
    tone: 'ok',
    text: `Stabilized Year ${s.stabilization_year} (${stabSpan ? `${stabSpan}; ` : ''}${stabWho}) — occupancy flat after it`,
  });
  if (!isGrowthOnly(s)) out.push({ id: 'ramp', ...rampSummary(s) });
  if (s.closed_months) {
    out.push({
      id: 'closed',
      tone: 'ok',
      text: `Closed for the first ${s.closed_months} months: no revenue or operating costs until the opening date. Taxes, insurance and other fixed charges keep running.`,
    });
  }
  return out;
}

/** Growth-only topline — which source set each Year-1 rate (registry labels, never composed here). */
function year1Clause(s: ToplineStatus): ToplineClause {
  const occ = s.year1_sources?.occupancy ?? null;
  const adr = s.year1_sources?.adr ?? null;
  const label = (id: string | null) => (id ? SOURCE_LABEL_FROM_REGISTRY[id] ?? id.replace(/_/g, ' ') : '—');
  if (!occ && !adr) {
    return { id: 'year1', tone: 'warn', text: 'Year 1: no rates — enter Year-1 occupancy and ADR' };
  }
  const text = occ === adr ? `Year 1: ${label(occ)}` : `Year 1: occupancy ${label(occ)} · ADR ${label(adr)}`;
  return {
    id: 'year1',
    tone: occ && adr ? 'ok' : 'warn',
    text,
    title: 'Growth-only topline: Year 1 is the subject’s own rates, grown by the market growth by year. The benchmark × penetration × ramp method is not switched on in this workspace.',
  };
}

export interface GrowthBanner {
  state: 'calendar_unknown' | 'reextract' | 'unavailable' | 'withheld' | 'partially_sourced';
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
  // Final review M-1 — the worker's order: a CoStar report on file but read
  // before class forecasts existed is "re-extract", never "no market forecast".
  const stale = (s.reextract_needed ?? []).filter((l) => typeof l === 'string' && l.trim());
  if (stale.length && state !== 'withheld') {
    return {
      state: 'reextract',
      title: 'CoStar report on file but extracted before class forecasts — re-extract it to read the forecast',
      detail: stale.join(' '),
      askCloseDate: false,
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
      if (metrics.length) gaps.push(`${yearWithSpan(r.year, r.deal_year)} ${metrics.join(' and ')}`);
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
  // Final review M-4 — the line explains where a MARKET FORECAST was placed;
  // with none placed (every year analyst / unavailable) there is nothing to say.
  const placed = (s?.growth_by_year ?? []).some((r) =>
    [r.occupancy, r.adr].some((g) => g && ['cbre_horizons', 'costar_submarket_forecast', 'carried_forward'].includes(String(g.source))),
  );
  if (!placed) return null;
  const where = ANCHOR_TEXT[a.source] ?? a.source.replace(/_/g, ' ');
  // Review M8 — a status from before deal years names Year 1's calendar year.
  const y1 = a.year1?.label ?? (a.first_model_year != null ? String(a.first_model_year) : null);
  const year = y1 ? ` — Year 1 = ${y1}` : '';
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

export interface ToplineYearChain {
  /** The benchmark level for the year (occupancy fraction or ADR $). */
  benchmark: number;
  /** The penetration the engine applied that year (`mpi_target` / `ari_target`) — it already includes the ramp. */
  effectivePenetration: number;
  /** The stabilized penetration before the ramp (effective ÷ ramp), when a ramp applied. */
  stabilizedPenetration: number | null;
  /** The ramp, only when it applied to this metric (never on a per-year penetration). */
  rampApplied: boolean;
  ramp: number | null;
  /** Your per-year penetration for this year (`*_target_source === 'by_year'`). */
  byYear: boolean;
  /** benchmark × effective penetration ÷ 100 (occupancy capped at 95%) — before displacement. */
  pre: number;
  capped: boolean;
  /** The figure that ran (`topline_by_year[i].occupancy` / `.adr`, after any displacement). */
  final: number;
  displaced: boolean;
  text: string;
}

/**
 * The engine's own chain for one year and metric, read from its record
 * (`revenue.topline_by_year[i]`): benchmark × penetration × ramp = the
 * pre-displacement rate, then displacement as its own step to the figure that
 * ran. The engine's `mpi_target` / `ari_target` is the EFFECTIVE penetration
 * (stabilized × ramp, or your per-year value with no ramp), so the ramp is
 * shown once and never re-applied. Null where the topline did not set the
 * figure (no benchmark for the metric, no record, an explicit Year-1 value).
 */
export function toplineYearChain(
  s: ToplineStatus | null | undefined,
  records: ToplineYearRecord[] | null | undefined,
  index: number,
  metric: 'occupancy' | 'adr',
): ToplineYearChain | null {
  if (!s || s.benchmark_level === 'none' || s.explicit_metrics?.includes(metric)) return null;
  const r = records?.[index] as (ToplineYearRecord & { mpi_target_source?: string | null; ari_target_source?: string | null }) | undefined;
  if (!r) return null;
  const occ = metric === 'occupancy';
  const benchmark = occ ? r.benchmark_occupancy : r.benchmark_adr;
  const pen = occ ? r.mpi_target : r.ari_target;
  const final = occ ? r.occupancy : r.adr;
  if (benchmark == null || pen == null || final == null) return null;
  const byYear = (occ ? r.mpi_target_source : r.ari_target_source) === 'by_year';
  const start = occ ? r.mpi_start_index : r.ari_start_index;
  const rampApplied = !byYear && start == null && r.ramp_pct != null && r.ramp_pct > 0;
  const ramp = rampApplied ? (r.ramp_pct as number) : null;
  const stabilizedPenetration = ramp != null ? pen / ramp : null;
  const raw = (benchmark * pen) / 100;
  const capped = occ && raw > 0.95;
  const pre = capped ? 0.95 : raw;
  const displaced = Math.abs(final - pre) > Math.max(1e-9, Math.abs(pre) * 1e-9);
  const fmt = (v: number) => (occ ? `${(v * 100).toFixed(1)}%` : `$${v.toFixed(2)}`);
  const pts = (v: number) => String(Math.round(v * 10) / 10);
  const penText = byYear
    ? `penetration ${pts(pen)} (your Year ${r.year} value — no ramp)`
    : start != null
      ? stabilizedPenetration != null && Math.abs(start - stabilizedPenetration) < 0.05
        ? `penetration ${pts(pen)} (holding today's index)`
        : `penetration ${pts(pen)} (straight line from today's ${pts(start)})`
    : ramp != null
      ? `penetration ${pts(stabilizedPenetration as number)} × ramp ${fmtPctWhole(ramp)}%`
      : `penetration ${pts(pen)}`;
  const capText = capped ? ' (capped at 95%)' : '';
  const text = displaced
    ? `Benchmark ${fmt(benchmark)} × ${penText} = ${fmt(pre)}${capText} before displacement; Year-${r.year} displacement → ${fmt(final)}`
    : `Benchmark ${fmt(benchmark)} × ${penText} = ${fmt(pre)}${capText}`;
  return {
    benchmark, effectivePenetration: pen, stabilizedPenetration, rampApplied, ramp, byYear,
    pre, capped, final, displaced, text,
  };
}

/**
 * The basis chip on one projected Occupancy / ADR column: the benchmark
 * year's own basis (actual / forecast / assumption), titled with the
 * engine's chain (`toplineYearChain`) and the benchmark's label.
 */
export function yearRateBadge(
  s: ToplineStatus | null | undefined,
  records: ToplineYearRecord[] | null | undefined,
  index: number,
  metric: 'occupancy' | 'adr',
): { basis: 'actual' | 'forecast' | 'assumption'; title: string } | null {
  const chain = toplineYearChain(s, records, index, metric);
  const b = s?.benchmark_by_year?.[index];
  if (!chain || !b || !b.basis) return null;
  return { basis: b.basis, title: b.label ? `${chain.text}. Benchmark: ${b.label}` : chain.text };
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
  // Growth-only topline: the T-12 IS Year 1's basis (no "reference" row), and no benchmark is in use.
  if (isGrowthOnly(s)) return { benchmark: null, t12: null };
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
          label: `T-12 actual (reference)${t.period_label && !/^t-?12$/i.test(t.period_label.trim()) ? ` · ${t.period_label}` : ''}`,
          occupancy: Array.from({ length: years }, () => t.occupancy ?? null),
          adr: Array.from({ length: years }, () => t.adr ?? null),
          revpar: Array.from({ length: years }, () => t.revpar ?? null),
          title: `The subject's own T-12${t.doc_name ? ` (${t.doc_name})` : ''} — shown for reference; it sets no projected year.`,
        }
      : null;
  return { benchmark, t12 };
}
