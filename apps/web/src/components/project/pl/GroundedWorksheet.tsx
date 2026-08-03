'use client';

/**
 * GroundedWorksheet — the "editable Excel, grounded" worksheet.
 *
 * A multi-year USALI operating statement where:
 *   • Historical year columns are GROUNDED FACTS pulled from the deal's
 *     extracted P&Ls (read-only here — you correct history at its source
 *     document, via the click-to-source panel → Review).
 *   • The "Year 1 · Model" column is your ASSUMPTION LAYER — expense-actual
 *     lines are inline-editable, GOP & NOI recompute live, and edits persist
 *     as tracked field_overrides that re-model the whole deal.
 *
 * Every cell carries its provenance (🟢 grounded / 🟡 seed-benchmark /
 * 🟣 override / 🔵 computed) and opens a source panel showing the exact
 * document, extracted line, page, and confidence it came from.
 *
 * Historical detail (A&G, insurance, mgmt fee, …) is often not broken out in
 * the source P&Ls — those cells render "—" while the Undistributed / Fixed
 * SUBTOTALS still reconcile against the historical aggregate. That's honest:
 * we show the totals we have and the Y1 detail you can edit.
 */

import { useMemo, useState, useEffect, useCallback } from 'react';
import { Loader2, RotateCcw, X, FileText, Info } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn, fmtCurrency } from '@/lib/format';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected, workerUrl } from '@/lib/api';
import type { ExtractionField, ExtractionResult, WorkerDocument } from '@/lib/api';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceKind, sourceExplanation } from '@/lib/provenance';

// ── Row model ──────────────────────────────────────────────────────────
// histField = key on a historical_baseline year (null → no historical value).
// overrideKey = canonical field_overrides key (present → editable in Model col).
type RowKind = 'section' | 'input' | 'subtotal' | 'computed';
interface RowDef {
  id: string;
  label: string;
  kind: RowKind;
  histField?: string;      // field on HistoricalYear
  overrideKey?: string;    // editable model-column key
  y1Read?: string[];       // dotted path into expense engine years[0]
  y1Rev?: boolean;         // read revenue line from fb engine years[0] instead
  compute?: (v: Record<string, number>) => number; // model-col derived value
}

const sumKeys = (v: Record<string, number>, keys: string[]) =>
  keys.reduce((s, k) => s + (v[k] ?? 0), 0);

const UNDIST_IDS = ['ag', 'sm', 'pom', 'util', 'it'];
const FIXED_FEE_IDS = ['mgmt', 'ffe', 'taxes', 'insurance'];
const DEPT_IDS = ['rooms_dept', 'fb_dept', 'other_dept'];

