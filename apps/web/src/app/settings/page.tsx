'use client';
import { useState } from 'react';
import { AlertTriangle, Loader2, Trash2, ExternalLink, Mail } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import KebabMenu from '@/components/ui/KebabMenu';
import { cn } from '@/lib/format';
import { useToast } from '@/components/ui/Toast';
import { api, isWorkerConnected, userMessage } from '@/lib/api';
import { useCurrentOrg, useOrgActions, useOrgMembers } from '@/lib/auth';
import { IntroCard } from '@/components/help/IntroCard';
import { useHintsEnabled, resetAllCoachMarks } from '@/components/help/useHintsEnabled';
import { Sparkles, RotateCcw } from 'lucide-react';

// Notifications and Integrations were tabs over fixtures: toggles that toasted
// "Preferences saved" without sending anything, and a vendor list relabelled
// "Enterprise plan". Neither is backed, so neither is offered.
const tabs = ['Team', 'Workspace'];

// Surface-level email check — gates the "Send Invite" CTA; Clerk validates
// server-side when the invitation is created.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** Fondok's own contact for enterprise questions — never hard-coded. */
const SALES_EMAIL = process.env.NEXT_PUBLIC_SALES_EMAIL ?? '';

type InviteRole = 'org:member' | 'org:admin';
const INVITE_ROLES: { value: InviteRole; label: string }[] = [
  { value: 'org:member', label: 'Member' },
  { value: 'org:admin', label: 'Admin' },
];

