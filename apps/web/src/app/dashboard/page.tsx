'use client';
import Link from 'next/link';
import {
  FolderKanban, FileText, TrendingUp, Clock, Plus, Building2, MoreHorizontal,
  ArrowUpRight, Sparkles,
} from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { StatusBadge } from '@/components/ui/Badge';
import {
  currentUser, dashboardStats, projects,
} from '@/lib/mockData';
import { fmtCurrency } from '@/lib/format';

const stats = [
  { label: 'Active Projects', value: dashboardStats.activeProjects.toString(), sub: `${dashboardStats.totalProjects} total`, subTone: 'green' as const, icon: FolderKanban },
  { label: 'Documents Processed', value: dashboardStats.documentsProcessed.toString(), sub: '', icon: FileText },
  { label: 'Total Deal Volume', value: fmtCurrency(dashboardStats.totalDealVolume), sub: '', icon: TrendingUp },
  { label: 'Avg. Time to IC', value: dashboardStats.avgTimeToIC ?? '—', sub: '', icon: Clock },
];

const riskTone = (r: string) => r === 'Low' ? 'text-success-700' : r === 'Medium' ? 'text-warn-700' : 'text-danger-700';

export default function DashboardPage() {
  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        title="Dashboard"
        subtitle={`Welcome back, ${currentUser.name.split(' ')[0]}. Here's your portfolio overview.`}
        action={
          <Link href="/projects/new">
            <Button variant="primary"><Plus size={14} /> New Project</Button>
          </Link>
        }
      />

      {/* Stat cards */}
      <div className="grid grid-cols-4 gap-4 mb-6">
        {stats.map(s => {
          const Icon = s.icon;
          return (
            <Card key={s.label} className="p-5">
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-[12px] text-ink-500 font-medium">{s.label}</div>
                  <div className="text-[28px] font-semibold text-ink-900 mt-1.5 tabular-nums tracking-tight">{s.value}</div>
                  {s.sub && (
                    <div className={`text-[11.5px] mt-1 ${s.subTone === 'green' ? 'text-success-700' : 'text-ink-500'}`}>
                      {s.sub}
                    </div>
                  )}
                </div>
                <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center">
                  <Icon size={16} className="text-brand-500" />
                </div>
              </div>
            </Card>
          );
        })}
      </div>

      <div className="grid grid-cols-3 gap-4">
        {/* Recent Projects */}
        <Card className="col-span-2">
          <div className="px-5 py-4 border-b border-border flex items-center justify-between">
            <h2 className="text-[14px] font-semibold text-ink-900">Recent Projects</h2>
            <Link href="/projects" className="text-[12px] text-brand-500 hover:text-brand-700 font-medium inline-flex items-center gap-1">
              View All <ArrowUpRight size={12} />
            </Link>
          </div>
          <div>
            {projects.map((p, i) => (
              <Link key={p.id} href={`/projects/${p.id}`}
                className={`flex items-center gap-3 px-5 py-3.5 hover:bg-ink-300/10 transition-colors ${i < projects.length - 1 ? 'border-b border-border' : ''}`}>
                <div className="w-9 h-9 rounded-md bg-brand-50 flex items-center justify-center flex-shrink-0">
                  <Building2 size={16} className="text-brand-500" />
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
                <div className="w-7 h-7 rounded-full bg-ink-300/30 flex items-center justify-center text-[10px] font-semibold text-ink-700">
                  {p.assignee}
                </div>
                <button className="p-1 hover:bg-ink-300/20 rounded" onClick={(e) => { e.preventDefault(); }}>
                  <MoreHorizontal size={14} className="text-ink-400" />
                </button>
              </Link>
            ))}
          </div>
        </Card>

        {/* Right rail */}
        <div className="space-y-4">
          <Card className="p-5">
            <h3 className="text-[13.5px] font-semibold text-ink-900 mb-4">Team Activity</h3>
            <div className="text-center py-6">
              <div className="text-[12px] text-ink-500">No recent activity</div>
            </div>
          </Card>
          <Card className="p-5">
            <div className="flex items-center gap-2 mb-4">
              <Sparkles size={14} className="text-brand-500" />
              <h3 className="text-[13.5px] font-semibold text-ink-900">AI Insights</h3>
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
