'use client';

/**
 * LineageDrawer — walk one number down to the page it came from (Phase 2.4).
 *
 * A right-anchored drawer that renders
 * ONE walk of the deal's lineage graph as a vertical chain:
 *
 *   Levered IRR            ← the KPI you clicked
 *     computed from ─ Equity cash flows
 *       normalized from ─ Rooms revenue (T-12)
 *         extracted from ─ Kimpton_T12_2025.pdf
 *           located on ─ Page 4
 *
 * Every step shows the node's label, its value with unit, and its source
 * badge; a `page` step additionally shows the document filename + page number
 * and can open the source pane. A stale record shows the "this run predates
 * the latest document or override" notice. A link the graph could not resolve
 * renders the refusal glyph plus its reason label from `REASONS` — never an
 * empty row.
 *
 * Nothing here introduces a new visual language: the dots are the canonical
 * `ProvenanceDot`, the chips + rows are the Provenance Ledger's list styling,
 * and the colours come from the existing tokens.
 *
 * Opened from the traceability affordances (`help/Traced`, `help/Sourced`, the
 * IC Memo KPI tiles) via the `fondok:lineage-open` window event, which the
 * globally mounted `<LineageDrawerHost>` (in `layout/AppShell`) listens for.
 * The endpoint is additive — when it 404s the drawer says so quietly and the
 * host screen renders exactly as it does today.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { FileText, X } from 'lucide-react';
import { cn } from '@/lib/format';
import { formatValue } from '@/lib/format';
import { sourceKind, sourceLabel } from '@/lib/provenance';
import { ProvenanceDot } from '@/components/design';
import type { ValueState } from '@/lib/api';
import type { LineageEdgeRel, LineageNode } from '@/lib/api';
import { REASONS, REFUSAL_GLYPH, isReasonCode } from '@/lib/ontology/reasons.generated';
import {
  documentIdOf,
  pageNumberOf,
  useLineage,
  type LineageStep,
  type LineageWalk,
} from '@/lib/hooks/useLineage';

/* ───────────────────────── open protocol ───────────────────────── */

export const LINEAGE_OPEN_EVENT = 'fondok:lineage-open';

export interface LineageOpenDetail {
  dealId: string;
  /** Candidate root ids, tried in order — the first the record knows wins.
   *  Lets a caller offer `kpi:returns.levered_irr` and fall back to
   *  `engine:returns.levered_irr` without knowing how the graph was built. */
  rootId: string | string[];
  /** Headline for the drawer — the label of the number being traced. */
  title?: string;
}

/** Ask the globally mounted drawer to trace a value. No-op server-side. */
export function openLineage(detail: LineageOpenDetail): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(LINEAGE_OPEN_EVENT, { detail }));
}

/* ───────────────────────── vocabulary ───────────────────────── */

const REL_LABEL: Record<LineageEdgeRel, string> = {
  computed_from: 'Computed from',
  seeded_from: 'Seeded from',
  normalized_from: 'Normalized from',
  extracted_from: 'Extracted from',
  located_on: 'Located on',
  overridden_by: 'Overridden by',
  cited_in: 'Cited in',
};

const KIND_LABEL: Record<LineageNode['kind'], string> = {
  kpi: 'KPI',
  engine_value: 'Engine value',
  assumption: 'Assumption',
  normalized_line: 'Normalized line',
  extracted_field: 'Extracted field',
  document: 'Document',
  page: 'Page',
  override: 'Override',
  seed: 'Seed',
  benchmark: 'Benchmark',
  memo_section: 'Memo section',
};

const VALUE_STATES = new Set<string>([
  'document_sourced',
  'linked',
  'assumption',
  'calculated',
  'awaiting_data',
  'needs_review',
]);

/** The canonical origin dot for a step — the node's own state when it
 *  carries one, otherwise the state its kind implies. A dashed step always
 *  reads as awaiting_data. */
function dotState(step: LineageStep): ValueState {
  if (step.refusal || step.missing) return 'awaiting_data';
  const s = step.node.state;
  if (s && VALUE_STATES.has(s)) return s as ValueState;
  switch (step.node.kind) {
    case 'document':
    case 'page':
    case 'extracted_field':
    case 'normalized_line':
      return 'document_sourced';
    case 'assumption':
    case 'seed':
    case 'benchmark':
    case 'override':
      return 'assumption';
    default:
      return 'calculated';
  }
}

