'use client';
//
// Wave 4 W4.3 — tenant-wide Compliance Explorer.
//
// One searchable / filterable table over every audit_log row in the
// tenant. Each row drills into the same side-panel the per-deal Activity
// Feed uses so analysts get one consistent affordance for "show me the
// details".
//
// Permission model: today the worker doesn't ship RBAC, so every
// signed-in user can hit /audit/explorer inside their own tenant. This
// page renders an "Admin only" lock when the current persona's role
// isn't admin/principal — keeps the visual contract in place for when
// the role check lands in the worker.

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Lock, RefreshCw, Search, ShieldAlert, X,
} from 'lucide-react';
import { Button } from '@/components/ui/Button';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { useToast } from '@/components/ui/Toast';
import { useCurrentUser, useCurrentRole } from '@/lib/auth';
import { api, isWorkerConnected, WorkerError } from '@/lib/api';
import type {
  AuditEntry,
  AuditSeverity,
  ExplorerQuery,
  ExplorerResponse,
} from '@/lib/api';
import { cn } from '@/lib/format';

const ENTITY_TYPES = [
  'deal', 'scenario', 'override', 'document', 'engine_run',
  'export', 'comp_transaction', 'portfolio_library_entry', 'memo',
];

const SEVERITIES: AuditSeverity[] = ['info', 'warning', 'critical'];

// Wave 5 RBAC reconciliation (Sam QA 2026-07-02): the audit page
// used to check `useCurrentUser().role` (a legacy custom
// publicMetadata field defaulting to 'Analyst') against a
// {admin, principal, senior_analyst} allowlist. That predates the
// Clerk-org membership RBAC we shipped for delete gates, and gave
// contradictory verdicts — Sam was tagged 'Analyst' here but had
// the sidebar 'Admin' pill from Clerk. Unified on Clerk's org
// membership: `useCurrentRole()` returns `org:admin` /
// `org:member` from `useOrganization().membership.role`. Now every
// admin gate in the app resolves against one source of truth.

const ADMIN_ROLES = new Set(['org:admin']);

