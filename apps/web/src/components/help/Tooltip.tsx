'use client';

/**
 * Tooltip — Fondok's keyboard-accessible, portal-rendered tooltip primitive.
 *
 * Replaces the native `title` attribute everywhere we need a tooltip that:
 *   - Renders on hover AND keyboard focus (native title doesn't)
 *   - Survives `overflow:hidden` containers (uses a React portal)
 *   - Carries styled content (Learn more →, multi-line bodies, formatted text)
 *   - Flips edges when it would overflow the viewport
 *   - Respects `prefers-reduced-motion`
 *
 * Bar: Linear / Stripe Dashboard tooltips. No external library, no animations
 * longer than 150ms, no click-blocking on the trigger.
 *
 * Usage:
 *
 *   <Tooltip content="Levered IRR — annualized return to the equity stack.">
 *     <span className="underline decoration-dotted">Levered IRR</span>
 *   </Tooltip>
 *
 * With a "Learn more →" link that opens the methodology page anchor:
 *
 *   <Tooltip
 *     content="Levered IRR is calibrated against the deal's selected return profile."
 *     learnMoreHref="/methodology#irr"
 *   >
 *     <button>i</button>
 *   </Tooltip>
 */

import {
  Children,
  cloneElement,
  isValidElement,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/format';

export type TooltipSide = 'top' | 'right' | 'bottom' | 'left';
export type TooltipAlign = 'start' | 'center' | 'end';

export interface TooltipProps {
  content: ReactNode;
  side?: TooltipSide;
  align?: TooltipAlign;
  /** Delay (ms) before showing on hover. Default 250. Focus is instant. */
  delayMs?: number;
  /** The trigger element. If non-focusable (span/div), we wrap it with
   *  tabIndex=0 so keyboard users can reach the tip. */
  children: ReactNode;
  /** Optional. Adds a `Learn more →` link in the body. New tab. */
  learnMoreHref?: string;
  /** Optional override for the maximum-width class. Default `max-w-xs`. */
  maxWidthClass?: string;
  /** When false, the tooltip is inert (used when hints are globally disabled). */
  disabled?: boolean;
  /** Extra class names for the floating panel. */
  className?: string;
}

// ───────────────────────── reduced-motion helper ─────────────────────────

function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    setReduced(mql.matches);
    const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener?.('change', onChange);
    return () => mql.removeEventListener?.('change', onChange);
  }, []);
  return reduced;
}

// ───────────────────────── positioning math ─────────────────────────────

interface Position {
  top: number;
  left: number;
  resolvedSide: TooltipSide;
}

const GAP = 8; // pixels between trigger and tooltip
const VIEWPORT_PAD = 8;

function computePosition(
  trigger: DOMRect,
  panel: { width: number; height: number },
  preferredSide: TooltipSide,
  align: TooltipAlign,
): Position {
  const vw =
    typeof window !== 'undefined' ? window.innerWidth : trigger.right + 1000;
  const vh =
    typeof window !== 'undefined' ? window.innerHeight : trigger.bottom + 1000;

  // Flip if the preferred side overflows the viewport
  const space = {
    top: trigger.top,
    bottom: vh - trigger.bottom,
    left: trigger.left,
    right: vw - trigger.right,
  };
  let side: TooltipSide = preferredSide;
  const needed = side === 'top' || side === 'bottom' ? panel.height : panel.width;
  if (space[side] < needed + GAP + VIEWPORT_PAD) {
    const opposites: Record<TooltipSide, TooltipSide> = {
      top: 'bottom',
      bottom: 'top',
      left: 'right',
      right: 'left',
    };
    if (space[opposites[side]] >= needed + GAP + VIEWPORT_PAD) {
      side = opposites[side];
    }
  }

  let top = 0;
  let left = 0;
  if (side === 'top') {
    top = trigger.top - panel.height - GAP;
    left = alignAxis(trigger.left, trigger.width, panel.width, align);
  } else if (side === 'bottom') {
    top = trigger.bottom + GAP;
    left = alignAxis(trigger.left, trigger.width, panel.width, align);
  } else if (side === 'left') {
    left = trigger.left - panel.width - GAP;
    top = alignAxis(trigger.top, trigger.height, panel.height, align);
  } else {
    left = trigger.right + GAP;
    top = alignAxis(trigger.top, trigger.height, panel.height, align);
  }

  // Clamp to viewport so the panel never gets clipped
  left = Math.max(VIEWPORT_PAD, Math.min(vw - panel.width - VIEWPORT_PAD, left));
  top = Math.max(VIEWPORT_PAD, Math.min(vh - panel.height - VIEWPORT_PAD, top));

  return { top, left, resolvedSide: side };
}

function alignAxis(
  triggerStart: number,
  triggerSize: number,
  panelSize: number,
  align: TooltipAlign,
): number {
  if (align === 'start') return triggerStart;
  if (align === 'end') return triggerStart + triggerSize - panelSize;
  return triggerStart + triggerSize / 2 - panelSize / 2;
}

// ───────────────────────── arrow helpers ────────────────────────────────

