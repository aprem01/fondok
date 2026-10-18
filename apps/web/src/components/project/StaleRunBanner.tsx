'use client';

/**
 * StaleRunBanner — says out loud that this deal's saved model is older than
 * the current version of Fondok.
 *
 * The defect it exists for: a healthy deal opened with the whole Stabilization
 * section of Overview rendering five dashes. Nothing was broken — the deal's
 * persisted engine output had been written before that block existed, and no
 * surface said so.
 *
 * How the worker knows (app/services/run_freshness.py): engine output is
 * persisted WITHOUT `exclude_none`, so a field the run knew about is always
 * written — even when null. A key ABSENT entirely means the run predates the
 * field. Only that case reaches this banner, as `stale_run.missing_blocks`
 * (`{ engine: [path, …] }`).
 *
 * What the reader sees is ONE line naming the product tabs affected and a
 * Re-run button. Engine paths never print: a client reads "P&L, CAPEX and
 * Financing", never `revenue.renovation_displacement_by_year`. The raw paths
 * stay behind a collapsed "Technical detail" for support, in a data attribute.
 */

import { History, RefreshCw, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import type { EngineOutputsResponse } from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { REASONS } from '@/lib/ontology/reasons.generated';
import { engineSectionLabel, engineTabLabel } from '@/lib/fieldLabels';

/** The section name for one missing path — never the path itself. */
export function sectionLabelFor(engine: string, path: string): string {
  return engineSectionLabel(engine, path);
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

/** Unique product tabs across every stale engine, in a stable order. */
export function staleTabs(
  missingBlocks: Record<string, string[]> | undefined,
): string[] {
  const seen: string[] = [];
  for (const [engine, paths] of Object.entries(missingBlocks ?? {})) {
    if (!(paths ?? []).length) continue;
    const label = engineTabLabel(engine);
    if (!seen.includes(label)) seen.push(label);
  }
  return seen;
}

/** Count of individual figures the saved run never published. */
export function staleFigureCount(
  missingBlocks: Record<string, string[]> | undefined,
): number {
  let n = 0;
  for (const paths of Object.values(missingBlocks ?? {})) n += (paths ?? []).length;
  return n;
}

/** "P&L, CAPEX and Financing" */
export function joinNatural(parts: string[]): string {
  if (parts.length <= 1) return parts[0] ?? '';
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`;
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/** The one-line headline. Exported so the per-tab notices say the same thing. */
export function staleRunHeadline(
  missingBlocks: Record<string, string[]> | undefined,
): string | null {
  const count = staleFigureCount(missingBlocks);
  if (count === 0) return null;
  const tabs = staleTabs(missingBlocks);
  const figures = count === 1 ? '1 figure' : `${count} figures`;
  const where = tabs.length === 1 ? `on ${tabs[0]}` : `across ${joinNatural(tabs)}`;
  return `This deal’s saved model is older than the current version — ${figures} ${where} show “—” until you re-run.`;
}

export function StaleRunBanner({
  outputs,
  dealId,
}: {
  outputs: EngineOutputsResponse | null;
  dealId: string;
}) {
  // Re-run the full chain (run-all) so every engine republishes together and
  // the snapshot stays internally consistent.
  const { run, status } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  const running = status === 'running' || status === 'queued';

  const missing = outputs?.stale_run?.missing_blocks;
  const headline = staleRunHeadline(missing);
  if (!headline) return null;
  const sections = staleSections(missing);
  const rawPaths = Object.entries(missing ?? {}).flatMap(([engine, paths]) =>
    (paths ?? []).map((p) => `${engine}.${p}`),
  );

  return (
    <Card
      className="p-4 mb-5 border-warn-500/40 bg-warn-50"
      role="status"
      data-testid="stale-run-banner"
      data-stale-paths={rawPaths.join(' ')}
    >
      <div className="flex items-start gap-3">
        <History size={18} className="text-warn-700 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-[13.5px] font-semibold text-warn-700" data-testid="stale-run-headline">
            {headline}
          </div>
          <p className="text-[12px] text-warn-700/80 mt-0.5">
            {REASONS.stale_run.explanation} Nothing already on screen is wrong.
          </p>
          <details className="mt-1.5" data-testid="stale-run-technical">
            <summary className="text-[11px] text-warn-700/70 cursor-pointer select-none">
              Technical detail
            </summary>
            <ul className="mt-1 space-y-0.5" data-testid="stale-run-sections">
              {sections.map((section) => (
                <li key={section} className="text-[11.5px] text-warn-700/90">
                  {section}
                </li>
              ))}
            </ul>
            <pre
              className="mt-1 text-[10.5px] text-warn-700/60 whitespace-pre-wrap break-words"
              title="Engine output blocks the saved run never published"
              data-testid="stale-run-paths"
            >
              {rawPaths.join('\n')}
            </pre>
          </details>
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
