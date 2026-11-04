'use client';
/**
 * Index Analysis — the two tester findings on the Market → Index Analysis tab.
 *
 * E-027 (P1) — "Index Analysis showed ~84.6% occupancy and $255 ADR for the
 * reviewed year, while Projections showed ~83.9% and $245; the reason for the
 * difference was not visible."
 *
 *   Root cause (both read the SAME revenue engine, nothing is overridden):
 *   Projections' "Base year (Year 1) · <calendar>" column is ``revenue.years[0]``
 *   with its calendar year from ``revenue.projection_deal_years`` (the
 *   acquisition close date). Index Analysis used to hard-code its forecast
 *   band to 2025–2033 and place ``revenue.years[i]`` at 2024 + i, so its 2025
 *   column was ``years[1]`` — model Year 2. FON-94 derives the Index Analysis
 *   columns from the same ``projection_deal_years``: Year 1 sits in the
 *   first forecast column, so the two tabs now print the same figure for the
 *   same calendar year. A third figure — the STR subject TTM — anchors the comp
 *   set and the MPI / ARI indices, on another source and period.
 *
 *   ``ProjectionsReconciliation`` still puts the Projections Base Year (value,
 *   source, and every step between source and engine) next to what Index
 *   Analysis shows for that column and the STR TTM, with one line naming the
 *   relationship — "the same", or the transformation when a future change
 *   ever makes them differ again. No silent difference.
 *
 * E-028 (P2) — the retired methodology selector and its market-growth rows are
 * gone (FON-116 / FON-117): the Index Analysis tab edits the topline the model
 * runs on (`Topline.tsx` — benchmark level, penetration, ramp, per-year
 * penetration), read from `topline_status`. What stays here is the basis chip,
 * the reconciliation and the shared figure formatters.
 */

import { Card } from '@/components/ui/Card';
import type { IndexFigure } from '@/lib/api';
import { INDEX_POINTS_RANGE, INDEX_RATIO_RANGE, toIndexPoints, validateIndexTarget } from '@/lib/indexTarget';
import { sourceLabel } from '@/lib/provenance';
import { PL_SUB_LABEL } from '@/lib/projectTabs';
import { BASIS_CHIP_TEXT, figureBasis, type BenchmarkBasis } from './indexAnalysisModel';

// ─────────────────────────── shared formatting ───────────────────────────

const occFrac = (v: number | null | undefined): number | null =>
  typeof v === 'number' && Number.isFinite(v) && v > 0 ? (v > 1.5 ? v / 100 : v) : null;
