'use client';

/**
 * Citation — inline IC-memo source chip.
 *
 * Clicking dispatches a window-level ``fondok:citation-focus`` event
 * with the citation payload. The globally mounted SourceDocPane
 * listens for that event and slides in showing the cited page.
 *
 * Ported from LogiCov, simplified to a single chip variant. Use this
 * for every numeric or qualitative claim in a memo, summary, or
 * variance row that has a backing document.
 */

import type { MouseEvent, ReactNode } from 'react';
import { cn } from '@/lib/format';

export type CitationData = {
  documentId: string;
  /** Optional human-readable filename — shown in the side pane header. */
  documentName?: string;
  page: number;
  field?: string;
  region?: { x0: number; y0: number; x1: number; y1: number };
  excerpt?: string;
};

export interface CitationProps {
  data: CitationData;
  /** Custom label override (e.g. ``OM:p3`` instead of ``[3]``). */
  label?: string;
  /** Wrap a span around children to make the surrounding text clickable. */
  children?: ReactNode;
  className?: string;
}

export function Citation({ data, label, children, className }: CitationProps) {
  const onClick = (e: MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (typeof window === 'undefined') return;
    window.dispatchEvent(
      new CustomEvent('fondok:citation-focus', { detail: data }),
    );
  };

  // When children are passed we render a "wrap" variant: the highlighted
  // phrase itself becomes the click target, with a subtle gold underline.
  if (children) {
    return (
      <button
        type="button"
        onClick={onClick}
        title={
          data.excerpt
            ? `${data.excerpt} — p.${data.page}`
            : `Source p.${data.page}`
        }
        className={cn(
          'inline cursor-pointer bg-transparent p-0 m-0 border-0',
          'text-brand-700 underline decoration-brand-300 decoration-dotted underline-offset-2',
          'hover:decoration-brand-500 hover:bg-brand-50/60 rounded',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          className,
        )}
      >
        {children}
      </button>
    );
  }

  // Default chip variant — small monospaced superscript reference.
  return (
    <button
      type="button"
      onClick={onClick}
      title={
        data.excerpt
          ? `${data.excerpt} — p.${data.page}`
          : `Source p.${data.page}`
      }
      className={cn(
        'inline-flex items-baseline gap-0.5 px-1 py-0 rounded',
        'bg-brand-50 text-brand-700 hover:bg-brand-100',
        'text-[10.5px] font-semibold tabular-nums leading-none',
        'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
        'transition-colors',
        className,
      )}
    >
      <span aria-hidden className="text-[9px]">↗</span>
      {label ?? `[${data.page}]`}
    </button>
  );
}
