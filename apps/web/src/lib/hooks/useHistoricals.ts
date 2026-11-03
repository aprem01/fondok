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
  derivePeriodBasis,
  deriveYearLabel,
  emptyFiveYearSkeleton,
  fillSamePeriodStats,
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

/** One statement's built column, before the per-period choice. */
interface ColumnEntry {
  doc: WorkerDocument;
  base: string;
  built: HistYear;
  /** The full extraction (``fillSamePeriodStats`` applies ``actualsOnly``). */
  fields: ExtractionResult['fields'];
}

const isChosenRole = (d: WorkerDocument): boolean =>
  d.statement_role === 'year_one_primary' || d.statement_role === 'period_primary';

const IN_FLIGHT_STATUSES = new Set(['PARSING', 'UPLOADED', 'CLASSIFYING', 'EXTRACTING', 'PROCESSING']);
const FAILED_STATUSES = new Set(['FAILED', 'PARSE_FAILED']);

/** FON-22 R2-I1 — the column's statement is not EXTRACTED: say so. ``shown``
 *  is whether its last extraction is on the column. */
function statusOf(d: WorkerDocument, shown: boolean): HistYear['statementStatus'] {
  if (d.status === 'EXTRACTED') return undefined;
  const name = d.filename ?? 'The statement';
  if (IN_FLIGHT_STATUSES.has(d.status)) {
    return {
      state: 'reextracting',
      note: shown
        ? `${name} is re-extracting; showing its last extraction, which the model still reads.`
        : `${name} is re-extracting; its last extraction, which the model still reads, is not loaded.`,
    };
  }
  if (!FAILED_STATUSES.has(d.status)) {
    // A status this build does not know: say what it is, claim nothing more.
    return {
      state: 'reextracting',
      note: shown
        ? `${name} is ${d.status}; showing its last extraction, which the model still reads.`
        : `${name} is ${d.status}; its last extraction, which the model still reads, is not loaded.`,
    };
  }
  return {
    state: 'reextract_failed',
    note: shown
      ? `${name}: re-extract failed — showing the last extraction, which the model still reads.`
      : `${name}: re-extract failed — its last extraction, which the model still reads, is not loaded.`,
  };
}

