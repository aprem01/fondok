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
  api,
  isWorkerConnected,
  EngineOutputsResponse,
  HistoricalBaselineResponse,
} from '@/lib/api';
import { getEngineField, useEngineOutputs } from '@/lib/hooks/useEngineOutputs';
import { useDeal } from '@/lib/hooks/useDeal';
import { useHistoricalBaseline } from '@/lib/hooks/useHistoricalBaseline';

const HISTORICAL_YEARS = [2019, 2020, 2021, 2022, 2023, 2024];
const FORECAST_YEARS = [2025, 2026, 2027, 2028, 2029, 2030, 2031, 2032, 2033];
const ALL_YEARS = [...HISTORICAL_YEARS, ...FORECAST_YEARS];
/** The column where the forecast starts — the one growth step that spans the
 *  historical/forecast boundary, and the only one whose two sides can sit on
 *  different period bases (FON-61 61.3). */
const FIRST_FORECAST_INDEX = HISTORICAL_YEARS.length;

const NOTES_TEXT =
  'Notes: (1) Competitive-set performance is the STR/CoStar comp-set aggregate, recovered from the subject’s published penetration indices (MPI occupancy, ARI rate, RGI RevPAR) — comp = subject ÷ index — matching Market Overview. STR anonymizes per-property comp performance, so individual competitor Occ/ADR/RevPAR are not published. (2) The MPI/ARI/RGI penetration indices are the STR-published values, measured on STR’s trailing-twelve-month subject basis; the subject rows are the operating P&L series. (3) The forecast band names its own origin, and the caption above states the methodology behind it. (4) The first forecast year shows no growth rate when the last historical column is a different period basis from the model’s Base Year — a growth rate across two bases is not a modelled assumption. (5) Blank cells are unreported data, not zero.';

interface RevenueYear {
  year: number;
  occupancy: number;
  adr: number;
  revpar: number;
}

/**
 * FON-61 (61.3) — is the last historical column comparable to the first
 * forecast column?
 *
 * Sam: "Index Analysis currently shows 2025: Occupancy Growth 13.3% … produced
 * by comparing the 2025 forecast against 2024 calendar-year historical
 * performance … display N/A for the first forecast-year growth rows."
 *
 * The subject's 2024 column is filled from the historical-baseline engine when
 * a full-year P&L was extracted — a FISCAL YEAR actual. The 2025 column is the
 * revenue engine's ``years[1]``, grown off ``years[0]`` — the Base Year, which
 * is a TRAILING TWELVE MONTHS window. Dividing one by the other is a growth
 * rate between two different period bases, presented as a modelled assumption.
 *
 * The one case where they DO agree is the deal with no multi-year P&L: the
 * anchor column then falls back to revenue ``years[0]`` itself, so the step is
 * the model's own Base Year → Year 2 growth and is a real number.
 */
export type ForecastBoundary =
  | { comparable: true }
  | { comparable: false; note: string };

const BOUNDARY_COMPARABLE: ForecastBoundary = { comparable: true };
const BOUNDARY_FY_VS_TTM: ForecastBoundary = {
  comparable: false,
  note:
    'Not applicable: the 2024 column is a full fiscal year of actuals while the ' +
    'model grows off a trailing-twelve-month Base Year. A growth rate across two ' +
    'different period bases would not be a modelled assumption. Growth from 2026 ' +
    'onward compares forecast to forecast.',
};
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
    // STR penetration indices (subject ÷ comp-set). May arrive as a ratio
    // (1.098) or as index points (109.8); buildCompSeries normalizes both.
    mpi_occupancy_index?: number | null;
    ari_adr_index?: number | null;
    rgi_revpar_index?: number | null;
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

