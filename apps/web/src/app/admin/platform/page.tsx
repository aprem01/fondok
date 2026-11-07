'use client';
// Platform — the Fondok admin's view across every firm: who is using Fondok,
// what it costs, what is failing and what is working. Visible only to an
// admin of a serving internal workspace; the worker (`GET /admin/platform`)
// and `/api/admin/platform/people` enforce the same rule server-side.
// Numbers that cannot be computed render as "—", never as an estimate.

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Eye, Lock, RefreshCw } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import {
  api,
  userMessage,
  type DailySeries,
  type PlatformDays,
  type PlatformFirm,
  type PlatformOverview,
  type PlatformSignal,
} from '@/lib/api';
import { useCurrentOrg, useCurrentRole } from '@/lib/auth';
import { useIsInternalAdmin } from '@/lib/useInternalAdmin';
import type { PlatformOrgPeople, PlatformPerson } from '@/app/api/admin/platform/people/route';
import { setSupportSession } from '@/lib/supportAccess';

// ─────────────────────────── formatting ───────────────────────────

const DASH = '—';
const nf = new Intl.NumberFormat('en-US');
const num = (n: number | null | undefined) => (n == null ? DASH : nf.format(n));
const pct = (r: number | null | undefined) => (r == null ? DASH : `${(r * 100).toFixed(r > 0 && r < 0.1 ? 1 : 0)}%`);
const usd = (n: number | null | undefined) =>
  n == null
    ? DASH
    : n.toLocaleString('en-US', {
        style: 'currency',
        currency: 'USD',
        minimumFractionDigits: n < 100 ? 2 : 0,
        maximumFractionDigits: n < 100 ? 2 : 0,
      });

function ago(isoStr: string | null | undefined): string {
  if (!isoStr) return DASH;
  const ms = Date.now() - new Date(isoStr).getTime();
  if (!Number.isFinite(ms)) return DASH;
  const min = Math.round(ms / 60000);
  if (min < 1) return 'just now';
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? 'yesterday' : `${d} days ago`;
}

const actionLabel = (a: string) => {
  const s = a.replace(/[._]+/g, ' ').trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
};

const STATUS_LABEL: Record<PlatformFirm['status'], string> = {
  active: 'Active',
  pending_review: 'Waiting for approval',
  suspended: 'Suspended',
};
const STATUS_TONE = { active: 'green', pending_review: 'amber', suspended: 'red' } as const;

const AREA_LABEL: Record<PlatformSignal['area'], string> = {
  access: 'Access',
  extraction: 'Documents',
  model: 'Model',
  ai: 'AI',
  quality: 'Quality',
  adoption: 'Adoption',
  collaboration: 'Teamwork',
};

/** Every calendar day in the window, oldest first, so gaps plot as zero. */
function windowDays(days: number): string[] {
  const out: string[] = [];
  const today = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate() - i));
    out.push(d.toISOString().slice(0, 10));
  }
  return out;
}

// ─────────────────────────── small pieces ───────────────────────────

