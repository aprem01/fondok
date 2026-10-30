'use client';
/**
 * RenovationBudgetSources — FON-101. "Renovation budget sources": every figure
 * the model's renovation budget could ground on, which one it is on, and why.
 *
 * Sam's decision (2026-10-14): a separate PIP / CapEx document outranks the
 * OM when both state a renovation budget (it is the more detailed source);
 * both values stay visible with their sources; the analyst can override the
 * selection; a newer approved capital plan supersedes an older PIP; Future
 * CapEx documents are reference only and never read into the model.
 *
 * The list is the worker's (`GET /deals/{id}/renovation-budget-candidates`),
 * which REPORTS the engine-input loader's resolution — nothing is re-derived
 * in the browser, so the disclosure and the model can never disagree. "Use
 * this" writes `renovation_budget` as an analyst override WITH a note
 * (prefilled "Selected <doc> dated <date>", editable) through the tab's
 * canonical save path, which re-runs the model. An override equal to the
 * selected document's figure keeps that document's badge (FON-65).
 *
 * Nothing here invents a number: a document without an extracted PIP total
 * renders "—" with the worker's reason.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronRight, FileText } from 'lucide-react';
import { api, isWorkerConnected, type RenovationBudgetCandidate, type RenovationBudgetCandidatesResponse, type RenovationBudgetReferenceDoc } from '@/lib/api';
import { fmtCurrency } from '@/lib/format';
import { sourceLabel } from '@/lib/provenance';
import { palette } from '@/components/design';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';

export const SOURCES_TITLE = 'Renovation budget sources';
export const REFERENCE_ONLY_TITLE = 'Reference only — not in the model';
export const USE_THIS_LABEL = 'Use this';
export const IN_THE_MODEL_LABEL = 'In the model';

/** Format an ISO date as "Mon D, YYYY" without a timezone shift. */
export function fmtPlanDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  return `${months[Number(m[2]) - 1]} ${Number(m[3])}, ${m[1]}`;
}

const DATE_BASIS_LABEL: Record<string, string> = {
  plan_date: 'plan date',
  report_as_of: 'report date',
  uploaded_at: 'upload date',
};

/**
 * The prefilled justification for "Use this": names the document and its
 * date so the override's own note says why the number is what it is. The
 * analyst can edit it before confirming; it is never written silently.
 */
export function prefillNote(c: RenovationBudgetCandidate): string {
  const name = c.filename || c.label || (c.kind === 'om_broker' ? 'the OM broker figure' : 'the PIP / CapEx document');
  const date = c.date ? ` dated ${c.date}` : '';
  const kind = c.kind === 'om_broker' ? ' (OM broker pro forma)' : c.status_label ? ` (${c.status_label})` : '';
  return `Selected ${name}${date}${kind}`;
}

export interface RenovationBudgetSourcesProps {
  dealId: string;
  /** False on demo deals / no worker — the disclosure renders nothing. */
  live: boolean;
  /** Changes whenever the deal's overrides or the last run change, to refetch. */
  refreshKey?: string | number;
  /** Writes `renovation_budget` as an analyst override with `note` and re-runs. */
  onUse: (valueUsd: number, note: string) => void | Promise<void>;
  /** Start expanded (tests / deep links). */
  defaultOpen?: boolean;
}

