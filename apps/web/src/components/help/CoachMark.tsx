'use client';

/**
 * CoachMark — one-time contextual onboarding hint that points at a specific
 * UI element. Heavier than `Tooltip`: it carries a title, body, primary +
 * secondary actions, and an "X" dismiss. Once the user dismisses it for an
 * `anchorId`, we record `fondok:coachmark:{anchorId}:dismissed=true` in
 * localStorage and never render it again for that user.
 *
 * Sequencing: when multiple coach marks register on the same `viewKey`,
 * only the first un-dismissed one renders. Dismissing it advances the
 * queue — the next un-dismissed mark renders.
 *
 * Global override: when the user toggles "Show contextual coach marks"
 * off in Settings (`fondok:coachmarks:disabled=true`), every CoachMark
 * renders null.
 *
 * Accessibility: the popover is `role="dialog"` + `aria-labelledby` on
 * the title. ESC closes it. The anchor pulses with `ring-2 ring-brand-500`
 * for ~2s on first render so the user's eye finds it.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import { cn } from '@/lib/format';
import { useHintsEnabled } from './useHintsEnabled';

export interface CoachMarkAction {
  label: string;
  onClick: () => void;
}

export interface CoachMarkProps {
  anchorId: string;
  title: string;
  body: ReactNode;
  side?: 'top' | 'right' | 'bottom' | 'left';
  primaryAction?: CoachMarkAction;
  secondaryAction?: CoachMarkAction;
  /** Optional. Adds a `Learn more →` link in the card. */
  learnMoreHref?: string;
  /** Used to sequence multiple coach marks on the same view — only the
   *  earliest un-dismissed mark renders at a time. */
  viewKey?: string;
  /** Default 0 — lower numbers render first when sequencing on a view. */
  order?: number;
  /** Wrapper layout. Defaults to `block` so full-width form fields and
   *  cards keep their dimensions. Switch to `inline` when the target is
   *  a chip / badge inside running text. */
  layout?: 'block' | 'inline';
  children: ReactNode;
}

// ─────────── sequencing registry (per viewKey) ───────────
//
// Each CoachMark with the same viewKey registers itself + listens. The
// queue only renders the anchor with the lowest `order` whose key is
// un-dismissed.

type RegistryEntry = { anchorId: string; order: number; setActive: (a: boolean) => void };
const registries = new Map<string, RegistryEntry[]>();

function dismissedKey(anchorId: string): string {
  return `fondok:coachmark:${anchorId}:dismissed`;
}

function isDismissed(anchorId: string): boolean {
  if (typeof window === 'undefined') return true;
  try {
    return window.localStorage.getItem(dismissedKey(anchorId)) === 'true';
  } catch {
    return true;
  }
}

function markDismissed(anchorId: string): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(dismissedKey(anchorId), 'true');
  } catch {
    // ignore
  }
}

function recompute(viewKey: string): void {
  const list = registries.get(viewKey);
  if (!list) return;
  const sorted = [...list].sort((a, b) => a.order - b.order);
  let activated = false;
  sorted.forEach((entry) => {
    const shouldRender = !activated && !isDismissed(entry.anchorId);
    entry.setActive(shouldRender);
    if (shouldRender) activated = true;
  });
}

// ─────────── positioning (matches Tooltip's flip logic) ───────────

const CARD_GAP = 12;
const VIEWPORT_PAD = 12;

interface Pos {
  top: number;
  left: number;
  resolvedSide: 'top' | 'right' | 'bottom' | 'left';
}

