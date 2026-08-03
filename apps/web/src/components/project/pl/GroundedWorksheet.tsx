'use client';

/**
 * GroundedWorksheet — P1 of the "editable Excel, grounded" vision.
 *
 * The Year-1 operating statement as a USALI tree-grid you can edit like a
 * spreadsheet — but every cell carries its provenance (🟢 from your docs /
 * 🟡 seed-benchmark / 🟣 your override / 🔵 computed), edits are TRACKED
 * overrides (never silent), and the subtotals (GOP, NOI) recompute live as
 * you type. Editable lines are the operating-expense actuals — the
 * directly-overridable inputs (this is where the Kimpton insurance outlier
 * lived); revenue is grounded/computed, GOP + NOI are derived.
 *
 * Persistence: an edit merges into the deal's field_overrides (with a note)
 * and re-runs the engines, so the whole model reflects it. Reset restores the
 * extracted / benchmark value.
 */

import { useMemo, useState, useCallback } from 'react';
import { Loader2, RotateCcw, Info } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn, fmtCurrency } from '@/lib/format';
import { useToast } from '@/components/ui/Toast';
import { api } from '@/lib/api';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { useDeal } from '@/lib/hooks/useDeal';
import { useSource } from '@/lib/hooks/useDealProvenance';
import { sourceKind, sourceLabel } from '@/lib/provenance';

// A worksheet row. `read` = dotted path into the expense engine's years[0].
// `overrideKey` = the canonical field_overrides key (editable rows only).
type Row =
  | { kind: 'section'; label: string }
  | { kind: 'input'; label: string; read: string[]; overrideKey: string; indent?: boolean }
  | { kind: 'computed'; label: string; compute: (v: Vals) => number; bold?: boolean };

type Vals = Record<string, number>;

const DEPT = [
  { label: 'Rooms', read: ['dept_expenses', 'rooms'], overrideKey: 'rooms_dept_expense' },
  { label: 'Food & Beverage', read: ['dept_expenses', 'food_beverage'], overrideKey: 'fb_dept_expense' },
  { label: 'Other Operated', read: ['dept_expenses', 'other_operated'], overrideKey: 'other_dept_expense' },
] as const;
const UNDIST = [
  { label: 'Administrative & General', read: ['undistributed', 'administrative_general'], overrideKey: 'administrative_general' },
  { label: 'Sales & Marketing', read: ['undistributed', 'sales_marketing'], overrideKey: 'sales_marketing' },
  { label: 'Property Operations', read: ['undistributed', 'property_operations'], overrideKey: 'property_operations' },
  { label: 'Utilities', read: ['undistributed', 'utilities'], overrideKey: 'utilities' },
  { label: 'Information & Telecom', read: ['undistributed', 'information_telecom'], overrideKey: 'information_telecom' },
] as const;
const FIXED = [
  { label: 'Property Taxes', read: ['fixed_charges', 'property_taxes'], overrideKey: 'property_taxes' },
  { label: 'Insurance', read: ['fixed_charges', 'insurance'], overrideKey: 'insurance' },
] as const;

const sum = (v: Vals, keys: readonly string[]) => keys.reduce((s, k) => s + (v[k] ?? 0), 0);
const DEPT_KEYS = DEPT.map((r) => r.overrideKey);
const UNDIST_KEYS = UNDIST.map((r) => r.overrideKey);
const FIXED_KEYS = FIXED.map((r) => r.overrideKey);

const ROWS: Row[] = [
  { kind: 'section', label: 'Revenue' },
  { kind: 'computed', label: 'Total Revenue', compute: (v) => v.total_revenue ?? 0, bold: true },
  { kind: 'section', label: 'Departmental Expenses' },
  ...DEPT.map((r) => ({ kind: 'input' as const, label: r.label, read: [...r.read], overrideKey: r.overrideKey, indent: true })),
  { kind: 'section', label: 'Undistributed Operating Expenses' },
  ...UNDIST.map((r) => ({ kind: 'input' as const, label: r.label, read: [...r.read], overrideKey: r.overrideKey, indent: true })),
  { kind: 'computed', label: 'Gross Operating Profit (GOP)', bold: true,
    compute: (v) => (v.total_revenue ?? 0) - sum(v, DEPT_KEYS) - sum(v, UNDIST_KEYS) },
  { kind: 'section', label: 'Fees & Fixed Charges' },
  { kind: 'input', label: 'Management Fee', read: ['mgmt_fee'], overrideKey: 'mgmt_fee', indent: true },
  { kind: 'input', label: 'FF&E Reserve', read: ['ffe_reserve'], overrideKey: 'ffe_reserve', indent: true },
  ...FIXED.map((r) => ({ kind: 'input' as const, label: r.label, read: [...r.read], overrideKey: r.overrideKey, indent: true })),
  { kind: 'computed', label: 'Net Operating Income (NOI)', bold: true,
    compute: (v) =>
      (v.total_revenue ?? 0) - sum(v, DEPT_KEYS) - sum(v, UNDIST_KEYS)
      - (v.mgmt_fee ?? 0) - (v.ffe_reserve ?? 0) - sum(v, FIXED_KEYS) },
];