/** Value + unit. `formatValue` already folds USD / percent / ratio into the
 *  string; anything else (keys, x, nights…) is appended so the unit is never
 *  lost. Returns null when the node carries no value. */
function displayValue(node: LineageNode): string | null {
  if (node.value == null || node.value === '') return null;
  const shown = formatValue(node.value, node.unit, node.concept ?? node.label);
  const folded = node.unit && ['USD', 'percent', 'ratio'].includes(node.unit);
  return node.unit && !folded ? `${shown} ${node.unit}` : shown;
}

/** Filename for a `page` / `document` step — the node's own meta first, then
 *  the `doc:` node in the same walk, then the document id. */
function filenameFor(step: LineageStep, walk: LineageWalk): string | null {
  const meta = step.node.meta ?? {};
  for (const k of ['filename', 'document_name', 'doc_name']) {
    const v = meta[k];
    if (typeof v === 'string' && v.trim()) return v;
  }
  if (step.node.kind === 'document') return step.node.label || null;
  const docId = documentIdOf(step.node);
  if (!docId) return null;
  const docStep = walk.steps.find((s) => s.node.id === `doc:${docId}`);
  if (docStep) {
    const dm = docStep.node.meta?.['filename'];
    if (typeof dm === 'string' && dm.trim()) return dm;
    if (docStep.node.label) return docStep.node.label;
  }
  return docId;
}

/* ───────────────────────── chip (mirrors the ledger) ───────────────────────── */

function SourceChip({ source }: { source: string }) {
  const kind = sourceKind(source);
  const cls =
    kind === 'grounded'
      ? 'bg-success-50 text-success-700'
      : kind === 'override'
        ? 'bg-brand-50 text-brand-700'
        : 'bg-warn-50 text-warn-700';
  return (
    <span
      className={cn(
        'inline-flex items-center rounded px-1.5 py-0.5 text-[10.5px] font-medium',
        cls,
      )}
    >
      {sourceLabel(source)}
    </span>
  );
}

function KindChipNeutral({ children }: { children: React.ReactNode }) {
  return (
    <span className="inline-flex items-center rounded bg-ink-100 px-1.5 py-0.5 text-[10.5px] font-medium text-ink-600">
      {children}
    </span>
  );
}

/* ───────────────────────── one step ───────────────────────── */

