'use client';
import { useState, useEffect } from 'react';
import {
  Sparkles, ArrowRight, RefreshCw, ShieldCheck, FileSearch,
  TrendingUp, Layers, DollarSign, FileText,
} from 'lucide-react';
import dynamic from 'next/dynamic';
import TabLoadingSkeleton from './TabLoadingSkeleton';

const CostPanel = dynamic(() => import('./CostPanel'), {
  loading: () => <TabLoadingSkeleton rows={4} />,
  ssr: false,
});
const MemoStream = dynamic(() => import('./MemoStream'), {
  loading: () => <TabLoadingSkeleton rows={6} />,
  ssr: false,
});
import { useSearchParams, useRouter, useParams } from 'next/navigation';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { kimptonAnalysis } from '@/lib/mockData';
import { fmtCurrency, cn } from '@/lib/format';
import VarianceTab from './VarianceTab';
import { criticalCount } from '@/lib/varianceData';

const sensTabs = ['ADR Sensitivity', 'Occupancy Sensitivity', 'Exit Cap Rate'];

const sensData: Record<string, { irr: number[]; coc: number[]; mult: number[] }> = {
  'ADR Sensitivity':       { irr: [16.2, 19.8, 23.48, 27.1, 30.8], coc: [3.2, 3.9, 4.6, 5.3, 6.0], mult: [1.74, 1.92, 2.12, 2.32, 2.52] },
  'Occupancy Sensitivity': { irr: [14.8, 19.1, 23.48, 27.7, 32.0], coc: [2.8, 3.7, 4.6, 5.5, 6.4], mult: [1.65, 1.88, 2.12, 2.36, 2.60] },
  'Exit Cap Rate':         { irr: [29.4, 26.4, 23.48, 20.6, 17.7], coc: [4.6, 4.6, 4.6, 4.6, 4.6], mult: [2.42, 2.27, 2.12, 1.97, 1.82] },
};

// Cache-hit badge: hits the worker's /observability/cache-stats once on
// mount. Worker URL is optional — when NEXT_PUBLIC_WORKER_URL is unset
// the badge silently renders as "—" so dev preview deploys still work.
type CacheStats = {
  cache_hit_rate: number;
  samples: number;
  totals: {
    input_tokens: number;
    cache_read_tokens: number;
    cache_creation_tokens: number;
    output_tokens: number;
    estimated_cost_usd: number;
  };
};

function CacheHitBadge() {
  const [stats, setStats] = useState<CacheStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const base = process.env.NEXT_PUBLIC_WORKER_URL;
    if (!base) {
      setError('worker url not configured');
      return;
    }
    const controller = new AbortController();
    fetch(`${base.replace(/\/$/, '')}/observability/cache-stats?n=100`, {
      signal: controller.signal,
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: CacheStats) => setStats(data))
      .catch((e) => {
        if (e?.name !== 'AbortError') setError(String(e?.message || e));
      });
    return () => controller.abort();
  }, []);

  if (error || !stats || stats.samples === 0) {
    return (
      <span title={error || 'no cache data yet'} className="inline-flex">
        <Badge tone="gray">Cache hit: —</Badge>
      </span>
    );
  }
  const pct = Math.round(stats.cache_hit_rate * 1000) / 10;
  const totals = stats.totals;
  const tooltip =
    `Last ${stats.samples} model calls\n` +
    `Cache reads: ${totals.cache_read_tokens.toLocaleString()} tokens\n` +
    `Cache writes: ${totals.cache_creation_tokens.toLocaleString()} tokens\n` +
    `Plain input: ${totals.input_tokens.toLocaleString()} tokens\n` +
    `Output: ${totals.output_tokens.toLocaleString()} tokens\n` +
    `Estimated spend: $${totals.estimated_cost_usd.toFixed(4)}`;
  return (
    <span title={tooltip} className="inline-flex">
      <Badge tone={pct >= 80 ? 'green' : pct >= 30 ? 'amber' : 'gray'}>
        Cache hit: {pct.toFixed(1)}%
      </Badge>
    </span>
  );
}

type SubTab = 'summary' | 'memo' | 'risks' | 'variance' | 'sensitivity' | 'scenarios' | 'cost';

