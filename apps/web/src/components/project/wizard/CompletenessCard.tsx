'use client';

/**
 * CompletenessCard — Wave 1 #1.
 *
 * Surfaces "how IC-ready is this deal?" on the deal workspace as a
 * single percent + per-category breakdown. The percent runs over the
 * 10 required-for-IC categories (SURVEYS is recommended only and
 * excluded from the denominator).
 *
 * Backed by GET /deals/{id}/completeness — the worker returns the
 * canonical 11-category list with covered/doc_count/required_for_ic
 * pre-computed so the UI doesn't have to re-derive against the
 * documents list.
 *
 * Lives on the Data Room tab and (eventually) the Validation tab so
 * IC reviewers can answer "what's still missing?" at a glance.
 */

import { useEffect, useState } from 'react';
import { AlertCircle, CheckCircle2, Loader2, Circle } from 'lucide-react';
import { api, isWorkerConnected } from '@/lib/api';
import type {
  CompletenessCategory,
  CompletenessResponse,
} from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';

export interface CompletenessCardProps {
  dealId: string;
  /** Show the per-category breakdown? Defaults to true; the dossier
   *  workspace summary surface passes ``false`` to render the percent
   *  + ring only. */
  showDetail?: boolean;
  className?: string;
}

export function CompletenessCard({
  dealId,
  showDetail = true,
  className,
}: CompletenessCardProps) {
  const [data, setData] = useState<CompletenessResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) {
      setLoading(false);
      return;
    }
    let cancelled = false;
    const ctrl = new AbortController();
    setLoading(true);
    api.deals
      .completeness(dealId, ctrl.signal)
      .then((res) => {
        if (cancelled) return;
        setData(res);
        setError(null);
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [dealId]);

  if (!isWorkerConnected() || /^\d+$/.test(dealId)) {
    // Mock / numeric demo deals don't have a real backend coverage
    // signal. Render a quiet "preview" state so the card slot doesn't
    // collapse to zero height.
    return (
      <Card
        className={cn('p-4', className)}
        aria-label="Deal completeness — demo deal"
      >
        <div className="flex items-center gap-2 mb-1">
          <CheckCircle2 size={14} className="text-brand-500" />
          <h3 className="text-[13px] font-semibold text-ink-900">
            IC Completeness
          </h3>
          <Badge tone="gray" className="ml-auto text-[9.5px]">
            Demo
          </Badge>
        </div>
        <div className="text-[11.5px] text-ink-500 leading-relaxed">
          Live completeness scoring activates once the deal has uploaded
          documents the worker can read.
        </div>
      </Card>
    );
  }

  if (loading) {
    return (
      <Card className={cn('p-4', className)} aria-label="Deal completeness">
        <div className="flex items-center gap-2 text-[12px] text-ink-500">
          <Loader2 size={13} className="animate-spin" aria-hidden="true" />
          Loading completeness…
        </div>
      </Card>
    );
  }
  if (error || !data) {
    return (
      <Card className={cn('p-4', className)} aria-label="Deal completeness">
        <div className="flex items-start gap-2 text-[12px] text-danger-700">
          <AlertCircle size={13} className="mt-0.5" aria-hidden="true" />
          Couldn&rsquo;t load completeness — {error ?? 'unknown error'}
        </div>
      </Card>
    );
  }

  const pct = data.completeness_pct;
  const tone =
    pct >= 80
      ? 'success'
      : pct >= 50
        ? 'brand'
        : 'danger';
  const required = data.categories.filter((c) => c.required_for_ic);
  const optional = data.categories.filter((c) => !c.required_for_ic);
  const missing = required.filter((c) => !c.covered).length;

  return (
    <Card
      className={cn('p-4', className)}
      aria-label="Deal completeness — IC checklist"
    >
      <div className="flex items-center gap-2 mb-3">
        <CheckCircle2 size={14} className="text-brand-500" />
        <h3 className="text-[13px] font-semibold text-ink-900">
          IC Completeness
        </h3>
        <span className="ml-auto inline-flex items-center gap-1 text-[11px] text-ink-500 tabular-nums">
          <span
            className={cn(
              'font-semibold',
              tone === 'success' && 'text-success-700',
              tone === 'brand' && 'text-brand-700',
              tone === 'danger' && 'text-danger-700',
            )}
          >
            {pct}%
          </span>
          <span aria-hidden="true">·</span>
          <span>
            {required.length - missing}/{required.length} required
          </span>
        </span>
      </div>

      <div className="mb-3">
        <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
          <div
            className={cn(
              'h-full transition-all motion-reduce:transition-none',
              tone === 'success' && 'bg-success-500',
              tone === 'brand' && 'bg-brand-500',
              tone === 'danger' && 'bg-danger-500',
            )}
            style={{ width: `${pct}%` }}
            aria-hidden="true"
          />
        </div>
        {missing > 0 && (
          <div className="mt-2 text-[10.5px] text-danger-700 flex items-center gap-1">
            <AlertCircle size={10} aria-hidden="true" />
            {missing} required categor{missing === 1 ? 'y' : 'ies'} still
            missing — IC reviewers will flag this.
          </div>
        )}
      </div>

      {showDetail && (
        <ul className="space-y-1.5" role="list">
          {required.map((c) => (
            <CompletenessRow key={c.id} category={c} />
          ))}
          {optional.length > 0 && (
            <li className="pt-2 mt-2 border-t border-border">
              <div className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">
                Optional
              </div>
              <ul className="space-y-1.5" role="list">
                {optional.map((c) => (
                  <CompletenessRow key={c.id} category={c} />
                ))}
              </ul>
            </li>
          )}
        </ul>
      )}
    </Card>
  );
}

function CompletenessRow({ category }: { category: CompletenessCategory }) {
  const covered = category.covered;
  const StatusIcon = covered
    ? CheckCircle2
    : category.required_for_ic
      ? AlertCircle
      : Circle;
  const statusClass = covered
    ? 'text-success-500'
    : category.required_for_ic
      ? 'text-danger-700'
      : 'text-ink-300';
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
          ) : category.required_for_ic ? (
            <Badge tone="red" className="text-[9.5px]">
              Missing
            </Badge>
          ) : (
            <span className="text-[10.5px] text-ink-500">Optional</span>
          )}
        </div>
      </div>
    </li>
  );
}
