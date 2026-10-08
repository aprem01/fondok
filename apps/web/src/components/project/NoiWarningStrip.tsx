'use client';

/**
 * FON-63 — the one-line negative-NOI strip shared by Debt, Cash Flow and
 * Returns. A WARNING, not a failure: the debt engine completed and every
 * number on the tab is real; the strip says why DSCR reads N/A and how much
 * debt service operations did not cover. Failures stay with
 * EngineFailuresBanner.
 *
 * Renders nothing when the run carries no negative year — including every run
 * persisted before the fields shipped.
 */
import { AlertTriangle } from 'lucide-react';
import type { EngineOutputsResponse } from '@/lib/api';
import { readNoiWarning } from '@/lib/noiWarning';

export default function NoiWarningStrip({
  outputs,
  className,
}: {
  outputs: EngineOutputsResponse | null;
  className?: string;
}) {
  const { warning } = readNoiWarning(outputs);
  if (!warning) return null;
  return (
    <div
      role="status"
      data-testid="noi-warning-strip"
      className={`flex items-center gap-2 rounded-md border border-warn-500/40 bg-warn-50 px-3 py-2 text-[12px] font-medium text-warn-700 ${className ?? 'mb-3'}`}
    >
      <AlertTriangle size={14} className="flex-shrink-0" aria-hidden="true" />
      <span>{warning}</span>
    </div>
  );
}
