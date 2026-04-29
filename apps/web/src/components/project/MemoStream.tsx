'use client';

/**
 * MemoStream — live IC memo viewer.
 *
 * Trigger: POST {WORKER}/deals/{id}/memo/generate
 * Stream:  EventSource on /deals/{id}/memo/stream — one SSE ``section``
 *          event per Opus draft, terminated by a ``done`` event.
 *
 * If ``NEXT_PUBLIC_WORKER_URL`` is unset (preview deploys) or the
 * browser lacks ``EventSource``, we render the cached Kimpton memo
 * from ``mockData`` so the page never goes blank.
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Sparkles, Square, RefreshCw, Loader2, AlertTriangle, CheckCircle2,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { kimptonAnalysis } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { Citation as CitationChip } from '@/components/citations/Citation';
import { useDocuments } from '@/lib/hooks/useDocuments';
import type { WorkerDocument } from '@/lib/api';

const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL ?? '';

type Citation = {
  document_id?: string;
  page?: number;
  field?: string | null;
  excerpt?: string | null;
};

type Section = {
  section_id: string;
  title: string;
  body: string;
  citations: Citation[];
};

type SectionMetadata = {
  input_tokens?: number;
  output_tokens?: number;
  model?: string | null;
  section_index?: number;
  section_total?: number;
};

type StreamState =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'streaming' }
  | { kind: 'done' }
  | { kind: 'error'; message: string }
  | { kind: 'unsupported' };

// Anthropic public list-price (Opus 4.7): $15/MTok input, $75/MTok output.
// Cheap/cached input is amortized into the input bucket — close enough
// for the live header gauge. Real per-call costs are recomputed worker-side.
const OPUS_INPUT_USD_PER_MTOK = 15;
const OPUS_OUTPUT_USD_PER_MTOK = 75;

function estimateCostUsd(inputTokens: number, outputTokens: number): number {
  return (
    (inputTokens / 1_000_000) * OPUS_INPUT_USD_PER_MTOK +
    (outputTokens / 1_000_000) * OPUS_OUTPUT_USD_PER_MTOK
  );
}

const REQUIRED_SECTION_ORDER = [
  'investment_thesis',
  'market_analysis',
  'deal_overview',
  'financial_analysis',
  'risk_factors',
  'recommendation',
];

function sectionLabel(id: string): string {
  return id
    .split('_')
    .map(s => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

// Map worker-side doc_type codes to the display labels reviewers expect
// in the chip — "T-12 p.7" reads cleaner than the raw "T12 p.7".
function formatDocTypeLabel(docType: string | null | undefined): string {
  if (!docType) return 'doc';
  const t = docType.toUpperCase();
  if (t === 'T12' || t === 'PNL') return 'T-12';
  if (t === 'OM') return 'OM';
  if (t === 'STR') return 'STR';
  if (t === 'RENT_ROLL') return 'Rent Roll';
  if (t === 'MARKET_STUDY') return 'Market';
  if (t === 'CONTRACT') return 'Contract';
  return t;
}

export default function MemoStream({ dealId }: { dealId: string }) {
  const [state, setState] = useState<StreamState>({ kind: 'idle' });
  const [sections, setSections] = useState<Section[]>([]);
  const [tokens, setTokens] = useState({ input: 0, output: 0 });
  const [progress, setProgress] = useState({ done: 0, total: REQUIRED_SECTION_ORDER.length });
  const esRef = useRef<EventSource | null>(null);

  const workerConnected = WORKER_URL.length > 0;
  const hasEventSource = typeof window !== 'undefined' && typeof window.EventSource !== 'undefined';

  // Pull the deal's documents so we can label each citation with the
  // doc_type the Analyst was citing (e.g. "T-12 p.7" instead of "[1]")
  // and pass the filename through to the side pane header.
  const { documents } = useDocuments(dealId);
  const docById = useMemo(() => {
    const m = new Map<string, WorkerDocument>();
    documents.forEach((d) => m.set(d.id, d));
    return m;
  }, [documents]);

  // Disable streaming entirely if EventSource is missing or worker unset.
  useEffect(() => {
    if (!workerConnected || !hasEventSource) {
      setState({ kind: 'unsupported' });
    }
  }, [workerConnected, hasEventSource]);

  // Tear down the EventSource on unmount so we don't leak open connections
  // across route changes.
  useEffect(() => {
    return () => {
      esRef.current?.close();
      esRef.current = null;
    };
  }, []);

  const startStream = async () => {
    if (!workerConnected || !hasEventSource) {
      setState({ kind: 'unsupported' });
      return;
    }
    // Reset accumulators for a fresh run.
    setSections([]);
    setTokens({ input: 0, output: 0 });
    setProgress({ done: 0, total: REQUIRED_SECTION_ORDER.length });
    setState({ kind: 'starting' });

    try {
      const r = await fetch(`${WORKER_URL}/deals/${dealId}/memo/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      if (!r.ok) {
        setState({ kind: 'error', message: `Generate trigger failed (${r.status})` });
        return;
      }
    } catch (err) {
      setState({ kind: 'error', message: err instanceof Error ? err.message : 'network error' });
      return;
    }

    // Open the SSE stream. EventSource auto-reconnects on transient drops;
    // on terminal ``error`` from the worker we close it explicitly.
    const es = new EventSource(`${WORKER_URL}/deals/${dealId}/memo/stream`);
    esRef.current = es;
    setState({ kind: 'streaming' });

    es.addEventListener('section', (evt: MessageEvent) => {
      try {
        const payload = JSON.parse(evt.data) as { data: Section; metadata?: SectionMetadata };
        const sec = payload.data;
        const meta = payload.metadata ?? {};
        setSections(prev => {
          // De-dupe by section_id in case the worker re-emits.
          const filtered = prev.filter(s => s.section_id !== sec.section_id);
          return [...filtered, sec];
        });
        setTokens({
          input: meta.input_tokens ?? 0,
          output: meta.output_tokens ?? 0,
        });
        if (meta.section_index && meta.section_total) {
          setProgress({ done: meta.section_index, total: meta.section_total });
        }
      } catch (err) {
        console.warn('memo-stream: malformed section payload', err);
      }
    });

    es.addEventListener('done', (evt: MessageEvent) => {
      try {
        const payload = JSON.parse(evt.data) as { metadata?: SectionMetadata };
        const meta = payload.metadata ?? {};
        if (meta.input_tokens || meta.output_tokens) {
          setTokens({
            input: meta.input_tokens ?? 0,
            output: meta.output_tokens ?? 0,
          });
        }
      } catch {
        /* ignore — done is informational */
      }
      setState({ kind: 'done' });
      es.close();
      esRef.current = null;
    });

    es.addEventListener('error', () => {
      // EventSource fires error on close too — only flip to error state
      // if we haven't already reached the done terminal.
      setState(curr => (curr.kind === 'done' ? curr : { kind: 'error', message: 'Stream interrupted' }));
      es.close();
      esRef.current = null;
    });
  };

  const stopStream = () => {
    esRef.current?.close();
    esRef.current = null;
    setState({ kind: 'idle' });
  };

  const orderedSections = useMemo(() => {
    const byId = new Map(sections.map(s => [s.section_id, s]));
    return REQUIRED_SECTION_ORDER
      .map(id => byId.get(id))
      .filter((s): s is Section => Boolean(s));
  }, [sections]);

  const totalTokens = tokens.input + tokens.output;
  const costUsd = estimateCostUsd(tokens.input, tokens.output);

  if (state.kind === 'unsupported') {
    return (
      <Card className="p-5">
        <div className="flex items-center gap-2 mb-3">
          <Sparkles size={15} className="text-brand-500" />
          <h3 className="text-[14px] font-semibold text-ink-900">IC Memo</h3>
        </div>
        <div className="space-y-3 text-[12.5px] text-ink-700 leading-relaxed">
          {kimptonAnalysis.summary.map((p, i) => (
            <p key={i}>{p}</p>
          ))}
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card className="p-5">
        <div className="flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <Sparkles size={15} className="text-brand-500" />
              <h3 className="text-[14px] font-semibold text-ink-900">Investment Committee Memo</h3>
              {state.kind === 'streaming' && (
                <Badge tone="blue">
                  <Loader2 size={11} className="animate-spin" /> Real-time generation
                </Badge>
              )}
              {state.kind === 'done' && (
                <Badge tone="green">
                  <CheckCircle2 size={11} /> Complete
                </Badge>
              )}
              {state.kind === 'error' && (
                <Badge tone="amber">
                  <AlertTriangle size={11} /> {state.message}
                </Badge>
              )}
            </div>
            <p className="text-[12px] text-ink-500">
              Opus 4.7 drafts each of {progress.total} sections live. Section{' '}
              <span className="font-medium tabular-nums text-ink-900">
                {progress.done}/{progress.total}
              </span>
              {totalTokens > 0 && (
                <>
                  {' '}· <span className="tabular-nums">{totalTokens.toLocaleString()} tokens</span>
                  {' '}· <span className="tabular-nums">${costUsd.toFixed(2)}</span>
                </>
              )}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {state.kind === 'streaming' && (
              <Button variant="secondary" size="sm" onClick={stopStream}>
                <Square size={11} /> Stop
              </Button>
            )}
            {(state.kind === 'idle' || state.kind === 'done' || state.kind === 'error') && (
              <Button variant="primary" size="sm" onClick={startStream}>
                {state.kind === 'idle' ? (
                  <>
                    <Sparkles size={12} /> Generate IC Memo
                  </>
                ) : (
                  <>
                    <RefreshCw size={12} /> Regenerate
                  </>
                )}
              </Button>
            )}
          </div>
        </div>
      </Card>

      {orderedSections.map(sec => (
        <SectionCard
          key={sec.section_id}
          section={sec}
          streaming={state.kind === 'streaming'}
          docById={docById}
        />
      ))}

      {state.kind === 'streaming' && orderedSections.length < progress.total && (
        <Card className="p-5 border-l-4 border-l-amber-300">
          <div className="flex items-center gap-2 mb-2">
            <Sparkles size={13} className="text-brand-500 animate-pulse" />
            <span className="text-[13px] font-medium text-ink-900">
              Drafting {sectionLabel(REQUIRED_SECTION_ORDER[orderedSections.length] ?? 'next section')}…
            </span>
            <span className="inline-block w-[2px] h-[14px] bg-brand-500 animate-pulse" aria-hidden />
          </div>
          <p className="text-[12px] text-ink-500">
            The Analyst agent is composing this section. It will appear here as soon as the model
            finishes the draft.
          </p>
        </Card>
      )}

      {state.kind === 'done' && (
        <Card className="p-4 bg-success-50 border-success-500/30">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-[12.5px] text-ink-700">
              <CheckCircle2 size={14} className="text-success-700" />
              Memo complete · <span className="font-medium tabular-nums">{totalTokens.toLocaleString()} tokens</span>
              {' '}· <span className="font-medium tabular-nums">${costUsd.toFixed(2)}</span>
            </div>
            <Button variant="secondary" size="sm" onClick={startStream}>
              <RefreshCw size={11} /> Regenerate
            </Button>
          </div>
        </Card>
      )}

      {state.kind === 'idle' && orderedSections.length === 0 && (
        <Card className="p-8 text-center text-[12.5px] text-ink-500">
          Click <span className="font-medium text-ink-700">Generate IC Memo</span> to draft each
          section in real time. Sections appear here as soon as the model finishes them.
        </Card>
      )}
    </div>
  );
}

