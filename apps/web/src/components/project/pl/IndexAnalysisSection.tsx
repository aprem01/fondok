'use client';
/**
 * IndexAnalysisSection — Subject vs Competitive Set vs Market, on the deal's
 * acquisition calendar (FON-94 / FON-95).
 *
 * Three stacked index tables (Subject Property, CoStar comp set, Market) and
 * a Penetration table. The year columns are derived from the revenue
 * engine's ``projection_calendar_years`` (Year 1 = calendar year of the first
 * operating month after close): the forecast columns are exactly the model
 * horizon and the six columns before them are history, so the same calendar
 * year sits in the same column as the Future P&L. Nothing is carried forward
 * beyond the engine horizon; with no close date the forecast columns say
 * "Year N" and a one-line note says why.
 *
 * Sources (every cell names its own on hover; the Source row says it inline):
 *  • Subject history → STR Trend annual subject figures
 *    (``ttm_performance.subject.annual.<YYYY>``), then the multi-year P&L
 *    fiscal year. Subject forecast → revenue engine ``years[ordinal − 1]``.
 *  • Comp set → STR TTM blend (subject ÷ MPI / ARI / RGI) in the last
 *    historical column; the forecast half is the worker's ``comp_set_by_year``
 *    — the TTM level grown at the market occupancy / ADR growth assumption
 *    (document default or analyst override), the same series the STR comp-set
 *    index method runs on, labelled as an assumption. With no growth value the
 *    out-years stay "—" with the reason — never the subject's growth re-scaled.
 *  • Market → market-level actuals (market study / STR market rows) by
 *    calendar year; forecast = CBRE Horizons placed by ITS calendar year,
 *    labelled Market / CBRE Horizons with document + page.
 *
 * All pure logic lives in ``indexAnalysisModel.ts``.
 */

import { useEffect, useMemo, useState } from 'react';
import { TrendingUp } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { cn } from '@/lib/format';
import { api, isWorkerConnected, type IndexMethodologyResponse } from '@/lib/api';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { toIndexPoints } from '@/lib/indexTarget';
import { useDeal } from '@/lib/hooks/useDeal';
import { useHistoricalBaseline } from '@/lib/hooks/useHistoricalBaseline';
import { useSource } from '@/lib/hooks/useDealProvenance';
import {
  BasisChip,
  buildReconciliation,
  IndexMethodologyPanel,
  ProjectionsReconciliation,
  type IaColumnOrigin,
  type ReconciliationSource,
} from './IndexMethodology';
import {
  buildCompSeries,
  buildMarketSeries,
  buildSubjectSeries,
  COMP_FORECAST_GROWN_LABEL,
  computePenetration,
  daysForColumn,
  emptySeries,
  deriveIndexColumns,
  fmtDollar,
  fmtInt,
  fmtPct,
  growth,
  indexPoints,
  NO_CLOSE_DATE_NOTE,
  occFraction,
  posOrNull,
  strPeriodLabel,
  subjectDocumentYears,
  type CellSource,
  type ForecastBoundary,
  type IaColumns,
  type MarketDataAPIResponse,
  type PenetrationBenchmark,
  type PenetrationRow,
  type RevenueYear,
  type YearSeries,
  type YearTarget,
  compSetKeys,
} from './indexAnalysisModel';

export type { ForecastBoundary } from './indexAnalysisModel';

