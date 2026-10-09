'use client';
/**
 * E-016 — per-line projection methodology chip for the Future P&L.
 *
 * Each departmental / undistributed expense line shows the method the expense
 * engine ACTUALLY ran it on (the worker's `expense.line_methods`): Growth,
 * % of rev, POR or PAR. Clicking opens a small editor — method + value + a
 * required justification — that saves through the deal's `field_overrides`
 * (`projection_methods.<line>.method` / `.value`) and re-runs the model.
 *
 * Formulas (worker `engines/expense.py`):
 *   Growth    Year-1 anchor × (1 + g)^(t−1)   blank g → model expense growth
 *   % of rev  value × department revenue (departmental) / total revenue
 *   POR       value × occupied rooms (keys × 365 × occupancy)
 *   PAR       value × available rooms (keys × 365)
 * A blank value on % of rev / POR / PAR holds the line's own Year-1 ratio.
 */

import { createContext, useContext, useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/format';
import { NOTE_PLACEHOLDER, NOTE_REQUIRED_MESSAGE, noteOf } from '@/lib/overrideNote';

export type ProjectionMethodId = 'growth' | 'pct_revenue' | 'por' | 'par';

export const PROJECTION_METHOD_LINES = [
  'rooms_dept_expense',
  'fb_dept_expense',
  'other_dept_expense',
  'administrative_general',
  'information_telecom',
  'sales_marketing',
  'property_operations',
  'utilities',
] as const;
export type ProjectionMethodLine = (typeof PROJECTION_METHOD_LINES)[number];

/** Mirrors the worker's `LineMethodInfo`. */
export interface LineMethodInfo {
  method: ProjectionMethodId;
  value: number | null;
  source: 'default' | 'override';
  note?: string | null;
}

export const METHOD_LABEL: Record<ProjectionMethodId, string> = {
  growth: 'Growth',
  pct_revenue: '% of rev',
  por: 'POR',
  par: 'PAR',
};

const DEPARTMENTAL: ReadonlySet<string> = new Set([
  'rooms_dept_expense', 'fb_dept_expense', 'other_dept_expense',
]);

/** `field_overrides` keys for one line. */
export function methodOverrideKeys(line: ProjectionMethodLine) {
  return {
    method: `projection_methods.${line}.method`,
    value: `projection_methods.${line}.value`,
  };
}

/** Methods whose value is a rate typed in percent. */
const isPctMethod = (m: ProjectionMethodId) => m === 'growth' || m === 'pct_revenue';

/** The chip's value text, e.g. "3.5%" / "$40.00". Empty when unknown. */
export function formatMethodValue(method: ProjectionMethodId, value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '';
  if (isPctMethod(method)) return `${(value * 100).toFixed(1)}%`;
  return `$${value.toFixed(2)}`;
}

function valueHelp(line: ProjectionMethodLine, m: ProjectionMethodId): string {
  switch (m) {
    case 'growth':
      return 'Annual growth on the Year-1 figure. Blank = the model expense growth.';
    case 'pct_revenue':
      return DEPARTMENTAL.has(line)
        ? "Share of the department's own revenue. Blank = hold the Year-1 ratio."
        : 'Share of total revenue. Blank = hold the Year-1 ratio.';
    case 'por':
      return '$ per occupied room. Blank = hold the Year-1 $/occupied room.';
    case 'par':
      return '$ per available room. Blank = hold the Year-1 $/available room.';
  }
}

export interface MethodChipCtx {
  /** The worker's `expense.line_methods` (null on a run that predates E-016). */
  lineMethods: Record<string, LineMethodInfo> | null;
  overrides: Record<string, unknown>;
  running: boolean;
  /** Persist method + value (null value = the method's default) with the note, then re-run. */
  save: (line: ProjectionMethodLine, method: ProjectionMethodId, value: number | null, note: string) => Promise<void>;
  /** Drop the line's method override (revert to the engine default), then re-run. */
  reset: (line: ProjectionMethodLine) => Promise<void>;
}

export const MethodChipContext = createContext<MethodChipCtx | null>(null);

function overriddenMethod(overrides: Record<string, unknown>, line: ProjectionMethodLine): ProjectionMethodId | null {
  const raw = overrides[methodOverrideKeys(line).method];
  const v = raw && typeof raw === 'object' ? (raw as { value?: unknown }).value : raw;
  return v === 'growth' || v === 'pct_revenue' || v === 'por' || v === 'par' ? v : null;
}

function overriddenValue(overrides: Record<string, unknown>, line: ProjectionMethodLine): number | null {
  const raw = overrides[methodOverrideKeys(line).value];
  const v = raw && typeof raw === 'object' ? (raw as { value?: unknown }).value : raw;
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN;
  return Number.isFinite(n) ? n : null;
}

export function MethodChip({ line }: { line: ProjectionMethodLine }) {
  const ctx = useContext(MethodChipContext);
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  // Close on an outside click.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, [open]);

  if (!ctx) return null;
  const info = ctx.lineMethods?.[line] ?? null;
  const pendingMethod = overriddenMethod(ctx.overrides, line);
  // What ran is the authority; with no published method (older run) fall
  // back to a saved override so the analyst still sees their choice.
  const method: ProjectionMethodId | null = info?.method ?? pendingMethod;
  const isOverride = info ? info.source === 'override' : pendingMethod != null;
  const valueText = method ? formatMethodValue(method, info ? info.value : overriddenValue(ctx.overrides, line)) : '';
  const storedNote = noteOf(ctx.overrides[methodOverrideKeys(line).method]);

  return (
    <span ref={wrapRef} data-method-chip="" className="relative inline-block ml-1.5 align-middle">
      <button
        type="button"
        data-testid={`method-chip-${line}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        disabled={ctx.running}
        onClick={() => setOpen((v) => !v)}
        title={
          [
            method ? `Projection method: ${METHOD_LABEL[method]}${valueText ? ` ${valueText}` : ''}` : 'Projection method',
            isOverride ? 'Analyst override' : 'Model default',
            storedNote ? `Note: ${storedNote}` : info?.note ?? '',
          ].filter(Boolean).join(' · ')
        }
        className={cn(
          'inline-flex items-center gap-1 rounded px-1.5 py-[1px] text-[9.5px] font-semibold leading-tight border whitespace-nowrap',
          isOverride
            ? 'border-brand-500 bg-brand-50 text-brand-700'
            : 'border-border bg-white text-ink-500 hover:border-ink-300',
        )}
      >
        {method ? METHOD_LABEL[method] : 'Method'}
        {valueText && <span className="font-normal tabular-nums">{valueText}</span>}
      </button>
      {open && (
        <MethodEditor
          line={line}
          initialMethod={pendingMethod ?? method ?? 'growth'}
          initialValue={pendingMethod ? overriddenValue(ctx.overrides, line) : null}
          initialNote={storedNote ?? ''}
          canReset={pendingMethod != null}
          ctx={ctx}
          onClose={() => setOpen(false)}
        />
      )}
    </span>
  );
}

function MethodEditor({
  line, initialMethod, initialValue, initialNote, canReset, ctx, onClose,
}: {
  line: ProjectionMethodLine;
  initialMethod: ProjectionMethodId;
  initialValue: number | null;
  initialNote: string;
  canReset: boolean;
  ctx: MethodChipCtx;
  onClose: () => void;
}) {
  const [method, setMethod] = useState<ProjectionMethodId>(initialMethod);
  const toDraft = (m: ProjectionMethodId, v: number | null) =>
    v == null ? '' : isPctMethod(m) ? String(Math.round(v * 100 * 1000) / 1000) : String(v);
  const [draft, setDraft] = useState(toDraft(initialMethod, initialValue));
  const [note, setNote] = useState(initialNote);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const parsed = draft.trim() === '' ? null : Number(draft);
  const invalid = parsed != null && !Number.isFinite(parsed);

  const onSave = async () => {
    if (invalid) { setError('Enter a number, or leave blank for the default.'); return; }
    if (!note.trim()) { setError(NOTE_REQUIRED_MESSAGE); return; }
    const value = parsed == null ? null : isPctMethod(method) ? parsed / 100 : parsed;
    setSaving(true);
    try {
      await ctx.save(line, method, value, note.trim());
      onClose();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-label="Projection method"
      data-testid={`method-editor-${line}`}
      className="absolute left-0 top-full mt-1 z-30 w-[260px] rounded-md border border-border bg-white p-3 shadow-lg text-left font-normal"
    >
      <div className="text-[11px] font-semibold text-ink-900 mb-2">Projection method</div>
      <div className="grid grid-cols-4 gap-1 mb-2" role="radiogroup" aria-label="Method">
        {(Object.keys(METHOD_LABEL) as ProjectionMethodId[]).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={method === m}
            onClick={() => { setMethod(m); setDraft(''); setError(null); }}
            className={cn(
              'rounded border px-1 py-1 text-[10.5px] font-semibold',
              method === m ? 'border-brand-500 bg-brand-50 text-brand-700' : 'border-border text-ink-700',
            )}
          >
            {METHOD_LABEL[m]}
          </button>
        ))}
      </div>
      <label className="block text-[10.5px] text-ink-700 mb-1">
        Value {isPctMethod(method) ? '(%)' : '($)'}
        <input
          aria-label="Method value"
          inputMode="decimal"
          value={draft}
          onChange={(e) => { setDraft(e.target.value); setError(null); }}
          placeholder="Default"
          className="mt-0.5 block w-full rounded border border-border px-2 py-1 text-[11.5px] tabular-nums"
        />
      </label>
      <div className="text-[10px] text-ink-500 mb-2 leading-snug">{valueHelp(line, method)}</div>
      <label className="block text-[10.5px] text-ink-700 mb-1">
        Note
        <textarea
          aria-label="Override note"
          value={note}
          onChange={(e) => { setNote(e.target.value); setError(null); }}
          placeholder={NOTE_PLACEHOLDER}
          rows={2}
          className="mt-0.5 block w-full rounded border border-border px-2 py-1 text-[11.5px]"
        />
      </label>
      {error && <div role="alert" className="text-[10.5px] text-danger-700 mb-1">{error}</div>}
      <div className="flex items-center justify-between gap-2 mt-2">
        {canReset ? (
          <button
            type="button"
            onClick={async () => { setSaving(true); try { await ctx.reset(line); onClose(); } finally { setSaving(false); } }}
            disabled={saving}
            className="text-[10.5px] text-ink-500 hover:text-ink-900"
          >
            Reset to default
          </button>
        ) : <span />}
        <div className="flex gap-1.5">
          <button type="button" onClick={onClose} className="rounded border border-border px-2 py-1 text-[10.5px]">
            Cancel
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saving || ctx.running}
            className="rounded bg-brand-500 px-2 py-1 text-[10.5px] font-semibold text-white disabled:opacity-60"
          >
            Save &amp; re-run
          </button>
        </div>
      </div>
    </div>
  );
}
