'use client';
// Wave 5 RBAC — admin-only Cost Dashboard.
//
// Renders `GET /admin/cost` from the worker: total spend across
// 24h/7d/30d, top agents, model split, and top deals by cost. Same
// RBAC pattern as /audit — Clerk org:admin required; anyone else sees
// the honest "admin only" panel.

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Lock, DollarSign, Bot, Cpu, TrendingUp, RefreshCw } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { api, AdminCostResponse } from '@/lib/api';
import { useCurrentRole } from '@/lib/auth';
import { cn } from '@/lib/format';

const fmtUSD = (n: number) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD',
    minimumFractionDigits: 2, maximumFractionDigits: 4,
  }).format(n);

const fmtNum = (n: number) => new Intl.NumberFormat('en-US').format(n);
const fmtPct = (r: number) =>
  `${(r * 100).toFixed(r > 0 ? 1 : 0)}%`;

export default function AdminCostPage() {
  const currentRole = useCurrentRole();
  const isAdmin = currentRole === 'org:admin';

  const [data, setData] = useState<AdminCostResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.admin.cost(signal);
      setData(r);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      if (!msg.toLowerCase().includes('abort')) setError(msg);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAdmin) return;
    const ctl = new AbortController();
    void load(ctl.signal);
    return () => ctl.abort();
  }, [isAdmin, load]);

  // ─────────── RBAC lock ───────────
  if (!isAdmin) {
    return (
      <div className="px-8 py-8 max-w-[1024px]">
        <PageHeader eyebrow="Ops" title="Cost Dashboard" subtitle="LLM spend + cache hit rate for this tenant." />
        <Card className="mt-6 p-10 text-center">
          <Lock className="mx-auto h-10 w-10 text-ink-300" />
          <h2 className="mt-4 text-[15px] font-semibold text-ink-900">Admin only</h2>
          <p className="mt-2 text-[12.5px] text-ink-500">
            Cost data is gated to org admins. Ping an admin to grant access.
          </p>
          <p className="mt-3 text-[11.5px] text-ink-500">
            Current role:{' '}
            <code className="rounded-sm bg-ink-100 px-1 py-0.5">{currentRole || 'unknown'}</code>
          </p>
        </Card>
      </div>
    );
  }

  const w24 = data?.windows['24h'];
  const w7 = data?.windows['7d'];
  const w30 = data?.windows['30d'];

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        eyebrow="Ops"
        title="Cost Dashboard"
        subtitle="LLM spend + cache hit rate. Data comes from the worker's model_calls table, tenant-scoped to your org."
        action={
          <Button variant="secondary" size="sm" onClick={() => load()} disabled={loading}>
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} /> Refresh
          </Button>
        }
      />

      {error && (
        <Card className="mt-6 p-4 border-danger-500/40 bg-danger-50">
          <div className="text-[12.5px] text-danger-700">
            Failed to load: <code>{error}</code>
          </div>
        </Card>
      )}

      {loading && !data ? (
        <Card className="mt-6 p-10 text-center text-[12.5px] text-ink-500">Loading…</Card>
      ) : data && w24 && w7 && w30 ? (
        <div className="mt-6 grid gap-5">
          {/* Windowed totals */}
          <div className="grid grid-cols-3 gap-4">
            {([['24h', w24], ['7d', w7], ['30d', w30]] as const).map(([label, w]) => (
              <Card key={label} className="p-4">
                <div className="text-[10.5px] uppercase tracking-wider text-ink-500 font-semibold">
                  {label}
                </div>
                <div className="mt-1.5 flex items-baseline gap-2">
                  <div className="text-[24px] font-semibold tabular-nums text-ink-900">
                    {fmtUSD(w.cost_usd)}
                  </div>
                  <div className="text-[11.5px] text-ink-500">{fmtNum(w.calls)} calls</div>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] text-ink-500">
                  <div>Input tokens</div>
                  <div className="text-right tabular-nums text-ink-700">{fmtNum(w.input_tokens)}</div>
                  <div>Output tokens</div>
                  <div className="text-right tabular-nums text-ink-700">{fmtNum(w.output_tokens)}</div>
                  <div>Cache read</div>
                  <div className="text-right tabular-nums text-ink-700">{fmtNum(w.cache_read_tokens)}</div>
                  <div>Cache hit rate</div>
                  <div
                    className={cn(
                      'text-right tabular-nums font-medium',
                      w.cache_hit_rate > 0.3 ? 'text-success-700' :
                      w.cache_hit_rate > 0 ? 'text-warn-700' : 'text-ink-500',
                    )}
                  >
                    {fmtPct(w.cache_hit_rate)}
                  </div>
                </div>
              </Card>
            ))}
          </div>

          {/* Split panels */}
          <div className="grid grid-cols-2 gap-5">
            <Card className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <Bot size={14} className="text-brand-500" />
                <h3 className="text-[13px] font-semibold text-ink-900">Spend by agent</h3>
              </div>
              {data.by_agent.length === 0 ? (
                <div className="text-[12px] text-ink-500 py-4 text-center">No calls yet.</div>
              ) : (
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="text-ink-500 border-b border-border">
                      <th className="text-left font-medium py-1.5">Agent</th>
                      <th className="text-right font-medium py-1.5">Calls</th>
                      <th className="text-right font-medium py-1.5">Cost</th>
                      <th className="text-right font-medium py-1.5">Cache hit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_agent.map((a) => (
                      <tr key={a.agent} className="border-b border-border/50 last:border-0">
                        <td className="py-1.5 font-medium text-ink-900">{a.agent}</td>
                        <td className="py-1.5 text-right tabular-nums text-ink-700">{fmtNum(a.calls)}</td>
                        <td className="py-1.5 text-right tabular-nums text-ink-900 font-medium">{fmtUSD(a.cost_usd)}</td>
                        <td className="py-1.5 text-right tabular-nums text-ink-500">{fmtPct(a.cache_hit_rate)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>

            <Card className="p-4">
              <div className="flex items-center gap-2 mb-3">
                <Cpu size={14} className="text-brand-500" />
                <h3 className="text-[13px] font-semibold text-ink-900">Spend by model</h3>
              </div>
              {data.by_model.length === 0 ? (
                <div className="text-[12px] text-ink-500 py-4 text-center">No calls yet.</div>
              ) : (
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="text-ink-500 border-b border-border">
                      <th className="text-left font-medium py-1.5">Model</th>
                      <th className="text-right font-medium py-1.5">Calls</th>
                      <th className="text-right font-medium py-1.5">Cost</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.by_model.map((m) => (
                      <tr key={m.model} className="border-b border-border/50 last:border-0">
                        <td className="py-1.5 font-medium text-ink-900 truncate">{m.model}</td>
                        <td className="py-1.5 text-right tabular-nums text-ink-700">{fmtNum(m.calls)}</td>
                        <td className="py-1.5 text-right tabular-nums text-ink-900 font-medium">{fmtUSD(m.cost_usd)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
          </div>

          {/* Top deals */}
          <Card className="p-4">
            <div className="flex items-center gap-2 mb-3">
              <TrendingUp size={14} className="text-brand-500" />
              <h3 className="text-[13px] font-semibold text-ink-900">Top deals by cost</h3>
            </div>
            {data.by_deal.length === 0 ? (
              <div className="text-[12px] text-ink-500 py-4 text-center">No deals yet.</div>
            ) : (
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-ink-500 border-b border-border">
                    <th className="text-left font-medium py-1.5">Deal id</th>
                    <th className="text-right font-medium py-1.5">Calls</th>
                    <th className="text-right font-medium py-1.5">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_deal.map((d) => (
                    <tr key={d.deal_id} className="border-b border-border/50 last:border-0">
                      <td className="py-1.5 font-mono text-[11.5px] text-ink-900">
                        <a href={`/projects/${d.deal_id}`} className="hover:underline">{d.deal_id.slice(0, 8)}…{d.deal_id.slice(-4)}</a>
                      </td>
                      <td className="py-1.5 text-right tabular-nums text-ink-700">{fmtNum(d.calls)}</td>
                      <td className="py-1.5 text-right tabular-nums text-ink-900 font-medium">{fmtUSD(d.cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <div className="text-[10.5px] text-ink-500 tabular-nums">
            Generated at {new Date(data.generated_at).toLocaleString()} · tenant {data.tenant_id.slice(0, 8)}…{data.tenant_id.slice(-4)}
          </div>
        </div>
      ) : (
        <Card className="mt-6 p-10 text-center text-[12.5px] text-ink-500">
          <DollarSign className="mx-auto h-8 w-8 text-ink-300 mb-2" />
          No cost data yet. Any LLM call you make will start populating this.
        </Card>
      )}
    </div>
  );
}
