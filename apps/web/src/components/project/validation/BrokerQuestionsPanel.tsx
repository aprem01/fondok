'use client';

/**
 * Broker Questions panel (ROADMAP #4, Feature C).
 *
 * The marquee surface on the Validation tab — Sam intends to show this
 * to Brookfield / KSL on pilot demos. Each row is a year-over-year
 * variance Fondok detected on the deal's historical P&Ls, expressed as
 * a broker-ready question with the supporting numbers attached.
 *
 * State machine (server-enforced):
 *   pending → sent | dismissed
 *   sent    → answered
 *   dismissed / answered are terminal.
 *
 * UX details:
 *   - Top: Refresh + last-refreshed timestamp + total open count.
 *   - Filter pills: All / Pending / Sent / Answered / Dismissed.
 *   - Row: left severity stripe, line item + period, broker-ready
 *     question text in display serif, prior → current chip, kebab menu.
 *   - Inline modals on Sent / Answered / Dismissed transitions —
 *     Answered + Dismissed require a justification textarea.
 *   - Answered rows show the broker's response collapsed below the
 *     question text, expandable.
 *   - Empty state per filter.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  AlertCircle,
  Info,
  RefreshCw,
  MessageSquare,
  Send,
  CheckCheck,
  XCircle,
  ChevronDown,
  ChevronUp,
  ClipboardList,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import Modal from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  isWorkerConnected,
  BrokerQuestion,
  BrokerQuestionState,
  BrokerQuestionSeverity,
} from '@/lib/api';
import { cn } from '@/lib/format';

type FilterKey = 'all' | BrokerQuestionState;

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: 'pending', label: 'Pending' },
  { key: 'sent', label: 'Sent' },
  { key: 'answered', label: 'Answered' },
  { key: 'dismissed', label: 'Dismissed' },
  { key: 'all', label: 'All' },
];

const SEV_META: Record<
  BrokerQuestionSeverity,
  {
    Icon: typeof AlertTriangle;
    badgeTone: 'red' | 'amber' | 'blue';
    stripe: string;
    iconColor: string;
    label: string;
  }
> = {
  CRITICAL: {
    Icon: AlertTriangle,
    badgeTone: 'red',
    stripe: 'bg-danger-500',
    iconColor: 'text-danger-700',
    label: 'Critical',
  },
  WARN: {
    Icon: AlertCircle,
    badgeTone: 'amber',
    stripe: 'bg-warn-500',
    iconColor: 'text-warn-700',
    label: 'Warn',
  },
  INFO: {
    Icon: Info,
    badgeTone: 'blue',
    stripe: 'bg-brand-500',
    iconColor: 'text-brand-700',
    label: 'Info',
  },
};

const STATE_META: Record<
  BrokerQuestionState,
  { label: string; tone: 'gray' | 'blue' | 'green' | 'amber' | 'red' }
> = {
  pending: { label: 'Pending', tone: 'amber' },
  sent: { label: 'Sent', tone: 'blue' },
  answered: { label: 'Answered', tone: 'green' },
  dismissed: { label: 'Dismissed', tone: 'gray' },
};

function formatPeriod(key: string): string {
  // Backend stores keys like "2024_vs_2025" — render as "2024 → 2025".
  const m = key.match(/(\d{4})[_\s-]+(?:vs[_\s-]+)?(\d{4})/i);
  if (m) return `${m[1]} → ${m[2]}`;
  // Also handle a single year (e.g. "2024_monthly_swing") gracefully.
  const single = key.match(/^(\d{4})/);
  if (single) return single[1];
  return key;
}

function formatPct(p: number): string {
  // Backend emits variance_pct as a decimal (0.18 → 18%) per
  // ``detect_yoy_variances``. Show 1 decimal, signed.
  const sign = p > 0 ? '+' : p < 0 ? '' : '';
  return `${sign}${(p * 100).toFixed(1)}%`;
}

function formatValue(v: number | null): string {
  if (v == null || Number.isNaN(v)) return '—';
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(2)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(1)}K`;
  if (Math.abs(v) < 1) return `${(v * 100).toFixed(1)}%`;
  return v.toLocaleString('en-US', { maximumFractionDigits: 0 });
}

function timeAgo(iso: string | null): string {
  if (!iso) return '—';
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return '—';
  const secs = Math.floor((Date.now() - t) / 1000);
  if (secs < 30) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

function isLiveDealId(id: string): boolean {
  return isWorkerConnected() && !!id && !/^\d+$/.test(id);
}

interface PanelState {
  loading: boolean;
  rows: BrokerQuestion[];
  error: string | null;
  lastRefreshedAt: string | null;
}

type ActionKind = 'sent' | 'answered' | 'dismissed';

interface PendingAction {
  question: BrokerQuestion;
  kind: ActionKind;
}

export function BrokerQuestionsPanel({ dealId }: { dealId: string }) {
  const { toast } = useToast();
  const [state, setState] = useState<PanelState>({
    loading: true,
    rows: [],
    error: null,
    lastRefreshedAt: null,
  });
  const [filter, setFilter] = useState<FilterKey>('pending');
  const [refreshing, setRefreshing] = useState(false);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [expandedAnswers, setExpandedAnswers] = useState<Set<string>>(new Set());
  const liveDeal = isLiveDealId(dealId);

  const fetchAll = useCallback(
    (opts: { showLoading?: boolean } = {}) => {
      if (!liveDeal) {
        setState({
          loading: false,
          rows: [],
          error: null,
          lastRefreshedAt: null,
        });
        return;
      }
      const ctrl = new AbortController();
      if (opts.showLoading !== false) {
        setState((s) => ({ ...s, loading: true, error: null }));
      }
      // No ``state`` filter so the panel can pivot client-side without
      // re-fetching every time the analyst flips a pill.
      api.validation.brokerQuestions
        .list(dealId, undefined, ctrl.signal)
        .then((rows) =>
          setState({
            loading: false,
            rows,
            error: null,
            lastRefreshedAt: new Date().toISOString(),
          }),
        )
        .catch((err: unknown) => {
          if ((err as { name?: string })?.name === 'AbortError') return;
          const msg = err instanceof Error ? err.message : String(err);
          setState((s) => ({ ...s, loading: false, error: msg }));
        });
      return () => ctrl.abort();
    },
    [dealId, liveDeal],
  );

  useEffect(() => {
    fetchAll();
  }, [fetchAll]);

  const onRefresh = useCallback(async () => {
    if (!liveDeal || refreshing) return;
    setRefreshing(true);
    try {
      const rows = await api.validation.brokerQuestions.refresh(dealId);
      setState({
        loading: false,
        rows,
        error: null,
        lastRefreshedAt: new Date().toISOString(),
      });
      toast(
        `Refreshed — ${rows.length} broker question${rows.length === 1 ? '' : 's'} after re-running detection.`,
        { type: 'success' },
      );
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Refresh failed: ${msg}`, { type: 'error' });
    } finally {
      setRefreshing(false);
    }
  }, [dealId, liveDeal, refreshing, toast]);

  const onSubmitAction = useCallback(
    async (action: PendingAction, text: string) => {
      const body =
        action.kind === 'sent'
          ? { next_state: 'sent' as const }
          : action.kind === 'answered'
            ? { next_state: 'answered' as const, broker_response: text }
            : { next_state: 'dismissed' as const, dismissal_reason: text };
      try {
        const updated = await api.validation.brokerQuestions.patch(
          dealId,
          action.question.id,
          body,
        );
        setState((s) => ({
          ...s,
          rows: s.rows.map((r) => (r.id === updated.id ? updated : r)),
        }));
        setPendingAction(null);
        toast(
          action.kind === 'sent'
            ? 'Marked as sent — awaiting broker response.'
            : action.kind === 'answered'
              ? 'Answer recorded — question moves to Answered.'
              : 'Question dismissed.',
          { type: 'success' },
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast(`Update failed: ${msg}`, { type: 'error' });
      }
    },
    [dealId, toast],
  );

  const onCopyQuestion = useCallback(
    (q: BrokerQuestion) => {
      void navigator.clipboard
        .writeText(q.question_text)
        .then(() => toast('Question copied to clipboard.', { type: 'success' }))
        .catch(() => toast('Could not copy.', { type: 'error' }));
    },
    [toast],
  );

  const toggleAnswer = useCallback((id: string) => {
    setExpandedAnswers((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const filteredRows = useMemo(() => {
    if (filter === 'all') return state.rows;
    return state.rows.filter((r) => r.state === filter);
  }, [state.rows, filter]);

  const counts = useMemo(() => {
    const c = { all: state.rows.length, pending: 0, sent: 0, answered: 0, dismissed: 0 };
    state.rows.forEach((r) => {
      c[r.state] += 1;
    });
    return c;
  }, [state.rows]);

  if (!liveDeal) {
    return (
      <Card className="p-5">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
            <MessageSquare size={16} className="text-brand-700" aria-hidden="true" />
          </div>
          <div className="flex-1">
            <h3 className="text-[14px] font-semibold text-ink-900">
              Broker Questions
            </h3>
            <p className="text-[12.5px] text-ink-500 mt-1 leading-relaxed">
              Live broker-question detection runs on real deals once the
              worker is connected. Create a new project to populate this
              surface.
            </p>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3 mb-4">
        <div className="flex items-start gap-3 min-w-0">
          <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
            <MessageSquare size={16} className="text-brand-700" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h3 className="text-[15px] font-semibold text-ink-900">
                Broker Questions
              </h3>
              <Badge tone="amber">
                <span className="tabular-nums font-semibold">{counts.pending}</span>
                <span className="ml-1">open</span>
              </Badge>
            </div>
            <p className="text-[12px] text-ink-500 mt-0.5 leading-relaxed">
              Year-over-year variances Fondok flagged on the deal's
              historical P&Ls. Each question is broker-ready — copy or
              mark sent and Fondok tracks the response.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span
            className="text-[11px] text-ink-500 tabular-nums hidden md:inline"
            title={state.lastRefreshedAt ?? ''}
          >
            {state.lastRefreshedAt
              ? `Refreshed ${timeAgo(state.lastRefreshedAt)}`
              : 'Not refreshed yet'}
          </span>
          <Button
            size="sm"
            variant="secondary"
            onClick={onRefresh}
            loading={refreshing}
            disabled={refreshing}
            aria-label="Refresh broker question detection"
          >
            <RefreshCw size={12} aria-hidden="true" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filter pills */}
      <div
        role="tablist"
        aria-label="Filter broker questions by state"
        className="flex items-center gap-1.5 mb-4 flex-wrap"
      >
        {FILTERS.map((f) => {
          const active = filter === f.key;
          const n = counts[f.key];
          return (
            <button
              key={f.key}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setFilter(f.key)}
              className={cn(
                'inline-flex items-center gap-1.5 px-3 py-1 rounded-md border text-[12px] transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                active
                  ? 'bg-brand-50 text-brand-700 border-brand-500/40 font-semibold'
                  : 'bg-white text-ink-700 border-border hover:bg-ink-100',
              )}
            >
              <span>{f.label}</span>
              <span className="text-[11px] tabular-nums opacity-80">{n}</span>
            </button>
          );
        })}
      </div>

      {/* Rows */}
      {state.loading ? (
        <div className="space-y-2" aria-busy="true" aria-label="Loading broker questions">
          {[0, 1, 2].map((i) => (
            <div
              key={i}
              className="h-24 rounded-md bg-ink-100 animate-pulse"
              aria-hidden="true"
            />
          ))}
        </div>
      ) : state.error ? (
        <div className="flex items-center justify-between gap-3 px-3 py-3 rounded-md bg-danger-50 border border-danger-500/30">
          <div className="flex items-center gap-2 text-[12.5px] text-danger-700">
            <AlertTriangle size={14} aria-hidden="true" />
            <span>Couldn't load broker questions — {state.error}</span>
          </div>
          <Button size="sm" variant="secondary" onClick={() => fetchAll()}>
            <RefreshCw size={12} aria-hidden="true" /> Try again
          </Button>
        </div>
      ) : filteredRows.length === 0 ? (
        <EmptyState filter={filter} hasAny={state.rows.length > 0} />
      ) : (
        <div className="space-y-2.5">
          {filteredRows.map((q) => (
            <QuestionRow
              key={q.id}
              question={q}
              expanded={expandedAnswers.has(q.id)}
              onToggleAnswer={() => toggleAnswer(q.id)}
              onCopyQuestion={() => onCopyQuestion(q)}
              onMarkSent={() => setPendingAction({ question: q, kind: 'sent' })}
              onMarkAnswered={() => setPendingAction({ question: q, kind: 'answered' })}
              onDismiss={() => setPendingAction({ question: q, kind: 'dismissed' })}
            />
          ))}
        </div>
      )}

      <ActionModal
        action={pendingAction}
        onClose={() => setPendingAction(null)}
        onSubmit={onSubmitAction}
      />
    </Card>
  );
}

