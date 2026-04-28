'use client';

/**
 * SourceDocPane — global slide-over that reveals the cited PDF page.
 *
 * Mounted once in AppShell. Listens for window-level
 * ``fondok:citation-focus`` events and slides in from the right with:
 *   • the document filename + page number
 *   • the cited excerpt highlighted
 *   • a deep-link to the worker's PDF preview route (when configured)
 *
 * State is fully local. ESC and outside-click both close. The pane
 * overlays page content but leaves the sidebar untouched.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import { FileText, X, ExternalLink, Loader2 } from 'lucide-react';
import { cn } from '@/lib/format';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { isWorkerConnected, workerUrl } from '@/lib/api';

type FocusDetail = {
  documentId: string;
  documentName?: string;
  page: number;
  field?: string;
  region?: { x0: number; y0: number; x1: number; y1: number };
  excerpt?: string;
};

const PANE_WIDTH = 480;

export default function SourceDocPane() {
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState<FocusDetail | null>(null);
  const paneRef = useRef<HTMLDivElement>(null);

  // Try to infer the active deal from the URL — works on /projects/:id.
  // When we're elsewhere the pane still opens; we just lack live extraction.
  const params = useParams();
  const rawId = (params?.id as string | undefined) ?? '';
  const isMockId = /^\d+$/.test(rawId);
  const enableLive = isWorkerConnected() && rawId && !isMockId;
  const { documents, extractions } = useDocuments(enableLive ? rawId : '');

  // Subscribe to citation-focus events. Each new event opens the pane
  // and replaces whatever was previously displayed.
  useEffect(() => {
    function handler(e: Event) {
      const ce = e as CustomEvent<FocusDetail>;
      if (!ce.detail) return;
      setFocus(ce.detail);
      setOpen(true);
    }
    window.addEventListener('fondok:citation-focus', handler);
    return () => window.removeEventListener('fondok:citation-focus', handler);
  }, []);

  // ESC closes.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);

  // Click outside (on backdrop) closes.
  const onBackdropClick = useCallback((e: React.MouseEvent) => {
    if (paneRef.current && !paneRef.current.contains(e.target as Node)) {
      setOpen(false);
    }
  }, []);

  // Best-effort: resolve the doc record from the live document list so
  // we can display the real filename and pull parsed page text out of
  // the extraction payload (when the worker eventually exposes it).
  const matchedDoc = useMemo(() => {
    if (!focus) return null;
    return documents.find((d) => d.id === focus.documentId) ?? null;
  }, [focus, documents]);

  const matchedExtraction = useMemo(() => {
    if (!focus) return null;
    return extractions[focus.documentId] ?? null;
  }, [focus, extractions]);

  // The worker doesn't yet expose ``parsed_pages``; when it does we'll
  // pluck the page text out of the extraction payload here. Until
  // then we display the cited excerpt as the body.
  const pageText = useMemo<string | null>(() => {
    if (!matchedExtraction) return null;
    const ex = matchedExtraction as unknown as {
      parsed_pages?: Record<string | number, string>;
    };
    if (!ex.parsed_pages || !focus) return null;
    return ex.parsed_pages[String(focus.page)] ?? null;
  }, [matchedExtraction, focus]);

  const filename =
    focus?.documentName ?? matchedDoc?.filename ?? focus?.documentId ?? '';

  // Worker preview route is best-effort — left as TODO worker-side.
  const previewUrl =
    focus && rawId && workerUrl()
      ? `${workerUrl()}/deals/${rawId}/documents/${focus.documentId}/preview?page=${focus.page}`
      : null;

  if (!open || !focus) return null;

  return (
    <div
      className="fixed inset-0 z-40 flex justify-end"
      onClick={onBackdropClick}
      role="presentation"
    >
      {/* Soft scrim — keeps the sidebar visible but tones down the page */}
      <div
        aria-hidden
        className="absolute inset-0 bg-ink-900/20 backdrop-blur-[1px] animate-[fadeIn_140ms_ease-out_both]"
      />

      <aside
        ref={paneRef}
        role="dialog"
        aria-modal="true"
        aria-label={`Source: ${filename}`}
        style={{ width: PANE_WIDTH }}
        className={cn(
          'relative h-full bg-card shadow-card-hover border-l border-border',
          'flex flex-col animate-[slideInRight_220ms_cubic-bezier(0.22,1,0.36,1)_both]',
        )}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-4 py-3.5 border-b border-border bg-bg/60">
          <div className="flex items-start gap-2.5 min-w-0">
            <FileText size={15} className="text-brand-500 mt-0.5 shrink-0" />
            <div className="min-w-0">
              <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold">
                Source Document
              </div>
              <div className="text-[13px] font-semibold text-ink-900 truncate">
                {filename || 'Unknown document'}
              </div>
              <div className="text-[11px] text-ink-500 mt-0.5 tabular-nums font-mono">
                page {focus.page}
                {focus.field ? ` · ${focus.field}` : ''}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close"
            className="p-1 -m-1 rounded text-ink-500 hover:text-ink-900 hover:bg-ink-300/20 transition-colors shrink-0"
          >
            <X size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
          {/* Excerpt highlight — always shown when we have one */}
          {focus.excerpt ? (
            <div className="rounded-md border border-brand-500/20 bg-bg/50 p-3">
              <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">
                Cited excerpt
              </div>
              <div className="text-[12.5px] leading-relaxed text-ink-900 font-serif">
                <mark className="bg-brand-50 text-ink-900 px-0.5 rounded-sm">
                  {focus.excerpt}
                </mark>
              </div>
            </div>
          ) : null}

          {/* Full page text when extraction has provided it */}
          {pageText ? (
            <div className="rounded-md border border-border bg-card p-3">
              <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">
                Page text
              </div>
              <div className="text-[12px] leading-relaxed text-ink-700 whitespace-pre-wrap font-serif">
                {focus.excerpt
                  ? highlightExcerpt(pageText, focus.excerpt)
                  : pageText}
              </div>
            </div>
          ) : enableLive && !matchedExtraction ? (
            <div className="flex items-center gap-2 text-[12px] text-ink-500 px-3 py-4 rounded-md border border-dashed border-border">
              <Loader2 size={12} className="animate-spin" />
              Loading source…
            </div>
          ) : !focus.excerpt ? (
            <div className="text-[12px] text-ink-500 px-3 py-4 rounded-md border border-dashed border-border">
              No excerpt is attached to this citation. Open the source PDF to
              view the cited region.
            </div>
          ) : null}

          {/* Region coordinates — diagnostic, helpful while building agents */}
          {focus.region ? (
            <div className="rounded-md border border-border bg-bg/40 p-3">
              <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5">
                Region
              </div>
              <div className="text-[11px] font-mono tabular-nums text-ink-700">
                x0={focus.region.x0.toFixed(1)} · y0={focus.region.y0.toFixed(1)} ·
                {' '}x1={focus.region.x1.toFixed(1)} · y1={focus.region.y1.toFixed(1)}
              </div>
            </div>
          ) : null}
        </div>

        {/* Footer — open in PDF */}
        {previewUrl ? (
          <div className="border-t border-border px-4 py-3 bg-bg/60">
            <a
              href={previewUrl}
              target="_blank"
              rel="noopener noreferrer"
              className={cn(
                'inline-flex items-center gap-1.5 text-[12px] font-medium',
                'text-brand-700 hover:text-brand-500 transition-colors',
              )}
            >
              <ExternalLink size={12} />
              See PDF page {focus.page}
            </a>
          </div>
        ) : null}
      </aside>

      <style jsx>{`
        @keyframes fadeIn {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to   { transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}

/**
 * Wrap the cited excerpt with a <mark> in the surrounding page text. We
 * fall back to raw text when no match is found rather than mangling the
 * string — citations don't always come from extracted page text verbatim.
 */
function highlightExcerpt(pageText: string, excerpt: string): React.ReactNode {
  const idx = pageText.indexOf(excerpt);
  if (idx < 0) return pageText;
  return (
    <>
      {pageText.slice(0, idx)}
      <mark className="bg-brand-50 text-ink-900 px-0.5 rounded-sm">
        {excerpt}
      </mark>
      {pageText.slice(idx + excerpt.length)}
    </>
  );
}
