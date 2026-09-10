'use client';

/**
 * useHistoricals — multi-year historical P&L columns for a deal.
 *
 * One column per EXTRACTED P&L / T-12 document (plus OM-embedded prior years),
 * labelled by fiscal year — the multi-doc logic HistoricalsSection shipped and
 * QA'd (Sam 2026-05-14), kept as the pure `buildHistoricalYears` below.
 *
 * FON-41 (Sam's deal, 2026-09-09): this hook used to fetch its OWN copy of the
 * document list and every extraction — serially, and only once the Financials
 * worksheet had mounted behind the tab's skeleton gate — while the Data Room
 * built its "N to review" badge from useDocuments' state (parallel, from page
 * load). Until that second chain finished, the worksheet had no columns at
 * all: "9 to review" in the Data Room, zero red cells in Financials. The hook
 * now builds columns from the SAME documents + extractions the caller already
 * holds, so both surfaces are one function over one state, and it reports
 * `loading` honestly so the worksheet can hold a skeleton instead of a false
 * empty state.
 *
 * The worker has no `/deals/{id}/historicals` route yet. The gated endpoint
 * branch stays so flipping HISTORICALS_ENDPOINT_READY prefers the server
 * payload when it lands — no route is added here.
 */

import { useEffect, useMemo, useState } from 'react';
import { isWorkerConnected, workerUrl, type ExtractionResult, type WorkerDocument } from '@/lib/api';
import {
  actualsOnly,
  baseYearLabel,
  buildHistYear,
  deriveYearLabel,
  emptyFiveYearSkeleton,
  labelOrdinal,
  uniqueYearLabel,
  type HistData,
  type HistYear,
} from '@/components/project/pl/HistoricalsSection';

const isPnlDoc = (d: WorkerDocument) => {
  const dt = (d.doc_type ?? '').toUpperCase();
  return (dt.includes('T12') || dt === 'T-12' || dt === 'PNL' || dt === 'P&L' || dt.includes('PROFIT'));
};
const isOmDoc = (d: WorkerDocument) => (d.doc_type ?? '').toUpperCase() === 'OM';

/** A document whose extraction feeds the historical columns. */
export const isHistoricalSourceDoc = (d: WorkerDocument): boolean =>
  d.status === 'EXTRACTED' && (isPnlDoc(d) || isOmDoc(d));

const isAnnualLabel = (label: string) => /^\d{4}$/.test(baseYearLabel(label));
const isT12Label = (label: string) => baseYearLabel(label) === 'T-12';

/**
 * Pure column builder — one HistYear per EXTRACTED P&L / T-12 document (plus
 * OM-embedded prior years that no statement covers). Same-label collisions
 * keep BOTH columns ("2023", "2023 (2)") instead of silently overwriting.
 * Returns [] until `keys` is known (the rooms fallback needs it).
 */
