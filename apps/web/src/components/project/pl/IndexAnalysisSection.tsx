'use client';
/**
 * IndexAnalysisSection — Subject vs CoStar Competitive Set, 2019–2033.
 *
 * Two stacked tables: subject property historical+forecast, and the
 * CoStar Market competitive-set historical+forecast. Each table runs
 * 15 year-columns (6 historical + 9 forecast) across the metrics
 * Days, Keys, Available Rooms, Occupied Rooms, Occupancy, ADR, RevPAR,
 * plus three growth rows.
 *
 * Sources:
 *  • Subject historical Y0 → revenue engine ``years[0]`` (post-T-12 anchor),
 *    earlier historical years left as "—" until multi-year extraction lands.
 *  • Subject forecast → revenue engine ``years[1..]``.
 *  • Comp set → ``GET /deals/{id}/market-data`` (str_trend for the most-
 *    recent historical anchor; cbre_horizons.years[] for forecast).
 *  • Kimpton demo (id=7) → kimptonAnglerOverview / kimptonAnalysis fixtures.
 *
 * Lovable parity: ADR + RevPAR rows render in green; growth rows render
 * negatives as red parens; Keys row is blue + link-styled. Wide tables
 * scroll horizontally with the leftmost Metric column sticky.
 */

import { useEffect, useMemo, useState } from 'react';
import { TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import {
  isWorkerConnected,
  workerUrl,
  EngineOutputsResponse,
  HistoricalBaselineResponse,
} from '@/lib/api';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useHistoricalBaseline } from '@/lib/hooks/useHistoricalBaseline';
import { kimptonAnglerOverview } from '@/lib/mockData';

const HISTORICAL_YEARS = [2019, 2020, 2021, 2022, 2023, 2024];
const FORECAST_YEARS = [2025, 2026, 2027, 2028, 2029, 2030, 2031, 2032, 2033];
const ALL_YEARS = [...HISTORICAL_YEARS, ...FORECAST_YEARS];

const NOTES_TEXT =
  'Notes: (1) Competitive Set RevPAR growth through 2020 is based on third-party projections. Assumed 3.0% growth thereafter.';

interface RevenueYear {
  year: number;
  occupancy: number;
  adr: number;
  revpar: number;
}
interface CbreYear {
  year_index: number;
  occupancy_pct: number | null;
  adr_usd: number | null;
  revpar_usd: number | null;
  revpar_growth_pct: number | null;
}
interface CompSetEntryAPI {
  name?: string | null;
  keys?: number | null;
  occupancy_pct?: number | null;
  adr_usd?: number | null;
  revpar_usd?: number | null;
}
interface MarketDataAPIResponse {
  deal_id: string;
  str_trend?: {
    subject_occupancy_pct?: number | null;
    subject_adr_usd?: number | null;
    subject_revpar_usd?: number | null;
    indices?: unknown;
    report_month?: string | null;
    comp_set_size?: number | null;
    total_keys?: number | null;
    compset?: CompSetEntryAPI[];
  } | null;
  cbre_horizons?: {
    submarket?: string | null;
    publication_date?: string | null;
    years?: CbreYear[];
  } | null;
}

// Per-year series for one entity (subject or comp set).
interface YearSeries {
  occupancy: (number | null)[];   // 0..1 ratio
  adr: (number | null)[];         // dollars
  revpar: (number | null)[];      // dollars
}

