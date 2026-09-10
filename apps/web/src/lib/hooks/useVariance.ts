'use client';

/**
 * useVariance — fetch + map the worker's deterministic variance report.
 *
 * The worker exposes ``GET /analysis/{deal_id}/variance`` returning a flat
 * list of (field, rule_id, severity, actual, broker, delta) tuples; the
 * web app renders a richer ``VarianceFlag`` shape (with format hints,
 * narratives, source documents). This hook resolves the gap so the
 * Variance tab can render real broker-vs-T12 deltas while keeping its
 * existing presentation layer.
 *
 * On non-live deals (mock ids, no worker) the hook returns ``null`` so
 * callers can fall back to the canned Kimpton fixtures.
 */

import { useEffect, useRef, useState } from 'react';
import {
  api,
  isWorkerConnected,
  VarianceFlagResult,
  VarianceReportResult,
} from '@/lib/api';
import type {
  VarianceFlag,
  Severity as LocalSeverity,
} from '@/lib/varianceData';

const POLL_MS = 5000;

export interface VarianceState {
  flags: VarianceFlag[] | null;
  critical: number;
  warn: number;
  info: number;
  note: string | null;
  loading: boolean;
  error: string | null;
}

export function useVariance(dealId: string | null | undefined): VarianceState {
  const [state, setState] = useState<VarianceState>({
    flags: null,
    critical: 0,
    warn: 0,
    info: 0,
    note: null,
    loading: false,
    error: null,
  });
  const tick = useRef(0);

  const idStr = dealId == null ? '' : String(dealId);

  useEffect(() => {
    // Mock deals (numeric ids) and unconfigured worker fall back to fixture.
    if (!isWorkerConnected() || !idStr || /^\d+$/.test(idStr)) {
      setState({
        flags: null,
        critical: 0,
        warn: 0,
        info: 0,
        note: null,
        loading: false,
        error: null,
      });
      return;
    }
    const localTick = ++tick.current;
    const ctrl = new AbortController();
    setState((prev) => ({ ...prev, loading: true }));

    const fetchOnce = () => {
      api.analysis
        .variance(idStr, ctrl.signal)
        .then((r: VarianceReportResult) => {
          if (localTick !== tick.current) return;
          setState({
            flags: r.flags.map((f, i) => mapWorkerFlag(f, i, idStr)),
            critical: r.critical_count,
            warn: r.warn_count,
            info: r.info_count,
            note: r.note,
            loading: false,
            error: null,
          });
        })
        .catch((err: unknown) => {
          if (localTick !== tick.current) return;
          if ((err as { name?: string })?.name === 'AbortError') return;
          setState((prev) => ({
            ...prev,
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          }));
        });
    };

    fetchOnce();
    const t = setInterval(fetchOnce, POLL_MS);
    return () => {
      clearInterval(t);
      ctrl.abort();
    };
  }, [idStr]);

  return state;
}

// ───────────────────────── mapping ─────────────────────────

const FIELD_LABELS: Record<string, string> = {
  noi: 'NOI',
  noi_usd: 'NOI',
  rooms_revenue: 'Rooms Revenue',
  fb_revenue: 'F&B Revenue',
  total_revenue: 'Total Revenue',
  occupancy: 'Occupancy',
  occupancy_pct: 'Occupancy',
  adr: 'ADR',
  revpar: 'RevPAR',
  gop: 'GOP',
  mgmt_fee: 'Management Fee',
  ffe_reserve: 'FF&E Reserve',
  fixed_charges: 'Fixed Charges',
  insurance: 'Insurance',
  departmental_expenses: 'Departmental Expenses',
  undistributed_expenses: 'Undistributed Expenses',
};

const PERCENT_FIELDS = new Set(['occupancy', 'occupancy_pct']);
const PER_KEY_FIELDS = new Set<string>(); // none yet from worker

/** Concepts whose dollar delta IS an NOI delta (mirrors the worker catalog). */
const NOI_CONCEPTS = new Set(['noi', 'gop']);