export function buildHistoricalYears(
  docs: WorkerDocument[],
  extractions: Record<string, ExtractionResult | undefined>,
  keys: number,
): HistYear[] {
  if (!(keys > 0)) return [];
  const extracted = (docs ?? []).filter((d) => d.status === 'EXTRACTED');
  const pnlDocs = extracted.filter(isPnlDoc);
  const omDocs = extracted.filter(isOmDoc);
  if (pnlDocs.length === 0 && omDocs.length === 0) return [];

  const byLabel = new Map<string, HistYear>();
  const sorted = [...pnlDocs].sort((a, b) => (a.uploaded_at ?? '').localeCompare(b.uploaded_at ?? ''));
  for (const doc of sorted) {
    const ext = extractions[doc.id];
    if (!ext?.fields) continue;
    const fields = actualsOnly(ext.fields);
    const base = deriveYearLabel(
      fields, doc.filename ?? '', doc.doc_type,
      doc.fiscal_year ?? doc.extracted_period_year,
    );
    // Build on the base label (day-count / leap-year logic keys off it), then
    // stamp the unique column label.
    const built = buildHistYear(fields, keys, base, doc.id);
    if (!built) continue;
    const label = uniqueYearLabel(base, new Set(byLabel.keys()));
    built.year = label;
    byLabel.set(label, built);
  }
  // OM-embedded prior years fill gaps a standalone statement doesn't cover
  // (e.g. an OM's own 2021-2023 P&L). Actual statements always win on a
  // shared year — the OM only backfills missing history.
  for (const doc of omDocs) {
    const ext = extractions[doc.id];
    if (!ext?.fields) continue;
    const embedded = extractEmbeddedYears(ext.fields);
    for (const [yr, vals] of Object.entries(embedded)) {
      if (!byLabel.has(yr) && Object.keys(vals).length >= 6) {
        byLabel.set(yr, omYearToHistYear(vals, yr));
      }
    }
  }
  if (byLabel.size === 0) return [];

  const byOrdinal = (a: string, b: string) => {
    const ya = baseYearLabel(a), yb = baseYearLabel(b);
    if (ya !== yb) return ya < yb ? -1 : 1;
    return labelOrdinal(a) - labelOrdinal(b);
  };
  const annualLabels = [...byLabel.keys()].filter(isAnnualLabel).sort(byOrdinal);
  const t12Labels = [...byLabel.keys()].filter(isT12Label).sort(byOrdinal);

  // Pad short histories with the 5-year skeleton so the grid keeps its
  // headers; padded years are populated:false and dropped by callers that
  // only want real data.
  const distinctAnnual = new Set(annualLabels.map(baseYearLabel));
  const skelAnnual = emptyFiveYearSkeleton().years.slice(0, -1);
  const labels = new Set<string>(annualLabels);
  if (distinctAnnual.size < skelAnnual.length) {
    for (const s of skelAnnual) if (!distinctAnnual.has(s.year)) labels.add(s.year);
  }
  const orderedAnnual = [...labels].sort(byOrdinal);
  const annualCols: HistYear[] = orderedAnnual.map((label) => {
    const real = byLabel.get(label);
    if (real) return real;
    return skelAnnual.find((y) => y.year === label) ?? blankYear(label);
  });
  const t12Cols: HistYear[] = t12Labels.length
    ? t12Labels.map((l) => byLabel.get(l)!)
    : [blankYear('T-12')];
  return [...annualCols, ...t12Cols];
}

export interface HistoricalsInputs {
  /** Deal key count — columns can't be built until it is known. */
  keys?: number | null;
  /** The caller's useDocuments state — the SAME objects the review state reads. */
  documents: WorkerDocument[];
  extractions: Record<string, ExtractionResult | undefined>;
  /** useDocuments.settled — the first document-list fetch has completed. */
  documentsSettled?: boolean;
  /** useDocuments.extractionFailures — docs whose extraction fetch gave up. */
  extractionFailures?: Record<string, boolean>;
}

export function useHistoricals(
  dealId: string,
  opts: HistoricalsInputs,
): { years: HistYear[]; keys: number; loading: boolean } {
  const keysHint = opts.keys ?? 0;
  const { documents, extractions, documentsSettled = true, extractionFailures } = opts;
  const [endpoint, setEndpoint] = useState<HistData | null>(null);

  // 1) endpoint — not implemented in the worker yet, so probing it just
  //    logged a 404 in every browser console on the Financials tab. Gated
  //    off until the route lands (flip when it does); when it answers, its
  //    years take precedence over the client-built columns.
  useEffect(() => {
    const HISTORICALS_ENDPOINT_READY = false;
    if (!HISTORICALS_ENDPOINT_READY) return;
    const isMockId = /^\d+$/.test(dealId);
    if (!dealId || isMockId || !isWorkerConnected()) { setEndpoint(null); return; }
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${workerUrl()}/deals/${dealId}/historicals`);
        if (!res.ok) return;
        const json = (await res.json()) as Partial<HistData> | null;
        if (json && Array.isArray(json.years) && json.years.length > 0 && !cancelled) {
          setEndpoint({ keys: json.keys ?? keysHint, years: json.years as HistYear[] });
        }
      } catch {
        /* worker offline / route absent — client-built columns stand */
      }
    })();
    return () => { cancelled = true; };
  }, [dealId, keysHint]);

  // 2) client-built columns from the caller's documents + extractions — the
  //    same inputs the Data Room badge is computed from (FON-41).
  const built = useMemo(
    () => buildHistoricalYears(documents, extractions, keysHint),
    [documents, extractions, keysHint],
  );

  // Loading is honest: the list hasn't been fetched yet, or a statement that
  // feeds the columns has neither an extraction nor a recorded fetch failure.
  const pending = useMemo(
    () => documents.some((d) => isHistoricalSourceDoc(d) && !extractions[d.id] && !extractionFailures?.[d.id]),
    [documents, extractions, extractionFailures],
  );
  const loading = !documentsSettled || pending;

  return {
    years: endpoint?.years ?? built,
    keys: endpoint?.keys ?? keysHint,
    loading,
  };
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
