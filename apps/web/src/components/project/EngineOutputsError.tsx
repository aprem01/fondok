'use client';

import { AlertTriangle, RefreshCw } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';

/**
 * EngineOutputsError — the engine-outputs READ failed (worker unreachable,
 * 5xx, timeout). Distinct from "the model has not run on this deal", which is
 * the tabs' own not-run empty state: a failed read says so and offers Retry,
 * so an analyst is never told to upload documents when the real problem is
 * that nothing could be fetched (go-live Wave 0, D1).
 *
 * Same visual as the deal list's "Couldn't reach Fondok" card.
 */
export function EngineOutputsError({
  error,
  onRetry,
  what = 'the model outputs',
}: {
  error: string;
  onRetry: () => void | Promise<void>;
  /** What this screen was trying to read, for the sentence. */
  what?: string;
}) {
  return (
    <Card className="p-5 border-danger-500/30 bg-danger-50" role="alert" data-testid="engine-outputs-error">
      <div className="flex items-start gap-3">
        <AlertTriangle size={16} className="text-danger-700 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold text-danger-700">Couldn’t load {what}</div>
          <p className="text-[12px] text-danger-700/80 mt-1">
            {error} This is a read failure, not a model that has yet to run — the deal’s saved results are unchanged.
          </p>
        </div>
        <Button variant="secondary" size="sm" onClick={() => { void onRetry(); }}>
          <RefreshCw size={12} /> Retry
        </Button>
      </div>
    </Card>
  );
}