const NOTES_TEXT =
  'Notes: (1) Competitive-set performance is the STR comp-set aggregate, recovered from the subject’s published penetration indices (MPI occupancy, ARI rate, RGI RevPAR) — comp = subject ÷ index — matching Market Overview; it sits in the last historical column because STR publishes it for the trailing twelve months only. Its forecast columns are that TTM level grown at the market occupancy / ADR growth assumption (the document default or your override on Index methodology) — an assumption, not an STR figure — and stay blank with the reason when no growth value exists. STR anonymizes per-property comp performance, so individual competitor Occ/ADR/RevPAR are not published. (2) The MPI/ARI/RGI penetration indices are the STR-published values, measured on STR’s trailing-twelve-month subject basis; the subject history rows are the STR annual subject figures where the report carries them, else the operating P&L fiscal years — the Source row says which. (3) The forecast band of each table names its own origin; the Market table’s forecast is CBRE Horizons placed by its own calendar year, and is a market forecast, not a comp-set forecast. (4) The first forecast year shows no growth rate when the last historical column is a reported actual on a different period basis from the model’s Base Year. (5) Forecast columns use the model’s 365-day year so Occupied Rooms foot to the Future P&L. (6) Blank cells are unreported data, not zero. (7) Every benchmark column carries a basis chip and label: Actual (a reported figure — the STR TTM with its period end, an STR annual or market-study year, a P&L fiscal year), Forecast (CBRE Horizons for that calendar year, with document and page) or Assumption (the Year-1 level grown at the market growth assumption), so reported history is never read as a projected benchmark.';

/**
 * FON-41 E-027 — where an Index Analysis SUBJECT column's numbers come from.
 * The Source row's entry for the column, restated for the reconciliation card.
 */
export function iaColumnOrigin(source: CellSource | null): IaColumnOrigin {
  if (!source) return { kind: 'none' };
  if (source.kind === 'engine') return { kind: 'engine', yearIndex: source.yearIndex ?? 0 };
  if (source.kind === 'str_annual') return { kind: 'str_annual' };
  return { kind: 'fiscal_year' };
}

// Negative growth → red parens "(36.0%)"; positive → "12.5%"; null → "N/A".
function GrowthCell({ value }: { value: number | null }) {
  if (value == null) {
    return <span className="text-ink-400">N/A</span>;
  }
  if (value < 0) {
    return <span className="text-danger-700">({(Math.abs(value) * 100).toFixed(1)}%)</span>;
  }
  return <span>{(value * 100).toFixed(1)}%</span>;
}

const stickyL = 'sticky left-0 bg-card z-10 border-r border-border';

function YearHeaderRow({ cols, firstLabel }: { cols: IaColumns; firstLabel: string }) {
  const { columns, firstForecastIndex } = cols;
  return (
    <tr className="text-ink-500 text-[10.5px] border-b border-border">
      <th className={cn(stickyL, 'text-left font-medium px-3 py-1.5')}>{firstLabel}</th>
      {columns.map((c, i) => (
        <th
          key={c.key}
          className={cn(
            'text-right font-medium px-2 py-1.5 tabular-nums',
            i === 0 && 'border-l border-border',
            i === firstForecastIndex && 'border-l border-border',
          )}
          title={c.ordinal != null && c.calendarYear != null ? `Model Year ${c.ordinal}` : undefined}
        >
          <div>{c.label}</div>
          {c.ordinal != null && c.calendarYear != null && (
            <div className="text-[9.5px] text-ink-400 font-normal">Year {c.ordinal}</div>
          )}
        </th>
      ))}
    </tr>
  );
}

function BandRow({
  cols,
  title,
  forecastBand,
  forecastReason,
}: {
  cols: IaColumns;
  title: string;
  forecastBand: string;
  forecastReason?: string | null;
}) {
  const { columns, firstForecastIndex } = cols;
  const histCount = firstForecastIndex;
  const fcCount = columns.length - firstForecastIndex;
  return (
    <tr className="text-ink-700 border-b border-border">
      <th className={cn(stickyL, 'text-left font-semibold uppercase tracking-wide text-[11px] px-3 py-2')}>{title}</th>
      {histCount > 0 && (
        <th
          colSpan={histCount}
          className="text-center font-semibold uppercase tracking-wide text-[10.5px] text-ink-500 bg-ink-100/40 border-l border-border px-2 py-2"
        >
          Historical
        </th>
      )}
      {fcCount > 0 && (
        <th
          colSpan={fcCount}
          className="text-center font-semibold uppercase tracking-wide text-[10.5px] text-brand-700 bg-brand-50/40 border-l border-border px-2 py-2"
          title={forecastReason ?? undefined}
        >
          Forecast — {forecastBand}
        </th>
      )}
    </tr>
  );
}

