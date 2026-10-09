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
 *   with its calendar year from ``revenue.projection_calendar_years`` (the
 *   acquisition close date). Index Analysis hard-codes its forecast band to
 *   2025–2033 and places ``revenue.years[i]`` at 2024 + i — so its 2025 column
 *   is ``years[1]``, model Year 2: the Base Year grown one year at the model's
 *   occupancy / ADR growth (seed +0.8% / +4.0%: 83.9% → 84.6%, $245 → $255).
 *   The Base Year itself sits in the 2024 anchor column only when no multi-year
 *   P&L exists; with one, the FY actual takes that column and the Base Year is
 *   not shown at all. A third figure — the STR subject TTM — anchors the comp
 *   set and the MPI / ARI indices, on yet another source and period.
 *
 *   ``ProjectionsReconciliation`` puts the Projections Base Year (value, source,
 *   and every step between source and engine) next to what Index Analysis shows
 *   for that calendar year and the STR TTM, with one line naming the
 *   transformation. No silent difference.
 *
 * E-028 (P2) — choose the index methodology and edit the market growth /
 * penetration assumptions before they feed Projections. ``IndexMethodologyPanel``
 * reads ``GET /market/{id}/index-methodology``; every value shows its document
 * + page, the documents a computed value used, or "Your override".
 */

import { useCallback, useEffect, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  isWorkerConnected,
  type IndexFigure,
  type IndexMethodId,
  type IndexMethodologyResponse,
} from '@/lib/api';
import { applyOverridePatch, NOTE_PLACEHOLDER, NOTE_REQUIRED_MESSAGE, requiresNote } from '@/lib/overrideNote';
import { sourceLabel } from '@/lib/provenance';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';

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

// ─────────────────────────── E-027 — reconciliation ───────────────────────────

/** Where an Index Analysis subject column's numbers come from. */
export type IaColumnOrigin =
  | { kind: 'engine'; yearIndex: number }
  | { kind: 'fiscal_year' }
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
  /** ``revenue.projection_calendar_years[0]`` — null with no close date. */
  baseCalendarYear: number | null;
  /** ``starting_occupancy`` / ``starting_adr`` — the stabilized baseline. */
  startingOccupancy: number | null;
  startingAdr: number | null;
  occSource: ReconciliationSource | null;
  adrSource: ReconciliationSource | null;
  y1OccDisplacement: number | null;
  y1AdrDisplacement: number | null;
  occupancyGrowth: number | null;
  adrGrowth: number | null;
  /** The Index Analysis subject column for ``iaColumnYear`` and its origin. */
  iaColumnYear: number;
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
  const cal = i.baseCalendarYear;
  const projectionsLabel = `Projections — Base year (Year 1)${cal != null ? ` · ${cal}` : ''}`;
  const projectionsSource = describeSource(i.occSource);

  // Every step between the source and revenue.years[0].
  const steps: string[] = [];
  if (i.startingOccupancy != null || i.startingAdr != null) {
    steps.push(`Stabilized baseline ${pair(i.startingOccupancy, i.startingAdr)} — ${projectionsSource}`);
  }
  if (i.occSource?.source === 'index_assumption' || i.adrSource?.source === 'index_assumption') {
    steps.push('Index Analysis penetration target applied to the selected benchmark (STR basis on)');
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
  } else {
    iaWhat = 'no subject figure';
  }
  const strLabel =
    i.strOccupancy != null || i.strAdr != null
      ? `STR subject ${i.strPeriodLabel ?? 'TTM'} ${pair(i.strOccupancy, i.strAdr)}`
      : null;

  const matches =
    near(occFrac(i.iaOccupancy), occFrac(i.baseYear.occupancy), 0.0005) && near(i.iaAdr, i.baseYear.adr, 0.5);
  const base = `Projections use ${cal != null ? `${cal} ` : ''}Base year (Year 1) ${pair(i.baseYear.occupancy, i.baseYear.adr)} from the ${projectionsSource}`;
  const ia = matches
    ? `Index Analysis shows the same ${pair(i.iaOccupancy, i.iaAdr)} in its ${i.iaColumnYear} column`
    : `Index Analysis's ${i.iaColumnYear} column shows ${pair(i.iaOccupancy, i.iaAdr)}, which is ${iaWhat}`;
  const str = strLabel
    ? `; the STR subject ${i.strPeriodLabel ?? 'TTM'} ${pair(i.strOccupancy, i.strAdr)} is a different source and period — it sets the comp set and the MPI / ARI indices, not the model, unless "Use STR rates in the model" is on`
    : '';
  const line = `${base}; ${ia}${str}.`;
  return { projectionsLabel, projectionsSource, steps, iaLabel, iaWhat, strLabel, line, matches };
}

