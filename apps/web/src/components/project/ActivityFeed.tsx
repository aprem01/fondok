'use client';

/**
 * Activity Feed — per-deal timeline of every audit_log event.
 *
 * Wave 4 W4.3. Mounted as the "Activity" tab on the project workspace.
 * Renders a vertical timeline with one row per event:
 *   actor initials/avatar  ·  one-line summary  ·  relative time  ·  severity badge
 *
 * Clicking a row opens a right-side panel (NOT a modal so the timeline
 * stays visible behind it) with the full before/after diff. Filter chips at
 * the top scope by action / entity type / severity.
 *
 * Everything a reader sees is a label, never an identifier: the worker's
 * ``engine_run.kicked_off`` reads "Model run started", an override key path
 * reads as the assumption's name, ``None`` reads "not set", and record ids
 * live only in hover titles (lib/auditLabels).
 */

import { useCallback, useEffect, useState } from 'react';
import {
  Activity, AlertTriangle, ChevronRight, Filter, RefreshCw, X,
} from 'lucide-react';
import { api, isWorkerConnected, userMessage, technicalDetail } from '@/lib/api';
import type { AuditEntry, AuditSeverity, DealAuditResponse } from '@/lib/api';
import { cn } from '@/lib/format';
import { Button } from '@/components/ui/Button';
import {
  actionLabel, entityLabel, humanizeDiffSummary,
} from '@/lib/auditLabels';
import RunComparePanel from './RunComparePanel';

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
  const [error, setError] = useState<{ message: string; detail: string | null } | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const [refreshSeq, setRefreshSeq] = useState(0);

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
        setError({ message: userMessage(err), detail: technicalDetail(err) });
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
  }, [fetchFeed, refreshSeq]);

  const entries = data?.entries ?? [];

  // ─────────────────────── render ───────────────────────

  if (!isWorkerConnected()) {
    return (
      <div className="rounded-md border border-border bg-white p-8 text-center">
        <Activity className="mx-auto h-8 w-8 text-ink-300" />
        <p className="mt-3 text-[13px] text-ink-500">
          Activity is recorded once this deal has been modelled.
        </p>
      </div>
    );
  }

  return (
    <>
    {/* Regression baselines: baseline run vs. head run. */}
    <RunComparePanel dealId={dealId} />
    <div className="flex gap-6">
      <div className="flex-1 min-w-0">
        {/* Filter chips */}
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <span className="flex items-center gap-1 text-[11.5px] uppercase tracking-wide text-ink-500">
            <Filter size={11} /> Filter
          </span>
          <FilterSelect
            label="Event"
            value={filters.action}
            options={uniqueValues(entries, 'action').map((v) => ({ value: v, label: actionLabel(v) }))}
            onChange={(v) => setFilters((f) => ({ ...f, action: v }))}
          />
          <FilterSelect
            label="Record"
            value={filters.entity_type}
            options={ENTITY_TYPES.map((v) => ({ value: v, label: entityLabel(v) }))}
            onChange={(v) => setFilters((f) => ({ ...f, entity_type: v }))}
          />
          <FilterSelect
            label="Severity"
            value={filters.severity}
            options={SEVERITIES.map((v) => ({ value: v, label: v.charAt(0).toUpperCase() + v.slice(1) }))}
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
          <div
            className="rounded-md border border-warn-200 bg-warn-50 p-4 text-[12.5px] text-warn-900"
            role="alert"
            data-testid="activity-feed-error"
          >
            <div className="flex items-center justify-between gap-3">
              <span>Couldn’t load the activity feed — {error.message}</span>
              <Button size="sm" variant="secondary" onClick={() => setRefreshSeq((n) => n + 1)}>
                <RefreshCw size={12} aria-hidden="true" /> Try again
              </Button>
            </div>
            {error.detail && (
              <details className="mt-2">
                <summary className="text-[11px] text-warn-900/70 cursor-pointer select-none">Technical detail</summary>
                <pre className="mt-1 text-[10.5px] text-warn-900/70 whitespace-pre-wrap break-words">{error.detail}</pre>
              </details>
            )}
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
    </>
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
  const summary = humanizeDiffSummary(entry.diff_summary);
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
            <span
              className="font-medium text-[13px] text-ink-900 truncate"
              title={entry.action}
              data-testid="activity-action"
            >
              {actionLabel(entry.action)}
            </span>
            <SeverityBadge severity={entry.severity} />
            <span className="ml-auto text-[11.5px] text-ink-500 tabular-nums">
              {formatRelative(entry.created_at)}
            </span>
          </div>
          <p
            className="mt-1 text-[12px] text-ink-500 truncate"
            title={entry.resource_id ?? undefined}
            data-testid="activity-summary"
          >
            {summary || entityLabel(entry.resource_type)}
          </p>
          {(entry.tags ?? []).length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {(entry.tags ?? []).map((t) => (
                <span
                  key={t}
                  className="rounded-sm bg-ink-100 px-1.5 py-0.5 text-[10px] text-ink-700"
                >
                  {t.replace(/[_-]+/g, ' ')}
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
  const summary = humanizeDiffSummary(entry.diff_summary);
  return (
    <aside
      className="w-[420px] flex-shrink-0 rounded-md border border-border bg-white"
      role="dialog"
      aria-label="Activity details"
    >
      <div className="flex items-start justify-between border-b border-border px-4 py-3">
        <div>
          <div className="text-[13px] font-semibold text-ink-900" title={entry.action}>
            {actionLabel(entry.action)}
          </div>
          <div className="mt-0.5 text-[11.5px] text-ink-500" title={entry.resource_id ?? undefined}>
            {entityLabel(entry.resource_type)}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="rounded-md p-1 text-ink-500 hover:bg-ink-100"
          aria-label="Close activity details"
        >
          <X size={14} />
        </button>
      </div>
      <dl className="space-y-3 px-4 py-3 text-[12px]">
        <Field label="Who">
          {entry.actor_email || (entry.actor_id ? 'Signed-in user' : 'Fondok')}
        </Field>
        <Field label="When">
          <span className="tabular-nums">
            {new Date(entry.created_at).toLocaleString()}
          </span>
        </Field>
        <Field label="Severity">
          <SeverityBadge severity={entry.severity} />
        </Field>
        {summary && (
          <Field label="Change">{summary}</Field>
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
        <details>
          <summary className="cursor-pointer text-[11.5px] text-brand-700">
            Technical detail
          </summary>
          <dl className="mt-2 space-y-1.5 text-[11px] text-ink-700">
            <div><span className="text-ink-500">Event: </span><code className="font-mono">{entry.action}</code></div>
            <div><span className="text-ink-500">Record: </span><code className="font-mono">{entry.resource_type}{entry.resource_id ? ` · ${entry.resource_id}` : ''}</code></div>
            {entry.actor_ip && <div><span className="text-ink-500">From: </span><span className="tabular-nums">{entry.actor_ip}</span></div>}
            {entry.output_hash && (
              <div><span className="text-ink-500">Output hash: </span><code className="font-mono">{entry.output_hash}</code></div>
            )}
          </dl>
          {entry.payload && (
            <pre className="mt-2 max-h-60 overflow-auto rounded-md bg-ink-50 p-2 text-[11px] text-ink-700">
              {JSON.stringify(entry.payload, null, 2)}
            </pre>
          )}
        </details>
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
  options: { value: string; label: string }[];
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
          <option key={o.value} value={o.value}>
            {o.label}
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
  const source = email || (actor ? 'user' : 'Fondok');
  const initials = source
    .split(/[@.\s-]+/)
    .filter(Boolean)
    .map((s) => s[0]?.toUpperCase() ?? '')
    .slice(0, 2)
    .join('') || 'FO';
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
        Nothing&apos;s happened on this deal yet.
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