interface TableProps {
  title: string;
  cols: IaColumns;
  keys: number | null;
  series: YearSeries;
  sources: (CellSource | null)[];
  /** Subject rows carry the underwriting forecast — forecast-year Occupancy /
   *  ADR / RevPAR cells render in assumption blue so a forecast is never read
   *  as an STR actual. Comp-set / market rows stay green. */
  subject?: boolean;
  /** WHOSE forecast the forecast band is (FON-60 60.4). */
  forecastBand: string;
  /** Why the forecast half is "—", when it is. On hover of the band. */
  forecastReason?: string | null;
  /** FON-61 (61.3) — whether the first forecast column's growth compares two
   *  periods on the same basis. When it does not, that one cell is N/A. */
  boundary: ForecastBoundary;
}

function IndexTable({ title, cols, keys, series, sources, subject = false, forecastBand, forecastReason, boundary }: TableProps) {
  const { columns, firstForecastIndex } = cols;
  const metricClass = (i: number): string =>
    subject && i >= firstForecastIndex ? 'text-brand-700' : 'text-success-700';
  const kv = typeof keys === 'number' && keys > 0 ? keys : null;
  const days = columns.map(daysForColumn);
  const available = days.map((d) => (kv != null ? d * kv : null));
  const occupied = columns.map((_, i) => {
    const occ = series.occupancy[i];
    const av = available[i];
    if (occ == null || av == null) return null;
    return Math.round(av * occ);
  });
  const refuseAtBoundary = !boundary.comparable;
  const growthAt = (arr: (number | null)[], i: number): number | null =>
    i === 0 ? null : i === firstForecastIndex && refuseAtBoundary ? null : growth(arr[i], arr[i - 1]);
  const boundaryNote = boundary.comparable ? null : boundary.note;
  const growthCell = (v: number | null, i: number) =>
    i === firstForecastIndex && refuseAtBoundary ? (
      <span className="text-ink-400" title={boundaryNote ?? undefined}>
        N/A
      </span>
    ) : (
      <GrowthCell value={v} />
    );
  const occGrowth = columns.map((_, i) => growthAt(series.occupancy, i));
  const adrGrowth = columns.map((_, i) => growthAt(series.adr, i));
  const revparGrowth = columns.map((_, i) => growthAt(series.revpar, i));
  const traced = (text: string, i: number) => (
    <span title={sources[i]?.detail ?? undefined}>{text}</span>
  );

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px] border-collapse" style={{ minWidth: Math.max(900, 180 + columns.length * 82) }}>
        <thead>
          <BandRow cols={cols} title={title} forecastBand={forecastBand} forecastReason={forecastReason} />
          <YearHeaderRow cols={cols} firstLabel="Metric" />
        </thead>
        <tbody>
          <Row cols={cols} label="Days" cells={days.map((d) => fmtInt(d))} />
          <Row
            cols={cols}
            label="Keys"
            cells={columns.map((_, i) => (
              <span key={i} className="text-brand-700 font-medium">{fmtInt(kv)}</span>
            ))}
            zebra
          />
          <Row cols={cols} label="Available Rooms" cells={available.map((v) => fmtInt(v))} />
          <Row cols={cols} label="Occupied Rooms" cells={occupied.map((v) => fmtInt(v))} zebra />
          <Row
            cols={cols}
            label="Occupancy"
            cells={series.occupancy.map((v, i) => (
              <span key={i} className={metricClass(i)}>{traced(fmtPct(v, 1), i)}</span>
            ))}
          />
          <Row
            cols={cols}
            label="ADR"
            cells={series.adr.map((v, i) => (
              <span key={i} className={metricClass(i)}>{traced(fmtDollar(v), i)}</span>
            ))}
            zebra
          />
          <Row
            cols={cols}
            label="RevPAR"
            cells={series.revpar.map((v, i) => (
              <span key={i} className={metricClass(i)}>{traced(fmtDollar(v), i)}</span>
            ))}
          />
          <Row cols={cols} label="Occupancy Growth" cells={occGrowth.map(growthCell)} zebra />
          <Row cols={cols} label="ADR Growth" cells={adrGrowth.map(growthCell)} />
          <Row cols={cols} label="RevPAR Growth" cells={revparGrowth.map(growthCell)} zebra />
          <Row
            cols={cols}
            label="Source"
            cells={sources.map((s, i) => (
              <span key={i} className="inline-flex flex-col items-end gap-0.5 text-[10.5px] text-ink-500">
                {s?.basis && <BasisChip basis={s.basis} label={s.basisLabel} />}
                <span title={s?.detail ?? undefined}>{s?.label ?? '—'}</span>
              </span>
            ))}
            testId={`index-source-row-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}
          />
          <Row
            cols={cols}
            label="Basis"
            labelTitle="Sam #6 — whether each column is reported historical data (Actual), a CBRE projection (Forecast) or the Year-1 level grown at the market growth assumption (Assumption), with its source and reporting period."
            cells={sources.map((s, i) => (
              <span key={i} className="inline-block max-w-[150px] truncate text-[10px] text-ink-500" title={s?.basisLabel ?? undefined}>
                {s?.basisLabel ?? '—'}
              </span>
            ))}
            zebra
            testId={`index-basis-row-${title.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}
          />
        </tbody>
      </table>
    </div>
  );
}