export default function SettingsPage() {
  const [tab, setTab] = useState('Team');
  const { toast } = useToast();
  // Real workspace + roster from Clerk (empty + flagged in demo mode).
  const org = useCurrentOrg();
  const { members, loading: membersLoading, demo: membersDemo } = useOrgMembers();
  const { inviteMember, openOrganizationProfile } = useOrgActions();
  const [confirmReset, setConfirmReset] = useState<{ count: number } | null>(null);
  const [resetting, setResetting] = useState(false);
  const workerConnected = isWorkerConnected();
  // Danger zone (Reset Worker Data) is hidden by default and only shown
  // when NEXT_PUBLIC_SHOW_DANGER_ZONE === 'true'. Keeps the destructive
  // affordance available to operators while keeping it out of customer view.
  const showDangerZone = process.env.NEXT_PUBLIC_SHOW_DANGER_ZONE === 'true';

  const [inviteEmail, setInviteEmail] = useState('');
  const [inviteRole, setInviteRole] = useState<InviteRole>('org:member');
  const [inviting, setInviting] = useState(false);
  const [inviteError, setInviteError] = useState<string | null>(null);

  const onSendInvite = async () => {
    const email = inviteEmail.trim();
    if (!EMAIL_RE.test(email)) {
      setInviteError('Enter a valid email address.');
      return;
    }
    if (!inviteMember) return;
    setInviting(true);
    setInviteError(null);
    try {
      await inviteMember(email, inviteRole);
      toast(`Invitation sent to ${email}`, { type: 'success' });
      setInviteEmail('');
    } catch (err) {
      // Clerk's error carries a readable `errors[0].longMessage`; fall back to
      // a plain sentence, never the raw object.
      const clerkMsg = (err as { errors?: { longMessage?: string; message?: string }[] } | null)?.errors?.[0];
      setInviteError(clerkMsg?.longMessage ?? clerkMsg?.message ?? 'The invitation could not be sent. Try again.');
    } finally {
      setInviting(false);
    }
  };

  const onContactFondok = () => {
    if (!SALES_EMAIL) return;
    const url = `mailto:${SALES_EMAIL}?subject=${encodeURIComponent('Fondok enterprise inquiry')}`;
    // _blank keeps the mail client invocation from replacing the tab in
    // browsers that route the protocol via a web client.
    window.open(url, '_blank');
  };

  const openResetModal = async () => {
    if (!workerConnected) return;
    try {
      const deals = await api.deals.list();
      setConfirmReset({ count: deals.length });
    } catch (err) {
      toast(`Couldn't list deals — ${userMessage(err)}`, { type: 'error' });
    }
  };

  const onConfirmReset = async () => {
    setResetting(true);
    try {
      const deals = await api.deals.list();
      if (deals.length === 0) {
        toast('No deals to archive', { type: 'info' });
        setConfirmReset(null);
        setResetting(false);
        return;
      }
      let archived = 0;
      let failed = 0;
      for (const d of deals) {
        try {
          await api.deals.delete(d.id);
          archived += 1;
          toast(`Archived ${d.name || d.id.slice(0, 8)}`, {
            type: 'info',
            duration: 1500,
          });
        } catch (err) {
          failed += 1;
          toast(`Couldn't archive ${d.name} — ${userMessage(err)}`, { type: 'error' });
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

  const orgManaged = (
    <span className="text-[11.5px] text-ink-500">
      Managed in your organization settings
      {openOrganizationProfile && (
        <>
          {' · '}
          <button
            type="button"
            onClick={openOrganizationProfile}
            className="text-brand-700 hover:text-brand-900 font-medium inline-flex items-center gap-1"
          >
            Open <ExternalLink size={11} aria-hidden="true" />
          </button>
        </>
      )}
    </span>
  );

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
                Invite your colleagues. <span className="font-semibold">Members</span> can run models
                and edit assumptions. <span className="font-semibold">Admins</span> can also change
                workspace settings and manage the team.
              </>
            }
          />
          {inviteMember ? (
            <Card className="p-5" data-testid="settings-invite">
              <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Invite Team Member</h3>
              <p className="text-[12px] text-ink-500 mb-4">Add a colleague to your workspace. They receive an email invitation from Fondok.</p>
              <div className="flex items-center gap-2">
                <input
                  value={inviteEmail}
                  onChange={(e) => { setInviteEmail(e.target.value); setInviteError(null); }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void onSendInvite();
                  }}
                  placeholder="colleague@company.com"
                  type="email"
                  aria-label="Colleague's email"
                  className="flex-1 px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                />
                <select
                  value={inviteRole}
                  onChange={(e) => setInviteRole(e.target.value as InviteRole)}
                  aria-label="Role"
                  className="px-3 py-2 text-[13px] bg-white border border-border rounded-md"
                >
                  {INVITE_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
                </select>
                <Button variant="primary" onClick={() => void onSendInvite()} disabled={inviting}>
                  {inviting && <Loader2 size={12} className="animate-spin" />}
                  {inviting ? 'Sending…' : 'Send Invite'}
                </Button>
              </div>
              {inviteError && (
                <p className="text-[12px] text-danger-700 mt-2" role="alert">{inviteError}</p>
              )}
            </Card>
          ) : (
            <Card className="p-5" data-testid="settings-invite-unavailable">
              <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Invite Team Member</h3>
              <p className="text-[12px] text-ink-500">
                {membersDemo
                  ? 'Team invitations are sent through your organization once you sign in.'
                  : 'Invitations are available once your workspace is active.'}
              </p>
            </Card>
          )}

          <Card>
            <div className="px-5 py-4 border-b border-border flex items-center justify-between gap-3">
              <h3 className="text-[14px] font-semibold text-ink-900">
                Team Members{!membersLoading && members.length > 0 ? ` (${members.length})` : ''}
              </h3>
              {openOrganizationProfile && (
                <Button variant="secondary" size="sm" onClick={openOrganizationProfile}>
                  Manage roles <ExternalLink size={11} aria-hidden="true" />
                </Button>
              )}
            </div>
            {membersLoading ? (
              <div className="px-5 py-6 space-y-3">
                {[0, 1].map((i) => (
                  <div key={i} className="flex items-center gap-3 animate-pulse">
                    <div className="w-9 h-9 rounded-full bg-ink-300/30" />
                    <div className="flex-1">
                      <div className="h-3 bg-ink-300/30 rounded w-1/3 mb-1.5" />
                      <div className="h-2.5 bg-ink-300/20 rounded w-1/4" />
                    </div>
                  </div>
                ))}
              </div>
            ) : members.length === 0 ? (
              <div className="px-5 py-8 text-center">
                <div className="text-[13px] font-medium text-ink-900">
                  {membersDemo ? 'Sign in to manage your team' : 'No teammates yet'}
                </div>
                <p className="text-[12px] text-ink-500 mt-1 max-w-sm mx-auto leading-relaxed">
                  {membersDemo
                    ? 'Team management runs through your organization once you sign in.'
                    : 'Invite a colleague above — they’ll appear here once they accept.'}
                </p>
              </div>
            ) : (
              members.map((m, i) => {
                const roleLabel = m.role === 'org:admin' ? 'Admin' : m.role === 'org:member' ? 'Member' : '—';
                return (
                  <div key={m.id} className={cn('flex items-center gap-3 px-5 py-4', i < members.length - 1 && 'border-b border-border')}>
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
                    <Badge tone={m.role === 'org:admin' ? 'blue' : 'gray'}>{roleLabel}</Badge>
                    {openOrganizationProfile && (
                      <KebabMenu
                        items={[
                          { label: 'Manage in organization…', onSelect: openOrganizationProfile },
                        ]}
                      />
                    )}
                  </div>
                );
              })
            )}
          </Card>
        </div>
      )}

      {tab === 'Workspace' && (
        <div className="space-y-5">
          <IntroCard
            dismissKey="settings-workspace-intro"
            title="Your workspace"
            body={
              <>
                Your workspace is the institution Fondok registered for you. Its name and address are
                managed in your organization settings; the hint preferences below are yours.
              </>
            }
          />
          <Card className="p-5" data-testid="settings-workspace">
            <div className="flex items-center justify-between gap-3 mb-4">
              <h3 className="text-[14px] font-semibold text-ink-900">Workspace Details</h3>
              {orgManaged}
            </div>
            <div className="space-y-4">
              <ReadOnlyField label="Workspace Name" value={org.name} />
              <ReadOnlyField label="Workspace URL" value={org.url && org.url !== 'none' ? org.url : '—'} prefix="fondok.ai/" />
            </div>
          </Card>

          <HintsSection />

          {SALES_EMAIL && (
            <Card className="p-5 bg-brand-50 border-brand-100" data-testid="settings-contact">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <h3 className="text-[14px] font-semibold text-ink-900">Integrations and custom data feeds</h3>
                  <p className="text-[12px] text-ink-700 mt-1">
                    Comp sets and market data come from the STR and market studies uploaded on each deal.
                    For a direct data feed or a custom integration, talk to Fondok.
                  </p>
                </div>
                <Button variant="secondary" onClick={onContactFondok}>
                  <Mail size={13} aria-hidden="true" /> Contact Fondok
                </Button>
              </div>
            </Card>
          )}

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
    </div>
  );
}

function ReadOnlyField({
  label, value, prefix,
}: {
  label: string;
  value: string;
  prefix?: string;
}) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <div className="flex items-center bg-ink-50 border border-border rounded-md">
        {prefix && <span className="pl-3 text-[12.5px] text-ink-500">{prefix}</span>}
        <input
          value={value}
          readOnly
          aria-readonly="true"
          aria-label={label}
          className="flex-1 px-3 py-2 text-[13px] bg-transparent rounded-md text-ink-700 focus:outline-none"
        />
      </div>
    </div>
  );
}

