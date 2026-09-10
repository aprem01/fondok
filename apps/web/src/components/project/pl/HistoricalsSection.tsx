'use client';
/**
 * HistoricalsSection — multi-year proforma historicals table.
 *
 * Lovable parity: "PRELIMINARY HOTEL UNDERWRITING / Proforma Historicals"
 * card with a wide table of operating metrics + revenue lines per
 * historical year. Each year column splits into 4 sub-columns:
 * Amount | % Rev | PAR | POR.
 *
 * Data sources, in priority order:
 *   1. ``GET /deals/{id}/historicals`` — net-new endpoint, may 404; we
 *      treat 404 as "fall through".
 *   2. The latest extraction on the deal's T-12 document (anchors a
 *      single rightmost year).
 *
 * No new worker route is wired here — that's net-new scope. We just
 * gracefully render an inline empty state when nothing's available.
 */
import { useEffect, useMemo, useState } from 'react';
import { Download, FileText, History } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import {
  api, isWorkerConnected, workerUrl, ExtractionField, WorkerDocument,
} from '@/lib/api';
import { useDeal } from '@/lib/hooks/useDeal';
import { downloadXlsx, type XlsxCell } from '@/lib/exportXlsx';
import {
  HISTORICALS_ALIASES, PERIOD_ALIASES, isSubordinatePath,
} from '@/lib/ontology/adapters';

// ─────────────────────────── Data shape ───────────────────────────
// One historical year column. ``amount`` for Rooms / F&B / Misc / dept
// expenses / GOP / NOI are in raw dollars (not thousands) so the display
// layer handles the /1000 scaling consistently with PAR / POR math.
//
// Expense / GOP / NOI fields use the SAME canonical field-name slugs the
// historical_baseline engine ships (rooms_dept_expense, fb_dept_expense,
// other_dept_expense, undistributed, gop, fixed_expenses, noi) so when
// the per-deal /historicals endpoint ships a HistoricalYear payload from
// the engine, no key remapping is needed — see task C 2026-06-29.
export interface HistYear {
  /** Calendar year label, e.g. 2023 or "T-12". */
  year: string;
  /** Days in the period (365/366 for a calendar year, 365 for T-12). */
  days: number;
  occupancyPct: number; // 0..1
  adr: number;          // $
  revpar: number;       // $
  rooms: number;        // $ (top-of-house)
  fb: number;           // $
  misc: number;         // $
  // ─── Expenses / profitability (Task C 2026-06-29) ───
  // Engine-canonical slugs, ``null`` when the extractor didn't ship the
  // line (renders em-dash). USALI convention: expenses are POSITIVE
  // numbers (the GOP/NOI math subtracts them); a negative here would be
  // an extraction bug.
  rooms_dept_expense: number | null;
  fb_dept_expense: number | null;
  other_dept_expense: number | null;
  /** A&G + sales/mkt + utilities + prop_ops + info/telecom rollup. */
  undistributed: number | null;
  gop: number | null;
  /** property_tax + insurance + mgmt_fee (institutional fixed-block). */
  fixed_expenses: number | null;
  noi: number | null;
  /** ``true`` when all numeric series are present; ``false`` for placeholder/empty columns. */
  populated: boolean;
  /** FON-41 Part B — the fixed-charge parts behind ``fixed_expenses``, kept
   *  individually so the worksheet's Management Fee / Property Taxes /
   *  Insurance cells can render (and flag) the extracted line. Optional:
   *  absent on OM-embedded years and older payloads. */
  mgmt_fee?: number | null;
  property_tax?: number | null;
  insurance?: number | null;
  /** The statement this column was built from (absent for OM-embedded years).
   *  Lets a Data Room deep-link (?doc=<id>) pin the column without scanning
   *  meta. */
  docId?: string;
  /** Design rewire: per-line source metadata for review flagging — worksheet
   *  line id → { extraction confidence 0..1, matched field name, source doc
   *  id }. Recorded for EVERY line ``buildHistYear`` resolves (FON-41 Part B)
   *  so any low-confidence historical cell can flag and open the SOURCE panel
   *  at its own document. */
  meta?: Record<string, HistLineMeta>;
}

export interface HistLineMeta {
  confidence: number;
  field: string;
  docId?: string;
}

export interface HistData {
  keys: number;
  years: HistYear[];
}