function PenetrationTable({ cols, rows, forecastBand }: { cols: IaColumns; rows: PenetrationRow[]; forecastBand: string }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px] border-collapse" style={{ minWidth: Math.max(900, 180 + cols.columns.length * 82) }}>
        <thead>
          <BandRow cols={cols} title="Penetration Index" forecastBand={forecastBand} />
          <YearHeaderRow cols={cols} firstLabel="Subject ÷ Benchmark × 100 · shares · variances" />
        </thead>
        <tbody>
          {rows.map((r, idx) => (
            <Row
              key={r.key}
              cols={cols}
              label={r.label}
              labelTitle={r.reason ?? undefined}
              zebra={idx % 2 === 1}
              testId={`index-penetration-${r.key}`}
              cells={r.cells.map((c, i) => (
                <span key={i} title={c.trace ?? r.reason ?? undefined}>{c.text}</span>
              ))}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Row({
  cols,
  label,
  labelTitle,
  cells,
  zebra,
  testId,
}: {
  cols: IaColumns;
  label: string;
  labelTitle?: string;
  cells: React.ReactNode[];
  zebra?: boolean;
  testId?: string;
}) {
  const { firstForecastIndex } = cols;
  return (
    <tr className={cn('border-b border-border/40', zebra && 'bg-ink-300/[0.03]')} data-testid={testId}>
      <td
        className={cn(stickyL, 'text-left font-medium text-ink-900 px-3 py-1.5 whitespace-nowrap', zebra && 'bg-card')}
        style={zebra ? { backgroundColor: 'var(--card, #fff)' } : undefined}
        title={labelTitle}
      >
        {label}
      </td>
      {cells.map((c, i) => (
        <td
          key={i}
          className={cn(
            'text-right tabular-nums px-2 py-1.5 whitespace-nowrap',
            i === 0 && 'border-l border-border',
            i === firstForecastIndex && 'border-l border-border',
          )}
        >
          {c}
        </td>
      ))}
    </tr>
  );
}

export default function IndexAnalysisSection({ dealId }: { dealId: string }) {
  const { outputs } = useEngineOutputs(dealId);
  const { deal } = useDeal(dealId);
  const { baseline: historicalBaseline } = useHistoricalBaseline(dealId);
  const [marketData, setMarketData] = useState<MarketDataAPIResponse | null>(null);
  const [methodology, setMethodology] = useState<IndexMethodologyResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const liveMode = isWorkerConnected() && !!dealId && !/^\d+$/.test(dealId);

  useEffect(() => {
    if (!liveMode) return;
    const ctrl = new AbortController();
    setLoading(true);
    // Route through the authenticated api helper (attaches Authorization +
    // X-Tenant-Id) — exactly as Market Overview does (FON-61).
    api.market
      .data(dealId, ctrl.signal)
      .then((json) => {
        if (json) setMarketData(json as MarketDataAPIResponse);
        setLoading(false);
      })
      .catch((e) => {
        if ((e as Error).name === 'AbortError') return;
        setLoading(false);
      });
    // The analyst's MPI / ARI targets feed "Revenue Opportunity at Target".
    // The methodology panel below does its own read; this one only needs the
    // two target values.
    api.market
      .indexMethodology(dealId, ctrl.signal)
      .then((r) => {
        if (r) setMethodology(r);
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [dealId, liveMode]);

  // Null (not 0) when the room count is unknown, so the table renders "—"
  // rather than a misleading "Available Rooms 0 / Occupied Rooms 0" (FON-61 c).
  const subjectKeys: number | null = deal?.keys && deal.keys > 0 ? deal.keys : null;

  const str = marketData?.str_trend ?? null;
  const revYears = getEngineField<RevenueYear[]>(outputs, 'revenue', 'years') ?? [];
  const calendarYears = getEngineField<number[]>(outputs, 'revenue', 'projection_calendar_years') ?? [];

  // FON-94 — the columns come from the engine's calendar, never a constant.
  const cols = useMemo(
    () =>
      deriveIndexColumns({
        calendarYears,
        forecastCount: revYears.length,
        documentYears: subjectDocumentYears(historicalBaseline, str),
        hasStrTtm: !!str,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [calendarYears.join(','), revYears.length, historicalBaseline, str],
  );

  const subject = useMemo(
    () => buildSubjectSeries(cols, outputs, historicalBaseline, str),
    [cols, outputs, historicalBaseline, str],
  );
  const comp = useMemo(
    () => buildCompSeries(cols, str, methodology?.comp_set_by_year ?? null),
    [cols, str, methodology],
  );
  const market = useMemo(() => buildMarketSeries(cols, marketData), [cols, marketData]);

  // Comp-set "keys" row = active roster keys (closed hotels excluded).
  const compKeys: number | null = compSetKeys(str);

  // The ANALYST's all-years MPI / ARI targets (source 'override') feed
  // "Revenue Opportunity at Target" where no per-year target applies. A
  // document default ('document' / 'computed') is the published index itself
  // — restating it as a "target" would price an opportunity nobody set — so
  // with no analyst target the row is "—" and its reason says so.
  const targetOf = (key: string): number | null => {
    const fig = methodology?.assumptions?.[key];
    // Same units as the worker (ratio 1.10 = 110 points; out of range = none).
    return fig?.source === 'override' ? toIndexPoints(fig.value) : null;
  };
  // FON-95 part 2 — the effective ANALYST-SET target per model year from the
  // worker's by-year grid (by-year beats scalar; index = ordinal − 1). These
  // drive the index rows' forecast cells and the per-year opportunity.
  const targetsByYear = useMemo(() => {
    const rows = methodology?.by_year ?? [];
    if (!rows.length) return null;
    const size = Math.max(...rows.map((r) => r.year));
    const mpi: (YearTarget | null)[] = Array.from({ length: size }, () => null);
    const ari: (YearTarget | null)[] = Array.from({ length: size }, () => null);
    const of = (t: { value: number | null; source: 'by_year' | 'scalar' | null } | undefined): YearTarget | null => {
      const v = toIndexPoints(t?.value ?? null);
      return v != null && t?.source ? { value: v, source: t.source } : null;
    };
    for (const r of rows) {
      mpi[r.year - 1] = of(r.mpi_target);
      ari[r.year - 1] = of(r.ari_target);
    }
    return { mpi, ari };
  }, [methodology]);
  const hasTargets =
    !!targetsByYear &&
    (targetsByYear.mpi.some((v) => v != null) || targetsByYear.ari.some((v) => v != null));
  // The index rows print a by-year target ONLY while the worker says the grid
  // drives the model (``by_year_applies`` — "Use STR rates in the model" on);
  // otherwise the Subject rows do not reflect the targets, so printing them
  // would show a penetration the model never runs. Then the published index
  // is held flat and a one-line note says the targets are set but not applied.
  const byYearApplies = !!methodology?.by_year_applies;
  const appliedTargetsByYear = byYearApplies ? targetsByYear : null;
  const scalarTargets = { mpi: targetOf('index_mpi_target'), ari: targetOf('index_ari_target') };
  const anyTargetSet = hasTargets || scalarTargets.mpi != null || scalarTargets.ari != null;
  const targetsNotApplied = anyTargetSet && !byYearApplies;
  // Revenue Opportunity at Target is gated EXACTLY like the index rows: a
  // target the model does not run on prices no opportunity.
  const appliedScalarTargets = byYearApplies ? scalarTargets : null;
  const targetGateReason = targetsNotApplied
    ? `Targets set but not applied — ${methodology?.by_year_disabled_reason ?? 'turn on “Use STR rates in the model” in Market Overview.'}`
    : null;

  // Sam #3 — the penetration rows follow the SELECTED benchmark (Index
  // methodology). The STR comp set keeps the published indices; another
  // method indexes the subject against that benchmark: its TTM level in the
  // anchor column and the worker's by-year benchmark in the forecast columns.
  const benchmark = useMemo((): PenetrationBenchmark | null => {
    if (!methodology) return null;
    const m = methodology.methods.find((x) => x.method === methodology.selected);
    if (!m || m.method === 'str_comp_set') return null;
    const series = emptySeries(cols.columns.length);
    const details: (string | null)[] = cols.columns.map(() => null);
    const byYear = new Map((methodology.by_year ?? []).map((r) => [r.year, r]));
    const put = (i: number, occRaw: number | null | undefined, adrRaw: number | null | undefined, detail: string | null) => {
      const o = occFraction(occRaw ?? null);
      const a = posOrNull(adrRaw ?? null);
      series.occupancy[i] = o;
      series.adr[i] = a;
      series.revpar[i] = o != null && a != null ? o * a : null;
      details[i] = detail;
    };
    const anchor = cols.firstForecastIndex - 1;
    if (m.available && anchor >= 0) {
      put(anchor, m.occupancy.value, m.adr.value, m.occupancy.detail ?? m.occupancy.period_label ?? null);
    }
    cols.columns.forEach((c, i) => {
      if (c.kind !== 'forecast' || c.ordinal == null) return;
      const r = byYear.get(c.ordinal);
      if (r) put(i, r.benchmark_occupancy.value, r.benchmark_adr.value, r.benchmark_occupancy.detail ?? null);
    });
    const subjOcc = occFraction(methodology.subject_occupancy?.value ?? null);
    const subjAdr = posOrNull(methodology.subject_adr?.value ?? null);
    const bOcc = m.available ? occFraction(m.occupancy.value) : null;
    const bAdr = m.available ? posOrNull(m.adr.value) : null;
    const mpi = subjOcc != null && bOcc != null ? (subjOcc / bOcc) * 100 : null;
    const ari = subjAdr != null && bAdr != null ? (subjAdr / bAdr) * 100 : null;
    return {
      method: m.method,
      label: m.label,
      series,
      details,
      current: {
        mpi,
        ari,
        trace:
          mpi != null || ari != null
            ? `subject STR TTM ÷ ${m.label} (${methodology.subject_period_label ?? 'TTM'}) × 100 — the current penetration against the selected benchmark`
            : null,
      },
    };
  }, [methodology, cols]);
  const penetrationRows = useMemo(
    () =>
      computePenetration({
        cols,
        subject: subject.series,
        comp: comp.series,
        market: market.series,
        indices: {
          mpi: indexPoints(str?.mpi_occupancy_index),
          ari: indexPoints(str?.ari_adr_index),
          rgi: indexPoints(str?.rgi_revpar_index),
        },
        subjectKeys,
        compKeys,
        targets: appliedScalarTargets,
        targetsByYear: appliedTargetsByYear,
        targetGateReason,
        benchmark,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cols, subject, comp, market, str, subjectKeys, compKeys, methodology, appliedTargetsByYear, benchmark, targetGateReason],
  );

  // FON-41 E-027 — reconcile this table to Financials → Projections. Every
  // figure is an engine output or a worker source tag, restated.
  const occSrc = useSource('starting_occupancy');
  const adrSrc = useSource('starting_adr');
  const occDispSrc = useSource('y1_occupancy_displacement_pct');
  const adrDispSrc = useSource('y1_adr_displacement_pct');
  const occGrowthSrc = useSource('occupancy_growth');
  const adrGrowthSrc = useSource('adr_growth');
  const reconciliation = useMemo(() => {
    const y0 = revYears[0];
    if (!y0) return null;
    const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
    const baseCal = num(calendarYears[0]);
    // The column that holds model Year 1 — the one Projections heads
    // "Base year (Year 1)".
    const colIdx = cols.columns.findIndex((c) => c.kind === 'forecast' && c.ordinal === 1);
    const col = colIdx >= 0 ? cols.columns[colIdx] : null;
    const iaOcc = col ? subject.series.occupancy[colIdx] ?? null : null;
    const iaAdr = col ? subject.series.adr[colIdx] ?? null : null;
    const srcOf = (s: ReturnType<typeof useSource>): ReconciliationSource | null =>
      s
        ? {
            source: s.source || null,
            filename: s.field?.filename ?? null,
            page: s.field?.page ?? null,
            asOf: s.field?.as_of ?? null,
            scope: s.field?.scope ?? null,
          }
        : null;
    return buildReconciliation({
      baseYear: { occupancy: y0.occupancy, adr: y0.adr },
      baseCalendarYear: baseCal,
      startingOccupancy: num(occSrc?.value),
      startingAdr: num(adrSrc?.value),
      occSource: srcOf(occSrc),
      adrSource: srcOf(adrSrc),
      y1OccDisplacement: num(occDispSrc?.value),
      y1AdrDisplacement: num(adrDispSrc?.value),
      occupancyGrowth: num(occGrowthSrc?.value),
      adrGrowth: num(adrGrowthSrc?.value),
      iaColumnYear: col ? col.calendarYear ?? col.label : 'Year 1',
      iaOccupancy: iaOcc,
      iaAdr: iaAdr,
      iaOrigin: iaColumnOrigin(colIdx >= 0 ? subject.sources[colIdx] : null),
      strOccupancy: occFraction(str?.subject_occupancy_pct),
      strAdr: posOrNull(str?.subject_adr_usd),
      strPeriodLabel: strPeriodLabel(str),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [outputs, cols, subject, str, occSrc, adrSrc, occDispSrc, adrDispSrc, occGrowthSrc, adrGrowthSrc]);

  // Empty state — no engine outputs and no market data.
  const hasAny = (s: YearSeries) => s.occupancy.some((v) => v != null) || s.adr.some((v) => v != null);
  if (!hasAny(subject.series) && !hasAny(comp.series) && !hasAny(market.series)) {
    return (
      <Card className="p-12 text-center">
        <div className="w-12 h-12 rounded-lg bg-ink-100 flex items-center justify-center mx-auto mb-3">
          <TrendingUp size={20} className="text-ink-500" />
        </div>
        <h3 className="text-[14px] font-semibold text-ink-900 mb-1">No Index Analysis data</h3>
        <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
          Index Analysis populates from the revenue engine, the STR Trend comp set and CBRE Horizons.
          Upload those reports to enable side-by-side subject vs comp set vs market comparison.
        </p>
        {loading && <div className="text-[10.5px] text-ink-400 mt-3 italic">Loading market data…</div>}
      </Card>
    );
  }

  const first = cols.columns[0]?.label ?? '';
  const last = cols.columns[cols.columns.length - 1]?.label ?? '';
  const marketTitle = market.segment
    ? `Market — ${market.segment.replace(/_/g, ' ')} segment`
    : 'Market';
  const marketBand = market.forecastProvenance
    ? `Market / ${market.forecastProvenance}`
    : 'no market forecast source';
  const subjectBand = 'Fondok underwriting (revenue engine)';
  const compBand = comp.forecastFilled ? COMP_FORECAST_GROWN_LABEL : 'no comp-set forecast source';
  // The table is the STR comp set (comp = subject ÷ STR index) — never titled
  // as a CoStar market table.
  const compTitle = 'STR Competitive Set';

  // FON-61 (61.3) — the comp set's own boundary: Year 1 is the STR TTM level
  // itself (the Year-1 benchmark), so there is no growth step between the
  // anchor and Year 1 to report; later years grow at the market assumption.
  const compBoundary: ForecastBoundary = comp.forecastFilled
    ? {
        comparable: false,
        note: 'Not applicable: Year 1 of the comp set is the STR trailing-twelve-month level itself (the Year-1 benchmark the index method uses), so there is no growth step from the TTM column. Later years grow at the market growth assumption.',
      }
    : { comparable: true };
  const marketBoundary: ForecastBoundary =
    market.forecastProvenance && cols.firstForecastIndex > 0 && market.sources[cols.firstForecastIndex - 1]?.kind === 'market_actual'
      ? {
          comparable: false,
          note:
            'Not applicable: the last historical market column is a reported actual while the first forecast column is the CBRE Horizons calendar-year forecast — a different basis. Growth from the second forecast year onward compares forecast to forecast.',
        }
      : { comparable: true };

  const compCaption = comp.forecastFilled
    ? 'the comp-set forecast is the STR TTM level grown at the market growth assumption (labelled as an assumption, with its source on hover).'
    : 'the comp set has no growth value to carry it forward, so its forecast is blank with the reason on hover.';
  const forecastCaption = market.forecastProvenance
    ? `Market forecast columns are the uploaded ${market.forecastProvenance} projection, placed by the report’s own calendar years; the subject forecast is the underwriting projection; ${compCaption}`
    : `No CBRE Horizons report is on this deal, so the Market forecast columns are blank with the reason on hover; the subject rows are the underwriting projection; ${compCaption}`;

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">Subject vs Competitive Set vs Market Index</h3>
          <p className="text-[11.5px] text-ink-500 mt-0.5">
            Historical and forecast index series, {first}–{last}
          </p>
        </div>
        <Badge tone="blue" uppercase>
          {cols.columns.length}-Year Series
        </Badge>
      </div>

      {!cols.hasCalendar && (
        <div className="text-[11.5px] text-ink-500" data-testid="index-no-close-date-note">
          {NO_CLOSE_DATE_NOTE}
        </div>
      )}

      <div className="text-[11.5px] text-ink-500 leading-relaxed">{forecastCaption}</div>

      <div className="text-[11px] text-ink-500 italic leading-relaxed">{NOTES_TEXT}</div>

      <ProjectionsReconciliation rec={reconciliation} />

      <IndexMethodologyPanel dealId={dealId} />

      <Card className="p-0 overflow-hidden">
        <IndexTable
          title="Subject Property"
          cols={cols}
          keys={subjectKeys}
          series={subject.series}
          sources={subject.sources}
          subject
          forecastBand={subjectBand}
          boundary={subject.boundary}
        />
      </Card>

      <Card className="p-0 overflow-hidden">
        <IndexTable
          title={compTitle}
          cols={cols}
          keys={compKeys}
          series={comp.series}
          sources={comp.sources}
          forecastBand={compBand}
          forecastReason={comp.forecastReason}
          boundary={compBoundary}
        />
      </Card>

      <Card className="p-0 overflow-hidden">
        <IndexTable
          title={marketTitle}
          cols={cols}
          keys={null}
          series={market.series}
          sources={market.sources}
          forecastBand={marketBand}
          forecastReason={market.forecastReason}
          boundary={marketBoundary}
        />
      </Card>

      <Card className="p-0 overflow-hidden">
        <PenetrationTable
          cols={cols}
          rows={penetrationRows}
          forecastBand={
            benchmark
              ? `indices against ${benchmark.label} (the selected benchmark)${hasTargets && byYearApplies ? ', at the analyst’s MPI / ARI targets where set (RGI derived)' : ''}; shares and variances against the STR comp set`
              : hasTargets && byYearApplies
                ? 'indices at the analyst’s MPI / ARI targets per year (RGI derived), else held at STR TTM; shares and variances where both sides are reported'
                : 'indices held at STR TTM; shares and variances where both sides are reported'
          }
        />
        {targetsNotApplied && (
          <div
            className="px-4 py-2 text-[11px] text-ink-500 italic border-t border-border"
            data-testid="index-penetration-targets-not-applied"
          >
            Targets set but not applied — turn on &lsquo;Use STR rates in the model&rsquo;
          </div>
        )}
      </Card>
    </div>
  );
}