const ALL_INPUT_KEYS = [...DEPT_KEYS, ...UNDIST_KEYS, FIXED_KEYS, 'mgmt_fee', 'ffe_reserve'].flat();

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
  const { toast } = useToast();
  const { run, status } = useEngineRun(rawId, 'returns', { runMode: 'all' });
  const running = status === 'running' || status === 'queued';

  const [draft, setDraft] = useState<Record<string, string>>({});
  const [savingKey, setSavingKey] = useState<string | null>(null);

  // Base values from the expense engine's Year-1 output.
  const base = useMemo<Vals>(() => {
    const y0 = (getEngineField<Record<string, unknown>[]>(outputs, 'expense', 'years') ?? [])[0] ?? {};
    const get = (path: string[]) => {
      let cur: unknown = y0;
      for (const p of path) cur = (cur as Record<string, unknown>)?.[p];
      return typeof cur === 'number' && Number.isFinite(cur) ? cur : 0;
    };
    const v: Vals = { total_revenue: get(['total_revenue']) };
    for (const r of [...DEPT, ...UNDIST, ...FIXED]) v[r.overrideKey] = get([...r.read]);
    v.mgmt_fee = get(['mgmt_fee']);
    v.ffe_reserve = get(['ffe_reserve']);
    return v;
  }, [outputs]);

  // Current values = base, with any in-flight draft edits overlaid, for live
  // GOP / NOI recompute as the analyst types.
  const live = useMemo<Vals>(() => {
    const v = { ...base };
    for (const [k, s] of Object.entries(draft)) {
      const n = Number(s.replace(/[$,\s]/g, ''));
      if (Number.isFinite(n)) v[k] = n;
    }
    return v;
  }, [base, draft]);

  const overrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const isOverridden = (key: string) => key in overrides;

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
        await run(); // re-run engines so GOP/NOI/returns reflect the edit
        await refresh();
        setDraft((d) => { const { [key]: _, ...rest } = d; return rest; });
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
  if (base.total_revenue === 0) {
    return (
      <Card className="p-6 text-[13px] text-ink-500">
        Run the model (upload financials + run the engines) to populate the worksheet.
      </Card>
    );
  }

  const rev = live.total_revenue || 1;

  return (
    <Card className="p-0 overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3 border-b border-border bg-surface-2/40">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">Operating worksheet · Year 1</h3>
          <p className="text-[11.5px] text-ink-500 mt-0.5">
            Edit any expense line — GOP &amp; NOI recompute live and re-model the deal. Every value shows where it came from.
          </p>
        </div>
        {running && (
          <span className="inline-flex items-center gap-1.5 text-[11.5px] text-brand-700">
            <Loader2 size={12} className="animate-spin" /> Re-modeling…
          </span>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[12.5px]" style={{ minWidth: 560 }}>
          <thead>
            <tr className="text-ink-500 text-[10px] uppercase tracking-wider border-b border-border">
              <th className="text-left font-semibold px-5 py-2">Line item</th>
              <th className="text-right font-semibold px-3 py-2 w-40">Amount</th>
              <th className="text-right font-semibold px-3 py-2 w-16">% Rev</th>
              <th className="text-left font-semibold px-4 py-2 w-40">Source</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row, i) => {
              if (row.kind === 'section') {
                return (
                  <tr key={`s-${i}`} className="bg-ink-100/50">
                    <td colSpan={4} className="px-5 py-1.5 text-[10.5px] font-semibold uppercase tracking-wide text-ink-600">
                      {row.label}
                    </td>
                  </tr>
                );
              }
              if (row.kind === 'computed') {
                const val = row.compute(live);
                return (
                  <tr key={`c-${i}`} className={cn('border-t border-border bg-brand-50/30', row.bold && 'font-semibold')}>
                    <td className="px-5 py-2 text-ink-900">{row.label}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink-900">{fmtCurrency(val)}</td>
                    <td className="px-3 py-2 text-right tabular-nums text-ink-500">{((val / rev) * 100).toFixed(1)}%</td>
                    <td className="px-4 py-2">
                      <span className="inline-flex items-center gap-1 text-[10.5px] text-sky-700">
                        <span className="w-1.5 h-1.5 rounded-full bg-sky-500" /> computed
                      </span>
                    </td>
                  </tr>
                );
              }
              return (
                <InputRow
                  key={row.overrideKey}
                  label={row.label}
                  value={live[row.overrideKey] ?? 0}
                  rev={rev}
                  overridden={isOverridden(row.overrideKey)}
                  draft={draft[row.overrideKey]}
                  saving={savingKey === row.overrideKey}
                  sourceKey={row.overrideKey}
                  onDraft={(s) => setDraft((d) => ({ ...d, [row.overrideKey]: s }))}
                  onSave={() => save(row.overrideKey)}
                  onCancel={() => setDraft((d) => { const { [row.overrideKey]: _, ...rest } = d; return rest; })}
                  onReset={() => reset(row.overrideKey)}
                />
              );
            })}
          </tbody>
        </table>
      </div>

      <div className="px-5 py-2.5 border-t border-border text-[11px] text-ink-500 flex items-center gap-1.5">
        <Info size={11} /> Editable lines are the operating-expense actuals. Revenue is grounded from your statements; GOP &amp; NOI are derived. Edits persist as tracked overrides and re-model the whole deal.
      </div>
    </Card>
  );
}

