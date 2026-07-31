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
import { AlertCircle, CheckCircle2, Loader2 } from 'lucide-react';
import { api, isWorkerConnected } from '@/lib/api';
import type { CompletenessResponse } from '@/lib/api';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import { DealReadinessSummary } from './DealReadinessSummary';

export interface CompletenessCardProps {
  dealId: string;
  /** Show the per-category breakdown? Defaults to true; the dossier
   *  workspace summary surface passes ``false`` to render the percent
   *  + ring only. */
  showDetail?: boolean;
  className?: string;
  /** FON-18 / FON-31 — optional doc count + per-type breakdown, folded
   *  into the readiness card header. This is the information the retired
   *  Document Checklist card used to own. */
  docSummary?: { count: number; breakdown: string[] };
}

export function CompletenessCard({
  dealId,
  showDetail = true,
  className,
  docSummary,
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

  // FON-31 — delegate to the shared readiness component so the
  // workspace card and the wizard rail can never drift.
  return (
    <DealReadinessSummary
      data={data}
      showDetail={showDetail}
      className={className}
      docSummary={docSummary}
    />
  );
}
