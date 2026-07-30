'use client';

/**
 * DealReadinessSummary — FON-31.
 *
 * One consolidated answer to the two questions a user actually has while
 * assembling a deal:
 *
 *   Tier 1 — "Can I run the model yet?"  → the run-the-model gate
 *            (Financials = T-12 OR any Annual / YTD / Monthly P&L).
 *   Tier 2 — "What else makes this IC-ready?" → the 10-category
 *            coverage breakdown, demoted to secondary so a runnable
 *            deal never reads as "40% complete / blocked".
 *
 * Replaces the two drifting surfaces this consolidates:
 *   - CompletenessCard (deal workspace, worker-fed)
 *   - DocumentsChecklist (onboarding wizard rail, client-fed)
 * Both now render THIS from the same {@link CompletenessResponse} shape,
 * so the wizard and the workspace can never disagree again.
 *
 * Pure presentation — the caller supplies the data (fetched on the
 * workspace, computed client-side in the wizard via
 * {@link readinessFromWizardFiles}).
 */

import { AlertCircle, CheckCircle2, Circle, Rocket } from 'lucide-react';
import type { CompletenessResponse, CompletenessCategory } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { cn } from '@/lib/format';

export interface DealReadinessSummaryProps {
  data: CompletenessResponse;
  /** 'card' — full workspace treatment (default). 'rail' — compact
   *  wizard right-rail (gate + a single coverage line). */
  variant?: 'card' | 'rail';
  /** Show the per-category IC breakdown (Tier 2 detail)? Card only. */
  showDetail?: boolean;
  className?: string;
}

const FINANCIAL_LABEL: Record<string, string> = {
  t12: 'T-12',
  historical_pnl: 'P&L',
};

export function DealReadinessSummary({
  data,
  variant = 'card',
  showDetail = true,
  className,
}: DealReadinessSummaryProps) {
  // Gate resolution — prefer the worker's fields, but derive from the
  // categories when they're absent. This keeps the workspace card correct
  // during the rollout window where the web deploy leads the worker (the
  // old /completeness response carries no gate), so a deal with financials
  // never wrongly reads "add a T-12 or P&L".
  const coveredIds = new Set(
    data.categories.filter((c) => c.covered).map((c) => c.id),
  );
  const derivedSatisfiedBy: CompletenessCategory['id'] | null = coveredIds.has(
    't12',
  )
    ? 't12'
    : coveredIds.has('historical_pnl')
      ? 'historical_pnl'
      : null;
  const canRun = data.can_run_model ?? derivedSatisfiedBy !== null;
  const satisfiedBy = data.model_gate?.satisfied_by ?? derivedSatisfiedBy;
  const required = data.categories.filter((c) => c.required_for_ic);
  const optional = data.categories.filter((c) => !c.required_for_ic);
  const missing = required.filter((c) => !c.covered).length;
  const coveredCount = required.length - missing;

  if (variant === 'rail') {
    return (
      <Card
        className={cn('p-4 sticky top-4 hidden xl:block', className)}
        aria-label="Deal readiness"
      >
        <GateBanner canRun={canRun} satisfiedBy={satisfiedBy} compact />
        <div className="mt-3 pt-3 border-t border-border">
          <div className="flex items-center justify-between text-[11px]">
            <span className="uppercase tracking-wider font-semibold text-ink-500">
              IC coverage
            </span>
            <span className="tabular-nums font-semibold text-ink-900">
              {coveredCount}
              <span className="text-ink-500 font-medium">
                /{required.length}
              </span>
            </span>
          </div>
          <div className="mt-2 h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full transition-all motion-reduce:transition-none',
                data.completeness_pct >= 100
                  ? 'bg-success-500'
                  : 'bg-brand-500',
              )}
              style={{ width: `${data.completeness_pct}%` }}
              aria-hidden="true"
            />
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card className={cn('p-4', className)} aria-label="Deal readiness">
      <div className="flex items-center gap-2 mb-3">
        <CheckCircle2 size={14} className="text-brand-500" />
        <h3 className="text-[13px] font-semibold text-ink-900">
          Deal readiness
        </h3>
        <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-ink-500 tabular-nums">
          <span className="font-semibold text-ink-700">
            {coveredCount}/{required.length}
          </span>
          <span>IC docs</span>
        </span>
      </div>

      {/* Tier 1 — the gate that actually blocks a model run. */}
      <GateBanner canRun={canRun} satisfiedBy={satisfiedBy} />

      {/* Tier 2 — IC package, demoted below the gate. */}
      <div className="mt-4">
        <div className="flex items-center gap-2 mb-2">
          <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold">
            Complete your IC package
          </div>
          {missing > 0 && (
            <span className="text-[10.5px] text-ink-500 tabular-nums">
              {missing} recommended to add
            </span>
          )}
        </div>
        {showDetail && (
          <ul className="space-y-1.5" role="list">
            {required.map((c) => (
              <ReadinessRow key={c.id} category={c} gateMet={canRun} />
            ))}
            {optional.length > 0 && (
              <li className="pt-2 mt-2 border-t border-border">
                <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">
                  Optional
                </div>
                <ul className="space-y-1.5" role="list">
                  {optional.map((c) => (
                    <ReadinessRow key={c.id} category={c} gateMet={canRun} />
                  ))}
                </ul>
              </li>
            )}
          </ul>
        )}
      </div>
    </Card>
  );
}

