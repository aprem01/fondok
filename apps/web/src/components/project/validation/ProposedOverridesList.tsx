'use client';

/**
 * Proposed Overrides List — the analyst's confirmation surface for the
 * Q&A re-ingestion loop (Wave 1 #5).
 *
 * Layout (institutional UX — no modal):
 *
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ [✓] field.path.here                  $1.15M    [high]        │  ← row 1
 *   │     Pre-closure baseline named by broker reply.              │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │ [ ] another.field.path               72.0%     [medium]      │  ← row 2
 *   │     Broker insurance renewal quote attached.                 │
 *   └──────────────────────────────────────────────────────────────┘
 *     [Skip all]                                  [Apply 1 override]
 *
 * Trust model (locked Wave 1 decision): the analyst confirms every
 * proposed override. Empty selection → ``onApply([])`` is the explicit
 * "skip all" path (writes ``applied_overrides=[]`` so the row shows
 * resolved-and-skipped instead of pending-forever).
 *
 * Per-override confidence chips are styled subtly so they inform the
 * analyst without screaming for attention — green/amber/gray.
 */

import { useState } from 'react';
import { CheckCircle2, SkipForward } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import type {
  ProposedOverride,
  ProposedOverrideConfidence,
} from '@/lib/api';
import { cn } from '@/lib/format';

/** Format a path's trailing two segments so a long
 *  ``p_and_l_usali.operating_revenue.fb_revenue`` reads as
 *  ``operating_revenue.fb_revenue`` in the UI. */
function shortPath(path: string): string {
  const parts = path.split('.');
  if (parts.length <= 2) return path;
  return parts.slice(-2).join('.');
}

function formatProposedValue(value: number | string, path: string): string {
  if (typeof value === 'string') return value;
  // Percentage-ish paths land as 0..1 fractions; render as percent.
  if (
    path === 'p_and_l_usali.operational_kpis.occupancy_pct' ||
    path === 'broker_proforma.entry_cap_rate' ||
    path === 'in_place_debt.interest_rate_pct' ||
    path === 'in_place_debt.ltv_pct'
  ) {
    return `${(value * 100).toFixed(1)}%`;
  }
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return value.toLocaleString('en-US', { maximumFractionDigits: 2 });
}

const CONFIDENCE_META: Record<
  ProposedOverrideConfidence,
  { label: string; classes: string }
> = {
  high: {
    label: 'high',
    classes:
      'bg-success-50 text-success-700 border-success-500/25',
  },
  medium: {
    label: 'medium',
    classes: 'bg-warn-50 text-warn-700 border-warn-500/30',
  },
  low: {
    label: 'low',
    classes: 'bg-ink-100 text-ink-700 border-ink-200',
  },
};

export function ProposedOverridesList({
  overrides,
  onApply,
  onSkipAll,
  submitting = false,
}: {
  overrides: ProposedOverride[];
  /** Called with the analyst's chosen indexes (always a subset of 0..n-1). */
  onApply: (indexes: number[]) => Promise<void> | void;
  /** Distinct path from ``onApply([])`` so the parent can render different
   *  toasts; kept as a separate prop for clarity. */
  onSkipAll: () => Promise<void> | void;
  submitting?: boolean;
}) {
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(overrides.map((_, i) => i)),
  );

  const toggle = (idx: number) => {
    if (submitting) return;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx);
      else next.add(idx);
      return next;
    });
  };

  const chosen = Array.from(selected).sort((a, b) => a - b);
  const allChecked = selected.size === overrides.length;

  if (overrides.length === 0) {
    return (
      <div
        role="status"
        className="mt-3 p-3 rounded-md bg-ink-100 border border-ink-200 text-[12.5px] text-ink-600 leading-relaxed"
      >
        Resolver did not propose any engine-input changes for this Q&A.
        The broker's reply has been logged on this question's history;
        no analyst confirmation needed.
      </div>
    );
  }

  return (
    <div
      className="mt-3 rounded-md border border-brand-500/25 bg-white overflow-hidden"
      role="group"
      aria-label="Proposed engine-input overrides"
    >
      <div className="px-3 py-2 border-b border-ink-100 bg-brand-50/40 flex items-center justify-between">
        <span className="text-[11px] uppercase tracking-wider font-semibold text-brand-700">
          Proposed overrides
        </span>
        <button
          type="button"
          onClick={() => {
            if (submitting) return;
            if (allChecked) setSelected(new Set());
            else setSelected(new Set(overrides.map((_, i) => i)));
          }}
          disabled={submitting}
          className="text-[11px] text-brand-700 hover:underline disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded px-1"
        >
          {allChecked ? 'Clear all' : 'Select all'}
        </button>
      </div>

      <ul className="divide-y divide-ink-100">
        {overrides.map((o, idx) => {
          const checked = selected.has(idx);
          const conf = CONFIDENCE_META[o.confidence];
          const id = `qa-override-${idx}`;
          return (
            <li
              key={`${o.field_path}-${idx}`}
              className={cn(
                'flex gap-3 px-3 py-2.5 transition-colors',
                checked ? 'bg-white' : 'bg-ink-100/30',
              )}
            >
              <input
                type="checkbox"
                id={id}
                checked={checked}
                onChange={() => toggle(idx)}
                disabled={submitting}
                className="mt-1 flex-shrink-0 h-3.5 w-3.5 rounded border-ink-300 text-brand-600 focus-visible:ring-brand-500 disabled:opacity-50 cursor-pointer disabled:cursor-not-allowed"
                aria-describedby={`${id}-rationale`}
              />
              <label htmlFor={id} className="flex-1 min-w-0 cursor-pointer">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <code
                    className="font-mono text-[12px] text-ink-900 truncate"
                    title={o.field_path}
                  >
                    {shortPath(o.field_path)}
                  </code>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    <span className="font-semibold tabular-nums text-[13px] text-ink-900">
                      {formatProposedValue(o.value, o.field_path)}
                    </span>
                    <span
                      className={cn(
                        'inline-flex items-center px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wider font-semibold',
                        conf.classes,
                      )}
                    >
                      {conf.label}
                    </span>
                  </div>
                </div>
                <p
                  id={`${id}-rationale`}
                  className="text-[12px] text-ink-600 mt-1 leading-relaxed"
                >
                  {o.rationale}
                </p>
              </label>
            </li>
          );
        })}
      </ul>

      <div className="px-3 py-2.5 border-t border-ink-100 bg-ink-100/40 flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[11px] text-ink-500 tabular-nums">
          {chosen.length}/{overrides.length} selected
        </span>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void onSkipAll()}
            disabled={submitting}
            aria-label="Skip all proposed overrides"
          >
            <SkipForward size={11} aria-hidden="true" />
            Skip all
          </Button>
          <Button
            size="sm"
            variant="primary"
            onClick={() => void onApply(chosen)}
            disabled={submitting || chosen.length === 0}
            loading={submitting}
            aria-label={`Apply ${chosen.length} proposed override${chosen.length === 1 ? '' : 's'}`}
          >
            {!submitting && <CheckCircle2 size={11} aria-hidden="true" />}
            {chosen.length > 0
              ? `Apply ${chosen.length} override${chosen.length === 1 ? '' : 's'}`
              : 'Apply'}
          </Button>
        </div>
      </div>
    </div>
  );
}