export default function AuditExplorerPage() {
  const currentUser = useCurrentUser();
  const currentRole = useCurrentRole();
  const isAdmin = useMemo(
    () => ADMIN_ROLES.has(currentRole),
    [currentRole],
  );

  // ─── filter state ───
  const [q, setQ] = useState('');
  const [actor, setActor] = useState('');
  const [entityType, setEntityType] = useState<string>('');
  const [severity, setSeverity] = useState<string>('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [limit, setLimit] = useState(50);
  const [offset, setOffset] = useState(0);

  // ─── data state ───
  const [data, setData] = useState<ExplorerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const { toast } = useToast();

  const load = useCallback(
    async (signal?: AbortSignal) => {
      if (!isAdmin) return;
      if (!isWorkerConnected()) {
        setLoading(false);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const query: ExplorerQuery = {
          q: q || undefined,
          actor: actor || undefined,
          entity_type: entityType || undefined,
          severity: (severity as AuditSeverity) || undefined,
          since: since || undefined,
          until: until || undefined,
          limit,
          offset,
        };
        const res = await api.audit.explorer(query, signal);
        setData(res);
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        const msg =
          err instanceof WorkerError
            ? `${err.message}: ${err.body || ''}`
            : (err as Error).message;
        setError(msg);
      } finally {
        setLoading(false);
      }
    },
    [actor, entityType, isAdmin, limit, offset, q, severity, since, until],
  );

  // Refetch when filters/offset change. Aborted on filter churn so we
  // don't render stale rows after a quick re-type.
  useEffect(() => {
    const ctl = new AbortController();
    void load(ctl.signal);
    return () => ctl.abort();
  }, [load]);

  // ─────────────────────────── RBAC lock ──────────────────────────
  if (!isAdmin) {
    return (
      <div className="px-8 py-8 max-w-[1024px]">
        <PageHeader
          eyebrow="Compliance"
          title="Audit Explorer"
          subtitle="Tenant-wide audit log search."
        />
        <Card className="mt-6 p-10 text-center">
          <Lock className="mx-auto h-10 w-10 text-ink-300" />
          <h2 className="mt-4 text-[15px] font-semibold text-ink-900">
            Admin only
          </h2>
          <p className="mt-2 text-[12.5px] text-ink-500">
            The Compliance Explorer surfaces audit-log entries across every
            deal in this tenant. Access is gated to admin and principal
            roles. Ping an admin to grant access.
          </p>
          <p className="mt-3 text-[11.5px] text-ink-500">
            Current role:{' '}
            <code className="rounded-sm bg-ink-100 px-1 py-0.5">
              {currentRole || 'unknown'}
            </code>
          </p>
        </Card>
      </div>
    );
  }

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        eyebrow={
          data ? `${data.total} event${data.total === 1 ? '' : 's'} match` : 'Loading'
        }
        title="Audit Explorer"
        subtitle="Every state-changing event across every deal — searchable, filterable, exportable. Append-only."
        action={
          <Button
            variant="secondary"
            onClick={() => {
              setOffset(0);
              void load();
            }}
            disabled={loading}
          >
            <RefreshCw size={14} className={cn(loading && 'animate-spin')} />
            Refresh
          </Button>
        }
      />

      {/* Filter bar */}
      <Card className="mt-6 p-4">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-3">
          <label className="md:col-span-4 relative">
            <Search
              size={14}
              className="absolute left-2 top-1/2 -translate-y-1/2 text-ink-500"
            />
            <input
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setOffset(0);
              }}
              placeholder="Search action, actor, summary, resource…"
              className="w-full rounded-md border border-border bg-white py-2 pl-7 pr-3 text-[12.5px] text-ink-900 placeholder:text-ink-500 focus:border-brand-500 focus:outline-none"
            />
          </label>
          <input
            value={actor}
            onChange={(e) => {
              setActor(e.target.value);
              setOffset(0);
            }}
            placeholder="Actor id"
            className="md:col-span-2 rounded-md border border-border bg-white py-2 px-3 text-[12.5px] text-ink-900 focus:border-brand-500 focus:outline-none"
          />
          <select
            value={entityType}
            onChange={(e) => {
              setEntityType(e.target.value);
              setOffset(0);
            }}
            className="md:col-span-2 rounded-md border border-border bg-white py-2 px-3 text-[12.5px] text-ink-900 focus:border-brand-500 focus:outline-none"
          >
            <option value="">All entities</option>
            {ENTITY_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
          <select
            value={severity}
            onChange={(e) => {
              setSeverity(e.target.value);
              setOffset(0);
            }}
            className="md:col-span-2 rounded-md border border-border bg-white py-2 px-3 text-[12.5px] text-ink-900 focus:border-brand-500 focus:outline-none"
          >
            <option value="">All severities</option>
            {SEVERITIES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <input
            type="date"
            value={since}
            onChange={(e) => {
              setSince(e.target.value);
              setOffset(0);
            }}
            className="md:col-span-1 rounded-md border border-border bg-white py-2 px-2 text-[12px] text-ink-900 focus:border-brand-500 focus:outline-none"
          />
          <input
            type="date"
            value={until}
            onChange={(e) => {
              setUntil(e.target.value);
              setOffset(0);
            }}
            className="md:col-span-1 rounded-md border border-border bg-white py-2 px-2 text-[12px] text-ink-900 focus:border-brand-500 focus:outline-none"
          />
        </div>
      </Card>

      {/* Results table */}
      <div className="mt-6 flex gap-6">
        <div className="flex-1 min-w-0">
          {error ? (
            <div className="rounded-md border border-warn-200 bg-warn-50 p-4 text-[12.5px] text-warn-900">
              <ShieldAlert size={14} className="inline mr-1" />
              Couldn't load audit explorer: {error}
            </div>
          ) : !isWorkerConnected() ? (
            <Card className="p-8 text-center text-[12.5px] text-ink-500">
              Audit explorer requires the worker to be connected.
            </Card>
          ) : (
            <Card className="overflow-hidden">
              <table className="w-full text-[12.5px]">
                <thead className="bg-ink-50 text-[11px] uppercase tracking-wide text-ink-500">
                  <tr>
                    <Th>Time</Th>
                    <Th>Actor</Th>
                    <Th>Deal</Th>
                    <Th>Action</Th>
                    <Th>Entity</Th>
                    <Th>Summary</Th>
                    <Th>Severity</Th>
                  </tr>
                </thead>
                <tbody>
                  {(data?.entries ?? []).map((e) => (
                    <tr
                      key={e.id}
                      className={cn(
                        'border-t border-border cursor-pointer hover:bg-ink-50',
                        selected?.id === e.id && 'bg-brand-50',
                      )}
                      onClick={() => setSelected(e)}
                    >
                      <Td>
                        <span className="tabular-nums text-ink-500">
                          {new Date(e.created_at).toLocaleString()}
                        </span>
                      </Td>
                      <Td>{e.actor_email || e.actor_id || 'system'}</Td>
                      <Td>
                        <code className="font-mono text-[11px] text-ink-500">
                          {e.deal_id ? shortId(e.deal_id) : '—'}
                        </code>
                      </Td>
                      <Td>
                        <span className="font-medium text-ink-900">
                          {e.action}
                        </span>
                      </Td>
                      <Td>{e.resource_type}</Td>
                      <Td className="max-w-[260px] truncate">
                        {e.diff_summary || '—'}
                      </Td>
                      <Td>
                        <SeverityBadge severity={e.severity} />
                      </Td>
                    </tr>
                  ))}
                  {data && data.entries.length === 0 && !loading && (
                    <tr>
                      <td
                        colSpan={7}
                        className="px-4 py-10 text-center text-[12.5px] text-ink-500"
                      >
                        No events matched these filters.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
              {/* Pagination */}
              <div className="flex items-center justify-between border-t border-border bg-ink-50 px-4 py-2 text-[11.5px] text-ink-500">
                <div>
                  Showing{' '}
                  <span className="tabular-nums text-ink-900">
                    {data?.entries.length ?? 0}
                  </span>{' '}
                  of{' '}
                  <span className="tabular-nums text-ink-900">
                    {data?.total ?? 0}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      setOffset((o) => Math.max(0, o - limit))
                    }
                    disabled={offset === 0 || loading}
                    className="rounded-md border border-border px-2 py-1 hover:bg-white disabled:opacity-50"
                  >
                    Previous
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (data && offset + limit < data.total) {
                        setOffset((o) => o + limit);
                      } else {
                        toast('No more events to load', { type: 'info' });
                      }
                    }}
                    disabled={loading}
                    className="rounded-md border border-border px-2 py-1 hover:bg-white disabled:opacity-50"
                  >
                    Next
                  </button>
                </div>
              </div>
            </Card>
          )}
        </div>

        {selected && (
          <DetailPanel
            entry={selected}
            onClose={() => setSelected(null)}
          />
        )}
      </div>
    </div>
  );
}