export function ProjectionsReconciliation({ rec }: { rec: Reconciliation | null }) {
  if (!rec) return null;
  return (
    <Card className="p-4" data-testid="index-projections-reconciliation">
      <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-500 mb-2">
        Reconciliation to Projections
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

// ─────────────────────────── E-028 — methodology + assumptions ───────────────────────────

export const INDEX_METHOD_KEY = 'index_methodology';
export const INDEX_ASSUMPTIONS: { key: string; label: string; unit: 'growth' | 'index' }[] = [
  { key: 'index_market_occupancy_growth', label: 'Market occupancy growth', unit: 'growth' },
  { key: 'index_market_adr_growth', label: 'Market ADR growth', unit: 'growth' },
  { key: 'index_mpi_target', label: 'Occupancy penetration target (MPI)', unit: 'index' },
  { key: 'index_ari_target', label: 'ADR penetration target (ARI)', unit: 'index' },
];

/** "Doc.pdf, p.6 (field)" for a document figure; the inputs for a computed one. */
export function figureSourceText(f: IndexFigure | undefined | null): string {
  if (!f) return '—';
  if (f.source === 'override') return `Your override${f.detail ? ` — ${f.detail}` : ''}`;
  const refs = (f.inputs ?? []).map(
    (r) => `${r.doc_name ?? 'document'}${r.page ? `, p.${r.page}` : ''} (${r.field_name})`,
  );
  if (f.source === 'document') return refs.join('; ') || 'document';
  if (f.source === 'computed') return `Computed${f.detail ? `: ${f.detail}` : ''} — ${refs.join('; ')}`;
  return f.detail ?? 'No source';
}

function fmtAssumption(v: number | null | undefined, unit: 'growth' | 'index'): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) return '—';
  return unit === 'growth' ? `${(v * 100).toFixed(2)}%` : v.toFixed(1);
}

