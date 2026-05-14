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
 *   3. Static Kimpton mock for the demo deal (id=7) — covers 2021-2025.
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
import { kimptonAnglerOverview } from '@/lib/mockData';

// ─────────────────────────── Data shape ───────────────────────────
// One historical year column. ``amount`` for Rooms / F&B / Misc are in
// raw dollars (not thousands) so the display layer handles the /1000
// scaling consistently with PAR / POR math.
interface HistYear {
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
  /** ``true`` when all numeric series are present; ``false`` for placeholder/empty columns. */
  populated: boolean;
}

interface HistData {
  keys: number;
  years: HistYear[];
}

// ─────────────────────────── Mock fallback ───────────────────────────
// Kimpton Angler — 2021-2025. Numbers are illustrative but internally
// consistent (RevPAR = ADR × occupancy, total rev ≈ rooms / (1 - 0.27)).
const KIMPTON_HISTORICAL: HistData = {
  keys: kimptonAnglerOverview.general.keys, // 132
  years: [
    { year: '2021', days: 365, occupancyPct: 0.581, adr: 245, revpar: 142,
      rooms: 6_854_000, fb: 1_998_000, misc: 444_000, populated: true },
    { year: '2022', days: 365, occupancyPct: 0.681, adr: 271, revpar: 184,
      rooms: 8_870_000, fb: 2_586_000, misc: 575_000, populated: true },
    { year: '2023', days: 365, occupancyPct: 0.715, adr: 287, revpar: 205,
      rooms: 9_881_000, fb: 2_881_000, misc: 640_000, populated: true },
    { year: '2024', days: 366, occupancyPct: 0.738, adr: 294, revpar: 217,
      rooms: 10_493_000, fb: 3_059_000, misc: 680_000, populated: true },
    { year: '2025', days: 365, occupancyPct: 0.762, adr: 312, revpar: 238,
      rooms: 11_472_000, fb: 3_344_000, misc: 743_000, populated: true },
  ],
};

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
 * Match an extracted field by alias. The extractor emits fields under
 * dotted USALI paths (``p_and_l_usali.operating_revenue.rooms_revenue``)
 * and with unit suffixes (``adr_usd``, ``occupancy_pct``). The old
 * exact-normalized-match only caught bare names like ``occupancy_pct``,
 * which is why the Historicals T-12 column showed Occupancy but blanked
 * ADR / RevPAR / every revenue line (Sam QA 2026-05-14 #1).
 *
 * Matching strategy — for each field, try the full normalized name,
 * the last dotted segment, and both with the unit suffix stripped.
 */
function findField(fields: ExtractionField[], aliases: string[]): ExtractionField | undefined {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const stripUnit = (s: string) => s.replace(/(usd|pct|percent|ratio|amount)$/i, '');
  const aSet = new Set<string>();
  for (const a of aliases) {
    const n = norm(a);
    aSet.add(n);
    aSet.add(stripUnit(n));
  }
  for (const f of fields) {
    const full = norm(f.field_name);
    const segs = f.field_name.split('.');
    const last = norm(segs[segs.length - 1] ?? '');
    for (const cand of [full, stripUnit(full), last, stripUnit(last)]) {
      if (cand && aSet.has(cand)) return f;
    }
  }
  return undefined;
}

/**
 * Drop forward-looking fields before any historical building.
 *
 * Sam QA 2026-05-14: the T-12 doc carries BOTH actuals
 * (``p_and_l_usali.period_ending`` = 2025-05-31) AND a forecast block
 * (``forecast.period_ending`` = 2025-12-31). ``findField`` matches on
 * the last dotted segment, so ``forecast.period_ending`` shadowed the
 * real period_ending — the T-12 doc got mislabeled "2025" (December)
 * instead of "T-12", and its data landed in the wrong column with the
 * real T-12 column left blank. The Historicals tab is actuals-only;
 * strip anything under a forecast/projection/budget namespace.
 */
function actualsOnly(fields: ExtractionField[]): ExtractionField[] {
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
      n.includes('.budget.')
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
function deriveYearLabel(
  fields: ExtractionField[],
  filename: string,
  docType: string | null | undefined,
): string {
  // Resolution order — most authoritative first. The extractor is the
  // format-agnostic layer (it now emits period metadata for any P&L
  // layout); the filename is only a last-resort safety net.
  const strVal = (f: ExtractionField | undefined): string | null =>
    f && typeof f.value === 'string' ? f.value : null;

  const dt = (docType ?? '').toUpperCase();
  const isT12Type = dt === 'T12' || dt === 'T-12' || dt.includes('T12');

  const periodEnding = strVal(findField(fields, [
    'period_ending', 'p_and_l_usali.period_ending',
    'period_end', 'statement_period_end',
  ]));
  const periodType = strVal(findField(fields, [
    'period_type', 'p_and_l_usali.period_type',
  ]));
  const periodLabel = strVal(findField(fields, [
    'period_label', 'p_and_l_usali.period_label',
  ]));

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

  // 3b. doc_type guard — a T12-classified doc is a trailing-twelve by
  //     definition. With no period metadata, it MUST land in the
  //     "T-12" column, never a calendar year pulled from the filename
  //     (which often carries the period-END year, e.g.
  //     "May 2025 Financials" → a T-12 ending May 2025, NOT FY2025).
  if (isT12Type) return 'T-12';

  // 4. filename — last resort for ANNUAL P&L docs only, e.g.
  //    "Angler's 2023 P&L.xlsx" → 2023.
  const fnYear = filename.match(/(20\d{2})/);
  if (fnYear) return fnYear[1];

  // 5. nothing usable — default to the T-12 slot.
  return 'T-12';
}

/**
 * Build one historical-year column from a P&L / T-12 extraction.
 * ``yearLabel`` comes from ``deriveYearLabel``; ``days`` is 365 for a
 * T-12 and a real day count for an annual column.
 */
function buildHistYear(
  fields: ExtractionField[],
  keys: number,
  yearLabel: string,
): HistYear | null {
  if (!fields.length) return null;

  const occ = num(findField(fields, ['occupancy', 'occupancy_pct', 'occ', 't12_occupancy']));
  const adr = num(findField(fields, ['adr', 'adr_usd', 'average_daily_rate', 't12_adr']));
  const revpar = num(findField(fields, ['revpar', 'revpar_usd', 't12_revpar']));
  const rooms = num(findField(fields, [
    'rooms_revenue', 'room_revenue', 'total_rooms_revenue', 't12_rooms_revenue',
  ]));
  const fb = num(findField(fields, [
    'fb_revenue', 'food_beverage_revenue', 'fnb_revenue', 'food_beverage',
  ]));
  const misc = num(findField(fields, [
    'other_revenue', 'misc_revenue', 'misc_income', 'miscellaneous_income',
    'other_operated_revenue',
  ]));

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
    populated: true,
  };
}

// Default 5-year window when nothing is extractable yet — used as the
// scaffold for the "empty" rendering so reviewers still see column
// headers and row labels.
function emptyFiveYearSkeleton(): HistData {
  const thisYear = new Date().getFullYear();
  const years: HistYear[] = [];
  for (let i = 4; i >= 0; i--) {
    const y = thisYear - 1 - i; // last fully-closed year and back
    years.push({
      year: String(y),
      days: y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0) ? 366 : 365,
      occupancyPct: 0, adr: 0, revpar: 0,
      rooms: 0, fb: 0, misc: 0,
      populated: false,
    });
  }
  return { keys: 0, years };
}

