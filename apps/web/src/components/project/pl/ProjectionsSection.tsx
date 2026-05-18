'use client';
/**
 * ProjectionsSection — Lovable-parity multi-year proforma projections.
 *
 * Renders the "PRELIMINARY HOTEL UNDERWRITING / Proforma Projections"
 * table with a Base Year + 5 forecast year span. Each year column
 * shows Amount / % Rev / PAR / POR sub-columns. Rows include hotel
 * delivery, days, room counts, occupancy, ADR, RevPAR + RevPAR growth,
 * and the REVENUES section (Rooms / F&B / Other / Total).
 *
 * Sources:
 *  - Worker: ``revenue.years`` + ``fb.years`` + ``expense.years`` via
 *    ``useEngineOutputs``. Year 0 (Base) = first engine year, treated
 *    as the T-12 anchor; Years 1-5 = engine years[0..4].
 *  - Kimpton demo (id=7): ``kimptonAnglerOverview.proforma`` mock.
 *
 * Helpers (mirroring Historicals):
 *   PAR  = Amount / Available Rooms × 1000
 *   POR  = Amount / Occupied Rooms  × 1000
 *   %Rev = Amount / Total Revenue   × 100
 */

import { useMemo, useState } from 'react';
import { Sparkles, Download, FileText, ExternalLink } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import Modal from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { kimptonAnglerOverview } from '@/lib/mockData';
import {
  api,
  isWorkerConnected,
  workerUrl,
  WorkerError,
  type AskAnswerResult,
} from '@/lib/api';
import { downloadXlsx, type XlsxCell } from '@/lib/exportXlsx';

// ────────────────────────────────────────────────────────────────────
// Worker output shapes — mirror PLTab.tsx (kept local so this file
// stays self-contained in the new pl/ subdirectory).
// ────────────────────────────────────────────────────────────────────
interface RevenueYearWorker {
  year: number;
  occupancy: number;
  adr: number;
  revpar: number;
  rooms_revenue: number;
  fb_revenue: number;
  other_revenue: number;
  total_revenue: number;
}

interface FBYearWorker {
  year: number;
  rooms_revenue: number;
  fb_revenue: number;
  resort_fees?: number;
  other_revenue: number;
  total_revenue: number;
}

interface ExpenseYearWorker {
  year: number;
  total_revenue: number;
}

// One year of normalized projection inputs — what the table renders.
interface ProjYear {
  year: number;
  // Available Rooms = keys × days (in days for the year).
  days: number;
  rooms: number;
  availableRooms: number;
  occupiedRooms: number;
  occupancy: number;     // 0..1
  adr: number;           // dollars
  revpar: number;        // dollars
  roomsRevenue: number;  // dollars
  fbRevenue: number;     // dollars
  miscRevenue: number;   // dollars (Other Operating)
  totalRevenue: number;  // dollars
}

const isLeap = (y: number) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;

