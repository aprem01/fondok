'use client';

import { PlugZap } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn } from '@/lib/format';

/**
 * WorkerNotConfigured — the ONE empty state every data surface renders when
 * `NEXT_PUBLIC_WORKER_URL` is unset (`isWorkerConnected()` is false).
 *
 * Standing rule (no invented data): a build without a worker has no deals, no
 * documents, no engine output and no variance report. It used to fall back to
 * four fictitious hotels and a static engine progress map so the screen looked
 * finished; a reader could not tell them from real data. Now it says so.
 *
 * `surface` names what is missing on this screen ("deal list", "this deal",
 * "engine readiness", "variance report"); `compact` is the in-panel variant a
 * card embeds in place of one section rather than a whole page.
 */
export const WORKER_NOT_CONFIGURED_TITLE = 'Worker not configured';

export function WorkerNotConfigured({
  surface,
  compact = false,
  className,
}: {
  surface: string;
  compact?: boolean;
  className?: string;
}) {
  const body = (
    <>
      This build has no worker URL, so there is no {surface} to show. Nothing is
      filled in from a sample: set <code className="rounded-sm bg-ink-100 px-1 py-0.5">NEXT_PUBLIC_WORKER_URL</code>{' '}
      to connect Fondok&apos;s worker and the {surface} loads from it.
    </>
  );
  if (compact) {
    return (
      <div
        role="status"
        data-testid="worker-not-configured"
        className={cn('flex items-start gap-3 rounded-md border border-dashed border-ink-300 bg-bg/40 px-3.5 py-3', className)}
      >
        <PlugZap size={15} className="text-ink-400 mt-0.5 flex-shrink-0" aria-hidden="true" />
        <div className="min-w-0">
          <div className="text-[12.5px] font-semibold text-ink-900">{WORKER_NOT_CONFIGURED_TITLE}</div>
          <p className="text-[11.5px] text-ink-500 mt-0.5 leading-relaxed">{body}</p>
        </div>
      </div>
    );
  }
  return (
    <Card role="status" data-testid="worker-not-configured" className={cn('p-8 text-center', className)}>
      <div className="w-12 h-12 mx-auto rounded-lg bg-ink-300/20 flex items-center justify-center mb-3">
        <PlugZap size={20} className="text-ink-400" aria-hidden="true" />
      </div>
      <h3 className="text-[14px] font-semibold text-ink-900 mb-1">{WORKER_NOT_CONFIGURED_TITLE}</h3>
      <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">{body}</p>
    </Card>
  );
}
