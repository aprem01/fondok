'use client';

/**
 * MisclassificationBanner — warn-tone banner that appears when the
 * Router agent's read of a document disagrees with the analyst's
 * wizard tag.
 *
 * Wave 1 locked product decision: WARN, never silently overwrite.
 * The user picks one path:
 *   - "Use Fondok's classification" → POST accept_classification {use_ai_classification: true}
 *   - "Keep mine"                   → POST accept_classification {use_ai_classification: false}
 *
 * Mounts in two places:
 *   - DataRoomTab — primary surface; the wizard usually leaves before
 *     extraction completes.
 *   - Step 3 of the new-project wizard — only if the user is still on
 *     the page when extraction finishes (rare for non-trivial OMs).
 *
 * The component is presentational; the parent owns the network call.
 */

import { useState } from 'react';
import { AlertCircle, Loader2, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import type { WorkerDocument } from '@/lib/api';

export interface MisclassificationBannerProps {
  document: Pick<
    WorkerDocument,
    'id' | 'filename' | 'doc_type' | 'user_provided_doc_type' | 'misclassified'
  >;
  onAcceptAi: (doc: MisclassificationBannerProps['document']) => Promise<void> | void;
  onKeepMine: (doc: MisclassificationBannerProps['document']) => Promise<void> | void;
  /** Compact mode tightens vertical padding — used inside the wizard step. */
  compact?: boolean;
  /** Optional dismiss hook — when present a × renders top-right and
   *  fires this callback. The banner stays visible until the parent
   *  reflects the resolution. */
  onDismiss?: () => void;
}

/** Friendly label for a doc-type token; falls back to the raw token. */
function labelFor(token: string | null | undefined): string {
  if (!token) return 'Unclassified';
  const upper = token.toUpperCase();
  switch (upper) {
    case 'OM':
      return 'Offering Memorandum';
    case 'T12':
      return 'T-12 / Trailing Twelve Months';
    case 'PNL_MONTHLY':
      return 'Monthly P&L';
    case 'PNL_YTD':
      return 'Year-to-Date P&L';
    case 'PNL':
      return 'Annual P&L';
    case 'STR':
    case 'STR_TREND':
      return 'STR / Comp Set Report';
    case 'CBRE_HORIZONS':
      return 'CBRE Horizons';
    case 'PNL_BENCHMARK':
      return 'P&L Benchmark';
    case 'ROOM_MIX':
      return 'Room Mix / Unit Mix';
    case 'RENT_ROLL':
      return 'Rent Roll';
    case 'CONTRACT':
    case 'LEASES':
      return 'Leases & Agreements';
    case 'INSURANCE':
      return 'Insurance Records';
    case 'PROPERTY_TAX':
      return 'Property Taxes';
    case 'CAPEX':
      return 'Historical CapEx';
    case 'PROPERTY_INFO':
      return 'Basic Property Info';
    case 'SURVEYS':
      return 'Surveys & Reviews';
    case 'MARKET_STUDY':
      return 'Market Study';
    default:
      return upper.replace(/_/g, ' ');
  }
}

export function MisclassificationBanner({
  document: doc,
  onAcceptAi,
  onKeepMine,
  compact = false,
  onDismiss,
}: MisclassificationBannerProps) {
  const [pending, setPending] = useState<'ai' | 'mine' | null>(null);

  const userLabel = labelFor(doc.user_provided_doc_type);
  const aiLabel = labelFor(doc.doc_type);

  const handle = async (which: 'ai' | 'mine') => {
    if (pending) return;
    setPending(which);
    try {
      if (which === 'ai') await onAcceptAi(doc);
      else await onKeepMine(doc);
    } finally {
      setPending(null);
    }
  };

  return (
    <div
      role="alert"
      aria-live="polite"
      className={
        'relative rounded-md bg-warn-50 border border-warn-500/30 ' +
        (compact ? 'p-3' : 'p-4')
      }
    >
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="absolute right-2 top-2 p-1 rounded text-warn-700 hover:bg-warn-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warn-500"
          aria-label="Dismiss classification mismatch banner"
        >
          <X size={12} aria-hidden="true" />
        </button>
      )}
      <div className="flex items-start gap-3">
        <AlertCircle
          size={compact ? 14 : 16}
          className="text-warn-700 flex-shrink-0 mt-0.5"
          aria-hidden="true"
        />
        <div className="flex-1 min-w-0">
          <div className="text-[12.5px] font-semibold text-warn-700">
            Category mismatch — pick the right bucket
          </div>
          <p className="text-[12px] text-warn-700/90 mt-1 leading-relaxed">
            You uploaded{' '}
            <span className="font-medium text-ink-900">{doc.filename}</span>{' '}
            under{' '}
            <span className="font-semibold">{userLabel}</span>, but Fondok thinks
            it&rsquo;s a{' '}
            <span className="font-semibold">{aiLabel}</span>. Engines route on
            category, so this changes which extractor reads it and which
            assumptions it feeds. Choose which to trust — we won&rsquo;t change
            it silently.
          </p>
          <div className="flex flex-wrap items-center gap-2 mt-3">
            <Button
              size="sm"
              variant="primary"
              onClick={() => handle('ai')}
              disabled={pending !== null}
              aria-label={`Use Fondok's classification (${aiLabel}) for ${doc.filename}`}
            >
              {pending === 'ai' && (
                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              )}
              Use Fondok&rsquo;s ({aiLabel})
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => handle('mine')}
              disabled={pending !== null}
              aria-label={`Keep my classification (${userLabel}) for ${doc.filename}`}
            >
              {pending === 'mine' && (
                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              )}
              Keep mine ({userLabel})
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