// ─────────────────────────── T-12 derivation ───────────────────────────
// Pull a tolerant set of T-12 metrics off the worker extraction. We look
// at common field-name aliases since the schema isn't fully locked. Any
// value that doesn't parse cleanly drops out of the displayed row.
function num(field: ExtractionField | undefined): number | null {
  if (!field || field.value === null || field.value === undefined) return null;
  if (typeof field.value === 'number' && Number.isFinite(field.value)) return field.value;
  if (typeof field.value === 'string') {
    const cleaned = field.value.replace(/[$,\s%]/g, '');
    const n = Number(cleaned);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/**
 * ``true`` for monthly / page / quarterly / per-month slices that must
 * never be matched as a period total.
 *
 * Phase 1.4: the namespace list is no longer hand-maintained here — it is
 * filtered out of the registry's ``SUBORDINATE_NAMESPACES`` by
 * ``isSubordinatePath`` (``lib/ontology/adapters``), which is generated from
 * ``concepts.yaml`` and so cannot drift from the worker resolver.
 *
 * Sam QA 2026-06-30 (deal b5f532ad…): the real prod T-12 ships a monthly
 * January slice of Rooms revenue (and 11 sibling months). ``findField``
 * matched the last dotted segment (the unit-stripped canonical), so the
 * JANUARY value ($1,086) was shown as both 2023 and 2025 Rooms revenue
 * across the Historicals tab. The worker resolver
 * (``_token_match_candidates``) already filters subordinate namespaces
 * out of its candidate pool; this is the matching frontend filter.
 */
function hasSubordinateNamespace(key: string): boolean {
  return isSubordinatePath(key);
}

/**
 * Match an extracted field by alias. The extractor emits fields under
 * dotted USALI paths and with unit suffixes (``adr_usd``,
 * ``occupancy_pct``). The old exact-normalized-match only caught bare
 * names like ``occupancy_pct``, which is why the Historicals T-12 column
 * showed Occupancy but blanked ADR / RevPAR / every revenue line
 * (Sam QA 2026-05-14 #1).
 *
 * Matching strategy — for each field, try the full normalized name,
 * the last dotted segment, and both with the unit suffix stripped. This
 * IS tier 4 + tier 5 of the registry resolver (DRIFT_NOTES.md §7): the
 * unit strip and the tail match, with the alias sets coming from
 * ``CONCEPTS[id].aliases``.
 *
 * Subordinate-namespace guard — fields under a monthly / page /
 * quarterly / q1-q4 namespace are skipped BEFORE the alias match, because
 * their tail segment looks identical to the annual canonical. See
 * ``hasSubordinateNamespace`` and the matching worker helper
 * ``_has_subordinate_namespace``.
 */
export function findField(fields: ExtractionField[], aliases: string[]): ExtractionField | undefined {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const stripUnit = (s: string) => s.replace(/(usd|pct|percent|ratio|amount)$/i, '');
  const aSet = new Set<string>();
  for (const a of aliases) {
    const n = norm(a);
    aSet.add(n);
    aSet.add(stripUnit(n));
  }
  const usable = fields.filter((f) => !hasSubordinateNamespace(f.field_name));
  // Pass 1 — exact FULL-PATH match. This must win over a loose last-segment
  // match: a bare alias like "food_beverage" (F&B revenue) otherwise matched
  // the last segment of an EXPENSE field (…expenses.food_beverage), so F&B
  // Revenue rendered the F&B expense value (QA #2 misplacement). A specific
  // revenue path (…food_and_beverage.revenue_usd) now resolves first.
  for (const f of usable) {
    const full = norm(f.field_name);
    if (aSet.has(full) || aSet.has(stripUnit(full))) return f;
  }
  // Pass 2 — last-segment fallback for flat extractions where field_name is
  // the bare token (e.g. "fb_revenue"). Only reached when no full-path
  // matched, so the specific-path collision above can't happen here.
  for (const f of usable) {
    const segs = f.field_name.split('.');
    const last = norm(segs[segs.length - 1] ?? '');
    if (last && (aSet.has(last) || aSet.has(stripUnit(last)))) return f;
  }
  return undefined;
}

/**
 * Drop forward-looking fields before any historical building.
 *
 * Sam QA 2026-05-14: the T-12 doc carries BOTH actuals (period_ending =
 * 2025-05-31) AND a forecast block (``forecast.period_ending`` =
 * 2025-12-31). ``findField`` matches on the last dotted segment, so
 * ``forecast.period_ending`` shadowed the real period_ending — the T-12
 * doc got mislabeled "2025" (December) instead of "T-12", and its data
 * landed in the wrong column with the real T-12 column left blank. The
 * Historicals tab is actuals-only; strip anything under a
 * forecast/projection/budget namespace.
 *
 * Sam QA 2026-06-30: also drop subordinate-period namespaces (monthly /
 * page / quarterly / per_month / q1-q4) so a stray monthly slice never
 * wins over the period total. That list is the registry's
 * ``SUBORDINATE_NAMESPACES`` via ``isSubordinatePath``, which mirrors
 * ``_has_subordinate_namespace`` in
 * ``apps/worker/app/services/usali_scorer.py``.
 */
export function actualsOnly(fields: ExtractionField[]): ExtractionField[] {
  return fields.filter((f) => {
    const n = f.field_name.toLowerCase();
    return !(
      n.startsWith('forecast.') ||
      n.startsWith('projection.') ||
      n.startsWith('projected.') ||
      n.startsWith('budget.') ||
      n.includes('.forecast.') ||
      n.includes('.projection.') ||
      n.includes('.projected.') ||
      n.includes('.budget.') ||
      // Subordinate-period slices — never a period total.
      isSubordinatePath(n)
    );
  });
}

/**
 * Derive the calendar-year label for a P&L / T-12 document from its
 * extracted fields, the document's classified ``doc_type``, and the
 * filename (last resort). Returns ``"T-12"`` for a trailing-twelve
 * period, or the 4-digit year for an annual statement.
 *
 * Sam QA 2026-05-14 (3rd report): the same T-12 file
 * ("…May 2025 Financials.xlsx") extracted WITH period_ending on some
 * deals and WITHOUT it on others (extractor non-determinism across
 * builds). When period_ending was missing, the resolver fell through
 * to the filename — which contains "2025" — and labeled the T-12 doc
 * as a "2025" calendar column. The actual "T-12" column then had
 * nothing in it. Fix: a doc classified ``T12`` is a trailing-twelve
 * BY DEFINITION; it can never take a calendar-year label from a
 * filename. Only annual P&L docs (``PNL``) use the filename-year
 * fallback.
 */
export function deriveYearLabel(
  fields: ExtractionField[],
  filename: string,
  docType: string | null | undefined,
  // The document's normalized period year — ``fiscal_year`` (the analyst's tag)
  // or, failing that, ``extracted_period_year`` (Fondok's read of period_ending).
  // This is the SAME signal Historical Coverage trusts, passed in so the
  // rendered year columns stay in lockstep with the coverage chips (FON-15).
  normalizedYear?: number | string | null,
): string {
  // Resolution order — most authoritative first. The extractor is the
  // format-agnostic layer (it now emits period metadata for any P&L
  // layout); the filename is only a last-resort safety net.
  const strVal = (f: ExtractionField | undefined): string | null =>
    f && typeof f.value === 'string' ? f.value : null;

  const dt = (docType ?? '').toUpperCase();
  const isT12Type = dt === 'T12' || dt === 'T-12' || dt.includes('T12');

  // 0. Normalized period on the document itself — the post-classification
  //    fiscal year that Historical Coverage reads. Trusting it here is what
  //    keeps the rendered columns dynamic and consistent with Coverage
  //    (FON-15): any uploaded year Coverage recognizes — e.g. 2019 or a
  //    partial/YTD 2025 — gets its own column instead of being re-derived
  //    from raw fields and silently dropped into the T-12 slot. A
  //    trailing-twelve doc still resolves to 'T-12' below.
  if (!isT12Type && normalizedYear != null && normalizedYear !== '') {
    const ny = String(normalizedYear).match(/((?:19|20)\d{2})/);
    if (ny) return ny[1];
  }

  // Alias lists come from the generated registry (Phase 1.4) — see
  // ``PERIOD_ALIASES`` in lib/ontology/adapters.
  const periodEnding = strVal(findField(fields, PERIOD_ALIASES.period_ending));
  const periodType = strVal(findField(fields, PERIOD_ALIASES.period_type));
  const periodLabel = strVal(findField(fields, PERIOD_ALIASES.period_label));

  // 1. period_type + period_ending — the cleanest signal. Annual →
  //    the calendar year of period_ending. Anything rolling/partial
  //    → "T-12".
  if (periodType) {
    const pt = periodType.toLowerCase();
    if (pt === 'annual') {
      const yr = (periodEnding ?? periodLabel ?? '').match(/(20\d{2})/);
      if (yr) return yr[1];
    }
    if (/trailing|ttm|t-?12|ytd|quarter|month/.test(pt)) return 'T-12';
  }

  // 2. period_ending alone — December-ending → calendar year;
  //    mid-year-ending → trailing-twelve.
  if (periodEnding) {
    const iso = periodEnding.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (iso) return iso[2] === '12' ? iso[1] : 'T-12';
    const yr = periodEnding.match(/(20\d{2})/);
    if (yr) return yr[1];
  }

  // 3. period_label text — "FY2023", "Year Ended Dec 2023", "TTM …".
  if (periodLabel) {
    if (/ttm|trailing|t-?12/i.test(periodLabel)) return 'T-12';
    const yr = periodLabel.match(/(20\d{2})/);
    if (yr) return yr[1];
  }

  // 3b. Explicit fiscal-year filename marker (e.g. "…_PL_FY2023") beats the
  //     blunt T-12 fallback below. A Summary P&L that failed to extract
  //     period metadata was being classified T12 and clobbering the real
  //     trailing-twelve column with an older annual's numbers (QA: Harbor
  //     Palms historicals showed 2023 metrics in the T-12 column). "FY" is
  //     an unambiguous fiscal-year signal — unlike a bare year, which can
  //     be a period-END ("May 2025 Financials" → a T-12, not FY2025).
  //
  //     The "FY" must be preceded by start-of-string or a separator
  //     ([\s_.-]) — NOT ``\bFY``: a word boundary fails on the common
  //     "_FY2023" (underscore is a word char, so there's no boundary
  //     before F), which was silently dropping this match and leaving the
  //     Summary in the T-12 column (QA re-run #2 repro).
  //     Trailing ``(?![0-9])`` (not ``\b``): the year is often followed by
  //     "_" ("FY2024_PL"), and "4_" has no word boundary either — a
  //     not-a-digit lookahead accepts "_", ".", or end while still
  //     rejecting a longer run like "FY20245".
  const fyYear = filename.match(/(?:^|[\s_.-])FY[\s_.-]?(20\d{2})(?![0-9])/i)?.[1];
  if (fyYear) return fyYear;

  // 3c. doc_type guard — a genuine T12-classified doc with no period
  //     metadata is a trailing-twelve by definition, so it lands in "T-12".
  if (isT12Type) return 'T-12';

  // 4. filename — last resort for ANNUAL P&L docs only, e.g.
  //    "Angler's 2023 P&L.xlsx" → 2023.
  const fnYear = filename.match(/(20\d{2})/);
  if (fnYear) return fnYear[1];

  // 5. nothing usable — default to the T-12 slot.
  return 'T-12';
}

/**
 * FON-41 — column-label collisions. Two statements can resolve to the same
 * label (two "2023" P&Ls, or a T-12 plus a re-upload of it). They used to
 * overwrite each other in the year map, so one document's "N to review" had
 * no column to land on. Each column now keeps a unique label: the first keeps
 * the bare label, later ones get an ordinal — "2023", "2023 (2)", "T-12 (2)".
 * ``baseYearLabel`` recovers the underlying period for sorting / coverage.
 */
const LABEL_ORDINAL = /^(.*?)\s\((\d+)\)$/;

export function baseYearLabel(label: string): string {
  const m = LABEL_ORDINAL.exec(label);
  return m ? m[1] : label;
}

export function labelOrdinal(label: string): number {
  const m = LABEL_ORDINAL.exec(label);
  return m ? Number(m[2]) : 1;
}

export function uniqueYearLabel(label: string, taken: ReadonlySet<string>): string {
  if (!taken.has(label)) return label;
  for (let n = 2; ; n++) {
    const candidate = `${label} (${n})`;
    if (!taken.has(candidate)) return candidate;
  }
}

/**
 * Build one historical-year column from a P&L / T-12 extraction.
 * ``yearLabel`` comes from ``deriveYearLabel``; ``days`` is 365 for a
 * T-12 and a real day count for an annual column.
 */
export function buildHistYear(
  fields: ExtractionField[],
  keys: number,
  yearLabel: string,
  docId?: string,
): HistYear | null {
  if (!fields.length) return null;

  // Design rewire: capture per-line source metadata (confidence + matched
  // field + doc) as we resolve each value, so historical cells can flag
  // low-confidence extractions and open the SOURCE panel. `pick` is a thin
  // wrapper over findField that records meta then returns the numeric value —
  // the value logic below is unchanged.
  const meta: Record<string, HistLineMeta> = {};
  const pick = (key: string): number | null => {
    const f = findField(fields, HISTORICALS_ALIASES[key] ?? []);
    if (f && typeof f.confidence === 'number') {
      meta[key] = { confidence: f.confidence, field: f.field_name, docId };
    }
    return num(f);
  };

  // ─── Alias lists come from the GENERATED concept registry (Phase 1.4) ───
  //   src/lib/ontology/concepts.generated.ts
  //     ← apps/worker/app/ontology/concepts.yaml  (CI-gated: gen_ontology.py --check)
  //
  // The extractor is non-deterministic across years: one year emits the
  // revenue namespace, the next the per-department bucket, 2019/2021 yet
  // another shape. Every shape any resolver has ever accepted now lives in
  // the registry, so this file no longer carries the hand-maintained copy
  // that used to sit here under a "FUTURE DRIFT WARNING" (Wave 1,
  // 2026-06-30 Sam QA). ``HISTORICALS_ALIASES`` flattens
  // ``CONCEPTS[id].aliases`` for the P&L family; ``findField`` matches both
  // the full normalized name and the last dotted segment with the unit
  // suffix stripped — tiers 4-5 of the worker resolver.
  //
  // Historicals COLLAPSES the registry's two revenue concepts —
  // ``other_revenue`` (Other Operated Departments) and ``misc_revenue``
  // (Miscellaneous Income) — into the single "Misc. Income" column, so the
  // ``misc`` list is the union of both. See DRIFT_NOTES.web.md.
  const occ = pick('occ');
  const adr = pick('adr');
  const revpar = pick('revpar');
  const rooms = pick('rooms');
  const fb = pick('fb');
  const misc = pick('misc');

  // ─── Expenses / profitability (Task C 2026-06-29) ───
  const roomsDept = pick('rooms_dept');
  const fbDept = pick('fb_dept');
  const otherDept = pick('other_dept');
  const undistributed = pick('undistributed');
  const gop = pick('gop');
  const propTax = pick('property_tax');
  const insurance = pick('insurance');
  const mgmtFee = pick('mgmt_fee');
  const fixedParts = [propTax, insurance, mgmtFee].filter(
    (v): v is number => v != null,
  );
  const fixedExpenses = fixedParts.length ? fixedParts.reduce((a, b) => a + b, 0) : null;
  // NOI also accepts the "EBITDA less Replacement Reserve" line as a
  // reasonable proxy when the statement never publishes NOI directly —
  // those paths are on the ``noi`` concept in the registry
  // (DRIFT_NOTES.md §3.4).
  const noi = pick('noi');

  // Need at least one of {rooms, occupancy, adr} to render anything.
  if (rooms == null && occ == null && adr == null) return null;

  // Annual columns use the real day count; T-12 is always 365.
  const isAnnual = /^\d{4}$/.test(yearLabel);
  const yearNum = isAnnual ? Number(yearLabel) : 0;
  const days = isAnnual
    ? (yearNum % 4 === 0 && (yearNum % 100 !== 0 || yearNum % 400 === 0) ? 366 : 365)
    : 365;

  // Occupancy may have come in as a percent (e.g. 73.8) — normalize.
  const occNorm = occ == null ? 0 : occ > 1.5 ? occ / 100 : occ;
  const adrNorm = adr ?? 0;
  const revparNorm = revpar ?? (adrNorm * occNorm);
  const roomsNorm = rooms ?? (keys > 0 ? revparNorm * keys * days : 0);

  return {
    year: yearLabel,
    days,
    occupancyPct: occNorm,
    adr: adrNorm,
    revpar: revparNorm,
    rooms: roomsNorm,
    fb: fb ?? 0,
    misc: misc ?? 0,
    rooms_dept_expense: roomsDept,
    fb_dept_expense: fbDept,
    other_dept_expense: otherDept,
    undistributed,
    gop,
    fixed_expenses: fixedExpenses,
    mgmt_fee: mgmtFee,
    property_tax: propTax,
    insurance,
    noi,
    populated: true,
    docId,
    meta,
  };
}

// Default 5-year window when nothing is extractable yet — used as the
// scaffold for the "empty" rendering so reviewers still see column
// headers and row labels.
export function emptyFiveYearSkeleton(): HistData {
  const thisYear = new Date().getFullYear();
  const years: HistYear[] = [];
  for (let i = 4; i >= 0; i--) {
    const y = thisYear - 1 - i; // last fully-closed year and back
    years.push({
      year: String(y),
      days: y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0) ? 366 : 365,
      occupancyPct: 0, adr: 0, revpar: 0,
      rooms: 0, fb: 0, misc: 0,
      rooms_dept_expense: null, fb_dept_expense: null,
      other_dept_expense: null, undistributed: null,
      gop: null, fixed_expenses: null, noi: null,
      populated: false,
    });
  }
  return { keys: 0, years };
}

// ─────────────────────────── Component ───────────────────────────
export default function HistoricalsSection({
  dealId,
}: {
  dealId: string;
}) {
  const { toast } = useToast();
  const { deal } = useDeal(dealId);

  const isMockId = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !!dealId && !isMockId;

  const [data, setData] = useState<HistData | null>(null);
  const [loading, setLoading] = useState(false);

  // 1) live deal: try /deals/{id}/historicals (graceful 404), then T-12.
  // 2) otherwise: render the empty 5-year skeleton.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!liveMode) {
        setData(emptyFiveYearSkeleton());
        return;
      }
      setLoading(true);
      // The `/deals/{id}/historicals` endpoint isn't implemented in the worker
      // yet, so probing it logged a 404 in every browser console on the
      // Financials tab. Skip straight to the T-12 multi-doc fallback (which
      // renders the table today) until the endpoint ships — flip this flag
      // when it does.
      const HISTORICALS_ENDPOINT_READY = false;
      try {
        if (HISTORICALS_ENDPOINT_READY) {
          const base = workerUrl();
          const res = await fetch(`${base}/deals/${dealId}/historicals`);
          if (res.ok) {
            const json = (await res.json()) as Partial<HistData> | null;
            if (json && Array.isArray(json.years) && json.years.length > 0) {
              if (!cancelled) {
                setData({
                  keys: json.keys ?? deal?.keys ?? 0,
                  years: json.years as HistYear[],
                });
              }
              return;
            }
          }
          // 404 (or empty payload) → fall through to T-12 fallback.
        }
      } catch {
        // Worker offline / route absent — fall through.
      } finally {
        // Note: setLoading(false) handled in fallback path below.
      }

      // Multi-doc fallback: build one historical column per EXTRACTED
      // P&L / T-12 document on the deal. Sam QA 2026-05-14 #2: a
      // separately-uploaded annual P&L (e.g. "Angler's 2023 P&L.xlsx")
      // was being ignored entirely — the old code only ever looked at
      // the single most-recent T12-typed doc and built one "T-12"
      // column. Now every P&L/T12 doc maps to its own year column via
      // the period_ending field (or filename year as a fallback).
      try {
        const docs = await api.documents.list(String(dealId)) as WorkerDocument[];
        const keysForBuild = deal?.keys ?? 0;
        const pnlDocs = (docs ?? [])
          .filter(d => {
            const dt = (d.doc_type ?? '').toUpperCase();
            return (
              dt.includes('T12') ||
              dt === 'T-12' ||
              dt === 'PNL' ||
              dt === 'P&L' ||
              dt.includes('PROFIT')
            );
          })
          .filter(d => d.status === 'EXTRACTED');

        if (pnlDocs.length > 0 && keysForBuild > 0) {
          // Build a year-keyed map. When two docs land on the same
          // label the most-recently-uploaded one wins.
          const byYear = new Map<string, HistYear>();
          const sorted = [...pnlDocs].sort(
            (a, b) => (a.uploaded_at ?? '').localeCompare(b.uploaded_at ?? ''),
          );
          for (const doc of sorted) {
            try {
              const ext = await api.documents.extraction(String(dealId), doc.id);
              // Historicals is actuals-only — strip the forecast block
              // so forecast.period_ending / forecast.adr_usd can't
              // shadow the real values (Sam QA 2026-05-14).
              const fields = actualsOnly(ext.fields ?? []);
              // Pass doc_type — a T12-classified doc is a
              // trailing-twelve by definition and must never be
              // labeled by a year in its filename.
              const label = deriveYearLabel(
                fields, doc.filename ?? '', doc.doc_type,
                doc.fiscal_year ?? doc.extracted_period_year,
              );
              const built = buildHistYear(fields, keysForBuild, label);
              if (built) byYear.set(label, built);
            } catch {
              // skip this doc — others may still populate.
            }
          }

          if (byYear.size > 0 && !cancelled) {
            // Lay out columns: every uploaded calendar year (ascending),
            // then a T-12 column on the far right. No upper cap — the
            // table grows horizontally as the user adds older P&Ls.
            // If fewer than 4 real years are present, fill from the
            // skeleton so the empty/partial state still shows a
            // consistent baseline.
            const t12 = byYear.get('T-12') ?? null;
            const annualYears = [...byYear.keys()]
              .filter(y => /^\d{4}$/.test(y))
              .sort();
            const skel = emptyFiveYearSkeleton();
            const skelAnnual = skel.years.slice(0, -1); // 4 placeholder cols

            const realByYear = new Map(annualYears.map(y => [y, byYear.get(y)!]));
            // Always include every real year. Pad with skeleton
            // placeholders only when we have fewer than 4 reals, so the
            // baseline view is never narrower than the demo skeleton.
            const labels = new Set<string>(annualYears);
            if (annualYears.length < skelAnnual.length) {
              for (const s of skelAnnual) labels.add(s.year);
            }
            const orderedAnnual = [...labels].sort();
            const annualCols: HistYear[] = [];
            for (const label of orderedAnnual) {
              const real = realByYear.get(label);
              if (real) {
                annualCols.push(real);
              } else {
                const placeholder = skelAnnual.find(y => y.year === label);
                annualCols.push(
                  placeholder ?? {
                    year: label,
                    days: 365,
                    occupancyPct: 0, adr: 0, revpar: 0,
                    rooms: 0, fb: 0, misc: 0,
                    rooms_dept_expense: null, fb_dept_expense: null,
                    other_dept_expense: null, undistributed: null,
                    gop: null, fixed_expenses: null, noi: null,
                    populated: false,
                  },
                );
              }
            }

            const merged: HistData = {
              keys: keysForBuild,
              years: [
                ...annualCols,
                t12 ?? {
                  year: 'T-12',
                  days: 365,
                  occupancyPct: 0, adr: 0, revpar: 0,
                  rooms: 0, fb: 0, misc: 0,
                  rooms_dept_expense: null, fb_dept_expense: null,
                  other_dept_expense: null, undistributed: null,
                  gop: null, fixed_expenses: null, noi: null,
                  populated: false,
                },
              ],
            };
            setData(merged);
            return;
          }
        }
      } catch {
        // ignore — empty state below.
      }
      if (!cancelled) setData(emptyFiveYearSkeleton());
    }
    load().finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [dealId, liveMode, deal?.keys]);

  const onNotes = () => {
    toast('Historicals notes — coming with the next deploy', { type: 'info' });
  };

  const onExport = async () => {
    if (!data) return;
    const headers: XlsxCell[] = ['Metric', ...data.years.flatMap(y => [
      `${y.year} Amount`, `${y.year} % Rev`, `${y.year} PAR`, `${y.year} POR`,
    ])];
    const rows: XlsxCell[][] = [];
    const keys = data.keys;
    const fmtRow = (label: string, get: (y: HistYear) => number | null) => {
      const row: XlsxCell[] = [label];
      for (const y of data.years) {
        const raw = get(y);
        const v = raw ?? 0;
        const totalRev = y.rooms + y.fb + y.misc;
        const avail = keys * y.days;
        const occRooms = avail * y.occupancyPct;
        // Keep numerics as numbers so Excel can re-sum / re-format them.
        // Null source values render as blank cells so analysts can spot
        // unextracted lines vs. legitimate zeros.
        row.push(
          raw != null ? Number(v.toFixed(0)) : '',
          raw != null && totalRev ? Number(((v / totalRev) * 100).toFixed(1)) : '',
          raw != null && avail ? Number((v / avail).toFixed(2)) : '',
          raw != null && occRooms ? Number((v / occRooms).toFixed(2)) : '',
        );
      }
      rows.push(row);
    };
    fmtRow('Rooms', y => y.rooms);
    fmtRow('Food & Beverage', y => y.fb);
    fmtRow('Misc. Income', y => y.misc);
    fmtRow('Rooms Expense', y => y.rooms_dept_expense);
    fmtRow('Food & Beverage Expense', y => y.fb_dept_expense);
    fmtRow('Other Operated Expense', y => y.other_dept_expense);
    fmtRow('Undistributed Expenses', y => y.undistributed);
    fmtRow('GOP', y => y.gop);
    fmtRow('Fixed Expenses', y => y.fixed_expenses);
    fmtRow('NOI', y => y.noi);
    await downloadXlsx(`historicals-${dealId || 'deal'}`, [
      { name: 'Historicals', rows: [headers, ...rows] },
    ]);
  };

  const keys = deal?.keys ?? data?.keys ?? 0;

  const hasAnyData = (data?.years ?? []).some(y => y.populated);

  return (
    <Card className="p-6">
      {/* Header */}
      <div className="flex items-start justify-between mb-5">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-lg bg-ink-100 text-ink-700 flex items-center justify-center">
            <History size={18} />
          </div>
          <div>
            <div className="text-[10.5px] tracking-[0.12em] uppercase font-semibold text-ink-500">
              Preliminary Hotel Underwriting
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900 leading-tight mt-0.5">
              Proforma Historicals
            </h3>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={onNotes}>
            <FileText size={11} /> Notes
          </Button>
          <Button variant="secondary" size="sm" onClick={onExport} disabled={!hasAnyData}>
            <Download size={11} /> Export
          </Button>
        </div>
      </div>

      {/* Table */}
      <HistoricalsTable
        data={data ?? emptyFiveYearSkeleton()}
        keys={keys}
        loading={loading}
        hasAnyData={hasAnyData}
      />
    </Card>
  );
}

