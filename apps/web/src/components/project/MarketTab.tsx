'use client';
import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
} from 'recharts';
import { MapPinned, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  isWorkerConnected,
  workerUrl,
  type TransactionCompsResult,
} from '@/lib/api';
import { useDeal } from '@/lib/hooks/useDeal';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { cn } from '@/lib/format';
import { IntroCard } from '@/components/help/IntroCard';
import IndexAnalysisSection from './pl/IndexAnalysisSection';

// FON-47 — real STR/CoStar market data served by /deals/{id}/market-data
// (apps/worker/app/api/documents.py _aggregate_market_data). Populated once an
// STR_TREND / CoStar report is uploaded + extracted.
interface StrCompRow {
  name: string;
  keys: number | null;
  occupancy_pct: number | null;
  adr_usd: number | null;
  revpar_usd: number | null;
}
interface StrTrend {
  subject_occupancy_pct: number | null;
  subject_adr_usd: number | null;
  subject_revpar_usd: number | null;
  rgi_revpar_index: number | null;
  ari_adr_index: number | null;
  mpi_occupancy_index: number | null;
  comp_set_size: number | null;
  total_keys: number | null;
  compset: StrCompRow[];
}
interface MarketDataResp {
  deal_id: string;
  str_trend: StrTrend | null;
  sources?: Record<string, unknown>;
}

const subTabs = ['Market Overview', 'Transaction Comps', 'Index Analysis'];

// Worker `GET /market/{deal_id}/overview` shape — mirrors
// apps/worker/app/api/market.py MarketOverview. Indices are null
// until the STR/CoStar feed is wired up.
interface WorkerMarketOverview {
  deal_id: string;
  market: string | null;
  keys: number | null;
  brand: string | null;
  service: string | null;
  property_name: string | null;
  occupancy_index: number | null;
  adr_index: number | null;
  revpar_index: number | null;
}

const tooltipStyle = {
  contentStyle: { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, fontSize: 12 },
  labelStyle: { color: '#64748b', fontSize: 11 },
};

// STR occupancy arrives as a 0..1 fraction (0.715); some sources send a whole
// percent (71.5). Normalize to a whole-percent number for display so we never
// render "0.7%" for a 72%-occupied hotel (Sam QA).
const occPct = (v: number): number => (v <= 1.5 ? v * 100 : v);

// STR / CoStar anonymize per-property comp performance, so every competitor's
// Occ/ADR/RevPAR comes back null. But the report DOES publish the subject's
// index vs the comp-set aggregate — MPI (occupancy), ARI (ADR), RGI (RevPAR) —
// where each index = subject metric ÷ comp-set metric. So the blended comp-set
// performance is fully recoverable: comp metric = subject metric ÷ index. This
// is the standard way an analyst benchmarks a subject to its comp set. Indices
// may arrive as a ratio (1.098) or as index points (109.8); normalize both.
function deriveCompSet(
  t: StrTrend | null,
): { occ: number | null; adr: number | null; revpar: number | null } | null {
  if (!t) return null;
  const ratio = (idx: number | null): number | null =>
    idx == null || idx <= 0 ? null : idx > 3 ? idx / 100 : idx;
  const div = (subj: number | null, idx: number | null, normalizeSubj = false): number | null => {
    const r = ratio(idx);
    if (subj == null || r == null) return null;
    return (normalizeSubj ? occPct(subj) : subj) / r;
  };
  const occ = div(t.subject_occupancy_pct, t.mpi_occupancy_index, true);
  const adr = div(t.subject_adr_usd, t.ari_adr_index);
  const revpar = div(t.subject_revpar_usd, t.rgi_revpar_index);
  if (occ == null && adr == null && revpar == null) return null;
  return { occ, adr, revpar };
}

type DerivedComp = { occ: number | null; adr: number | null; revpar: number | null } | null;

