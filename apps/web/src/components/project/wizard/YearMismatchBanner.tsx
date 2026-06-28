'use client';

/**
 * YearMismatchBanner — Wave 1 #4.
 *
 * Mirrors MisclassificationBanner: warns when the analyst pinned a
 * ``fiscal_year`` in the wizard and the Extractor pulled a
 * ``period_ending`` whose year disagrees. The user picks one path:
 *
 *   - "Use Fondok's year" → POST accept_year {use_ai_year: true}
 *     fiscal_year is overwritten with extracted_period_year.
 *   - "Keep mine"          → POST accept_year {use_ai_year: false}
 *     fiscal_year stays as the analyst's tag.
 *
 * Both branches clear ``year_mismatch`` and dismiss the banner.
 */

import { useState } from 'react';
import { CalendarClock, Loader2, X } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import type { WorkerDocument } from '@/lib/api';

export interface YearMismatchBannerProps {
  document: Pick<
    WorkerDocument,
    | 'id'
    | 'filename'
    | 'fiscal_year'
    | 'extracted_period_year'
    | 'year_mismatch'
  >;
  onAcceptAi: (doc: YearMismatchBannerProps['document']) => Promise<void> | void;
  onKeepMine: (doc: YearMismatchBannerProps['document']) => Promise<void> | void;
  compact?: boolean;
  onDismiss?: () => void;
}

export function YearMismatchBanner({
  document: doc,
  onAcceptAi,
  onKeepMine,
  compact = false,
  onDismiss,
}: YearMismatchBannerProps) {
  const [pending, setPending] = useState<'ai' | 'mine' | null>(null);
  const userYear = doc.fiscal_year;
  const aiYear = doc.extracted_period_year;

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
          aria-label="Dismiss year mismatch banner"
        >
          <X size={12} aria-hidden="true" />
        </button>
      )}
      <div className="flex items-start gap-3">
        <CalendarClock
          size={compact ? 14 : 16}
          className="text-warn-700 flex-shrink-0 mt-0.5"
          aria-hidden="true"
        />
        <div className="flex-1 min-w-0">
          <div className="text-[12.5px] font-semibold text-warn-700">
            Year doesn&rsquo;t match the document
          </div>
          <p className="text-[12px] text-warn-700/90 mt-1 leading-relaxed">
            <span className="font-medium text-ink-900">{doc.filename}</span> was
            tagged as fiscal year{' '}
            <span className="font-semibold tabular-nums">{userYear ?? '—'}</span>{' '}
            in the wizard, but Fondok read the period ending as{' '}
            <span className="font-semibold tabular-nums">{aiYear ?? '—'}</span>.
            Pick one — historical variance flags depend on aligning to the
            right year.
          </p>
          <div className="flex flex-wrap items-center gap-2 mt-3">
            <Button
              size="sm"
              variant="primary"
              onClick={() => handle('ai')}
              disabled={pending !== null || aiYear == null}
              aria-label={`Use Fondok's year (${aiYear ?? '—'}) for ${doc.filename}`}
            >
              {pending === 'ai' && (
                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              )}
              Use Fondok&rsquo;s ({aiYear ?? '—'})
            </Button>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => handle('mine')}
              disabled={pending !== null}
              aria-label={`Keep my year (${userYear ?? '—'}) for ${doc.filename}`}
            >
              {pending === 'mine' && (
                <Loader2 size={11} className="animate-spin" aria-hidden="true" />
              )}
              Keep mine ({userYear ?? '—'})
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
