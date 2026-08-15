'use client';

/**
 * DataKeyLegend — the "DATA KEY" provenance legend from the Fondok design
 * mockup. Explains what each value color/style means so an analyst can read a
 * screen's trust at a glance. Two variants:
 *   - overview:   Assumption/input · Linked/market data · Calculated · Total · Editable
 *   - financials: Linked/Extracted · Needs review · User input/assumption · Calculated · Total · Editable
 *
 * Colors are the shared taxonomy: green = grounded/extracted, red = needs
 * review, blue = input/assumption, gray = calculated. Keep in sync with the
 * cell colors those screens render.
 */

import { Info } from 'lucide-react';
import { cn } from '@/lib/format';

type Variant = 'overview' | 'financials';

interface LegendItem {
  dot?: string;
  glyph?: 'B' | '123';
  label: string;
}

const OVERVIEW_ITEMS: LegendItem[] = [
  { dot: 'bg-blue-500', label: 'Assumption / input' },
  { dot: 'bg-emerald-500', label: 'Linked / market data' },
  { dot: 'bg-slate-400', label: 'Calculated' },
  { glyph: 'B', label: 'Total (bold)' },
  { glyph: '123', label: 'Editable — click to change' },
];

const FINANCIALS_ITEMS: LegendItem[] = [
  { dot: 'bg-emerald-500', label: 'Linked / Extracted' },
  { dot: 'bg-red-500', label: 'Needs review' },
  { dot: 'bg-blue-500', label: 'User input / assumption' },
  { dot: 'bg-slate-400', label: 'Calculated' },
  { glyph: 'B', label: 'Total (bold)' },
  { glyph: '123', label: 'Editable' },
];

const COLORS_EXPLAINED =
  'Colors mark where each value came from: green = extracted from a document, ' +
  'red = low-confidence and needs review, blue = your input or an assumption, ' +
  'gray = calculated by the model.';

export function DataKeyLegend({ variant, className }: { variant: Variant; className?: string }) {
  const items = variant === 'overview' ? OVERVIEW_ITEMS : FINANCIALS_ITEMS;
  return (
    <div className={cn('flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-ink-500', className)}>
      <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">Data key</span>
      {items.map((it) => (
        <span key={it.label} className="inline-flex items-center gap-1.5">
          {it.dot && <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', it.dot)} aria-hidden="true" />}
          {it.glyph === 'B' && <span className="font-bold text-ink-800 text-[11px]" aria-hidden="true">B</span>}
          {it.glyph === '123' && (
            <span className="font-mono text-[10px] text-ink-600 tabular-nums" aria-hidden="true">123</span>
          )}
          <span>{it.label}</span>
        </span>
      ))}
      {variant === 'financials' && (
        <span
          className="inline-flex items-center gap-1 ml-auto text-ink-400 cursor-help"
          title={COLORS_EXPLAINED}
        >
          <Info size={11} aria-hidden="true" /> Colors explained
        </span>
      )}
    </div>
  );
}