const ROWS: RowDef[] = [
  { id: 's_rev', label: 'Revenue', kind: 'section' },
  { id: 'rooms_rev', label: 'Rooms Revenue', kind: 'input', histField: 'rooms_revenue', y1Rev: true, y1Read: ['rooms_revenue'] },
  { id: 'fb_rev', label: 'Food & Beverage Revenue', kind: 'input', histField: 'fnb_revenue', y1Rev: true, y1Read: ['fb_revenue'] },
  { id: 'other_rev', label: 'Other Revenue', kind: 'input', histField: 'other_revenue', y1Rev: true, y1Read: ['other_revenue'] },
  { id: 'total_rev', label: 'Total Revenue', kind: 'subtotal', histField: 'total_revenue',
    compute: (v) => v.rooms_rev + v.fb_rev + v.other_rev },

  { id: 's_dept', label: 'Departmental Expenses', kind: 'section' },
  { id: 'rooms_dept', label: 'Rooms', kind: 'input', histField: 'rooms_dept_expense', overrideKey: 'rooms_dept_expense', y1Read: ['dept_expenses', 'rooms'] },
  { id: 'fb_dept', label: 'Food & Beverage', kind: 'input', histField: 'fnb_dept_expense', overrideKey: 'fb_dept_expense', y1Read: ['dept_expenses', 'food_beverage'] },
  { id: 'other_dept', label: 'Other Operated', kind: 'input', histField: 'other_dept_expense', overrideKey: 'other_dept_expense', y1Read: ['dept_expenses', 'other_operated'] },

  { id: 's_undist', label: 'Undistributed Operating Expenses', kind: 'section' },
  { id: 'ag', label: 'Administrative & General', kind: 'input', overrideKey: 'administrative_general', y1Read: ['undistributed', 'administrative_general'] },
  { id: 'sm', label: 'Sales & Marketing', kind: 'input', overrideKey: 'sales_marketing', y1Read: ['undistributed', 'sales_marketing'] },
  { id: 'pom', label: 'Property Operations', kind: 'input', overrideKey: 'property_operations', y1Read: ['undistributed', 'property_operations'] },
  { id: 'util', label: 'Utilities', kind: 'input', overrideKey: 'utilities', y1Read: ['undistributed', 'utilities'] },
  { id: 'it', label: 'Information & Telecom', kind: 'input', overrideKey: 'information_telecom', y1Read: ['undistributed', 'information_telecom'] },
  { id: 'undist_total', label: 'Total Undistributed', kind: 'subtotal', histField: 'undistributed',
    compute: (v) => sumKeys(v, UNDIST_IDS) },

  { id: 'gop', label: 'Gross Operating Profit (GOP)', kind: 'computed', histField: 'gop',
    compute: (v) => v.total_rev - sumKeys(v, DEPT_IDS) - sumKeys(v, UNDIST_IDS) },

  { id: 's_fixed', label: 'Management Fee & Fixed Charges', kind: 'section' },
  { id: 'mgmt', label: 'Management Fee', kind: 'input', overrideKey: 'mgmt_fee', y1Read: ['mgmt_fee'] },
  { id: 'ffe', label: 'FF&E Reserve', kind: 'input', overrideKey: 'ffe_reserve', y1Read: ['ffe_reserve'] },
  { id: 'taxes', label: 'Property Taxes', kind: 'input', overrideKey: 'property_taxes', y1Read: ['fixed_charges', 'property_taxes'] },
  { id: 'insurance', label: 'Insurance', kind: 'input', overrideKey: 'insurance', y1Read: ['fixed_charges', 'insurance'] },
  { id: 'fixed_total', label: 'Total Fees & Fixed', kind: 'subtotal', histField: 'fixed_expenses',
    compute: (v) => sumKeys(v, FIXED_FEE_IDS) },

  { id: 'noi', label: 'Net Operating Income (NOI)', kind: 'computed', histField: 'noi',
    compute: (v) => v.total_rev - sumKeys(v, DEPT_IDS) - sumKeys(v, UNDIST_IDS) - sumKeys(v, FIXED_FEE_IDS) },
];

const num = (x: unknown): number => (typeof x === 'number' && Number.isFinite(x) ? x : 0);

// Shape returned by GET /deals/{id}/historicals (see HistoricalsSection).
// Revenue lines are rooms / fb / misc; expense detail below GOP is a single
// `undistributed` + `fixed_expenses` rollup (no per-line breakout).
interface HistYear {
  year: string;
  rooms: number | null;
  fb: number | null;
  misc: number | null;
  rooms_dept_expense: number | null;
  fb_dept_expense: number | null;
  other_dept_expense: number | null;
  undistributed: number | null;
  gop: number | null;
  fixed_expenses: number | null;
  noi: number | null;
  populated?: boolean;
}

const nOrNull = (x: unknown): number | null =>
  typeof x === 'number' && Number.isFinite(x) ? x : null;

// Historical value for a worksheet row from one /historicals year. Detail
// rows (A&G, insurance, mgmt fee, …) return null — the source P&Ls only
// carry the rolled-up subtotals, so those cells render "—".
function histValue(rowId: string, h: HistYear): number | null {
  switch (rowId) {
    case 'rooms_rev': return nOrNull(h.rooms);
    case 'fb_rev': return nOrNull(h.fb);
    case 'other_rev': return nOrNull(h.misc);
    case 'total_rev': {
      const parts = [h.rooms, h.fb, h.misc].map(nOrNull).filter((x): x is number => x != null);
      return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
    }
    case 'rooms_dept': return nOrNull(h.rooms_dept_expense);
    case 'fb_dept': return nOrNull(h.fb_dept_expense);
    case 'other_dept': return nOrNull(h.other_dept_expense);
    case 'undist_total': return nOrNull(h.undistributed);
    case 'gop': return nOrNull(h.gop);
    case 'fixed_total': return nOrNull(h.fixed_expenses);
    case 'noi': return nOrNull(h.noi);
    default: return null;
  }
}