// ─────────────────────────── pieces ───────────────────────────

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left font-medium">
      {children}
    </th>
  );
}

function Td({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <td className={cn('px-3 py-2 align-top', className)}>{children}</td>;
}

function SeverityBadge({ severity }: { severity: AuditSeverity }) {
  const colors: Record<AuditSeverity, string> = {
    info: 'bg-ink-100 text-ink-700',
    warning: 'bg-warn-50 text-warn-900',
    critical: 'bg-danger-50 text-danger-700',
  };
  return (
    <span
      className={cn(
        'inline-flex rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
        colors[severity],
      )}
    >
      {severity}
    </span>
  );
}

function DetailPanel({
  entry,
  onClose,
}: {
  entry: AuditEntry;
  onClose: () => void;
}) {
  return (
    <aside
      className="w-[420px] flex-shrink-0 rounded-md border border-border bg-white"
      role="dialog"
      aria-label="Audit event details"
    >
      <div className="flex items-start justify-between border-b border-border px-4 py-3">
        <div>
          <div className="text-[13px] font-semibold text-ink-900">
            {entry.action}
          </div>
          <div className="mt-0.5 text-[11.5px] text-ink-500">
            {entry.resource_type}
            {entry.resource_id ? ` · ${shortId(entry.resource_id)}` : ''}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 text-ink-500 hover:bg-ink-100"
          aria-label="Close audit event details"
        >
          <X size={14} />
        </button>
      </div>
      <div className="space-y-3 px-4 py-3 text-[12px]">
        <Field label="Actor">
          {entry.actor_email || entry.actor_id || 'system'}
          {entry.actor_ip && (
            <span className="ml-2 text-ink-500 tabular-nums">
              ({entry.actor_ip})
            </span>
          )}
        </Field>
        <Field label="When">
          <span className="tabular-nums">
            {new Date(entry.created_at).toLocaleString()}
          </span>
        </Field>
        <Field label="Severity">
          <SeverityBadge severity={entry.severity} />
        </Field>
        {entry.deal_id && (
          <Field label="Deal">
            <code className="font-mono text-[11px] text-ink-500">
              {entry.deal_id}
            </code>
          </Field>
        )}
        {entry.diff_summary && (
          <Field label="Summary">{entry.diff_summary}</Field>
        )}
        {(entry.before || entry.after) && (
          <div>
            <div className="mb-1 text-[11px] uppercase tracking-wide text-ink-500">
              Before → After
            </div>
            <div className="grid grid-cols-2 gap-2">
              <pre className="max-h-60 overflow-auto rounded-md bg-ink-50 p-2 text-[11px] text-ink-700">
                {JSON.stringify(entry.before ?? {}, null, 2)}
              </pre>
              <pre className="max-h-60 overflow-auto rounded-md bg-ink-50 p-2 text-[11px] text-ink-700">
                {JSON.stringify(entry.after ?? {}, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </div>
    </aside>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <div className="w-24 flex-shrink-0 text-[11px] uppercase tracking-wide text-ink-500">
        {label}
      </div>
      <div className="flex-1 text-[12px] text-ink-900">{children}</div>
    </div>
  );
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}
