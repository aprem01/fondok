'use client';
import { useState, useEffect } from 'react';
import {
  Sparkles, ArrowRight, RefreshCw, ShieldCheck, FileSearch,
  TrendingUp, Layers, DollarSign, FileText, Eye, AlertTriangle,
  AlertCircle, Info,
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
import {
  kimptonAnalysis,
  kimptonCriticFindings,
  kimptonCriticSummary,
  type KimptonCriticFinding,
  type KimptonCriticSeverity,
} from '@/lib/mockData';
import { fmtCurrency, cn } from '@/lib/format';
import VarianceTab from './VarianceTab';
import { criticalCount } from '@/lib/varianceData';
import { Citation, type CitationData } from '@/components/citations/Citation';
import { IntroCard } from '@/components/help/IntroCard';
import { Term } from '@/components/help/Term';
import { GLOSSARY } from '@/lib/glossary';

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

type SubTab = 'summary' | 'memo' | 'risks' | 'variance' | 'critic' | 'sensitivity' | 'scenarios' | 'cost';

const subTabs: { id: SubTab; label: string; icon: typeof Sparkles }[] = [
  { id: 'summary',     label: 'AI Summary',     icon: Sparkles },
  { id: 'memo',        label: 'IC Memo',        icon: FileText },
  { id: 'risks',       label: 'Risks',          icon: ShieldCheck },
  { id: 'variance',    label: 'Broker Variance', icon: FileSearch },
  { id: 'critic',      label: 'Critic Review',  icon: Eye },
  { id: 'sensitivity', label: 'Sensitivity',    icon: TrendingUp },
  { id: 'scenarios',   label: 'Scenarios',      icon: Layers },
  { id: 'cost',        label: 'Cost',           icon: DollarSign },
];

export default function AnalysisTab() {
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const projectId = Number(params?.id);
  // Only the Kimpton Angler mock deal (id 7) ships with cached analysis data.
  // Every other deal — including any worker-backed UUID — gets an empty
  // state nudge until we wire live analysis fetching.
  const rawId = (params?.id as string | undefined) ?? String(projectId);
  const hasCannedAnalysis = /^\d+$/.test(rawId) && Number(rawId) === 7;

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

  // Sub-tab specific intro cards. Variance gets the prominent "why this
  // matters" treatment (amber tone). Cost and Memo get their own framing.
  const subIntro: Partial<Record<SubTab, { title: string; body: React.ReactNode; tone?: 'default' | 'amber' }>> = {
    variance: {
      title: 'Why broker variance matters',
      tone: 'amber',
      body: (
        <>
          Brokers always project a rosier picture than reality. Their pro forma assumes higher
          occupancy, lower expenses, more revenue growth. The AI compares every line of their
          pitch deck to the actual T-12 and flags every gap.
          <span className="font-semibold"> Critical</span> flags are deal-breakers;
          <span className="font-semibold"> warnings</span> are negotiation points.
        </>
      ),
    },
    cost: {
      title: 'AI spend on this deal',
      body: (
        <>
          How much you&apos;ve spent on AI for this deal. Each underwriting run costs around $0.05
          in Anthropic API calls. The budget cap of $20/deal exists so a runaway loop never
          costs more than a coffee.
        </>
      ),
    },
    memo: {
      title: 'The Investment Committee Memo',
      body: (
        <>
          The deliverable you take to your IC. Generated by Claude Opus reading every extracted
          field and engine output, with citations back to the source documents. You can
          regenerate or download it as a PDF from the Export tab.
        </>
      ),
    },
    critic: {
      title: 'Cross-field Critic Review',
      body: (
        <>
          A second-pass agent that catches stories spanning multiple fields — e.g.,
          &quot;NOI margin claimed at 38% but the labor and insurance assumptions don&apos;t add up.&quot;
          Runs after the per-field Variance pass to surface narrative-level issues.
        </>
      ),
    },
    sensitivity: {
      title: 'Sensitivity Analysis',
      body: (
        <>
          How much do the headline returns move when ADR, occupancy, or exit cap rate shift?
          A flat curve means the deal is robust; a steep one means small changes flip the IRR.
        </>
      ),
    },
    scenarios: {
      title: 'Scenario Comparison',
      body: (
        <>
          Three side-by-side cases — downside, base, upside — with probability weights so you
          can show the IC the full distribution of outcomes, not just the broker&apos;s base case.
        </>
      ),
    },
    risks: {
      title: 'Risk Assessment',
      body: (
        <>
          A categorized risk score across market, brand, debt, execution, and exit. Each
          category gets a 0–100 score and an explanation.
        </>
      ),
    },
  };

  return (
    <div className="space-y-5">
      <IntroCard
        dismissKey="analysis-intro"
        title="The Analysis view"
        body={
          <>
            The AI&apos;s read on the deal. Investment thesis, risk assessment, scenario comparison,
            and a check of whether the broker&apos;s pro forma reconciles with the actuals. Use the
            sub-tabs below to drill in.
          </>
        }
      />

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
              </button>
            );
          })}
        </div>
      </Card>

      {subIntro[sub] && (
        <IntroCard
          dismissKey={`analysis-sub-${sub}`}
          title={subIntro[sub]!.title}
          body={subIntro[sub]!.body}
          tone={subIntro[sub]!.tone}
        />
      )}

      {sub === 'summary' && !hasCannedAnalysis && (
        <Card className="p-8 text-center">
          <div className="w-12 h-12 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
            <Sparkles size={20} className="text-brand-500" />
          </div>
          <h3 className="text-[14px] font-semibold text-ink-900 mb-1">No analysis yet</h3>
          <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
            Generate the IC memo to see AI investment summary, risk assessment, and variance flags.
          </p>
          <div className="mt-4">
            <Button variant="primary" size="sm" onClick={() => setSubTab('memo')}>
              <Sparkles size={12} /> Generate IC Memo
            </Button>
          </div>
        </Card>
      )}

      {sub === 'summary' && hasCannedAnalysis && (
        <Card className="p-5">
          <div className="flex items-center gap-2 mb-3">
            <Sparkles size={15} className="text-brand-500" />
            <h3 className="text-[14px] font-semibold text-ink-900">AI Investment Summary</h3>
          </div>
          <div className="space-y-3 text-[12.5px] text-ink-700 leading-relaxed">
            {a.summary.map((p, i) => (
              <p key={i}>{renderSummaryParagraph(p)}</p>
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

      {sub === 'variance' && (
        hasCannedAnalysis ? (
          <VarianceTab />
        ) : (
          <Card className="p-8 text-center">
            <div className="w-12 h-12 mx-auto rounded-lg bg-warn-50 flex items-center justify-center mb-3">
              <FileSearch size={20} className="text-warn-700" />
            </div>
            <h3 className="text-[14px] font-semibold text-ink-900 mb-1">No variance flags</h3>
            <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
              Either you haven&apos;t uploaded broker proforma + T-12, or extraction is still running.
              Variance detection runs automatically once both documents are extracted.
            </p>
          </Card>
        )
      )}

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

      {sub === 'critic' && (
        hasCannedAnalysis ? (
          <CriticReview findings={kimptonCriticFindings} summary={kimptonCriticSummary} />
        ) : (
          <Card className="p-8 text-center">
            <div className="w-12 h-12 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
              <Eye size={20} className="text-brand-500" />
            </div>
            <h3 className="text-[14px] font-semibold text-ink-900 mb-1">
              No cross-field issues detected
            </h3>
            <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
              The Critic agent runs after the Variance pass. Once both broker proforma
              and T-12 documents are extracted, Fondok will surface narrative issues
              spanning multiple fields here.
            </p>
          </Card>
        )
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

// ---------- AI Summary inline citations ------------------------------
// The Kimpton summary paragraphs reference specific extracted figures.
// Each phrase below maps to the source document/page that grounds it,
// so the analyst can click any number and see exactly where it came
// from. When live memo data exists this would come off the streamed
// citations array — until then this is the canned mapping.
const SUMMARY_CITATIONS: Record<string, CitationData> = {
  '$36.4M':              { documentId: 'kimpton-angler-om-2026',     documentName: 'Offering_Memorandum_Final.pdf', page: 6,  field: 'asking_price', excerpt: 'Asking price: $36,400,000' },
  '$276K/key':           { documentId: 'kimpton-angler-om-2026',     documentName: 'Offering_Memorandum_Final.pdf', page: 6,  field: 'price_per_key', excerpt: '$276,000 per key (132 keys)' },
  '24.5% levered IRR':   { documentId: 'kimpton-angler-om-2026',     documentName: 'Offering_Memorandum_Final.pdf', page: 41, field: 'returns.levered_irr', excerpt: 'Levered IRR projection: 24.5% over 5-year hold' },
  '22% discount':        { documentId: 'kimpton-angler-om-2026',     documentName: 'Offering_Memorandum_Final.pdf', page: 28, field: 'comp_set.price_per_key', excerpt: 'Submarket comp set average: $354K/key — basis represents a 22% discount' },
  '14% ADR premium':     { documentId: 'kimpton-angler-t12-2026q1',  documentName: 'T12_FinancialStatement.xlsx',   page: 1,  field: 'adr_premium_vs_comp', excerpt: 'Property ADR $385 vs comp-set ADR $338 (+14%)' },
};

const SUMMARY_PHRASE_RE =
  /(\$36\.4M|\$276K\/key|24\.5% levered IRR|22% discount|14% ADR premium)/g;

// Acronyms we wrap in <Term> tooltips inline. Word-boundary, case-sensitive
// match so "noise" and "branded" never trip the regex. Excludes ADR / IRR
// when they appear inside a citation phrase ("14% ADR premium",
// "24.5% levered IRR") — citation match runs first via SUMMARY_PHRASE_RE.
const TERM_RE = /\b(NOI|RevPAR|ADR|IRR|DSCR|LTV|LTC|GOP|FF&E|CoC|OpEx|PIP|STR|USALI|OM|T-12|T12|MOIC|GP|LP|SOFR|RGI|ARI|MPI)\b/g;

function wrapTermsInChunk(text: string, keyPrefix: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  // Reset since the regex is module-scoped with /g flag.
  TERM_RE.lastIndex = 0;
  while ((match = TERM_RE.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    const acronym = match[0];
    const def = GLOSSARY[acronym];
    if (def) {
      parts.push(
        <Term key={`${keyPrefix}-t-${match.index}`} tip={def}>
          {acronym}
        </Term>,
      );
    } else {
      parts.push(acronym);
    }
    lastIndex = match.index + acronym.length;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function renderSummaryParagraph(text: string): React.ReactNode[] {
  const parts = text.split(SUMMARY_PHRASE_RE);
  const out: React.ReactNode[] = [];
  parts.forEach((part, i) => {
    const cite = SUMMARY_CITATIONS[part];
    if (cite) {
      out.push(
        <Citation key={`c-${i}`} data={cite}>
          <span className="font-semibold text-brand-700">{part}</span>
        </Citation>,
      );
      return;
    }
    // Wrap any remaining acronyms in Term tooltips so 10th-graders can
    // hover any jargon and read the definition without leaving the page.
    wrapTermsInChunk(part, `p${i}`).forEach(n => out.push(n));
  });
  return out;
}

// ---------- Critic Review sub-tab ------------------------------------
// Renders the cross-field findings from the Critic agent. Each finding
// pairs a narrative paragraph with a rule_id chip, severity badge, the
// USALI fields it spans, and clickable page citations into the source
// document pane. Sorted by severity (CRITICAL → WARN → INFO).

const SEVERITY_RANK: Record<KimptonCriticSeverity, number> = {
  CRITICAL: 0,
  WARN: 1,
  INFO: 2,
};

const SEVERITY_TONE: Record<KimptonCriticSeverity, 'red' | 'amber' | 'blue'> = {
  CRITICAL: 'red',
  WARN: 'amber',
  INFO: 'blue',
};

const SEVERITY_BORDER: Record<KimptonCriticSeverity, string> = {
  CRITICAL: 'border-l-danger-500 bg-danger-50/40',
  WARN: 'border-l-warn-500 bg-warn-50/40',
  INFO: 'border-l-brand-500/50 bg-brand-50/30',
};

const SEVERITY_ICON: Record<KimptonCriticSeverity, typeof AlertTriangle> = {
  CRITICAL: AlertTriangle,
  WARN: AlertCircle,
  INFO: Info,
};

function CriticReview({
  findings,
  summary,
}: {
  findings: KimptonCriticFinding[];
  summary: string;
}) {
  if (findings.length === 0) {
    return (
      <Card className="p-8 text-center">
        <div className="w-12 h-12 mx-auto rounded-lg bg-success-50 flex items-center justify-center mb-3">
          <ShieldCheck size={20} className="text-success-500" />
        </div>
        <h3 className="text-[14px] font-semibold text-ink-900 mb-1">
          No cross-field issues detected
        </h3>
        <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
          The broker proforma is internally consistent. Fondok found no
          cross-field stories to surface beyond the per-field Variance pass.
        </p>
      </Card>
    );
  }

  const sorted = [...findings].sort(
    (a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity],
  );
  const counts = sorted.reduce(
    (acc, f) => {
      acc[f.severity] = (acc[f.severity] ?? 0) + 1;
      return acc;
    },
    { CRITICAL: 0, WARN: 0, INFO: 0 } as Record<KimptonCriticSeverity, number>,
  );

  return (
    <div className="space-y-4">
      <Card className="p-5 border-l-4 border-l-brand-500 bg-brand-50/40">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-lg bg-brand-50 flex items-center justify-center flex-shrink-0">
            <Eye size={16} className="text-brand-500" />
          </div>
          <div className="flex-1">
            <div className="flex items-center justify-between mb-1.5">
              <h3 className="text-[14px] font-semibold text-ink-900">
                Cross-field Critic Review
              </h3>
              <div className="flex items-center gap-1.5">
                {counts.CRITICAL > 0 && (
                  <Badge tone="red">{counts.CRITICAL} Critical</Badge>
                )}
                {counts.WARN > 0 && (
                  <Badge tone="amber">{counts.WARN} Warn</Badge>
                )}
                {counts.INFO > 0 && (
                  <Badge tone="blue">{counts.INFO} Info</Badge>
                )}
              </div>
            </div>
            <p className="text-[12.5px] text-ink-700 leading-relaxed">{summary}</p>
          </div>
        </div>
      </Card>

      <div className="space-y-3">
        {sorted.map((f) => {
          const Icon = SEVERITY_ICON[f.severity];
          return (
            <Card
              key={f.id}
              className={cn('p-5 border-l-4', SEVERITY_BORDER[f.severity])}
            >
              <div className="flex items-start gap-3">
                <div className="flex-shrink-0 mt-0.5">
                  <Icon
                    size={16}
                    className={cn(
                      f.severity === 'CRITICAL' && 'text-danger-500',
                      f.severity === 'WARN' && 'text-warn-700',
                      f.severity === 'INFO' && 'text-brand-500',
                    )}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <h4 className="text-[13px] font-semibold text-ink-900 leading-snug">
                      {f.title}
                    </h4>
                    <Badge tone={SEVERITY_TONE[f.severity]}>
                      {f.severity === 'CRITICAL'
                        ? 'Critical'
                        : f.severity === 'WARN'
                          ? 'Warn'
                          : 'Info'}
                    </Badge>
                  </div>

                  <div className="flex items-center gap-2 flex-wrap mb-2.5">
                    <span
                      className="inline-flex items-center px-2 py-0.5 rounded text-[10.5px] font-mono font-medium bg-ink-100 text-ink-700 border border-border"
                      title="USALI rule that grounds this finding"
                    >
                      {f.ruleId}
                    </span>
                    {f.citedFields.map((field) => (
                      <span
                        key={field}
                        className="inline-flex items-center px-1.5 py-0.5 rounded text-[10.5px] font-mono text-ink-500 bg-ink-50"
                      >
                        {field}
                      </span>
                    ))}
                  </div>

                  <p className="text-[12.5px] text-ink-700 leading-relaxed mb-3">
                    {f.narrative}
                  </p>

                  <div className="flex items-center gap-3 flex-wrap text-[11.5px]">
                    {f.citedPages.length > 0 && f.citedDocumentId && (
                      <div className="flex items-center gap-1.5">
                        <span className="text-ink-500">Sources:</span>
                        {f.citedPages.map((page) => (
                          <Citation
                            key={page}
                            data={{
                              documentId: f.citedDocumentId!,
                              documentName: f.citedDocumentName,
                              page,
                              field: f.citedFields[0],
                            }}
                          />
                        ))}
                      </div>
                    )}
                    {typeof f.impactEstimateUsd === 'number' && (
                      <div className="flex items-center gap-1 text-ink-700">
                        <DollarSign size={11} className="text-ink-500" />
                        <span className="text-ink-500">Impact:</span>
                        <span className="font-semibold tabular-nums">
                          {fmtCurrency(f.impactEstimateUsd, { compact: true })}
                        </span>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