function compute(
  trigger: DOMRect,
  panel: { width: number; height: number },
  preferred: 'top' | 'right' | 'bottom' | 'left',
): Pos {
  const vw = typeof window !== 'undefined' ? window.innerWidth : trigger.right + 1000;
  const vh = typeof window !== 'undefined' ? window.innerHeight : trigger.bottom + 1000;

  const space = {
    top: trigger.top,
    bottom: vh - trigger.bottom,
    left: trigger.left,
    right: vw - trigger.right,
  };
  let side = preferred;
  const needed = side === 'top' || side === 'bottom' ? panel.height : panel.width;
  if (space[side] < needed + CARD_GAP + VIEWPORT_PAD) {
    const opp: Record<typeof side, typeof side> = {
      top: 'bottom',
      bottom: 'top',
      left: 'right',
      right: 'left',
    } as const;
    if (space[opp[side]] >= needed + CARD_GAP + VIEWPORT_PAD) side = opp[side];
  }

  let top = 0;
  let left = 0;
  if (side === 'top') {
    top = trigger.top - panel.height - CARD_GAP;
    left = trigger.left + trigger.width / 2 - panel.width / 2;
  } else if (side === 'bottom') {
    top = trigger.bottom + CARD_GAP;
    left = trigger.left + trigger.width / 2 - panel.width / 2;
  } else if (side === 'left') {
    left = trigger.left - panel.width - CARD_GAP;
    top = trigger.top + trigger.height / 2 - panel.height / 2;
  } else {
    left = trigger.right + CARD_GAP;
    top = trigger.top + trigger.height / 2 - panel.height / 2;
  }

  left = Math.max(VIEWPORT_PAD, Math.min(vw - panel.width - VIEWPORT_PAD, left));
  top = Math.max(VIEWPORT_PAD, Math.min(vh - panel.height - VIEWPORT_PAD, top));

  return { top, left, resolvedSide: side };
}

function arrowFor(side: Pos['resolvedSide']): CSSProperties {
  const base: CSSProperties = {
    position: 'absolute',
    width: 0,
    height: 0,
    borderStyle: 'solid',
    pointerEvents: 'none',
  };
  switch (side) {
    case 'top':
      return {
        ...base,
        bottom: -7,
        left: '50%',
        transform: 'translateX(-50%)',
        borderWidth: '7px 7px 0 7px',
        borderColor: '#ffffff transparent transparent transparent',
        filter: 'drop-shadow(0 1px 0 rgba(99,102,241,0.35))',
      };
    case 'bottom':
      return {
        ...base,
        top: -7,
        left: '50%',
        transform: 'translateX(-50%)',
        borderWidth: '0 7px 7px 7px',
        borderColor: 'transparent transparent #ffffff transparent',
        filter: 'drop-shadow(0 -1px 0 rgba(99,102,241,0.35))',
      };
    case 'left':
      return {
        ...base,
        right: -7,
        top: '50%',
        transform: 'translateY(-50%)',
        borderWidth: '7px 0 7px 7px',
        borderColor: 'transparent transparent transparent #ffffff',
        filter: 'drop-shadow(1px 0 0 rgba(99,102,241,0.35))',
      };
    case 'right':
      return {
        ...base,
        left: -7,
        top: '50%',
        transform: 'translateY(-50%)',
        borderWidth: '7px 7px 7px 0',
        borderColor: 'transparent #ffffff transparent transparent',
        filter: 'drop-shadow(-1px 0 0 rgba(99,102,241,0.35))',
      };
  }
}

// ─────────── component ───────────

