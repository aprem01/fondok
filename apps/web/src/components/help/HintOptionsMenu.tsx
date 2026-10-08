'use client';

/**
 * R-074 — hint preferences.
 *
 * `HintOptionsMenu` is the small "⋯" menu that sits beside the X on every
 * pop-up hint (IntroCard, CoachMark): "Hide this hint" (same as the X) and
 * "Hide all hints" (the global switch Settings → Hints also flips).
 *
 * `ShowHintsAgainButton` is the global way back — it re-enables hints and
 * forgets every per-hint dismissal. Mounted in the sidebar user menu and on
 * the Methodology page header.
 *
 * Everything persists in this browser's localStorage (try/catch throughout,
 * see `useHintsEnabled.ts`), so a blocked storage just means the menu
 * still closes the hint for this page view.
 */

import { useEffect, useRef, useState } from 'react';
import { MoreHorizontal, RotateCcw } from 'lucide-react';
import { cn } from '@/lib/format';
import { hideAllHints, showHintsAgain } from './useHintsEnabled';

export const HIDE_THIS_HINT = 'Hide this hint';
export const HIDE_ALL_HINTS = 'Hide all hints';
export const SHOW_HINTS_AGAIN = 'Show hints again';

export function HintOptionsMenu({
  onHideThis,
  className,
}: {
  onHideThis: () => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  return (
    <div ref={ref} className={cn('relative', className)}>
      <button
        type="button"
        aria-label="Hint options"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="text-ink-400 hover:text-ink-700 p-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
      >
        <MoreHorizontal size={14} />
      </button>
      {open && (
        <div
          role="menu"
          aria-label="Hint options"
          className="absolute right-0 top-full mt-1 z-[9999] min-w-[150px] bg-white border border-border rounded-md shadow-lg py-1"
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              onHideThis();
            }}
            className="w-full text-left px-3 py-1.5 text-[12px] text-ink-700 hover:bg-ink-300/10"
          >
            {HIDE_THIS_HINT}
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              hideAllHints();
            }}
            className="w-full text-left px-3 py-1.5 text-[12px] text-ink-700 hover:bg-ink-300/10"
          >
            {HIDE_ALL_HINTS}
          </button>
        </div>
      )}
    </div>
  );
}

export function ShowHintsAgainButton({
  className,
  onDone,
  role,
}: {
  className?: string;
  /** Called after the reset with the number of dismissals cleared. */
  onDone?: (cleared: number) => void;
  role?: 'menuitem';
}) {
  return (
    <button
      type="button"
      role={role}
      onClick={() => {
        const n = showHintsAgain();
        onDone?.(n);
      }}
      className={className ?? 'inline-flex items-center gap-1.5 text-[12px] text-ink-700 hover:text-ink-900'}
    >
      <RotateCcw size={12} aria-hidden="true" className="text-ink-500" /> {SHOW_HINTS_AGAIN}
    </button>
  );
}

export default HintOptionsMenu;
