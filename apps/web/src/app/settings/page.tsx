'use client';
import { useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { workspace, teamMembers, notificationDefaults, integrations } from '@/lib/mockData';
import { cn } from '@/lib/format';

const tabs = ['Team', 'Workspace', 'Notifications', 'Integrations'];

export default function SettingsPage() {
  const [tab, setTab] = useState('Team');
  const [notifs, setNotifs] = useState(notificationDefaults);

  return (
    <div className="px-8 py-8 max-w-[1100px]">
      <PageHeader title="Settings" subtitle="Manage your workspace settings and team members" />

      <div className="flex items-center gap-1 mb-6 bg-white border border-border rounded-md p-1 inline-flex">
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn(
              'px-3.5 py-1.5 text-[12.5px] rounded transition-colors',
              tab === t ? 'bg-brand-50 text-brand-700 font-medium' : 'text-ink-500 hover:text-ink-900'
            )}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'Team' && (
        <div className="space-y-5">
          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Invite Team Member</h3>
            <p className="text-[12px] text-ink-500 mb-4">Add new members to your workspace. They'll receive an email invitation.</p>
            <div className="flex items-center gap-2">
              <input placeholder="colleague@company.com"
                className="flex-1 px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
              <select className="px-3 py-2 text-[13px] bg-white border border-border rounded-md">
                <option>Analyst</option><option>Principal</option><option>Admin</option>
              </select>
              <Button variant="primary">Send Invite</Button>
            </div>
          </Card>

          <Card>
            <div className="px-5 py-4 border-b border-border">
              <h3 className="text-[14px] font-semibold text-ink-900">Team Members ({teamMembers.length})</h3>
            </div>
            {teamMembers.map((m, i) => (
              <div key={m.email} className={cn('flex items-center gap-3 px-5 py-4', i < teamMembers.length - 1 && 'border-b border-border')}>
                <div className="w-9 h-9 rounded-full bg-ink-300/30 flex items-center justify-center text-[11px] font-semibold text-ink-700">
                  {m.initials}
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <div className="text-[13px] font-medium text-ink-900">{m.name}</div>
                    {m.pending && <Badge tone="amber">Pending</Badge>}
                  </div>
                  <div className="text-[11.5px] text-ink-500">{m.email}</div>
                </div>
                <select defaultValue={m.role} className="px-2.5 py-1.5 text-[12px] bg-white border border-border rounded-md">
                  <option>Analyst</option><option>Principal</option><option>Admin</option>
                </select>
                <button className="p-1.5 hover:bg-ink-300/20 rounded"><MoreHorizontal size={14} className="text-ink-400" /></button>
              </div>
            ))}
          </Card>
        </div>
      )}

      {tab === 'Workspace' && (
        <div className="space-y-5">
          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-4">Workspace Details</h3>
            <div className="space-y-4">
              <Field label="Workspace Name" defaultValue={workspace.name} />
              <Field label="Workspace URL" defaultValue={workspace.url} prefix="fondok.ai/" />
            </div>
          </Card>

          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Default Assumptions</h3>
            <p className="text-[12px] text-ink-500 mb-4">Set default values for new underwriting projects.</p>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <label className="block text-[12px] font-medium text-ink-700 mb-1.5">Default Hold Period</label>
                <select className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md">
                  <option>3 years</option><option selected>5 years</option><option>7 years</option><option>10 years</option>
                </select>
              </div>
              <Field label="Default LTV" defaultValue="65%" />
              <Field label="Default Interest Rate" defaultValue="6.25%" />
            </div>
            <Button variant="primary">Save Defaults</Button>
          </Card>
        </div>
      )}

      {tab === 'Notifications' && (
        <Card className="p-5">
          <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Email Notifications</h3>
          <p className="text-[12px] text-ink-500 mb-5">Choose what updates you want to receive via email.</p>
          {[
            { k: 'projectStatus', t: 'Project status changes', d: 'When a project moves to a new status' },
            { k: 'documentUploads', t: 'Document uploads', d: 'When new documents are uploaded to a project' },
            { k: 'aiExtraction', t: 'AI extraction complete', d: 'When AI finishes extracting data from documents' },
            { k: 'teamActivity', t: 'Team member activity', d: 'When team members make significant changes' },
            { k: 'weeklyDigest', t: 'Weekly digest', d: 'Summary of all project activity' },
          ].map(n => (
            <div key={n.k} className="flex items-center justify-between py-3 border-b border-border last:border-0">
              <div>
                <div className="text-[13px] font-medium text-ink-900">{n.t}</div>
                <div className="text-[12px] text-ink-500 mt-0.5">{n.d}</div>
              </div>
              <Toggle on={(notifs as any)[n.k]} onChange={v => setNotifs({ ...notifs, [n.k]: v })} />
            </div>
          ))}
        </Card>
      )}

      {tab === 'Integrations' && (
        <div className="space-y-5">
          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Data Sources</h3>
            <p className="text-[12px] text-ink-500 mb-5">Connect external data sources to enhance your underwriting.</p>
            {integrations.map((i, idx) => (
              <div key={i.name} className={cn('flex items-center justify-between py-4', idx < integrations.length - 1 && 'border-b border-border')}>
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-md bg-brand-50 flex items-center justify-center text-[11px] font-bold text-brand-700">
                    {i.name.charAt(0)}
                  </div>
                  <div>
                    <div className="text-[13px] font-medium text-ink-900">{i.name}</div>
                    <div className="text-[11.5px] text-ink-500">{i.description}</div>
                  </div>
                </div>
                <Badge tone="gray">{i.status}</Badge>
              </div>
            ))}
          </Card>

          <Card className="p-5 bg-brand-50 border-brand-100">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-[14px] font-semibold text-ink-900">Enterprise Integrations</h3>
                <p className="text-[12px] text-ink-700 mt-1">Contact us to discuss custom integrations with your existing systems.</p>
              </div>
              <Button variant="secondary">Contact Sales</Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function Field({ label, defaultValue, prefix }: { label: string; defaultValue: string; prefix?: string }) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <div className="flex items-center bg-white border border-border rounded-md focus-within:ring-2 focus-within:ring-brand-100 focus-within:border-brand-500">
        {prefix && <span className="pl-3 text-[12.5px] text-ink-500">{prefix}</span>}
        <input defaultValue={defaultValue}
          className="flex-1 px-3 py-2 text-[13px] bg-transparent rounded-md focus:outline-none" />
      </div>
    </div>
  );
}

function Toggle({ on, onChange }: { on: boolean; onChange: (v: boolean) => void }) {
  return (
    <button onClick={() => onChange(!on)}
      className={cn(
        'w-10 h-5 rounded-full transition-colors relative',
        on ? 'bg-brand-500' : 'bg-ink-300'
      )}>
      <div className={cn(
        'absolute top-0.5 w-4 h-4 bg-white rounded-full transition-transform',
        on ? 'translate-x-5' : 'translate-x-0.5'
      )} />
    </button>
  );
}