const fmtOcc = (v: number | null | undefined): string => {
  const f = occFrac(v);
  return f == null ? '—' : `${(f * 100).toFixed(1)}%`;
};
const fmtAdr = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) && v > 0
    ? `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
    : '—';
const fmtGrowth = (v: number | null | undefined): string =>
  typeof v === 'number' && Number.isFinite(v) ? `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%` : '—';

// ─────────────────────────── Sam #6 — basis chip ───────────────────────────

const CHIP_BORDER: Record<BenchmarkBasis, string> = {
  actual: 'border-solid',
  forecast: 'border-dashed',
  assumption: 'border-dotted',
};

/**
 * Sam #6 (2026-10-15) — a small worded chip saying whether a benchmark value
 * is a reported ACTUAL, a CBRE FORECAST or an ASSUMPTION. The word carries the
 * meaning (the border style only echoes it), so the distinction never rests
 * on colour.
 */
export function BasisChip({ basis, label, testId }: { basis: BenchmarkBasis; label?: string | null; testId?: string }) {
  return (
    <span
      data-testid={testId ?? `basis-chip-${basis}`}
      data-basis={basis}
      title={label ?? undefined}
      className={`inline-block align-middle rounded border ${CHIP_BORDER[basis]} border-ink-400 px-1 leading-[14px] text-[9px] font-semibold uppercase tracking-wide text-ink-700`}
    >
      {BASIS_CHIP_TEXT[basis]}
    </span>
  );
}

/** Chip + the one-line basis label ("Actual · STR TTM May 2025 · <doc>"). */
export function BasisLine({ fig, testId }: { fig: IndexFigure | null | undefined; testId?: string }) {
  const b = figureBasis(fig);
  if (!b) return null;
  return (
    <div className="flex items-center justify-end gap-1 text-[10px] text-ink-500" data-testid={testId} title={b.label}>
      <BasisChip basis={b.basis} label={b.label} />
      <span className="max-w-[200px] truncate">{b.label}</span>
    </div>
  );
}

// ─────────────────────────── E-027 — reconciliation ───────────────────────────

/** Where an Index Analysis subject column's numbers come from. */
export type IaColumnOrigin =
  | { kind: 'engine'; yearIndex: number }
  | { kind: 'fiscal_year' }
  | { kind: 'str_annual' }
  | { kind: 'none' };

export interface ReconciliationSource {
  /** The worker source id on ``starting_occupancy`` / ``starting_adr``. */
  source?: string | null;
  filename?: string | null;
  page?: number | null;
  /** The period end the source row carries (``as_of``), e.g. 2025-03-31. */
  asOf?: string | null;
  scope?: string | null;
}

export interface ReconciliationInput {
  /** ``revenue.years[0]`` — what Projections shows as Base year (Year 1). */
  baseYear: { occupancy: number; adr: number } | null;
  /** ``revenue.projection_deal_years[0].label`` — null with no close date. */
  baseDealYear: string | null;
  /** ``starting_occupancy`` / ``starting_adr`` — the stabilized baseline. */
  startingOccupancy: number | null;
  startingAdr: number | null;
  occSource: ReconciliationSource | null;
  adrSource: ReconciliationSource | null;
  y1OccDisplacement: number | null;
  y1AdrDisplacement: number | null;
  occupancyGrowth: number | null;
  adrGrowth: number | null;
  /** The Index Analysis subject column for ``iaColumnYear`` and its origin.
   *  The calendar year, or the column's ordinal label ("Year 1") when the
   *  deal has no acquisition close date. */
  iaColumnYear: number | string;
  iaOccupancy: number | null;
  iaAdr: number | null;
  iaOrigin: IaColumnOrigin;
  /** The STR subject TTM the comp set / indices are measured on. */
  strOccupancy: number | null;
  strAdr: number | null;
  strPeriodLabel: string | null;
}

export interface Reconciliation {
  projectionsLabel: string;
  projectionsSource: string;
  steps: string[];
  iaLabel: string;
  iaWhat: string;
  strLabel: string | null;
  line: string;
  matches: boolean;
}

function describeSource(s: ReconciliationSource | null): string {
  if (!s?.source) return 'source not reported';
  const label = s.source === 't12_actual' ? 'T-12 actual' : sourceLabel(s.source);
  const period = s.asOf ? ` to ${s.asOf.slice(0, 7)}` : '';
  const doc = s.filename ? ` — ${s.filename}${s.page ? `, p.${s.page}` : ''}` : '';
  return `${label}${period}${doc}`;
}

const pair = (occ: number | null | undefined, adr: number | null | undefined) => `${fmtOcc(occ)} / ${fmtAdr(adr)}`;
const near = (a: number | null, b: number | null, tol: number) =>
  a != null && b != null && Math.abs(a - b) <= tol;

/**
 * The reconciliation between what Projections uses for its Base Year and what
 * Index Analysis shows for the same calendar year. Pure — every number in the
 * output is one of the inputs (engine outputs / worker sources), restated.
 */
export function buildReconciliation(i: ReconciliationInput): Reconciliation | null {
  if (!i.baseYear) return null;
  const cal = i.baseDealYear;
  const projectionsLabel = `${PL_SUB_LABEL.projections} — Base year (Year 1)${cal != null ? ` · ${cal}` : ''}`;
  const projectionsSource = describeSource(i.occSource);

  // Every step between the source and revenue.years[0].
  const steps: string[] = [];
  if (i.startingOccupancy != null || i.startingAdr != null) {
    steps.push(`Stabilized baseline ${pair(i.startingOccupancy, i.startingAdr)} — ${projectionsSource}`);
  }
  if (i.occSource?.source === 'topline_benchmark' || i.adrSource?.source === 'topline_benchmark') {
    steps.push('Topline: the benchmark × that year’s penetration sets the Year-1 rate');
  }
  const dOcc = i.y1OccDisplacement ?? 0;
  const dAdr = i.y1AdrDisplacement ?? 0;
  if (dOcc > 0 || dAdr > 0) {
    steps.push(
      `Year-1 renovation displacement ${dOcc > 0 ? `−${(dOcc * 100).toFixed(1)}% occupancy` : ''}${dOcc > 0 && dAdr > 0 ? ', ' : ''}${dAdr > 0 ? `−${(dAdr * 100).toFixed(1)}% ADR` : ''}`,
    );
  }
  steps.push(`Revenue engine Year 1 (revenue.years[0]) ${pair(i.baseYear.occupancy, i.baseYear.adr)}`);

  const iaLabel = `Index Analysis — ${i.iaColumnYear} column`;
  let iaWhat: string;
  const growthClause =
    i.occupancyGrowth != null && i.adrGrowth != null
      ? ` at ${fmtGrowth(i.occupancyGrowth)} occupancy / ${fmtGrowth(i.adrGrowth)} ADR a year`
      : " at the model's occupancy / ADR growth";
  if (i.iaOrigin.kind === 'engine' && i.iaOrigin.yearIndex === 0) {
    iaWhat = 'the same revenue engine Year 1 (revenue.years[0])';
  } else if (i.iaOrigin.kind === 'engine') {
    const k = i.iaOrigin.yearIndex;
    iaWhat = `model Year ${k + 1} (revenue.years[${k}]) — the Base Year grown ${k} year${k === 1 ? '' : 's'}${growthClause}`;
  } else if (i.iaOrigin.kind === 'fiscal_year') {
    iaWhat = `the fiscal-year ${i.iaColumnYear} P&L actual from the multi-year baseline`;
  } else if (i.iaOrigin.kind === 'str_annual') {
    iaWhat = `the STR Trend annual ${i.iaColumnYear} subject actual`;
  } else {
    iaWhat = 'no subject figure';
  }
  const strLabel =
    i.strOccupancy != null || i.strAdr != null
      ? `STR subject ${i.strPeriodLabel ?? 'TTM'} ${pair(i.strOccupancy, i.strAdr)}`
      : null;

  const matches =
    near(occFrac(i.iaOccupancy), occFrac(i.baseYear.occupancy), 0.0005) && near(i.iaAdr, i.baseYear.adr, 0.5);
  const base = `${PL_SUB_LABEL.projections} uses ${cal != null ? `${cal} ` : ''}Base year (Year 1) ${pair(i.baseYear.occupancy, i.baseYear.adr)} from the ${projectionsSource}`;
  const ia = matches
    ? `Index Analysis shows the same ${pair(i.iaOccupancy, i.iaAdr)} in its ${i.iaColumnYear} column`
    : `Index Analysis's ${i.iaColumnYear} column shows ${pair(i.iaOccupancy, i.iaAdr)}, which is ${iaWhat}`;
  const str = strLabel
    ? `; the STR subject ${i.strPeriodLabel ?? 'TTM'} ${pair(i.strOccupancy, i.strAdr)} is a different source and period — it sets the comp set and the MPI / ARI indices; the model runs on the benchmark × penetration × ramp`
    : '';
  const line = `${base}; ${ia}${str}.`;
  return { projectionsLabel, projectionsSource, steps, iaLabel, iaWhat, strLabel, line, matches };
}