export default function ProjectionsSection({
  dealId,
  isKimptonDemo,
}: {
  dealId: string;
  isKimptonDemo: boolean;
}) {
  const { toast } = useToast();
  const { outputs } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);

  // Resolve key count: Kimpton mock or real deal.keys; default 0 until known.
  const keys = isKimptonDemo
    ? kimptonAnglerOverview.general.keys
    : (deal?.keys && deal.keys > 0 ? deal.keys : 0);

  // Pull engine years.
  const revenueYears = getEngineField<RevenueYearWorker[]>(outputs, 'revenue', 'years');
  const fbYears = getEngineField<FBYearWorker[]>(outputs, 'fb', 'years');
  const expenseYears = getEngineField<ExpenseYearWorker[]>(outputs, 'expense', 'years');
  const hasWorker =
    Array.isArray(revenueYears) && revenueYears.length > 0 &&
    Array.isArray(expenseYears) && expenseYears.length > 0;

  const years = useMemo<ProjYear[] | null>(() => {
    if (hasWorker && keys > 0) {
      return buildFromWorker(revenueYears!, fbYears ?? null, expenseYears!, keys);
    }
    if (isKimptonDemo) {
      return buildFromKimpton();
    }
    return null;
  }, [hasWorker, revenueYears, fbYears, expenseYears, keys, isKimptonDemo]);

  // ── AI NOI Summary modal ────────────────────────────────────────
  // Hits the worker's grounded Q&A endpoint (`/deals/{id}/ask`),
  // which returns answer + per-fact citations back to source PDF
  // pages. The fixed prompt frames the question around the projection
  // years rendered in this table — keeps the answer on-topic for the
  // P&L tab without dragging in unrelated assumptions.
  //
  // CRITICAL: these hooks MUST be declared BEFORE the early-return
  // empty-state guard below. React's Rules of Hooks require the same
  // hook count on every render; placing them after the guard caused
  // React error #310 when `years` flipped from null → populated
  // between renders (2026-05-12 prod crash on the P&L tab).
  const [noiModalOpen, setNoiModalOpen] = useState(false);
  const [noiLoading, setNoiLoading] = useState(false);
  const [noiResult, setNoiResult] = useState<AskAnswerResult | null>(null);
  const [noiError, setNoiError] = useState<string | null>(null);

  const noiQuestion = useMemo(() => {
    if (!years || years.length === 0) {
      return 'Summarize the deal NOI trajectory across the projection horizon.';
    }
    const span = years.length - 1;
    const baseYear = years[0]?.year;
    const exitYear = years[years.length - 1]?.year;
    const revSeries = years
      .map(
        (y) =>
          `Year ${y.year}: Occ ${(y.occupancy * 100).toFixed(1)}%, ADR $${y.adr.toFixed(0)}, Rev $${y.totalRevenue.toLocaleString()}`,
      )
      .join('; ');
    return [
      `Summarize the NOI trajectory across this ${span}-year projection`,
      `(${baseYear} → ${exitYear}). Underlying revenue series: ${revSeries}.`,
      "Cover: (1) what's driving Year-1 NOI vs the broker proforma,",
      '(2) the key revenue / expense levers in the ramp years,',
      '(3) the terminal Year NOI vs entry, and (4) the top two risks',
      'that would compress NOI below this trajectory. Cite source pages',
      'when grounded.',
    ].join(' ');
  }, [years]);

  if (!years || years.length === 0) {
    return (
      <Card className="p-12 text-center">
        <Sparkles size={22} className="mx-auto text-brand-500 mb-3" />
        <div className="text-[14px] font-semibold text-ink-900">
          No projections yet
        </div>
        <p className="text-[12.5px] text-ink-500 mt-1.5 max-w-md mx-auto leading-relaxed">
          Upload a T-12 or P&amp;L to populate projections — engines run automatically once extraction completes.
        </p>
      </Card>
    );
  }

  const onNoiSummary = async () => {
    if (!isWorkerConnected() || !dealId || /^\d+$/.test(dealId)) {
      toast('AI NOI Summary needs a live deal — try the demo deal.', {
        type: 'info',
      });
      return;
    }
    setNoiModalOpen(true);
    setNoiLoading(true);
    setNoiError(null);
    setNoiResult(null);
    try {
      const res = await api.dossier.ask(dealId, noiQuestion);
      setNoiResult(res);
    } catch (err) {
      const detail = err instanceof WorkerError ? err.body : String(err);
      setNoiError(detail || 'Worker rejected the request.');
    } finally {
      setNoiLoading(false);
    }
  };

  const onExport = async () => {
    const headers: XlsxCell[] = [
      'Metric',
      ...years.flatMap((y, i) => {
        const label = i === 0 ? `Base Year ${y.year}` : `Year ${i} ${y.year}`;
        return [
          `${label} Amount`,
          `${label} % Rev`,
          `${label} PAR`,
          `${label} POR`,
        ];
      }),
    ];
    const rows: XlsxCell[][] = [headers];
    const trMap = years.map(y => y.totalRevenue);
    const arMap = years.map(y => y.availableRooms);
    const orMap = years.map(y => y.occupiedRooms);
    const expand = (label: string, vals: number[], asPct = false) => {
      const cells: XlsxCell[] = [label];
      vals.forEach((v, i) => {
        // Keep numerics as numbers so Excel can re-sum / re-format. The
        // % Rev / PAR / POR columns stay blank when the row itself is a
        // percentage (e.g. Occupancy) — those derivations don't apply.
        const amount = asPct
          ? Number((v * 100).toFixed(1))
          : Number(v.toFixed(0));
        const pctRev = trMap[i] > 0 && !asPct
          ? Number(((v / trMap[i]) * 100).toFixed(1))
          : '';
        const par = arMap[i] > 0 && !asPct
          ? Number(((v / arMap[i]) * 1000).toFixed(2))
          : '';
        const por = orMap[i] > 0 && !asPct
          ? Number(((v / orMap[i]) * 1000).toFixed(2))
          : '';
        cells.push(amount, pctRev, par, por);
      });
      rows.push(cells);
    };
    rows.push(['Days', ...years.flatMap(y => [y.days, '', '', '']) as XlsxCell[]]);
    rows.push(['Number of Rooms', ...years.flatMap(y => [y.rooms, '', '', '']) as XlsxCell[]]);
    rows.push(['Available Rooms', ...years.flatMap(y => [y.availableRooms, '', '', '']) as XlsxCell[]]);
    rows.push(['Occupied Rooms', ...years.flatMap(y => [y.occupiedRooms, '', '', '']) as XlsxCell[]]);
    expand('Occupancy', years.map(y => y.occupancy), true);
    expand('Average Rate', years.map(y => y.adr));
    expand('RevPAR', years.map(y => y.revpar));
    expand('Rooms', years.map(y => y.roomsRevenue));
    expand('Food & Beverage', years.map(y => y.fbRevenue));
    expand('Misc. Income', years.map(y => y.miscRevenue));
    expand('Total Revenue', years.map(y => y.totalRevenue));

    await downloadXlsx(`projections-${dealId || 'deal'}`, [
      { name: 'Projections', rows },
    ]);
    toast('Projections exported', { type: 'success' });
  };

  return (
    <>
      <Card className="p-0 overflow-hidden">
        {/* Header */}
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border bg-bg/40">
          <div>
            <div className="text-[10.5px] uppercase tracking-[0.12em] text-ink-500 font-semibold">
              Preliminary Hotel Underwriting
            </div>
            <h3 className="text-[15px] font-semibold text-ink-900 mt-0.5">
              Proforma Projections
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="secondary" size="sm" onClick={onNoiSummary}>
              <Sparkles size={11} />
              NOI Summary
              <Badge tone="blue" className="ml-1 !py-0 !px-1.5 !text-[9px]">AI</Badge>
            </Button>
            <Button variant="secondary" size="sm" onClick={onExport}>
              <Download size={11} /> Export
            </Button>
          </div>
        </div>

        <ProjectionsTable years={years} />
      </Card>

      <Modal
        open={noiModalOpen}
        onClose={() => setNoiModalOpen(false)}
        title="AI NOI Summary"
        maxWidth="max-w-2xl"
      >
        <div className="px-5 py-4 space-y-3">
          {noiLoading && (
            <div className="text-[12.5px] text-ink-500 py-6 text-center">
              <Sparkles className="inline-block w-3.5 h-3.5 mr-1.5 animate-pulse text-brand-500" />
              Synthesizing NOI summary from extracted deal data…
            </div>
          )}

          {noiError && (
            <div className="text-[12.5px] text-error-700 bg-error-50 border border-error-200 rounded p-3">
              <div className="font-semibold mb-1">Couldn&apos;t generate summary</div>
              <div className="text-error-600">{noiError}</div>
            </div>
          )}

          {noiResult && !noiLoading && (
            <>
              <div className="text-[12px] text-ink-500 leading-relaxed border-l-2 border-brand-200 pl-3 italic">
                {noiQuestion}
              </div>
              <div className="text-[13px] text-ink-900 leading-relaxed whitespace-pre-wrap">
                {noiResult.answer}
              </div>
              {noiResult.confidence != null && (
                <div className="text-[11px] text-ink-500">
                  Model confidence: {(noiResult.confidence * 100).toFixed(0)}%
                  {noiResult.note && ` · ${noiResult.note}`}
                </div>
              )}
              {noiResult.citations && noiResult.citations.length > 0 && (
                <div className="border-t border-border pt-3">
                  <div className="text-[10.5px] uppercase tracking-wide text-ink-500 font-semibold mb-2">
                    Citations
                  </div>
                  <ul className="space-y-1.5 text-[11.5px] text-ink-700">
                    {noiResult.citations.map((c, i) => {
                      const href =
                        c.document_id && c.page
                          ? `${workerUrl()}/deals/${dealId}/documents/${c.document_id}/download#page=${c.page}`
                          : null;
                      return (
                        <li key={`citation-${i}`} className="flex items-start gap-1.5">
                          <FileText size={11} className="mt-0.5 text-ink-400 shrink-0" />
                          <span>
                            {href ? (
                              <a
                                href={href}
                                target="_blank"
                                rel="noreferrer"
                                className="hover:underline inline-flex items-center gap-0.5"
                              >
                                {c.field ?? 'source'} (page {c.page})
                                <ExternalLink size={10} />
                              </a>
                            ) : (
                              <span className="text-ink-500">
                                {c.field ?? 'source'}
                              </span>
                            )}
                            {c.excerpt && (
                              <span className="block text-[11px] text-ink-500 italic mt-0.5">
                                &ldquo;{c.excerpt.slice(0, 200)}
                                {c.excerpt.length > 200 ? '…' : ''}&rdquo;
                              </span>
                            )}
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </>
          )}
        </div>
      </Modal>
    </>
  );
}

// ────────────────────────────────────────────────────────────────────
// Builders
// ────────────────────────────────────────────────────────────────────

function buildFromWorker(
  revenueYears: RevenueYearWorker[],
  fbYears: FBYearWorker[] | null,
  expenseYears: ExpenseYearWorker[],
  keys: number,
): ProjYear[] {
  // Slice up to first 6 entries; if worker only emits 5 forecast years
  // without a base year, we render whatever we have anchored on Y0.
  const span = Math.min(6, revenueYears.length);
  const out: ProjYear[] = [];
  for (let i = 0; i < span; i++) {
    const r = revenueYears[i];
    const f = fbYears?.[i];
    const e = expenseYears[i];
    const days = isLeap(r.year) ? 366 : 365;
    const availableRooms = keys * days;
    const occupiedRooms = Math.round(availableRooms * (r.occupancy ?? 0));
    const totalRevenue = e?.total_revenue ?? r.total_revenue;
    out.push({
      year: r.year,
      days,
      rooms: keys,
      availableRooms,
      occupiedRooms,
      occupancy: r.occupancy ?? 0,
      adr: r.adr ?? 0,
      revpar: r.revpar ?? 0,
      roomsRevenue: f?.rooms_revenue ?? r.rooms_revenue ?? 0,
      fbRevenue: f?.fb_revenue ?? r.fb_revenue ?? 0,
      miscRevenue: (f?.other_revenue ?? r.other_revenue ?? 0) + (f?.resort_fees ?? 0),
      totalRevenue,
    });
  }
  return out;
}

// Kimpton mock: synthesize a Base Year (T-12 anchor) + 5 projection
// years from the proforma block. Numbers in proforma are $000s, so
// we multiply by 1000 to get dollars in the table.
function buildFromKimpton(): ProjYear[] {
  const keys = kimptonAnglerOverview.general.keys; // 132
  const p = kimptonAnglerOverview.proforma;
  const get = (label: string) => p.find(r => r.label === label)!;
  const room = get('Room Revenue');
  const fb = get('F&B Revenue');
  const other = get('Other Revenue');
  const totalRev = get('Total Revenue');

  // Base Year (2025) — derived as Y1 / 1.05 to back into a T-12 anchor.
  const baseYear = 2025;
  const baseScale = 1 / 1.05;
  const baseOcc = 0.701;
  // Forecast years (2026..2030) follow the existing Kimpton occupancy ramp.
  const occRamp = [0.701, 0.738, 0.762, 0.776, 0.787];
  const yearsArr = [baseYear, 2026, 2027, 2028, 2029, 2030];

  return yearsArr.map((y, i) => {
    const isBase = i === 0;
    const days = isLeap(y) ? 366 : 365;
    const availableRooms = keys * days;
    const occ = isBase ? baseOcc * baseScale * 1.05 : occRamp[i - 1]; // ≈ baseOcc for i=0
    const occupiedRooms = Math.round(availableRooms * occ);

    const ykey = (['y1', 'y2', 'y3', 'y4', 'y5'] as const)[Math.max(0, i - 1)];
    const scale = isBase ? baseScale : 1;
    const rooms$ = (room[ykey] * 1000) * scale;
    const fb$ = (fb[ykey] * 1000) * scale;
    const other$ = (other[ykey] * 1000) * scale;
    const total$ = (totalRev[ykey] * 1000) * scale;
    const adr = occupiedRooms > 0 ? rooms$ / occupiedRooms : 0;
    const revpar = availableRooms > 0 ? rooms$ / availableRooms : 0;

    return {
      year: y,
      days,
      rooms: keys,
      availableRooms,
      occupiedRooms,
      occupancy: occ,
      adr,
      revpar,
      roomsRevenue: rooms$,
      fbRevenue: fb$,
      miscRevenue: other$,
      totalRevenue: total$,
    };
  });
}

// ────────────────────────────────────────────────────────────────────
// Table
// ────────────────────────────────────────────────────────────────────

function ProjectionsTable({ years }: { years: ProjYear[] }) {
  // Hotel Delivery — render the base year period-end as the anchor date.
  const baseYear = years[0]?.year ?? new Date().getFullYear();
  const hotelDelivery = `9/30/${baseYear}`;

  // Annual RevPAR growth — Y0 N/A, Y1+ vs prior.
  const revparGrowth = years.map((y, i) => {
    if (i === 0) return null;
    const prev = years[i - 1].revpar;
    return prev > 0 ? (y.revpar - prev) / prev : null;
  });

  // Helpers for sub-columns.
  const fmtAmount = (v: number, opts?: { decimals?: number; prefix?: string }) => {
    const decimals = opts?.decimals ?? 0;
    const prefix = opts?.prefix ?? '';
    return `${prefix}${v.toLocaleString('en-US', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    })}`;
  };
  const par = (v: number, available: number) =>
    available > 0 ? (v / available) * 1000 : 0;
  const por = (v: number, occupied: number) =>
    occupied > 0 ? (v / occupied) * 1000 : 0;
  const pctRev = (v: number, total: number) =>
    total > 0 ? (v / total) * 100 : 0;

  // Index column helper labels (growth-rate references shown for Year 1).
  const indexLabel = (key: string): string => {
    if (key === 'occupancy') return `${(years[0].occupancy * 100).toFixed(1)}%`;
    if (key === 'adr') return 'Growth';
    if (key === 'revpar') return '';
    if (key === 'rooms') return 'Mkt';
    if (key === 'fb') return 'Mkt';
    if (key === 'misc') return 'Mkt';
    return '';
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11.5px] min-w-[1100px] border-collapse">
        <thead>
          {/* Top header row — BASE YEAR / YEAR N */}
          <tr className="border-b border-border">
            <th
              rowSpan={3}
              className="text-left text-[10.5px] font-semibold text-ink-700 uppercase tracking-wider px-3 py-2 align-bottom border-r border-border bg-bg/40"
            >
              Index
            </th>
            <th
              rowSpan={3}
              className="text-left text-[10.5px] font-semibold text-ink-700 uppercase tracking-wider px-3 py-2 align-bottom border-r border-border bg-bg/40"
            >
              $/%
            </th>
            {years.map((y, i) => (
              <th
                key={`yh-${i}`}
                colSpan={4}
                className={cn(
                  'text-center text-[10.5px] font-semibold uppercase tracking-wider px-2 pt-2 pb-0',
                  i === 0 ? 'bg-ink-300/10 text-ink-700' : 'bg-brand-50/40 text-brand-700',
                  'border-l border-border',
                )}
              >
                {i === 0 ? 'Base Year' : `Year ${i}`}
              </th>
            ))}
          </tr>
          {/* Subtitle — actual years */}
          <tr className="border-b border-border">
            {years.map((y, i) => (
              <th
                key={`ys-${i}`}
                colSpan={4}
                className={cn(
                  'text-center text-[11px] font-semibold tabular-nums px-2 pb-1',
                  i === 0 ? 'bg-ink-300/10 text-ink-900' : 'bg-brand-50/40 text-ink-900',
                  'border-l border-border',
                )}
              >
                {y.year}
              </th>
            ))}
          </tr>
          {/* Sub-column headers */}
          <tr className="border-b border-border text-[9.5px] uppercase tracking-wider text-ink-500">
            {years.map((_, i) => (
              <SubHeaderGroup key={`sh-${i}`} dim={i === 0} />
            ))}
          </tr>
        </thead>
        <tbody>
          {/* Hotel Delivery */}
          <tr className="border-b border-border/60">
            <td className="px-3 py-2 text-[11px] text-ink-700 font-medium border-r border-border bg-bg/30">
              Hotel Delivery
            </td>
            <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30 tabular-nums">
              {hotelDelivery}
            </td>
            {years.map((_, i) => (
              <td
                key={`hd-${i}`}
                colSpan={4}
                className="px-2 py-2 text-center text-[11px] text-ink-400 border-l border-border"
              >
                —
              </td>
            ))}
          </tr>

          {/* Days */}
          <SimpleRow
            label="Days"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.days}
            fmt={(v) => v.toLocaleString()}
          />

          {/* Number of Rooms */}
          <SimpleRow
            label="Number of Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.rooms}
            fmt={(v) => v.toLocaleString()}
          />

          {/* Available Rooms */}
          <SimpleRow
            label="Available Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.availableRooms}
            fmt={(v) => v.toLocaleString()}
          />

          {/* Occupied Rooms */}
          <SimpleRow
            label="Occupied Rooms"
            indexLabel=""
            unit=""
            years={years}
            value={(y) => y.occupiedRooms}
            fmt={(v) => v.toLocaleString()}
          />

          {/* Occupancy */}
          <SimpleRow
            label="Occupancy"
            indexLabel={indexLabel('occupancy')}
            unit="%"
            years={years}
            value={(y) => y.occupancy * 100}
            fmt={(v) => `${v.toFixed(1)}%`}
          />

          {/* Average Rate */}
          <SimpleRow
            label="Average Rate"
            indexLabel={indexLabel('adr')}
            unit="$"
            years={years}
            value={(y) => y.adr}
            fmt={(v) => `$${v.toFixed(2)}`}
          />

          {/* RevPAR */}
          <SimpleRow
            label="RevPAR"
            indexLabel={indexLabel('revpar')}
            unit="$"
            years={years}
            value={(y) => y.revpar}
            fmt={(v) => `$${v.toFixed(2)}`}
          />

          {/* Annual RevPAR Growth */}
          <tr className="border-b border-border/60">
            <td className="px-3 py-2 text-[11px] text-ink-700 font-medium border-r border-border bg-bg/30">
              Annual RevPAR Growth
            </td>
            <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
              %
            </td>
            {years.map((_, i) => {
              const g = revparGrowth[i];
              return (
                <td
                  key={`rg-${i}`}
                  colSpan={4}
                  className={cn(
                    'px-2 py-2 text-center text-[11px] tabular-nums border-l border-border',
                    g === null ? 'text-ink-400' : 'text-ink-900',
                  )}
                >
                  {g === null ? 'N/A' : `${(g * 100).toFixed(1)}%`}
                </td>
              );
            })}
          </tr>

          {/* REVENUES section header */}
          <tr className="bg-brand-500/95">
            <td
              colSpan={2 + years.length * 4}
              className="px-3 py-1.5 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-white"
            >
              Revenues
            </td>
          </tr>

          {/* Rooms */}
          <FullRow
            label="Rooms"
            indexLabel={indexLabel('rooms')}
            unit="$"
            years={years}
            amountOf={(y) => y.roomsRevenue}
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
          />
          {/* Food & Beverage */}
          <FullRow
            label="Food & Beverage"
            indexLabel={indexLabel('fb')}
            unit="$"
            years={years}
            amountOf={(y) => y.fbRevenue}
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
          />
          {/* Misc. Income */}
          <FullRow
            label="Misc. Income"
            indexLabel={indexLabel('misc')}
            unit="$"
            years={years}
            amountOf={(y) => y.miscRevenue}
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
          />
          {/* Total Revenue */}
          <FullRow
            label="Total Revenue"
            indexLabel=""
            unit="$"
            years={years}
            amountOf={(y) => y.totalRevenue}
            fmtAmount={fmtAmount}
            pctRev={pctRev}
            par={par}
            por={por}
            bold
          />
        </tbody>
      </table>
      <div className="px-5 py-3 border-t border-border text-[11px] text-ink-500 flex items-center gap-1.5">
        <FileText size={11} />
        PAR = $/available room. POR = $/occupied room. % Rev = share of Total Revenue.
      </div>
    </div>
  );
}

// Sub-column header group: Amount | % Rev | PAR | POR.
function SubHeaderGroup({ dim }: { dim: boolean }) {
  const cls = cn(
    'px-2 py-1.5 text-right font-semibold border-l border-border',
    dim ? 'bg-ink-300/10' : 'bg-brand-50/40',
  );
  return (
    <>
      <th className={cls}>Amount</th>
      <th className={cls}>% Rev</th>
      <th className={cls}>PAR</th>
      <th className={cls}>POR</th>
    </>
  );
}

// Simple single-cell row (Days, Rooms, Occupancy, ADR, etc.) — value
// is rendered once per year, spanning all 4 sub-columns.
function SimpleRow({
  label,
  indexLabel,
  unit,
  years,
  value,
  fmt,
}: {
  label: string;
  indexLabel: string;
  unit: string;
  years: ProjYear[];
  value: (y: ProjYear) => number;
  fmt: (v: number) => string;
}) {
  return (
    <tr className="border-b border-border/60 hover:bg-ink-300/5">
      <td className="px-3 py-2 text-[11px] text-ink-700 font-medium border-r border-border bg-bg/30">
        {label}
      </td>
      <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
        {indexLabel || unit}
      </td>
      {years.map((y, i) => (
        <td
          key={`sr-${i}`}
          colSpan={4}
          className="px-2 py-2 text-center text-[11px] text-ink-900 tabular-nums border-l border-border"
        >
          {fmt(value(y))}
        </td>
      ))}
    </tr>
  );
}

// Full Amount/%Rev/PAR/POR row — used for revenue lines.
function FullRow({
  label,
  indexLabel,
  unit,
  years,
  amountOf,
  fmtAmount,
  pctRev,
  par,
  por,
  bold = false,
}: {
  label: string;
  indexLabel: string;
  unit: string;
  years: ProjYear[];
  amountOf: (y: ProjYear) => number;
  fmtAmount: (v: number, opts?: { decimals?: number; prefix?: string }) => string;
  pctRev: (v: number, total: number) => number;
  par: (v: number, available: number) => number;
  por: (v: number, occupied: number) => number;
  bold?: boolean;
}) {
  return (
    <tr
      className={cn(
        'border-b border-border/60 hover:bg-ink-300/5',
        bold && 'bg-brand-50/30 font-semibold',
      )}
    >
      <td
        className={cn(
          'px-3 py-2 text-[11px] border-r border-border bg-bg/30',
          bold ? 'text-ink-900 font-semibold' : 'text-ink-700 font-medium',
        )}
      >
        {label}
      </td>
      <td className="px-3 py-2 text-[11px] text-ink-500 border-r border-border bg-bg/30">
        {indexLabel || unit}
      </td>
      {years.map((y, i) => {
        const amt = amountOf(y);
        return (
          <SubCells
            key={`fr-${label}-${i}`}
            amount={amt}
            pctRev={pctRev(amt, y.totalRevenue)}
            par={par(amt, y.availableRooms)}
            por={por(amt, y.occupiedRooms)}
            fmtAmount={fmtAmount}
          />
        );
      })}
    </tr>
  );
}

function SubCells({
  amount,
  pctRev,
  par,
  por,
  fmtAmount,
}: {
  amount: number;
  pctRev: number;
  par: number;
  por: number;
  fmtAmount: (v: number, opts?: { decimals?: number; prefix?: string }) => string;
}) {
  const td = 'px-2 py-2 text-right text-[11px] text-ink-900 tabular-nums border-l border-border';
  return (
    <>
      <td className={td}>{fmtAmount(amount, { prefix: '$' })}</td>
      <td className={cn(td, 'text-ink-500')}>{pctRev > 0 ? `${pctRev.toFixed(1)}%` : '—'}</td>
      <td className={cn(td, 'text-ink-700')}>{par > 0 ? fmtAmount(par, { decimals: 0, prefix: '$' }) : '—'}</td>
      <td className={cn(td, 'text-ink-700')}>{por > 0 ? fmtAmount(por, { decimals: 0, prefix: '$' }) : '—'}</td>
    </>
  );
}
