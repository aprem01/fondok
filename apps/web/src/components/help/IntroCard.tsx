'use client';
import { useState, useEffect } from 'react';
import { Info, X } from 'lucide-react';
import { cn } from '@/lib/format';
import { HintOptionsMenu } from './HintOptionsMenu';
import { useHintsEnabled, HINTS_CHANGED_EVENT } from './useHintsEnabled';

/**
 * Dismissible "what is this view for" card. Persists dismissal under both
 * localStorage[`fondok-intro-${dismissKey}`] *and* a year-long cookie of
 * the same name — Sam reported the banner reappearing across sessions, and
 * the cookie fallback covers browsers that wipe localStorage on close
 * (private/incognito modes) or partitioned-storage clears.
 *
 * R-074 — beside the X sits a hint-options menu ("Hide this hint" /
 * "Hide all hints"). The card honours the global hints switch, and re-reads
 * its dismissal when "Show hints again" fires the hints-changed event.
 */
function readDismissed(dismissKey: string): boolean {
  if (typeof window === 'undefined') return false;
  const k = `fondok-intro-${dismissKey}`;
  try {
    if (window.localStorage.getItem(k) === '1') return true;
  } catch {
    /* private mode etc. */
  }
  try {
    const cookies = document.cookie.split(';');
    const needle = `${k}=1`;
    return cookies.some(c => c.trim() === needle);
  } catch {
    return false;
  }
}

function writeDismissed(dismissKey: string): void {
  if (typeof window === 'undefined') return;
  const k = `fondok-intro-${dismissKey}`;
  try {
    window.localStorage.setItem(k, '1');
  } catch {
    /* swallow */
  }
  try {
    const oneYear = 60 * 60 * 24 * 365;
    document.cookie = `${k}=1; Max-Age=${oneYear}; Path=/; SameSite=Lax`;
  } catch {
    /* swallow */
  }
}

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
  const { enabled: hintsOn } = useHintsEnabled();

  useEffect(() => {
    setDismissed(readDismissed(dismissKey));
    const sync = () => setDismissed(readDismissed(dismissKey));
    window.addEventListener(HINTS_CHANGED_EVENT, sync);
    return () => window.removeEventListener(HINTS_CHANGED_EVENT, sync);
  }, [dismissKey]);

  if (dismissed === null || dismissed || !hintsOn) return null;
  const hideThis = () => {
    writeDismissed(dismissKey);
    setDismissed(true);
  };

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
      <div className="flex-1 pr-12">
        <div className="text-[13px] font-semibold text-ink-900 mb-1">
          {title}
        </div>
        <div className="text-[12.5px] text-ink-700 leading-relaxed">{body}</div>
      </div>
      <div className="absolute top-3 right-3 flex items-center gap-0.5">
        <HintOptionsMenu onHideThis={hideThis} />
        <button
          type="button"
          aria-label="Dismiss"
          onClick={hideThis}
          className="text-ink-400 hover:text-ink-700 p-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}

export default IntroCard;