// ─────────────────────────── Component ───────────────────────────
export default function HistoricalsSection({
  dealId,
  isKimptonDemo,
}: {
  dealId: string;
  isKimptonDemo: boolean;
}) {
  const { toast } = useToast();
  const { deal } = useDeal(dealId);

  const isMockId = /^\d+$/.test(dealId);
  const liveMode = isWorkerConnected() && !!dealId && !isMockId;

  const [data, setData] = useState<HistData | null>(null);
  const [loading, setLoading] = useState(false);

  // 1) demo deal short-circuits to the static mock.
  // 2) live deal: try /deals/{id}/historicals (graceful 404), then T-12.
  // 3) otherwise: render the empty 5-year skeleton.
  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (isKimptonDemo) {
        setData(KIMPTON_HISTORICAL);
        return;
      }
      if (!liveMode) {
        setData(emptyFiveYearSkeleton());
        return;
      }
      setLoading(true);
      try {
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
              );
              const built = buildHistYear(fields, keysForBuild, label);
              if (built) byYear.set(label, built);
            } catch {
              // skip this doc — others may still populate.
            }
          }

          if (byYear.size > 0 && !cancelled) {
            // Lay out columns: up to 4 calendar-year columns
            // (ascending), then a T-12 column on the far right.
            const t12 = byYear.get('T-12') ?? null;
            const annualYears = [...byYear.keys()]
              .filter(y => /^\d{4}$/.test(y))
              .sort();
            // Skeleton fills any gaps so the table always shows a
            // consistent column count with placeholders.
            const skel = emptyFiveYearSkeleton();
            const skelAnnual = skel.years.slice(0, -1); // 4 placeholder cols

            // Merge: prefer real annual data, else skeleton placeholder.
            const annualCols: HistYear[] = [];
            const realByYear = new Map(annualYears.map(y => [y, byYear.get(y)!]));
            // Use the union of skeleton years + any real annual years,
            // keeping the most recent 4.
            const allAnnualLabels = new Set<string>([
              ...skelAnnual.map(y => y.year),
              ...annualYears,
            ]);
            const orderedAnnual = [...allAnnualLabels].sort().slice(-4);
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
  }, [dealId, isKimptonDemo, liveMode, deal?.keys]);

  const onNotes = () => {
    toast('Historicals notes — coming with the next deploy', { type: 'info' });
  };

  const onExport = () => {
    if (!data) return;
    const headers = ['Metric', ...data.years.flatMap(y => [
      `${y.year} Amount`, `${y.year} % Rev`, `${y.year} PAR`, `${y.year} POR`,
    ])];
    const rows: string[][] = [];
    const keys = data.keys;
    const fmtRow = (label: string, get: (y: HistYear) => number) => {
      const row = [label];
      for (const y of data.years) {
        const v = get(y);
        const totalRev = y.rooms + y.fb + y.misc;
        const avail = keys * y.days;
        const occRooms = avail * y.occupancyPct;
        row.push(
          v ? v.toFixed(0) : '',
          totalRev ? ((v / totalRev) * 100).toFixed(1) : '',
          avail ? ((v / avail)).toFixed(2) : '',
          occRooms ? ((v / occRooms)).toFixed(2) : '',
        );
      }
      rows.push(row);
    };
    fmtRow('Rooms', y => y.rooms);
    fmtRow('Food & Beverage', y => y.fb);
    fmtRow('Misc. Income', y => y.misc);
    const tsv = [headers, ...rows].map(r => r.join('\t')).join('\n');
    const blob = new Blob([tsv], { type: 'text/tab-separated-values' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `historicals-${dealId || 'deal'}.tsv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const keys = isKimptonDemo
    ? kimptonAnglerOverview.general.keys
    : (deal?.keys ?? data?.keys ?? 0);

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

  // Full row — Amount / %Rev / PAR / POR per year. Used for revenue lines.
  const FullRow = ({ label, get, idx }: {
    label: string;
    get: (y: HistYear) => number;
    idx: number;
  }) => (
    <tr className={cn(idx % 2 === 1 && 'bg-ink-300/5')}>
      <td className="sticky left-0 bg-inherit pl-3 pr-4 py-2 text-[12px] text-ink-700 whitespace-nowrap border-r border-border">
        {label}
      </td>
      {years.map((y, i) => {
        const amount = y.populated ? get(y) : 0;
        const d = derived[i];
        return (
          <Cells
            key={y.year}
            amount={amount}
            populated={y.populated}
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