function GateBanner({
  canRun,
  satisfiedBy,
  compact = false,
}: {
  canRun: boolean;
  satisfiedBy: string | null;
  compact?: boolean;
}) {
  if (canRun) {
    const via = satisfiedBy ? FINANCIAL_LABEL[satisfiedBy] ?? null : null;
    return (
      <div
        className="flex items-start gap-2 rounded-lg bg-success-500/10 px-3 py-2.5"
        role="status"
      >
        <Rocket
          size={compact ? 13 : 15}
          className="text-success-700 flex-shrink-0 mt-0.5"
          aria-hidden="true"
        />
        <div className="min-w-0">
          <div className="text-[12px] font-semibold text-success-700">
            Ready to run the model
          </div>
          {!compact && (
            <div className="mt-0.5 text-[11px] text-ink-700 leading-relaxed">
              You&rsquo;ve uploaded the minimum
              {via ? ` (a ${via})` : ' financials'} to underwrite this deal.
              Everything below sharpens the projection but isn&rsquo;t
              required to run.
            </div>
          )}
        </div>
      </div>
    );
  }
  return (
    <div
      className="flex items-start gap-2 rounded-lg bg-brand-500/10 px-3 py-2.5"
      role="status"
    >
      <AlertCircle
        size={compact ? 13 : 15}
        className="text-brand-700 flex-shrink-0 mt-0.5"
        aria-hidden="true"
      />
      <div className="min-w-0">
        <div className="text-[12px] font-semibold text-brand-700">
          One step to run the model
        </div>
        {!compact && (
          <div className="mt-0.5 text-[11px] text-ink-700 leading-relaxed">
            Add a <span className="font-medium">T-12</span> or an{' '}
            <span className="font-medium">Annual / YTD / Monthly P&amp;L</span>{' '}
            to run the underwrite. Other documents are optional.
          </div>
        )}
      </div>
    </div>
  );
}

function ReadinessRow({
  category,
  gateMet,
}: {
  category: CompletenessCategory;
  gateMet: boolean;
}) {
  const covered = category.covered;
  const StatusIcon = covered ? CheckCircle2 : Circle;
  const statusClass = covered ? 'text-success-500' : 'text-ink-300';
  return (
    <li className="flex items-start gap-2" role="listitem">
      <StatusIcon
        size={13}
        className={cn('flex-shrink-0 mt-0.5', statusClass)}
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0">
        <div className="text-[12px] font-medium text-ink-900 truncate">
          {category.label}
        </div>
        <div className="mt-0.5">
          {covered ? (
            <span className="text-[10.5px] tabular-nums font-medium text-success-700">
              {category.doc_count} file{category.doc_count === 1 ? '' : 's'}
            </span>
          ) : (
            // Once the model can run, everything else is an enhancement —
            // frame missing rows as "recommended", never an alarming
            // "Missing" that reads as a hard blocker.
            <span className="text-[10.5px] text-ink-500">
              {gateMet
                ? 'Recommended'
                : category.required_for_ic
                  ? 'Recommended for IC'
                  : 'Optional'}
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

/**
 * Build a {@link CompletenessResponse}-shaped object from the wizard's
 * in-memory uploads, so the onboarding rail renders the exact same
 * component as the worker-fed workspace card. Mid-wizard the deal
 * doesn't exist yet, so there is no worker to ask — but the taxonomy
 * and the gate are identical, so the client can compute it locally.
 */
export function readinessFromWizardFiles(
  categories: readonly {
    id: string;
    label: string;
    requiredForIc: boolean;
  }[],
  counts: Record<string, number>,
): CompletenessResponse {
  const cats: CompletenessCategory[] = categories.map((c) => ({
    id: c.id as CompletenessCategory['id'],
    label: c.label,
    covered: (counts[c.id] ?? 0) > 0,
    doc_count: counts[c.id] ?? 0,
    required_for_ic: c.requiredForIc,
  }));
  const required = cats.filter((c) => c.required_for_ic);
  const covered = required.filter((c) => c.covered).length;
  const pct =
    required.length > 0 ? Math.round((covered / required.length) * 100) : 0;
  // Mirror the worker gate: T-12 first, then any P&L.
  const satisfiedBy =
    (counts['t12'] ?? 0) > 0
      ? 't12'
      : (counts['historical_pnl'] ?? 0) > 0
        ? 'historical_pnl'
        : null;
  return {
    deal_id: '',
    completeness_pct: pct,
    can_run_model: satisfiedBy !== null,
    model_gate: {
      met: satisfiedBy !== null,
      satisfied_by: satisfiedBy as CompletenessResponse['model_gate']['satisfied_by'],
    },
    categories: cats,
  };
}