export default function RenovationBudgetSources({ dealId, live, refreshKey, onUse, defaultOpen = false }: RenovationBudgetSourcesProps) {
  const [data, setData] = useState<RenovationBudgetCandidatesResponse | null>(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(defaultOpen);

  useEffect(() => {
    if (!live || !isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) { setData(null); return; }
    const ac = new AbortController();
    setFailed(false);
    Promise.resolve()
      .then(() => api.deals.renovationBudgetCandidates(dealId, ac.signal))
      .then((res) => { if (!ac.signal.aborted) setData(res); })
      .catch(() => { if (!ac.signal.aborted) setFailed(true); });
    return () => ac.abort();
  }, [dealId, live, refreshKey]);

  if (!live) return null;

  const candidates = data?.candidates ?? [];
  const reference = data?.reference_only ?? [];
  const selected = candidates.find((c) => c.selected) ?? null;
  const summary: string = !data
    ? (failed ? 'Sources unavailable' : 'Loading…')
    : selected
      ? `${selected.kind === 'analyst_override' ? 'Your override' : (selected.filename || selected.label)} · ${selected.value_usd == null ? '—' : fmtCurrency(selected.value_usd)}`
      : data.selected_source === 'seed'
        ? 'Seed — no document states a budget'
        : `${candidates.length} source${candidates.length === 1 ? '' : 's'}`;

  return (
    <div data-testid="renovation-budget-sources" style={{ marginTop: 10, border: `1px solid ${palette.border}`, borderRadius: 7, background: palette.surfaceTint }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="renovation-budget-sources-body"
        data-testid="renovation-budget-sources-toggle"
        style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%', gap: 8,
          padding: '8px 11px', background: 'transparent', border: 0, cursor: 'pointer', textAlign: 'left',
          fontSize: 11.5, color: palette.hoverInk,
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 600 }}>
          {open ? <ChevronDown size={12} aria-hidden="true" /> : <ChevronRight size={12} aria-hidden="true" />}
          {SOURCES_TITLE}
        </span>
        <span style={{ color: palette.textMuted, fontVariantNumeric: 'tabular-nums' }}>{summary}</span>
      </button>
      {open && (
        <div id="renovation-budget-sources-body" style={{ padding: '0 11px 10px', display: 'flex', flexDirection: 'column', gap: 8 }}>
          {failed && !data && (
            <div style={{ fontSize: 11.5, color: palette.textMuted }}>The worker did not return the candidate list — the model&apos;s budget is unchanged.</div>
          )}
          {data && candidates.length === 0 && (
            <div style={{ fontSize: 11.5, color: palette.textMuted }} data-testid="renovation-budget-sources-empty">
              No OM or PIP / CapEx document on this deal states a renovation budget — the model is on the seed. Upload a PIP or an OM, or set the budget above.
            </div>
          )}
          {candidates.map((c) => (
            <CandidateRow key={c.id} c={c} keys={data?.keys ?? null} onUse={onUse} />
          ))}
          {reference.length > 0 && (
            <div data-testid="renovation-budget-reference-only" style={{ borderTop: `1px dashed ${palette.border}`, paddingTop: 8 }}>
              <div style={{ fontSize: 11, fontWeight: 600, color: palette.textMuted, textTransform: 'uppercase', letterSpacing: '.03em' }}>
                {REFERENCE_ONLY_TITLE}
              </div>
              {reference.map((r) => <ReferenceRow key={r.id} r={r} />)}
            </div>
          )}
          {data && (
            <div style={{ fontSize: 10.5, color: palette.textMuted, lineHeight: 1.5 }}>
              Precedence: {data.precedence}.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Meta({ children }: { children: ReactNode }) {
  return <span style={{ fontSize: 10.5, color: palette.textMuted }}>{children}</span>;
}

function CandidateRow({ c, keys, onUse }: { c: RenovationBudgetCandidate; keys: number | null; onUse: RenovationBudgetSourcesProps['onUse'] }) {
  const [choosing, setChoosing] = useState(false);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  const value = typeof c.value_usd === 'number' && Number.isFinite(c.value_usd) ? c.value_usd : null;
  const perKey = typeof c.per_key_usd === 'number' && Number.isFinite(c.per_key_usd)
    ? c.per_key_usd
    : (value != null && keys && keys > 0 ? value / keys : null);
  const date = fmtPlanDate(c.date);
  const basis = c.date_basis ? DATE_BASIS_LABEL[c.date_basis] ?? c.date_basis : null;
  const canUse = !c.selected && value != null && c.kind !== 'analyst_override';

  const start = useCallback(() => { setNote(prefillNote(c)); setChoosing(true); }, [c]);
  const confirm = useCallback(async () => {
    if (value == null) return;
    const trimmed = note.trim();
    if (!trimmed) return;
    setSaving(true);
    try { await onUse(value, trimmed); setChoosing(false); } finally { setSaving(false); }
  }, [note, onUse, value]);

  return (
    <div data-testid={`renovation-candidate-${c.kind}`} data-selected={c.selected ? 'true' : 'false'}
      style={{ display: 'flex', flexDirection: 'column', gap: 3, padding: '6px 0', borderBottom: `1px solid ${palette.hairlineRow}` }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10, fontSize: 12 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <FileText size={11} aria-hidden="true" style={{ color: palette.textMuted, flexShrink: 0 }} />
          <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {c.kind === 'analyst_override' ? 'Your override' : (c.filename || c.label)}
          </span>
          <AssumptionBadge source={c.kind} documentId={c.document_id ?? undefined} />
          {c.selected && (
            <span data-testid="renovation-candidate-selected" style={{
              fontSize: 9.5, fontWeight: 600, padding: '1px 5px', borderRadius: 4,
              background: palette.surfaceTint, border: `1px solid ${palette.border}`, color: palette.hoverInk, whiteSpace: 'nowrap',
            }}>
              {IN_THE_MODEL_LABEL}
            </span>
          )}
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
          <span style={{ fontVariantNumeric: 'tabular-nums', fontWeight: c.selected ? 700 : 500, color: palette.ink }}>
            {value != null ? fmtCurrency(value) : '—'}
          </span>
          {canUse && !choosing && (
            <button type="button" onClick={start} aria-label={`${USE_THIS_LABEL}: ${c.filename || c.label}`}
              style={{ fontSize: 10.5, fontWeight: 600, color: palette.linkBlue, background: 'transparent', border: 0, cursor: 'pointer', padding: 0 }}>
              {USE_THIS_LABEL}
            </button>
          )}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, paddingLeft: 17 }}>
        {perKey != null && <Meta>{fmtCurrency(perKey)} / key</Meta>}
        {date && <Meta>{date}{basis ? ` (${basis})` : ''}</Meta>}
        {c.status_label && <Meta>{c.status_label}</Meta>}
        {c.prepared_by && <Meta>Prepared by {c.prepared_by}</Meta>}
        {c.page != null && <Meta>p.{c.page}</Meta>}
        {c.kind === 'analyst_override' && c.note && <Meta>“{c.note}”</Meta>}
      </div>
      <div style={{ fontSize: 10.5, color: palette.textMuted, paddingLeft: 17, lineHeight: 1.45 }} data-testid="renovation-candidate-reason">
        {c.reason}
      </div>
      {choosing && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, paddingLeft: 17, marginTop: 2 }}>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            aria-label="Override justification"
            data-testid="renovation-candidate-note"
            disabled={saving}
            style={{ flex: 1, fontSize: 11, padding: '3px 6px', border: `1px solid ${palette.border}`, borderRadius: 4 }}
          />
          <button type="button" onClick={() => void confirm()} disabled={saving || !note.trim()} aria-label="Confirm"
            style={{ fontSize: 10.5, fontWeight: 600, color: '#fff', background: palette.ink, border: 0, borderRadius: 4, padding: '3px 8px', cursor: 'pointer' }}>
            Confirm
          </button>
          <button type="button" onClick={() => setChoosing(false)} disabled={saving} aria-label="Cancel"
            style={{ fontSize: 10.5, color: palette.textMuted, background: 'transparent', border: 0, cursor: 'pointer' }}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

/** The lane a reference-only document is filed under, in the Data Room's
 *  words. The worker sends `lane_label`; older workers sent only Future CapEx
 *  rows, so a row with neither field keeps that label. */
export function referenceLaneLabel(r: Pick<RenovationBudgetReferenceDoc, 'lane' | 'lane_label'>): string {
  if (r.lane_label && r.lane_label.trim()) return r.lane_label.trim();
  return r.lane === 'historic' ? 'Historic CapEx' : 'Future CapEx';
}

function ReferenceRow({ r }: { r: RenovationBudgetReferenceDoc }) {
  const date = fmtPlanDate(r.date);
  const laneLabel = referenceLaneLabel(r);
  // Historic CapEx only — the ledger's own annual totals
  // (`historic_capex.by_year.<YYYY>.total_usd`), as printed.
  const byYear = Object.entries(r.by_year ?? {})
    .filter(([, v]) => typeof v === 'number' && Number.isFinite(v))
    .sort(([a], [b]) => a.localeCompare(b));
  return (
    <div data-testid="renovation-reference-doc" data-lane={r.lane ?? ''} style={{ padding: '6px 0', fontSize: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          <FileText size={11} aria-hidden="true" style={{ color: palette.textMuted, flexShrink: 0 }} />
          <span style={{ color: palette.textSecondary, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.filename}</span>
          <Meta>{laneLabel}</Meta>
        </span>
        <span style={{ fontVariantNumeric: 'tabular-nums', color: palette.textMuted }}>
          {typeof r.total_usd === 'number' ? fmtCurrency(r.total_usd) : '—'}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, paddingLeft: 17 }}>
        {date && <Meta>{date}</Meta>}
        {r.prepared_by && <Meta>Prepared by {r.prepared_by}</Meta>}
        {!r.extracted && <Meta>Not extracted yet</Meta>}
      </div>
      {byYear.length > 0 && (
        <div data-testid="renovation-reference-by-year" style={{ display: 'flex', flexWrap: 'wrap', gap: 10, paddingLeft: 17, marginTop: 2 }}>
          <Meta>By year:</Meta>
          {byYear.map(([year, usd]) => (
            <Meta key={year}>{year} {fmtCurrency(usd as number)}</Meta>
          ))}
        </div>
      )}
      {r.items.length > 0 && (
        <ul style={{ listStyle: 'none', margin: '3px 0 0', padding: '0 0 0 17px', display: 'flex', flexDirection: 'column', gap: 2 }}>
          {r.items.map((it, i) => (
            <li key={it.index ?? i} style={{ display: 'flex', justifyContent: 'space-between', gap: 10, fontSize: 11, color: palette.textSecondary }}>
              <span>{it.year ?? '—'} · {it.name ?? '—'}{it.category ? ` (${it.category.replace(/_/g, ' ')})` : ''}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{typeof it.usd === 'number' ? fmtCurrency(it.usd) : '—'}</span>
            </li>
          ))}
        </ul>
      )}
      <div style={{ fontSize: 10.5, color: palette.textMuted, paddingLeft: 17, lineHeight: 1.45 }}>{r.reason}</div>
    </div>
  );
}

/** The source label shown next to the Renovation tile / row for a resolved
 *  `renovation_budget` source, or null when none is recorded. */
export function renovationSourceLabel(source: string | null | undefined): string | null {
  if (!source) return null;
  return sourceLabel(source);
}