function QuestionRow({
  question,
  expanded,
  onToggleAnswer,
  onCopyQuestion,
  onMarkSent,
  onMarkAnswered,
  onDismiss,
}: {
  question: BrokerQuestion;
  expanded: boolean;
  onToggleAnswer: () => void;
  onCopyQuestion: () => void;
  onMarkSent: () => void;
  onMarkAnswered: () => void;
  onDismiss: () => void;
}) {
  const sev = SEV_META[question.severity] ?? SEV_META.INFO;
  const stateMeta = STATE_META[question.state];
  const period = formatPeriod(question.period_key);
  const variancePct = formatPct(question.variance_pct);
  const thresholdPct = formatPct(question.threshold_pct);
  const isTerminal = question.state === 'answered' || question.state === 'dismissed';

  // State-aware kebab options — never expose illegal transitions.
  const kebabItems: { label: string; onSelect?: () => void; danger?: boolean }[] = [
    { label: 'Copy question', onSelect: onCopyQuestion },
  ];
  if (question.state === 'pending') {
    kebabItems.push(
      { label: 'Mark Sent', onSelect: onMarkSent },
      { label: 'Dismiss', onSelect: onDismiss, danger: true },
    );
  } else if (question.state === 'sent') {
    kebabItems.push({ label: 'Mark Answered', onSelect: onMarkAnswered });
  }

  return (
    <div
      className={cn(
        'relative flex gap-3 bg-white border border-border rounded-lg overflow-hidden',
        isTerminal && 'opacity-90',
      )}
    >
      {/* 4px severity stripe */}
      <div
        className={cn('w-1 self-stretch flex-shrink-0', sev.stripe)}
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0 p-3.5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-[14px] font-semibold text-ink-900">
                {question.line_item}
              </span>
              <span className="text-[11.5px] text-ink-500 tabular-nums">
                {period}
              </span>
              <Badge tone={sev.badgeTone} dot uppercase>
                {sev.label}
              </Badge>
              <Badge tone={stateMeta.tone} uppercase>
                {stateMeta.label}
              </Badge>
            </div>
            <p className="font-serif text-[14px] text-ink-900 mt-2 leading-relaxed">
              {question.question_text}
            </p>
            <div className="mt-2.5 flex items-center gap-2 flex-wrap text-[11px] tabular-nums">
              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-ink-100 text-ink-700 border border-ink-200">
                <span className="text-ink-500">Prior</span>
                <span className="font-semibold text-ink-900">
                  {formatValue(question.actual_prior)}
                </span>
                <span className="text-ink-400" aria-hidden="true">→</span>
                <span className="text-ink-500">Current</span>
                <span className="font-semibold text-ink-900">
                  {formatValue(question.actual_current)}
                </span>
              </span>
              <span
                className={cn(
                  'inline-flex items-center gap-1 px-2 py-0.5 rounded-md border font-semibold',
                  question.variance_pct >= 0
                    ? 'bg-danger-50 text-danger-700 border-danger-500/25'
                    : 'bg-brand-50 text-brand-700 border-brand-500/25',
                )}
              >
                {variancePct}
              </span>
              <span className="inline-flex items-center gap-1 text-ink-500">
                <span>threshold</span>
                <span className="font-medium text-ink-700">{thresholdPct}</span>
              </span>
              <span className="text-ink-300" aria-hidden="true">·</span>
              <span className="text-ink-500" title={question.created_at}>
                Detected {timeAgo(question.created_at)}
              </span>
            </div>

            {/* Inline answered response (collapsed by default) */}
            {question.state === 'answered' && question.broker_response && (
              <div className="mt-3">
                <button
                  type="button"
                  onClick={onToggleAnswer}
                  className="inline-flex items-center gap-1 text-[11.5px] font-medium text-success-700 hover:text-success-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded px-1"
                  aria-expanded={expanded}
                  aria-label={expanded ? 'Collapse broker response' : 'Expand broker response'}
                >
                  {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
                  Broker response
                </button>
                {expanded && (
                  <div
                    className="mt-1.5 p-2.5 rounded-md bg-success-50/60 border border-success-500/25 text-[12.5px] text-ink-700 leading-relaxed whitespace-pre-wrap fade-in-up"
                  >
                    {question.broker_response}
                  </div>
                )}
              </div>
            )}

            {/* Dismissal reason (always visible — it's why we closed the row) */}
            {question.state === 'dismissed' && question.dismissal_reason && (
              <div className="mt-3 p-2.5 rounded-md bg-ink-100 border border-ink-200 text-[12px] text-ink-700 leading-relaxed">
                <span className="font-semibold text-ink-900 mr-1">Dismissed:</span>
                {question.dismissal_reason}
              </div>
            )}
          </div>

          {/* Action menu */}
          <div className="flex items-start gap-1.5 flex-shrink-0">
            {question.state === 'pending' && (
              <Button
                size="sm"
                variant="primary"
                onClick={onMarkSent}
                aria-label="Mark this question sent to the broker"
              >
                <Send size={11} aria-hidden="true" />
                Mark Sent
              </Button>
            )}
            {question.state === 'sent' && (
              <Button
                size="sm"
                variant="primary"
                onClick={onMarkAnswered}
                aria-label="Record the broker's answer"
              >
                <CheckCheck size={11} aria-hidden="true" />
                Record Answer
              </Button>
            )}
            <KebabMenu items={kebabItems} />
          </div>
        </div>
      </div>
    </div>
  );
}

function EmptyState({
  filter,
  hasAny,
}: {
  filter: FilterKey;
  hasAny: boolean;
}) {
  const copy: Record<FilterKey, { title: string; body: string }> = {
    all: {
      title: 'No broker questions yet',
      body:
        'Once Fondok detects year-over-year variances on the deal\'s historical P&Ls, broker-ready questions land here. Click Refresh to run detection now.',
    },
    pending: hasAny
      ? {
          title: 'No pending questions',
          body:
            'Great consistency on this deal\'s financials — all detected variances have been sent or resolved.',
        }
      : {
          title: 'Detection hasn\'t run yet',
          body:
            'Upload historical P&Ls + a T-12, then click Refresh to surface broker-ready questions on material YoY moves.',
        },
    sent: {
      title: 'Nothing awaiting a response',
      body: 'When you mark a question sent, it shows up here until the broker responds.',
    },
    answered: {
      title: 'No answered questions',
      body:
        'Broker responses get logged here so the deal team can reference the answer mid-IC review without digging through email.',
    },
    dismissed: {
      title: 'Nothing dismissed',
      body:
        'Dismissed questions live here with the justification so the audit trail stays complete.',
    },
  };
  const { title, body } = copy[filter];
  return (
    <div
      role="status"
      className="text-center py-10 px-6 rounded-md bg-bg border border-dashed border-ink-200"
    >
      <div className="w-10 h-10 rounded-md bg-success-50 border border-success-500/20 mx-auto flex items-center justify-center mb-3">
        <ClipboardList size={18} className="text-success-700" aria-hidden="true" />
      </div>
      <div className="text-[13px] font-semibold text-ink-900">{title}</div>
      <p className="text-[12px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
        {body}
      </p>
    </div>
  );
}

function ActionModal({
  action,
  onClose,
  onSubmit,
}: {
  action: PendingAction | null;
  onClose: () => void;
  onSubmit: (action: PendingAction, text: string) => Promise<void>;
}) {
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Reset text whenever a fresh action lands.
  useEffect(() => {
    setText('');
    setSubmitting(false);
  }, [action?.question.id, action?.kind]);

  if (!action) return null;

  const config = {
    sent: {
      title: 'Mark question sent',
      description:
        'Records that this question went to the broker. The next step (Answered) requires their response.',
      requiresText: false,
      cta: 'Mark Sent',
      placeholder: '',
      icon: Send,
    },
    answered: {
      title: 'Record broker response',
      description:
        'Paste the broker\'s exact reply. Fondok stores it with the question for the IC audit trail.',
      requiresText: true,
      cta: 'Save Response',
      placeholder: 'The broker explained that the F&B drop was driven by a closed-for-renovation kitchen for 11 weeks…',
      icon: CheckCheck,
    },
    dismissed: {
      title: 'Dismiss question',
      description:
        'Required reason — what did the deal team conclude that makes this question moot? (Stored on the audit trail.)',
      requiresText: true,
      cta: 'Dismiss',
      placeholder: 'Non-comparable period — 2024 was pre-renovation operations under a different brand.',
      icon: XCircle,
    },
  }[action.kind];
  const Icon = config.icon;
  const canSubmit = !config.requiresText || text.trim().length > 0;

  const handleSubmit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    try {
      await onSubmit(action, text.trim());
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal open onClose={onClose} title={config.title} maxWidth="max-w-lg">
      <div className="p-5 space-y-4">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
            <Icon size={16} className="text-brand-700" aria-hidden="true" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[11px] uppercase tracking-wider text-ink-500 font-semibold">
              {action.question.line_item} · {formatPeriod(action.question.period_key)}
            </div>
            <p className="font-serif text-[13px] text-ink-900 mt-1 leading-relaxed">
              {action.question.question_text}
            </p>
          </div>
        </div>
        <p className="text-[12.5px] text-ink-700 leading-relaxed">
          {config.description}
        </p>
        {config.requiresText && (
          <div>
            <label
              htmlFor="broker-action-text"
              className="block text-[11px] uppercase tracking-wider text-ink-500 font-semibold mb-1.5"
            >
              {action.kind === 'answered' ? 'Broker response' : 'Dismissal reason'}
            </label>
            <textarea
              id="broker-action-text"
              autoFocus
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={config.placeholder}
              rows={5}
              className="w-full px-3 py-2 rounded-md border border-border text-[13px] leading-relaxed text-ink-900 placeholder:text-ink-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              required
              maxLength={action.kind === 'answered' ? 4000 : 2000}
            />
            <div className="mt-1 flex items-center justify-between text-[11px] text-ink-500">
              <span>Required.</span>
              <span className="tabular-nums">
                {text.length}/{action.kind === 'answered' ? 4000 : 2000}
              </span>
            </div>
          </div>
        )}
        <div className="flex items-center justify-end gap-2 pt-2 border-t border-border">
          <Button size="sm" variant="ghost" onClick={onClose} disabled={submitting}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant={action.kind === 'dismissed' ? 'danger' : 'primary'}
            onClick={handleSubmit}
            loading={submitting}
            disabled={!canSubmit || submitting}
            aria-label={config.cta}
          >
            {!submitting && (
              action.kind === 'dismissed'
                ? <XCircle size={11} aria-hidden="true" />
                : action.kind === 'answered'
                  ? <CheckCheck size={11} aria-hidden="true" />
                  : <Send size={11} aria-hidden="true" />
            )}
            {config.cta}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// Re-export for any test harness or scratch file that wants the copy.
export const __test = { formatPeriod, formatPct, formatValue, timeAgo };
