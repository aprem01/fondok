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

function findField(fields: ExtractionField[], aliases: string[]): ExtractionField | undefined {
  const lc = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const aSet = new Set(aliases.map(lc));
  return fields.find(f => aSet.has(lc(f.field_name)));
}

function buildT12Year(fields: ExtractionField[], keys: number): HistYear | null {
  if (!fields.length) return null;

  const occ = num(findField(fields, ['occupancy', 'occupancy_pct', 'occ', 't12_occupancy']));
  const adr = num(findField(fields, ['adr', 'average_daily_rate', 't12_adr']));
  const revpar = num(findField(fields, ['revpar', 't12_revpar']));
  const rooms = num(findField(fields, [
    'rooms_revenue', 'room_revenue', 'total_rooms_revenue', 't12_rooms_revenue',
  ]));
  const fb = num(findField(fields, ['fb_revenue', 'food_beverage_revenue', 'fnb_revenue']));
  const misc = num(findField(fields, [
    'other_revenue', 'misc_revenue', 'misc_income', 'other_operated_revenue',
  ]));

  // Need at least one of {rooms, occupancy} to render anything meaningful.
  if (rooms == null && occ == null && adr == null) return null;

  // Occupancy may have come in as a percent (e.g. 73.8) — normalize.
  const occNorm = occ == null ? 0 : occ > 1.5 ? occ / 100 : occ;
  const adrNorm = adr ?? 0;
  const revparNorm = revpar ?? (adrNorm * occNorm);
  const roomsNorm = rooms ?? (keys > 0 ? revparNorm * keys * 365 : 0);

  return {
    year: 'T-12',
    days: 365,
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

      // T-12 fallback: pick the most recent EXTRACTED T-12 doc on the deal.
      try {
        const docs = await api.documents.list(String(dealId)) as WorkerDocument[];
        const t12 = (docs ?? [])
          .filter(d => (d.doc_type ?? '').toUpperCase().includes('T12') ||
                       (d.doc_type ?? '').toLowerCase() === 't-12')
          .filter(d => d.status === 'EXTRACTED')
          .sort((a, b) => (b.uploaded_at ?? '').localeCompare(a.uploaded_at ?? ''))[0];
        if (t12 && deal?.keys) {
          const ext = await api.documents.extraction(String(dealId), t12.id);
          const t12Year = buildT12Year(ext.fields ?? [], deal.keys);
          if (t12Year && !cancelled) {
            // Pad earlier years as placeholders so the table still
            // renders 5 columns with T-12 in the rightmost slot.
            const skel = emptyFiveYearSkeleton();
            const merged: HistData = {
              keys: deal.keys,
              years: [...skel.years.slice(0, -1), t12Year],
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