// ─────────────────────────── Table ───────────────────────────
function HistoricalsTable({
  data,
  keys,
  loading,
  hasAnyData,
}: {
  data: HistData;
  keys: number;
  loading: boolean;
  hasAnyData: boolean;
}) {
  const years = data.years;
  const colsPerYear = 4;

  // Per-year derived metrics — kept in lockstep with the year list.
  const derived = useMemo(() => years.map(y => {
    const avail = keys * y.days;
    const occRooms = avail * y.occupancyPct;
    const totalRev = y.rooms + y.fb + y.misc;
    return { avail, occRooms, totalRev };
  }), [years, keys]);

  const fmt$ = (v: number) => v ? `$${Math.round(v / 1000).toLocaleString('en-US')}` : '—';
  const fmtPct1 = (v: number) => Number.isFinite(v) ? `${(v * 100).toFixed(1)}%` : '—';
  const fmtNum = (v: number) => v ? v.toLocaleString('en-US') : '—';

  // PAR / POR / % Rev — operate in raw $.
  const par = (amount: number, avail: number) =>
    avail > 0 && amount > 0 ? `$${Math.round(amount / avail).toLocaleString('en-US')}` : '—';
  const por = (amount: number, occRooms: number) =>
    occRooms > 0 && amount > 0 ? `$${Math.round(amount / occRooms).toLocaleString('en-US')}` : '—';
  const pctRev = (amount: number, totalRev: number) =>
    totalRev > 0 && amount > 0 ? `${((amount / totalRev) * 100).toFixed(1)}%` : '—';

  // Single-value row (collapses across the 4 sub-columns of each year).
  const SpanRow = ({ label, render, idx }: {
    label: string;
    render: (y: HistYear, i: number) => string;
    idx: number;
  }) => (
    <tr className={cn(idx % 2 === 1 && 'bg-ink-300/5')}>
      <td className="sticky left-0 bg-inherit pl-3 pr-4 py-2 text-[12px] text-ink-700 whitespace-nowrap border-r border-border">
        {label}
      </td>
      {years.map((y, i) => (
        <td
          key={y.year}
          colSpan={colsPerYear}
          className="px-3 py-2 text-center text-[12px] tabular-nums text-ink-900 border-r border-border last:border-r-0"
        >
          {y.populated ? render(y, i) : '—'}
        </td>
      ))}
    </tr>
  );

  // Full row — Amount / %Rev / PAR / POR per year. Used for revenue,
  // expense, GOP, and NOI lines. ``get`` may return ``null`` for lines
  // the extractor didn't ship on a given year (renders em-dash even
  // when the column itself is populated).
  //
  // NOTE on expense sign convention: per the historical_baseline
  // engine, expenses ship as POSITIVE numbers (the engine subtracts
  // them in the GOP/NOI math). The ``fmt$`` formatter renders the
  // absolute magnitude — a negative value would surface as "—" via
  // the ``amount > 0`` guard, which is the right behavior for a sign
  // anomaly (caller should see an em-dash and dig into extraction).
  const FullRow = ({ label, get, idx }: {
    label: string;
    get: (y: HistYear) => number | null;
    idx: number;
  }) => (
    <tr className={cn(idx % 2 === 1 && 'bg-ink-300/5')}>
      <td className="sticky left-0 bg-inherit pl-3 pr-4 py-2 text-[12px] text-ink-700 whitespace-nowrap border-r border-border">
        {label}
      </td>
      {years.map((y, i) => {
        const raw = y.populated ? get(y) : 0;
        const amount = raw ?? 0;
        const cellPopulated = y.populated && raw != null;
        const d = derived[i];
        return (
          <Cells
            key={y.year}
            amount={amount}
            populated={cellPopulated}
            totalRev={d.totalRev}
            avail={d.avail}
            occRooms={d.occRooms}
            fmt$={fmt$}
            par={par}
            por={por}
            pctRev={pctRev}
          />
        );
      })}
    </tr>
  );

  return (
    <div className="rounded-lg border border-border overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          {/* Two-row header: HISTORICAL pill + sub-columns */}
          <thead>
            <tr className="bg-ink-100 border-b border-border">
              <th
                rowSpan={2}
                className="sticky left-0 bg-ink-100 text-left pl-3 pr-4 py-2 text-[10.5px] uppercase tracking-[0.08em] font-semibold text-ink-500 whitespace-nowrap border-r border-border"
              >
                $ in 000s
              </th>
              {years.map(y => (
                <th
                  key={y.year}
                  colSpan={colsPerYear}
                  className="px-3 py-1.5 text-center border-r border-border last:border-r-0"
                >
                  <div className="inline-flex items-center gap-1.5">
                    <span className="px-2 py-0.5 rounded-md bg-brand-50 text-brand-700 text-[9.5px] tracking-[0.1em] uppercase font-semibold">
                      Historical
                    </span>
                    <span className="text-[12px] font-semibold text-ink-900">{y.year}</span>
                  </div>
                </th>
              ))}
            </tr>
            <tr className="bg-ink-100/60 border-b border-border">
              {years.map(y => (
                <SubColumnHeaders key={y.year} />
              ))}
            </tr>
          </thead>

          <tbody className="bg-white">
            {/* Operating metrics */}
            <SpanRow label="Days" idx={0} render={(y) => fmtNum(y.days)} />
            <SpanRow label="Number of Rooms" idx={1} render={() => fmtNum(keys)} />
            <SpanRow label="Available Rooms" idx={2} render={(_, i) => fmtNum(derived[i].avail)} />
            <SpanRow
              label="Occupied Rooms"
              idx={3}
              render={(_, i) => fmtNum(Math.round(derived[i].occRooms))}
            />
            <SpanRow label="Occupancy" idx={4} render={(y) => fmtPct1(y.occupancyPct)} />
            <SpanRow
              label="Average Rate"
              idx={5}
              render={(y) => y.adr ? `$${y.adr.toFixed(0)}` : '—'}
            />
            <SpanRow
              label="Annual ADR Growth"
              idx={6}
              render={(y, i) => {
                if (i === 0) return 'N/A';
                const prev = years[i - 1].adr;
                if (!prev || !y.adr || !y.populated || !years[i - 1].populated) return '—';
                return fmtPct1(y.adr / prev - 1);
              }}
            />
            <SpanRow
              label="RevPAR"
              idx={7}
              render={(y) => y.revpar ? `$${y.revpar.toFixed(0)}` : '—'}
            />
            <SpanRow
              label="Annual RevPAR Growth"
              idx={8}
              render={(y, i) => {
                if (i === 0) return 'N/A';
                const prev = years[i - 1].revpar;
                if (!prev || !y.revpar || !y.populated || !years[i - 1].populated) return '—';
                return fmtPct1(y.revpar / prev - 1);
              }}
            />

            {/* REVENUES band */}
            <tr>
              <td
                colSpan={1 + years.length * colsPerYear}
                className="bg-brand-50 border-y border-brand-100 px-3 py-1.5 text-[10.5px] uppercase tracking-[0.1em] font-semibold text-brand-700"
              >
                Revenues
              </td>
            </tr>
            <FullRow label="Rooms" idx={10} get={(y) => y.rooms} />
            <FullRow label="Food & Beverage" idx={11} get={(y) => y.fb} />
            <FullRow label="Misc. Income" idx={12} get={(y) => y.misc} />

            {/* DEPARTMENTAL EXPENSES band — engine-canonical slugs
                ``rooms_dept_expense`` / ``fb_dept_expense`` /
                ``other_dept_expense`` from historical_baseline.py. */}
            <tr>
              <td
                colSpan={1 + years.length * colsPerYear}
                className="bg-brand-50 border-y border-brand-100 px-3 py-1.5 text-[10.5px] uppercase tracking-[0.1em] font-semibold text-brand-700"
              >
                Departmental Expenses
              </td>
            </tr>
            <FullRow label="Rooms Expense" idx={14} get={(y) => y.rooms_dept_expense} />
            <FullRow label="Food & Beverage Expense" idx={15} get={(y) => y.fb_dept_expense} />
            <FullRow label="Other Operated Expense" idx={16} get={(y) => y.other_dept_expense} />

            {/* GROSS OPERATING PROFIT band — Undistributed rollup + GOP.
                Engine slugs: ``undistributed`` (A&G + sales/mkt +
                utilities + prop_ops + IT) and ``gop``. */}
            <tr>
              <td
                colSpan={1 + years.length * colsPerYear}
                className="bg-brand-50 border-y border-brand-100 px-3 py-1.5 text-[10.5px] uppercase tracking-[0.1em] font-semibold text-brand-700"
              >
                Gross Operating Profit
              </td>
            </tr>
            <FullRow label="Undistributed Expenses" idx={18} get={(y) => y.undistributed} />
            <FullRow label="GOP" idx={19} get={(y) => y.gop} />

            {/* NET OPERATING INCOME band — Fixed (property tax +
                insurance + mgmt fee) and NOI. Engine slugs:
                ``fixed_expenses`` and ``noi``. */}
            <tr>
              <td
                colSpan={1 + years.length * colsPerYear}
                className="bg-brand-50 border-y border-brand-100 px-3 py-1.5 text-[10.5px] uppercase tracking-[0.1em] font-semibold text-brand-700"
              >
                Net Operating Income
              </td>
            </tr>
            <FullRow label="Fixed Expenses" idx={21} get={(y) => y.fixed_expenses} />
            <FullRow label="NOI" idx={22} get={(y) => y.noi} />

            {/* Empty-state overlay row */}
            {!hasAnyData && (
              <tr>
                <td
                  colSpan={1 + years.length * colsPerYear}
                  className="px-4 py-8 text-center bg-surface"
                >
                  <div className="text-[12.5px] text-ink-700 max-w-2xl mx-auto leading-relaxed">
                    Trailing twelve months from the uploaded T-12 anchors a single
                    historical column. Upload prior-period operating statements to
                    back-fill T-3 trend.
                  </div>
                  <div className="mt-3">
                    <Badge tone="gray" uppercase>
                      {loading ? 'Loading…' : 'Coming with the next deploy'}
                    </Badge>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SubColumnHeaders() {
  return (
    <>
      {(['Amount', '% Rev', 'PAR', 'POR'] as const).map((h, idx, arr) => (
        <th
          key={h}
          className={cn(
            'px-2 py-1.5 text-[10px] uppercase tracking-[0.08em] font-semibold text-ink-500 text-right',
            idx === arr.length - 1 ? 'border-r border-border' : 'border-r border-border/60',
          )}
        >
          {h}
        </th>
      ))}
    </>
  );
}

function Cells({
  amount,
  populated,
  totalRev,
  avail,
  occRooms,
  fmt$,
  par,
  por,
  pctRev,
}: {
  amount: number;
  populated: boolean;
  totalRev: number;
  avail: number;
  occRooms: number;
  fmt$: (v: number) => string;
  par: (amount: number, avail: number) => string;
  por: (amount: number, occRooms: number) => string;
  pctRev: (amount: number, totalRev: number) => string;
}) {
  const cell = 'px-2 py-2 text-right text-[12px] tabular-nums text-ink-900';
  if (!populated) {
    return (
      <>
        <td className={cn(cell, 'border-r border-border/60 text-ink-400')}>—</td>
        <td className={cn(cell, 'border-r border-border/60 text-ink-400')}>—</td>
        <td className={cn(cell, 'border-r border-border/60 text-ink-400')}>—</td>
        <td className={cn(cell, 'border-r border-border text-ink-400')}>—</td>
      </>
    );
  }
  return (
    <>
      <td className={cn(cell, 'border-r border-border/60')}>{fmt$(amount)}</td>
      <td className={cn(cell, 'border-r border-border/60 text-ink-700')}>
        {pctRev(amount, totalRev)}
      </td>
      <td className={cn(cell, 'border-r border-border/60 text-ink-700')}>
        {par(amount, avail)}
      </td>
      <td className={cn(cell, 'border-r border-border text-ink-700')}>
        {por(amount, occRooms)}
      </td>
    </>
  );
}