function isLeapYear(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

// Build the subject-property year series. Historical years pull from
// the historical_baseline engine (multi-year P&L roll-up — Sam's Wave 2
// P2.6 ask); forecast years pull from the revenue engine years[].
//
// The revenue engine only seeds from T-12 (most recent year), so without
// the baseline blend the 2019-2023 historical columns would be blank
// even when the Historicals tab is fully populated. Blending in the
// baseline.years[] (keyed by fiscal_year) populates every historical
// column the extractor actually shipped.
function buildSubjectSeries(
  outputs: EngineOutputsResponse | null,
  baseline: HistoricalBaselineResponse | null,
  isKimptonDemo: boolean,
): YearSeries {
  const series: YearSeries = {
    occupancy: ALL_YEARS.map(() => null),
    adr: ALL_YEARS.map(() => null),
    revpar: ALL_YEARS.map(() => null),
  };

  if (isKimptonDemo) {
    // Synthesized historical ramp consistent with Miami Beach lifestyle
    // boutique and the Kimpton mock proforma. Years align with index 0..14.
    const occH = [0.701, 0.448, 0.612, 0.703, 0.738, 0.762];
    const adrH = [298, 272, 312, 348, 372, 385];
    occH.forEach((o, i) => {
      series.occupancy[i] = o;
      series.adr[i] = adrH[i];
      series.revpar[i] = o * adrH[i];
    });
    // Forecast: anchor on revenue engine if present, otherwise grow at 4% / 3%.
    let occ = 0.762;
    let adr = 385;
    for (let i = 0; i < FORECAST_YEARS.length; i++) {
      const idx = HISTORICAL_YEARS.length + i;
      occ = i === 0 ? occ : Math.min(0.95, occ * 1.012);
      adr = i === 0 ? adr * 1.04 : adr * 1.035;
      series.occupancy[idx] = occ;
      series.adr[idx] = adr;
      series.revpar[idx] = occ * adr;
    }
    return series;
  }

  // Historical years — pull from historical_baseline.years[] by matching
  // fiscal_year to the column year. RevPAR falls back to occ × ADR when
  // the baseline didn't ship it directly (same identity the engine uses).
  if (baseline?.years && baseline.years.length > 0) {
    const byYear = new Map<number, (typeof baseline.years)[number]>();
    for (const y of baseline.years) byYear.set(y.fiscal_year, y);
    HISTORICAL_YEARS.forEach((yr, i) => {
      const row = byYear.get(yr);
      if (!row) return;
      series.occupancy[i] = row.occupancy;
      series.adr[i] = row.adr;
      series.revpar[i] =
        row.revpar ??
        (row.occupancy != null && row.adr != null
          ? row.occupancy * row.adr
          : null);
    });
  }

  // Live deal: revenue engine years[] starts at the post-T-12 stabilized
  // year and projects forward. Use it for forecast columns only; the
  // anchor (latest historical column) is left to the baseline above
  // when present, otherwise revenue years[0] serves as the fallback
  // anchor (preserves pre-baseline behavior for deals without P&L docs).
  const revYears = getEngineField<RevenueYear[]>(outputs, 'revenue', 'years');
  if (revYears && revYears.length > 0) {
    const anchorIdx = HISTORICAL_YEARS.length - 1;
    if (series.occupancy[anchorIdx] == null) {
      series.occupancy[anchorIdx] = revYears[0].occupancy;
      series.adr[anchorIdx] = revYears[0].adr;
      series.revpar[anchorIdx] = revYears[0].revpar;
    }
    for (let i = 1; i < revYears.length && i <= FORECAST_YEARS.length; i++) {
      const idx = anchorIdx + i;
      series.occupancy[idx] = revYears[i].occupancy;
      series.adr[idx] = revYears[i].adr;
      series.revpar[idx] = revYears[i].revpar;
    }
  }
  return series;
}

// Build the CoStar comp-set year series from market-data envelope.
function buildCompSeries(
  marketData: MarketDataAPIResponse | null,
  isKimptonDemo: boolean,
): YearSeries {
  const series: YearSeries = {
    occupancy: ALL_YEARS.map(() => null),
    adr: ALL_YEARS.map(() => null),
    revpar: ALL_YEARS.map(() => null),
  };

  if (isKimptonDemo) {
    // STR-style comp set: Miami Beach upscale boutique cohort. The
    // Lovable mock note says comp set RevPAR through 2020 is from a
    // third-party projection and 3.0% thereafter — we honor that.
    const occH = [0.731, 0.468, 0.622, 0.708, 0.741, 0.759];
    const adrH = [310, 285, 322, 358, 379, 391];
    occH.forEach((o, i) => {
      series.occupancy[i] = o;
      series.adr[i] = adrH[i];
      series.revpar[i] = o * adrH[i];
    });
    let occ = 0.759;
    let adr = 391;
    for (let i = 0; i < FORECAST_YEARS.length; i++) {
      const idx = HISTORICAL_YEARS.length + i;
      occ = i === 0 ? occ * 1.005 : Math.min(0.95, occ * 1.008);
      adr = i === 0 ? adr * 1.03 : adr * 1.03;
      series.occupancy[idx] = occ;
      series.adr[idx] = adr;
      series.revpar[idx] = occ * adr;
    }
    return series;
  }

  // Live: STR comp set carries one year of historical (subject + comp).
  const str = marketData?.str_trend;
  if (str && str.subject_occupancy_pct != null && str.subject_adr_usd != null) {
    const occ = str.subject_occupancy_pct > 1
      ? str.subject_occupancy_pct / 100
      : str.subject_occupancy_pct;
    const adr = str.subject_adr_usd;
    const idx = HISTORICAL_YEARS.length - 1;
    series.occupancy[idx] = occ;
    series.adr[idx] = adr;
    series.revpar[idx] = str.subject_revpar_usd ?? occ * adr;
  }

  // CBRE Horizons forecast — year_index 1..5 maps to FORECAST_YEARS[0..4].
  const cbreYears = marketData?.cbre_horizons?.years ?? [];
  for (const y of cbreYears) {
    const fIdx = (y.year_index ?? 0) - 1;
    if (fIdx < 0 || fIdx >= FORECAST_YEARS.length) continue;
    const idx = HISTORICAL_YEARS.length + fIdx;
    if (y.occupancy_pct != null) {
      series.occupancy[idx] = y.occupancy_pct > 1 ? y.occupancy_pct / 100 : y.occupancy_pct;
    }
    if (y.adr_usd != null) series.adr[idx] = y.adr_usd;
    if (y.revpar_usd != null) series.revpar[idx] = y.revpar_usd;
  }
  // Beyond CBRE's 5-year horizon, grow last known RevPAR at 3.0%.
  for (let i = HISTORICAL_YEARS.length + 5; i < ALL_YEARS.length; i++) {
    if (series.revpar[i] != null) continue;
    const prevAdr = series.adr[i - 1];
    const prevOcc = series.occupancy[i - 1];
    if (prevAdr != null && prevOcc != null) {
      series.adr[i] = prevAdr * 1.03;
      series.occupancy[i] = prevOcc;
      series.revpar[i] = series.adr[i]! * series.occupancy[i]!;
    }
  }
  return series;
}

// (current / prior) - 1, returning null when either side is missing/zero.
function growth(curr: number | null, prior: number | null): number | null {
  if (curr == null || prior == null || prior === 0) return null;
  return curr / prior - 1;
}

function fmtPct(v: number | null, decimals = 1): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
}
function fmtDollar(v: number | null): string {
  if (v == null) return '—';
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}
function fmtInt(v: number | null): string {
  if (v == null) return '—';
  return v.toLocaleString('en-US');
}

