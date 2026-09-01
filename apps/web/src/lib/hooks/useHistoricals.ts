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

      // 2) multi-doc fallback: one column per extracted P&L / T-12 doc, PLUS
      //    any multi-year P&L embedded in an OM (p_and_l_usali.YYYY.*).
      try {
        const docs = (await api.documents.list(String(dealId))) as WorkerDocument[];
        const extracted = (docs ?? []).filter((d) => d.status === 'EXTRACTED');
        const pnlDocs = extracted.filter(isPnlDoc);
        const omDocs = extracted.filter((d) => (d.doc_type ?? '').toUpperCase() === 'OM');
        if ((pnlDocs.length > 0 || omDocs.length > 0) && keysHint > 0) {
          const byYear = new Map<string, HistYear>();
          const sorted = [...pnlDocs].sort((a, b) => (a.uploaded_at ?? '').localeCompare(b.uploaded_at ?? ''));
          for (const doc of sorted) {
            try {
              const ext = await api.documents.extraction(String(dealId), doc.id);
              const fields = actualsOnly(ext.fields ?? []);
              const label = deriveYearLabel(
                fields, doc.filename ?? '', doc.doc_type,
                doc.fiscal_year ?? doc.extracted_period_year,
              );
              const built = buildHistYear(fields, keysHint, label, doc.id);
              if (built) byYear.set(label, built);
            } catch {
              /* skip this doc — others may still populate */
            }
          }
          // OM-embedded prior years fill gaps a standalone statement doesn't
          // cover (e.g. an OM's own 2021-2023 P&L). Actual statements always
          // win on a shared year — the OM only backfills missing history.
          for (const doc of omDocs) {
            try {
              const ext = await api.documents.extraction(String(dealId), doc.id);
              const embedded = extractEmbeddedYears(ext.fields ?? []);
              for (const [yr, vals] of Object.entries(embedded)) {
                if (!byYear.has(yr) && Object.keys(vals).length >= 6) {
                  byYear.set(yr, omYearToHistYear(vals, yr));
                }
              }
            } catch {
              /* skip — OM may lack an embedded P&L */
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

// Pull multi-year P&L blocks embedded in a document's extraction, keyed
// `p_and_l_usali.<YYYY>.<line>` — an OM typically reproduces the property's own
// 2-3 year operating history this way. Returns { year: { line: value } }.
function extractEmbeddedYears(
  fields: { field_name?: string; value?: unknown }[],
): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {};
  const re = /^p_and_l_usali\.((?:19|20)\d\d)\.(.+)$/;
  for (const f of fields) {
    const m = re.exec(f.field_name ?? '');
    if (!m) continue;
    const v = f.value;
    if (typeof v !== 'number' || !Number.isFinite(v)) continue;
    (out[m[1]] ??= {})[m[2]] = v;
  }
  return out;
}

// Map one OM-embedded year's USALI lines (all `*_usd`, in dollars) to a HistYear.
function omYearToHistYear(v: Record<string, number>, year: string): HistYear {
  const n = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null);
  const num = (x: unknown): number => n(x) ?? 0;
  return {
    year,
    days: 365,
    occupancyPct: num(v.occupancy_pct),
    adr: num(v.adr_usd),
    revpar: num(v.revpar_usd),
    rooms: num(v.rooms_revenue_usd),
    fb: num(v.fb_revenue_usd),
    misc: num(v.other_operated_depts_revenue_usd) + num(v.miscellaneous_revenue_usd),
    rooms_dept_expense: n(v.rooms_dept_expense_usd),
    fb_dept_expense: n(v.fb_dept_expense_usd),
    other_dept_expense: n(v.other_operated_depts_expense_usd),
    undistributed: n(v.total_undistributed_expense_usd),
    gop: n(v.gop_usd),
    // Fees & fixed = mgmt + FF&E + property tax + insurance + rent (matches the
    // worksheet's Model "Total Fees & Fixed" so the columns reconcile).
    fixed_expenses:
      num(v.management_fee_usd) + num(v.ffe_reserve_usd) + num(v.property_tax_usd) +
      num(v.insurance_expense_usd) + num(v.rent_usd),
    noi: n(v.noi_usd),
    populated: true,
  };
}
