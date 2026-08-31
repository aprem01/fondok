'use client';
import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import {
  FolderKanban, TrendingUp, Building2, Target, Plus,
  ArrowUpRight, Sparkles, AlertTriangle, Loader2, MapPin,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { api, isWorkerConnected } from '@/lib/api';
import type { PipelineResponse, PipelineDealRow, PipelineSummary } from '@/lib/api';
import { useCurrentUser } from '@/lib/auth';
import { fmtCurrency, cn } from '@/lib/format';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';
import { GettingStartedSidebar } from '@/components/help/GettingStartedSidebar';
import { GetStartedHero, useGetStartedHeroVisible } from '@/components/help/GetStartedHero';

type StatTone = 'default' | 'luxe';

const pct = (v: number | null | undefined) =>
  v == null ? '—' : `${(v * 100).toFixed(1)}%`;

function buildStats(s: PipelineSummary | null): Array<{
  label: string; value: string; sub: string; subTone?: 'green';
  icon: typeof FolderKanban; tone: StatTone; tip: string;
}> {
  const dealCount = s?.deal_count ?? 0;
  const withTarget = s?.deals_with_target_irr ?? 0;
  const met = s?.deals_meeting_target_irr ?? 0;
  return [
    {
      label: 'Active Deals', value: String(dealCount),
      sub: dealCount === 1 ? '1 in pipeline' : `${dealCount} in pipeline`, subTone: 'green',
      icon: FolderKanban, tone: 'default',
      tip: 'Hotel deals currently in your tenant that are not archived — the live pipeline the model is tracking.',
    },
    // Median IRR is the anchor metric — luxe treatment.
    {
      label: 'Median Levered IRR', value: pct(s?.median_irr),
      sub: s?.p25_irr != null && s?.p75_irr != null ? `${pct(s.p25_irr)}–${pct(s.p75_irr)} p25–p75` : '',
      icon: TrendingUp, tone: 'luxe',
      tip: 'Median annualized return on equity across every deal that has run the returns engine. The bracket shows the 25th–75th percentile spread.',
    },
    {
      label: 'Median $/Key', value: s?.median_per_key != null ? fmtCurrency(s.median_per_key) : '—',
      sub: '', icon: Building2, tone: 'default',
      tip: 'Median acquisition basis per hotel room across the pipeline — the yardstick for whether you are buying cheap or rich versus replacement cost.',
    },
    {
      label: 'Meeting Target', value: withTarget === 0 ? '—' : `${met}/${withTarget}`,
      sub: withTarget === 0 ? 'set a target IRR' : `deal${withTarget === 1 ? '' : 's'} clearing target`,
      icon: Target, tone: 'default',
      tip: 'How many deals with an analyst-set target IRR are currently clearing it. Set a target on a deal to populate this.',
    },
  ];
}

export default function DashboardPage() {
  const user = useCurrentUser();
  const firstName = (user.name || '').trim().split(/\s+/)[0] || '';
  const connected = isWorkerConnected();

  const [resp, setResp] = useState<PipelineResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(connected);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!connected) {
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    setLoading(true);
    setError(null);
    api.deals
      .pipeline({ sort: 'last_activity_desc', limit: 100 }, ctrl.signal)
      .then((r) => setResp(r))
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === 'AbortError') return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoading(false));
    return () => ctrl.abort();
  }, [connected]);

  const summary = resp?.summary ?? null;
  const stats = useMemo(() => buildStats(summary), [summary]);
  const recent: PipelineDealRow[] = useMemo(
    () => (resp?.deals ?? []).slice(0, 5),
    [resp],
  );
  const dealCount = summary?.deal_count ?? 0;
  // A brand-new user (worker connected, load done, zero deals) can get the
  // guided first-run hero instead of a wall of empty KPIs — but it's opt-out:
  // hidden once dismissed or when coach marks are off, falling back to the
  // normal welcome.
  const isNewUser = connected && !loading && !error && dealCount === 0;
  const heroVisible = useGetStartedHeroVisible();
  const showHero = isNewUser && heroVisible;

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <GettingStartedSidebar />
      <PageHeader
        eyebrow={connected ? `Portfolio · ${dealCount} active deal${dealCount === 1 ? '' : 's'}` : 'Portfolio'}
        title="Dashboard"
        subtitle={
          firstName
            ? `Welcome back, ${firstName}. Here's your portfolio overview.`
            : "Here's your portfolio overview."
        }
        action={
          <Link href="/projects/new" data-tour="new-deal">
            <Button variant="primary"><Plus size={14} /> New Project</Button>
          </Link>
        }
      />

      {showHero && <GetStartedHero />}

      {!showHero && (
        <IntroCard
          dismissKey="dashboard-overview"
          title="Welcome to your portfolio"
          body={
            <>
              The numbers up top summarize every hotel deal in your pipeline — how many
              you have in flight, the median return, your typical basis per key, and how many
              are clearing their target. Click any deal below to open its full underwriting model.
            </>
          }
        />
      )}

      {error && (
        <Card className="p-5 mb-5 border-danger-500/30 bg-danger-50">
          <div className="flex items-start gap-3">
            <AlertTriangle size={16} className="text-danger-700 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-[13px] font-semibold text-danger-700">Couldn&rsquo;t load your portfolio</div>
              <p className="text-[12px] text-danger-700/80 mt-1">{error}</p>
            </div>
          </div>
        </Card>
      )}

      {/* Stat cards — Bloomberg-cell rhythm. */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {stats.map((s) => {
          const Icon = s.icon;
          const isLuxe = s.tone === 'luxe';
          return (
            <Card key={s.label} tone={s.tone} className={isLuxe ? 'p-5 pl-6' : 'p-5'}>
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <MetricLabel label={s.label} tip={s.tip} eyebrow />
                  <div className="text-display-lg text-ink-900 mt-2 tabular-nums font-display">
                    {loading ? <span className="inline-block h-7 w-16 rounded bg-ink-300/30 animate-pulse align-middle" /> : s.value}
                  </div>
                  {s.sub && !loading && (
                    <div className={`text-[11.5px] mt-1.5 tabular-nums ${s.subTone === 'green' ? 'text-success-700' : 'text-ink-500'}`}>
                      {s.sub}
                    </div>
                  )}
                </div>
                <div className={'w-8 h-8 rounded-md border flex items-center justify-center flex-shrink-0 ' + (isLuxe ? 'border-gold-200 text-gold-500' : 'border-ink-200 text-ink-500')}>
                  <Icon size={14} strokeWidth={1.75} />
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      <div className="grid grid-cols-3 gap-4">
        {/* Recent Projects */}
        <Card className="col-span-2">
          <div className="px-5 py-4 border-b hairline flex items-center justify-between">
            <h2 className="font-display text-[14px] font-semibold text-ink-900 tracking-tight">Recent Projects</h2>
            <Link href="/projects" className="text-[12px] text-brand-700 hover:text-brand-900 font-medium inline-flex items-center gap-1">
              View All <ArrowUpRight size={12} />
            </Link>
          </div>

          {loading ? (
            <div className="p-5 space-y-3">
              {[0, 1, 2].map((i) => (
                <div key={i} className="flex items-center gap-3 animate-pulse">
                  <div className="w-9 h-9 rounded-md bg-ink-300/30" />
                  <div className="flex-1">
                    <div className="h-3 bg-ink-300/30 rounded w-1/3 mb-1.5" />
                    <div className="h-2.5 bg-ink-300/20 rounded w-1/4" />
                  </div>
                </div>
              ))}
            </div>
          ) : recent.length === 0 ? (
            <div className="px-5 py-12 text-center">
              <Building2 size={32} className="text-ink-400 mx-auto mb-3" strokeWidth={1.5} />
              <div className="text-[13.5px] font-semibold text-ink-900">No deals yet</div>
              <p className="text-[12px] text-ink-500 mt-1 max-w-sm mx-auto leading-relaxed">
                A deal is one hotel acquisition you&apos;re evaluating. Create your first one, then
                drop in the offering memorandum and T-12 to start underwriting.
              </p>
              <div className="mt-4">
                <Link href="/projects/new">
                  <Button variant="primary" size="sm"><Plus size={13} /> New Project</Button>
                </Link>
              </div>
            </div>
          ) : (
            <div>
              {recent.map((d, i) => (
                <Link
                  key={d.deal_id}
                  href={`/projects/${d.deal_id}`}
                  data-tour={i === 0 ? 'project-card' : undefined}
                  className={cn(
                    'flex items-center gap-3 px-5 py-3.5 hover:bg-ink-100 transition-colors',
                    i < recent.length - 1 && 'border-b hairline',
                  )}
                >
                  <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
                    <Building2 size={16} className="text-brand-500" strokeWidth={1.75} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <div className="text-[13.5px] font-medium text-ink-900 truncate">{d.name}</div>
                      <StatusBadge value={d.status || 'Draft'} />
                    </div>
                    <div className="flex items-center gap-1 text-[12px] text-ink-500 mt-0.5">
                      <MapPin size={10} /> {d.city || '—'}{d.keys ? ` · ${d.keys} keys` : ''}
                    </div>
                  </div>
                  <div className="text-right w-20">
                    <div className="text-[10px] text-ink-500 uppercase tracking-wide">IRR</div>
                    <div className="text-[13px] font-semibold tabular-nums text-ink-900">{pct(d.levered_irr)}</div>
                  </div>
                  <div className="text-right w-24">
                    <div className="text-[10px] text-ink-500 uppercase tracking-wide">$/Key</div>
                    <div className="text-[13px] font-semibold tabular-nums text-ink-900">
                      {d.price_per_key != null ? fmtCurrency(d.price_per_key) : '—'}
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </Card>

        {/* Right rail */}
        <div className="space-y-4">
          <Card className="p-5">
            <h3 className="font-display text-[13.5px] font-semibold text-ink-900 mb-4 tracking-tight">Pipeline health</h3>
            {loading ? (
              <div className="space-y-2">
                <div className="h-3 bg-ink-300/20 rounded animate-pulse" />
                <div className="h-3 bg-ink-300/20 rounded w-2/3 animate-pulse" />
              </div>
            ) : summary && Object.keys(summary.deals_by_state || {}).length > 0 ? (
              <div className="space-y-2.5">
                {Object.entries(summary.deals_by_state).map(([state, n]) => (
                  <div key={state} className="flex items-center justify-between text-[12px]">
                    <span className="text-ink-600 capitalize">{state.toLowerCase()}</span>
                    <span className="font-semibold tabular-nums text-ink-900">{n}</span>
                  </div>
                ))}
                {summary.median_cap_rate != null && (
                  <div className="flex items-center justify-between text-[12px] pt-2 border-t border-border">
                    <span className="text-ink-600">Median exit cap</span>
                    <span className="font-semibold tabular-nums text-ink-900">{pct(summary.median_cap_rate)}</span>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-center py-6">
                <div className="text-[12px] text-ink-500">No deals in the pipeline yet</div>
              </div>
            )}
          </Card>
          <Card className="p-5">
            <div className="flex items-center gap-2 mb-4">
              <Sparkles size={14} className="text-gold-500" strokeWidth={1.75} />
              <h3 className="font-display text-[13.5px] font-semibold text-ink-900 tracking-tight">AI Insights</h3>
            </div>
            <div className="text-center py-6">
              <div className="text-[12px] text-ink-500 leading-relaxed">
                AI-powered insights will appear here once you have active projects with uploaded documents.
              </div>
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}