function InputRow({
  label, value, rev, overridden, draft, saving, sourceKey,
  onDraft, onSave, onCancel, onReset,
}: {
  label: string; value: number; rev: number; overridden: boolean;
  draft: string | undefined; saving: boolean; sourceKey: string;
  onDraft: (s: string) => void; onSave: () => void; onCancel: () => void; onReset: () => void;
}) {
  const resolved = useSource(sourceKey);
  const src = overridden ? 'analyst_override' : resolved?.source ?? null;
  const kind = src ? sourceKind(src) : null;
  const dotCls =
    kind === 'grounded' ? 'bg-emerald-500' : kind === 'override' ? 'bg-violet-500' : kind === 'benchmark' ? 'bg-amber-500' : 'bg-ink-300';
  const editing = draft != null;

  return (
    <tr className="border-t border-border hover:bg-ink-100/40 group">
      <td className="px-5 py-1.5 pl-9 text-ink-800">{label}</td>
      <td className="px-3 py-1.5 text-right">
        {editing ? (
          <input
            autoFocus
            value={draft}
            onChange={(e) => onDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') onSave(); if (e.key === 'Escape') onCancel(); }}
            onBlur={onSave}
            className="w-28 px-2 py-0.5 text-[12.5px] text-right tabular-nums border border-brand-500 rounded focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
        ) : (
          <button
            type="button"
            onClick={() => onDraft(String(Math.round(value)))}
            disabled={saving}
            className={cn(
              'tabular-nums px-1.5 py-0.5 rounded hover:bg-brand-50 -mr-1.5',
              overridden ? 'text-violet-700 font-medium' : 'text-ink-900',
            )}
            title="Click to edit"
          >
            {saving ? <Loader2 size={11} className="animate-spin inline" /> : fmtCurrency(value)}
          </button>
        )}
      </td>
      <td className="px-3 py-1.5 text-right tabular-nums text-ink-400">{((value / rev) * 100).toFixed(1)}%</td>
      <td className="px-4 py-1.5">
        <span className="inline-flex items-center gap-1.5 text-[10.5px] text-ink-500">
          <span className={cn('w-1.5 h-1.5 rounded-full', dotCls)} />
          {src ? sourceLabel(src) : 'seed'}
          {overridden && (
            <button type="button" onClick={onReset} disabled={saving}
              className="ml-1 opacity-0 group-hover:opacity-100 inline-flex items-center gap-0.5 text-brand-700 hover:text-brand-500">
              <RotateCcw size={10} /> reset
            </button>
          )}
        </span>
      </td>
    </tr>
  );
}