function ChainStep({
  step,
  walk,
  isLast,
}: {
  step: LineageStep;
  walk: LineageWalk;
  isLast: boolean;
}) {
  const value = displayValue(step.node);
  const reason =
    step.refusal && isReasonCode(step.refusal.code) ? REASONS[step.refusal.code] : null;
  const isPage = step.node.kind === 'page';
  const filename = isPage || step.node.kind === 'document' ? filenameFor(step, walk) : null;
  const page = isPage ? pageNumberOf(step.node) : null;
  const docId = isPage || step.node.kind === 'document' ? documentIdOf(step.node) : null;

  const openDoc = () => {
    if (!docId || typeof window === 'undefined') return;
    window.dispatchEvent(
      new CustomEvent('fondok:citation-focus', {
        detail: { documentId: docId, documentName: filename ?? undefined, page: page ?? 1 },
      }),
    );
  };

  // Don't echo the label back at the reader: a `doc:` node is already
  // named by its filename, a page node often by "Page 4".
  const showFilename = !!filename && filename !== step.node.label;
  const showPage = page != null && step.node.label !== `Page ${page}`;

  return (
    <li className="border-t border-border first:border-t-0" data-testid={`lineage-step-${step.node.id}`}>
      {step.edge && (
        <div
          className="px-5 pt-2 text-[10px] uppercase tracking-wider text-ink-500 font-semibold"
          style={{ paddingLeft: 20 + step.depth * 10 }}
        >
          {REL_LABEL[step.edge.rel] ?? step.edge.rel}
        </div>
      )}
      <div
        className="flex items-start gap-2.5 px-5 py-2"
        style={{ paddingLeft: 20 + step.depth * 10 }}
      >
        <span className="mt-1.5 shrink-0">
          <ProvenanceDot state={dotState(step)} size={7} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline justify-between gap-3">
            <span className="text-[12.5px] text-ink-900 font-medium truncate">
              {step.node.label}
            </span>
            <span className="shrink-0 tabular-nums text-[12.5px] font-medium text-ink-900">
              {value ?? (
                <span className="text-ink-400" aria-label="No value">
                  {REFUSAL_GLYPH}
                </span>
              )}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            {step.node.source ? (
              <SourceChip source={step.node.source} />
            ) : (
              <KindChipNeutral>{KIND_LABEL[step.node.kind] ?? step.node.kind}</KindChipNeutral>
            )}
            {reason && <span className="text-[11px] text-ink-500">{reason.label}</span>}
          </div>
          {step.edge?.formula && (
            <div className="mt-1 font-mono text-[11px] leading-snug text-ink-600">
              {step.edge.formula}
            </div>
          )}
          {(showFilename || showPage || docId) && (
            <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[11.5px] text-ink-600">
              <FileText size={12} className="shrink-0 text-ink-400" aria-hidden="true" />
              {showFilename && <span className="truncate">{filename}</span>}
              {showPage && <span className="text-ink-500">Page {page}</span>}
              {docId && (
                <button
                  type="button"
                  onClick={openDoc}
                  className="font-medium text-brand-700 hover:text-brand-500"
                >
                  Open source document →
                </button>
              )}
            </div>
          )}
          {step.refusal?.detail && (
            <div className="mt-1 text-[11px] leading-snug text-ink-500">
              {step.refusal.detail}
            </div>
          )}
        </div>
      </div>
      {isLast && <span className="sr-only">End of chain</span>}
    </li>
  );
}

/* ───────────────────────── the drawer ───────────────────────── */

export interface LineageDrawerProps {
  open: boolean;
  dealId: string;
  /** Candidate root ids, tried in order. */
  rootId: string | string[];
  title?: string;
  onClose: () => void;
  /** Pin the walk to one run; omit for the run the worker serves. */
  runId?: string | null;
}