export function ProjectionsReconciliation({ rec }: { rec: Reconciliation | null }) {
  if (!rec) return null;
  return (
    <Card className="p-4" data-testid="index-projections-reconciliation">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-500 mb-2">
        Reconciliation to {PL_SUB_LABEL.projections}
      </div>
      <p className="text-[12.5px] text-ink-900 leading-relaxed" data-testid="index-reconciliation-line">
        {rec.line}
      </p>
      <div className="grid gap-3 mt-3 md:grid-cols-2 text-[11.5px]">
        <div>
          <div className="font-semibold text-ink-700">{rec.projectionsLabel}</div>
          <ol className="list-decimal ml-4 mt-1 space-y-0.5 text-ink-600">
            {rec.steps.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ol>
        </div>
        <div>
          <div className="font-semibold text-ink-700">{rec.iaLabel}</div>
          <div className="mt-1 text-ink-600">{rec.iaWhat}</div>
          {rec.strLabel && <div className="mt-1 text-ink-600">{rec.strLabel} — comp-set / index basis</div>}
        </div>
      </div>
    </Card>
  );
}

// ─────────────────────────── shared figure helpers ───────────────────────────

/**
 * "Doc.pdf, p.6" for a document figure; the inputs for a computed one. The
 * extraction field path is NOT printed — a client reads the document and page.
 * ``figureSourceTitle`` carries the path for a hover tooltip (audit).
 */
export function figureSourceText(f: IndexFigure | undefined | null): string {
  return figureSource(f, false);
}

/** The same attribution with each input's extraction field path, for `title=`. */
export function figureSourceTitle(f: IndexFigure | undefined | null): string {
  return figureSource(f, true);
}

function figureSource(f: IndexFigure | undefined | null, withPath: boolean): string {
  if (!f) return '—';
  if (f.source === 'override') return `Your override${f.detail ? ` — ${f.detail}` : ''}`;
  // A stored value the model does not apply — never presented as an active override.
  if (f.source === 'ignored_override') return `Stored override ignored${f.detail ? ` — ${f.detail}` : ''}`;
  const refs = (f.inputs ?? []).map(
    (r) =>
      `${r.doc_name ?? 'document'}${r.page ? `, p.${r.page}` : ''}${withPath ? ` (${r.field_name})` : ''}`,
  );
  if (f.source === 'document') return refs.join('; ') || 'document';
  if (f.source === 'computed') return `Computed${f.detail ? `: ${f.detail}` : ''} — ${refs.join('; ')}`;
  return f.detail ?? 'No source';
}

/**
 * One readable sentence from the worker's by-year "no benchmark" detail.
 *
 * The worker phrases it as two clauses with the method label lower-cased and
 * a doubled full stop — "No market occupancy growth assumption to carry the
 * str competitive set past Year 1 — No market occupancy growth forecast in
 * the uploaded reports — enter your own.." — once per year, for occupancy and
 * ADR. The grid prints it once, above the table, as: "No market occupancy
 * growth forecast in the uploaded reports — enter one to carry the STR
 * competitive set past Year 1."
 */
export function plainIndexDetail(detail: string): string {
  const properCase = (s: string) =>
    s
      .replace(/\bstr\b/gi, 'STR')
      .replace(/\bcostar\b/gi, 'CoStar')
      .replace(/\bcbre\b/gi, 'CBRE')
      .replace(/\badr\b/g, 'ADR');
  const m = detail.match(
    /^No market (occupancy|ADR|adr) growth assumption to carry the (.+?) past Year 1 — No market (?:occupancy|ADR|adr) growth forecast in the uploaded reports — enter your own\.*$/i,
  );
  if (m) {
    const metric = m[1].toLowerCase() === 'adr' ? 'ADR' : 'occupancy';
    return `No market ${metric} growth forecast in the uploaded reports — enter one to carry the ${properCase(m[2])} past Year 1.`;
  }
  // Generic cleaning: proper-case the method label, drop repeated clauses,
  // collapse a doubled full stop.
  const clauses = detail.split(' — ').map((c) => properCase(c.trim())).filter(Boolean);
  const unique = clauses.filter((c, i) => clauses.indexOf(c) === i);
  return unique.join(' — ').replace(/\.{2,}$/, '.').replace(/([^.])$/, '$1.');
}

/** The accepted target units, for the refusal message. */
export const INDEX_TARGET_UNITS = `index points ${INDEX_POINTS_RANGE[0]}–${INDEX_POINTS_RANGE[1]} (100 = parity with the benchmark) or a ratio ${INDEX_RATIO_RANGE[0]}–${INDEX_RATIO_RANGE[1]}`;

export function fmtAssumption(v: number | null | undefined, unit: 'growth' | 'index'): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  if (unit === 'growth') return `${(v * 100).toFixed(2)}%`;
  // A target is always shown in index points, read exactly as the worker
  // reads it: a ratio (1.10) is 110.0, an out-of-range value is not a target.
  const pts = toIndexPoints(v);
  return pts == null ? '—' : pts.toFixed(1);
}

/** Parse one grid cell: '' → null (not set), else index points read exactly as
 *  the worker reads them (``@/lib/indexTarget``): a ratio 0.25–2.5 (1.10 → 110)
 *  or points 25–250; anything else (5, 12, 300) → undefined (refused). */
export function parseIndexPoints(raw: string): number | null | undefined {
  const t = raw.replace(/[\s,]/g, '');
  if (!t) return null;
  const { points } = validateIndexTarget(t);
  return points == null ? undefined : points;
}
