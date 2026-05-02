'use client';

/**
 * AskDeal — grounded Q&A on a single deal.
 *
 * Sends the user's question to ``POST /deals/{id}/ask``; the worker
 * composes the deal's full Context Data Product (dossier) and hands
 * it to the Researcher agent (Opus 4.7) which returns a single
 * grounded answer with citations back to the source PDF pages.
 *
 * Citations reuse the ``Citation`` chip component so clicking opens
 * the existing SourceDocPane at the cited page — same affordance as
 * the IC memo.
 */

import { useState } from 'react';
import { Sparkles, Send, Loader2, AlertTriangle, MessageSquare } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { api, AskAnswerResult, isWorkerConnected } from '@/lib/api';
import { Citation as CitationChip } from '@/components/citations/Citation';

type AskState =
  | { kind: 'idle' }
  | { kind: 'asking' }
  | { kind: 'answered'; result: AskAnswerResult }
  | { kind: 'error'; message: string };

const SUGGESTIONS = [
  'What is the broker NOI vs the T-12 actual NOI on this deal?',
  'Which expense lines were lifted from the T-12 vs synthesized at USALI ratios?',
  'What is the Year-1 occupancy and how does it compare to the OM projection?',
  'Summarize the variance flags by NOI impact.',
];

export default function AskDeal({ dealId }: { dealId: string }) {
  const [question, setQuestion] = useState('');
  const [state, setState] = useState<AskState>({ kind: 'idle' });
  const [history, setHistory] = useState<AskAnswerResult[]>([]);

  const liveDeal = isWorkerConnected() && /^[0-9a-f-]{36}$/i.test(dealId);

  const submit = async () => {
    const q = question.trim();
    if (!q || state.kind === 'asking') return;
    if (!liveDeal) {
      setState({ kind: 'error', message: 'Q&A is only available on real (non-demo) deals once the worker is connected.' });
      return;
    }
    setState({ kind: 'asking' });
    try {
      const result = await api.dossier.ask(dealId, q);
      setState({ kind: 'answered', result });
      setHistory((prev) => [result, ...prev].slice(0, 8));
      setQuestion('');
    } catch (err) {
      setState({
        kind: 'error',
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <div className="space-y-4">
      <Card className="p-5 border-l-4 border-l-brand-500">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
            <MessageSquare size={16} className="text-brand-700" />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-2 mb-1.5">
              <h3 className="text-[14px] font-semibold text-ink-900">
                Ask About This Deal
              </h3>
              <Badge tone="blue">Beta</Badge>
            </div>
            <p className="text-[12.5px] text-ink-700 leading-relaxed mb-3">
              Ask any question about this deal in plain English. The Researcher agent
              answers from the deal&apos;s extracted documents, engine outputs, and
              variance flags — every claim cites the source page so reviewers can
              click through and verify.
            </p>

            <div className="flex flex-wrap gap-1.5 mb-3">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => setQuestion(s)}
                  className="text-[11px] px-2 py-1 rounded border border-border bg-bg/40 hover:bg-brand-50 hover:border-brand-500/40 text-ink-700"
                >
                  {s}
                </button>
              ))}
            </div>

            <div className="flex items-end gap-2">
              <textarea
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submit();
                }}
                placeholder="What's the broker's stabilized NOI vs my T-12 actuals?"
                rows={2}
                className="flex-1 text-[12.5px] px-3 py-2 border border-border rounded-md resize-none focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
              <Button
                variant="primary"
                size="sm"
                onClick={submit}
                disabled={!question.trim() || state.kind === 'asking'}
              >
                {state.kind === 'asking' ? (
                  <>
                    <Loader2 size={12} className="animate-spin" /> Thinking…
                  </>
                ) : (
                  <>
                    <Send size={12} /> Ask
                  </>
                )}
              </Button>
            </div>

            {state.kind === 'error' && (
              <div className="mt-3 inline-flex items-center gap-2 px-3 py-1.5 rounded bg-amber-50 text-amber-900 text-[11.5px] border border-amber-200">
                <AlertTriangle size={12} />
                {state.message}
              </div>
            )}
          </div>
        </div>
      </Card>

      {history.length > 0 && (
        <div className="space-y-3">
          {history.map((r, idx) => (
            <Card key={`${r.deal_id}:${idx}:${r.question}`} className="p-5">
              <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold mb-1">
                Question
              </div>
              <div className="text-[13px] font-medium text-ink-900 mb-3">
                {r.question}
              </div>
              <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold mb-1 flex items-center gap-2">
                <Sparkles size={11} className="text-brand-500" />
                Answer
                <Badge tone={r.confidence >= 0.8 ? 'green' : r.confidence >= 0.5 ? 'amber' : 'gray'}>
                  {(r.confidence * 100).toFixed(0)}% confidence
                </Badge>
              </div>
              <div className="text-[12.5px] text-ink-700 leading-relaxed whitespace-pre-wrap">
                {r.answer || (
                  <span className="text-ink-500 italic">
                    {r.note ?? 'No answer returned.'}
                  </span>
                )}
              </div>
              {r.citations.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1.5 items-baseline">
                  <span className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold">
                    Citations:
                  </span>
                  {r.citations.map((c, i) => (
                    <CitationChip
                      key={`${c.document_id ?? 'unknown'}:${c.page ?? 0}:${i}`}
                      data={{
                        documentId: c.document_id ?? '',
                        page: c.page ?? 1,
                        field: c.field ?? undefined,
                        excerpt: c.excerpt ?? undefined,
                      }}
                      label={`p.${c.page ?? '—'}`}
                    />
                  ))}
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