// FON-61 — a metric that arrives as 0, negative, null, or non-finite is "not
// reported", not a real value: an operating hotel never books 0% occupancy or a
// $0 ADR. Coerce all of these to null so the UI shows blank/N-A and never
// fabricates a zero (a populated ADR next to "Occupancy 0.0% / Occupied 0" was
// the exact defect). Applied to every series fill, subject and comp.
function posOrNull(v: number | null | undefined): number | null {
  return typeof v === 'number' && isFinite(v) && v > 0 ? v : null;
}

// Normalize an STR penetration index to a plain ratio. STR/CoStar publish these
// either as a ratio (1.098) or as index points (109.8); anything > 3 is points.
function indexRatio(idx: number | null | undefined): number | null {
  if (idx == null || idx <= 0) return null;
  return idx > 3 ? idx / 100 : idx;
}

// Same normalization, expressed in index points (109.8) for display.
function indexPoints(idx: number | null | undefined): number | null {
  const r = indexRatio(idx);
  return r == null ? null : r * 100;
}

// Normalize an occupancy figure to a 0..1 fraction (sources vary between 0.72
// and 72). Values ≤ 1.5 are already fractions; larger are whole percents.
function occFraction(v: number | null | undefined): number | null {
  const p = posOrNull(v);
  if (p == null) return null;
  return p > 1.5 ? p / 100 : p;
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
): YearSeries & { boundary: ForecastBoundary } {
  const series: YearSeries = {
    occupancy: ALL_YEARS.map(() => null),
    adr: ALL_YEARS.map(() => null),
    revpar: ALL_YEARS.map(() => null),
  };

  // Historical years — pull from historical_baseline.years[] by matching
  // fiscal_year to the column year. RevPAR falls back to occ × ADR when
  // the baseline didn't ship it directly (same identity the engine uses).
  if (baseline?.years && baseline.years.length > 0) {
    const byYear = new Map<number, (typeof baseline.years)[number]>();
    for (const y of baseline.years) byYear.set(y.fiscal_year, y);
    HISTORICAL_YEARS.forEach((yr, i) => {
      const row = byYear.get(yr);
      if (!row) return;
      // Validate each metric independently — a P&L year can ship a real ADR
      // while the occupancy cell was blank (stored as 0). Coerce non-positive
      // to null so the column reads blank, not a fabricated 0 (FON-61 d).
      series.occupancy[i] = occFraction(row.occupancy);
      series.adr[i] = posOrNull(row.adr);
      const rp = posOrNull(row.revpar);
      series.revpar[i] =
        rp ??
        (series.occupancy[i] != null && series.adr[i] != null
          ? series.occupancy[i]! * series.adr[i]!
          : null);
    });
  }

  // Live deal: revenue engine years[] starts at the post-T-12 stabilized
  // year and projects forward. Use it for forecast columns only; the
  // anchor (latest historical column) is left to the baseline above
  // when present, otherwise revenue years[0] serves as the fallback
  // anchor (preserves pre-baseline behavior for deals without P&L docs).
  const revYears = getEngineField<RevenueYear[]>(outputs, 'revenue', 'years');
  // FON-61 (61.3) — default to comparable: with no revenue forecast at all
  // there is no forecast column to refuse, and the fallback below sets the
  // anchor to the Base Year itself, which IS the model's own basis.
  let boundary: ForecastBoundary = BOUNDARY_COMPARABLE;
  if (revYears && revYears.length > 0) {
    const anchorIdx = HISTORICAL_YEARS.length - 1;
    if (series.occupancy[anchorIdx] == null) {
      series.occupancy[anchorIdx] = occFraction(revYears[0].occupancy);
      series.adr[anchorIdx] = posOrNull(revYears[0].adr);
      series.revpar[anchorIdx] = posOrNull(revYears[0].revpar);
    } else {
      // The anchor came from the multi-year P&L baseline — a fiscal year —
      // while the forecast grows off the TTM Base Year, which this table never
      // shows. The first forecast-year growth spans the two.
      boundary = BOUNDARY_FY_VS_TTM;
    }
    for (let i = 1; i < revYears.length && i <= FORECAST_YEARS.length; i++) {
      const idx = anchorIdx + i;
      series.occupancy[idx] = occFraction(revYears[i].occupancy);
      series.adr[idx] = posOrNull(revYears[i].adr);
      series.revpar[idx] = posOrNull(revYears[i].revpar);
    }
    // FON-61 — extend the forecast to the full window. The revenue engine
    // projects a finite horizon (often short of the hold's exit year); beyond
    // it, carry the stabilized operation forward — occupancy held flat once
    // stabilized, ADR/RevPAR grown at the trailing rate — so the index series
    // covers the whole hold instead of trailing off into blanks.
    const lastIdx = Math.min(anchorIdx + revYears.length - 1, ALL_YEARS.length - 1);
    const aPrev = series.adr[lastIdx - 1];
    const aLast = series.adr[lastIdx];
    const g =
      aPrev != null && aLast != null && aPrev > 0
        ? Math.max(0, Math.min(0.08, aLast / aPrev - 1))
        : 0.03;
    for (let idx = lastIdx + 1; idx < ALL_YEARS.length; idx++) {
      const occPrev = series.occupancy[idx - 1];
      const adrPrev = series.adr[idx - 1];
      if (occPrev == null || adrPrev == null) break;
      series.occupancy[idx] = occPrev; // stabilized occupancy holds flat
      series.adr[idx] = adrPrev * (1 + g);
      series.revpar[idx] = occPrev * series.adr[idx]!;
    }
  }
  return { ...series, boundary };
}