export function LineageDrawer({
  open,
  dealId,
  rootId,
  title,
  onClose,
  runId,
}: LineageDrawerProps) {
  const [mounted, setMounted] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const { record, loading, error, settled, walk } = useLineage(dealId, {
    runId,
    enabled: open,
  });

  useEffect(() => setMounted(true), []);

  const handleClose = useCallback(() => onClose(), [onClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        handleClose();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, handleClose]);

  const candidates = useMemo(
    () => (Array.isArray(rootId) ? rootId : [rootId]).filter(Boolean),
    [rootId],
  );
  const resolved = useMemo(() => {
    for (const id of candidates) {
      const w = walk(id);
      if (w) return w;
    }
    return null;
  }, [candidates, walk]);

  if (!open || !mounted || typeof document === 'undefined') return null;

  return createPortal(
    <div className="fixed inset-0 z-50">
      <div
        onClick={handleClose}
        aria-hidden="true"
        className="absolute inset-0 bg-ink-900/30 backdrop-blur-[1px]"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="false"
        aria-label="Value lineage"
        className="absolute top-0 right-0 flex h-full w-full flex-col border-l border-border bg-white shadow-card-hover sm:w-[420px]"
      >
        <header className="flex flex-shrink-0 items-start justify-between gap-3 border-b border-border bg-white px-5 py-3.5">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">
              Value lineage
            </div>
            <h2 className="mt-0.5 truncate text-[14px] font-semibold text-ink-900">
              {title ?? candidates[0] ?? 'Trace to source'}
            </h2>
            <p className="mt-0.5 text-[11.5px] text-ink-500">
              Every step from this number down to the page it came from.
            </p>
          </div>
          <button
            type="button"
            onClick={handleClose}
            aria-label="Close lineage"
            className="-mr-1 rounded p-1 text-ink-500 hover:bg-ink-100 hover:text-ink-700"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {record?.stale && (
            <div className="border-b border-border bg-warn-50 px-5 py-2.5 text-[11.5px] leading-snug text-warn-700">
              This run predates the latest document or override on this deal — re-run the
              model to refresh the chain.
            </div>
          )}

          {loading && !settled && (
            <div className="px-5 py-6 text-[12.5px] text-ink-500">Loading lineage…</div>
          )}

          {settled && error && (
            <div className="px-5 py-6 text-[12.5px] text-ink-500">
              Couldn’t load lineage — {error}
            </div>
          )}

          {settled && !error && !record && (
            <div className="px-5 py-6 text-[12.5px] leading-relaxed text-ink-500">
              No lineage recorded for this deal yet. Nothing else on this screen changes —
              every number still reads from the latest model run.
            </div>
          )}

          {settled && !error && record && !resolved && (
            <div className="px-5 py-6 text-[12.5px] leading-relaxed text-ink-500">
              No lineage recorded for this value yet.
            </div>
          )}

          {resolved && (
            <>
              <ol className="list-none">
                {resolved.steps.map((s, i) => (
                  <ChainStep
                    key={`${s.node.id}-${i}`}
                    step={s}
                    walk={resolved}
                    isLast={i === resolved.steps.length - 1}
                  />
                ))}
              </ol>

              {resolved.unresolved.length > 0 && (
                <div className="border-t border-border">
                  <div className="px-5 pt-3 text-[10px] font-semibold uppercase tracking-wider text-ink-500">
                    Unresolved
                  </div>
                  <ul className="list-none px-5 pb-3 pt-1.5">
                    {resolved.unresolved.map((r, i) => {
                      const meta = isReasonCode(r.code) ? REASONS[r.code] : null;
                      return (
                        <li key={`${r.code}-${i}`} className="py-1 text-[11.5px] leading-snug">
                          <span className="mr-1.5 tabular-nums text-ink-400">
                            {REFUSAL_GLYPH}
                          </span>
                          <span className="font-medium text-ink-900">
                            {meta?.label ?? r.code}
                          </span>
                          {r.detail && <span className="text-ink-500"> — {r.detail}</span>}
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>

        {record && (
          <footer className="flex-shrink-0 border-t border-border bg-surface px-5 py-2 text-[10.5px] text-ink-500">
            Run {record.run_id ?? '—'} · pipeline {record.pipeline_version} · registry v
            {record.registry_version}
          </footer>
        )}
      </div>
    </div>,
    document.body,
  );
}

/* ───────────────────────── global host ───────────────────────── */

/**
 * Globally mounted listener for `fondok:lineage-open`. Mounted once in
 * `AppShell` next to `SourceDocPane` so any surface — a hover primitive, an
 * IC Memo tile — can ask for a trace without threading props through tabs.
 * Renders nothing until something asks.
 */
export function LineageDrawerHost() {
  const [req, setReq] = useState<LineageOpenDetail | null>(null);

  useEffect(() => {
    const onOpen = (e: Event) => {
      const detail = (e as CustomEvent<LineageOpenDetail>).detail;
      if (!detail?.dealId || !detail?.rootId) return;
      setReq(detail);
    };
    window.addEventListener(LINEAGE_OPEN_EVENT, onOpen as EventListener);
    return () => window.removeEventListener(LINEAGE_OPEN_EVENT, onOpen as EventListener);
  }, []);

  if (!req) return null;
  return (
    <LineageDrawer
      open
      dealId={req.dealId}
      rootId={req.rootId}
      title={req.title}
      onClose={() => setReq(null)}
    />
  );
}

/* ───────────────────────── the affordance ───────────────────────── */

/**
 * "Trace to source" — the shared action the hover primitives render inside
 * their tooltip. Styled as the existing tooltip links (brand text link), so
 * it adds an action, not a new visual language.
 */
export function TraceToSourceAction({
  dealId,
  rootId,
  title,
  className,
}: {
  dealId: string;
  rootId: string | string[];
  title?: string;
  className?: string;
}) {
  if (!dealId) return null;
  return (
    <button
      type="button"
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        openLineage({ dealId, rootId, title });
      }}
      className={cn(
        'inline-flex items-center gap-1 text-[11px] font-medium text-brand-700 hover:text-brand-500',
        className,
      )}
    >
      Trace to source →
    </button>
  );
}

export default LineageDrawer;
