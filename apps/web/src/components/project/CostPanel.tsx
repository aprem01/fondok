'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  DollarSign, RefreshCw, Activity, Gauge, Database, AlertTriangle,
} from 'lucide-react';
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
  PieChart, Pie, Cell,
} from 'recharts';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { api, isWorkerConnected, WorkerError } from '@/lib/api';
import { fmtPctRaw, cn } from '@/lib/format';

// ─── Types — narrow mirror of fondok_schemas.DealCostReport ────────────

interface AgentCost {
  agent: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  cost_usd: number | string;
  avg_latency_ms: number;
}

interface ModelCallTimeline {
  model: string;
  agent_name?: string | null;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  trace_id: string;
  started_at: string;
  completed_at: string;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
}

interface DealCostReport {
  deal_id: string;
  total_cost_usd: number | string;
  budget_usd: number | string;
  cache_hit_rate: number;
  by_agent: AgentCost[];
  by_model: Record<string, AgentCost>;
  timeline: ModelCallTimeline[];
  generated_at: string;
}

const num = (v: number | string | undefined): number => {
  if (typeof v === 'number') return v;
  if (typeof v === 'string') {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  }
  return 0;
};

const fmtUsd = (n: number, decimals = 4) =>
  `$${n.toLocaleString('en-US', {
    minimumFractionDigits: Math.min(decimals, 2),
    maximumFractionDigits: decimals,
  })}`;

const MODEL_COLORS: Record<string, string> = {
  haiku: '#22c55e',
  sonnet: '#3b82f6',
  opus: '#a855f7',
  other: '#94a3b8',
};

const REFRESH_MS = 10_000;

// Demo report so the panel has something to render when the worker is
// not connected (preview deploys without NEXT_PUBLIC_WORKER_URL).
function demoReport(dealId: string): DealCostReport {
  const now = new Date();
  return {
    deal_id: dealId,
    total_cost_usd: 4.873,
    budget_usd: 20,
    cache_hit_rate: 0.31,
    by_agent: [
      { agent: 'analyst', calls: 4, input_tokens: 62_000, output_tokens: 18_000,
        cache_read_tokens: 12_000, cache_creation_tokens: 4_000,
        cost_usd: 2.61, avg_latency_ms: 4_200 },
      { agent: 'extractor', calls: 11, input_tokens: 95_000, output_tokens: 8_400,
        cache_read_tokens: 28_000, cache_creation_tokens: 6_000,
        cost_usd: 1.42, avg_latency_ms: 1_100 },
      { agent: 'normalizer', calls: 6, input_tokens: 31_000, output_tokens: 5_200,
        cache_read_tokens: 9_000, cache_creation_tokens: 1_500,
        cost_usd: 0.61, avg_latency_ms: 820 },
      { agent: 'router', calls: 22, input_tokens: 14_000, output_tokens: 1_800,
        cache_read_tokens: 6_500, cache_creation_tokens: 0,
        cost_usd: 0.23, avg_latency_ms: 290 },
    ],
    by_model: {
      opus: { agent: 'opus', calls: 4, input_tokens: 62_000, output_tokens: 18_000,
        cache_read_tokens: 12_000, cache_creation_tokens: 4_000,
        cost_usd: 2.61, avg_latency_ms: 4_200 },
      sonnet: { agent: 'sonnet', calls: 17, input_tokens: 126_000, output_tokens: 13_600,
        cache_read_tokens: 37_000, cache_creation_tokens: 7_500,
        cost_usd: 2.03, avg_latency_ms: 980 },
      haiku: { agent: 'haiku', calls: 22, input_tokens: 14_000, output_tokens: 1_800,
        cache_read_tokens: 6_500, cache_creation_tokens: 0,
        cost_usd: 0.23, avg_latency_ms: 290 },
    },
    timeline: Array.from({ length: 8 }).map((_, i) => ({
      model: i % 3 === 0 ? 'claude-opus-4-7' : i % 2 === 0 ? 'claude-sonnet-4-6' : 'claude-haiku-4-5',
      agent_name: ['analyst', 'extractor', 'router', 'normalizer'][i % 4],
      input_tokens: 1_000 + i * 800,
      output_tokens: 200 + i * 80,
      cost_usd: 0.04 + i * 0.02,
      trace_id: `trc-${i.toString().padStart(4, '0')}`,
      started_at: new Date(now.getTime() - i * 90_000).toISOString(),
      completed_at: new Date(now.getTime() - i * 90_000).toISOString(),
      cache_creation_input_tokens: 0,
      cache_read_input_tokens: i * 200,
    })),
    generated_at: now.toISOString(),
  };
}