const subTabs: { id: SubTab; label: string; icon: typeof Sparkles; badge?: string }[] = [
  { id: 'summary',     label: 'AI Summary',     icon: Sparkles },
  { id: 'memo',        label: 'IC Memo',        icon: FileText, badge: 'LIVE' },
  { id: 'risks',       label: 'Risks',          icon: ShieldCheck },
  { id: 'variance',    label: 'Broker Variance', icon: FileSearch, badge: 'NEW' },
  { id: 'sensitivity', label: 'Sensitivity',    icon: TrendingUp },
  { id: 'scenarios',   label: 'Scenarios',      icon: Layers },
  { id: 'cost',        label: 'Cost',           icon: DollarSign },
];

export default function AnalysisTab() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectId = Number(params?.id);

  // Sub-tab is driven by ?sub= so DataRoom / header pills can deep-link.
  const requested = (searchParams.get('sub') as SubTab | null) || 'summary';
  const [sub, setSub] = useState<SubTab>(
    subTabs.some(t => t.id === requested) ? requested : 'summary',
  );
  const [sensTab, setSensTab] = useState('ADR Sensitivity');
  const a = kimptonAnalysis;

  useEffect(() => {
    const next = (searchParams.get('sub') as SubTab | null) || 'summary';
    if (subTabs.some(t => t.id === next) && next !== sub) {
      setSub(next);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  const setSubTab = (id: SubTab) => {
    setSub(id);
    const url = `/projects/${projectId}?tab=analysis&sub=${id}`;
    router.replace(url, { scroll: false });
  };

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-[15px] font-semibold text-ink-900">Analysis</h2>
            <p className="text-[12.5px] text-ink-500 mt-1">
              AI-generated investment summary, risk assessment, broker variance detection,
              sensitivity analysis, and scenario comparison.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <CacheHitBadge />
            <Badge tone="green">✓ Analysis Complete</Badge>
          </div>
        </div>
        <div className="flex items-center gap-1 border-b border-border -mb-5 px-0 -mx-1">
          {subTabs.map(t => {
            const Icon = t.icon;
            const isActive = sub === t.id;
            const isVariance = t.id === 'variance';
            return (
              <button
                key={t.id}
                onClick={() => setSubTab(t.id)}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-2.5 text-[12.5px] border-b-2 -mb-px transition-colors whitespace-nowrap',
                  isActive
                    ? 'border-brand-500 text-brand-700 font-medium'
                    : 'border-transparent text-ink-500 hover:text-ink-900',
                )}
              >
                <Icon size={13} />
                {t.label}
                {isVariance && criticalCount > 0 && (
                  <span className="ml-1 inline-flex items-center justify-center w-4 h-4 text-[9.5px] font-semibold rounded-full bg-danger-500 text-white tabular-nums">
                    {criticalCount}
                  </span>
                )}
                {t.badge && !isVariance && (
                  <span className="ml-1 text-[9.5px] font-semibold tracking-wide text-brand-700 bg-brand-50 px-1 rounded">
                    {t.badge}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </Card>

      {sub === 'summary' && (
        <Card className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles size={15} className="text-brand-500" />
            <h3 className="text-[14px] font-semibold text-ink-900">AI Investment Summary</h3>
          </div>
          <div className="space-y-3 text-[12.5px] text-ink-700 leading-relaxed">
            {a.summary.map((p, i) => (
              <p key={i} dangerouslySetInnerHTML={{ __html: p
                .replace(/(\$36\.4M|\$276K\/key|24\.5% levered IRR|22% discount|14% ADR premium)/g,
                  '<span class="font-semibold text-brand-700">$1</span>')
              }} />
            ))}
          </div>
          <div className="flex items-center gap-2 mt-4">
            <Button variant="primary" size="sm" onClick={() => setSubTab('memo')}>
              Generate IC Memo <ArrowRight size={12} />
            </Button>
            <Button variant="secondary" size="sm"><RefreshCw size={12} /> Regenerate Summary</Button>
            <Button variant="secondary" size="sm" onClick={() => setSubTab('variance')}>
              <FileSearch size={12} /> Review {criticalCount} Critical Variance Flags
            </Button>
          </div>
        </Card>
      )}

      {sub === 'memo' && <MemoStream dealId={String(projectId)} />}

      {sub === 'risks' && (
        <div className="grid grid-cols-3 gap-5">
          <Card className="col-span-2 p-5">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2">
                <ShieldCheck size={15} className="text-success-500" />
                <h3 className="text-[14px] font-semibold text-ink-900">Risk Assessment</h3>
              </div>
              <Badge tone="green">Low Risk</Badge>
            </div>
            <div className="space-y-3">
              {a.risks.map(r => (
                <div key={r.name}>
                  <div className="flex justify-between text-[12px] mb-1">
                    <span className={r.name === 'Overall Risk Score' ? 'font-semibold text-ink-900' : 'text-ink-700'}>
                      {r.name}
                    </span>
                    <div className="flex items-center gap-2">
                      <Badge tone={r.tier === 'Low Risk' ? 'green' : 'amber'}>{r.tier}</Badge>
                      <span className="font-medium tabular-nums w-8 text-right">{r.score}</span>
                    </div>
                  </div>
                  <div className="h-1.5 bg-ink-300/30 rounded-full overflow-hidden">
                    <div className={cn('h-full', r.tier === 'Low Risk' ? 'bg-success-500' : 'bg-warn-500')} style={{ width: `${r.score}%` }} />
                  </div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-4">Key Insights</h3>
            <div className="space-y-3">
              {a.insights.map(i => (
                <div key={i.title} className="border border-border rounded-md p-3">
                  <div className="text-[12px] font-semibold text-ink-900 mb-1">{i.title}</div>
                  <p className="text-[11.5px] text-ink-500 leading-relaxed">{i.body}</p>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {sub === 'variance' && <VarianceTab />}

      {sub === 'sensitivity' && (
        <Card className="p-5">
          <h3 className="text-[14px] font-semibold text-ink-900 mb-3">Sensitivity Analysis</h3>
          <div className="flex items-center gap-1 mb-4 border-b border-border">
            {sensTabs.map(t => (
              <button key={t} onClick={() => setSensTab(t)}
                className={cn(
                  'px-3 py-2 text-[12px] border-b-2 -mb-px',
                  sensTab === t ? 'border-brand-500 text-brand-700 font-medium' : 'border-transparent text-ink-500 hover:text-ink-900'
                )}>{t}</button>
            ))}
          </div>
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[11px] border-b border-border">
                <th className="text-left font-medium pb-2">Change</th>
                <th className="text-right font-medium pb-2">Levered IRR</th>
                <th className="text-right font-medium pb-2">Cash-on-Cash</th>
                <th className="text-right font-medium pb-2">Equity Multiple</th>
              </tr>
            </thead>
            <tbody>
              {['-10%', '-5%', 'Base', '+5%', '+10%'].map((c, i) => (
                <tr key={c} className={cn('border-b border-border/50', c === 'Base' && 'bg-brand-50 font-semibold')}>
                  <td className="py-2">{c}</td>
                  <td className="text-right tabular-nums">{sensData[sensTab].irr[i].toFixed(2)}%</td>
                  <td className="text-right tabular-nums">{sensData[sensTab].coc[i].toFixed(1)}%</td>
                  <td className="text-right tabular-nums">{sensData[sensTab].mult[i].toFixed(2)}x</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {sub === 'cost' && <CostPanel />}

      {sub === 'scenarios' && (
        <Card className="p-5">
          <h3 className="text-[14px] font-semibold text-ink-900 mb-4">Scenario Comparison</h3>
          <div className="grid grid-cols-3 gap-4">
            {a.scenarios.map(s => {
              const tone = s.name.includes('Down') ? 'border-danger-500 bg-danger-50' :
                           s.name.includes('Up') ? 'border-success-500 bg-success-50' :
                           'border-brand-500 bg-brand-50';
              return (
                <Card key={s.name} className={cn('p-4 border-2', tone)}>
                  <div className="flex items-center justify-between mb-3">
                    <div className="text-[13px] font-semibold text-ink-900">{s.name}</div>
                    <Badge tone="gray">{s.probability}% probability</Badge>
                  </div>
                  <div className="space-y-2 text-[12.5px]">
                    <Row k="IRR" v={`${s.irr.toFixed(2)}%`} />
                    <Row k="Cash-on-Cash" v={`${s.coc.toFixed(1)}%`} />
                    <Row k="Equity Multiple" v={`${s.multiple.toFixed(2)}x`} />
                    <Row k="Exit Value" v={fmtCurrency(s.exitValue, { compact: true })} />
                  </div>
                </Card>
              );
            })}
          </div>
        </Card>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between py-1 border-b border-border/30 last:border-0">
      <span className="text-ink-500">{k}</span>
      <span className="font-medium tabular-nums text-ink-900">{v}</span>
    </div>
  );
}
