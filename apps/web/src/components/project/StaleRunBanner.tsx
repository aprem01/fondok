'use client';

/**
 * StaleRunBanner — FON-75. Says out loud that this deal's saved run is older
 * than the model that produced it.
 *
 * The defect: on 2026-09-12 a healthy deal opened with the whole Stabilization
 * section of Overview rendering five dashes. Nothing was broken — the deal's
 * persisted engine output had been written before `expense.stabilization`
 * existed, and no surface said so. A per-section banner shipped for that one
 * block (`stabilization-needs-rerun` in OverviewTab); this is the general net,
 * so the NEXT block addition is covered the day it deploys rather than being
 * noticed one screen at a time.
 *
 * How the worker knows (app/services/run_freshness.py): engine output is
 * persisted with `model_dump_json()` and WITHOUT `exclude_none`, so a field the
 * run knew about is always written — even when its value is null. A key present
 * with value null therefore means "the engine answered: no value" and is NOT
 * stale; a key ABSENT entirely means the run predates the field. Only the
 * second case reaches this banner.
 *
 * Same anatomy and the same one-click re-run as EngineFailuresBanner, mounted
 * beside it — one is "a model crashed", this is "a model moved on". Renders
 * nothing when the run is current, which is the overwhelmingly common case.
 */

import { History, RefreshCw, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import type { EngineOutputsResponse } from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { REASONS } from '@/lib/ontology/reasons.generated';

/**
 * Dotted `engine.path` → the section heading a reader actually recognises.
 *
 * Deliberately small and deliberately incomplete. An unmapped path falls back
 * to the raw dotted path below: printing `revenue.some_new_block` is honest and
 * greppable, whereas guessing a friendly name for a block nobody has named yet
 * would be inventing product copy at render time.
 */
const SECTION_LABEL: Record<string, string> = {
  'expense.stabilization': 'Stabilization',
  'capital.sources': 'Sources & Uses',
  'capital.uses': 'Sources & Uses',
  'capital.renovation_breakdown': 'Renovation / CapEx',
};

/** The section name for one missing path, or the path itself when unmapped. */
export function sectionLabelFor(engine: string, path: string): string {
  const dotted = `${engine}.${path}`;
  // A nested path (`stabilization.stabilized_cash_noi`) belongs to the section
  // its parent block names, so try the parent before falling back.
  const parent = path.includes('.') ? `${engine}.${path.split('.')[0]}` : null;
  return SECTION_LABEL[dotted] ?? (parent ? SECTION_LABEL[parent] : undefined) ?? dotted;
}

/** Unique section names across every stale engine, in a stable order. */
export function staleSections(
  missingBlocks: Record<string, string[]> | undefined,
): string[] {
  const seen: string[] = [];
  for (const [engine, paths] of Object.entries(missingBlocks ?? {})) {
    for (const path of paths ?? []) {
      const label = sectionLabelFor(engine, path);
      if (!seen.includes(label)) seen.push(label);
    }
  }
  return seen;
}

export function StaleRunBanner({
  outputs,
  dealId,
}: {
  outputs: EngineOutputsResponse | null;
  dealId: string;
}) {
  // Re-run the full chain (run-all) so every engine republishes together and
  // the snapshot stays internally consistent (FON-73).
  const { run, status } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  const running = status === 'running' || status === 'queued';

  const sections = staleSections(outputs?.stale_run?.missing_blocks);
  if (sections.length === 0) return null;

  return (
    <Card
      className="p-4 mb-5 border-warn-500/40 bg-warn-50"
      role="status"
      data-testid="stale-run-banner"
    >
      <div className="flex items-start gap-3">
        <History size={18} className="text-warn-700 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-[13.5px] font-semibold text-warn-700">
            {sections.length === 1
              ? `This deal’s saved run predates ${sections[0]}`
              : `This deal’s saved run predates ${sections.length} sections`}
          </div>
          <p className="text-[12px] text-warn-700/80 mt-0.5">
            {REASONS.stale_run.explanation}
          </p>
          <p className="text-[12px] text-warn-700/80 mt-1.5">
            {sections.length === 1 ? 'This section shows ' : 'These sections show '}
            “—” because the saved run never published{' '}
            {sections.length === 1 ? 'that figure' : 'those figures'} — not because the
            data is missing. Nothing already on screen is wrong.
          </p>
          <ul className="mt-2 space-y-1" data-testid="stale-run-sections">
            {sections.map((section) => (
              <li key={section} className="text-[12px] font-semibold text-warn-700">
                {section}
              </li>
            ))}
          </ul>
        </div>
        <button
          type="button"
          onClick={() => { void run(); }}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-md bg-warn-700 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-warn-600 disabled:opacity-60 flex-shrink-0"
        >
          {running ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {running ? 'Re-running…' : 'Re-run models'}
        </button>
      </div>
    </Card>
  );
}
