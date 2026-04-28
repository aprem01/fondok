'use client';
import { useEffect, useRef, useState } from 'react';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import dynamic from 'next/dynamic';
import Link from 'next/link';
import {
  ArrowLeft, MapPin, Building2, Calendar, Users, Share2, X,
  Sparkles, FolderOpen, FileText, DollarSign, TrendingUp, BarChart3, Activity,
  Briefcase, MapPinned, FileSearch, Download, AlertTriangle,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import { ErrorBoundary } from '@/components/ui/ErrorBoundary';
import KebabMenu from '@/components/ui/KebabMenu';
import { useToast } from '@/components/ui/Toast';
import { projects, kimptonDocuments } from '@/lib/mockData';
import { criticalCount as varianceCriticalCount } from '@/lib/varianceData';
import { cn } from '@/lib/format';
import { useDeal } from '@/lib/hooks/useDeal';
import { useDocuments } from '@/lib/hooks/useDocuments';
import { isWorkerConnected, workerUrl } from '@/lib/api';
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
  const { toast } = useToast();
  const rawId = (params?.id as string | undefined) ?? '';
  const isMockId = /^\d+$/.test(rawId);
  const id = isMockId ? Number(rawId) : NaN;
  const { deal } = useDeal(rawId);
  const mockMatch = isMockId ? projects.find(p => p.id === id) : undefined;
  const workerConnected = isWorkerConnected();

  // Header kebab actions — Export Excel / Export IC Memo / Mark IC Ready /
  // Archive Project. Worker-backed deals (UUID rawId) actually hit the worker;
  // mock deals show toasts so the affordance is still discoverable.
  const onExportExcel = () => {
    if (workerConnected && !isMockId) {
      window.location.href = `${workerUrl()}/deals/${rawId}/export/excel`;
    } else {
      toast('Excel export available once worker is connected to this deal', {
        type: 'info',
      });
    }
  };
  const onExportMemo = () => {
    if (workerConnected && !isMockId) {
      window.location.href = `${workerUrl()}/deals/${rawId}/export/memo.pdf`;
    } else {
      toast('IC Memo export available once worker is connected to this deal', {
        type: 'info',
      });
    }
  };
  const onMarkICReady = () => toast('Marked as IC Ready', { type: 'success' });
  const onArchive = async () => {
    if (workerConnected && !isMockId) {
      try {
        // No typed delete on api.deals yet — use the raw fetch path the
        // worker exposes. Settings page does the same dance.
        await fetch(`${workerUrl()}/deals/${rawId}`, { method: 'DELETE' });
        toast('Project archived', { type: 'success' });
        router.push('/projects');
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        toast(`Archive failed: ${msg}`, { type: 'error' });
      }
    } else {
      toast('Archive available once worker is connected to this deal', {
        type: 'info',
      });
    }
  };

  const headerMenu = [
    { label: 'Export Excel', onSelect: onExportExcel },
    { label: 'Export IC Memo', onSelect: onExportMemo },
    { label: 'Mark as IC Ready', onSelect: onMarkICReady },
    { label: 'Archive Project', onSelect: onArchive, danger: true },
  ];

  // Build a unified Project-shaped record so the existing UI keeps working.
  // For real (UUID) deals we synthesize a minimal record from the worker payload.
  // (Hoisted above the header-pill / confidence helpers below so they can read it.)
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

  // Header pill state — popovers/drawers/tooltips. We keep all four
  // independent so opening one doesn't smash the others. Each closes on
  // outside-click or Esc.
  const [docsOpen, setDocsOpen] = useState(false);
  const [confidenceOpen, setConfidenceOpen] = useState(false);
  const [collabOpen, setCollabOpen] = useState(false);
  const docsBtnRef = useRef<HTMLButtonElement | null>(null);
  const confidenceBtnRef = useRef<HTMLButtonElement | null>(null);
  const collabBtnRef = useRef<HTMLButtonElement | null>(null);

  // Pull live worker docs when on a real (UUID) deal so the Docs drawer
  // reflects what's actually in the data room. Mock deals fall back to the
  // canned kimptonDocuments list (only Kimpton has rich mock data).
  const liveMode = workerConnected && !isMockId;
  const { documents: liveDocs } = useDocuments(liveMode ? rawId : '');
  const drawerDocs = liveMode
    ? liveDocs.map((d) => ({ name: d.filename, status: d.status, type: d.doc_type ?? '—' }))
    : (id === 7
        ? kimptonDocuments.map((d) => ({ name: d.name, status: d.status, type: d.type }))
        : []);

  // Esc closes any open header overlay.
  useEffect(() => {
    if (!docsOpen && !confidenceOpen && !collabOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setDocsOpen(false);
        setConfidenceOpen(false);
        setCollabOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [docsOpen, confidenceOpen, collabOpen]);

  const onShare = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      toast('Share link copied to clipboard', { type: 'success' });
    } catch {
      toast('Could not copy link', { type: 'error' });
    }
  };

  // Confidence breakdown — derive from project shape. Mock projects keep
  // their static numbers; live deals lean on the worker payload via useDeal.
  const docCount = drawerDocs.length;
  const avgFieldConfidence = project.aiConfidence;
  // Mock variance flag count is global today; only Kimpton (id=7) has real flags.
  const varianceFlagCount = id === 7 ? varianceCriticalCount : 0;

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
            <div className="relative">
              <button
                ref={docsBtnRef}
                type="button"
                onClick={() => { setDocsOpen(o => !o); setConfidenceOpen(false); setCollabOpen(false); }}
                aria-label={`Open documents (${project.docs} files)`}
                aria-expanded={docsOpen}
                aria-haspopup="dialog"
                className="h-8 px-2.5 bg-white border border-border hover:border-ink-300 hover:bg-ink-100 rounded-md text-[12px] inline-flex items-center gap-1.5 text-ink-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <FileText size={12} aria-hidden="true" /> Docs <span className="text-ink-900 font-medium tabular-nums">{project.docs}</span>
              </button>
            </div>
            <div className="relative">
              <button
                ref={confidenceBtnRef}
                type="button"
                onMouseEnter={() => setConfidenceOpen(true)}
                onMouseLeave={() => setConfidenceOpen(false)}
                onFocus={() => setConfidenceOpen(true)}
                onBlur={() => setConfidenceOpen(false)}
                aria-label={project.aiConfidence === 0 ? 'Awaiting documents' : `AI confidence ${project.aiConfidence}%`}
                aria-describedby={confidenceOpen ? 'ai-confidence-tooltip' : undefined}
                className="h-8 px-2.5 bg-brand-50 hover:bg-brand-100 border border-brand-100 rounded-md text-[12px] inline-flex items-center gap-1.5 text-brand-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <Sparkles size={12} aria-hidden="true" />
                {project.aiConfidence === 0 ? 'Awaiting docs' : (
                  <span className="tabular-nums"><span className="font-semibold">{project.aiConfidence}%</span> AI Confidence</span>
                )}
              </button>
              {confidenceOpen && (
                <div
                  id="ai-confidence-tooltip"
                  role="tooltip"
                  className="absolute right-0 top-full mt-1.5 z-30 w-64 rounded-md border border-border bg-white p-3 shadow-card text-[11.5px] text-ink-700"
                >
                  <div className="text-[12px] font-semibold text-ink-900 mb-1.5">AI Confidence Breakdown</div>
                  <div className="space-y-1 tabular-nums">
                    <div className="flex justify-between"><span className="text-ink-500">Doc count</span><span className="font-medium text-ink-900">{docCount}</span></div>
                    <div className="flex justify-between"><span className="text-ink-500">Avg field confidence</span><span className="font-medium text-ink-900">{avgFieldConfidence}%</span></div>
                    <div className="flex justify-between"><span className="text-ink-500">Variance flags</span><span className="font-medium text-ink-900">{varianceFlagCount}</span></div>
                  </div>
                </div>
              )}
            </div>
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
            <div className="relative">
              <button
                ref={collabBtnRef}
                type="button"
                onClick={() => { setCollabOpen(o => !o); setDocsOpen(false); }}
                aria-label="Manage collaborators"
                aria-expanded={collabOpen}
                aria-haspopup="dialog"
                className="h-8 w-8 inline-flex items-center justify-center hover:bg-ink-100 rounded-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <Users size={14} className="text-ink-700" aria-hidden="true" />
              </button>
              {collabOpen && (
                <>
                  <div className="fixed inset-0 z-20" onClick={() => setCollabOpen(false)} aria-hidden="true" />
                  <div
                    role="dialog"
                    aria-label="Collaborators"
                    className="absolute right-0 top-full mt-1.5 z-30 w-64 rounded-md border border-border bg-white p-3 shadow-card"
                  >
                    <div className="text-[12px] font-semibold text-ink-900 mb-2">Collaborators</div>
                    <div className="space-y-2">
                      {[
                        { initials: 'SC', name: 'Sarah Chen', role: 'Admin' },
                        { initials: 'MJ', name: 'Mike Johnson', role: 'Analyst' },
                        { initials: 'AW', name: 'Alex Wong', role: 'Analyst' },
                      ].map(p => (
                        <div key={p.name} className="flex items-center gap-2.5">
                          <div className="w-7 h-7 rounded-full bg-ink-300/30 flex items-center justify-center text-[10.5px] font-semibold text-ink-700">{p.initials}</div>
                          <div className="flex-1 min-w-0">
                            <div className="text-[12px] font-medium text-ink-900 truncate">{p.name}</div>
                            <div className="text-[10.5px] text-ink-500">{p.role}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                    <button
                      type="button"
                      onClick={() => { setCollabOpen(false); toast('Invite collaborators — coming soon', { type: 'info' }); }}
                      className="mt-3 w-full text-left text-[12px] text-brand-700 hover:text-brand-500 font-medium border-t border-border pt-2"
                    >
                      + Invite collaborators
                    </button>
                  </div>
                </>
              )}
            </div>
            <Button
              size="sm"
              variant={project.status === 'IC Ready' ? 'premium' : 'secondary'}
              aria-label="Share deal"
              onClick={onShare}
            >
              <Share2 size={12} aria-hidden="true" /> Share
            </Button>
            <KebabMenu items={headerMenu} />
            {/* Note: KebabMenu has no divider primitive; the danger styling on
                the last item visually separates Archive from the safe actions. */}
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
          <ErrorBoundary tabName="Investment"><InvestmentTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'pl' && (
          <ErrorBoundary tabName="P&L"><PLTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'debt' && (
          <ErrorBoundary tabName="Debt"><DebtTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'cash-flow' && (
          <ErrorBoundary tabName="Cash Flow"><CashFlowTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'returns' && (
          <ErrorBoundary tabName="Returns"><ReturnsTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'partnership' && (
          <ErrorBoundary tabName="Partnership"><PartnershipTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'market' && (
          <ErrorBoundary tabName="Market"><MarketTab projectId={id} /></ErrorBoundary>
        )}
        {activeTab === 'analysis' && (
          <ErrorBoundary tabName="Analysis"><AnalysisTab /></ErrorBoundary>
        )}
        {activeTab === 'export' && (
          <ErrorBoundary tabName="Export"><ExportTab project={project} /></ErrorBoundary>
        )}
      </div>

      {/* Docs side drawer — slides in from the right when the Docs pill is
          clicked. Mock deals show kimptonDocuments; live (UUID) deals pull
          from the worker via useDocuments. */}
      {docsOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-ink-900/30"
            onClick={() => setDocsOpen(false)}
            aria-hidden="true"
          />
          <aside
            role="dialog"
            aria-label="Documents"
            className="fixed right-0 top-0 bottom-0 z-50 w-[360px] bg-white border-l border-border shadow-card-hover flex flex-col"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <div>
                <div className="text-[13.5px] font-semibold text-ink-900">Documents</div>
                <div className="text-[11px] text-ink-500">{drawerDocs.length} file{drawerDocs.length === 1 ? '' : 's'}</div>
              </div>
              <button
                type="button"
                onClick={() => setDocsOpen(false)}
                aria-label="Close documents drawer"
                className="p-1 rounded hover:bg-ink-100 text-ink-500"
              >
                <X size={14} />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
              {drawerDocs.length === 0 ? (
                <div className="text-[12px] text-ink-500 text-center py-6">
                  No documents yet. Upload via the Data Room tab.
                </div>
              ) : drawerDocs.map(d => (
                <div key={d.name} className="flex items-start gap-2.5 p-2 rounded-md border border-border hover:bg-ink-100">
                  <FileText size={14} className="text-ink-500 mt-0.5 flex-shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="text-[12px] font-medium text-ink-900 truncate">{d.name}</div>
                    <div className="text-[10.5px] text-ink-500 mt-0.5">{d.type} · {d.status}</div>
                  </div>
                </div>
              ))}
            </div>
            <div className="px-4 py-3 border-t border-border">
              <Button
                variant="secondary"
                size="sm"
                className="w-full"
                onClick={() => { setDocsOpen(false); setTab(''); }}
              >
                Open Data Room
              </Button>
            </div>
          </aside>
        </>
      )}
    </div>
  );

  return id === 7 ? <AssumptionsProvider>{inner}</AssumptionsProvider> : inner;
}