// Build the CoStar comp-set year series from the market-data envelope. The
// subject series is passed so the forecast can be carried at a held-flat
// penetration when no CBRE Horizons forecast was uploaded (see below).
function buildCompSeries(
  marketData: MarketDataAPIResponse | null,
  subjectSeries?: YearSeries | null,
): YearSeries {
  const series: YearSeries = {
    occupancy: ALL_YEARS.map(() => null),
    adr: ALL_YEARS.map(() => null),
    revpar: ALL_YEARS.map(() => null),
  };

  // The STR/CoStar TTM report anonymizes per-property comp performance
  // (compset[i].occupancy_pct/adr/revpar all come back null), but it publishes
  // the subject's penetration index vs the comp-set aggregate — MPI (occupancy),
  // ARI (ADR), RGI (RevPAR), each = subject ÷ comp. So the blended comp-set
  // metric is recoverable: comp = subject ÷ index. This is the SAME derivation
  // Market Overview uses (MarketTab.deriveCompSet), so both views consume one
  // dataset (FON-61 a). It lands on the anchor — the latest historical year,
  // which the TTM window most closely represents. STR does not publish
  // prior-year comp performance, so earlier historical comp columns stay blank
  // (FON-61 b: populate only where docs support it).
  const anchorIdx = HISTORICAL_YEARS.length - 1;
  const str = marketData?.str_trend;
  const occR = indexRatio(str?.mpi_occupancy_index);
  const adrR = indexRatio(str?.ari_adr_index);
  const revparR = indexRatio(str?.rgi_revpar_index);
  if (str) {
    // Anchor the comp on the STR report's own TTM subject figures ÷ the
    // penetration index — IDENTICAL to MarketTab.deriveCompSet — so the comp
    // row here equals the blended comp set shown in Market Overview to the
    // dollar (FON-61 a: the two views must agree). The penetration index
    // itself (MPI/ARI/RGI) is shown from the STR-published values, not
    // recomputed against the operating subject series, since STR measures it
    // on its own TTM subject basis (see PenetrationTable).
    const subjOcc = occFraction(str.subject_occupancy_pct);
    const subjAdr = posOrNull(str.subject_adr_usd);
    const subjRevpar = posOrNull(str.subject_revpar_usd);
    const compOcc = subjOcc != null && occR != null ? subjOcc / occR : null;
    const compAdr = subjAdr != null && adrR != null ? subjAdr / adrR : null;
    const compRevpar =
      subjRevpar != null && revparR != null
        ? subjRevpar / revparR
        : compOcc != null && compAdr != null
          ? compOcc * compAdr
          : null;
    if (compOcc != null) series.occupancy[anchorIdx] = compOcc;
    if (compAdr != null) series.adr[anchorIdx] = compAdr;
    if (compRevpar != null) series.revpar[anchorIdx] = compRevpar;
  }

  // Forecast — a real CBRE Horizons projection when one was uploaded.
  const cbreYears = marketData?.cbre_horizons?.years ?? [];
  for (const y of cbreYears) {
    const fIdx = (y.year_index ?? 0) - 1;
    if (fIdx < 0 || fIdx >= FORECAST_YEARS.length) continue;
    const idx = HISTORICAL_YEARS.length + fIdx;
    const occ = occFraction(y.occupancy_pct);
    if (occ != null) series.occupancy[idx] = occ;
    const adr = posOrNull(y.adr_usd);
    if (adr != null) series.adr[idx] = adr;
    const rp = posOrNull(y.revpar_usd);
    if (rp != null) series.revpar[idx] = rp;
  }

  if (cbreYears.length > 0) {
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
  } else if (subjectSeries) {
    // No CBRE doc — carry the comp set forward by holding its trailing-twelve-
    // month penetration to the subject flat: grow the anchor comp-set by the
    // subject's own (doc-based revenue-engine) forecast growth. This keeps the
    // penetration/index columns populated across the forecast (FON-61 e) with a
    // transparent, standard assumption rather than a wall of blanks. Penetration
    // then equals the anchor relationship, held flat.
    const sOccA = subjectSeries.occupancy[anchorIdx];
    const sAdrA = subjectSeries.adr[anchorIdx];
    const sRevA = subjectSeries.revpar[anchorIdx];
    const cOccA = series.occupancy[anchorIdx];
    const cAdrA = series.adr[anchorIdx];
    const cRevA = series.revpar[anchorIdx];
    for (let idx = HISTORICAL_YEARS.length; idx < ALL_YEARS.length; idx++) {
      if (series.occupancy[idx] == null && cOccA != null && sOccA && subjectSeries.occupancy[idx] != null) {
        series.occupancy[idx] = cOccA * (subjectSeries.occupancy[idx]! / sOccA);
      }
      if (series.adr[idx] == null && cAdrA != null && sAdrA && subjectSeries.adr[idx] != null) {
        series.adr[idx] = cAdrA * (subjectSeries.adr[idx]! / sAdrA);
      }
      if (series.revpar[idx] == null) {
        if (cRevA != null && sRevA && subjectSeries.revpar[idx] != null) {
          series.revpar[idx] = cRevA * (subjectSeries.revpar[idx]! / sRevA);
        } else if (series.occupancy[idx] != null && series.adr[idx] != null) {
          series.revpar[idx] = series.occupancy[idx]! * series.adr[idx]!;
        }
      }
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

// Penetration index = subject ÷ comp-set × 100. 100 = at par with the comp set;
// >100 = the subject outperforms. Null when either side is unreported.
function pctIndex(subj: number | null, comp: number | null): number | null {
  if (subj == null || comp == null || comp === 0) return null;
  return (subj / comp) * 100;
}
function fmtIndex(v: number | null): string {
  if (v == null) return '—';
  return v.toFixed(1);
}
// Signed dollar variance, e.g. "$12" / "−$5". Null → em dash.
function fmtVarDollar(v: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '' : '−'}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}
// Share ratio (0..1) → percent with 2 decimals. Null → em dash.
function fmtShare(v: number | null): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(2)}%`;
}
// Revenue opportunity at par — only a positive gap is an opportunity; ≤0 or
// unreported renders "—" (never a fabricated number).
function fmtOppty(v: number | null): string {
  if (v == null || v <= 0) return '—';
  return `$${Math.round(v).toLocaleString('en-US')}`;
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
  keys: number | null;
  series: YearSeries;
  /** Subject rows carry the underwriting forecast — forecast-year Occupancy /
   *  ADR / RevPAR cells render in assumption blue so a forecast is never read
   *  as an STR actual (canonical Index Analysis). Comp-set rows stay green. */
  subject?: boolean;
  /** FON-60 (60.4) — WHOSE forecast the forecast band is. The truth was in
   *  footnote 3; it belongs on the column band. */
  forecastOrigin: string;
  /** FON-61 (61.3) — whether the first forecast column's growth compares two
   *  periods on the same basis. When it does not, that one cell is N/A. */
  boundary: ForecastBoundary;
}

function IndexTable({
  title,
  keys,
  series,
  subject = false,
  forecastOrigin,
  boundary,
}: TableProps) {
  // Historical → green (grounded actual); subject forecast → assumption blue.
  const metricClass = (i: number): string =>
    subject && i >= HISTORICAL_YEARS.length ? 'text-brand-700' : 'text-success-700';
  // Derived rows. When the room count is unknown, Available/Occupied Rooms are
  // N/A (null) rather than 0 — a 0 would misread as "no rooms available"
  // (FON-61 c). Occupied is also null wherever occupancy itself is unreported.
  const kv = typeof keys === 'number' && keys > 0 ? keys : null;
  const days = ALL_YEARS.map((y) => (isLeapYear(y) ? 366 : 365));
  const available = days.map((d) => (kv != null ? d * kv : null));
  const occupied = ALL_YEARS.map((_, i) => {
    const occ = series.occupancy[i];
    const av = available[i];
    if (occ == null || av == null) return null;
    return Math.round(av * occ);
  });
  // FON-61 (61.3) — the growth step into the first forecast year is REFUSED
  // when its two sides sit on different period bases. Refused is not the same
  // as missing: the cell says N/A and names the mismatch on hover, rather than
  // printing a number that reads as a modelled growth assumption. Every later
  // year is forecast-over-forecast and is untouched.
  const refuseAtBoundary = !boundary.comparable;
  const growthAt = (arr: (number | null)[], i: number): number | null =>
    i === 0 ? null : i === FIRST_FORECAST_INDEX && refuseAtBoundary ? null : growth(arr[i], arr[i - 1]);
  const boundaryNote = boundary.comparable ? null : boundary.note;
  const growthCell = (v: number | null, i: number) =>
    i === FIRST_FORECAST_INDEX && refuseAtBoundary ? (
      <span className="text-ink-400" title={boundaryNote ?? undefined}>
        N/A
      </span>
    ) : (
      <GrowthCell value={v} />
    );
  const occGrowth = ALL_YEARS.map((_, i) => growthAt(series.occupancy, i));
  const adrGrowth = ALL_YEARS.map((_, i) => growthAt(series.adr, i));
  const revparGrowth = ALL_YEARS.map((_, i) => growthAt(series.revpar, i));

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
            {/* FON-60 (60.4) — the band says WHOSE forecast this is. Absent a
                real CBRE Horizons report the series is Fondok-derived (the
                comp-set relationship carried forward), which used to be
                admitted only in footnote 3. */}
            <th
              colSpan={FORECAST_YEARS.length}
              className="text-center font-semibold uppercase tracking-wide text-[10.5px] text-brand-700 bg-brand-50/40 border-l border-border px-2 py-2"
            >
              Forecast — {forecastOrigin}
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
                {fmtInt(kv)}
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
            cells={series.occupancy.map((v, i) => (
              <span className={metricClass(i)}>{fmtPct(v, 1)}</span>
            ))}
            stickyL={stickyL}
          />
          <Row
            label="ADR"
            cells={series.adr.map((v, i) => (
              <span className={metricClass(i)}>{fmtDollar(v)}</span>
            ))}
            stickyL={stickyL}
            zebra
          />
          <Row
            label="RevPAR"
            cells={series.revpar.map((v, i) => (
              <span className={metricClass(i)}>{fmtDollar(v)}</span>
            ))}
            stickyL={stickyL}
          />
          <Row
            label="Occupancy Growth"
            cells={occGrowth.map(growthCell)}
            stickyL={stickyL}
            zebra
          />
          <Row label="ADR Growth" cells={adrGrowth.map(growthCell)} stickyL={stickyL} />
          <Row
            label="RevPAR Growth"
            cells={revparGrowth.map(growthCell)}
            stickyL={stickyL}
            zebra
          />
        </tbody>
      </table>
    </div>
  );
}

// Subject-vs-comp penetration index (MPI / ARI / RGI) (FON-61 e). For live
// deals the values are the STR-published indices — the canonical penetration,
// measured by STR on its TTM subject basis, so the MPI/ARI/RGI shown reconcile
// exactly to the analyst's STR report. STR only publishes them for the TTM
// window (the anchor year); we hold them flat across the forecast wherever the
// subject series is populated (standard assumption absent a market forecast)
// and leave pre-anchor history blank (STR anonymizes prior-year comp data).
// Absent published indices (the demo), fall back to computing subject ÷ comp.
// Blank — never a fabricated 0 or 100 — where unsupported.
function PenetrationTable({
  subject,
  comp,
  indices,
  subjectKeys,
  compKeys,
  forecastOrigin,
}: {
  subject: YearSeries;
  comp: YearSeries;
  indices?: { mpi: number | null; ari: number | null; rgi: number | null } | null;
  subjectKeys: number | null;
  compKeys: number | null;
  forecastOrigin: string;
}) {
  const anchorIdx = HISTORICAL_YEARS.length - 1;
  const rowFor = (
    published: number | null,
    subjArr: (number | null)[],
    compArr: (number | null)[],
  ): (number | null)[] =>
    ALL_YEARS.map((_, i) => {
      if (published != null) {
        if (i < anchorIdx) return null; // no STR comp history before the TTM anchor
        if (i === anchorIdx) return published;
        return subjArr[i] != null ? published : null; // hold flat across forecast
      }
      return pctIndex(subjArr[i], compArr[i]);
    });
  const mpi = rowFor(indices?.mpi ?? null, subject.occupancy, comp.occupancy);
  const ari = rowFor(indices?.ari ?? null, subject.adr, comp.adr);
  const rgi = rowFor(indices?.rgi ?? null, subject.revpar, comp.revpar);

  // Analytical penetration rows. Each renders only where BOTH inputs are
  // reported — otherwise "—" (never a fabricated number). MPI/ARI/RGI come
  // from the STR path above; these derive from the subject + comp series and
  // the room counts, matching the canonical Penetration / Index Analysis group.
  const days = ALL_YEARS.map((y) => (isLeapYear(y) ? 366 : 365));
  // FON-60 (60.5) — there is no ``RGI Growth (pts)`` row. STR publishes the
  // penetration indices for the TTM window only, so ``rgi`` is held flat across
  // every forecast column (see ``rowFor``): the row's forecast half was a wall
  // of "+0.0" that read as a modelled assumption of zero index movement. The
  // LEVEL rows (MPI / ARI / RGI) stay. Penetration-improvement assumptions are
  // post-MVP; when they land, the growth row comes back with real movement.
  const revparVariance = ALL_YEARS.map((_, i) =>
    subject.revpar[i] != null && comp.revpar[i] != null ? subject.revpar[i]! - comp.revpar[i]! : null,
  );
  const kv = subjectKeys != null && subjectKeys > 0 ? subjectKeys : null;
  const ckv = compKeys != null && compKeys > 0 ? compKeys : null;
  const fairShare = ALL_YEARS.map(() => (kv != null && ckv != null ? kv / ckv : null));
  const actualShare = ALL_YEARS.map((_, i) => {
    const so = subject.occupancy[i];
    const co = comp.occupancy[i];
    if (kv == null || ckv == null || so == null || co == null || co <= 0) return null;
    return (kv * so) / (ckv * co);
  });
  const revOppty = ALL_YEARS.map((_, i) => {
    if (kv == null || subject.revpar[i] == null || comp.revpar[i] == null) return null;
    return (comp.revpar[i]! - subject.revpar[i]!) * kv * days[i];
  });
  const stickyL = 'sticky left-0 bg-card z-10 border-r border-border';

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[12px] border-collapse" style={{ minWidth: 1400 }}>
        <thead>
          <tr className="text-ink-700 border-b border-border">
            <th
              className={cn(
                stickyL,
                'text-left font-semibold uppercase tracking-wide text-[11px] px-3 py-2',
              )}
            >
              Penetration Index
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
              Forecast — {forecastOrigin}
            </th>
          </tr>
          <tr className="text-ink-500 text-[10.5px] border-b border-border">
            <th className={cn(stickyL, 'text-left font-medium px-3 py-1.5')}>
              Subject ÷ Comp × 100
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
          <Row label="Occupancy Index (MPI)" cells={mpi.map((v) => fmtIndex(v))} stickyL={stickyL} />
          <Row
            label="ADR Index (ARI)"
            cells={ari.map((v) => fmtIndex(v))}
            stickyL={stickyL}
            zebra
          />
          <Row label="RevPAR Index (RGI)" cells={rgi.map((v) => fmtIndex(v))} stickyL={stickyL} />
          <Row
            label="RevPAR Variance to Comp Set"
            cells={revparVariance.map((v) => fmtVarDollar(v))}
            stickyL={stickyL}
            zebra
          />
          <Row label="Fair Share of Supply" cells={fairShare.map((v) => fmtShare(v))} stickyL={stickyL} zebra />
          <Row
            label="Actual Share of Demand"
            cells={actualShare.map((v) => fmtShare(v))}
            stickyL={stickyL}
          />
          <Row
            label="Revenue Opportunity at Par"
            cells={revOppty.map((v) => fmtOppty(v))}
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
}: {
  dealId: string;
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
    // Route through the authenticated api helper (attaches Authorization +
    // X-Tenant-Id) — exactly as Market Overview does. A raw, tenant-unscoped
    // fetch 404s ("deal not found"), which is why the comp-set + penetration
    // tables were blank here while Market Overview had the same data (FON-61).
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
    return () => ctrl.abort();
  }, [dealId, liveMode]);

  // Null (not 0) when the room count is unknown, so the table renders N/A
  // rather than a misleading "Available Rooms 0 / Occupied Rooms 0" (FON-61 c).
  const subjectKeys: number | null = deal?.keys && deal.keys > 0 ? deal.keys : null;

  const subjectSeries = useMemo(
    () => buildSubjectSeries(outputs, historicalBaseline),
    [outputs, historicalBaseline],
  );
  // FON-60 (60.4) — a real CBRE Horizons projection, or Fondok's own carry-
  // forward. ``buildCompSeries`` already branches on exactly this; the band
  // header and the caption now say which branch was taken.
  const hasCbreForecast = (marketData?.cbre_horizons?.years ?? []).length > 0;
  const forecastOrigin = hasCbreForecast ? 'CBRE Horizons' : 'Fondok-derived';
  // FON-61 (61.3) — the comp set's own boundary. Its 2024 column is always the
  // STR trailing-twelve-month blend. With a CBRE report, 2025 is that report's
  // calendar-year forecast — a different basis. Without one, the comp forecast
  // is the anchor scaled by the SUBJECT's growth, so it inherits exactly the
  // subject's comparability, and the refusal (when there is one) is about the
  // subject's own mismatched step.
  const compBoundary: ForecastBoundary = hasCbreForecast
    ? {
        comparable: false,
        note:
          'Not applicable: the 2024 comp-set column is the STR trailing-twelve-month blend while 2025 is the CBRE Horizons calendar-year forecast. Growth from 2026 onward compares forecast to forecast.',
      }
    : subjectSeries.boundary.comparable
      ? BOUNDARY_COMPARABLE
      : {
          comparable: false,
          note:
            'Not applicable: absent a CBRE Horizons forecast the comp set is carried forward at the subject’s growth, and the subject’s own first forecast step spans a fiscal-year actual and a trailing-twelve-month Base Year. Growth from 2026 onward compares forecast to forecast.',
        };
  const forecastCaption = hasCbreForecast
    ? 'Forecast columns are the uploaded CBRE Horizons projection for this submarket; years beyond its horizon carry the last known RevPAR forward at 3.0%.'
    : 'Forecast columns are Fondok-derived: no CBRE Horizons report is on this deal, so the comp set and the penetration indices are carried forward at the trailing-twelve-month relationship, and the subject rows are the underwriting projection.';
  const compSeries = useMemo(
    () => buildCompSeries(marketData, subjectSeries),
    [marketData, subjectSeries],
  );

  // Comp-set "keys" row = total comp-set room count. Source priority:
  //   1. Sum of the named `compset[i].keys` roster — this is exactly what
  //      Market Overview's blended comp-set row shows, so the two views agree
  //      on the key count (FON-61 a). It is also the concrete, verifiable
  //      figure (5 named properties on the live deals).
  //   2. `str_trend.total_keys` rollup — only when the roster is empty. The
  //      extracted rollup has proven unreliable (e.g. 1011 for a 424-key,
  //      5-property comp set), so the roster wins whenever it exists.
  //   3. null — the Available/Occupied-Rooms rows render N/A, never a
  //      fabricated 0, and the empty-state copy tells the user to upload an
  //      STR Trend report (FON-61 c).
  const compKeys: number | null = (() => {
    const fromRoster = (marketData?.str_trend?.compset ?? []).reduce(
      (acc, row) => acc + (typeof row.keys === 'number' && row.keys > 0 ? row.keys : 0),
      0,
    );
    if (fromRoster > 0) return fromRoster;
    const fromRollup = marketData?.str_trend?.total_keys;
    return typeof fromRollup === 'number' && fromRollup > 0 ? fromRollup : null;
  })();

  // Empty state — no engine outputs and no market data.
  const subjectHasAny =
    subjectSeries.occupancy.some((v) => v != null) ||
    subjectSeries.adr.some((v) => v != null);
  const compHasAny =
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

      {/* FON-60 (60.4) — the methodology line sits with the tables, not only in
          the footnote block, so the forecast's origin is read before the
          numbers rather than after them. */}
      <div className="text-[11.5px] text-ink-500 leading-relaxed">{forecastCaption}</div>

      <div className="text-[11px] text-ink-500 italic leading-relaxed">{NOTES_TEXT}</div>

      <Card className="p-0 overflow-hidden">
        <IndexTable
          title="Subject Property"
          keys={subjectKeys}
          series={subjectSeries}
          subject
          forecastOrigin={forecastOrigin}
          boundary={subjectSeries.boundary}
        />
      </Card>

      <Card className="p-0 overflow-hidden">
        <IndexTable
          title="CoStar Market — Competitive Set"
          keys={compKeys}
          series={compSeries}
          forecastOrigin={forecastOrigin}
          boundary={compBoundary}
        />
      </Card>

      <Card className="p-0 overflow-hidden">
        <PenetrationTable
          subject={subjectSeries}
          comp={compSeries}
          subjectKeys={subjectKeys}
          compKeys={compKeys}
          forecastOrigin={forecastOrigin}
          indices={{
            mpi: indexPoints(marketData?.str_trend?.mpi_occupancy_index),
            ari: indexPoints(marketData?.str_trend?.ari_adr_index),
            rgi: indexPoints(marketData?.str_trend?.rgi_revpar_index),
          }}
        />
      </Card>
    </div>
  );
}