export function CoachMark({
  anchorId,
  title,
  body,
  side = 'bottom',
  primaryAction,
  secondaryAction,
  learnMoreHref,
  viewKey,
  order = 0,
  layout = 'block',
  children,
}: CoachMarkProps) {
  const titleId = useId();
  const { enabled: hintsEnabled } = useHintsEnabled();
  const [active, setActive] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [pos, setPos] = useState<Pos | null>(null);
  const [pulsing, setPulsing] = useState(true);
  const triggerRef = useRef<HTMLDivElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => setMounted(true), []);

  // Register in the sequencing queue. With no viewKey, render as soon
  // as un-dismissed.
  useEffect(() => {
    if (!viewKey) {
      setActive(!isDismissed(anchorId));
      return;
    }
    const entry: RegistryEntry = { anchorId, order, setActive };
    const list = registries.get(viewKey) ?? [];
    list.push(entry);
    registries.set(viewKey, list);
    recompute(viewKey);
    return () => {
      const cur = registries.get(viewKey);
      if (!cur) return;
      registries.set(
        viewKey,
        cur.filter((e) => e !== entry),
      );
      recompute(viewKey);
    };
  }, [viewKey, anchorId, order]);

  // Stop pulsing after 2s.
  useEffect(() => {
    if (!active) return;
    const t = setTimeout(() => setPulsing(false), 2000);
    return () => clearTimeout(t);
  }, [active]);

  // Compute position once active + on resize/scroll.
  useLayoutEffect(() => {
    if (!active || !triggerRef.current || !panelRef.current) return;
    const t = triggerRef.current.getBoundingClientRect();
    const p = panelRef.current.getBoundingClientRect();
    setPos(compute(t, { width: p.width, height: p.height }, side));
  }, [active, side, title, body]);

  useEffect(() => {
    if (!active) return;
    const onMove = () => {
      if (!triggerRef.current || !panelRef.current) return;
      const t = triggerRef.current.getBoundingClientRect();
      const p = panelRef.current.getBoundingClientRect();
      setPos(compute(t, { width: p.width, height: p.height }, side));
    };
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [active, side]);

  const dismiss = useCallback(() => {
    markDismissed(anchorId);
    setActive(false);
    if (viewKey) recompute(viewKey);
  }, [anchorId, viewKey]);

  // ESC dismisses.
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') dismiss();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, dismiss]);

  const showCoach = hintsEnabled && active;

  return (
    <>
      {/* Wrapper layout: `block` by default so full-width form fields and
       *  cards keep their dimensions; `inline` when the target lives in
       *  running text (a badge or chip). */}
      {layout === 'inline' ? (
        <span
          ref={(el) => {
            triggerRef.current = el as HTMLDivElement | null;
          }}
          className={cn(
            'inline-block',
            showCoach &&
              pulsing &&
              'rounded-md ring-2 ring-brand-500 ring-offset-2 motion-reduce:animate-none animate-pulse',
          )}
        >
          {children}
        </span>
      ) : (
        <div
          ref={triggerRef}
          className={cn(
            'block',
            showCoach &&
              pulsing &&
              'rounded-md ring-2 ring-brand-500 ring-offset-2 motion-reduce:animate-none animate-pulse',
          )}
        >
          {children}
        </div>
      )}

      {showCoach && mounted && typeof document !== 'undefined'
        ? createPortal(
            <div
              ref={panelRef}
              role="dialog"
              aria-labelledby={titleId}
              aria-modal="false"
              className="fixed z-[9998] animate-in fade-in zoom-in-95 duration-150 motion-reduce:animate-none"
              style={
                pos
                  ? { top: pos.top, left: pos.left }
                  : { top: -9999, left: -9999, opacity: 0 }
              }
            >
              <div className="relative bg-white border border-brand-300 shadow-xl rounded-lg p-4 max-w-sm">
                <button
                  type="button"
                  onClick={dismiss}
                  aria-label="Dismiss hint"
                  className="absolute top-2.5 right-2.5 text-ink-400 hover:text-ink-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded p-0.5"
                >
                  <X size={13} />
                </button>
                <div className="pr-5">
                  <div className="flex items-center gap-1.5 mb-1">
                    <span className="inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
                    <span className="text-[10px] uppercase tracking-wider text-brand-700 font-semibold">
                      Hint
                    </span>
                  </div>
                  <h4
                    id={titleId}
                    className="text-[14px] font-semibold text-ink-900 leading-snug"
                  >
                    {title}
                  </h4>
                  <div className="mt-1.5 text-[12.5px] text-ink-700 leading-relaxed">
                    {body}
                  </div>
                  {learnMoreHref && (
                    <a
                      href={learnMoreHref}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-2 inline-block text-[11.5px] text-brand-700 underline decoration-dotted hover:text-brand-500"
                    >
                      Learn more →
                    </a>
                  )}
                  <div className="mt-3 flex items-center justify-end gap-2">
                    {secondaryAction ? (
                      <button
                        type="button"
                        onClick={() => {
                          secondaryAction.onClick();
                          dismiss();
                        }}
                        className="text-[11.5px] text-ink-500 hover:text-ink-900 px-2 py-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                      >
                        {secondaryAction.label}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={dismiss}
                        className="text-[11.5px] text-ink-500 hover:text-ink-900 px-2 py-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                      >
                        Got it
                      </button>
                    )}
                    {primaryAction && (
                      <button
                        type="button"
                        onClick={() => {
                          primaryAction.onClick();
                          dismiss();
                        }}
                        className="text-[11.5px] bg-brand-500 hover:bg-brand-700 text-white px-2.5 py-1 rounded font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-100"
                      >
                        {primaryAction.label}
                      </button>
                    )}
                  </div>
                </div>
                {pos && <span style={arrowFor(pos.resolvedSide)} aria-hidden="true" />}
              </div>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

export default CoachMark;