function SectionCard({
  section,
  streaming,
  docById,
}: {
  section: Section;
  streaming: boolean;
  docById: Map<string, WorkerDocument>;
}) {
  // Word-by-word fade-in: split the body into tokens and animate them in.
  // Keeps under-render cost bounded — sections are <=500 words.
  const words = useMemo(() => section.body.split(/(\s+)/), [section.body]);

  return (
    <Card
      className={cn(
        'p-5 transition-[border-color,background-color] duration-300',
        streaming && 'border-l-4 border-l-amber-300',
      )}
    >
      <div className="flex items-center gap-2 mb-3">
        <Sparkles size={13} className="text-brand-500" />
        <h4 className="text-[13.5px] font-semibold text-ink-900">{section.title}</h4>
        <span className="text-[10.5px] uppercase tracking-wider text-ink-500">
          {section.section_id.replace(/_/g, ' ')}
        </span>
      </div>

      <div className="text-[12.5px] text-ink-700 leading-relaxed whitespace-pre-wrap">
        {words.map((w, i) =>
          /\s/.test(w) ? (
            w
          ) : (
            <span
              key={i}
              className="inline-block animate-[fadeIn_240ms_ease-out_both]"
              style={{ animationDelay: `${Math.min(i * 4, 400)}ms` }}
            >
              {w}
            </span>
          ),
        )}
        {section.citations.length > 0 && (
          // Each chip dispatches `fondok:citation-focus` so the global
          // SourceDocPane slides in to the cited page. Label format is
          // "T-12 p.7" when we can resolve the citation's document_id
          // back to a real upload; otherwise we fall back to a numeric
          // index so fixture/synthetic citations still render.
          <span className="ml-1 inline-flex flex-wrap items-baseline gap-0.5 align-super">
            {section.citations.map((c, i) => {
              const docId = c.document_id ?? '';
              const page = c.page ?? 1;
              const matched = docId ? docById.get(docId) : undefined;
              const label = matched
                ? `${formatDocTypeLabel(matched.doc_type)} p.${page}`
                : `[${i + 1}]`;
              return (
                <CitationChip
                  key={`${docId || 'unknown'}:${page}:${i}`}
                  data={{
                    documentId: docId,
                    documentName: matched?.filename,
                    page,
                    field: c.field ?? undefined,
                    excerpt: c.excerpt ?? undefined,
                  }}
                  label={label}
                />
              );
            })}
          </span>
        )}
      </div>

      <style jsx>{`
        @keyframes fadeIn {
          from {
            opacity: 0;
            transform: translateY(2px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </Card>
  );
}
