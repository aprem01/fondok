'use client';
import Link from 'next/link';
import {
  FolderKanban, FileText, TrendingUp, Clock, Plus, Building2,
  ArrowUpRight, Sparkles,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import {
  currentUser, dashboardStats, projects,
} from '@/lib/mockData';
import { fmtCurrency } from '@/lib/format';
import { useToast } from '@/components/ui/Toast';
import { IntroCard } from '@/components/help/IntroCard';
import { MetricLabel } from '@/components/help/MetricLabel';

type StatTone = 'default' | 'luxe';

const stats: Array<{
  label: string;
  value: string;
  sub: string;
  subTone?: 'green';
  icon: typeof FolderKanban;
  tone: StatTone;
  tip: string;
}> = [
  { label: 'Active Projects',     value: dashboardStats.activeProjects.toString(),       sub: `${dashboardStats.totalProjects} total`, subTone: 'green', icon: FolderKanban, tone: 'default',
    tip: 'Hotel deals you\'re currently underwriting — anything not archived. Excludes deals you\'ve passed on or closed.' },
  { label: 'Documents Processed', value: dashboardStats.documentsProcessed.toString(),   sub: '',                                       icon: FileText,    tone: 'default',
    tip: 'PDFs and Excels our AI has read end-to-end and pulled fields out of (offering memos, T-12 financials, STR reports).' },
  // Total Deal Volume is the anchor metric — gets the luxe treatment.
  { label: 'Total Deal Volume',   value: fmtCurrency(dashboardStats.totalDealVolume),    sub: '',                                       icon: TrendingUp,  tone: 'luxe',
    tip: 'Sum of asking prices across all active deals in your pipeline. Tells you how much capital you\'re evaluating right now.' },
  { label: 'Avg. Time to IC',     value: dashboardStats.avgTimeToIC ?? '—',              sub: '',                                       icon: Clock,       tone: 'default',
    tip: 'How long your team typically takes to get a deal from "received OM" to "investment committee ready." Industry norm is 2–4 weeks; with Fondok it should drop to days.' },
];

const riskTone = (r: string) => r === 'Low' ? 'text-success-700' : r === 'Medium' ? 'text-warn-700' : 'text-danger-700';

export default function DashboardPage() {
  const { toast } = useToast();
  // The dashboard list shows seeded mock projects only — none are
  // worker-backed. The kebab actions therefore route Export and Archive to an
  // honest toast rather than a fake URL. Once a deal moves to the worker,
  // the equivalent kebab on /projects (which knows about the worker URL)
  // takes over.
  const mockKebabItems = (id: number) => [
    { label: 'View Details', onSelect: () => { window.location.href = `/projects/${id}`; } },
    {
      label: 'Export Excel',
      onSelect: () => toast('Excel export opens from the project Export tab once the model has run', { type: 'info' }),
    },
    {
      label: 'Export Memo',
      onSelect: () => toast('IC memo export opens from the project Export tab once the model has run', { type: 'info' }),
    },
    {
      label: 'Archive',
      onSelect: () => toast('Open the project to archive it', { type: 'info' }),
      danger: true,
    },
  ];
  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        eyebrow={`Portfolio · ${dashboardStats.activeProjects} active deals`}
        title="Dashboard"
        subtitle={`Welcome back, ${currentUser.name.split(' ')[0]}. Here's your portfolio overview.`}
        action={
          <Link href="/projects/new">
            <Button variant="primary"><Plus size={14} /> New Project</Button>
          </Link>
        }
      />

      <IntroCard
        dismissKey="dashboard-overview"
        title="Welcome to your portfolio"
        body={
          <>
            The numbers up top show how many hotel deals you have in flight, how many
            documents we&apos;ve extracted data from, and total deal volume across your
            active pipeline. Click any deal below to open its full underwriting model.
          </>
        }
      />

      {/* Stat cards — Bloomberg-cell rhythm. */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {stats.map(s => {
          const Icon = s.icon;
          const isLuxe = s.tone === 'luxe';
          return (
            <Card
              key={s.label}
              tone={s.tone}
              className={isLuxe ? 'p-5 pl-6' : 'p-5'}
            >
              <div className="flex items-start justify-between">
                <div className="min-w-0">
                  <MetricLabel label={s.label} tip={s.tip} eyebrow />

                  <div
                    className={
                      'text-display-lg text-ink-900 mt-2 tabular-nums ' +
                      (isLuxe ? 'font-display' : 'font-display')
                    }
                  >
                    {s.value}
                  </div>
                  {s.sub && (
                    <div className={`text-[11.5px] mt-1.5 tabular-nums ${s.subTone === 'green' ? 'text-success-700' : 'text-ink-500'}`}>
                      {s.sub}
                    </div>
                  )}
                </div>
                {/* Outlined icon — refined. */}
                <div
                  className={
                    'w-8 h-8 rounded-md border flex items-center justify-center flex-shrink-0 ' +
                    (isLuxe ? 'border-gold-200 text-gold-500' : 'border-ink-200 text-ink-500')
                  }
                >
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
          <div>
            {projects.map((p, i) => (
              <Link key={p.id} href={`/projects/${p.id}`}
                className={`flex items-center gap-3 px-5 py-3.5 hover:bg-ink-100 transition-colors ${i < projects.length - 1 ? 'border-b hairline' : ''}`}>
                <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
                  <Building2 size={16} className="text-brand-500" strokeWidth={1.75} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <div className="text-[13.5px] font-medium text-ink-900 truncate">{p.name}</div>
                    <StatusBadge value={p.status} />
                  </div>
                  <div className="text-[12px] text-ink-500 mt-0.5">{p.city} · {p.keys} keys</div>
                </div>
                <div className="text-[12.5px] text-ink-700 tabular-nums">${p.revpar} <span className="text-ink-400">RevPAR</span></div>
                <div className={`text-[12px] font-medium w-16 text-center ${riskTone(p.risk)}`}>{p.risk}</div>
                <div className="w-7 h-7 rounded-full bg-ink-100 border border-ink-200 flex items-center justify-center text-[10px] font-semibold text-ink-700">
                  {p.assignee}
                </div>
                <KebabMenu items={mockKebabItems(p.id)} />
              </Link>
            ))}
          </div>
        </Card>

        {/* Right rail */}
        <div className="space-y-4">
          <Card className="p-5">
            <h3 className="font-display text-[13.5px] font-semibold text-ink-900 mb-4 tracking-tight">Team Activity</h3>
            <div className="text-center py-6">
              <div className="text-[12px] text-ink-500">No recent activity</div>
            </div>
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