const rankOf = (e: ColumnEntry): number =>
  typeof e.doc.statement_period_rank === 'number' ? e.doc.statement_period_rank : Number.POSITIVE_INFINITY;
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
  const shown = extracted.filter(isPnlDoc);
  // FON-22 R2-I1 / M6 — a period's CHOSEN statement (worker role) is the
  // period's column whatever its list status (re-extracting, or a failed
  // re-extract: the model still reads its last extraction) and whatever its
  // P&L-family type, as long as the period has a column at all; it is never
  // replaced by a supplemental statement.
  const shownPeriods = new Set(shown.map((d) => d.statement_period).filter((p): p is string => !!p));
  const chosenExtra = (docs ?? []).filter(
    (d) => !shown.includes(d) && isChosenRole(d)
      && (isPnlDoc(d) || (!!d.statement_period && shownPeriods.has(d.statement_period))),
  );
  const pnlDocs = [...shown, ...chosenExtra];
  const omDocs = extracted.filter(isOmDoc);
  if (pnlDocs.length === 0 && omDocs.length === 0) return [];

  const byLabel = new Map<string, HistYear>();
  const sorted = [...pnlDocs].sort((a, b) => (a.uploaded_at ?? '').localeCompare(b.uploaded_at ?? ''));
  const entries: ColumnEntry[] = [];
  const unavailable: WorkerDocument[] = [];
  for (const doc of sorted) {
    const ext = extractions[doc.id];
    if (!ext?.fields?.length) {
      if (chosenExtra.includes(doc)) unavailable.push(doc);
      continue;
    }
    const fields = actualsOnly(ext.fields);
    const base = deriveYearLabel(
      fields, doc.filename ?? '', doc.doc_type,
      doc.fiscal_year ?? doc.extracted_period_year,
    );
    // FON-41 #4 — the same inputs also say WHAT PERIOD this is (FY / YTD /
    // T12), which is what the column header renders. ``base`` stays the
    // stable key the worksheet and reviewState pin on.
    const period = derivePeriodBasis(
      fields, doc.filename ?? '', doc.doc_type,
      doc.fiscal_year ?? doc.extracted_period_year,
    );
    // Build on the base label (day-count / leap-year logic keys off it), then
    // stamp the unique column label.
    const built = buildHistYear(fields, keys, base, doc.id, period);
    if (!built) continue;
    entries.push({ doc, base, built, fields: ext.fields });
  }

  // FON-22 — one statement per period, and it is the WORKER's choice: the
  // engine loader's per-period pick (shared ranking + footing / outlier
  // override), surfaced on every document as ``statement_role`` /
  // ``statement_period`` / ``statement_period_rank``. Statements the worker
  // puts in one period give ONE column — its chosen statement — and the rest
  // are listed on it as supplemental; the column never re-ranks them.
  // Without roles (an older worker, or the rows could not be read) the
  // legacy collision rule below stands.
  if (entries.some((e) => e.doc.statement_role != null)) {
    const groups = new Map<string, ColumnEntry[]>();
    // A chosen statement whose last extraction is not on the page: its period
    // gets an empty, labelled column — the supplemental is NOT promoted.
    const held = new Map<string, WorkerDocument>();
    for (const d of unavailable) {
      if (d.statement_period) held.set(`period:${d.statement_period}`, d);
    }
    for (const e of entries) {
      const key = e.doc.statement_period ? `period:${e.doc.statement_period}` : `doc:${e.doc.id}`;
      const g = groups.get(key);
      if (g) g.push(e);
      else groups.set(key, [e]);
    }
    for (const [key, d] of held) {
      if (groups.has(key)) continue;
      const base = deriveYearLabel([], d.filename ?? '', d.doc_type, d.fiscal_year ?? d.extracted_period_year);
      const label = uniqueYearLabel(base, new Set(byLabel.keys()));
      byLabel.set(label, { ...blankYear(label), statementStatus: statusOf(d, false) });
    }
    for (const [key, members] of groups) {
      const heldChosen = held.get(key);
      if (heldChosen) {
        const base = members[0].base;
        const label = uniqueYearLabel(base, new Set(byLabel.keys()));
        byLabel.set(label, {
          ...blankYear(label),
          statementStatus: statusOf(heldChosen, false),
          supplementalDocs: members.map((s) => ({
            docId: s.doc.id, filename: s.doc.filename ?? 'statement', fields: s.fields.length,
          })),
        });
        continue;
      }
      // The worker's order within the period; a member without a rank (no
      // role yet) sorts last, in upload order.
      const ordered = members
        .map((e, i) => ({ e, i }))
        .sort((a, b) => (rankOf(a.e) - rankOf(b.e)) || (a.i - b.i))
        .map((x) => x.e);
      const [chosen, ...siblings] = ordered;
      let col = chosen.built;
      if (siblings.length) {
        col = fillSamePeriodStats(
          col,
          siblings.map((s) => ({ docId: s.doc.id, filename: s.doc.filename ?? 'statement', fields: s.fields })),
          chosen.doc.filename ?? 'the statement used',
          chosen.doc.statement_period_words ?? null,
        );
        col.supplementalDocs = siblings.map((s) => ({
          docId: s.doc.id, filename: s.doc.filename ?? 'statement', fields: s.fields.length,
        }));
      }
      const status = statusOf(chosen.doc, true);
      if (status) col = { ...col, statementStatus: status };
      const label = uniqueYearLabel(chosen.base, new Set(byLabel.keys()));
      col.year = label;
      byLabel.set(label, col);
    }
  } else {
    for (const { doc, base, built, fields } of entries) {
      // Legacy (no worker roles): two statements on one fiscal year give one
      // column — the flagged primary, else the fuller extraction, else the
      // newer upload (``entries`` is upload order, so the challenger is
      // newer). T-12 columns keep their own labels.
      if (isAnnualLabel(base) && byLabel.has(base)) {
        const incumbent = byLabel.get(base)!;
        const incumbentDoc = pnlDocs.find((d) => d.id === incumbent.docId);
        const incumbentFields = incumbentDoc ? (extractions[incumbentDoc.id]?.fields?.length ?? 0) : 0;
        const challengerFields = actualsOnly(fields).length;
        const incumbentPrimary = incumbentDoc?.primary_financial_source ?? false;
        const challengerPrimary = doc.primary_financial_source ?? false;
        const challengerWins = challengerPrimary !== incumbentPrimary
          ? challengerPrimary
          : challengerFields >= incumbentFields;
        const winner = challengerWins ? built : incumbent;
        const loserEntry = challengerWins
          ? { docId: incumbent.docId ?? incumbentDoc?.id ?? '', filename: incumbentDoc?.filename ?? 'statement', fields: incumbentFields }
          : { docId: doc.id, filename: doc.filename ?? 'statement', fields: challengerFields };
        winner.year = base;
        winner.supplementalDocs = [...(incumbent.supplementalDocs ?? []), loserEntry];
        byLabel.set(base, winner);
        continue;
      }
      const label = uniqueYearLabel(base, new Set(byLabel.keys()));
      built.year = label;
      byLabel.set(label, built);
    }
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
  /** useDocuments.settled — the shared per-deal store has completed its first
   *  list fetch and every EXTRACTED doc's extraction is loaded-or-failed
   *  (latched, so a late-arriving doc never flips a rendered grid back to a
   *  skeleton). Omit for callers that don't hold a skeleton. */
  documentsSettled?: boolean;
}

