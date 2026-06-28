'use client';

/**
 * Activity Feed — per-deal timeline of every audit_log event.
 *
 * Wave 4 W4.3. Mounted as the "Activity" tab on the project workspace.
 * Renders a vertical timeline with one row per event:
 *   actor initials/avatar  ·  one-line summary  ·  relative time  ·  severity badge
 *
 * Clicking a row opens a right-side panel (NOT a modal so the timeline
 * stays visible behind it) with the full before/after JSON diff. Filter
 * chips at the top scope by action / entity_type / severity. The empty
 * state matches Eshan's June 2026 ask for "every change should be one
 * click away."
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertTriangle, ChevronRight, Filter, Search, X,
} from 'lucide-react';
import { api, isWorkerConnected, WorkerError } from '@/lib/api';
import type { AuditEntry, AuditSeverity, DealAuditResponse } from '@/lib/api';
import { cn } from '@/lib/format';

interface Props {
  dealId: string;
}

type Filters = {
  action?: string;
  entity_type?: string;
  severity?: AuditSeverity;
};

const ENTITY_TYPES = [
  'deal', 'scenario', 'override', 'document', 'engine_run',
  'export', 'comp_transaction', 'portfolio_library_entry', 'memo',
];

const SEVERITIES: AuditSeverity[] = ['info', 'warning', 'critical'];

export default function ActivityFeed({ dealId }: Props) {
  const [data, setData] = useState<DealAuditResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [selected, setSelected] = useState<AuditEntry | null>(null);

  const fetchFeed = useCallback(
    async (signal: AbortSignal) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.audit.deal(
          dealId,
          { ...filters, limit: 200 },
          signal,
        );
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
    [dealId, filters],
  );

  useEffect(() => {
    const ctl = new AbortController();
    if (isWorkerConnected()) {
      void fetchFeed(ctl.signal);
    } else {
      setLoading(false);
      setData(null);
    }
    return () => ctl.abort();
  }, [fetchFeed]);

  const entries = data?.entries ?? [];

  // ─────────────────────── render ───────────────────────

  if (!isWorkerConnected()) {
    return (
      <div className="rounded-md border border-border bg-white p-8 text-center">
        <Activity className="mx-auto h-8 w-8 text-ink-300" />
        <p className="mt-3 text-[13px] text-ink-500">
          Activity feed available once the worker is connected.
        </p>
      </div>
    );
  }

  return (
    <div className="flex gap-6">
      <div className="flex-1 min-w-0">
        {/* Filter chips */}
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1 text-[11.5px] uppercase tracking-wide text-ink-500">
            <Filter size={11} /> Filter
          </span>
          <FilterSelect
            label="Action"
            value={filters.action}
            options={uniqueValues(entries, 'action')}
            onChange={(v) => setFilters((f) => ({ ...f, action: v }))}
          />
          <FilterSelect
            label="Entity"
            value={filters.entity_type}
            options={ENTITY_TYPES}
            onChange={(v) => setFilters((f) => ({ ...f, entity_type: v }))}
          />
          <FilterSelect
            label="Severity"
            value={filters.severity}
            options={SEVERITIES}
            onChange={(v) =>
              setFilters((f) => ({
                ...f,
                severity: v as AuditSeverity | undefined,
              }))
            }
          />
          {(filters.action || filters.entity_type || filters.severity) && (
            <button
              type="button"
              onClick={() => setFilters({})}
              className="text-[11.5px] text-brand-700 hover:text-brand-900"
            >
              Clear
            </button>
          )}
          <div className="ml-auto text-[11.5px] text-ink-500 tabular-nums">
            {loading ? 'Loading…' : `${entries.length} of ${data?.total ?? 0}`}
          </div>
        </div>

        {/* Body */}
        {error ? (
          <div className="rounded-md border border-warn-200 bg-warn-50 p-4 text-[12.5px] text-warn-900">
            Couldn't load activity feed: {error}
          </div>
        ) : entries.length === 0 && !loading ? (
          <EmptyState />
        ) : (
          <ol className="relative space-y-3" data-testid="activity-feed-list">
            {entries.map((e) => (
              <Row
                key={e.id}
                entry={e}
                isSelected={selected?.id === e.id}
                onClick={() => setSelected(e)}
              />
            ))}
          </ol>
        )}
      </div>

      {/* Side panel (not a modal — the timeline stays visible) */}
      {selected && (
        <DetailPanel
          entry={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}

// ───────────────────────── pieces ─────────────────────────

function Row({
  entry,
  isSelected,
  onClick,
}: {
  entry: AuditEntry;
  isSelected: boolean;
  onClick: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onClick}
        className={cn(
          'group w-full flex items-start gap-3 rounded-md border bg-white px-4 py-3 text-left transition-colors',
          isSelected
            ? 'border-brand-500 shadow-card-hover'
            : 'border-border hover:border-ink-300/60',
        )}
      >
        <ActorAvatar actor={entry.actor_id} email={entry.actor_email} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-[13px] text-ink-900 truncate">
              {entry.action}
            </span>
            <SeverityBadge severity={entry.severity} />
            <span className="ml-auto text-[11.5px] text-ink-500 tabular-nums">
              {formatRelative(entry.created_at)}
            </span>
          </div>
          <p className="mt-1 text-[12px] text-ink-500 truncate">
            {entry.diff_summary
              || `${entry.resource_type}${entry.resource_id ? ` · ${shortId(entry.resource_id)}` : ''}`}
          </p>
          {(entry.tags ?? []).length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {(entry.tags ?? []).map((t) => (
                <span
                  key={t}
                  className="rounded-sm bg-ink-100 px-1.5 py-0.5 text-[10px] text-ink-700"
                >
                  {t}
                </span>
              ))}
            </div>
          )}
        </div>
        <ChevronRight
          size={14}
          className={cn(
            'mt-1 text-ink-300 group-hover:text-ink-700',
            isSelected && 'text-brand-500',
          )}
        />
      </button>
    </li>
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
      <dl className="space-y-3 px-4 py-3 text-[12px]">
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
        {entry.payload && (
          <details>
            <summary className="cursor-pointer text-[11.5px] text-brand-700">
              Raw payload
            </summary>
            <pre className="mt-2 max-h-60 overflow-auto rounded-md bg-ink-50 p-2 text-[11px] text-ink-700">
              {JSON.stringify(entry.payload, null, 2)}
            </pre>
          </details>
        )}
        {entry.output_hash && (
          <Field label="Output hash">
            <code className="font-mono text-[10.5px] text-ink-500">
              {entry.output_hash.slice(0, 16)}…
            </code>
          </Field>
        )}
      </dl>
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
      <dt className="w-24 flex-shrink-0 text-[11px] uppercase tracking-wide text-ink-500">
        {label}
      </dt>
      <dd className="flex-1 text-[12px] text-ink-900">{children}</dd>
    </div>
  );
}

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | undefined;
  options: string[];
  onChange: (v: string | undefined) => void;
}) {
  return (
    <label className="inline-flex items-center gap-1 rounded-md border border-border bg-white px-2 py-1 text-[11.5px] text-ink-700 focus-within:border-brand-500">
      <span className="text-ink-500">{label}:</span>
      <select
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value || undefined)}
        className="bg-transparent text-[11.5px] text-ink-900 focus:outline-none"
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </select>
    </label>
  );
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
        'inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide',
        colors[severity],
      )}
    >
      {severity === 'critical' && <AlertTriangle size={9} />}
      {severity}
    </span>
  );
}

