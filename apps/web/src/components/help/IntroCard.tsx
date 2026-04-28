'use client';
import { useState, useEffect } from 'react';
import { Info, X } from 'lucide-react';
import { cn } from '@/lib/format';

/**
 * Dismissible "what is this view for" card. Persists dismissal under
 * localStorage[`fondok-intro-${dismissKey}`] so users only see each intro
 * once. Two tones — default (brand-toned) and amber for emphasis (e.g. the
 * Variance "why this matters" card).
 */
export function IntroCard({
  title,
  body,
  dismissKey,
  tone = 'default',
}: {
  title: string;
  body: React.ReactNode;
  dismissKey: string;
  tone?: 'default' | 'amber';
}) {
  // Tri-state: null = not yet read from storage (don't render to avoid SSR
  // flicker), true = already dismissed, false = show the card.
  const [dismissed, setDismissed] = useState<boolean | null>(null);

  useEffect(() => {
    const v =
      typeof window !== 'undefined'
        ? localStorage.getItem(`fondok-intro-${dismissKey}`)
        : null;
    setDismissed(v === '1');
  }, [dismissKey]);

  if (dismissed === null || dismissed) return null;

  return (
    <div
      className={cn(
        'mb-5 rounded-lg border p-4 flex items-start gap-3 relative',
        tone === 'amber'
          ? 'bg-warn-50 border-warn-500/30'
          : 'bg-brand-50 border-brand-100',
      )}
    >
      <Info
        size={16}
        className={cn(
          'flex-shrink-0 mt-0.5',
          tone === 'amber' ? 'text-warn-700' : 'text-brand-500',
        )}
      />
      <div className="flex-1 pr-6">
        <div className="text-[13px] font-semibold text-ink-900 mb-1">
          {title}
        </div>
        <div className="text-[12.5px] text-ink-700 leading-relaxed">{body}</div>
      </div>
      <button
        type="button"
        aria-label="Dismiss"
        onClick={() => {
          try {
            localStorage.setItem(`fondok-intro-${dismissKey}`, '1');
          } catch {
            /* swallow — private mode etc. */
          }
          setDismissed(true);
        }}
        className="absolute top-3 right-3 text-ink-400 hover:text-ink-700 p-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        <X size={14} />
      </button>
    </div>
  );
}

export default IntroCard;
