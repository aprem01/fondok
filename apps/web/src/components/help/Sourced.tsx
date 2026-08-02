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
  formatAssumptionValue,
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
  const decoColor =
    kind === 'grounded'
      ? 'decoration-emerald-500'
      : kind === 'override'
        ? 'decoration-violet-500'
        : 'decoration-amber-500';
  const hoverBg =
    kind === 'grounded'
      ? 'bg-emerald-50'
      : kind === 'override'
        ? 'bg-violet-50'
        : 'bg-amber-50';

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
        className={cn(
          'cursor-help rounded-sm px-0.5 -mx-0.5 underline decoration-dotted decoration-2 underline-offset-[3px] transition-colors',
          decoColor,
          open && hoverBg,
        )}
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
          className="absolute z-50 left-1/2 -translate-x-1/2 top-full mt-1.5 w-60 rounded-lg border border-border bg-card shadow-card-hover p-3 text-left whitespace-normal"
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

/**
 * SourcedValue — render a live deal's ACTUAL assumption value (from the
 * provenance provider) with the source hover, falling back to `fallback`
 * ('—' by default) when the deal has no value for the key. This is the
 * "activate the dashes" primitive: on a real deal it shows the real
 * number instead of a placeholder, and every number carries its source.
 *
 *   {isKimptonDemo ? '6.80%' : <SourcedValue sourceKey="interest_rate" fmt={pct} />}
 */
export function SourcedValue({
  sourceKey,
  fmt,
  fallback = '—',
  className,
}: {
  sourceKey: string;
  fmt?: (v: number | string | boolean) => ReactNode;
  fallback?: ReactNode;
  className?: string;
}) {
  const resolved = useSource(sourceKey);
  if (!resolved || resolved.value == null || resolved.value === '') {
    return <>{fallback}</>;
  }
  const shown = fmt ? fmt(resolved.value) : formatAssumptionValue(sourceKey, resolved.value);
  return (
    <Sourced sourceKey={sourceKey} className={className}>
      {shown}
    </Sourced>
  );
}