export function useHistoricals(
  dealId: string,
  opts: HistoricalsInputs,
): { years: HistYear[]; keys: number; loading: boolean } {
  const keysHint = opts.keys ?? 0;
  const { documents, extractions, documentsSettled = true } = opts;
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

  // Loading is honest and comes from the shared store: until the first list
  // fetch has completed and every EXTRACTED doc is loaded-or-failed.
  return {
    years: endpoint?.years ?? built,
    keys: endpoint?.keys ?? keysHint,
    loading: !documentsSettled,
  };
}

function blankYear(year: string): HistYear {
  return {
    // No statement behind it — it states no basis (and claims no reason to
    // refuse one: nothing was uploaded to mis-read).
    year, periodBasis: 'UNKNOWN', periodEnd: null, periodLabel: year,
    days: 365, occupancyPct: 0, adr: 0, revpar: 0,
    rooms: 0, fb: 0, misc: null,
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

/** Σ of the stated parts; ``null`` when none is stated (a dash, never $0). */
function sumStated(...parts: (number | null)[]): number | null {
  const xs = parts.filter((x): x is number => x != null);
  return xs.length ? xs.reduce((a, b) => a + b, 0) : null;
}

// Map one OM-embedded year's USALI lines (all `*_usd`, in dollars) to a HistYear.
function omYearToHistYear(v: Record<string, number>, year: string): HistYear {
  const n = (x: unknown): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null);
  const num = (x: unknown): number => n(x) ?? 0;
  return {
    year,
    // An OM's embedded historical-year block is annual by construction
    // (``concepts.yaml`` tags those aliases ``scope: annual``); the OM does
    // not publish the period end, so none is claimed.
    periodBasis: 'FY',
    periodEnd: null,
    periodLabel: `FY${year}`,
    days: 365,
    occupancyPct: num(v.occupancy_pct),
    adr: num(v.adr_usd),
    revpar: num(v.revpar_usd),
    rooms: num(v.rooms_revenue_usd),
    fb: num(v.fb_revenue_usd),
    // Wave 5 (Sam #3) — Other Operated + Misc. Income, each when stated; a
    // dash (null) when the OM states neither.
    misc: sumStated(n(v.other_operated_depts_revenue_usd), n(v.miscellaneous_revenue_usd)),
    other_operated_revenue: n(v.other_operated_depts_revenue_usd),
    misc_income: n(v.miscellaneous_revenue_usd),
    // E-012 — the OM's own stated total for that year, when it prints one.
    total_revenue_stated: n(v.total_revenue_usd),
    rooms_dept_expense: n(v.rooms_dept_expense_usd),
    fb_dept_expense: n(v.fb_dept_expense_usd),
    other_dept_expense: n(v.other_operated_depts_expense_usd),
    undistributed: n(v.total_undistributed_expense_usd),
    gop: n(v.gop_usd),
    // Fees & fixed = mgmt + FF&E + property tax + insurance + rent (matches the
    // worksheet's Model "Total Fees & Fixed" so the columns reconcile).
    fixed_expenses: sumStated(
      n(v.management_fee_usd), n(v.ffe_reserve_usd), n(v.property_tax_usd),
      n(v.insurance_expense_usd), n(v.rent_usd),
    ),
    mgmt_fee: n(v.management_fee_usd),
    ffe_reserve: n(v.ffe_reserve_usd),
    property_tax: n(v.property_tax_usd),
    insurance: n(v.insurance_expense_usd),
    rent: n(v.rent_usd),
    noi: n(v.noi_usd),
    populated: true,
  };
}
