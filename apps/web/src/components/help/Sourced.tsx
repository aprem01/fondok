'use client';

/**
 * Sourced — the "where did this number come from?" hover primitive.
 *
 * Wrap ANY value; it gets a subtle dotted underline colored by source kind
 * (🟢 grounded · 🟡 benchmark/seed · 🟣 override) so a whole screen's trust
 * reads at a glance. On hover, a tooltip shows the source label, a one-line
 * explanation, and — for extracted values — a "View source document" jump.
 *
 * Two ways to feed it:
 *   <Sourced sourceKey="mgmt_fee_pct">$390K</Sourced>   ← looks up ProvenanceProvider
 *   <Sourced source="t12_actual" docId={id}>$236</Sourced>  ← explicit
 *
 * With no resolvable source it renders children untouched — safe to sprinkle
 * everywhere during rollout.
 */

import { useState, type ReactNode } from 'react';
import { useParams } from 'next/navigation';
import { cn } from '@/lib/format';
import {
  sourceKind,
  sourceLabel,
  sourceExplanation,
  KIND_TONE,
} from '@/lib/provenance';
import { useSource } from '@/lib/hooks/useDealProvenance';

export function Sourced({
  sourceKey,
  source,
  docId,
  children,
  className,
}: {
  sourceKey?: string;
  source?: string;
  docId?: string;
  children: ReactNode;
  className?: string;
}) {
  const resolved = useSource(sourceKey);
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? '';

  const src = source ?? resolved?.source;
  const doc = docId ?? resolved?.docId;
  const [open, setOpen] = useState(false);

  if (!src) return <>{children}</>;

  const kind = sourceKind(src);
  const tone = KIND_TONE[kind];
  const borderColor =
    kind === 'grounded'
      ? 'border-success-500/60'
      : kind === 'override'
        ? 'border-brand-500/60'
        : 'border-warn-500/70';

  const openDoc = () => {
    if (!doc || typeof window === 'undefined') return;
    window.dispatchEvent(
      new CustomEvent('fondok:citation-focus', {
        detail: { documentId: doc, page: 1, field: sourceKey ?? '' },
      }),
    );
  };

  return (
    <span
      className={cn('relative inline-flex items-center', className)}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        className={cn('border-b border-dotted cursor-help', borderColor)}
        tabIndex={0}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        aria-label={`Source: ${sourceLabel(src)}`}
      >
        {children}
      </span>
      {open && (
        <span
          role="tooltip"
          className="absolute z-50 left-1/2 -translate-x-1/2 top-full mt-1.5 w-60 rounded-lg border border-border bg-card shadow-card-hover p-3 text-left"
        >
          <span className="flex items-center gap-1.5 mb-1">
            <span className={cn('w-2 h-2 rounded-full', tone.dot)} aria-hidden="true" />
            <span className={cn('text-[11px] font-semibold', tone.text)}>
              {sourceLabel(src)}
            </span>
          </span>
          <span className="block text-[11.5px] text-ink-600 leading-snug">
            {sourceExplanation(src)}
          </span>
          {doc && kind === 'grounded' && (
            <button
              type="button"
              onClick={openDoc}
              className="mt-2 inline-flex items-center gap-1 text-[11px] font-medium text-brand-700 hover:text-brand-500"
            >
              View source document →
            </button>
          )}
          {dealId && (
            <span className="block mt-1.5 text-[10px] text-ink-400">
              Full trace in Analysis → Sources
            </span>
          )}
        </span>
      )}
    </span>
  );
}