// Negative growth → red parens "(36.0%)"; positive → "12.5%"; null → "N/A".
function GrowthCell({ value }: { value: number | null }) {
  if (value == null) {
    return <span className="text-ink-400">N/A</span>;
  }
  if (value < 0) {
    return (
      <span className="text-danger-700">
        ({(Math.abs(value) * 100).toFixed(1)}%)
      </span>
    );
  }
  return <span>{(value * 100).toFixed(1)}%</span>;
}

interface TableProps {
  title: string;
  keys: number;
  series: YearSeries;
}

function IndexTable({ title, keys, series }: TableProps) {
  // Derived rows.
  const days = ALL_YEARS.map((y) => (isLeapYear(y) ? 366 : 365));
  const available = days.map((d) => d * keys);
  const occupied = ALL_YEARS.map((_, i) => {
    const occ = series.occupancy[i];
    if (occ == null) return null;
    return Math.round(available[i] * occ);
  });
  const occGrowth = ALL_YEARS.map((_, i) =>
    i === 0 ? null : growth(series.occupancy[i], series.occupancy[i - 1]),
  );
  const adrGrowth = ALL_YEARS.map((_, i) =>
    i === 0 ? null : growth(series.adr[i], series.adr[i - 1]),
  );
  const revparGrowth = ALL_YEARS.map((_, i) =>
    i === 0 ? null : growth(series.revpar[i], series.revpar[i - 1]),
  );

  // Sticky-leftmost column class shorthand.
  const stickyL = 'sticky left-0 bg-card z-10 border-r border-border';

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px] border-collapse" style={{ minWidth: 1400 }}>
        <thead>
          {/* Top header — entity title + HISTORICAL / FORECAST band */}
          <tr className="text-ink-700 border-b border-border">
            <th
              className={cn(
                stickyL,
                'text-left font-semibold uppercase tracking-wide text-[11px] px-3 py-2',
              )}
            >
              {title}
            </th>
            <th
              colSpan={HISTORICAL_YEARS.length}
              className="text-center font-semibold uppercase tracking-wide text-[10.5px] text-ink-500 bg-ink-100/40 border-l border-border px-2 py-2"
            >
              Historical
            </th>
            <th
              colSpan={FORECAST_YEARS.length}
              className="text-center font-semibold uppercase tracking-wide text-[10.5px] text-brand-700 bg-brand-50/40 border-l border-border px-2 py-2"
            >
              Forecast
            </th>
          </tr>
          {/* Sub-header — Metric + each year */}
          <tr className="text-ink-500 text-[10.5px] border-b border-border">
            <th
              className={cn(
                stickyL,
                'text-left font-medium px-3 py-1.5',
              )}
            >
              Metric
            </th>
            {ALL_YEARS.map((y, i) => (
              <th
                key={y}
                className={cn(
                  'text-right font-medium px-2 py-1.5 tabular-nums',
                  i === 0 && 'border-l border-border',
                  i === HISTORICAL_YEARS.length && 'border-l border-border',
                )}
              >
                {y}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <Row label="Days" cells={days.map((d) => fmtInt(d))} stickyL={stickyL} />
          <Row
            label="Keys"
            cells={ALL_YEARS.map(() => (
              <span className="text-brand-700 font-medium underline-offset-2 hover:underline cursor-pointer">
                {fmtInt(keys)}
              </span>
            ))}
            stickyL={stickyL}
            zebra
          />
          <Row label="Available Rooms" cells={available.map((v) => fmtInt(v))} stickyL={stickyL} />
          <Row
            label="Occupied Rooms"
            cells={occupied.map((v) => fmtInt(v))}
            stickyL={stickyL}
            zebra
          />
          <Row
            label="Occupancy"
            cells={series.occupancy.map((v) => fmtPct(v, 1))}
            stickyL={stickyL}
          />
          <Row
            label="ADR"
            cells={series.adr.map((v) => (
              <span className="text-success-700">{fmtDollar(v)}</span>
            ))}
            stickyL={stickyL}
            zebra
          />
          <Row
            label="RevPAR"
            cells={series.revpar.map((v) => (
              <span className="text-success-700">{fmtDollar(v)}</span>
            ))}
            stickyL={stickyL}
          />
          <Row
            label="Occupancy Growth"
            cells={occGrowth.map((v) => <GrowthCell value={v} />)}
            stickyL={stickyL}
            zebra
          />
          <Row
            label="ADR Growth"
            cells={adrGrowth.map((v) => <GrowthCell value={v} />)}
            stickyL={stickyL}
          />
          <Row
            label="RevPAR Growth"
            cells={revparGrowth.map((v) => <GrowthCell value={v} />)}
            stickyL={stickyL}
            zebra
          />
        </tbody>
      </table>
    </div>
  );
}

function Row({
  label,
  cells,
  stickyL,
  zebra,
}: {
  label: string;
  cells: React.ReactNode[];
  stickyL: string;
  zebra?: boolean;
}) {
  return (
    <tr
      className={cn(
        'border-b border-border/40',
        zebra && 'bg-ink-300/[0.03]',
      )}
    >
      <td
        className={cn(
          stickyL,
          'text-left font-medium text-ink-900 px-3 py-1.5 whitespace-nowrap',
          zebra && 'bg-card', // keep sticky column readable; bg-card sits on top
        )}
        style={zebra ? { backgroundColor: 'var(--card, #fff)' } : undefined}
      >
        {label}
      </td>
      {cells.map((c, i) => (
        <td
          key={i}
          className={cn(
            'text-right tabular-nums px-2 py-1.5 whitespace-nowrap',
            i === 0 && 'border-l border-border',
            i === HISTORICAL_YEARS.length && 'border-l border-border',
          )}
        >
          {c}
        </td>
      ))}
    </tr>
  );
}

export default function IndexAnalysisSection({
  dealId,
  isKimptonDemo,
}: {
  dealId: string;
  isKimptonDemo: boolean;
}) {
  const { outputs } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);
  // Wave 2 P2.6 — multi-year historical baseline. Without it, the
  // 2019-2023 historical columns would render blank because the
  // revenue engine only seeds from T-12 (most recent year only).
  const { baseline: historicalBaseline } = useHistoricalBaseline(dealId);
  const [marketData, setMarketData] = useState<MarketDataAPIResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const liveMode = isWorkerConnected() && !!dealId && !/^\d+$/.test(dealId);

  useEffect(() => {
    if (!liveMode) return;
    const ctrl = new AbortController();
    setLoading(true);
    fetch(`${workerUrl()}/deals/${dealId}/market-data`, { signal: ctrl.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return (await res.json()) as MarketDataAPIResponse;
      })
      .then((json) => {
        setMarketData(json);
        setLoading(false);
      })
      .catch((e) => {
        if ((e as Error).name === 'AbortError') return;
        setLoading(false);
      });
    return () => ctrl.abort();
  }, [dealId, liveMode]);

  const subjectKeys = isKimptonDemo
    ? kimptonAnglerOverview.general.keys
    : (deal?.keys && deal.keys > 0 ? deal.keys : 0);

  const subjectSeries = useMemo(
    () => buildSubjectSeries(outputs, historicalBaseline, isKimptonDemo),
    [outputs, historicalBaseline, isKimptonDemo],
  );
  const compSeries = useMemo(
    () => buildCompSeries(marketData, isKimptonDemo),
    [marketData, isKimptonDemo],
  );

  // Comp-set "keys" row uses the total comp-set room count.
  //
  // Source priority for live deals:
  //   1. `str_trend.total_keys` — extracted from the STR Trend
  //      "Response" tab roster (or summed from `compset[i].keys` when
  //      the rollup row was missing — see _build_str_trend_block).
  //   2. Sum of `str_trend.compset[i].keys` — defensive fallback if
  //      the backend somehow lost the rollup mid-flight.
  //   3. 0 — last resort; the Available-Rooms row will render zeros
  //      and the empty-state copy elsewhere tells the user to upload
  //      an STR Trend report.
  const compKeys = isKimptonDemo
    ? 1240
    : (() => {
        const fromRollup = marketData?.str_trend?.total_keys;
        if (typeof fromRollup === 'number' && fromRollup > 0) return fromRollup;
        const fromRoster = (marketData?.str_trend?.compset ?? []).reduce(
          (acc, row) => acc + (typeof row.keys === 'number' && row.keys > 0 ? row.keys : 0),
          0,
        );
        return fromRoster > 0 ? fromRoster : 0;
      })();

  // Empty state — no engine outputs and no market data (and not Kimpton demo).
  const subjectHasAny =
    isKimptonDemo ||
    subjectSeries.occupancy.some((v) => v != null) ||
    subjectSeries.adr.some((v) => v != null);
  const compHasAny =
    isKimptonDemo ||
    compSeries.occupancy.some((v) => v != null) ||
    compSeries.adr.some((v) => v != null);

  if (!subjectHasAny && !compHasAny) {
    return (
      <Card className="p-12 text-center">
        <div className="w-12 h-12 rounded-lg bg-ink-100 flex items-center justify-center mx-auto mb-3">
          <TrendingUp size={20} className="text-ink-500" />
        </div>
        <h3 className="text-[14px] font-semibold text-ink-900 mb-1">
          No Index Analysis data
        </h3>
        <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
          Index Analysis populates from CBRE Horizons + STR comp set extraction.
          Upload those reports to enable side-by-side subject vs market comparison.
        </p>
        {loading && (
          <div className="text-[10.5px] text-ink-400 mt-3 italic">Loading market data…</div>
        )}
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">
            Subject vs Competitive Set Index
          </h3>
          <p className="text-[11.5px] text-ink-500 mt-0.5">
            Historical and forecast index series, 2019–2033
          </p>
        </div>
        <Badge tone="blue" uppercase>15-Year Series</Badge>
      </div>

      <div className="text-[11px] text-ink-500 italic leading-relaxed">{NOTES_TEXT}</div>

      <Card className="p-0 overflow-hidden">
        <IndexTable
          title="Subject Property"
          keys={subjectKeys}
          series={subjectSeries}
        />
      </Card>

      <Card className="p-0 overflow-hidden">
        <IndexTable
          title="CoStar Market — Competitive Set"
          keys={compKeys}
          series={compSeries}
        />
      </Card>
    </div>
  );
}
