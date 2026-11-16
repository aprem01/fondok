'use client';
/**
 * FON-80 (QA 2026-11-16, gap 2) — the deal header's acquisition-stage control.
 * `deals.deal_stage` is what the Projects list's STAGE column binds to; the
 * wizard asks for it on Step 1, but every deal created before it did (and
 * every header-only create) carries none and reads "—" on the list with no
 * way to set one. This pill opens a menu of the catalog's stages and writes
 * the choice through `api.deals.update` (PATCH /deals/{id} — audited by the
 * worker as "Deal stage"). Nothing is defaulted: an unset stage says so.
 */
import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Loader2 } from 'lucide-react';
import { StatusBadge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { api, userMessage } from '@/lib/api';
import { dealStages, type DealStage } from '@/lib/catalogs';
import { notifyDealChanged } from '@/lib/dealEvents';

export const DEAL_STAGE_HELP: Record<DealStage, string> = {
  'Teaser': 'The broker’s teaser is in hand; no confidential materials yet.',
  'Under NDA': 'NDA signed — the OM and operating statements are available.',
  'LOI': 'A letter of intent is out or agreed; terms are being negotiated.',
  'PSA': 'The purchase and sale agreement is signed; the deal is in diligence.',
};

export default function DealStageMenu({
  dealId,
  stage,
  onChanged,
}: {
  dealId: string;
  /** The deal row's stage, or null when none was ever set. */
  stage: DealStage | null;
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

  const choose = async (next: DealStage) => {
    setOpen(false);
    if (next === stage || busy) return;
    setBusy(true);
    try {
      await api.deals.update(dealId, { deal_stage: next });
      toast(`Stage set to ${next}`, { type: 'success' });
      notifyDealChanged(dealId);
      onChanged();
    } catch (err) {
      toast(`Couldn't change the stage — ${userMessage(err)}`, { type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const title = stage
    ? `Acquisition stage: ${DEAL_STAGE_HELP[stage]} Click to change.`
    : 'Acquisition stage not set — this deal was created before Fondok asked for it. Click to set one.';

  return (
    <div className="relative inline-block" ref={ref}>
      <button
        type="button"
        aria-label={stage ? `Deal stage: ${stage}` : 'Deal stage: not set'}
        aria-haspopup="menu"
        aria-expanded={open}
        title={title}
        disabled={busy}
        data-testid="deal-stage-menu"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        {stage
          ? <StatusBadge value={stage} />
          : <span className="text-[11px] text-ink-500 border border-dashed border-ink-300 rounded-md px-1.5 py-0.5">Set stage</span>}
        {busy
          ? <Loader2 size={12} className="animate-spin text-ink-400" aria-hidden="true" />
          : <ChevronDown size={12} className="text-ink-400" aria-hidden="true" />}
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Set deal stage"
          className="absolute left-0 top-full mt-1 z-40 min-w-[260px] bg-white border border-border rounded-lg shadow-lg py-1"
        >
          {dealStages.map((s) => {
            const current = s === stage;
            return (
              <button
                key={s}
                type="button"
                role="menuitemradio"
                aria-checked={current}
                data-stage={s}
                onClick={() => void choose(s)}
                className="w-full px-3 py-2 text-left flex items-start gap-2 hover:bg-ink-300/10"
              >
                <span className="w-3.5 pt-0.5 text-brand-700" aria-hidden="true">
                  {current ? <Check size={12} /> : null}
                </span>
                <span className="flex-1 min-w-0">
                  <span className="block text-[12.5px] text-ink-900">{s}</span>
                  <span className="block text-[11px] text-ink-500">{DEAL_STAGE_HELP[s]}</span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
