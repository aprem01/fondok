'use client';

/**
 * useHistoricals — multi-year historical P&L for a deal, resilient loader.
 *
 * The `/deals/{id}/historicals` endpoint isn't implemented in the worker, so
 * that path always 404s and the real work is the MULTI-DOC FALLBACK: build one
 * historical column per EXTRACTED P&L / T-12 document, labelled by its fiscal
 * year (or filename). This is the exact logic HistoricalsSection has shipped
 * and QA'd (Sam 2026-05-14) — reused here (not reimplemented) so the grounded
 * worksheet renders the same grounded historical columns as the old table did.
 *
 * Returns the last `keep` populated years (plus any T-12) so callers get a
 * tidy multi-year grid without empty placeholder columns.
 */

import { useEffect, useState } from 'react';
import { api, isWorkerConnected, workerUrl, type WorkerDocument } from '@/lib/api';
import {
  actualsOnly,
  buildHistYear,
  deriveYearLabel,
  emptyFiveYearSkeleton,
  type HistData,
  type HistYear,
} from '@/components/project/pl/HistoricalsSection';

const isPnlDoc = (d: WorkerDocument) => {
  const dt = (d.doc_type ?? '').toUpperCase();
  return (dt.includes('T12') || dt === 'T-12' || dt === 'PNL' || dt === 'P&L' || dt.includes('PROFIT'));
};

export function useHistoricals(
  dealId: string,
  opts: { keys?: number | null } = {},
): { years: HistYear[]; keys: number; loading: boolean } {
  const [data, setData] = useState<HistData | null>(null);
  const [loading, setLoading] = useState(false);
  const keysHint = opts.keys ?? 0;

  useEffect(() => {
    const isMockId = /^\d+$/.test(dealId);
    if (!dealId || isMockId || !isWorkerConnected()) { setData(null); return; }

    let cancelled = false;
    async function load() {
      setLoading(true);

      // 1) endpoint (currently 404s; kept for when it lands).
      try {
        const res = await fetch(`${workerUrl()}/deals/${dealId}/historicals`);
        if (res.ok) {
          const json = (await res.json()) as Partial<HistData> | null;
          if (json && Array.isArray(json.years) && json.years.length > 0) {
            if (!cancelled) setData({ keys: json.keys ?? keysHint, years: json.years as HistYear[] });
            return;
          }
        }
      } catch {
        /* worker offline / route absent — fall through */
      }

      // 2) multi-doc fallback: one column per extracted P&L / T-12 doc.
      try {
        const docs = (await api.documents.list(String(dealId))) as WorkerDocument[];
        const pnlDocs = (docs ?? []).filter(isPnlDoc).filter((d) => d.status === 'EXTRACTED');
        if (pnlDocs.length > 0 && keysHint > 0) {
          const byYear = new Map<string, HistYear>();
          const sorted = [...pnlDocs].sort((a, b) => (a.uploaded_at ?? '').localeCompare(b.uploaded_at ?? ''));
          for (const doc of sorted) {
            try {
              const ext = await api.documents.extraction(String(dealId), doc.id);
              const fields = actualsOnly(ext.fields ?? []);
              const label = deriveYearLabel(fields, doc.filename ?? '', doc.doc_type);
              const built = buildHistYear(fields, keysHint, label);
              if (built) byYear.set(label, built);
            } catch {
              /* skip this doc — others may still populate */
            }
          }
          if (byYear.size > 0 && !cancelled) {
            const t12 = byYear.get('T-12') ?? null;
            const annualYears = [...byYear.keys()].filter((y) => /^\d{4}$/.test(y)).sort();
            const skelAnnual = emptyFiveYearSkeleton().years.slice(0, -1);
            const realByYear = new Map(annualYears.map((y) => [y, byYear.get(y)!]));
            const labels = new Set<string>(annualYears);
            if (annualYears.length < skelAnnual.length) for (const s of skelAnnual) labels.add(s.year);
            const orderedAnnual = [...labels].sort();
            const annualCols: HistYear[] = orderedAnnual.map((label) => {
              const real = realByYear.get(label);
              if (real) return real;
              return skelAnnual.find((y) => y.year === label) ?? blankYear(label);
            });
            setData({ keys: keysHint, years: [...annualCols, t12 ?? blankYear('T-12')] });
            return;
          }
        }
      } catch {
        /* ignore — empty below */
      }

      if (!cancelled) setData(null);
    }
    load().finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [dealId, keysHint]);

  return { years: data?.years ?? [], keys: data?.keys ?? keysHint, loading };
}

function blankYear(year: string): HistYear {
  return {
    year, days: 365, occupancyPct: 0, adr: 0, revpar: 0,
    rooms: 0, fb: 0, misc: 0,
    rooms_dept_expense: null, fb_dept_expense: null, other_dept_expense: null,
    undistributed: null, gop: null, fixed_expenses: null, noi: null,
    populated: false,
  };
}
