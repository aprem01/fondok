'use client';

/**
 * Popover — click-anchored, viewport-flipping content surface.
 *
 * The click-driven sibling of `Tooltip`. Use when you want a rich
 * interactive panel attached to a trigger (CTA buttons, links, longer
 * copy) without taking over the whole viewport with a modal.
 *
 * Bar: Linear's status / priority pickers; Notion's column header
 * popovers; Stripe Dashboard's filter chips. Each one anchors to its
 * trigger, flips on viewport overflow, and closes on outside-click or
 * ESC. No backdrop overlay — it stays light-touch.
 *
 * Usage:
 *
 *   <Popover
 *     content={<GapDetail gap={g} onUpload={…} />}
 *     side="top"
 *     onOpenChange={(o) => …}
 *   >
 *     <button>Open</button>
 *   </Popover>
 *
 * Controlled mode is supported via the `open` + `onOpenChange` props —
 * pass both and the popover stops managing its own open state.
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
  type ReactElement,
  type ReactNode,
} from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/lib/format';

export type PopoverSide = 'top' | 'right' | 'bottom' | 'left';
export type PopoverAlign = 'start' | 'center' | 'end';

export interface PopoverProps {
  content: ReactNode;
  side?: PopoverSide;
  align?: PopoverAlign;
  children: ReactNode;
  /** Optional controlled open state. */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Max width (Tailwind class) for the floating panel. */
  maxWidthClass?: string;
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
  resolvedSide: PopoverSide;
}

const GAP = 8;
const VIEWPORT_PAD = 8;

function alignAxis(
  triggerStart: number,
  triggerSize: number,
  panelSize: number,
  align: PopoverAlign,
): number {
  if (align === 'start') return triggerStart;
  if (align === 'end') return triggerStart + triggerSize - panelSize;
  return triggerStart + triggerSize / 2 - panelSize / 2;
}

function computePosition(
  trigger: DOMRect,
  panel: { width: number; height: number },
  preferredSide: PopoverSide,
  align: PopoverAlign,
): Position {
  const vw = typeof window !== 'undefined' ? window.innerWidth : trigger.right + 1000;
  const vh = typeof window !== 'undefined' ? window.innerHeight : trigger.bottom + 1000;

  const space = {
    top: trigger.top,
    bottom: vh - trigger.bottom,
    left: trigger.left,
    right: vw - trigger.right,
  };
  let side: PopoverSide = preferredSide;
  const needed = side === 'top' || side === 'bottom' ? panel.height : panel.width;
  if (space[side] < needed + GAP + VIEWPORT_PAD) {
    const opposites: Record<PopoverSide, PopoverSide> = {
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

  left = Math.max(VIEWPORT_PAD, Math.min(vw - panel.width - VIEWPORT_PAD, left));
  top = Math.max(VIEWPORT_PAD, Math.min(vh - panel.height - VIEWPORT_PAD, top));

  return { top, left, resolvedSide: side };
}

// ───────────────────────── component ────────────────────────────────────

export function Popover({
  content,
  side = 'top',
  align = 'center',
  children,
  open: controlledOpen,
  onOpenChange,
  maxWidthClass = 'max-w-xs',
  className,
}: PopoverProps) {
  const popId = useId();
  const isControlled = controlledOpen !== undefined;
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = isControlled ? !!controlledOpen : uncontrolledOpen;

  const [pos, setPos] = useState<Position | null>(null);
  const [mounted, setMounted] = useState(false);
  const triggerRef = useRef<HTMLElement | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => setMounted(true), []);

  const setOpen = useCallback(
    (next: boolean) => {
      if (next) {
        returnFocusRef.current = (document.activeElement as HTMLElement) ?? null;
      }
      if (!isControlled) setUncontrolledOpen(next);
      onOpenChange?.(next);
      if (!next) {
        // Return focus to whatever opened the popover.
        setTimeout(() => returnFocusRef.current?.focus?.(), 0);
      }
    },
    [isControlled, onOpenChange],
  );

  const toggle = useCallback(() => setOpen(!open), [open, setOpen]);
  const close = useCallback(() => setOpen(false), [setOpen]);

  // Position once mounted + after content size resolves.
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

  // Reposition on scroll / resize.
  useEffect(() => {
    if (!open) return;
    const onMove = () => {
      if (!triggerRef.current || !panelRef.current) return;
      const t = triggerRef.current.getBoundingClientRect();
      const p = panelRef.current.getBoundingClientRect();
      setPos(computePosition(t, { width: p.width, height: p.height }, side, align));
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
      if (e.key === 'Escape') {
        e.preventDefault();
        close();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, close]);

  // Outside click closes — fires on the next mousedown after open. Anything
  // inside the panel OR the trigger stays open.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      if (panelRef.current?.contains(target)) return;
      if (triggerRef.current?.contains(target)) return;
      close();
    };
    // Defer one tick so the click that opened us doesn't immediately close us.
    const t = setTimeout(() => {
      window.addEventListener('mousedown', onDown);
    }, 0);
    return () => {
      clearTimeout(t);
      window.removeEventListener('mousedown', onDown);
    };
  }, [open, close]);

  // ───── trigger wiring ─────
  const single = Children.count(children) === 1 ? Children.only(children) : null;
  const cloneable =
    single && isValidElement(single) && typeof single.type !== 'symbol';

  const triggerHandlers = {
    onClick: (e: React.MouseEvent) => {
      e.stopPropagation();
      toggle();
    },
    'aria-haspopup': 'dialog' as const,
    'aria-expanded': open,
    'aria-controls': open ? popId : undefined,
  };

  let trigger: ReactNode;
  if (cloneable) {
    const child = single as ReactElement<any>;
    const existingRef: any = (child as any).ref;
    const childOnClick = (child as any).props?.onClick;
    trigger = cloneElement(child, {
      ...triggerHandlers,
      onClick: (e: React.MouseEvent) => {
        childOnClick?.(e);
        if (!e.defaultPrevented) {
          triggerHandlers.onClick(e);
        }
      },
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
            id={popId}
            role="dialog"
            aria-modal="false"
            className={cn(
              'fixed z-[9999]',
              !reducedMotion && 'animate-in fade-in zoom-in-95 duration-150',
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
                'relative bg-white border border-border rounded-lg shadow-xl overflow-hidden',
                className,
              )}
            >
              {content}
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

export default Popover;