function AssumptionRow({
  label,
  unit,
  figure,
  overrideKey,
  overridden,
  disabled,
  onSave,
  onReset,
}: {
  label: string;
  unit: 'growth' | 'index';
  figure: IndexFigure | undefined;
  overrideKey: string;
  overridden: boolean;
  disabled: boolean;
  onSave: (key: string, value: number, note: string) => void;
  onReset: (key: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState('');
  const [note, setNote] = useState('');
  const needNote = requiresNote(overrideKey);
  const start = () => {
    const v = figure?.value;
    setDraft(typeof v === 'number' ? (unit === 'growth' ? (v * 100).toFixed(2) : v.toFixed(1)) : '');
    setNote('');
    setEditing(true);
  };
  const save = () => {
    const n = Number(draft.replace(/[%\s,]/g, ''));
    if (!Number.isFinite(n) || draft.trim() === '') return;
    if (needNote && !note.trim()) return;
    onSave(overrideKey, unit === 'growth' ? n / 100 : n, note.trim());
    setEditing(false);
  };
  return (
    <tr className="border-b border-border/40 align-top" data-testid={`index-assumption-${overrideKey}`}>
      <td className="py-1.5 pr-3 text-ink-900 font-medium whitespace-nowrap">{label}</td>
      <td className="py-1.5 pr-3 tabular-nums text-right">
        <span className={figure?.source === 'override' ? 'text-brand-700' : 'text-ink-900'}>
          {fmtAssumption(figure?.value, unit)}
        </span>
      </td>
      <td className="py-1.5 pr-3 text-ink-500" data-testid={`index-assumption-source-${overrideKey}`}>
        {figureSourceText(figure)}
      </td>
      <td className="py-1.5 whitespace-nowrap">
        {editing ? (
          <div className="flex flex-col gap-1 items-end">
            <input
              aria-label={`${label} value`}
              className="w-20 border border-border rounded px-1 text-right"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
            <input
              aria-label={`${label} note`}
              className="w-56 border border-border rounded px-1"
              placeholder={NOTE_PLACEHOLDER}
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
            {needNote && !note.trim() && <span className="text-[10.5px] text-ink-400">{NOTE_REQUIRED_MESSAGE}</span>}
            <div className="flex gap-2">
              <button type="button" className="text-brand-700 font-semibold" onClick={save} disabled={disabled}>
                Save
              </button>
              <button type="button" className="text-ink-500" onClick={() => setEditing(false)}>
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <div className="flex gap-2 justify-end">
            <button type="button" className="text-brand-700 font-semibold" onClick={start} disabled={disabled}>
              Edit
            </button>
            {overridden && (
              <button type="button" className="text-ink-500" onClick={() => onReset(overrideKey)} disabled={disabled}>
                Reset
              </button>
            )}
          </div>
        )}
      </td>
    </tr>
  );
}

export function IndexMethodologyPanel({ dealId }: { dealId: string }) {
  const { toast } = useToast();
  const { deal, refresh: refreshDeal } = useDeal(dealId);
  const { run, status } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  const [data, setData] = useState<IndexMethodologyResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const liveMode = isWorkerConnected() && !!dealId && !/^\d+$/.test(dealId);

  const load = useCallback(
    (signal?: AbortSignal) => {
      if (!liveMode) return;
      api.market
        .indexMethodology(dealId, signal)
        .then((r) => {
          if (r) setData(r);
        })
        .catch(() => {});
    },
    [dealId, liveMode],
  );
  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const overrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const write = async (patch: Record<string, unknown>, note: string, rerun: boolean, msg: string) => {
    // The PATCH sends the whole blob — never write before the deal row loaded.
    if (!deal) {
      toast('Still loading this deal — try again in a moment', { type: 'error' });
      return;
    }
    setSaving(true);
    try {
      const next = applyOverridePatch(overrides, patch, note);
      await api.deals.update(dealId, { field_overrides: next });
      refreshDeal();
      load();
      if (rerun) await run();
      toast(msg, { type: 'success' });
    } catch (e) {
      toast(e instanceof Error && e.message === NOTE_REQUIRED_MESSAGE ? e.message : 'Could not save', { type: 'error' });
    } finally {
      setSaving(false);
    }
  };

  if (!data) return null;
  const busy = saving || status === 'running' || status === 'queued';
  const selected = data.methods.find((m) => m.method === data.selected) ?? null;
  const feeds = data.toggle_on;
  const hasTargets = INDEX_ASSUMPTIONS.some((a) => data.assumptions[a.key]?.source === 'override');

  const selectMethod = (id: IndexMethodId) => {
    if (id === data.selected) return;
    void write(
      { [INDEX_METHOD_KEY]: id },
      '',
      feeds && hasTargets,
      `Index methodology: ${data.methods.find((m) => m.method === id)?.label ?? id}`,
    );
  };

  return (
    <Card className="p-4 space-y-3" data-testid="index-methodology-panel">
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">Index methodology</div>
        <div className="text-[11.5px] text-ink-500 mt-0.5">
          The benchmark the subject is indexed against, and the market growth and penetration assumptions that ride on it.
        </div>
      </div>
      <div role="radiogroup" aria-label="Index methodology" className="flex flex-wrap gap-2">
        {data.methods.map((m) => {
          const active = m.method === data.selected;
          return (
            <button
              key={m.method}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={!m.available || busy}
              title={m.available ? undefined : m.disabled_reason ?? undefined}
              data-testid={`index-method-${m.method}`}
              onClick={() => selectMethod(m.method)}
              className={
                'px-3 py-1.5 rounded border text-[12px] ' +
                (active
                  ? 'border-brand-700 bg-brand-50 text-brand-700 font-semibold'
                  : m.available
                    ? 'border-border text-ink-900'
                    : 'border-border text-ink-400 cursor-not-allowed')
              }
            >
              {m.label}
            </button>
          );
        })}
      </div>
      {data.methods
        .filter((m) => !m.available && m.disabled_reason)
        .map((m) => (
          <div key={m.method} className="text-[11px] text-ink-500" data-testid={`index-method-reason-${m.method}`}>
            <span className="font-medium">{m.label} unavailable:</span> {m.disabled_reason}
          </div>
        ))}
      {selected && (
        <div className="text-[11.5px]" data-testid="index-method-sources">
          <div className="font-semibold text-ink-700">
            {selected.label}
            {selected.segment ? ` — ${selected.segment.replace(/_/g, ' ')} segment` : ''}
            {data.selected_source === 'default' ? ' (default)' : ''}
          </div>
          <div className="text-ink-600 mt-0.5">
            Occupancy {fmtOcc(selected.occupancy.value)} — {figureSourceText(selected.occupancy)}
          </div>
          <div className="text-ink-600">
            ADR {fmtAdr(selected.adr.value)} — {figureSourceText(selected.adr)}
          </div>
        </div>
      )}
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-ink-500 text-[10.5px] border-b border-border">
            <th className="text-left font-medium py-1">Assumption</th>
            <th className="text-right font-medium py-1 pr-3">Value</th>
            <th className="text-left font-medium py-1">Source</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {INDEX_ASSUMPTIONS.map((a) => (
            <AssumptionRow
              key={a.key}
              label={a.label}
              unit={a.unit}
              overrideKey={a.key}
              figure={data.assumptions[a.key]}
              overridden={a.key in overrides}
              disabled={busy}
              onSave={(key, value, note) => void write({ [key]: value }, note, feeds, `${a.label} saved`)}
              onReset={(key) => void write({ [key]: null }, '', feeds, `${a.label} reset to source`)}
            />
          ))}
        </tbody>
      </table>
      <div className="text-[11px] text-ink-500" data-testid="index-feeds-note">
        {feeds
          ? 'These assumptions feed Projections now: "Use STR rates in the model" is on, so your overrides set Year-1 occupancy / ADR (benchmark × penetration target) and the growth rates.'
          : 'These assumptions do not feed Projections yet: turn on "Use STR rates in the model" in Market Overview to apply your overrides. Until then Projections stay on their current basis.'}
      </div>
    </Card>
  );
}
