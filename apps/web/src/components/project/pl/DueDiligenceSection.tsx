'use client';
/**
 * DueDiligenceSection — Lovable-parity broker question packet.
 *
 * Renders 4 KPI counters, a category filter bar, and a list of
 * AI-generated broker questions sourced from the worker's
 * ``/deals/{id}/due-diligence`` endpoint. Batch actions: Copy,
 * Export All, Mark as Sent. Per-question status flow:
 * pending → sent → answered.
 *
 * Empty state ships with a "Generate Due Diligence Questions" CTA
 * that fires ``POST /deals/{id}/due-diligence/generate``.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Sparkles, AlertCircle, Clock, CheckCircle2, ListChecks,
  Copy, Download, Send, RefreshCw, TrendingUp, DollarSign,
  Activity, MapPinned, Wrench, Loader2,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import {
  api, isWorkerConnected,
  DueDiligenceCategory, DueDiligencePacket, DueDiligenceQuestion,
} from '@/lib/api';

const CATEGORIES: ReadonlyArray<{ id: 'all' | DueDiligenceCategory; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'revenue', label: 'Revenue' },
  { id: 'expenses', label: 'Expenses' },
  { id: 'operations', label: 'Operations' },
  { id: 'market', label: 'Market' },
  { id: 'capex', label: 'CapEx' },
];

const CATEGORY_ICON: Record<DueDiligenceCategory, typeof TrendingUp> = {
  revenue: TrendingUp,
  expenses: DollarSign,
  operations: Activity,
  market: MapPinned,
  capex: Wrench,
};

const PRIORITY_TONE: Record<'high' | 'medium' | 'low', 'red' | 'amber' | 'gray'> = {
  high: 'red',
  medium: 'amber',
  low: 'gray',
};
const PRIORITY_LABEL: Record<'high' | 'medium' | 'low', string> = {
  high: 'High Priority',
  medium: 'Medium Priority',
  low: 'Low Priority',
};

const STATUS_TONE: Record<'pending' | 'sent' | 'answered', 'amber' | 'blue' | 'green'> = {
  pending: 'amber',
  sent: 'blue',
  answered: 'green',
};

export default function DueDiligenceSection({ dealId }: { dealId: string }) {
  const { toast } = useToast();
  const isMockId = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && dealId && !isMockId;

  const [packet, setPacket] = useState<DueDiligencePacket | null>(null);
  const [loading, setLoading] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [filter, setFilter] = useState<'all' | DueDiligenceCategory>('all');
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const refresh = async () => {
    if (!liveMode) return;
    setLoading(true);
    try {
      const p = await api.dueDiligence.list(String(dealId));
      setPacket(p);
    } catch (err) {
      console.warn('due-diligence list failed', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dealId]);

  const onGenerate = async () => {
    if (!liveMode) {
      toast('Worker not connected — cannot generate', { type: 'error' });
      return;
    }
    setGenerating(true);
    try {
      const r = await api.dueDiligence.generate(String(dealId));
      if (r.error) {
        toast(`Generation failed: ${r.error}`, { type: 'error' });
      } else {
        toast(`Generated ${r.generated} broker questions`, { type: 'success' });
        await refresh();
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Generation failed: ${msg}`, { type: 'error' });
    } finally {
      setGenerating(false);
    }
  };

  const onCopy = () => {
    const targets = (packet?.questions ?? []).filter(q => selected.has(q.id));
    if (targets.length === 0) {
      toast('Select at least one question to copy', { type: 'info' });
      return;
    }
    const text = targets.map(q => `Q: ${q.question}\n${q.narrative}`).join('\n\n');
    navigator.clipboard.writeText(text);
    toast(`Copied ${targets.length} question${targets.length === 1 ? '' : 's'}`, {
      type: 'success',
    });
  };

  const onExportAll = () => {
    if (!packet?.questions.length) return;
    const lines = [
      ['Priority', 'Category', 'Question', 'Narrative', 'Source', 'Metric', 'Status']
        .join('\t'),
      ...packet.questions.map(q =>
        [
          q.priority,
          q.category,
          q.question.replace(/\t/g, ' '),
          q.narrative.replace(/\t/g, ' '),
          q.source,
          `${q.supporting_metric_key ?? ''}: ${q.supporting_metric_value ?? ''}`,
          q.status,
        ].join('\t'),
      ),
    ].join('\n');
    const blob = new Blob([lines], { type: 'text/tab-separated-values' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `due-diligence-${dealId}.tsv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onMarkAsSent = async () => {
    const targets = (packet?.questions ?? []).filter(q => selected.has(q.id));
    if (targets.length === 0) {
      toast('Select questions to mark as sent', { type: 'info' });
      return;
    }
    try {
      await Promise.all(
        targets.map(q =>
          api.dueDiligence.updateStatus(String(dealId), q.id, 'sent'),
        ),
      );
      toast(`Marked ${targets.length} question${targets.length === 1 ? '' : 's'} as sent`, {
        type: 'success',
      });
      setSelected(new Set());
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Update failed: ${msg}`, { type: 'error' });
    }
  };

  const visible = useMemo(() => {
    const all = packet?.questions ?? [];
    if (filter === 'all') return all;
    return all.filter(q => q.category === filter);
  }, [packet, filter]);

  if (!liveMode) {
    return (
      <Card className="p-12 text-center">
        <Sparkles size={20} className="mx-auto text-ink-400 mb-2" />
        <div className="text-[13px] text-ink-700 font-medium">
          Due Diligence questions populate from a real worker-backed deal.
        </div>
        <div className="text-[11.5px] text-ink-500 mt-1">
          Mock and demo deals use the static Kimpton sample.
        </div>
      </Card>
    );
  }

  if (packet === null && loading) {
    return (
      <Card className="p-12 text-center">
        <Loader2 size={18} className="mx-auto animate-spin text-ink-400 mb-2" />
        <div className="text-[12.5px] text-ink-500">Loading broker question packet…</div>
      </Card>
    );
  }

  if (!packet || packet.questions.length === 0) {
    return (
      <Card className="p-12 text-center">
        <Sparkles size={22} className="mx-auto text-brand-500 mb-3" />
        <div className="text-[14px] font-semibold text-ink-900">
          No broker questions yet
        </div>
        <p className="text-[12.5px] text-ink-500 mt-1.5 max-w-md mx-auto leading-relaxed">
          {packet?.note ??
            'Run the Due Diligence agent to generate a prioritized broker-question packet from the deal\'s extracted state.'}
        </p>
        <Button
          variant="primary"
          size="sm"
          className="mt-4"
          onClick={onGenerate}
          disabled={generating}
        >
          {generating ? (
            <><Loader2 size={12} className="animate-spin" /> Generating…</>
          ) : (
            <><Sparkles size={12} /> Generate Due Diligence Questions</>
          )}
        </Button>
      </Card>
    );
  }

  const allSelected =
    visible.length > 0 && visible.every(q => selected.has(q.id));

  return (
    <div className="space-y-5">
      {/* KPI strip */}
      <div className="grid grid-cols-4 gap-4">
        <KpiCard
          label="Total Questions"
          value={packet.total}
          icon={ListChecks}
          tone="brand"
        />
        <KpiCard
          label="High Priority"
          value={packet.high_priority}
          icon={AlertCircle}
          tone="danger"
        />
        <KpiCard
          label="Pending"
          value={packet.pending}
          icon={Clock}
          tone="warn"
        />
        <KpiCard
          label="Answered"
          value={packet.answered}
          icon={CheckCircle2}
          tone="success"
        />
      </div>

      {/* Action bar */}
      <Card className="p-3.5">
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-2 text-[12.5px] text-ink-700 cursor-pointer">
            <input
              type="checkbox"
              checked={allSelected}
              onChange={() => {
                if (allSelected) setSelected(new Set());
                else setSelected(new Set(visible.map(q => q.id)));
              }}
              className="cursor-pointer"
            />
            Select all
          </label>
          <div className="flex items-center gap-1 ml-2">
            {CATEGORIES.map(c => (
              <button
                key={c.id}
                type="button"
                onClick={() => setFilter(c.id)}
                className={cn(
                  'px-3 py-1 text-[12px] rounded-md transition-colors',
                  filter === c.id
                    ? 'bg-brand-50 text-brand-700 font-semibold'
                    : 'text-ink-700 hover:bg-ink-300/15',
                )}
              >
                {c.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <Button variant="secondary" size="sm" onClick={onCopy}>
              <Copy size={11} /> Copy
            </Button>
            <Button variant="secondary" size="sm" onClick={onExportAll}>
              <Download size={11} /> Export All
            </Button>
            <Button variant="primary" size="sm" onClick={onMarkAsSent}>
              <Send size={11} /> Mark as Sent
            </Button>
            <button
              type="button"
              onClick={onGenerate}
              disabled={generating}
              title="Regenerate questions from the latest extraction state"
              className="p-1.5 rounded text-ink-500 hover:text-ink-900 hover:bg-ink-300/15"
            >
              {generating ? (
                <Loader2 size={13} className="animate-spin" />
              ) : (
                <RefreshCw size={13} />
              )}
            </button>
          </div>
        </div>
      </Card>

      {/* Questions */}
      <Card className="p-5">
        <div className="flex items-start gap-2.5 mb-4">
          <div className="w-8 h-8 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
            <Sparkles size={14} className="text-brand-700" />
          </div>
          <div>
            <h3 className="text-[14px] font-semibold text-ink-900">
              Due Diligence Questions for Broker
            </h3>
            <p className="text-[11.5px] text-ink-500 mt-0.5">
              AI-generated questions based on underwriting analysis and data gaps
            </p>
          </div>
        </div>
        <div className="space-y-3">
          {visible.map((q, idx) => (
            <QuestionCard
              key={q.id}
              q={q}
              index={idx + 1}
              selected={selected.has(q.id)}
              onToggle={() => {
                setSelected(prev => {
                  const next = new Set(prev);
                  if (next.has(q.id)) next.delete(q.id);
                  else next.add(q.id);
                  return next;
                });
              }}
            />
          ))}
          {visible.length === 0 && (
            <div className="text-center text-[12px] text-ink-500 py-8">
              No questions in this category. Switch filter to "All" to see the full packet.
            </div>
          )}
        </div>
      </Card>
    </div>
  );
}

function QuestionCard({
  q,
  index,
  selected,
  onToggle,
}: {
  q: DueDiligenceQuestion;
  index: number;
  selected: boolean;
  onToggle: () => void;
}) {
  const CategoryIcon = CATEGORY_ICON[q.category] ?? TrendingUp;
  return (
    <div
      className={cn(
        'rounded-md border bg-bg/30 p-4 transition-colors',
        selected ? 'border-brand-500 bg-brand-50/40' : 'border-border',
      )}
    >
      <div className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          className="mt-1 cursor-pointer"
        />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-2">
            <span className="text-[11px] font-mono text-ink-500">Q{index}</span>
            <CategoryIcon size={12} className="text-ink-500" />
            <Badge tone={PRIORITY_TONE[q.priority]}>{PRIORITY_LABEL[q.priority]}</Badge>
            <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-ink-500">
              <Clock size={11} />
              <Badge tone={STATUS_TONE[q.status]}>
                {q.status === 'pending' ? 'Pending' : q.status === 'sent' ? 'Sent' : 'Answered'}
              </Badge>
            </span>
          </div>
          <div className="text-[13px] font-medium text-ink-900 leading-snug">
            {q.question}
          </div>
          <div className="text-[12px] text-ink-700 mt-1.5 leading-relaxed">
            {q.narrative}
          </div>
          <div className="flex items-center gap-3 mt-3 text-[11px] text-ink-500 flex-wrap">
            <span className="inline-flex items-center gap-1">
              <Sparkles size={10} className="text-brand-500" />
              Source: <span className="text-ink-700 font-medium">{q.source}</span>
            </span>
            {q.supporting_metric_key && q.supporting_metric_value && (
              <>
                <span className="text-ink-300">|</span>
                <span>
                  {q.supporting_metric_key}: <span className="text-ink-700 font-medium tabular-nums">{q.supporting_metric_value}</span>
                </span>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  icon: Icon,
  tone,
}: {
  label: string;
  value: number;
  icon: typeof TrendingUp;
  tone: 'brand' | 'danger' | 'warn' | 'success';
}) {
  const tones: Record<typeof tone, string> = {
    brand: 'bg-brand-50 text-brand-700',
    danger: 'bg-danger-50 text-danger-700',
    warn: 'bg-warn-50 text-warn-700',
    success: 'bg-success-50 text-success-700',
  };
  return (
    <Card className="p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold">
            {label}
          </div>
          <div className="text-[26px] font-bold tabular-nums text-ink-900 mt-1.5">
            {value}
          </div>
        </div>
        <div className={cn('w-9 h-9 rounded-md flex items-center justify-center', tones[tone])}>
          <Icon size={16} />
        </div>
      </div>
    </Card>
  );
}
