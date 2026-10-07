'use client';

/**
 * HScroll — a horizontal scroll container whose overflow is DISCOVERABLE.
 *
 * FON-41 E-026 (external testers): on Historical P&L the extra year columns sat
 * off-screen and nothing said so — the OS hides the scrollbar until a trackpad
 * gesture, and the table simply ended at the card edge. Three cues now say
 * "there is more":
 *
 *   1. a persistent horizontal scrollbar (`overflow-x: auto` +
 *      `scrollbar-gutter: stable`, with a styled, always-drawn track — see
 *      `.fondok-hscroll` in `app/globals.css`);
 *   2. a right-edge fade while content extends past the viewport (and a
 *      left-edge fade once the analyst has scrolled);
 *   3. a "N more years →" pill counting the column headers whose right edge
 *      sits past the visible area — the headers opt in with `data-year-col`.
 *
 * The count is measured off the real header cells, never assumed from a column
 * total, so it is right when the "Show" trim or a period filter changes what
 * renders. In jsdom every box is 0×0, so the cues simply never show there.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

export interface HScrollProps {
  children: ReactNode;
  /** Noun for the cue ("year" → "3 more years →"). */
  unit?: string;
  /** Selector for the column headers to count; defaults to `[data-year-col]`. */
  columnSelector?: string;
  className?: string;
  'data-testid'?: string;
}

interface Overflow {
  hiddenRight: number;
  canScrollLeft: boolean;
  canScrollRight: boolean;
}

const NONE: Overflow = { hiddenRight: 0, canScrollLeft: false, canScrollRight: false };

export function HScroll({
  children,
  unit = 'column',
  columnSelector = '[data-year-col]',
  className,
  'data-testid': testId,
}: HScrollProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [state, setState] = useState<Overflow>(NONE);

  const measure = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const visibleRight = el.scrollLeft + el.clientWidth;
    const canScrollRight = el.scrollWidth - visibleRight > 1;
    const canScrollLeft = el.scrollLeft > 1;
    let hiddenRight = 0;
    if (canScrollRight) {
      const cells = el.querySelectorAll<HTMLElement>(columnSelector);
      cells.forEach((c) => {
        // offsetLeft is relative to the offsetParent; the table sits at the
        // scroller's origin so this is the column's left edge in scroll space.
        const left = c.offsetLeft;
        const width = c.offsetWidth;
        // Count a column once MOST of it is past the visible edge, so a column
        // clipped by a few pixels is not reported as hidden.
        if (width > 0 && left + width / 2 > visibleRight) hiddenRight += 1;
      });
    }
    setState((prev) =>
      prev.hiddenRight === hiddenRight &&
      prev.canScrollLeft === canScrollLeft &&
      prev.canScrollRight === canScrollRight
        ? prev
        : { hiddenRight, canScrollLeft, canScrollRight },
    );
  }, [columnSelector]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    measure();
    const onScroll = () => measure();
    el.addEventListener('scroll', onScroll, { passive: true });
    let ro: ResizeObserver | null = null;
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => measure());
      ro.observe(el);
      if (el.firstElementChild) ro.observe(el.firstElementChild);
    }
    window.addEventListener('resize', measure);
    return () => {
      el.removeEventListener('scroll', onScroll);
      ro?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [measure, children]);

  const scrollByColumn = () => {
    const el = ref.current;
    if (!el) return;
    el.scrollBy({ left: Math.max(160, el.clientWidth * 0.6), behavior: 'smooth' });
  };

  const n = state.hiddenRight;
  const cue = n > 0 ? `${n} more ${unit}${n === 1 ? '' : 's'} →` : null;

  return (
    <div className={className} style={{ position: 'relative' }} data-testid={testId}>
      <div
        ref={ref}
        className="fondok-hscroll"
        data-testid={testId ? `${testId}-scroller` : undefined}
        data-overflow-right={state.canScrollRight ? 'true' : 'false'}
      >
        {children}
      </div>
      {state.canScrollLeft && (
        <div aria-hidden="true" className="fondok-hscroll-fade fondok-hscroll-fade-left" />
      )}
      {state.canScrollRight && (
        <div aria-hidden="true" className="fondok-hscroll-fade fondok-hscroll-fade-right" />
      )}
      {cue && (
        <button
          type="button"
          onClick={scrollByColumn}
          className="fondok-hscroll-cue"
          data-testid={testId ? `${testId}-cue` : 'hscroll-cue'}
          title="More columns to the right — scroll, or click to move over"
        >
          {cue}
        </button>
      )}
    </div>
  );
}

export default HScroll;