function arrowStyle(side: TooltipSide): CSSProperties {
  // 6px chevron; pointer-events:none so the tip never blocks the trigger.
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
        bottom: -5,
        left: '50%',
        transform: 'translateX(-50%)',
        borderWidth: '5px 5px 0 5px',
        borderColor: 'rgba(15,23,42,0.95) transparent transparent transparent',
      };
    case 'bottom':
      return {
        ...base,
        top: -5,
        left: '50%',
        transform: 'translateX(-50%)',
        borderWidth: '0 5px 5px 5px',
        borderColor: 'transparent transparent rgba(15,23,42,0.95) transparent',
      };
    case 'left':
      return {
        ...base,
        right: -5,
        top: '50%',
        transform: 'translateY(-50%)',
        borderWidth: '5px 0 5px 5px',
        borderColor: 'transparent transparent transparent rgba(15,23,42,0.95)',
      };
    case 'right':
      return {
        ...base,
        left: -5,
        top: '50%',
        transform: 'translateY(-50%)',
        borderWidth: '5px 5px 5px 0',
        borderColor: 'transparent rgba(15,23,42,0.95) transparent transparent',
      };
  }
}

// ───────────────────────── component ────────────────────────────────────

export function Tooltip({
  content,
  side = 'top',
  align = 'center',
  delayMs = 250,
  children,
  learnMoreHref,
  maxWidthClass = 'max-w-xs',
  disabled = false,
  className,
}: TooltipProps) {
  const tooltipId = useId();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<Position | null>(null);
  const [mounted, setMounted] = useState(false);
  const triggerRef = useRef<HTMLElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const showTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prefersReducedMotion = usePrefersReducedMotion();

  useEffect(() => setMounted(true), []);

  const cancelTimer = useCallback(() => {
    if (showTimer.current) {
      clearTimeout(showTimer.current);
      showTimer.current = null;
    }
  }, []);

  const show = useCallback(
    (instant = false) => {
      if (disabled) return;
      cancelTimer();
      if (instant || delayMs <= 0) {
        setOpen(true);
        return;
      }
      showTimer.current = setTimeout(() => setOpen(true), delayMs);
    },
    [cancelTimer, delayMs, disabled],
  );

  const hide = useCallback(() => {
    cancelTimer();
    setOpen(false);
  }, [cancelTimer]);

  // Compute position once mounted + every time the trigger moves while open.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current || !panelRef.current) return;
    const triggerRect = triggerRef.current.getBoundingClientRect();
    const panelRect = panelRef.current.getBoundingClientRect();
    setPos(
      computePosition(
        triggerRect,
        { width: panelRect.width, height: panelRect.height },
        side,
        align,
      ),
    );
  }, [open, side, align, content]);

  // Reposition on scroll / resize while open.
  useEffect(() => {
    if (!open) return;
    const onMove = () => {
      if (!triggerRef.current || !panelRef.current) return;
      const t = triggerRef.current.getBoundingClientRect();
      const p = panelRef.current.getBoundingClientRect();
      setPos(
        computePosition(t, { width: p.width, height: p.height }, side, align),
      );
    };
    window.addEventListener('scroll', onMove, true);
    window.addEventListener('resize', onMove);
    return () => {
      window.removeEventListener('scroll', onMove, true);
      window.removeEventListener('resize', onMove);
    };
  }, [open, side, align]);

  // ESC closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') hide();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, hide]);

  // Clean up timer on unmount.
  useEffect(() => () => cancelTimer(), [cancelTimer]);

  // ───── trigger wiring: clone if possible, otherwise wrap ─────
  //
  // We attach refs + handlers to the child. When the child is a single React
  // element we clone it; when it's a string / fragment we wrap with a span so
  // the focus ring + ARIA wiring stay consistent.
  const single = Children.count(children) === 1 ? Children.only(children) : null;
  const cloneable =
    single && isValidElement(single) && typeof single.type !== 'symbol';

  const triggerHandlers = {
    onMouseEnter: () => show(),
    onMouseLeave: () => hide(),
    onFocus: () => show(true),
    onBlur: () => hide(),
    'aria-describedby': open ? tooltipId : undefined,
  };

  let trigger: ReactNode;
  if (cloneable) {
    const child = single as ReactElement<any>;
    const existingRef: any = (child as any).ref;
    trigger = cloneElement(child, {
      ...triggerHandlers,
      ref: (el: HTMLElement | null) => {
        triggerRef.current = el;
        if (typeof existingRef === 'function') existingRef(el);
        else if (existingRef && typeof existingRef === 'object') {
          (existingRef as { current: HTMLElement | null }).current = el;
        }
      },
    });
  } else {
    trigger = (
      <span
        ref={(el) => {
          triggerRef.current = el;
        }}
        tabIndex={0}
        className="inline-flex items-center outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded"
        {...triggerHandlers}
      >
        {children}
      </span>
    );
  }

  const panel =
    open && mounted && typeof document !== 'undefined'
      ? createPortal(
          <div
            ref={panelRef}
            id={tooltipId}
            role="tooltip"
            className={cn(
              'fixed z-[9999] pointer-events-none',
              !prefersReducedMotion && 'animate-in fade-in zoom-in-95 duration-100',
              maxWidthClass,
            )}
            style={
              pos
                ? { top: pos.top, left: pos.left }
                : { top: -9999, left: -9999, opacity: 0 }
            }
          >
            <div
              className={cn(
                'relative bg-ink-900/95 text-white text-[12px] leading-snug px-3 py-2 rounded-md shadow-lg',
                // Allow clicks inside when there's a Learn more link.
                learnMoreHref && 'pointer-events-auto',
                className,
              )}
            >
              <div>{content}</div>
              {learnMoreHref && (
                <a
                  href={learnMoreHref}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="mt-1.5 inline-block text-brand-300 underline decoration-dotted text-[11.5px] hover:text-brand-100"
                >
                  Learn more →
                </a>
              )}
              {pos && <span style={arrowStyle(pos.resolvedSide)} aria-hidden="true" />}
            </div>
          </div>,
          document.body,
        )
      : null;

  return (
    <>
      {trigger}
      {panel}
    </>
  );
}

export default Tooltip;
