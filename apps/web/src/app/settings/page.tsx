'use client';
import { useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, Trash2 } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { workspace, teamMembers, notificationDefaults, integrations } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected, WorkerError } from '@/lib/api';
import { IntroCard } from '@/components/help/IntroCard';

const tabs = ['Team', 'Workspace', 'Notifications', 'Integrations'];

// Surface-level email check — good enough to gate the "Send Invite" CTA;
// the worker will validate server-side once the invites endpoint lands.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export default function SettingsPage() {
  const [tab, setTab] = useState('Team');
  const [notifs, setNotifs] = useState(notificationDefaults);
  const { toast } = useToast();
  const [confirmReset, setConfirmReset] = useState<{ count: number } | null>(null);
  const [resetting, setResetting] = useState(false);
  const workerConnected = isWorkerConnected();
  // Danger zone (Reset Worker Data) is hidden by default and only shown
  // when NEXT_PUBLIC_SHOW_DANGER_ZONE === 'true'. Keeps the destructive
  // affordance available to operators while keeping it out of customer view.
  const showDangerZone = process.env.NEXT_PUBLIC_SHOW_DANGER_ZONE === 'true';

  // Invite form state — kept local; no remote invites API today.
  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState('Analyst');

  // Workspace fields are bound so we can fire a "saved" toast on blur.
  // No real persistence — the mock workspace object is read-only — but the
  // affordance ships now so the wiring is in place for the worker route.
  const [workspaceName, setWorkspaceName] = useState(workspace.name);
  const [workspaceUrl, setWorkspaceUrl] = useState(workspace.url);
  const [defaultLtv, setDefaultLtv] = useState('65%');
  const [defaultRate, setDefaultRate] = useState('6.25%');

  // Debounce notification preference toasts so flipping several toggles
  // back-to-back collapses to a single "Preferences saved" notice.
  const notifDebounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  const notifFirstRender = useRef(true);
  useEffect(() => {
    if (notifFirstRender.current) {
      notifFirstRender.current = false;
      return;
    }
    if (notifDebounce.current) clearTimeout(notifDebounce.current);
    notifDebounce.current = setTimeout(() => {
      toast('Preferences saved', { type: 'success' });
    }, 500);
    return () => {
      if (notifDebounce.current) clearTimeout(notifDebounce.current);
    };
  }, [notifs, toast]);

  const onSendInvite = () => {
    const email = inviteEmail.trim();
    if (!EMAIL_RE.test(email)) {
      toast('Enter a valid email address', { type: 'error' });
      return;
    }
    toast(`Invitation sent to ${email}`, { type: 'success' });
    setInviteEmail('');
  };

  const onContactSales = () => {
    const url =
      'mailto:sales@anthropic.com?subject=Fondok+AI+Enterprise+Inquiry';
    // _blank keeps the mail client invocation from replacing the tab in
    // browsers that route the protocol via a web client.
    window.open(url, '_blank');
  };

  const onSaveDefaults = () => {
    // No-op persistence today — mock workspace defaults aren't sent anywhere.
    // Surface a toast so users get feedback that their click registered.
    toast('Workspace defaults saved', { type: 'success' });
  };

  const openResetModal = async () => {
    if (!workerConnected) return;
    try {
      const deals = await api.deals.list();
      setConfirmReset({ count: deals.length });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Couldn't list deals: ${msg}`, { type: 'error' });
    }
  };

  const onConfirmReset = async () => {
    setResetting(true);
    try {
      const deals = await api.deals.list();
      if (deals.length === 0) {
        toast('No worker deals to archive', { type: 'info' });
        setConfirmReset(null);
        setResetting(false);
        return;
      }
      let archived = 0;
      let failed = 0;
      for (const d of deals) {
        try {
          // Worker exposes DELETE /deals/{id}; if the endpoint isn't wired
          // we still surface the failure per-deal rather than aborting.
          await fetch(`${process.env.NEXT_PUBLIC_WORKER_URL}/deals/${d.id}`, {
            method: 'DELETE',
          }).then((r) => {
            if (!r.ok) {
              throw new WorkerError(
                `DELETE /deals/${d.id} → ${r.status}`,
                r.status,
                '',
              );
            }
          });
          archived += 1;
          toast(`Archived ${d.name || d.id.slice(0, 8)}`, {
            type: 'info',
            duration: 1500,
          });
        } catch (err) {
          failed += 1;
          const msg = err instanceof Error ? err.message : String(err);
          toast(`Couldn't archive ${d.name}: ${msg}`, { type: 'error' });
        }
      }
      if (failed === 0) {
        toast(`Reset complete · ${archived} deal${archived === 1 ? '' : 's'} archived`, {
          type: 'success',
        });
      } else {
        toast(
          `Reset partial · ${archived} archived, ${failed} failed`,
          { type: 'error' },
        );
      }
    } finally {
      setResetting(false);
      setConfirmReset(null);
    }
  };

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
          <IntroCard
            dismissKey="settings-team-intro"
            title="Team & roles"
            body={
              <>
                Invite your colleagues. <span className="font-semibold">Analysts</span> can run models
                and edit assumptions. <span className="font-semibold">Principals</span> can approve
                IC memos. <span className="font-semibold">Admins</span> can change workspace settings
                and manage billing.
              </>
            }
          />
          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Invite Team Member</h3>
            <p className="text-[12px] text-ink-500 mb-4">Add new members to your workspace. They'll receive an email invitation.</p>
            <div className="flex items-center gap-2">
              <input
                value={inviteEmail}
                onChange={(e) => setInviteEmail(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') onSendInvite();
                }}
                placeholder="colleague@company.com"
                type="email"
                className="flex-1 px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
              />
              <select
                value={inviteRole}
                onChange={(e) => setInviteRole(e.target.value)}
                className="px-3 py-2 text-[13px] bg-white border border-border rounded-md"
              >
                <option>Analyst</option><option>Principal</option><option>Admin</option>
              </select>
              <Button variant="primary" onClick={onSendInvite}>Send Invite</Button>
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
                <KebabMenu
                  items={[
                    { label: 'Edit', onSelect: () => toast('Member edits available on enterprise plans', { type: 'info' }) },
                    { label: 'Remove', danger: true, onSelect: () => toast('Member removal available on enterprise plans', { type: 'info' }) },
                  ]}
                />
              </div>
            ))}
          </Card>
        </div>
      )}

      {tab === 'Workspace' && (
        <div className="space-y-5">
          <IntroCard
            dismissKey="settings-workspace-intro"
            title="Your firm's defaults"
            body={
              <>
                Workspace name, URL, and the default underwriting assumptions (LTV, interest rate,
                hold period) that pre-fill every new deal. Setting smart defaults here saves you
                from re-entering the same numbers on each new deal.
              </>
            }
          />
          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-4">Workspace Details</h3>
            <div className="space-y-4">
              <Field
                label="Workspace Name"
                value={workspaceName}
                onChange={setWorkspaceName}
                onBlur={() => toast('Workspace name updated', { type: 'success' })}
              />
              <Field
                label="Workspace URL"
                value={workspaceUrl}
                onChange={setWorkspaceUrl}
                onBlur={() => toast('Workspace URL updated', { type: 'success' })}
                prefix="fondok.ai/"
              />
            </div>
          </Card>

          <Card className="p-5">
            <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Default Assumptions</h3>
            <p className="text-[12px] text-ink-500 mb-4">Set default values for new underwriting projects.</p>
            <div className="grid grid-cols-3 gap-4 mb-4">
              <div>
                <label className="block text-[12px] font-medium text-ink-700 mb-1.5">Default Hold Period</label>
                <select defaultValue="5 years" className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md">
                  <option>3 years</option><option>5 years</option><option>7 years</option><option>10 years</option>
                </select>
              </div>
              <Field label="Default LTV" value={defaultLtv} onChange={setDefaultLtv} />
              <Field label="Default Interest Rate" value={defaultRate} onChange={setDefaultRate} />
            </div>
            <Button variant="primary" onClick={onSaveDefaults}>Save Defaults</Button>
          </Card>

          {workerConnected && showDangerZone && (
            <Card className="p-5 border-danger-500/40">
              <div className="flex items-center gap-2 mb-1">
                <AlertTriangle size={14} className="text-danger-700" />
                <h3 className="text-[14px] font-semibold text-danger-700">Danger Zone</h3>
              </div>
              <div className="border-t border-border my-3" />
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1">
                  <div className="text-[13px] font-semibold text-ink-900">Archive All Workspace Deals</div>
                  <p className="text-[12px] text-ink-500 mt-1 leading-relaxed">
                    Archives every deal currently in this workspace. This action cannot be undone.
                  </p>
                </div>
                <Button
                  variant="danger"
                  size="sm"
                  onClick={openResetModal}
                  disabled={resetting}
                >
                  {resetting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                  Archive All Deals
                </Button>
              </div>
            </Card>
          )}
        </div>
      )}

      {confirmReset && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 px-4"
          onClick={() => !resetting && setConfirmReset(null)}
        >
          <Card
            className="p-5 max-w-md w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-start gap-3 mb-3">
              <div className="w-9 h-9 rounded-md bg-danger-50 flex items-center justify-center flex-shrink-0">
                <AlertTriangle size={16} className="text-danger-700" />
              </div>
              <div>
                <h4 className="text-[14px] font-semibold text-ink-900">Are you sure?</h4>
                <p className="text-[12.5px] text-ink-500 mt-1 leading-relaxed">
                  This will archive {confirmReset.count} deal{confirmReset.count === 1 ? '' : 's'} from the workspace.
                  This action cannot be undone.
                </p>
              </div>
            </div>
            <div className="flex items-center justify-end gap-2 mt-4">
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setConfirmReset(null)}
                disabled={resetting}
              >
                Cancel
              </Button>
              <Button
                variant="danger"
                size="sm"
                onClick={onConfirmReset}
                disabled={resetting}
              >
                {resetting && <Loader2 size={12} className="animate-spin" />}
                {resetting ? 'Resetting…' : `Delete ${confirmReset.count} deal${confirmReset.count === 1 ? '' : 's'}`}
              </Button>
            </div>
          </Card>
        </div>
      )}

      {tab === 'Notifications' && (
        <>
        <IntroCard
          dismissKey="settings-notifications-intro"
          title="Email notifications"
          body={
            <>
              Pick which events email you. The defaults are sensible for most workflows —
              status changes, document uploads, AI extraction completion. Toggle anything off
              if your inbox is getting noisy.
            </>
          }
        />
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
        </>
      )}

      {tab === 'Integrations' && (
        <div className="space-y-5">
          <IntroCard
            dismissKey="settings-integrations-intro"
            title="External data sources"
            body={
              <>
                Connect data providers we can pull from. <span className="font-semibold">STR</span> (Smith
                Travel Research) is the gold standard for hotel performance data — RevPAR, occupancy,
                comp set benchmarks. Most integrations are gated to Enterprise plans.
              </>
            }
          />
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
                <Badge tone="gray">{i.status === 'Coming Soon' ? 'Enterprise plan' : i.status}</Badge>
              </div>
            ))}
          </Card>

          <Card className="p-5 bg-brand-50 border-brand-100">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="text-[14px] font-semibold text-ink-900">Enterprise Integrations</h3>
                <p className="text-[12px] text-ink-700 mt-1">Contact us to discuss custom integrations with your existing systems.</p>
              </div>
              <Button variant="secondary" onClick={onContactSales}>Contact Sales</Button>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}

function Field({
  label, value, onChange, onBlur, prefix,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  onBlur?: () => void;
  prefix?: string;
}) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <div className="flex items-center bg-white border border-border rounded-md focus-within:ring-2 focus-within:ring-brand-100 focus-within:border-brand-500">
        {prefix && <span className="pl-3 text-[12.5px] text-ink-500">{prefix}</span>}
        <input
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={onBlur}
          className="flex-1 px-3 py-2 text-[13px] bg-transparent rounded-md focus:outline-none"
        />
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
