'use client';

/**
 * Traced — the "how was this number computed?" hover primitive.
 *
 * Sibling to <Sourced> (which explains where an INPUT assumption came from);
 * <Traced> explains how a COMPUTED output was derived. Wrap any modeled value
 * (NOI, GOP, DSCR, equity multiple …) with its engine + dotted output path;
 * on hover it shows the formula, each named input with its value + where THAT
 * input came from (a source label, an assumption, or a one-hop nested
 * formula), and any note. Values with no trace (or no formula) render
 * untouched — safe to sprinkle during rollout. FON-25 / FON-27.
 *
 *   <Traced engine="expense" path="years[0].noi">{fmtCurrency(noi)}</Traced>
 */

import { useState, type ReactNode } from 'react';
import { cn } from '@/lib/format';
import { sourceLabel } from '@/lib/provenance';
import { useTrace, useTraceGraph } from '@/lib/hooks/useValueTrace';

function fmtTraceValue(v: number): string {
  if (!Number.isFinite(v)) return '—';
  const abs = Math.abs(v);
  if (abs >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `$${Math.round(v).toLocaleString()}`;
  if (abs > 0 && abs < 1) return v.toFixed(4).replace(/0+$/, '');
  return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function Traced({
  engine,
  path,
  children,
  className,
}: {
  engine: string;
  path: string;
  children: ReactNode;
  className?: string;
}) {
  const trace = useTrace(engine, path);
  const graph = useTraceGraph(engine);
  const [open, setOpen] = useState(false);

  // Only decorate genuinely-computed values (a formula to explain).
  if (!trace || !trace.formula) return <>{children}</>;

  return (
    <span
      className={cn('relative inline-flex items-center', className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        className="border-b border-dotted border-brand-500/60 cursor-help"
        tabIndex={0}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-label="How this value is computed"
      >
        {children}
      </span>
      {open && (
        <span
          role="tooltip"
          className="absolute z-50 left-1/2 -translate-x-1/2 top-full mt-1.5 w-72 rounded-lg border border-border bg-card shadow-card-hover p-3 text-left whitespace-normal"
        >
          <span className="flex items-center gap-1.5 mb-1.5">
            <span className="w-2 h-2 rounded-full bg-brand-500" aria-hidden="true" />
            <span className="text-[11px] font-semibold text-brand-700">Computed value</span>
          </span>
          {/* The formula. Rendered mono so operators line up. */}
          <span className="block text-[11.5px] font-mono text-ink-800 leading-snug mb-2">
            {trace.formula}
          </span>
          {trace.inputs.length > 0 && (
            <span className="block space-y-0.5">
              {trace.inputs.map((inp, i) => {
                const nested = inp.traces_to ? graph.get(inp.traces_to) : null;
                const originLabel = inp.source
                  ? sourceLabel(inp.source)
                  : inp.assumption_key
                    ? 'assumption'
                    : inp.traces_to
                      ? 'computed'
                      : null;
                return (
                  <span key={`${inp.name}-${i}`} className="block text-[11px] leading-snug">
                    <span className="inline-flex w-full items-baseline justify-between gap-2">
                      <span className="text-ink-600 truncate">{inp.name}</span>
                      <span className="tabular-nums text-ink-900 font-medium shrink-0">
                        {fmtTraceValue(inp.value)}
                      </span>
                    </span>
                    {originLabel && (
                      <span className="text-[10px] text-ink-400">
                        {inp.traces_to ? '↳ ' : ''}
                        {originLabel}
                        {nested?.formula ? `: ${nested.formula}` : ''}
                      </span>
                    )}
                  </span>
                );
              })}
            </span>
          )}
          {trace.note && (
            <span className="block mt-2 text-[10.5px] text-ink-500 leading-snug border-t border-border pt-1.5">
              {trace.note}
            </span>
          )}
        </span>
      )}
    </span>
  );
}
