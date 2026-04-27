'use client';
import { useParams, useSearchParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import {
  ArrowLeft, MapPin, Building2, Calendar, Users, Share2, MoreHorizontal,
  Sparkles, FolderOpen, FileText, DollarSign, TrendingUp, BarChart3, Activity,
  Briefcase, MapPinned, FileSearch, Download,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusBadge, Badge } from '@/components/ui/Badge';
import { projects } from '@/lib/mockData';
import { cn } from '@/lib/format';
import DataRoomTab from '@/components/project/DataRoomTab';
import OverviewTab from '@/components/project/OverviewTab';
import EnginePlaceholder from '@/components/project/EnginePlaceholder';
import InvestmentTab from '@/components/project/InvestmentTab';
import DebtTab from '@/components/project/DebtTab';
import ReturnsTab from '@/components/project/ReturnsTab';
import PartnershipTab from '@/components/project/PartnershipTab';
import MarketTab from '@/components/project/MarketTab';
import AnalysisTab from '@/components/project/AnalysisTab';
import ExportTab from '@/components/project/ExportTab';

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
  const id = Number(params.id);
  const project = projects.find(p => p.id === id) || projects[0];
  const activeTab = searchParams.get('tab') || '';

  const setTab = (tab: string) => {
    const url = tab ? `/projects/${id}?tab=${tab}` : `/projects/${id}`;
    router.push(url, { scroll: false });
  };

  return (
    <div>
      {/* Header */}
      <div className="px-8 pt-8 pb-4 bg-white border-b border-border">
        <Link href="/projects" className="inline-flex items-center gap-1 text-[12.5px] text-ink-500 hover:text-ink-900 mb-3">
          <ArrowLeft size={13} /> Back to Projects
        </Link>

        <div className="flex items-start justify-between mb-3">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-[24px] font-semibold text-ink-900">{project.name}</h1>
              <StatusBadge value={project.status} />
            </div>
            <div className="flex items-center gap-4 text-[12.5px] text-ink-500 mt-2">
              <div className="flex items-center gap-1"><MapPin size={12} /> {project.city}</div>
              <div className="flex items-center gap-1"><Building2 size={12} /> {project.keys} keys · {project.service}</div>
              <div className="flex items-center gap-1"><Calendar size={12} /> Created {project.createdAt || 'Apr 2026'}</div>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button className="px-2.5 py-1.5 bg-ink-300/15 hover:bg-ink-300/30 rounded-md text-[12px] flex items-center gap-1.5 text-ink-700">
              <FileText size={12} /> Docs <span className="text-ink-500">{project.docs}</span>
            </button>
            <button className="px-2.5 py-1.5 bg-brand-50 hover:bg-brand-100 rounded-md text-[12px] flex items-center gap-1.5 text-brand-700">
              <Sparkles size={12} /> {project.aiConfidence}% AI Confidence
            </button>
            <button className="p-2 hover:bg-ink-300/20 rounded-md"><Users size={14} className="text-ink-500" /></button>
            <Button size="sm" variant="secondary"><Share2 size={12} /> Share</Button>
            <button className="p-2 hover:bg-ink-300/20 rounded-md"><MoreHorizontal size={14} className="text-ink-500" /></button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex items-center gap-1 -mb-px overflow-x-auto scrollbar-thin">
          {tabs.map(t => {
            const Icon = t.icon;
            const isActive = activeTab === t.id;
            return (
              <button key={t.id} onClick={() => setTab(t.id)}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-2.5 text-[12.5px] border-b-2 whitespace-nowrap transition-colors',
                  isActive
                    ? 'border-brand-500 text-brand-700 font-medium'
                    : 'border-transparent text-ink-500 hover:text-ink-900'
                )}>
                <Icon size={13} /> {t.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Tab content */}
      <div className="p-8">
        {activeTab === '' && <DataRoomTab projectId={id} />}
        {activeTab === 'overview' && <OverviewTab projectId={id} />}
        {activeTab === 'investment' && <InvestmentTab />}
        {activeTab === 'pl' && <EnginePlaceholder name="P&L Engine" desc="Models room revenue, F&B, and operating expenses across the projection period." outputs={['Total Revenue', 'NOI', 'GOP Margin', '+1']} dependsOn={null} />}
        {activeTab === 'debt' && <DebtTab />}
        {activeTab === 'cash-flow' && <EnginePlaceholder name="Cash Flow Engine" desc="Computes levered and unlevered cash flow from operations through hold period." outputs={['Levered CF', 'Unlevered CF', 'CoC', '+1']} dependsOn="P&L" />}
        {activeTab === 'returns' && <ReturnsTab />}
        {activeTab === 'partnership' && <PartnershipTab />}
        {activeTab === 'market' && <MarketTab />}
        {activeTab === 'analysis' && <AnalysisTab />}
        {activeTab === 'export' && <ExportTab project={project} />}
      </div>
    </div>
  );
}
