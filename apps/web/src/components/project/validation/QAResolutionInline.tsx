'use client';

/**
 * QAResolutionInline — the Q&A re-ingestion surface (Wave 1 #5).
 *
 * Mounts inside an open broker-question row when the question's state
 * is ``sent``. The analyst pastes the broker's emailed reply, hits a
 * single button, watches a ~5s loading state, then sees:
 *
 *   - A verdict badge (green Resolved / amber Partial / red Still concerning)
 *   - The resolver's 1-sentence plain-English summary
 *   - A ProposedOverridesList (checkbox per row + apply CTA)
 *
 * After apply: the surface collapses to a single-line confirmation
 * ("✓ Resolved · 2 overrides applied") with an expandable details
 * pane the analyst can pop back open from the broker-questions panel.
 *
 * UX patterns (Wave 1 no-modals doctrine):
 *   - Inline expand-in-place. Never a modal, popover, or side panel.
 *   - ``fade-in-up`` animation + ``motion-reduce:animate-none`` respect.
 *   - Single ESC binding lives on the parent panel — no nested handlers.
 *   - Cost-feedback path: 402 from the API surfaces as a clear toast.
 *
 * State machine (local to this component):
 *
 *   idle    — empty textarea, waiting on paste.
 *   typing  — textarea has content; CTA enabled.
 *   loading — fetch in flight; spinner + "Reading broker reply…" copy.
 *   error   — fetch failed; inline retry surface.
 *   review  — agent returned; render verdict + summary + overrides list.
 *   done    — analyst applied or skipped; collapsed confirmation.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Sparkles,
  XCircle,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  type BrokerQAPair,
  type BrokerQAVerdict,
  type BrokerQuestion,
} from '@/lib/api';
import { cn } from '@/lib/format';
import { ProposedOverridesList } from './ProposedOverridesList';

type Phase = 'idle' | 'loading' | 'error' | 'review' | 'done';

const VERDICT_META: Record<
  BrokerQAVerdict,
  {
    label: string;
    tone: 'green' | 'amber' | 'red';
    classes: string;
    Icon: typeof CheckCircle2;
  }
> = {
  resolved: {
    label: 'Resolved',
    tone: 'green',
    classes:
      'bg-success-50 text-success-700 border-success-500/30',
    Icon: CheckCircle2,
  },
  partially_resolved: {
    label: 'Partially resolved',
    tone: 'amber',
    classes: 'bg-warn-50 text-warn-700 border-warn-500/30',
    Icon: AlertTriangle,
  },
  still_concerning: {
    label: 'Still concerning',
    tone: 'red',
    classes:
      'bg-danger-50 text-danger-700 border-danger-500/30',
    Icon: XCircle,
  },
};

export function QAResolutionInline({
  dealId,
  question,
  existing,
  onResolved,
  onApplied,
}: {
  dealId: string;
  question: BrokerQuestion;
  /** When the panel already loaded a QA pair for this question, render
   *  the resolver verdict + overrides surface immediately (skips the
   *  textarea). */
  existing: BrokerQAPair | null;
  /** Bubble new + updated QA pair rows up so the parent panel can keep
   *  its local cache in sync without an extra round-trip. */
  onResolved?: (pair: BrokerQAPair) => void;
  onApplied?: (pair: BrokerQAPair) => void;
}) {
  const { toast } = useToast();
  const [phase, setPhase] = useState<Phase>(() => {
    if (existing && existing.applied_overrides !== null) return 'done';
    if (existing) return 'review';
    return 'idle';
  });
  const [pair, setPair] = useState<BrokerQAPair | null>(existing);
  const [text, setText] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [showDetails, setShowDetails] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Re-sync local state when the parent loads a different QA pair (e.g.
  // user clicks a different question row in quick succession).
  useEffect(() => {
    setPair(existing);
    if (existing && existing.applied_overrides !== null) setPhase('done');
    else if (existing) setPhase('review');
    else setPhase('idle');
    setText('');
    setError(null);
    setShowDetails(false);
  }, [existing]);

  useEffect(() => {
    if (phase === 'idle') {
      const t = setTimeout(() => textareaRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [phase]);

  const submit = useCallback(async () => {
    const body = text.trim();
    if (!body) return;
    setPhase('loading');
    setError(null);
    try {
      const created = await api.validation.brokerQA.submit(dealId, {
        broker_question_id: question.id,
        broker_response: body,
      });
      setPair(created);
      setPhase('review');
      onResolved?.(created);
      toast('Resolver finished — review proposed overrides.', {
        type: 'success',
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(msg);
      setPhase('error');
      // Distinguished surface for the per-deal budget cap.
      const isBudget = /budget|402|payment required/i.test(msg);
      toast(
        isBudget
          ? 'QA budget exhausted on this deal. Raise the budget in Settings or contact admin.'
          : `Resolver failed: ${msg}`,
        { type: 'error' },
      );
    }
  }, [dealId, question.id, text, onResolved, toast]);

  const applyOverrides = useCallback(
    async (indexes: number[]) => {
      if (!pair) return;
      try {
        const updated = await api.validation.brokerQA.apply(dealId, pair.id, {
          override_indexes_to_apply: indexes,
        });
        setPair(updated);
        setPhase('done');
        onApplied?.(updated);
        toast(
          indexes.length === 0
            ? 'No overrides applied.'
            : `Applied ${indexes.length} override${indexes.length === 1 ? '' : 's'} — run the model to recompute.`,
          { type: 'success' },
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast(`Apply failed: ${msg}`, { type: 'error' });
      }
    },
    [dealId, pair, onApplied, toast],
  );

  // ────────────────────── phases ──────────────────────

  if (phase === 'idle') {
    return (
      <div
        className="mt-3 p-3 rounded-md border border-brand-500/30 bg-brand-50/30 fade-in-up motion-reduce:animate-none"
        role="group"
        aria-label="Paste the broker's emailed reply"
      >
        <div className="flex items-center gap-1.5 mb-1.5">
          <Sparkles
            size={12}
            className="text-brand-700"
            aria-hidden="true"
          />
          <label
            htmlFor="qa-broker-reply"
            className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold"
          >
            Broker reply
          </label>
        </div>
        <textarea
          id="qa-broker-reply"
          ref={textareaRef}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={
            "Paste the email excerpt the broker sent back.\n\n" +
            "Example: 'F&B contract reset Nov-24; running in-house since with leaner labor. Expect FB margin to recover to the pre-closure 18% baseline.'"
          }
          rows={4}
          maxLength={8000}
          className="w-full px-2.5 py-2 rounded-md border border-border bg-white text-[12.5px] leading-relaxed text-ink-900 placeholder:text-ink-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:border-brand-500 resize-y"
          aria-describedby="qa-broker-reply-hint"
        />
        <div className="mt-2 flex items-center justify-between gap-2">
          <span
            id="qa-broker-reply-hint"
            className="text-[11px] text-ink-500 tabular-nums"
          >
            {text.length}/8000
          </span>
          <Button
            size="sm"
            variant="primary"
            onClick={() => void submit()}
            disabled={!text.trim()}
            aria-label="Run QA resolver on the broker's reply"
          >
            <Sparkles size={11} aria-hidden="true" />
            Paste broker reply
          </Button>
        </div>
      </div>
    );
  }

  if (phase === 'loading') {
    return (
      <div
        className="mt-3 p-3 rounded-md border border-brand-500/30 bg-brand-50/30 fade-in-up motion-reduce:animate-none"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-center gap-2 text-[12.5px] text-brand-700 font-medium">
          <span
            className="inline-block w-3 h-3 border-2 border-brand-500/30 border-t-brand-700 rounded-full animate-spin motion-reduce:animate-none"
            aria-hidden="true"
          />
          Reading broker reply…
        </div>
        <p className="text-[11.5px] text-ink-500 mt-1 leading-relaxed">
          The QA resolver is comparing the reply against the variance
          snapshot and the deal's current assumptions. ~5s.
        </p>
      </div>
    );
  }

  if (phase === 'error') {
    return (
      <div
        className="mt-3 p-3 rounded-md border border-danger-500/30 bg-danger-50/50 fade-in-up motion-reduce:animate-none"
        role="alert"
      >
        <div className="flex items-center gap-2 text-[12.5px] text-danger-700 font-medium">
          <AlertTriangle size={12} aria-hidden="true" />
          Resolver failed
        </div>
        <p className="text-[11.5px] text-ink-700 mt-1 leading-relaxed">
          {error || 'Unknown error.'}
        </p>
        <div className="mt-2 flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setPhase('idle')}
          >
            Try again
          </Button>
        </div>
      </div>
    );
  }

  if (phase === 'review' && pair && pair.resolver_verdict) {
    const meta = VERDICT_META[pair.resolver_verdict];
    const Icon = meta.Icon;
    return (
      <div
        className="mt-3 fade-in-up motion-reduce:animate-none"
        role="group"
        aria-label="QA resolver result"
      >
        <div
          className={cn(
            'p-3 rounded-md border flex items-start gap-2',
            meta.classes,
          )}
        >
          <Icon size={14} className="flex-shrink-0 mt-0.5" aria-hidden="true" />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-semibold text-[12.5px] uppercase tracking-wider">
                {meta.label}
              </span>
            </div>
            {pair.resolver_summary && (
              <p className="text-[12.5px] text-ink-900 mt-1 leading-relaxed">
                {pair.resolver_summary}
              </p>
            )}
          </div>
        </div>

        <ProposedOverridesList
          overrides={pair.proposed_overrides}
          onApply={applyOverrides}
          onSkipAll={() => applyOverrides([])}
        />
      </div>
    );
  }

  // phase === 'done'
  if (pair) {
    const meta = pair.resolver_verdict
      ? VERDICT_META[pair.resolver_verdict]
      : VERDICT_META.resolved;
    const Icon = meta.Icon;
    const appliedCount = pair.applied_overrides?.length ?? 0;
    return (
      <div
        className="mt-3 fade-in-up motion-reduce:animate-none"
        role="group"
        aria-label="QA resolver outcome"
      >
        <div
          className={cn(
            'flex items-center justify-between gap-2 px-3 py-2 rounded-md border',
            meta.classes,
          )}
        >
          <div className="flex items-center gap-2 min-w-0">
            <Icon size={13} className="flex-shrink-0" aria-hidden="true" />
            <span className="text-[12.5px] font-semibold">{meta.label}</span>
            <span className="text-ink-400" aria-hidden="true">
              ·
            </span>
            <span className="text-[12px] text-ink-700">
              {appliedCount === 0
                ? 'no overrides applied'
                : `${appliedCount} override${appliedCount === 1 ? '' : 's'} applied`}
            </span>
          </div>
          <button
            type="button"
            onClick={() => setShowDetails((s) => !s)}
            className="inline-flex items-center gap-1 text-[11.5px] font-medium text-ink-700 hover:text-ink-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded px-1"
            aria-expanded={showDetails}
            aria-label={showDetails ? 'Hide resolver details' : 'Show resolver details'}
          >
            {showDetails ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
            Details
          </button>
        </div>

        {showDetails && (
          <div className="mt-2 p-3 rounded-md border border-ink-200 bg-white text-[12.5px] text-ink-700 leading-relaxed fade-in-up motion-reduce:animate-none">
            {pair.resolver_summary && (
              <p className="mb-2">{pair.resolver_summary}</p>
            )}
            {pair.applied_overrides && pair.applied_overrides.length > 0 && (
              <ul className="space-y-1.5">
                {pair.applied_overrides.map((o, idx) => (
                  <li
                    key={`${o.field_path}-${idx}`}
                    className="flex items-start gap-2"
                  >
                    <CheckCircle2
                      size={11}
                      className="text-success-700 mt-0.5 flex-shrink-0"
                      aria-hidden="true"
                    />
                    <span className="min-w-0">
                      <code className="font-mono text-[11.5px] text-ink-900">
                        {o.field_path}
                      </code>{' '}
                      <span className="text-ink-500">—</span>{' '}
                      <span className="text-ink-700">{o.rationale}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {pair.audit_note && (
              <div className="mt-2 pt-2 border-t border-ink-100 text-[11.5px] text-ink-500 italic">
                IC memo footnote: {pair.audit_note}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }

  return null;
}