function Sparkline({ series, days, label, format }: { series: DailySeries; days: string[]; label: string; format: (n: number) => string }) {
  const values = days.map((d) => series[d] ?? 0);
  const max = Math.max(...values, 0);
  const total = values.reduce((a, b) => a + b, 0);
  const w = 240;
  const h = 44;
  const step = values.length > 1 ? w / (values.length - 1) : w;
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${(h - 4 - (max ? (v / max) * (h - 8) : 0)).toFixed(1)}`);
  const last = values[values.length - 1] ?? 0;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[12px] text-ink-500">{label}</span>
        <span className="text-[13px] font-semibold tabular-nums text-ink-900">{format(total)}</span>
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} className="mt-1 h-11 w-full" preserveAspectRatio="none" role="img" aria-label={`${label}: ${format(total)} over ${days.length} days`}>
        <line x1="0" x2={w} y1={h - 4} y2={h - 4} className="stroke-ink-200" strokeWidth="1" />
        {max > 0 && (
          <>
            <polygon points={`0,${h - 4} ${pts.join(' ')} ${w},${h - 4}`} className="fill-brand-500/10" />
            <polyline points={pts.join(' ')} fill="none" className="stroke-brand-500" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
            <circle cx={w} cy={h - 4 - (last / max) * (h - 8)} r="2.5" className="fill-brand-500" />
          </>
        )}
      </svg>
      {max === 0 && <p className="text-[11px] text-ink-400">Nothing in this period</p>}
    </div>
  );
}

function Kpi({ label, value, note, tone }: { label: string; value: string; note?: string; tone?: 'bad' | 'good' }) {
  return (
    <Card className="p-4">
      <div className="text-[11.5px] font-medium uppercase tracking-[0.04em] text-ink-500">{label}</div>
      <div
        className={`mt-1.5 text-[22px] font-semibold tabular-nums ${
          tone === 'bad' ? 'text-danger-700' : tone === 'good' ? 'text-success-700' : 'text-ink-900'
        }`}
      >
        {value}
      </div>
      {note && <div className="mt-0.5 text-[11.5px] text-ink-500">{note}</div>}
    </Card>
  );
}

function SignalList({ title, items, kind, onFirm }: { title: string; items: PlatformSignal[]; kind: 'bad' | 'good'; onFirm: (id: string) => void }) {
  const Icon = kind === 'bad' ? AlertTriangle : CheckCircle2;
  return (
    <Card>
      <div className="flex items-center gap-2 border-b border-border px-5 py-3.5">
        <Icon size={15} className={kind === 'bad' ? 'text-danger-600' : 'text-success-600'} aria-hidden="true" />
        <h2 className="text-[14px] font-semibold text-ink-900">
          {title} <span className="font-normal text-ink-500">({items.length})</span>
        </h2>
      </div>
      {items.length === 0 ? (
        <p className="px-5 py-4 text-[13px] text-ink-500">{kind === 'bad' ? 'Nothing needs attention.' : 'Nothing to report yet.'}</p>
      ) : (
        <ul className="max-h-[360px] divide-y divide-border overflow-y-auto">
          {items.map((s, i) => (
            <li key={`${s.tenant_id}-${i}`} className="flex items-start gap-3 px-5 py-2.5">
              <Badge tone={kind === 'bad' ? 'red' : 'green'} className="mt-0.5 shrink-0">
                {AREA_LABEL[s.area] ?? s.area}
              </Badge>
              <p className="min-w-0 text-[13px] text-ink-700">
                <button type="button" className="font-semibold text-ink-900 hover:underline" onClick={() => onFirm(s.tenant_id)}>
                  {s.firm}
                </button>{' '}
                {s.text}
              </p>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function BarList({ rows, valueLabel }: { rows: { key: string; value: number; sub?: string }[]; valueLabel: (n: number) => string }) {
  const max = Math.max(...rows.map((r) => r.value), 0);
  if (!rows.length) return <p className="text-[13px] text-ink-500">Nothing in this period.</p>;
  return (
    <ul className="space-y-2">
      {rows.map((r) => (
        <li key={r.key}>
          <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
            <span className="truncate text-ink-700" title={r.key}>
              {r.key}
            </span>
            <span className="shrink-0 tabular-nums text-ink-900">
              {valueLabel(r.value)}
              {r.sub && <span className="ml-1.5 text-ink-500">{r.sub}</span>}
            </span>
          </div>
          <div className="mt-1 h-1.5 rounded-full bg-ink-100">
            <div className="h-1.5 rounded-full bg-brand-500" style={{ width: `${max ? Math.max(2, (r.value / max) * 100) : 0}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

// ─────────────────────────── support access ───────────────────────────

const SUPPORT_HOURS = [1, 4, 24] as const;

function SupportAccessForm({ firm }: { firm: PlatformFirm }) {
  const [reason, setReason] = useState('');
  const [hours, setHours] = useState<(typeof SUPPORT_HOURS)[number]>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const open = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const g = await api.admin.support.open({ tenant_id: firm.tenant_id, reason: reason.trim(), hours });
      setSupportSession({ grantId: g.id, tenantId: g.tenant_id, firm: g.firm ?? firm.name, expiresAt: g.expires_at });
      // Full navigation so nothing from your own workspace stays in memory.
      window.location.assign('/projects');
    } catch (err) {
      const msg = userMessage(err);
      setError(
        /support_access_requires_staff/.test(String((err as { body?: unknown })?.body ?? msg))
          ? 'Support access needs your Fondok staff sign-in with two-factor turned on. Sign out and in again after setting it up.'
          : msg,
      );
      setBusy(false);
    }
  };

  return (
    <form onSubmit={open} className="rounded-md border border-border bg-white p-4">
      <div className="flex items-center gap-2">
        <Eye size={14} className="text-ink-500" aria-hidden="true" />
        <h3 className="text-[13px] font-semibold text-ink-900">Open support access</h3>
      </div>
      <p className="mt-1 text-[12px] text-ink-500">
        See {firm.name}&apos;s deals read-only for a limited time. The start, each page you open and the end are
        recorded in {firm.name}&apos;s own activity log, which its admins can see.
      </p>
      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto_auto]">
        <label className="block">
          <span className="sr-only">Reason</span>
          <input
            className="h-9 w-full rounded-md border border-border bg-white px-3 text-[13px] text-ink-900 placeholder:text-ink-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            placeholder="Reason, e.g. investigating a failed T-12 extraction"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            minLength={10}
            maxLength={500}
            required
          />
        </label>
        <label className="block">
          <span className="sr-only">Duration</span>
          <select
            className="h-9 rounded-md border border-border bg-white px-2 text-[13px] text-ink-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            value={hours}
            onChange={(e) => setHours(Number(e.target.value) as (typeof SUPPORT_HOURS)[number])}
          >
            {SUPPORT_HOURS.map((h) => (
              <option key={h} value={h}>
                {h === 1 ? '1 hour' : `${h} hours`}
              </option>
            ))}
          </select>
        </label>
        <Button type="submit" variant="primary" size="sm" loading={busy} disabled={busy || reason.trim().length < 10}>
          Open read-only view
        </Button>
      </div>
      {error && (
        <p className="mt-2 text-[12px] text-danger-700" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

// ─────────────────────────── firm detail ───────────────────────────

function FirmDetail({
  firm,
  people,
  peopleLoaded,
  personById,
  fondokStaff,
  days,
}: {
  firm: PlatformFirm;
  people: PlatformOrgPeople | undefined;
  /** True once the team lookup has answered (with or without this firm). */
  peopleLoaded: boolean;
  personById: Map<string, PlatformPerson>;
  fondokStaff: Set<string>;
  days: string[];
}) {
  const activityById = new Map(firm.activity.people.map((p) => [p.actor_id, p]));
  const members = people?.members ?? [];
  const neverSignedIn = members.filter((m) => !m.lastSignInAt && !fondokStaff.has(m.userId)).length;
  const sectionTitle = 'text-[12px] font-semibold uppercase tracking-[0.04em] text-ink-500';

  return (
    <div className="grid gap-5 bg-ink-50/60 px-5 py-5 lg:grid-cols-3">
      {firm.kind === 'external' && firm.status === 'active' && (
        <section className="lg:col-span-3">
          <SupportAccessForm firm={firm} />
        </section>
      )}

      <section className="lg:col-span-2">
        <h3 className={sectionTitle}>
          Team{people && !people.error ? ` (${members.length})` : ''}{people?.pendingInvitations ? ` · ${people.pendingInvitations} invitation(s) pending` : ''}
          {neverSignedIn ? ` · ${neverSignedIn} never signed in` : ''}
        </h3>
        {people?.error && <p className="mt-2 text-[12.5px] text-danger-700">{people.error}</p>}
        {!people && !peopleLoaded && <p className="mt-2 text-[12.5px] text-ink-500">Loading team…</p>}
        {!people && peopleLoaded && (
          <p className="mt-2 text-[12.5px] text-ink-500">Team list unavailable. Activity below comes from Fondok&apos;s own records.</p>
        )}
        {people && !people.error && members.length === 0 && <p className="mt-2 text-[12.5px] text-ink-500">No members.</p>}
        {(members.length > 0 || firm.activity.people.length > 0) && (
          <div className="mt-2 overflow-x-auto">
            <table className="w-full text-left text-[12.5px]">
              <thead className="text-ink-500">
                <tr>
                  <th className="py-1.5 pr-3 font-medium">Person</th>
                  <th className="py-1.5 pr-3 font-medium">Role</th>
                  <th className="py-1.5 pr-3 font-medium">Last sign-in</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Actions in period</th>
                  <th className="py-1.5 font-medium">Last action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {members.map((m) => {
                  const act = activityById.get(m.userId);
                  return (
                    <tr key={m.userId}>
                      <td className="py-1.5 pr-3">
                        <div className="flex items-center gap-1.5">
                          <span className="text-ink-900">{m.name ?? m.email ?? m.userId}</span>
                          {fondokStaff.has(m.userId) && <Badge tone="blue">Fondok</Badge>}
                        </div>
                        {m.name && m.email && <div className="text-[11.5px] text-ink-500">{m.email}</div>}
                      </td>
                      <td className="py-1.5 pr-3 text-ink-700">{m.role === 'org:admin' ? 'Admin' : 'Member'}</td>
                      <td className="py-1.5 pr-3 text-ink-700">{m.lastSignInAt ? ago(m.lastSignInAt) : 'Never'}</td>
                      <td className="py-1.5 pr-3 text-right tabular-nums text-ink-900">{act ? num(act.events) : '0'}</td>
                      <td className="py-1.5 text-ink-700">{act ? ago(act.last_at) : DASH}</td>
                    </tr>
                  );
                })}
                {firm.activity.people
                  .filter((p) => !members.some((m) => m.userId === p.actor_id))
                  .map((p) => {
                    const known = personById.get(p.actor_id);
                    return (
                      <tr key={p.actor_id}>
                        <td className="py-1.5 pr-3 text-ink-700">
                          {known?.name ?? known?.email ?? (peopleLoaded && people && !people.error ? 'Former member or system' : p.actor_id)}
                          {fondokStaff.has(p.actor_id) && <Badge tone="blue" className="ml-1.5">Fondok</Badge>}
                        </td>
                        <td className="py-1.5 pr-3 text-ink-500">{DASH}</td>
                        <td className="py-1.5 pr-3 text-ink-500">{DASH}</td>
                        <td className="py-1.5 pr-3 text-right tabular-nums text-ink-900">{num(p.events)}</td>
                        <td className="py-1.5 text-ink-700">{ago(p.last_at)}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        )}

        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          <Sparkline series={firm.activity.daily_events} days={days} label="Actions" format={num} />
          <Sparkline series={firm.ai.daily_cost_usd} days={days} label="AI cost" format={usd} />
          <Sparkline series={firm.deals.daily} days={days} label="New deals" format={num} />
        </div>
      </section>

      <section className="space-y-4">
        <div>
          <h3 className={sectionTitle}>What they do most</h3>
          <div className="mt-2">
            <BarList rows={firm.activity.top_actions.map((a) => ({ key: actionLabel(a.action), value: a.count }))} valueLabel={num} />
          </div>
        </div>
        <div>
          <h3 className={sectionTitle}>Documents by type</h3>
          <div className="mt-2">
            <BarList
              rows={firm.documents.by_type.map((t) => ({
                key: t.doc_type,
                value: t.count,
                sub: t.failed ? `${t.failed} failed` : undefined,
              }))}
              valueLabel={num}
            />
          </div>
        </div>
      </section>

      <section>
        <h3 className={sectionTitle}>AI spend by step</h3>
        <div className="mt-2">
          <BarList
            rows={firm.ai.by_agent.map((a) => ({ key: a.agent, value: a.cost_usd, sub: a.errors ? `${a.errors} errors` : undefined }))}
            valueLabel={usd}
          />
        </div>
        <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[12.5px]">
          <dt className="text-ink-500">Cost per deal</dt>
          <dd className="text-right tabular-nums text-ink-900">{usd(firm.ai.cost_per_deal_usd)}</dd>
          <dt className="text-ink-500">Cache hit rate</dt>
          <dd className="text-right tabular-nums text-ink-900">{pct(firm.ai.cache_hit_rate)}</dd>
          <dt className="text-ink-500">Avg response time</dt>
          <dd className="text-right tabular-nums text-ink-900">
            {firm.ai.avg_latency_ms == null ? DASH : `${(firm.ai.avg_latency_ms / 1000).toFixed(1)} s`}
          </dd>
        </dl>
      </section>

      <section>
        <h3 className={sectionTitle}>Model steps</h3>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[12.5px]">
          <dt className="text-ink-500">Model runs</dt>
          <dd className="text-right tabular-nums text-ink-900">{num(firm.engines.model_runs)}</dd>
          <dt className="text-ink-500">Step failure rate</dt>
          <dd className="text-right tabular-nums text-ink-900">{pct(firm.engines.failure_rate)}</dd>
        </dl>
        <ul className="mt-2 space-y-1 text-[12.5px]">
          {firm.engines.by_engine
            .filter((e) => e.failed)
            .slice(0, 6)
            .map((e) => (
              <li key={e.engine} className="flex justify-between gap-3">
                <span className="truncate text-ink-700">{e.engine}</span>
                <span className="shrink-0 text-danger-700">
                  {e.failed}/{e.runs} failed · {ago(e.last_failed_at)}
                </span>
              </li>
            ))}
          {firm.engines.failed === 0 && firm.engines.outputs > 0 && <li className="text-success-700">No failed steps.</li>}
        </ul>
      </section>

      <section>
        <h3 className={sectionTitle}>Quality and teamwork</h3>
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[12.5px]">
          <dt className="text-ink-500">Verification pass rate</dt>
          <dd className="text-right tabular-nums text-ink-900">{pct(firm.quality.avg_pass_rate)}</dd>
          <dt className="text-ink-500">Lowest pass rate</dt>
          <dd className="text-right tabular-nums text-ink-900">{pct(firm.quality.min_pass_rate)}</dd>
          {Object.entries(firm.quality.critic_findings).map(([sev, n]) => (
            <Fragment key={sev}>
              <dt className="text-ink-500">Reviewer findings · {sev}</dt>
              <dd className="text-right tabular-nums text-ink-900">{num(n)}</dd>
            </Fragment>
          ))}
          <dt className="text-ink-500">Memo edits</dt>
          <dd className="text-right tabular-nums text-ink-900">{num(firm.collaboration.memo_edits)}</dd>
          <dt className="text-ink-500">Comments (open)</dt>
          <dd className="text-right tabular-nums text-ink-900">
            {num(firm.collaboration.comments_created)} ({num(firm.collaboration.comments_open)})
          </dd>
          <dt className="text-ink-500">Deals by status</dt>
          <dd className="text-right text-ink-900">
            {firm.deals.by_status.length ? firm.deals.by_status.map((s) => `${s.status} ${s.count}`).join(' · ') : DASH}
          </dd>
        </dl>
      </section>
    </div>
  );
}

// ─────────────────────────── page ───────────────────────────

export default function PlatformPage() {
  const role = useCurrentRole();
  const org = useCurrentOrg();
  const isInternalAdmin = useIsInternalAdmin(role, org.id);

  const [days, setDays] = useState<PlatformDays>(30);
  const [data, setData] = useState<PlatformOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [people, setPeople] = useState<Record<string, PlatformOrgPeople> | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async (d: PlatformDays) => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.admin.platform(d);
      setData(res);
      const orgIds = res.firms.map((f) => f.clerk_org_id);
      fetch('/api/admin/platform/people', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ orgIds }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((j: { orgs?: Record<string, PlatformOrgPeople> } | null) => setPeople(j?.orgs ?? {}))
        .catch(() => setPeople({}));
    } catch (e) {
      setError(userMessage(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (isInternalAdmin) void load(days);
  }, [isInternalAdmin, days, load]);

  const windowDayList = useMemo(() => windowDays(days), [days]);

  const { personById, fondokStaff } = useMemo(() => {
    const byId = new Map<string, PlatformPerson>();
    const staff = new Set<string>();
    for (const f of data?.firms ?? []) {
      const p = people?.[f.clerk_org_id];
      for (const m of p?.members ?? []) {
        byId.set(m.userId, m);
        if (f.kind === 'internal') staff.add(m.userId);
      }
    }
    return { personById: byId, fondokStaff: staff };
  }, [data, people]);

  if (!isInternalAdmin) {
    return (
      <div className="px-6 py-10">
        <Card className="mx-auto max-w-md p-6 text-center">
          <Lock size={20} className="mx-auto text-ink-500" aria-hidden="true" />
          <h1 className="mt-3 text-[15px] font-semibold text-ink-900">Fondok admins only</h1>
          <p className="mt-1 text-[13px] text-ink-500">Switch to the Fondok workspace to see every firm.</p>
        </Card>
      </div>
    );
  }

  const t = data?.totals;
  const bad = data?.signals.filter((s) => s.kind === 'bad') ?? [];
  const good = data?.signals.filter((s) => s.kind === 'good') ?? [];
  const jumpTo = (tenantId: string) => {
    setOpen(tenantId);
    requestAnimationFrame(() => document.getElementById(`firm-${tenantId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  };

  return (
    <div className="space-y-5 px-6 py-6">
      <PageHeader
        eyebrow="Fondok admin"
        title="Platform"
        subtitle="How every firm uses Fondok: activity, AI cost, what is failing and what is working."
        action={
          <div className="flex items-center gap-2">
            <div role="radiogroup" aria-label="Period" className="flex rounded-md border border-border bg-white p-0.5">
              {([7, 30, 90] as PlatformDays[]).map((d) => (
                <button
                  key={d}
                  type="button"
                  role="radio"
                  aria-checked={days === d}
                  onClick={() => setDays(d)}
                  className={`rounded px-2.5 py-1 text-[12px] font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 ${
                    days === d ? 'bg-ink-900 text-white' : 'text-ink-600 hover:bg-ink-50'
                  }`}
                >
                  {d} days
                </button>
              ))}
            </div>
            <Button variant="ghost" size="sm" onClick={() => void load(days)} loading={loading}>
              <RefreshCw size={13} aria-hidden="true" /> Refresh
            </Button>
          </div>
        }
      />

      {error && (
        <Card className="p-4">
          <p className="text-[13px] text-danger-700" role="alert">
            {error}
          </p>
        </Card>
      )}
      {!data && !error && <p className="text-[13px] text-ink-500">Loading…</p>}

      {data && t && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
            <Kpi label="Firms" value={num(t.firms)} note={`${t.firms_active} active · ${t.firms_pending} waiting`} />
            <Kpi label="People active" value={num(t.active_people)} note={`${num(t.events)} actions`} />
            <Kpi label="New deals" value={num(t.deals_created)} note={`${num(t.deals_total)} in total`} />
            <Kpi
              label="Documents"
              value={num(t.documents_uploaded)}
              note={`${pct(t.document_failure_rate)} failed · ${num(t.documents_stuck)} stuck`}
              tone={t.documents_failed || t.documents_stuck ? 'bad' : undefined}
            />
            <Kpi label="AI cost" value={usd(t.ai_cost_usd)} note={`${num(t.ai_calls)} AI calls`} />
            <Kpi
              label="AI error rate"
              value={pct(t.ai_error_rate)}
              note={`${num(t.ai_errors)} errors`}
              tone={(t.ai_error_rate ?? 0) >= 0.05 ? 'bad' : undefined}
            />
            <Kpi
              label="Model runs"
              value={num(t.model_runs)}
              note={`${pct(t.engine_failure_rate)} of steps failed`}
              tone={(t.engine_failure_rate ?? 0) >= 0.05 ? 'bad' : undefined}
            />
          </div>

          <Card className="grid gap-6 p-5 md:grid-cols-3">
            <Sparkline series={data.daily.events} days={windowDayList} label="Actions per day" format={num} />
            <Sparkline series={data.daily.cost_usd} days={windowDayList} label="AI cost per day" format={usd} />
            <Sparkline series={data.daily.deals_created} days={windowDayList} label="New deals per day" format={num} />
          </Card>

          <div className="grid gap-5 lg:grid-cols-2">
            <SignalList title="Needs attention" items={bad} kind="bad" onFirm={jumpTo} />
            <SignalList title="Going well" items={good} kind="good" onFirm={jumpTo} />
          </div>

          <div className="grid gap-5 lg:grid-cols-2">
            <Card className="p-5">
              <h2 className="text-[14px] font-semibold text-ink-900">AI cost by model</h2>
              <p className="mt-0.5 text-[12px] text-ink-500">Where the spend goes, all firms combined.</p>
              <div className="mt-4">
                <BarList
                  rows={data.cost_by_model.map((m) => ({ key: m.model, value: m.cost_usd, sub: `${num(m.calls)} calls` }))}
                  valueLabel={usd}
                />
              </div>
            </Card>
            <Card className="p-5">
              <h2 className="text-[14px] font-semibold text-ink-900">AI cost by step</h2>
              <p className="mt-0.5 text-[12px] text-ink-500">The most expensive steps are the first to optimise.</p>
              <div className="mt-4">
                <BarList
                  rows={data.cost_by_agent.map((a) => ({
                    key: a.agent,
                    value: a.cost_usd,
                    sub: a.errors ? `${num(a.errors)} errors` : `${num(a.calls)} calls`,
                  }))}
                  valueLabel={usd}
                />
              </div>
            </Card>
          </div>

          <Card>
            <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border px-5 py-3.5">
              <h2 className="text-[14px] font-semibold text-ink-900">Firms ({data.firms.length})</h2>
              <span className="text-[11.5px] text-ink-500">Updated {ago(data.generated_at)} · open a firm for its team and details</span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[880px] text-left text-[13px]">
                <thead className="border-b border-border text-[11.5px] uppercase tracking-[0.04em] text-ink-500">
                  <tr>
                    <th className="px-5 py-2.5 font-medium">Firm</th>
                    <th className="px-3 py-2.5 font-medium">People</th>
                    <th className="px-3 py-2.5 font-medium">Last activity</th>
                    <th className="px-3 py-2.5 text-right font-medium">Deals</th>
                    <th className="px-3 py-2.5 text-right font-medium">Docs</th>
                    <th className="px-3 py-2.5 text-right font-medium">Failed / stuck</th>
                    <th className="px-3 py-2.5 text-right font-medium">AI cost</th>
                    <th className="px-3 py-2.5 text-right font-medium">AI errors</th>
                    <th className="px-5 py-2.5 text-right font-medium">Attention</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {data.firms.map((f) => {
                    const p = people?.[f.clerk_org_id];
                    const isOpen = open === f.tenant_id;
                    return (
                      <Fragment key={f.tenant_id}>
                        <tr id={`firm-${f.tenant_id}`} className={`scroll-mt-20 ${isOpen ? 'bg-ink-50/60' : 'hover:bg-ink-50/40'}`}>
                          <td className="px-5 py-3">
                            <button
                              type="button"
                              aria-expanded={isOpen}
                              onClick={() => setOpen(isOpen ? null : f.tenant_id)}
                              className="flex items-center gap-2 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                            >
                              {isOpen ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
                              <span className="font-semibold text-ink-900">{f.name}</span>
                              {f.kind === 'internal' && <Badge tone="blue">Fondok</Badge>}
                              {f.status !== 'active' && (
                                <Badge tone={STATUS_TONE[f.status]} dot>
                                  {STATUS_LABEL[f.status]}
                                </Badge>
                              )}
                            </button>
                          </td>
                          <td className="px-3 py-3 tabular-nums text-ink-700">
                            {num(f.activity.active_people)} active
                            <span className="text-ink-500"> / {p ? num(p.members.length) : DASH}</span>
                          </td>
                          <td className="px-3 py-3 text-ink-700">{ago(f.activity.last_activity_at)}</td>
                          <td className="px-3 py-3 text-right tabular-nums text-ink-900">
                            {num(f.deals.total)}
                            {f.deals.created > 0 && <span className="ml-1 text-success-700">+{f.deals.created}</span>}
                          </td>
                          <td className="px-3 py-3 text-right tabular-nums text-ink-900">{num(f.documents.uploaded)}</td>
                          <td
                            className={`px-3 py-3 text-right tabular-nums ${
                              f.documents.failed || f.documents.stuck ? 'text-danger-700' : 'text-ink-500'
                            }`}
                          >
                            {f.documents.failed} / {f.documents.stuck}
                          </td>
                          <td className="px-3 py-3 text-right tabular-nums text-ink-900">{usd(f.ai.cost_usd)}</td>
                          <td className={`px-3 py-3 text-right tabular-nums ${(f.ai.error_rate ?? 0) >= 0.05 ? 'text-danger-700' : 'text-ink-700'}`}>
                            {pct(f.ai.error_rate)}
                          </td>
                          <td className="px-5 py-3 text-right">
                            {f.attention ? <Badge tone="red">{f.attention}</Badge> : <Badge tone="green">OK</Badge>}
                          </td>
                        </tr>
                        {isOpen && (
                          <tr>
                            <td colSpan={9} className="p-0">
                              <FirmDetail firm={f} people={p} peopleLoaded={people !== null} personById={personById} fondokStaff={fondokStaff} days={windowDayList} />
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