function HintsSection() {
  const { enabled, setEnabled } = useHintsEnabled();
  const { toast } = useToast();
  const onReset = () => {
    const removed = resetAllCoachMarks();
    toast(`Reset ${removed} dismissed hint${removed === 1 ? '' : 's'} — they will reappear on next visit.`, {
      type: 'success',
    });
  };
  return (
    <Card className="p-5">
      <div className="flex items-center gap-2 mb-1">
        <Sparkles size={14} className="text-brand-500" aria-hidden="true" />
        <h3 className="text-[14px] font-semibold text-ink-900">Hints &amp; coaching</h3>
      </div>
      <p className="text-[12px] text-ink-500 mb-4 leading-relaxed">
        Fondok ships with one-time contextual hints that appear next to UI you have
        not interacted with yet. Turn them off if your analysts already know the workflow,
        or reset them so dismissed hints reappear during onboarding.
      </p>
      <div className="flex items-center justify-between py-2 border-b border-border">
        <div>
          <div className="text-[13px] font-medium text-ink-900">Show contextual coach marks</div>
          <div className="text-[12px] text-ink-500 mt-0.5">When off, every coach mark and the first-visit tour are silenced for this browser.</div>
        </div>
        <Toggle on={enabled} onChange={setEnabled} />
      </div>
      <div className="flex items-center justify-between py-3">
        <div>
          <div className="text-[13px] font-medium text-ink-900">Reset all coach marks</div>
          <div className="text-[12px] text-ink-500 mt-0.5">Clears every &ldquo;dismissed&rdquo; record so the hints surface again — useful for onboarding a new teammate.</div>
        </div>
        <Button variant="secondary" size="sm" onClick={onReset}>
          <RotateCcw size={12} aria-hidden="true" /> Reset hints
        </Button>
      </div>
    </Card>
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
