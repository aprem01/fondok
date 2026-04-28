'use client';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import {
  ArrowLeft, MapPin, Building2, Calendar, Users, Share2, MoreHorizontal,
  Sparkles, FolderOpen, FileText, DollarSign, TrendingUp, BarChart3, Activity,
  Briefcase, MapPinned, FileSearch, Download, AlertTriangle,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import { projects } from '@/lib/mockData';
import { criticalCount as varianceCriticalCount } from '@/lib/varianceData';
import { cn } from '@/lib/format';
import { useDeal } from '@/lib/hooks/useDeal';
import DataRoomTab from '@/components/project/DataRoomTab';
import OverviewTab from '@/components/project/OverviewTab';
import InvestmentTab from '@/components/project/InvestmentTab';
import DebtTab from '@/components/project/DebtTab';
import ExportTab from '@/components/project/ExportTab';
import TabLoadingSkeleton from '@/components/project/TabLoadingSkeleton';
import { AssumptionsProvider } from '@/stores/assumptionsStore';

// Heavy tabs (Recharts-bound) lazy-loaded so the initial /projects/[id]
// JS bundle drops by the size of recharts + each tab's own code.
// Light tabs (Data Room / Overview / Investment / Debt / Export) stay
// eagerly loaded since they're the most common landing tabs.
const PLTab = dynamic(() => import('@/components/project/PLTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const CashFlowTab = dynamic(() => import('@/components/project/CashFlowTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const ReturnsTab = dynamic(() => import('@/components/project/ReturnsTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const PartnershipTab = dynamic(() => import('@/components/project/PartnershipTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const MarketTab = dynamic(() => import('@/components/project/MarketTab'), {
  loading: () => <TabLoadingSkeleton />,
});
const AnalysisTab = dynamic(() => import('@/components/project/AnalysisTab'), {
  loading: () => <TabLoadingSkeleton />,
});

const tabs = [
  { id: '', label: 'Data Room', icon: FolderOpen },
  { id: 'overview', label: 'Overview', icon: FileText },
  { id: 'investment', label: 'Investment', icon: Briefcase },
  { id: 'pl', label: 'P&L', icon: BarChart3 },
  { id: 'debt', label: 'Debt', icon: DollarSign },
  { id: 'cash-flow', label: 'Cash Flow', icon: Activity },
  { id: 'returns', label: 'Returns', icon: TrendingUp },
  { id: 'partnership', label: 'Partnership', icon: Users },
  { id: 'market', label: 'Market', icon: MapPinned },
  { id: 'analysis', label: 'Analysis', icon: FileSearch },
  { id: 'export', label: 'Export', icon: Download },
];

export default function ProjectDetailPage() {
  const params = useParams();
  const searchParams = useSearchParams();
  const router = useRouter();
  const rawId = (params?.id as string | undefined) ?? '';
  const isMockId = /^\d+$/.test(rawId);
  const id = isMockId ? Number(rawId) : NaN;
  const { deal } = useDeal(rawId);
  const mockMatch = isMockId ? projects.find(p => p.id === id) : undefined;

  // Build a unified Project-shaped record so the existing UI keeps working.
  // For real (UUID) deals we synthesize a minimal record from the worker payload.
  const project = mockMatch ?? (deal ? {
    id: 0,
    name: deal.name,
    city: deal.city ?? '—',
    keys: deal.keys ?? 0,
    service: deal.service ?? '—',
    status: (deal.status as typeof projects[number]['status']) || 'Draft',
    dealStage: (deal.deal_stage as typeof projects[number]['dealStage']) || 'Teaser',
    revpar: 0,
    irr: 0,
    risk: ((deal.risk as typeof projects[number]['risk']) || 'Medium'),
    aiConfidence: Math.round((deal.ai_confidence ?? 0) * 100),
    assignee: '—',
    docs: '0/0',
    updatedAt: deal.updated_at ? new Date(deal.updated_at).toLocaleDateString() : '—',
    createdAt: deal.created_at ? new Date(deal.created_at).toLocaleDateString() : undefined,
    noDocs: true,
  } : projects[0]);
  const activeTab = searchParams.get('tab') || '';
  const activeLabel = tabs.find(t => t.id === activeTab)?.label ?? 'Data Room';

  const setTab = (tab: string) => {
    const url = tab ? `/projects/${id}?tab=${tab}` : `/projects/${id}`;
    router.push(url, { scroll: false });
  };

  // Only the Kimpton Angler deal (id=7) has the live assumption sliders wired.
  // For other deals we render without the provider; tabs that need it use the
  // optional accessor and fall back to static mockData.
  const inner = (
    <div>
      {/* Header */}
      <div className="px-8 pt-7 pb-0 bg-white border-b hairline">
        <Link
          href="/projects"
          className="inline-flex items-center gap-1 text-[12px] text-ink-500 hover:text-ink-900 mb-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded eyebrow normal-case tracking-wide"
        >
          <ArrowLeft size={13} aria-hidden="true" /> Back to Projects
        </Link>

        {/* Title + IC-ready badge + right-side controls */}
        <div className="flex items-start justify-between gap-6 mb-4">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3 flex-wrap">
              <h1 className="font-display text-[28px] font-semibold tracking-[-0.018em] text-ink-900 leading-[1.15]">
                {project.name}
              </h1>
              <StatusBadge value={project.status} />
            </div>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <button
              type="button"
              aria-label={`Open documents (${project.docs} files)`}
              className="h-8 px-2.5 bg-white border border-border hover:border-ink-300 hover:bg-ink-100 rounded-md text-[12px] inline-flex items-center gap-1.5 text-ink-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <FileText size={12} aria-hidden="true" /> Docs <span className="text-ink-900 font-medium tabular-nums">{project.docs}</span>
            </button>
            <button
              type="button"
              aria-label={project.aiConfidence === 0 ? 'Awaiting documents' : `AI confidence ${project.aiConfidence}%`}
              className="h-8 px-2.5 bg-brand-50 hover:bg-brand-100 border border-brand-100 rounded-md text-[12px] inline-flex items-center gap-1.5 text-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <Sparkles size={12} aria-hidden="true" />
              {project.aiConfidence === 0 ? 'Awaiting docs' : (
                <span className="tabular-nums"><span className="font-semibold">{project.aiConfidence}%</span> AI Confidence</span>
              )}
            </button>
            {id === 7 && varianceCriticalCount > 0 && (
              <button
                type="button"
                onClick={() => router.push(`/projects/${id}?tab=analysis&sub=variance`, { scroll: false })}
                className="h-8 px-2.5 bg-danger-50 hover:bg-danger-500 hover:text-white text-danger-700 border border-danger-500/30 rounded-md text-[12px] inline-flex items-center gap-1.5 font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                title="Open Broker Variance review"
                aria-label={`${varianceCriticalCount} critical variance flags — open review`}
              >
                <AlertTriangle size={12} aria-hidden="true" /> {varianceCriticalCount} critical flag{varianceCriticalCount === 1 ? '' : 's'}
              </button>
            )}
            <div className="w-px h-5 bg-ink-200 mx-1" aria-hidden="true" />
            <button
              type="button"
              aria-label="Manage collaborators"
              className="h-8 w-8 inline-flex items-center justify-center hover:bg-ink-100 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <Users size={14} className="text-ink-700" aria-hidden="true" />
            </button>
            <Button
              size="sm"
              variant={project.status === 'IC Ready' ? 'premium' : 'secondary'}
              aria-label="Share deal"
            >
              <Share2 size={12} aria-hidden="true" /> Share
            </Button>
            <button
              type="button"
              aria-label="More actions"
              className="h-8 w-8 inline-flex items-center justify-center hover:bg-ink-100 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            >
              <MoreHorizontal size={14} className="text-ink-700" aria-hidden="true" />
            </button>
          </div>
        </div>

        {/* Hairline divider between title row and meta line. */}
        <div className="border-t hairline" />

        {/* Meta line — breathing room. */}
        <div className="flex items-center gap-5 text-[12.5px] text-ink-600 py-3">
          <div className="flex items-center gap-1.5"><MapPin size={12} className="text-ink-400" aria-hidden="true" /> {project.city}</div>
          <div className="w-px h-3 bg-ink-200" aria-hidden="true" />
          <div className="flex items-center gap-1.5"><Building2 size={12} className="text-ink-400" aria-hidden="true" /> <span className="tabular-nums">{project.keys}</span> keys · {project.service}</div>
          <div className="w-px h-3 bg-ink-200" aria-hidden="true" />
          <div className="flex items-center gap-1.5"><Calendar size={12} className="text-ink-400" aria-hidden="true" /> Created {project.createdAt || 'Apr 2026'}</div>
        </div>

        {/* Tabs */}
        <nav
          role="tablist"
          aria-label="Project sections"
          className="flex items-center gap-1 -mb-px overflow-x-auto scrollbar-thin border-t hairline pt-1"
        >
          {tabs.map(t => {
            const Icon = t.icon;
            const isActive = activeTab === t.id;
            return (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={isActive}
                aria-label={t.label}
                tabIndex={isActive ? 0 : -1}
                onClick={() => setTab(t.id)}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-2.5 text-[12.5px] border-b-2 whitespace-nowrap transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-t',
                  isActive
                    ? 'border-brand-500 text-brand-700 font-semibold'
                    : 'border-transparent text-ink-700 hover:text-ink-900'
                )}>
                <Icon size={13} aria-hidden="true" /> {t.label}
              </button>
            );
          })}
        </nav>
      </div>

      {/* Tab content */}
      <div className="p-8" role="tabpanel" aria-label={`${activeLabel} content`}>
        {activeTab === '' && (
          <ErrorBoundary tabName="Data Room"><DataRoomTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'overview' && (
          <ErrorBoundary tabName="Overview"><OverviewTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'investment' && (
          <ErrorBoundary tabName="Investment"><InvestmentTab /></ErrorBoundary>
        )}
        {activeTab === 'pl' && (
          <ErrorBoundary tabName="P&L"><PLTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'debt' && (
          <ErrorBoundary tabName="Debt"><DebtTab /></ErrorBoundary>
        )}
        {activeTab === 'cash-flow' && (
          <ErrorBoundary tabName="Cash Flow"><CashFlowTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'returns' && (
          <ErrorBoundary tabName="Returns"><ReturnsTab /></ErrorBoundary>
        )}
        {activeTab === 'partnership' && (
          <ErrorBoundary tabName="Partnership"><PartnershipTab /></ErrorBoundary>
        )}
        {activeTab === 'market' && (
          <ErrorBoundary tabName="Market"><MarketTab /></ErrorBoundary>
        )}
        {activeTab === 'analysis' && (
          <ErrorBoundary tabName="Analysis"><AnalysisTab /></ErrorBoundary>
        )}
        {activeTab === 'export' && (
          <ErrorBoundary tabName="Export"><ExportTab project={project} /></ErrorBoundary>
        )}
      </div>
    </div>
  );

  return id === 7 ? <AssumptionsProvider>{inner}</AssumptionsProvider> : inner;
}