async function fetchCosts(dealId: string, signal?: AbortSignal): Promise<DealCostReport> {
  if (!isWorkerConnected()) {
    return demoReport(dealId);
  }
  // The api wrapper doesn't yet have a costs() helper — use a raw fetch
  // so we don't have to widen the public surface here.
  const url = `${process.env.NEXT_PUBLIC_WORKER_URL?.replace(/\/+$/, '')}/deals/${dealId}/costs`;
  const res = await fetch(url, { signal });
  if (!res.ok) {
    throw new WorkerError(`GET /deals/${dealId}/costs → ${res.status}`, res.status, await res.text().catch(() => ''));
  }
  return (await res.json()) as DealCostReport;
}

// ─── Component ─────────────────────────────────────────────────────────

export default function CostPanel() {
  const params = useParams();
  // Project routes use numeric IDs in the demo; the worker expects UUIDs.
  // For preview / demo we synthesize a deterministic UUID from the numeric id.
  const rawId = String(params?.id ?? '0');
  const dealId = useMemo(() => synthesizeUuid(rawId), [rawId]);

  const [data, setData] = useState<DealCostReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setLoading(true);
    setError(null);
    try {
      const r = await fetchCosts(dealId, ctrl.signal);
      setData(r);
    } catch (e) {
      if ((e as { name?: string })?.name === 'AbortError') return;
      setError(e instanceof Error ? e.message : 'Failed to load cost report');
    } finally {
      setLoading(false);
    }
  }, [dealId]);

  useEffect(() => {
    load();
    return () => abortRef.current?.abort();
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return;
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [autoRefresh, load]);

  if (!data && loading) {
    return (
      <Card className="p-8 text-center" aria-busy="true">
        <div className="text-[12.5px] text-ink-700">Loading cost dashboard…</div>
      </Card>
    );
  }

  if (error && !data) {
    return (
      <Card className="p-8 text-center" role="alert">
        <AlertTriangle size={20} className="text-danger-700 mx-auto mb-2" aria-hidden="true" />
        <div className="text-[13px] font-semibold text-ink-900 mb-1">Cost report unavailable</div>
        <div className="text-[12px] text-ink-700 mb-3">{error}</div>
        <Button size="sm" variant="primary" onClick={load} aria-label="Retry loading costs">
          <RefreshCw size={12} aria-hidden="true" /> Retry
        </Button>
      </Card>
    );
  }

  if (!data) return null;

  const total = num(data.total_cost_usd);
  const budget = num(data.budget_usd) || 20;
  const pct = budget > 0 ? Math.min(total / budget, 1) : 0;
  const totalCalls = data.by_agent.reduce((acc, a) => acc + a.calls, 0);
  const avgLatency = data.by_agent.length
    ? data.by_agent.reduce((acc, a) => acc + a.avg_latency_ms * a.calls, 0) /
      Math.max(totalCalls, 1)
    : 0;

  if (totalCalls === 0) {
    return (
      <Card className="p-12 text-center">
        <div className="w-12 h-12 rounded-lg bg-brand-50 flex items-center justify-center mx-auto mb-4">
          <DollarSign size={20} className="text-brand-700" aria-hidden="true" />
        </div>
        <h3 className="text-[15px] font-semibold text-ink-900 mb-1">No LLM activity yet</h3>
        <p className="text-[12.5px] text-ink-700 mb-4 max-w-sm mx-auto">
          Run an agent (Extractor, Normalizer, Analyst) to populate this dashboard
          with token usage, cache hit rate, and per-call cost.
        </p>
        <Button size="sm" variant="secondary" onClick={load} aria-label="Refresh cost report">
          <RefreshCw size={12} aria-hidden="true" /> Refresh
        </Button>
      </Card>
    );
  }

  // ── Charts data ─────────────────────────────────────────────────────
  const barData = data.by_agent.map(a => ({
    agent: a.agent,
    Input: a.input_tokens,
    Output: a.output_tokens,
    'Cache Read': a.cache_read_tokens,
  }));

  const pieData = Object.entries(data.by_model).map(([bucket, c]) => ({
    name: bucket,
    value: num(c.cost_usd),
  }));

  return (
    <div className="space-y-5">
      {/* KPI strip */}
      <div className="grid grid-cols-4 gap-4">
        <KpiCard
          icon={<DollarSign size={14} aria-hidden="true" />}
          label="Total Spend"
          value={fmtUsd(total, 4)}
          tone={pct < 0.5 ? 'green' : pct < 0.8 ? 'amber' : 'red'}
        />
        <KpiCard
          icon={<Gauge size={14} aria-hidden="true" />}
          label="Budget"
          value={fmtUsd(budget, 2)}
          sub={`${(pct * 100).toFixed(1)}% used`}
        />
        <KpiCard
          icon={<Database size={14} aria-hidden="true" />}
          label="Cache Hit Rate"
          value={fmtPctRaw(data.cache_hit_rate * 100, 1)}
          sub={data.cache_hit_rate >= 0.3 ? 'Healthy' : 'Low — review prompt structure'}
        />
        <KpiCard
          icon={<Activity size={14} aria-hidden="true" />}
          label="Avg Latency"
          value={`${(avgLatency / 1000).toFixed(2)}s`}
          sub={`${totalCalls} calls`}
        />
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-3 gap-5">
        <Card className="col-span-2 p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[14px] font-semibold text-ink-900">By Agent — Token Usage</h3>
            <Badge tone="blue">{data.by_agent.length} agents</Badge>
          </div>
          <div className="h-64" role="img" aria-label="Stacked bar chart of token usage by agent">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={barData}>
                <XAxis dataKey="agent" tick={{ fontSize: 11, fill: '#475569' }} />
                <YAxis tick={{ fontSize: 11, fill: '#475569' }} />
                <Tooltip />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Bar dataKey="Input" stackId="a" fill="#3b82f6" />
                <Bar dataKey="Output" stackId="a" fill="#a855f7" />
                <Bar dataKey="Cache Read" stackId="a" fill="#22c55e" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card className="p-5">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-[14px] font-semibold text-ink-900">By Model — Cost</h3>
            <Badge tone="gray">{Object.keys(data.by_model).length} buckets</Badge>
          </div>
          <div className="h-64" role="img" aria-label="Pie chart of LLM cost distribution by model">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={pieData}
                  dataKey="value"
                  nameKey="name"
                  cx="50%"
                  cy="50%"
                  innerRadius={40}
                  outerRadius={75}
                  paddingAngle={2}
                  label={(e) => `${e.name} ${fmtUsd(e.value, 2)}`}
                  labelLine={false}
                >
                  {pieData.map((entry, i) => (
                    <Cell key={i} fill={MODEL_COLORS[entry.name] ?? '#94a3b8'} />
                  ))}
                </Pie>
                <Tooltip formatter={(v: number) => fmtUsd(v, 4)} />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </Card>
      </div>

      {/* Timeline table */}
      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-[14px] font-semibold text-ink-900">Recent Calls</h3>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-1.5 text-[11.5px] text-ink-700">
              <input
                type="checkbox"
                checked={autoRefresh}
                onChange={e => setAutoRefresh(e.target.checked)}
                aria-label="Toggle auto refresh"
              />
              Auto-refresh
            </label>
            <Button size="sm" variant="secondary" onClick={load} aria-label="Refresh cost report now">
              <RefreshCw size={12} className={cn(loading && 'animate-spin')} aria-hidden="true" /> Refresh
            </Button>
          </div>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-[12px]">
            <caption className="sr-only">Most recent LLM calls for this deal.</caption>
            <thead>
              <tr className="text-ink-700 text-[11px] border-b border-border">
                <th className="text-left font-medium pb-2">When</th>
                <th className="text-left font-medium pb-2">Agent</th>
                <th className="text-left font-medium pb-2">Model</th>
                <th className="text-right font-medium pb-2">Input</th>
                <th className="text-right font-medium pb-2">Output</th>
                <th className="text-right font-medium pb-2">Cache Read</th>
                <th className="text-right font-medium pb-2">Cost</th>
              </tr>
            </thead>
            <tbody>
              {data.timeline.slice(0, 25).map((c) => (
                <tr key={c.trace_id} className="border-b border-border/50">
                  <td className="py-2 text-ink-700">{formatRelative(c.completed_at)}</td>
                  <td className="py-2 text-ink-900">{c.agent_name ?? '—'}</td>
                  <td className="py-2 text-ink-700">{c.model}</td>
                  <td className="py-2 text-right tabular-nums">{c.input_tokens.toLocaleString()}</td>
                  <td className="py-2 text-right tabular-nums">{c.output_tokens.toLocaleString()}</td>
                  <td className="py-2 text-right tabular-nums">
                    {(c.cache_read_input_tokens ?? 0).toLocaleString()}
                  </td>
                  <td className="py-2 text-right tabular-nums font-medium">{fmtUsd(c.cost_usd, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Budget bar */}
      <Card className="p-5">
        <div className="flex items-center justify-between mb-2">
          <div>
            <div className="text-[13px] font-semibold text-ink-900">Deal Budget</div>
            <div className="text-[11.5px] text-ink-700">
              {fmtUsd(total, 4)} of {fmtUsd(budget, 2)} ({(pct * 100).toFixed(1)}%)
            </div>
          </div>
          <Badge tone={pct < 0.5 ? 'green' : pct < 0.8 ? 'amber' : 'red'}>
            {pct < 0.5 ? 'Within budget' : pct < 0.8 ? 'Approaching cap' : 'Over threshold'}
          </Badge>
        </div>
        <div
          className="h-2 bg-ink-300/30 rounded-full overflow-hidden"
          role="progressbar"
          aria-label="Budget consumption"
          aria-valuenow={Math.round(pct * 100)}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div
            className={cn(
              'h-full transition-all',
              pct < 0.5 && 'bg-success-500',
              pct >= 0.5 && pct < 0.8 && 'bg-warn-500',
              pct >= 0.8 && 'bg-danger-500',
            )}
            style={{ width: `${pct * 100}%` }}
          />
        </div>
      </Card>
    </div>
  );
}

// ─── Helpers ──────────────────────────────────────────────────────────

function KpiCard({
  icon, label, value, sub, tone,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
  sub?: string;
  tone?: 'green' | 'amber' | 'red';
}) {
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2 text-ink-700 text-[11.5px] mb-2">
        {icon} <span>{label}</span>
      </div>
      <div className={cn(
        'text-[20px] font-semibold tabular-nums text-ink-900',
        tone === 'amber' && 'text-warn-700',
        tone === 'red' && 'text-danger-700',
        tone === 'green' && 'text-success-700',
      )}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-ink-700 mt-1">{sub}</div>}
    </Card>
  );
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const ms = Date.now() - d.getTime();
  if (ms < 0) return 'now';
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return d.toLocaleDateString();
}

/**
 * Project pages use numeric mock IDs ("1", "2", …) but the worker
 * /deals/{id}/costs endpoint expects a UUID. For preview/demo we map a
 * numeric id to a deterministic dummy UUID; once real deals land,
 * params.id will already be a UUID and this is a passthrough.
 */
function synthesizeUuid(raw: string): string {
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)) {
    return raw;
  }
  const padded = raw.padStart(12, '0').slice(-12);
  return `00000000-0000-0000-0000-${padded}`;
}
