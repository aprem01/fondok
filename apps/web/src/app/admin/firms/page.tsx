'use client';
// Firms — the Fondok admin's list of every institution on the platform.
// Visible only to an admin of a serving internal workspace (the worker's
// `GET /admin/tenants` scope `all`); everyone else sees a lock card. The
// worker and the /api/admin/firms routes enforce the same rule server-side.

import { useCallback, useEffect, useState } from 'react';
import { Building2, Lock, Mail, RefreshCw } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { api, userMessage, type RegisteredTenant } from '@/lib/api';
import { useCurrentOrg, useCurrentRole } from '@/lib/auth';
import { useIsInternalAdmin } from '@/lib/useInternalAdmin';

const STATUS_LABEL: Record<RegisteredTenant['status'], string> = {
  active: 'Active',
  pending_review: 'Waiting for approval',
  suspended: 'Suspended',
};
const STATUS_TONE = { active: 'green', pending_review: 'amber', suspended: 'red' } as const;

async function postJson(url: string, body: unknown): Promise<{ ok: boolean; data: Record<string, unknown> }> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
  return { ok: res.ok, data };
}

export default function FirmsPage() {
  const role = useCurrentRole();
  const org = useCurrentOrg();
  const isInternalAdmin = useIsInternalAdmin(role, org.id);
  const { toast } = useToast();

  const [firms, setFirms] = useState<RegisteredTenant[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [adminEmail, setAdminEmail] = useState('');
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const [inviteFor, setInviteFor] = useState<string | null>(null);
  const [inviteEmail, setInviteEmail] = useState('');

  const load = useCallback(async () => {
    setError(null);
    try {
      const res = await api.admin.tenants.list();
      setFirms([...res.tenants].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '')));
    } catch (e) {
      setError(userMessage(e));
    }
  }, []);

  useEffect(() => {
    if (isInternalAdmin) void load();
  }, [isInternalAdmin, load]);

  if (!isInternalAdmin) {
    return (
      <div className="px-6 py-10">
        <Card className="mx-auto max-w-md p-6 text-center">
          <Lock size={20} className="mx-auto text-ink-500" aria-hidden="true" />
          <h1 className="mt-3 text-[15px] font-semibold text-ink-900">Fondok admins only</h1>
          <p className="mt-1 text-[13px] text-ink-500">This page manages every firm on the platform.</p>
        </Card>
      </div>
    );
  }

  const createFirm = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setCreating(true);
    const { ok, data } = await postJson('/api/admin/firms', { name, adminEmail });
    setCreating(false);
    if (ok) {
      toast(`${name.trim()} is set up. Invitation sent to ${String(data.invited)}.`, { type: 'success' });
      setName('');
      setAdminEmail('');
    } else {
      setFormError(String(data.error ?? 'The firm could not be set up.'));
    }
    void load();
  };

  const setStatus = async (firm: RegisteredTenant, status: RegisteredTenant['status']) => {
    setBusy(firm.id);
    try {
      await api.admin.tenants.register({ clerk_org_id: firm.clerk_org_id, name: firm.name, kind: firm.kind, status });
      toast(`${firm.name}: ${STATUS_LABEL[status]}`, { type: 'success' });
      await load();
    } catch (e) {
      toast(userMessage(e), { type: 'error' });
    } finally {
      setBusy(null);
    }
  };

  const sendInvite = async (firm: RegisteredTenant) => {
    setBusy(firm.id);
    const { ok, data } = await postJson('/api/admin/firms/invite', { orgId: firm.clerk_org_id, email: inviteEmail });
    setBusy(null);
    if (ok) {
      toast(`Invitation sent to ${String(data.invited)}.`, { type: 'success' });
      setInviteFor(null);
      setInviteEmail('');
    } else {
      toast(String(data.error ?? 'The invitation could not be sent.'), { type: 'error' });
    }
  };

  const inputCls =
    'h-9 w-full rounded-md border border-border bg-white px-3 text-[13px] text-ink-900 placeholder:text-ink-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500';

  return (
    <div className="px-6 py-6 space-y-5">
      <PageHeader
        eyebrow="Fondok admin"
        title="Firms"
        subtitle="Every institution on Fondok. Each firm sees only its own deals; its admins invite their own team."
        action={
          <Button variant="ghost" size="sm" onClick={() => void load()}>
            <RefreshCw size={13} aria-hidden="true" /> Refresh
          </Button>
        }
      />

      <Card className="p-5">
        <h2 className="text-[14px] font-semibold text-ink-900">Add a firm</h2>
        <p className="mt-1 text-[12px] text-ink-500">
          Creates the firm&apos;s workspace, approves it and emails its first admin an invitation. You are added
          to the firm as a member, so you can open its workspace to support it; the firm sees you in its team list.
        </p>
        <form onSubmit={createFirm} className="mt-4 grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <label className="block">
            <span className="sr-only">Firm name</span>
            <input className={inputCls} placeholder="Firm name" value={name} onChange={(e) => setName(e.target.value)} required />
          </label>
          <label className="block">
            <span className="sr-only">First admin&apos;s email</span>
            <input
              className={inputCls}
              type="email"
              placeholder="First admin's email"
              value={adminEmail}
              onChange={(e) => setAdminEmail(e.target.value)}
              required
            />
          </label>
          <Button type="submit" variant="primary" loading={creating} disabled={creating}>
            Set up firm
          </Button>
        </form>
        {formError && (
          <p className="mt-2 text-[12px] text-danger-700" role="alert">
            {formError}
          </p>
        )}
      </Card>

      <Card>
        <div className="border-b border-border px-5 py-4">
          <h2 className="text-[14px] font-semibold text-ink-900">
            All firms{firms ? ` (${firms.length})` : ''}
          </h2>
        </div>
        {error && <p className="px-5 py-4 text-[13px] text-danger-700">{error}</p>}
        {!firms && !error && <p className="px-5 py-4 text-[13px] text-ink-500">Loading…</p>}
        {firms && firms.length === 0 && <p className="px-5 py-4 text-[13px] text-ink-500">No firms yet.</p>}
        <ul className="divide-y divide-border">
          {firms?.map((f) => (
            <li key={f.id} className="px-5 py-4">
              <div className="flex flex-wrap items-center gap-3">
                <Building2 size={16} className="text-ink-500" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13.5px] font-semibold text-ink-900">{f.name}</span>
                    {f.kind === 'internal' && <Badge tone="blue">Fondok</Badge>}
                    <Badge tone={STATUS_TONE[f.status]} dot>
                      {STATUS_LABEL[f.status]}
                    </Badge>
                  </div>
                  <div className="mt-0.5 text-[11.5px] text-ink-500">
                    {f.created_at ? `Added ${new Date(f.created_at).toLocaleDateString()}` : 'Added —'}
                  </div>
                </div>
                {f.kind === 'external' && (
                  <div className="flex flex-wrap items-center gap-2">
                    {f.status !== 'active' && (
                      <Button size="sm" variant="primary" loading={busy === f.id} onClick={() => void setStatus(f, 'active')}>
                        {f.status === 'suspended' ? 'Reactivate' : 'Approve'}
                      </Button>
                    )}
                    {f.status === 'active' && (
                      <Button
                        size="sm"
                        variant="ghost"
                        loading={busy === f.id}
                        onClick={() => {
                          if (window.confirm(`Suspend ${f.name}? Its team loses access until you reactivate it.`)) {
                            void setStatus(f, 'suspended');
                          }
                        }}
                      >
                        Suspend
                      </Button>
                    )}
                    <Button size="sm" variant="secondary" onClick={() => setInviteFor(inviteFor === f.id ? null : f.id)}>
                      <Mail size={13} aria-hidden="true" /> Invite admin
                    </Button>
                  </div>
                )}
              </div>
              {inviteFor === f.id && (
                <form
                  className="mt-3 flex flex-wrap items-center gap-2 pl-7"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void sendInvite(f);
                  }}
                >
                  <input
                    className={`${inputCls} max-w-xs`}
                    type="email"
                    placeholder="Admin's email"
                    aria-label={`Admin's email for ${f.name}`}
                    value={inviteEmail}
                    onChange={(e) => setInviteEmail(e.target.value)}
                    required
                  />
                  <Button size="sm" type="submit" variant="primary" loading={busy === f.id}>
                    Send invitation
                  </Button>
                </form>
              )}
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