/** Strip the extractor's namespace prefix + unit suffix → concept key. */
function conceptKey(field: string): string {
  const tail = field.includes('.') ? field.slice(field.lastIndexOf('.') + 1) : field;
  return tail.toLowerCase().replace(/_(usd|pct)$/, '');
}

function fieldLabel(field: string): string {
  const key = conceptKey(field);
  return FIELD_LABELS[key] ?? humanize(key);
}

function humanize(field: string): string {
  return field
    .split('_')
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

function detectFormat(field: string): VarianceFlag['format'] {
  const key = conceptKey(field);
  if (PERCENT_FIELDS.has(key)) return 'percent';
  if (PER_KEY_FIELDS.has(key)) return 'currency_per_key';
  return 'currency';
}

function normalizeSeverity(s: string): LocalSeverity {
  const upper = s.toUpperCase();
  if (upper === 'CRITICAL') return 'CRITICAL';
  if (upper === 'WARN') return 'WARN';
  return 'INFO';
}

/**
 * Map one worker flag onto the web `VarianceFlag` shape.
 *
 * FON-54a honesty rules:
 *  • the IC-facing label is the worker's business-readable `concept_label`
 *    when present; a raw extractor path is never humanised into a title
 *    when the label exists (older workers without labels still fall back
 *    to the local catalog / humaniser);
 *  • `noi_impact_usd` is |delta| ONLY when the impact basis is `'noi'`
 *    (NOI / GOP). A revenue- or expense-line delta is NOT an NOI impact
 *    and is never dressed up as one — it reads 0 and the UI says
 *    "NOI impact not estimated".
 */
export function mapWorkerFlag(
  f: VarianceFlagResult,
  index: number,
  dealId: string,
): VarianceFlag {
  const broker_value = f.broker ?? undefined;
  const t12_value = f.actual ?? undefined;
  const delta = f.delta ?? undefined;
  const delta_pct = f.delta_pct ?? undefined;
  const concept = (f.concept && f.concept.trim()) || conceptKey(f.field);
  const impact_basis: VarianceFlag['impact_basis'] =
    f.impact_basis ?? (NOI_CONCEPTS.has(concept) ? 'noi' : 'other');
  const noi_impact_usd = impact_basis === 'noi' ? Math.abs(delta ?? 0) : 0;
  const label = (f.concept_label && f.concept_label.trim()) || fieldLabel(f.field);
  return {
    flag_id: `${f.rule_id ?? 'flag'}-${index}`,
    rule_id: f.rule_id ?? 'BROKER_VS_T12_NOI_VARIANCE',
    severity: normalizeSeverity(f.severity),
    metric: f.field,
    field_label: label,
    broker_value,
    t12_value,
    variance_abs: delta,
    variance_pct: delta_pct,
    format: detectFormat(f.field),
    broker_overstates: (broker_value ?? 0) > (t12_value ?? 0),
    noi_impact_usd,
    explanation:
      f.note ??
      `Broker pro forma ${broker_value !== undefined ? broker_value : '—'} vs T-12 actual ${t12_value !== undefined ? t12_value : '—'} on ${label}. Delta ${delta !== undefined ? delta.toLocaleString() : '—'}.`,
    recommended_action:
      'Review the cited T-12 line and re-underwrite the broker assumption.',
    source_documents: f.source_page
      ? [
          {
            document_id: dealId,
            page: f.source_page,
            field: f.field,
          },
        ]
      : [],
    concept,
    impact_basis,
    basis_mismatch: f.basis_mismatch === true,
    unit_note: f.unit_note ?? null,
    source_doc_type: f.source_doc_type ?? null,
    raw_fields: (f.raw_fields ?? []).map((r) => ({
      field: r.field,
      rule_id: r.rule_id ?? null,
      severity: r.severity,
      broker: r.broker ?? null,
      actual: r.actual ?? null,
      delta: r.delta ?? null,
      delta_pct: r.delta_pct ?? null,
      source_page: r.source_page ?? null,
      source_doc_type: r.source_doc_type ?? null,
      source_document: r.source_document ?? null,
      unit_note: r.unit_note ?? null,
      excluded_reason: r.excluded_reason ?? null,
      basis_mismatch: r.basis_mismatch === true,
    })),
  };
}
