'use client';
/**
 * Wave 5 — the deal header's one status control (replaces the scattered
 * "Mark as IC Ready" buttons). The pill opens a menu of the statuses the
 * team sets; every choice goes through `api.deals.setStatus` (POST
 * /deals/{id}/status — validated, run-gated and audited by the worker).
 * The worker's refusal sentence is shown verbatim.
 */
import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Loader2 } from 'lucide-react';
import { StatusBadge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { api, userMessage } from '@/lib/api';
import type { ManualDealStatus, ProjectStatus } from '@/lib/catalogs';
import { DEAL_STATUS_HELP, needsModelRun, settableStatuses } from '@/lib/dealStatus';
import { cn } from '@/lib/format';

export default function DealStatusMenu({
  dealId,
  status,
  hasCompletedRun,
  onChanged,
}: {
  dealId: string;
  status: ProjectStatus | null;
  /** The served run has finished — enables the three IC statuses (a hint;
   *  the worker decides). */
  hasCompletedRun: boolean;
  onChanged: () => void;
}) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const options = settableStatuses(status);
  if (status == null) return null;
  if (options.length === 0) {
    return (
      <span title={DEAL_STATUS_HELP[status]}>
        <StatusBadge value={status} />
      </span>
    );
  }

  const choose = async (next: ManualDealStatus) => {
    setOpen(false);
    if (next === status || busy) return;
    setBusy(true);
    try {
      await api.deals.setStatus(dealId, next);
      toast(`Status set to ${next}`, { type: 'success' });
      onChanged();
    } catch (err) {
      toast(`Couldn't change the status — ${userMessage(err)}`, { type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative inline-block" ref={ref}>
      <button
        type="button"
        aria-label={`Deal status: ${status}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`${DEAL_STATUS_HELP[status]} Click to change.`}
        disabled={busy}
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        <StatusBadge value={status} />
        {busy
          ? <Loader2 size={12} className="animate-spin text-ink-400" aria-hidden="true" />
          : <ChevronDown size={12} className="text-ink-400" aria-hidden="true" />}
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Set deal status"
          className="absolute left-0 top-full mt-1 z-40 min-w-[260px] bg-white border border-border rounded-lg shadow-lg py-1"
        >
          {options.map((s) => {
            const gated = needsModelRun(s) && !hasCompletedRun;
            const current = s === status;
            const label = status === 'Archived' ? `Restore to ${s}` : s;
            return (
              <button
                key={s}
                type="button"
                role="menuitemradio"
                aria-checked={current}
                data-status={s}
                disabled={gated}
                onClick={() => void choose(s)}
                className={cn(
                  'w-full px-3 py-2 text-left flex items-start gap-2',
                  gated ? 'cursor-not-allowed opacity-60' : 'hover:bg-ink-300/10',
                )}
              >
                <span className="w-3.5 pt-0.5 text-brand-700" aria-hidden="true">
                  {current ? <Check size={12} /> : null}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-[12.5px] text-ink-900">{label}</span>
                  <span className="block text-[11px] text-ink-500">
                    {gated ? 'Run the model first — needs a completed model run.' : DEAL_STATUS_HELP[s]}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
