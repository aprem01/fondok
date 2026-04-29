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

function fieldLabel(field: string): string {
  const key = field.toLowerCase().replace(/_usd$/, '');
  return FIELD_LABELS[key] ?? humanize(field);
}

function humanize(field: string): string {
  return field
    .split('_')
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(' ');
}

function detectFormat(field: string): VarianceFlag['format'] {
  const key = field.toLowerCase().replace(/_usd$/, '');
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

function mapWorkerFlag(
  f: VarianceFlagResult,
  index: number,
  dealId: string,
): VarianceFlag {
  const broker_value = f.broker ?? undefined;
  const t12_value = f.actual ?? undefined;
  const delta = f.delta ?? undefined;
  const delta_pct = f.delta_pct ?? undefined;
  // Estimate NOI impact: when the field is NOI itself, the delta IS the
  // impact. Otherwise we don't have a precise translation, so we use the
  // absolute delta as a stand-in — accurate enough for heatmap sizing.
  const noi_impact_usd = Math.abs(delta ?? 0);
  return {
    flag_id: `${f.rule_id ?? 'flag'}-${index}`,
    rule_id: f.rule_id ?? 'BROKER_VS_T12_NOI_VARIANCE',
    severity: normalizeSeverity(f.severity),
    metric: f.field,
    field_label: fieldLabel(f.field),
    broker_value,
    t12_value,
    variance_abs: delta,
    variance_pct: delta_pct,
    format: detectFormat(f.field),
    broker_overstates: (broker_value ?? 0) > (t12_value ?? 0),
    noi_impact_usd,
    explanation:
      f.note ??
      `Broker pro forma ${broker_value !== undefined ? broker_value : '—'} vs T-12 actual ${t12_value !== undefined ? t12_value : '—'} on ${fieldLabel(f.field)}. Delta ${delta !== undefined ? delta.toLocaleString() : '—'}.`,
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
  };
}