// FON-60 Market Overview §1 — an executive submarket read built from the
// competitive-set aggregate (the subject's local market): inventory from the
// comp-set roster, Occupancy/ADR/RevPAR from the blended comp metrics. Demand
// and supply growth need a multi-year trend report, so they read "—" (never a
// fabricated fixture value — FON-60 requirement #10).
function SubmarketSnapshot({
  derivedComp, compSetSize, compKeyCount, submarketLabel,
}: {
  derivedComp: DerivedComp;
  compSetSize: number | null;
  compKeyCount: number | null;
  submarketLabel: string | null;
}) {
  const inventory = compSetSize && compKeyCount
    ? `${compSetSize} hotels · ${compKeyCount.toLocaleString()} keys`
    : compKeyCount ? `${compKeyCount.toLocaleString()} keys` : '—';
  const kpis = [
    { label: 'Inventory', value: inventory, sub: 'competitive set' },
    { label: 'Market Occupancy', value: derivedComp?.occ != null ? `${derivedComp.occ.toFixed(1)}%` : '—', sub: 'comp-set blend' },
    { label: 'Market ADR', value: derivedComp?.adr != null ? `$${derivedComp.adr.toFixed(0)}` : '—', sub: 'comp-set blend' },
    { label: 'Market RevPAR', value: derivedComp?.revpar != null ? `$${derivedComp.revpar.toFixed(0)}` : '—', sub: 'comp-set blend' },
    { label: 'Demand Growth', value: '—', sub: 'needs trend report' },
    { label: 'Supply Growth', value: '—', sub: 'needs trend report' },
  ];
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-5 py-3 border-b border-border bg-surface-2/40">
        <h3 className="text-[14px] font-semibold text-ink-900">Submarket Snapshot</h3>
        <p className="text-[11.5px] text-ink-500 mt-0.5">
          {submarketLabel ? `${submarketLabel} · ` : ''}Competitive-set aggregate — the subject&apos;s local market.
        </p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 divide-x divide-y lg:divide-y-0 divide-border">
        {kpis.map((k) => (
          <div key={k.label} className="px-4 py-3.5">
            <div className="text-[10px] uppercase tracking-wide text-ink-500">{k.label}</div>
            <div className="text-[17px] font-semibold text-ink-900 tabular-nums mt-0.5">{k.value}</div>
            <div className="text-[9.5px] text-ink-400 mt-0.5">{k.sub}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// FON-60 Market Overview §5 — a concise MPI / ARI / RGI read. The detailed
// historical + forecast penetration series lives on Index Analysis; this is the
// executive summary. Values come straight from the STR report indices.
function MarketIndexSummary({ strTrend }: { strTrend: StrTrend }) {
  const pt = (idx: number | null): number | null =>
    idx == null || idx <= 0 ? null : idx > 3 ? idx : idx * 100;
  const items = [
    { key: 'MPI', label: 'Occupancy Index', v: pt(strTrend.mpi_occupancy_index) },
    { key: 'ARI', label: 'ADR Index', v: pt(strTrend.ari_adr_index) },
    { key: 'RGI', label: 'RevPAR Index', v: pt(strTrend.rgi_revpar_index) },
  ];
  if (!items.some((i) => i.v != null)) return null;
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-5 py-3 border-b border-border bg-surface-2/40">
        <h3 className="text-[14px] font-semibold text-ink-900">Subject vs Comp Set — Index Summary</h3>
        <p className="text-[11.5px] text-ink-500 mt-0.5">100 = at par with the comp set · &gt;100 = the subject outperforms.</p>
      </div>
      <div className="grid grid-cols-3 divide-x divide-border">
        {items.map((it) => {
          const over = it.v != null && it.v >= 100;
          return (
            <div key={it.key} className="px-5 py-4">
              <div className="text-[10px] uppercase tracking-wide text-ink-500">{it.label} <span className="text-ink-400">· {it.key}</span></div>
              <div className={cn('text-[24px] font-semibold tabular-nums mt-1', it.v == null ? 'text-ink-400' : over ? 'text-success-700' : 'text-warn-700')}>
                {it.v != null ? it.v.toFixed(1) : '—'}
              </div>
              <div className="text-[10.5px] text-ink-500 mt-0.5">
                {it.v == null
                  ? 'not published'
                  : over
                    ? `subject leads by ${(it.v - 100).toFixed(0)} pts`
                    : `subject trails by ${(100 - it.v).toFixed(0)} pts`}
              </div>
            </div>
          );
        })}
      </div>
      <div className="px-5 py-2.5 border-t border-border text-[11px] text-ink-500">
        Full historical + forecast penetration series on <span className="font-medium text-ink-700">Index Analysis</span>.
      </div>
    </Card>
  );
}

// FON-60 Market Overview §2/3/6/7 — sections in Sam's hierarchy whose data
// source isn't extracted yet. Shown as labeled "awaiting data" rows (never a
// fixture) so the structure is visible and it's clear what report populates each.
function PendingMarketCard({ title, items }: { title: string; items: { label: string; note: string }[] }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-5 py-3 border-b border-border bg-surface-2/40">
        <h3 className="text-[14px] font-semibold text-ink-900">{title}</h3>
      </div>
      <div className="divide-y divide-border">
        {items.map((it) => (
          <div key={it.label} className="flex items-start justify-between gap-4 px-5 py-3">
            <div>
              <div className="text-[12.5px] font-medium text-ink-800">{it.label}</div>
              <div className="text-[11px] text-ink-500 mt-0.5 leading-relaxed">{it.note}</div>
            </div>
            <span className="shrink-0 mt-0.5 text-[9.5px] uppercase tracking-wide text-ink-400 bg-ink-100/60 rounded px-1.5 py-0.5">Awaiting data</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function MarketTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Market Overview');
  const { toast } = useToast();
  const params = useParams();
  const dealId = (params?.id as string | undefined) ?? String(projectId);
  const { deal, refresh: refreshDeal } = useDeal(dealId);

  // FON-47 (a) — fetch the REAL aggregated STR/CoStar market data. The tab
  // previously only hit /market/overview (indices null) and showed an empty
  // state even when an STR report was uploaded.
  const [marketData, setMarketData] = useState<MarketDataResp | null>(null);
  useEffect(() => {
    if (!isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) return;
    const ctrl = new AbortController();
    api.market
      .data(dealId, ctrl.signal)
      .then((d) => { if (d) setMarketData(d as MarketDataResp); })
      .catch(() => { /* silent — empty state covers it */ });
    return () => ctrl.abort();
  }, [dealId]);
  const strTrend = marketData?.str_trend ?? null;
  const hasStr = !!(strTrend && (strTrend.subject_occupancy_pct != null || strTrend.subject_adr_usd != null));
  // Blended comp-set benchmark recovered from the STR indices (see deriveCompSet).
  const derivedComp = deriveCompSet(strTrend);
  // Comp-set room count = sum of the named roster's keys. This (not the extracted
  // `total_keys` rollup, which has proven unreliable — e.g. 1011 for a 424-key
  // set) is the figure shown consistently across the header, the blended comp-set
  // row, and Index Analysis, so all three agree (FON-61 a).
  const compKeyCount =
    strTrend?.compset?.reduce((s, c) => s + (c.keys && c.keys > 0 ? c.keys : 0), 0) || null;

  // FON-47 (b) — let the analyst drive the model's Year-1 occupancy/ADR from
  // the STR market rates. The revenue engine already reads
  // `revenue_seed_from_str_forecast` from field_overrides (default off); this
  // toggles that override + re-runs (same mechanism as the FON-27 overrides).
  const { run, status: runStatus } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  const strRunning = runStatus === 'running' || runStatus === 'queued';
  const overrides = (deal?.field_overrides ?? {}) as Record<string, unknown>;
  const rawSeed = overrides['revenue_seed_from_str_forecast'];
  const strSeeded =
    rawSeed === true ||
    (typeof rawSeed === 'object' && rawSeed !== null && (rawSeed as { value?: unknown }).value === true);
  const toggleStrSeed = async () => {
    const next = { ...overrides };
    if (strSeeded) delete next['revenue_seed_from_str_forecast'];
    // Same {value, note} shape the FON-27 overrides use (engine reads it via
    // _normalize_override_shape → base["revenue_seed_from_str_forecast"]).
    else next['revenue_seed_from_str_forecast'] = { value: true, note: 'STR market rates enabled from the Market tab' };
    try {
      await api.deals.update(dealId, { field_overrides: next });
      refreshDeal();
      await run();
      toast(
        strSeeded ? 'Reverted Year-1 to the T-12 actuals' : 'Year-1 occupancy & ADR now driven by STR market rates — re-modeled',
        { type: 'success' },
      );
    } catch {
      toast('Could not update the model', { type: 'error' });
    }
  };

  // Worker market overview — populated for live deals once
  // /market/{deal_id}/overview returns. Indices are null until the STR
  // feed is wired in (TODO(str-integration) on the worker side), so we
  // only use the response for the submarket label / keys readout.
  const [workerMarket, setWorkerMarket] = useState<WorkerMarketOverview | null>(null);
  useEffect(() => {
    if (!isWorkerConnected()) return;
    if (!dealId || /^\d+$/.test(dealId)) return; // mock id, no UUID
    const ctrl = new AbortController();
    api.market
      .overview(dealId, ctrl.signal)
      .then((data) => { if (data) setWorkerMarket(data as WorkerMarketOverview); })
      .catch(() => { /* silent — empty state covers it */ });
    return () => ctrl.abort();
  }, [dealId]);

  // Transaction comps — extracted from OMs by the worker. Sam called
  // these "critical for anchoring exit cap rate" (May 7 call summary).
  // For the Kimpton demo deal we keep the curated fixture so the demo
  // story stays clean. For every other deal we hit the worker
  // endpoint; an empty array surfaces the "awaiting OM extraction"
  // empty state.
  const [workerComps, setWorkerComps] =
    useState<TransactionCompsResult | null>(null);
  useEffect(() => {
    if (!isWorkerConnected()) return;
    if (!dealId || /^\d+$/.test(dealId)) return;
    const ctrl = new AbortController();
    api.market
      .transactionComps(dealId, ctrl.signal)
      .then((res) => setWorkerComps(res))
      .catch(() => { /* silent — empty state covers it */ });
    return () => ctrl.abort();
  }, [dealId]);

  // Submarket label: deal.city wins for live deals; falls back to the
  // worker's market overview label.
  const submarketLabel = deal?.city ?? workerMarket?.market ?? null;

  return (
      <div>
        <IntroCard
          dismissKey="market-intro"
          title="The Market view"
          body={
            <>
              What&apos;s happening in this submarket — recent performance trends, new hotels being
              built (the supply pipeline), what&apos;s driving demand, and recent sales of comparable
              hotels. The basis for your projections and exit valuation.
            </>
          }
        />
        <Card className="p-5 mb-5">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-[15px] font-semibold text-ink-900">Market Data</h2>
              <p className="text-[12.5px] text-ink-500 mt-1">
                Submarket performance, supply pipeline, demand drivers, and recent transaction comparables.
              </p>
            </div>
          </div>
        </Card>

        {/* FON-60/61 — real deals get the same 3 sub-tabs as the demo
            (Market Overview / Transaction Comps / Index Analysis), each wired
            to live data. Previously this early-return path rendered only the
            STR card and skipped the sub-tab shell entirely. */}
        <div className="flex items-center gap-1 mb-5 border-b border-border">
          {subTabs.map((st) => (
            <button key={st} onClick={() => setTab(st)}
              className={cn(
                'px-4 py-2 text-[12.5px] border-b-2 transition-colors -mb-px',
                tab === st ? 'border-brand-500 text-brand-700 font-medium' : 'border-transparent text-ink-500 hover:text-ink-900'
              )}>
              {st}
            </button>
          ))}
        </div>

        {tab === 'Market Overview' && (hasStr && strTrend ? (
          // FON-60 — executive Market Overview: submarket read, subject-vs-comp
          // benchmark, index summary, and the sections awaiting a submarket feed.
          <div className="space-y-5">
            {/* §1 — Submarket snapshot (competitive-set aggregate) */}
            <SubmarketSnapshot
              derivedComp={derivedComp}
              compSetSize={strTrend.comp_set_size ?? null}
              compKeyCount={compKeyCount}
              submarketLabel={submarketLabel}
            />

            {/* §2 & §3 — market performance trends (need a multi-year/monthly feed) */}
            <PendingMarketCard
              title="Market Performance Trends"
              items={[
                { label: 'Historical Market Performance', note: 'Multi-year submarket Occupancy / ADR / RevPAR trends — populates from a multi-year STR / CoStar trend report.' },
                { label: 'Monthly / TTM Performance', note: 'Recent performance and seasonality. TTM subject metrics appear below; monthly detail needs a monthly STR report.' },
              ]}
            />

          {/* §4 — Subject vs Comp Set snapshot (blended STR, live from /market-data) */}
          <Card className="p-0 overflow-hidden">
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-border bg-surface-2/40">
              <div>
                <h3 className="text-[14px] font-semibold text-ink-900">Subject vs Comp Set</h3>
                <p className="text-[11.5px] text-ink-500 mt-0.5">
                  Subject TTM vs the blended competitive set · from this deal&apos;s uploaded STR / CoStar Trend report
                  {strTrend.comp_set_size ? ` · ${strTrend.comp_set_size}-property comp set` : ''}
                  {compKeyCount ? ` · ${compKeyCount.toLocaleString()} keys` : ''}.
                </p>
              </div>
            </div>
            <div className="grid grid-cols-3 divide-x divide-border border-b border-border">
              {[
                { label: 'Occupancy', value: strTrend.subject_occupancy_pct != null ? `${occPct(strTrend.subject_occupancy_pct).toFixed(1)}%` : '—' },
                { label: 'ADR', value: strTrend.subject_adr_usd != null ? `$${strTrend.subject_adr_usd.toFixed(2)}` : '—' },
                { label: 'RevPAR', value: strTrend.subject_revpar_usd != null ? `$${strTrend.subject_revpar_usd.toFixed(2)}` : '—' },
              ].map((mm) => (
                <div key={mm.label} className="px-5 py-4">
                  <div className="text-[10px] uppercase tracking-wide text-ink-500">{mm.label} <span className="text-ink-400 normal-case">· subject</span></div>
                  <div className="text-[20px] font-semibold text-ink-900 tabular-nums mt-0.5">{mm.value}</div>
                </div>
              ))}
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-border bg-brand-50/30">
              <div className="text-[12px] text-ink-700 max-w-xl">
                <span className="font-semibold">Drive Year-1 from STR rates.</span>{' '}
                {strSeeded
                  ? 'The model is using these STR market rates for Year-1 occupancy & ADR.'
                  : 'By default Year-1 uses the T-12 actuals. Switch to these STR market rates instead.'}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {strRunning && (
                  <span className="inline-flex items-center gap-1.5 text-[11.5px] text-brand-700">
                    <Loader2 size={12} className="animate-spin" /> Re-modeling…
                  </span>
                )}
                <Button variant={strSeeded ? 'secondary' : 'primary'} size="sm" onClick={toggleStrSeed} disabled={strRunning}>
                  {strSeeded ? 'Revert to T-12 actuals' : 'Use STR rates in the model'}
                </Button>
              </div>
            </div>
            {strTrend.compset && strTrend.compset.length > 0 && (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="text-ink-500 text-[10px] uppercase tracking-wide border-b border-border">
                      <th className="text-left font-semibold px-5 py-2">Comp set</th>
                      <th className="text-right font-semibold px-3 py-2">Keys</th>
                      <th className="text-right font-semibold px-3 py-2">Occupancy</th>
                      <th className="text-right font-semibold px-3 py-2">ADR</th>
                      <th className="text-right font-semibold px-5 py-2">RevPAR</th>
                    </tr>
                  </thead>
                  <tbody>
                    {derivedComp && (
                      // Blended comp-set benchmark FIRST — it's the real, analyst-facing
                      // number (recovered from the STR indices). Per-property performance is
                      // anonymized, so leading with the aggregate keeps the table from reading
                      // as a wall of blanks.
                      <tr className="border-b-2 border-brand-300 bg-brand-50/60">
                        <td className="px-5 py-3 font-semibold text-ink-900">
                          Comp Set
                          <span className="ml-1.5 rounded bg-brand-100 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-brand-700">blended</span>
                        </td>
                        <td className="px-3 py-3 text-right tabular-nums text-ink-800">
                          {compKeyCount ?? '—'}
                        </td>
                        <td className="px-3 py-3 text-right tabular-nums font-semibold text-ink-900">{derivedComp.occ != null ? `${derivedComp.occ.toFixed(1)}%` : '—'}</td>
                        <td className="px-3 py-3 text-right tabular-nums font-semibold text-ink-900">{derivedComp.adr != null ? `$${derivedComp.adr.toFixed(0)}` : '—'}</td>
                        <td className="px-5 py-3 text-right tabular-nums font-semibold text-ink-900">{derivedComp.revpar != null ? `$${derivedComp.revpar.toFixed(0)}` : '—'}</td>
                      </tr>
                    )}
                    {derivedComp && (
                      <tr>
                        <td colSpan={5} className="px-5 pt-2.5 pb-1 text-[10px] font-medium uppercase tracking-wide text-ink-400">
                          Comp-set members · per-property performance not published
                        </td>
                      </tr>
                    )}
                    {strTrend.compset.map((c, i) => {
                      // When per-property performance is anonymized (the common case), mute the
                      // empty cells to a faint dash so they recede instead of reading as "missing".
                      const perf = (v: string | null, cls = '') =>
                        v != null
                          ? <span className={cn('tabular-nums text-ink-700', cls)}>{v}</span>
                          : <span className="text-ink-300">—</span>;
                      return (
                        <tr key={`${c.name}-${i}`} className="border-b border-border/60">
                          <td className="px-5 py-2 text-ink-800">{c.name}</td>
                          <td className="px-3 py-2 text-right tabular-nums text-ink-700">{c.keys ?? '—'}</td>
                          <td className="px-3 py-2 text-right">{perf(c.occupancy_pct != null ? `${occPct(c.occupancy_pct).toFixed(1)}%` : null)}</td>
                          <td className="px-3 py-2 text-right">{perf(c.adr_usd != null ? `$${c.adr_usd.toFixed(0)}` : null)}</td>
                          <td className="px-5 py-2 text-right">{perf(c.revpar_usd != null ? `$${c.revpar_usd.toFixed(0)}` : null)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                {strTrend.compset.length > 0
                  && strTrend.compset.every((c) => c.occupancy_pct == null && c.adr_usd == null && c.revpar_usd == null) && (
                  <div className="px-5 py-2.5 border-t border-border text-[11px] text-ink-500 leading-relaxed">
                    {derivedComp ? (
                      <>
                        STR / CoStar anonymize <span className="font-medium">per-property</span> performance, so the
                        individual rows show key counts only. The <span className="font-medium text-ink-700">Comp Set · blended</span> row
                        is the aggregate the subject is benchmarked against — recovered from the report&apos;s STR indices
                        (MPI&nbsp;occupancy · ARI&nbsp;rate · RGI&nbsp;RevPAR) against the subject shown above.
                      </>
                    ) : (
                      <>
                        STR / CoStar reports anonymize competitor performance — per-property Occupancy / ADR / RevPAR
                        isn&apos;t published, so only key counts appear here. The subject&apos;s own performance is shown above.
                      </>
                    )}
                  </div>
                )}
              </div>
            )}
          </Card>

            {/* §5 — Index summary (MPI / ARI / RGI); detail lives on Index Analysis */}
            <MarketIndexSummary strTrend={strTrend} />

            {/* §6 & §7 — supply pipeline + demand drivers (need a submarket feed) */}
            <PendingMarketCard
              title="Supply Pipeline & Demand Drivers"
              items={[
                { label: 'Supply Pipeline', note: 'Upcoming hotels / rooms, development status, expected opening — populates from a CoStar submarket supply report.' },
                { label: 'Demand Drivers', note: 'Key market demand generators — populates from submarket demand data where supported.' },
              ]}
            />
          </div>
        ) : (
          <Card className="p-16 text-center">
            <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
              <MapPinned size={20} className="text-ink-400" />
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900">No market data yet</h3>
            {submarketLabel && (
              <p className="text-[12px] text-ink-700 mt-1.5 font-medium">{submarketLabel}</p>
            )}
            <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
              We don&apos;t have benchmark data for this submarket yet. Open the
              <span className="font-medium"> Data Library</span> to add it (paste in an STR report or
              attach a saved market).
            </p>
            <Link href="/data-library?tab=market" className="inline-block mt-4">
              <Button variant="primary" size="sm">Open Data Library</Button>
            </Link>
          </Card>
        ))}

        {tab === 'Transaction Comps' && (
          <TransactionCompsSection
            workerComps={workerComps}
            dealId={dealId}
          />
        )}

        {tab === 'Index Analysis' && (
          <IndexAnalysisSection dealId={dealId} />
        )}
      </div>
    );
}

function fmtSalePrice(usd: number | null): string {
  if (usd == null) return '—';
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(1)}M`;
  if (usd >= 1_000) return `$${(usd / 1_000).toFixed(0)}K`;
  return `$${usd.toFixed(0)}`;
}

function fmtPerKey(usd: number | null): string {
  if (usd == null) return '—';
  if (usd >= 1_000_000) return `$${(usd / 1_000_000).toFixed(2)}M`;
  return `$${usd.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

function fmtCapRate(pct: number | null): string {
  if (pct == null) return '—';
  return `${pct.toFixed(1)}%`;
}

function fmtSaleDate(s: string | null): string {
  if (!s) return '—';
  // Worker may emit ISO date or a free-form string; show first 10 chars
  // when ISO, otherwise pass through trimmed.
  const trimmed = s.trim();
  return /^\d{4}-\d{2}-\d{2}/.test(trimmed) ? trimmed.slice(0, 10) : trimmed;
}

function TransactionCompsSection({
  workerComps,
  dealId,
}: {
  workerComps: TransactionCompsResult | null;
  dealId: string;
}) {
  // Live deal path. Empty result + worker connected = OM not yet
  // extracted. Null result = haven't fetched yet.
  if (!workerComps) {
    return (
      <Card className="p-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">
          Transaction Comparables
        </h3>
        <div className="text-[12px] text-ink-500 py-8 text-center">
          Loading transaction comps…
        </div>
      </Card>
    );
  }

  if (workerComps.comps.length === 0) {
    return (
      <Card className="p-5">
        <h3 className="text-[13px] font-semibold text-ink-900 mb-3">
          Transaction Comparables
        </h3>
        <div className="text-[12px] text-ink-500 py-8 text-center">
          {workerComps.note ??
            'No comparable sales extracted yet. Upload an OM with a Comparable Sales table to populate this view.'}
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-[13px] font-semibold text-ink-900">
          Transaction Comparables
        </h3>
        <div className="text-[10.5px] text-ink-500">
          {workerComps.comps.length} comp
          {workerComps.comps.length === 1 ? '' : 's'} from extracted OMs
        </div>
      </div>

      {/* Headline anchors — median $/key + median cap rate. The exit-cap
          conversation in the IC memo grounds on these two numbers. */}
      <div className="grid grid-cols-2 gap-3 mb-4">
        <Card className="p-3 bg-slate-50/50">
          <div className="text-[10px] text-ink-500 uppercase tracking-wide">
            Median $/Key
          </div>
          <div className="text-[18px] font-semibold tabular-nums mt-0.5 text-ink-900">
            {fmtPerKey(workerComps.median_price_per_key)}
          </div>
          <div className="text-[10.5px] text-ink-500 mt-0.5">
            Anchor for entry / exit valuation
          </div>
        </Card>
        <Card className="p-3 bg-slate-50/50">
          <div className="text-[10px] text-ink-500 uppercase tracking-wide">
            Median Cap Rate
          </div>
          <div className="text-[18px] font-semibold tabular-nums mt-0.5 text-ink-900">
            {fmtCapRate(workerComps.median_cap_rate_pct)}
          </div>
          <div className="text-[10.5px] text-ink-500 mt-0.5">
            Anchor for exit-cap rate selection
          </div>
        </Card>
      </div>

      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-ink-500 text-[10.5px] border-b border-border">
            <th className="text-left font-medium pb-2">Property</th>
            <th className="text-left font-medium pb-2">Market</th>
            <th className="text-left font-medium pb-2">Sale Date</th>
            <th className="text-right font-medium pb-2">Keys</th>
            <th className="text-right font-medium pb-2">Sale Price</th>
            <th className="text-right font-medium pb-2">$/Key</th>
            <th className="text-right font-medium pb-2">Cap Rate</th>
            <th className="text-left font-medium pb-2">Buyer</th>
          </tr>
        </thead>
        <tbody>
          {workerComps.comps.map((c, i) => {
            const citationHref =
              c.source_document_id && c.source_page
                ? `${workerUrl()}/deals/${dealId}/documents/${c.source_document_id}/download#page=${c.source_page}`
                : null;
            return (
              <tr key={`${c.name}-${i}`} className="border-b border-border/50">
                <td className="py-2 font-medium">
                  {citationHref ? (
                    <Link
                      href={citationHref}
                      target="_blank"
                      rel="noreferrer"
                      className="text-ink-900 hover:text-brand-700 underline-offset-2 hover:underline"
                    >
                      {c.name}
                    </Link>
                  ) : (
                    c.name
                  )}
                </td>
                <td className="text-ink-700">{c.market ?? '—'}</td>
                <td className="text-ink-700">{fmtSaleDate(c.sale_date)}</td>
                <td className="text-right tabular-nums">
                  {c.keys != null ? c.keys.toLocaleString() : '—'}
                </td>
                <td className="text-right tabular-nums">
                  {fmtSalePrice(c.sale_price_usd)}
                </td>
                <td className="text-right tabular-nums">
                  {fmtPerKey(c.price_per_key_usd)}
                </td>
                <td className="text-right tabular-nums">
                  {fmtCapRate(c.cap_rate_pct)}
                </td>
                <td className="text-ink-700">
                  {c.buyer_name ?? c.buyer_type ?? '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="text-[10.5px] text-ink-400 mt-2">
        Source: extracted from this deal&apos;s OM. Property names
        deep-link to the source page in the OM.
      </div>
    </Card>
  );
}