function ActorAvatar({
  actor,
  email,
}: {
  actor: string | null;
  email: string | null;
}) {
  const source = email || actor || 'system';
  const initials = source
    .split(/[@.\s-]+/)
    .filter(Boolean)
    .map((s) => s[0]?.toUpperCase() ?? '')
    .slice(0, 2)
    .join('') || 'SY';
  return (
    <div
      className="mt-0.5 flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-ink-300/40 text-[10.5px] font-semibold text-ink-700"
      aria-hidden="true"
    >
      {initials}
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-md border border-dashed border-border bg-white p-8 text-center">
      <Activity className="mx-auto h-8 w-8 text-ink-300" />
      <p className="mt-3 text-[13px] text-ink-700">
        Nothing's happened on this deal yet.
      </p>
      <p className="mt-1 text-[12px] text-ink-500">
        Once you start editing assumptions or running scenarios, every change
        shows up here.
      </p>
    </div>
  );
}

// ─────────────────────── helpers ───────────────────────

function uniqueValues(entries: AuditEntry[], key: keyof AuditEntry): string[] {
  const seen = new Set<string>();
  for (const e of entries) {
    const v = e[key];
    if (typeof v === 'string' && v) seen.add(v);
  }
  return [...seen].sort();
}

function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 8)}…` : id;
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const now = Date.now();
  const diffMs = now - d.getTime();
  const sec = Math.round(diffMs / 1000);
  if (sec < 5) return 'just now';
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day}d ago`;
  return d.toLocaleDateString();
}

// Keep imports referenced when minimizers strip unused symbols.
export { Search };