const histHasData = (h: HistYear) =>
  [h.noi, h.gop, h.rooms, h.fb].some((x) => nOrNull(x) != null);

interface InspectTarget {
  rowLabel: string;
  colLabel: string;
  kind: 'grounded' | 'benchmark' | 'override' | 'computed';
  value: number;
  overrideKey?: string;
  docIds: string[];
  formula?: string;
}

export default function GroundedWorksheet({
  dealId,
  isKimptonDemo,
}: {
  dealId: string | number;
  isKimptonDemo?: boolean;
}) {
  const rawId = String(dealId);
  const { outputs, refresh } = useEngineOutputs(rawId);
  const { deal, refresh: refreshDeal } = useDeal(rawId);
  const { documents, extractions } = useDocuments(rawId);
  const { toast } = useToast();
  const { run, status } = useEngineRun(rawId, 'returns', { runMode: 'all' });
  const running = status === 'running' || status === 'queued';

  const [draft, setDraft] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);
  const [inspect, setInspect] = useState<InspectTarget | null>(null);

  const expY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'expense', 'years') ?? [])[0] ?? {}, [outputs]);
  const fbY0 = useMemo(() => (getEngineField<Record<string, unknown>[]>(outputs, 'fb', 'years') ?? [])[0] ?? {}, [outputs]);

  // Multi-year grounded columns come from GET /deals/{id}/historicals (the
  // same source HistoricalsSection uses). Absent / 404 → we render the Model
  // column alone. Keep the last 4 populated years.
  const [histYears, setHistYears] = useState<HistYear[]>([]);
  useEffect(() => {
    const isMockId = /^\d+$/.test(rawId);
    if (isKimptonDemo || isMockId || !isWorkerConnected()) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${workerUrl()}/deals/${rawId}/historicals`);
        if (!res.ok) return;
        const json = (await res.json()) as { years?: HistYear[] } | null;
        if (!cancelled && Array.isArray(json?.years)) {
          setHistYears(json!.years.filter(histHasData).slice(-4));
        }
      } catch {
        /* worker offline / route absent — model column still renders */
      }
    })();
    return () => { cancelled = true; };
  }, [rawId, isKimptonDemo]);

  // Model-column base (pre-edit) values per row id.
  const modelBase = useMemo<Record<string, number>>(() => {
    const readPath = (obj: Record<string, unknown>, path: string[]) => {
      let cur: unknown = obj;
      for (const p of path) cur = (cur as Record<string, unknown>)?.[p];
      return num(cur);
    };
    const v: Record<string, number> = {};
    for (const r of ROWS) {
      if (!r.y1Read) continue;
      v[r.id] = readPath(r.y1Rev ? fbY0 : expY0, r.y1Read);
    }
    return v;
  }, [expY0, fbY0]);

  // Model-column live values (base + draft edits), then derived rows.
  const modelLive = useMemo<Record<string, number>>(() => {
    const v = { ...modelBase };
    for (const [k, s] of Object.entries(draft)) {
      const n = Number(s.replace(/[$,\s]/g, ''));
      if (Number.isFinite(n)) {
        const row = ROWS.find((r) => r.overrideKey === k);
        if (row) v[row.id] = n;
      }
    }
    // resolve derived rows in declared order (total_rev before gop/noi)
    for (const r of ROWS) {
      if (r.compute) v[r.id] = r.compute(v);
    }
    return v;
  }, [modelBase, draft]);

  const overrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const isOverridden = (key?: string) => !!key && key in overrides;

  const save = useCallback(
    async (key: string) => {
      const s = draft[key];
      if (s == null) return;
      const n = Number(s.replace(/[$,\s]/g, ''));
      if (!Number.isFinite(n)) { toast('Enter a number', { type: 'error' }); return; }
      setSavingKey(key);
      try {
        const next = { ...overrides, [key]: { value: n, note: 'Edited on the Financials worksheet' } };
        await api.deals.update(rawId, { field_overrides: next });
        await refreshDeal();
        await run();
        await refresh();
        setDraft((d) => { const { [key]: _d, ...rest } = d; return rest; });
        toast('Saved + re-modeled', { type: 'success' });
      } catch (err) {
        toast(`Couldn’t save: ${err instanceof Error ? err.message : String(err)}`, { type: 'error' });
      } finally {
        setSavingKey(null);
      }
    },
    [draft, overrides, rawId, refreshDeal, run, refresh, toast],
  );

  const reset = useCallback(
    async (key: string) => {
      setSavingKey(key);
      try {
        const { [key]: _drop, ...rest } = overrides;
        await api.deals.update(rawId, { field_overrides: rest });
        await refreshDeal();
        await run();
        await refresh();
        toast('Reset to source', { type: 'success' });
      } catch (err) {
        toast(`Couldn’t reset: ${err instanceof Error ? err.message : String(err)}`, { type: 'error' });
      } finally {
        setSavingKey(null);
      }
    },
    [overrides, rawId, refreshDeal, run, refresh, toast],
  );

  if (isKimptonDemo) {
    return <Card className="p-6 text-[13px] text-ink-500">The editable worksheet is available on live deals.</Card>;
  }
  if (num(expY0['total_revenue']) === 0) {
    return (
      <Card className="p-6 text-[13px] text-ink-500">
        Run the model (upload financials + run the engines) to populate the worksheet.
      </Card>
    );
  }

  const cols = [
    ...histYears.map((y, i) => ({
      id: `h${y.year}-${i}`,
      label: y.year,
      historical: true as const,
      year: y,
    })),
    { id: 'model', label: 'Year 1 · Model', historical: false as const, year: null },
  ];

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-surface-2/40">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">Operating worksheet</h3>
          <p className="text-[11.5px] text-ink-500 mt-0.5">
            Historical years are grounded facts · <span className="text-brand-700 font-medium">Year 1 · Model</span> is editable —
            click any expense line to edit, or any cell’s dot to see its source.
          </p>
        </div>
        {running && (
          <span className="inline-flex items-center gap-1.5 text-[11.5px] text-brand-700">
            <Loader2 size={12} className="animate-spin" /> Re-modeling…
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]" style={{ minWidth: 320 + cols.length * 110 }}>
          <thead>
            <tr className="text-ink-500 text-[10px] uppercase tracking-wider border-b border-border">
              <th className="text-left font-semibold px-5 py-2 sticky left-0 bg-bg z-10">Line item</th>
              {cols.map((c) => (
                <th key={c.id} className={cn('text-right font-semibold px-3 py-2 w-[110px]', !c.historical && 'text-brand-700')}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => {
              if (row.kind === 'section') {
                return (
                  <tr key={row.id} className="bg-ink-100/50">
                    <td colSpan={cols.length + 1} className="px-5 py-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-ink-600">
                      {row.label}
                    </td>
                  </tr>
                );
              }
              const emphatic = row.kind === 'computed' || row.kind === 'subtotal';
              return (
                <tr key={row.id} className={cn('border-t border-border hover:bg-ink-100/30 group', emphatic && 'bg-brand-50/25')}>
                  <td className={cn('px-5 py-1.5 text-ink-800 sticky left-0 z-10', emphatic ? 'font-semibold text-ink-900 bg-brand-50/25' : 'pl-9 bg-bg')}>
                    {row.label}
                  </td>
                  {cols.map((c) => (
                    <WorksheetCell
                      key={c.id}
                      row={row}
                      historical={c.historical}
                      histYear={c.year}
                      modelLive={modelLive}
                      overridden={!c.historical && isOverridden(row.overrideKey)}
                      draft={row.overrideKey ? draft[row.overrideKey] : undefined}
                      saving={!!row.overrideKey && savingKey === row.overrideKey}
                      onDraft={(s) => row.overrideKey && setDraft((d) => ({ ...d, [row.overrideKey!]: s }))}
                      onSave={() => row.overrideKey && save(row.overrideKey)}
                      onCancel={() => row.overrideKey && setDraft((d) => { const { [row.overrideKey!]: _x, ...rest } = d; return rest; })}
                      onInspect={(t) => setInspect(t)}
                      colLabel={c.label}
                    />
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="px-5 py-2.5 border-t border-border text-[11px] text-ink-500 flex items-center gap-1.5">
        <Info size={11} /> Editable lines are the operating-expense actuals in the Model column. Revenue &amp; subtotals are derived; historical columns are read-only facts — correct them at their source document.
      </div>

      {inspect && (
        <SourcePanel
          target={inspect}
          documents={documents}
          extractions={extractions}
          overrides={overrides}
          onClose={() => setInspect(null)}
          onReset={inspect.overrideKey && isOverridden(inspect.overrideKey)
            ? () => { reset(inspect.overrideKey!); setInspect(null); }
            : undefined}
        />
      )}
    </Card>
  );
}

// ── One cell (historical read-only, model editable, computed) ──────────
function WorksheetCell({
  row, historical, histYear, modelLive, overridden, draft, saving, colLabel,
  onDraft, onSave, onCancel, onInspect,
}: {
  row: RowDef;
  historical: boolean;
  histYear: HistYear | null;
  modelLive: Record<string, number>;
  overridden: boolean;
  draft: string | undefined;
  saving: boolean;
  colLabel: string;
  onDraft: (s: string) => void;
  onSave: () => void;
  onCancel: () => void;
  onInspect: (t: InspectTarget) => void;
}) {
  const resolved = useSource(!historical ? row.overrideKey : undefined);

  // Value for this cell.
  let value: number | null = null;
  if (historical) {
    value = histYear ? histValue(row.id, histYear) : null;
  } else {
    value = modelLive[row.id] ?? 0;
  }

  // Provenance kind for the dot.
  let kind: InspectTarget['kind'];
  if (row.kind === 'computed' || row.kind === 'subtotal') kind = 'computed';
  else if (historical) kind = 'grounded';
  else if (overridden) kind = 'override';
  else {
    const k = resolved?.source ? sourceKind(resolved.source) : null;
    kind = k ?? 'benchmark';
  }
  const dotCls =
    kind === 'grounded' ? 'bg-emerald-500'
    : kind === 'override' ? 'bg-violet-500'
    : kind === 'computed' ? 'bg-sky-500'
    : 'bg-amber-500';

  const openInspect = () => {
    if (value == null) return;
    const docIds = historical
      ? []
      : (resolved?.docId ? [resolved.docId] : []);
    onInspect({
      rowLabel: row.label,
      colLabel,
      kind,
      value,
      overrideKey: !historical ? row.overrideKey : undefined,
      docIds,
      formula: row.kind === 'computed' || row.kind === 'subtotal' ? formulaFor(row.id) : undefined,
    });
  };

  if (value == null) {
    return <td className="px-3 py-1.5 text-right text-ink-300">—</td>;
  }

  const editing = draft != null;
  const editable = !historical && !!row.overrideKey;

  return (
    <td className="px-3 py-1.5 text-right whitespace-nowrap">
      <span className="inline-flex items-center gap-1.5 justify-end">
        <button
          type="button"
          onClick={openInspect}
          title="See where this came from"
          className={cn('w-1.5 h-1.5 rounded-full shrink-0 hover:ring-2 hover:ring-offset-1 hover:ring-ink-300', dotCls)}
        />
        {editing ? (
          <input
            autoFocus
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') onSave(); if (e.key === 'Escape') onCancel(); }}
            onBlur={onSave}
            className="w-24 px-1.5 py-0.5 text-[12.5px] text-right tabular-nums border border-brand-500 rounded focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        ) : editable ? (
          <button
            type="button"
            onClick={() => onDraft(String(Math.round(value!)))}
            disabled={saving}
            title="Click to edit"
            className={cn('tabular-nums px-1 rounded hover:bg-brand-50', overridden ? 'text-violet-700 font-medium' : 'text-ink-900')}
          >
            {saving ? <Loader2 size={11} className="animate-spin inline" /> : fmtCurrency(value, { compact: true })}
          </button>
        ) : (
          <span className={cn('tabular-nums', (row.kind === 'computed' || row.kind === 'subtotal') ? 'font-semibold text-ink-900' : 'text-ink-700')}>
            {fmtCurrency(value, { compact: true })}
          </span>
        )}
      </span>
    </td>
  );
}

function formulaFor(id: string): string {
  switch (id) {
    case 'total_rev': return 'Rooms + F&B + Other Revenue';
    case 'undist_total': return 'A&G + Sales & Marketing + Property Ops + Utilities + Info & Telecom';
    case 'fixed_total': return 'Management Fee + FF&E Reserve + Property Taxes + Insurance';
    case 'gop': return 'Total Revenue − Departmental Expenses − Total Undistributed';
    case 'noi': return 'Total Revenue − Departmental − Undistributed − Fees & Fixed';
    default: return '';
  }
}

// ── Click-to-source slide-over ─────────────────────────────────────────
function SourcePanel({
  target, documents, extractions, overrides, onClose, onReset,
}: {
  target: InspectTarget;
  documents: WorkerDocument[];
  extractions: Record<string, ExtractionResult | undefined>;
  overrides: Record<string, unknown>;
  onClose: () => void;
  onReset?: () => void;
}) {
  const docs = target.docIds
    .map((id) => documents.find((d) => d.id === id))
    .filter((d): d is WorkerDocument => Boolean(d));

  // Try to locate the exact extracted field behind a model-column key.
  const field: (ExtractionField & { docName: string }) | null = (() => {
    if (!target.overrideKey) return null;
    for (const d of documents) {
      const ex = extractions[d.id];
      if (!ex?.fields) continue;
      const f = ex.fields.find((ff) => fieldMatchesKey(ff.field_name, target.overrideKey!));
      if (f) return { ...f, docName: d.filename };
    }
    return null;
  })();

  const overrideNote = (() => {
    if (target.kind !== 'override' || !target.overrideKey) return null;
    const raw = overrides[target.overrideKey];
    if (raw && typeof raw === 'object' && 'note' in raw) return String((raw as { note?: unknown }).note ?? '');
    return null;
  })();

  const kindLabel =
    target.kind === 'grounded' ? 'Grounded in your documents'
    : target.kind === 'override' ? 'Your override'
    : target.kind === 'computed' ? 'Computed'
    : 'Seed / benchmark default';
  const kindDot =
    target.kind === 'grounded' ? 'bg-emerald-500'
    : target.kind === 'override' ? 'bg-violet-500'
    : target.kind === 'computed' ? 'bg-sky-500'
    : 'bg-amber-500';

  return (
    <div className="fixed inset-0 z-50 flex justify-end" onClick={onClose}>
      <div className="absolute inset-0 bg-ink-900/20" />
      <div
        className="relative w-full max-w-sm h-full bg-bg border-l border-border shadow-xl overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between px-5 py-4 border-b border-border">
          <div>
            <div className="text-[10.5px] uppercase tracking-wider text-ink-500">{target.colLabel}</div>
            <h4 className="text-[15px] font-semibold text-ink-900 mt-0.5">{target.rowLabel}</h4>
            <div className="text-[19px] font-semibold tabular-nums text-ink-900 mt-1">{fmtCurrency(target.value)}</div>
          </div>
          <button type="button" onClick={onClose} className="p-1 text-ink-400 hover:text-ink-900">
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4">
          <div className="flex items-center gap-2 text-[12.5px]">
            <span className={cn('w-2 h-2 rounded-full', kindDot)} />
            <span className="font-medium text-ink-900">{kindLabel}</span>
          </div>

          {target.formula && (
            <div className="rounded-lg bg-sky-50 border border-sky-100 px-3 py-2.5">
              <div className="text-[10.5px] uppercase tracking-wide text-sky-700 font-semibold mb-1">Formula</div>
              <div className="text-[12px] text-ink-700">{target.formula}</div>
            </div>
          )}

          {overrideNote && (
            <div className="rounded-lg bg-violet-50 border border-violet-100 px-3 py-2.5">
              <div className="text-[10.5px] uppercase tracking-wide text-violet-700 font-semibold mb-1">Your note</div>
              <div className="text-[12px] text-ink-700">{overrideNote}</div>
            </div>
          )}

          {docs.length > 0 && (
            <div>
              <div className="text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold mb-1.5">
                Source document{docs.length > 1 ? 's' : ''}
              </div>
              <div className="space-y-1.5">
                {docs.map((d) => (
                  <div key={d.id} className="flex items-start gap-2 rounded-lg border border-border px-3 py-2">
                    <FileText size={14} className="text-ink-400 mt-0.5 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-[12px] text-ink-900 truncate">{d.filename}</div>
                      {d.doc_type && <div className="text-[10.5px] text-ink-500">{d.doc_type}</div>}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {field && (
            <div className="rounded-lg bg-emerald-50 border border-emerald-100 px-3 py-2.5 space-y-1.5">
              <div className="text-[10.5px] uppercase tracking-wide text-emerald-700 font-semibold">Extracted line</div>
              <div className="text-[12px] text-ink-800"><span className="text-ink-500">field</span> · {field.field_name}</div>
              {field.raw_text && <div className="text-[12px] text-ink-800 italic">“{field.raw_text}”</div>}
              <div className="flex items-center gap-3 text-[11px] text-ink-600">
                {field.source_page != null && <span>page {field.source_page}</span>}
                {field.confidence != null && <span>{Math.round(field.confidence * 100)}% confidence</span>}
                {field.reviewed && <span className="text-emerald-700">✓ {field.reviewed}</span>}
              </div>
            </div>
          )}

          {target.kind === 'grounded' && docs.length === 0 && (
            <p className="text-[11.5px] text-ink-500 leading-relaxed">Extracted from this year’s uploaded P&amp;L.</p>
          )}
          {target.kind === 'benchmark' && (
            <p className="text-[11.5px] text-ink-500 leading-relaxed">{sourceExplanation('seed')}</p>
          )}

          {onReset && (
            <button
              type="button"
              onClick={onReset}
              className="inline-flex items-center gap-1.5 text-[12px] text-brand-700 hover:text-brand-500 font-medium"
            >
              <RotateCcw size={12} /> Reset to source value
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// Fuzzy match an extraction field_name to a canonical override key. Handles
// USALI dotted paths, *_usd suffixes, and fb/f_and_b variants.
function fieldMatchesKey(fieldName: string, key: string): boolean {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z]/g, '');
  const fn = norm(fieldName);
  const aliases: Record<string, string[]> = {
    insurance: ['insurance'],
    property_taxes: ['propertytax', 'propertytaxes', 'realestatetax'],
    mgmt_fee: ['managementfee', 'mgmtfee', 'basemanagementfee'],
    ffe_reserve: ['ffereserve', 'ffe', 'reservereplacement', 'replacementreserve'],
    administrative_general: ['administrativegeneral', 'adminandgeneral'],
    sales_marketing: ['salesmarketing', 'salesandmarketing'],
    property_operations: ['propertyoperations', 'propertyoperationsmaintenance'],
    utilities: ['utilities'],
    information_telecom: ['informationtelecom', 'infotelecom'],
    rooms_dept_expense: ['roomsexpense', 'roomsdepartmentexpense', 'roomsdept'],
    fb_dept_expense: ['fbexpense', 'foodbeverageexpense'],
    other_dept_expense: ['otheroperatedexpense', 'otherdepartmentexpense'],
  };
  const cands = aliases[key] ?? [norm(key)];
  return cands.some((c) => c.length > 2 && fn.includes(c));
}
