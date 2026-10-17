/**
 * Index Analysis — the pure model behind ``IndexAnalysisSection`` (FON-94 /
 * FON-95). No React, no fetching: every function here takes engine outputs
 * and extracted market data and returns the per-column series the tables
 * print, each cell carrying the source it came from. Nothing in this file
 * invents a number — a cell with no source is ``null`` and renders "—".
 *
 * Column calendar (FON-94): the forecast columns are EXACTLY the revenue
 * engine's ``projection_calendar_years`` (Year 1 = the calendar year of the
 * first operating month after the acquisition close), so the same calendar
 * year sits in the same column as the Future P&L. Historical columns are the
 * six years before Year 1. With no close date the forecast columns carry the
 * ordinal ("Year 1") and the historical columns carry the years the documents
 * themselves report — never a guessed year.
 */

import type { EngineOutputsResponse, HistoricalBaselineResponse, IndexCompSetYear, IndexFigure } from '@/lib/api';
import { getEngineField } from '@/lib/hooks/useEngineOutputs';
import { toIndexPoints } from '@/lib/indexTarget';

// ─────────────────────────── market-data envelope (worker) ───────────────────────────
// Mirrors apps/worker/app/api/documents.py MarketDataResponse.

export interface CbreYear {
  year_index: number;
  /** FON-94 — the calendar year the forecast ordinal stands for; null when
   *  the report gave no way to place it (the UI then refuses to place it). */
  calendar_year?: number | null;
  occupancy_pct: number | null;
  adr_usd: number | null;
  revpar_usd: number | null;
  revpar_growth_pct: number | null;
  page?: number | null;
}
export interface CompSetEntryAPI {
  name?: string | null;
  keys?: number | null;
  occupancy_pct?: number | null;
  adr_usd?: number | null;
  revpar_usd?: number | null;
  /** 'closed' when any STR report on the deal marks the hotel closed (the
   *  worker's market_comp_set rule) — listed, but never counted in keys. */
  status?: 'active' | 'closed' | null;
}
export interface StrSubjectAnnualAPI {
  calendar_year: number;
  occupancy_pct?: number | null;
  adr_usd?: number | null;
  revpar_usd?: number | null;
  page?: number | null;
}
export interface StrTrendAPI {
  subject_occupancy_pct?: number | null;
  subject_adr_usd?: number | null;
  subject_revpar_usd?: number | null;
  // STR penetration indices (subject ÷ comp-set). May arrive as a ratio
  // (1.098) or as index points (109.8); indexRatio normalizes both.
  mpi_occupancy_index?: number | null;
  ari_adr_index?: number | null;
  rgi_revpar_index?: number | null;
  report_month?: string | null;
  report_year?: number | null;
  /** Sam #6 — the subject TTM's reporting period end (ISO) for the extraction
   *  that supplied it, how it was resolved, and that document. */
  ttm_period_end?: string | null;
  ttm_period_end_basis?: string | null;
  ttm_doc_name?: string | null;
  doc_name?: string | null;
  comp_set_size?: number | null;
  total_keys?: number | null;
  compset?: CompSetEntryAPI[];
  subject_annual?: StrSubjectAnnualAPI[];
}
export interface CbreHorizonsAPI {
  submarket?: string | null;
  chain_scale?: string | null;
  publication_date?: string | null;
  doc_name?: string | null;
  years?: CbreYear[];
}
export interface MarketHistoryPointAPI {
  calendar_year?: number | null;
  period?: 'annual' | 'ttm' | 'undated';
  segment?: string | null;
  occupancy_pct?: number | null;
  adr_usd?: number | null;
  revpar_usd?: number | null;
  doc_type?: string | null;
  doc_name?: string | null;
  page?: number | null;
  fields?: string[];
}
export interface MarketDataAPIResponse {
  deal_id: string;
  str_trend?: StrTrendAPI | null;
  cbre_horizons?: CbreHorizonsAPI | null;
  market_history?: MarketHistoryPointAPI[];
}

export interface RevenueYear {
  year: number;
  occupancy: number;
  adr: number;
  revpar: number;
}

// ─────────────────────────── columns (FON-94) ───────────────────────────

export type IaColumnKind = 'historical' | 'forecast';

export interface IaColumn {
  /** Stable React key. */
  key: string;
  kind: IaColumnKind;
  /** The calendar year this column stands for; null when the deal has no
   *  acquisition close date (forecast) or the column is the STR TTM anchor. */
  calendarYear: number | null;
  /** Model year ordinal (1..hold) for forecast columns; null for historical. */
  ordinal: number | null;
  /** Header text: "2025", "Year 1" (no close date), "STR TTM". */
  label: string;
}

/** Historical columns shown before Year 1 when the calendar is known. */
export const HISTORICAL_COLUMN_COUNT = 6;

/** The revenue engine projects on a flat 365-day year
 *  (apps/worker/app/engines/revenue.py DAYS_PER_YEAR). Forecast columns use
 *  the same count so Occupied Rooms foots to the Future P&L; historical
 *  columns with a known calendar year use that year's real day count. */
export const DAYS_PER_PROJECTION_YEAR = 365;

/** Same convention as ProjectionsSection.NO_CLOSE_DATE_NOTE — the one line
 *  shown when the deal has no acquisition close date. */
export const NO_CLOSE_DATE_NOTE = 'Set the acquisition date on CAPEX to map years to calendar years';

export function isLeapYear(y: number): boolean {
  return (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
}

export function daysForColumn(col: IaColumn): number {
  if (col.kind === 'forecast') return DAYS_PER_PROJECTION_YEAR;
  return col.calendarYear != null ? (isLeapYear(col.calendarYear) ? 366 : 365) : 365;
}

export interface DeriveColumnsInput {
  /** ``revenue.projection_calendar_years`` — empty / absent with no close date. */
  calendarYears: readonly number[] | null | undefined;
  /** ``revenue.years.length`` — the model horizon (hold years). */
  forecastCount: number;
  /** Calendar years the documents report for the subject (STR annual + P&L
   *  fiscal years). Used ONLY without a close date, so the historical
   *  columns are real document years rather than guessed ones. */
  documentYears: readonly number[];
  /** Whether an STR TTM comp-set anchor exists and needs a column when no
   *  other historical column does. */
  hasStrTtm?: boolean;
}

export interface IaColumns {
  columns: IaColumn[];
  hasCalendar: boolean;
  /** Index of the first forecast column, or ``columns.length`` when none. */
  firstForecastIndex: number;
}

const finiteInt = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? Math.trunc(v) : null;

/**
 * FON-94 — the year columns, derived from the acquisition close calendar.
 *
 *  • With a calendar: historical = Year 1 − 6 … Year 1 − 1; forecast = the
 *    engine's calendar years, exactly the model horizon. Nothing beyond it.
 *  • Without one: forecast columns are "Year N"; historical columns are the
 *    last six calendar years the documents report (or one "STR TTM" anchor
 *    column when only the STR comp set exists).
 */
export function deriveIndexColumns(i: DeriveColumnsInput): IaColumns {
  const cal = (i.calendarYears ?? []).map(finiteInt).filter((y): y is number => y != null);
  const forecastCount = Math.max(0, Math.trunc(i.forecastCount));
  const hasCalendar = cal.length > 0 && forecastCount > 0;
  const columns: IaColumn[] = [];

  if (hasCalendar) {
    const base = cal[0];
    for (let k = HISTORICAL_COLUMN_COUNT; k >= 1; k--) {
      const y = base - k;
      columns.push({ key: `h-${y}`, kind: 'historical', calendarYear: y, ordinal: null, label: String(y) });
    }
    for (let n = 0; n < forecastCount; n++) {
      // The engine emits one calendar year per row (revenue.projection_calendar_years).
      // A row the engine did not label is NOT extrapolated: the column stays an
      // ordinal ("Year N") with no calendar year rather than a guessed figure.
      const y = cal[n] ?? null;
      columns.push({
        key: `f-${n + 1}`,
        kind: 'forecast',
        calendarYear: y,
        ordinal: n + 1,
        label: y != null ? String(y) : `Year ${n + 1}`,
      });
    }
  } else {
    const docYears = Array.from(new Set(i.documentYears.map(finiteInt).filter((y): y is number => y != null)))
      .sort((a, b) => a - b)
      .slice(-HISTORICAL_COLUMN_COUNT);
    if (docYears.length > 0) {
      for (const y of docYears) {
        columns.push({ key: `h-${y}`, kind: 'historical', calendarYear: y, ordinal: null, label: String(y) });
      }
    } else if (i.hasStrTtm) {
      columns.push({ key: 'h-ttm', kind: 'historical', calendarYear: null, ordinal: null, label: 'STR TTM' });
    }
    for (let n = 0; n < forecastCount; n++) {
      columns.push({ key: `f-${n + 1}`, kind: 'forecast', calendarYear: null, ordinal: n + 1, label: `Year ${n + 1}` });
    }
  }
  const firstForecastIndex = columns.findIndex((c) => c.kind === 'forecast');
  return {
    columns,
    hasCalendar,
    firstForecastIndex: firstForecastIndex < 0 ? columns.length : firstForecastIndex,
  };
}

// ─────────────────────────── series ───────────────────────────

/** Per-column series for one entity (subject, comp set, or market). */
export interface YearSeries {
  occupancy: (number | null)[]; // 0..1 ratio
  adr: (number | null)[]; // dollars
  revpar: (number | null)[]; // dollars
}

/** Sam #6 (2026-10-15) — what kind of number a benchmark cell is: a reported
 *  actual, a CBRE forecast, or an assumption (a reported level grown at the
 *  market growth assumption). Shown as a worded chip, never colour alone. */
export type BenchmarkBasis = 'actual' | 'forecast' | 'assumption';

/** Where one column's figures came from — the short label goes in the
 *  Source row, the detail on hover. */
export interface CellSource {
  kind: 'str_annual' | 'pnl_fiscal_year' | 'engine' | 'str_ttm' | 'market_actual' | 'cbre_forecast' | 'comp_grown';
  label: string;
  detail: string;
  /** Engine rows only — ``revenue.years[yearIndex]``. */
  yearIndex?: number;
  /** Sam #6 — the basis chip and its one-line label ("Actual · STR TTM May
   *  2025 · <doc>", "Forecast · CBRE Horizons 2027 · <doc> p.4", "Assumption
   *  · grown at market growth"). Absent on the subject's model projection,
   *  which is the underwriting, not a benchmark. */
  basis?: BenchmarkBasis;
  basisLabel?: string;
}

// ─────────────────────────── basis labels (Sam #6) ───────────────────────────
// Mirrors apps/worker/app/services/index_methodology.py actual_label /
// forecast_label / ASSUMPTION_GROWN_LABEL so the by-year grid (worker labels)
// and the tables (built here from market-data) read the same.

export const BASIS_CHIP_TEXT: Record<BenchmarkBasis, string> = {
  actual: 'Actual',
  forecast: 'Forecast',
  assumption: 'Assumption',
};

export const ASSUMPTION_GROWN_BASIS_LABEL = 'Assumption · grown at market growth';

const MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "2025-05" / "2025-05-31" → "May 2025"; "2025" → "2025"; else null. */
export function periodEndText(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const m = /^((?:19|20)\d{2})(?:-(\d{2}))?/.exec(String(iso).trim());
  if (!m) return null;
  if (m[2]) {
    const month = MONTHS_SHORT[Number(m[2]) - 1];
    if (month) return `${month} ${m[1]}`;
  }
  return m[1];
}

/** "Actual · STR TTM May 2025 · <doc>" — period / doc omitted when unknown. */
export function actualBasisLabel(source: string, period: string | null | undefined, doc: string | null | undefined): string {
  const head = `Actual · ${source}${period ? ` ${period}` : ''}`;
  return doc ? `${head} · ${doc}` : head;
}

/** "Forecast · CBRE Horizons 2027 · <doc> p.4". */
export function cbreForecastBasisLabel(calendarYear: number, doc: string | null | undefined, page: number | null | undefined): string {
  const head = `Forecast · CBRE Horizons ${calendarYear}`;
  return doc ? `${head} · ${doc}${page ? ` p.${page}` : ''}` : head;
}

/** The STR TTM period end the market-data envelope names: the worker's
 *  resolved ``ttm_period_end``, else the legacy ``report_month``. */
export function strTtmPeriodEnd(str: StrTrendAPI | null | undefined): string | null {
  return str?.ttm_period_end ?? str?.report_month ?? null;
}

/** "Actual · STR TTM May 2025 · <doc>" for the STR comp-set anchor. */
export function strTtmBasisLabel(str: StrTrendAPI | null | undefined): string {
  const end = periodEndText(strTtmPeriodEnd(str)) ?? (str?.report_year ? String(str.report_year) : null);
  return actualBasisLabel('STR TTM', end, str?.ttm_doc_name ?? str?.doc_name ?? null);
}

/** The worker's basis for one by-year figure, or null (no value / older worker). */
export function figureBasis(
  f: IndexFigure | null | undefined,
): { basis: BenchmarkBasis; label: string } | null {
  if (!f || f.value == null || !f.basis) return null;
  return { basis: f.basis, label: f.basis_label ?? BASIS_CHIP_TEXT[f.basis] };
}

export function emptySeries(n: number): YearSeries {
  return {
    occupancy: Array.from({ length: n }, () => null),
    adr: Array.from({ length: n }, () => null),
    revpar: Array.from({ length: n }, () => null),
  };
}

// FON-61 — a metric that arrives as 0, negative, null, or non-finite is "not
// reported", not a real value: an operating hotel never books 0% occupancy or a
// $0 ADR. Coerce all of these to null so the UI shows blank and never
// fabricates a zero.
/** Comp-set "keys" row = total room count of the ACTIVE hotels on the named
 *  roster (closed hotels excluded; the roster excludes the subject); the
 *  ``total_keys`` rollup only when the roster yields nothing — the extracted
 *  rollup has proven unreliable. */
export function compSetKeys(str: StrTrendAPI | null | undefined): number | null {
  const fromRoster = (str?.compset ?? []).reduce(
    (acc, row) =>
      acc + (row.status !== 'closed' && typeof row.keys === 'number' && row.keys > 0 ? row.keys : 0),
    0,
  );
  if (fromRoster > 0) return fromRoster;
  const fromRollup = str?.total_keys;
  return typeof fromRollup === 'number' && fromRollup > 0 ? fromRollup : null;
}

export function posOrNull(v: number | null | undefined): number | null {
  return typeof v === 'number' && isFinite(v) && v > 0 ? v : null;
}

// Normalize an STR penetration index to a plain ratio. STR/CoStar publish these
// either as a ratio (1.098) or as index points (109.8); anything > 3 is points.
export function indexRatio(idx: number | null | undefined): number | null {
  if (idx == null || idx <= 0) return null;
  return idx > 3 ? idx / 100 : idx;
}

// An index or penetration target in index points (109.8), read EXACTLY as the
// worker reads it (``validate_index_target``): a ratio 0.25–2.5 × 100, points
// 25–250 as is, anything else null — see ``@/lib/indexTarget``.
export function indexPoints(idx: number | null | undefined): number | null {
  return toIndexPoints(idx);
}

// Normalize an occupancy figure to a 0..1 fraction (sources vary between 0.72
// and 72). Values ≤ 1.5 are already fractions; larger are whole percents.
export function occFraction(v: number | null | undefined): number | null {
  const p = posOrNull(v);
  if (p == null) return null;
  return p > 1.5 ? p / 100 : p;
}

/** FON-61 (61.3) — is the last historical column comparable to the first
 *  forecast column? The last historical column is a reported actual (a
 *  fiscal-year P&L or an STR calendar year); Year 1 is the model's Base Year,
 *  grown off a trailing-twelve-month window. A growth rate across the two is
 *  not a modelled assumption, so that one cell is N/A. */
export type ForecastBoundary = { comparable: true } | { comparable: false; note: string };

export const BOUNDARY_COMPARABLE: ForecastBoundary = { comparable: true };

export function boundaryNote(lastHistoricalLabel: string, firstForecastLabel: string, basis: string): string {
  return (
    `Not applicable: the ${lastHistoricalLabel} column is ${basis} while ${firstForecastLabel} is the model's ` +
    'Base Year (Year 1), grown off a trailing-twelve-month window. A growth rate across two different period ' +
    'bases would not be a modelled assumption. Growth from the second forecast year onward compares forecast to forecast.'
  );
}

const docRef = (doc?: string | null, page?: number | null): string =>
  doc ? ` — ${doc}${page ? `, p.${page}` : ''}` : '';

export interface SubjectSeriesResult {
  series: YearSeries;
  sources: (CellSource | null)[];
  boundary: ForecastBoundary;
}

/**
 * FON-95 — the subject rows. Historical columns: the STR Trend annual
 * subject figures (``ttm_performance.subject.annual.<YYYY>``) first, the
 * multi-year P&L fiscal year as the fallback. Forecast columns: the revenue
 * engine's ``years[ordinal − 1]`` — Year 1 in the first forecast column,
 * exactly as the Future P&L places it (E-027).
 */
export function buildSubjectSeries(
  cols: IaColumns,
  outputs: EngineOutputsResponse | null,
  baseline: HistoricalBaselineResponse | null,
  str: StrTrendAPI | null | undefined,
): SubjectSeriesResult {
  const { columns, firstForecastIndex } = cols;
  const series = emptySeries(columns.length);
  const sources: (CellSource | null)[] = columns.map(() => null);

  const strAnnual = new Map<number, StrSubjectAnnualAPI>();
  for (const a of str?.subject_annual ?? []) {
    const y = finiteInt(a?.calendar_year);
    if (y != null) strAnnual.set(y, a);
  }
  const fiscal = new Map<number, HistoricalBaselineResponse['years'][number]>();
  for (const y of baseline?.years ?? []) fiscal.set(y.fiscal_year, y);

  columns.forEach((col, i) => {
    if (col.kind === 'historical') {
      if (col.calendarYear == null) return;
      const a = strAnnual.get(col.calendarYear);
      const aOcc = occFraction(a?.occupancy_pct);
      const aAdr = posOrNull(a?.adr_usd);
      const aRev = posOrNull(a?.revpar_usd);
      if (aOcc != null || aAdr != null || aRev != null) {
        series.occupancy[i] = aOcc;
        series.adr[i] = aAdr;
        series.revpar[i] = aRev ?? (aOcc != null && aAdr != null ? aOcc * aAdr : null);
        sources[i] = {
          kind: 'str_annual',
          label: 'STR annual',
          detail: `STR Trend annual ${col.calendarYear} subject figures (ttm_performance.subject.annual.${col.calendarYear})${docRef(str?.doc_name, a?.page)}`,
          basis: 'actual',
          basisLabel: actualBasisLabel('STR annual', String(col.calendarYear), str?.doc_name ?? null),
        };
        return;
      }
      const row = fiscal.get(col.calendarYear);
      if (!row) return;
      // Validate each metric independently — a P&L year can ship a real ADR
      // while the occupancy cell was blank (stored as 0).
      const fOcc = occFraction(row.occupancy);
      const fAdr = posOrNull(row.adr);
      const fRev = posOrNull(row.revpar);
      if (fOcc == null && fAdr == null && fRev == null) return;
      series.occupancy[i] = fOcc;
      series.adr[i] = fAdr;
      series.revpar[i] = fRev ?? (fOcc != null && fAdr != null ? fOcc * fAdr : null);
      sources[i] = {
        kind: 'pnl_fiscal_year',
        label: 'P&L FY',
        detail: `Fiscal-year ${col.calendarYear} P&L actual (multi-year historical baseline)`,
        basis: 'actual',
        basisLabel: actualBasisLabel('P&L FY', String(col.calendarYear), null),
      };
      return;
    }
    const revYears = getEngineField<RevenueYear[]>(outputs, 'revenue', 'years') ?? [];
    const k = (col.ordinal ?? 1) - 1;
    const y = revYears[k];
    if (!y) return;
    series.occupancy[i] = occFraction(y.occupancy);
    series.adr[i] = posOrNull(y.adr);
    series.revpar[i] = posOrNull(y.revpar);
    sources[i] = {
      kind: 'engine',
      label: `Model Y${k + 1}`,
      detail:
        k === 0
          ? 'Revenue engine Year 1 (revenue.years[0]) — the Base Year Projections print'
          : `Revenue engine Year ${k + 1} (revenue.years[${k}])`,
      yearIndex: k,
    };
  });

  // FON-61 (61.3) — the boundary step is refused when both sides exist.
  let boundary: ForecastBoundary = BOUNDARY_COMPARABLE;
  const lastHist = firstForecastIndex - 1;
  if (lastHist >= 0 && firstForecastIndex < columns.length) {
    const histHas = series.occupancy[lastHist] != null || series.adr[lastHist] != null;
    const fcHas = series.occupancy[firstForecastIndex] != null || series.adr[firstForecastIndex] != null;
    if (histHas && fcHas) {
      const src = sources[lastHist];
      const basis =
        src?.kind === 'str_annual'
          ? 'the STR calendar-year actual'
          : 'a full fiscal year of P&L actuals';
      boundary = {
        comparable: false,
        note: boundaryNote(columns[lastHist].label, columns[firstForecastIndex].label, basis),
      };
    }
  }
  return { series, sources, boundary };
}

export interface CompSeriesResult {
  series: YearSeries;
  sources: (CellSource | null)[];
  /** The column the STR TTM blend sits in, or −1. */
  anchorIndex: number;
  /** Why the forecast cells are "—" (all, or the ones still blank). Null when
   *  every forecast column is filled. */
  forecastReason: string | null;
  /** True when at least one forecast column carries the comp set grown at the
   *  market growth assumption. */
  forecastFilled: boolean;
}

/** Why the comp-set forecast columns are blank — not a number re-scaled off
 *  the subject's growth (FON-95). */
export const COMP_FORECAST_NONE_REASON =
  'No comp-set forecast source: STR publishes the comp set for the trailing twelve months only, and CBRE Horizons forecasts the market, not this comp set. The comp set is not carried forward at the subject’s growth.';

/** The comp-set forecast band when it is filled (Sam #3): the STR TTM level
 *  grown at the market occupancy / ADR growth assumption — an ASSUMPTION, the
 *  same series the ``str_comp_set`` index method runs on. */
export const COMP_FORECAST_GROWN_LABEL = 'comp set grown at market growth (assumption)';

const figureDocs = (f: IndexFigure | null | undefined): string => {
  const docs: string[] = [];
  for (const r of f?.inputs ?? []) {
    const d = r.doc_name ? `${r.doc_name}${r.page ? `, p.${r.page}` : ''}` : null;
    if (d && !docs.includes(d)) docs.push(d);
  }
  return docs.length ? ` — ${docs.join('; ')}` : '';
};

/**
 * The STR/CoStar comp set. The TTM report anonymizes per-property comp
 * performance but publishes the subject's penetration indices — MPI (occ),
 * ARI (ADR), RGI (RevPAR), each = subject ÷ comp — so the blended comp set is
 * comp = subject ÷ index, the SAME derivation Market Overview uses. It lands
 * on the last historical column (the TTM window's anchor). Every other cell
 * is "—": STR publishes no prior-year comp set and no comp-set forecast.
 */
export function buildCompSeries(
  cols: IaColumns,
  str: StrTrendAPI | null | undefined,
  /** The worker's STR comp set by model year (``comp_set_by_year``): TTM level
   *  in Year 1, grown at the market growth assumption after it; a year with no
   *  growth value is null with the reason. Absent on an older worker. */
  compByYear?: IndexCompSetYear[] | null,
): CompSeriesResult {
  const { columns, firstForecastIndex } = cols;
  const series = emptySeries(columns.length);
  const sources: (CellSource | null)[] = columns.map(() => null);
  const anchorIndex = firstForecastIndex - 1;
  if (str && anchorIndex >= 0) {
    const occR = indexRatio(str.mpi_occupancy_index);
    const adrR = indexRatio(str.ari_adr_index);
    const revparR = indexRatio(str.rgi_revpar_index);
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
    if (compOcc != null || compAdr != null || compRevpar != null) {
      series.occupancy[anchorIndex] = compOcc;
      series.adr[anchorIndex] = compAdr;
      series.revpar[anchorIndex] = compRevpar;
      sources[anchorIndex] = {
        kind: 'str_ttm',
        label: 'STR TTM',
        detail: `STR comp set = subject TTM ÷ penetration index (MPI / ARI / RGI), ${strPeriodLabel(str) ?? 'trailing twelve months'}${docRef(str.ttm_doc_name ?? str.doc_name)}`,
        basis: 'actual',
        basisLabel: strTtmBasisLabel(str),
      };
    }
  }
  // Forecast — the comp set grown at the market growth assumption, by model
  // year, exactly as the worker's ``str_comp_set`` benchmark series.
  const byYear = new Map<number, IndexCompSetYear>();
  for (const r of compByYear ?? []) if (finiteInt(r?.year) != null) byYear.set(r.year, r);
  let filled = 0;
  let firstBlankReason: string | null = null;
  columns.forEach((col, i) => {
    if (col.kind !== 'forecast' || col.ordinal == null) return;
    const r = byYear.get(col.ordinal);
    const occ = occFraction(r?.occupancy?.value ?? null);
    const adr = posOrNull(r?.adr?.value ?? null);
    if (occ == null && adr == null) {
      firstBlankReason ??= r?.occupancy?.detail ?? r?.adr?.detail ?? null;
      return;
    }
    series.occupancy[i] = occ;
    series.adr[i] = adr;
    series.revpar[i] = occ != null && adr != null ? occ * adr : null;
    const parts = [
      r?.occupancy?.detail ? `Occupancy: ${r.occupancy.detail}${figureDocs(r.occupancy)}` : null,
      r?.adr?.detail ? `ADR: ${r.adr.detail}${figureDocs(r.adr)}` : null,
    ].filter(Boolean);
    // The worker labels each year; an older worker sends no basis, then the
    // ordinal says it (Year 1 = the TTM level itself, later years = grown).
    const wb = figureBasis(r?.occupancy) ?? figureBasis(r?.adr);
    const basis: BenchmarkBasis = wb?.basis ?? (col.ordinal === 1 ? 'actual' : 'assumption');
    sources[i] = {
      basis,
      basisLabel: wb?.label ?? (basis === 'actual' ? strTtmBasisLabel(str) : ASSUMPTION_GROWN_BASIS_LABEL),
      kind: 'comp_grown',
      label: col.ordinal === 1 ? 'STR TTM (Y1)' : 'Grown (assumption)',
      detail: `STR comp set ${col.ordinal === 1 ? 'TTM level as the Year-1 benchmark' : 'grown at the market growth assumption'} — the same series the STR comp-set index method runs on. ${parts.join(' · ')}`,
    };
    filled++;
  });
  const forecastCount = columns.length - firstForecastIndex;
  const forecastReason =
    filled === 0
      ? firstBlankReason ?? COMP_FORECAST_NONE_REASON
      : filled < forecastCount
        ? firstBlankReason
        : null;
  return { series, sources, anchorIndex, forecastReason, forecastFilled: filled > 0 };
}

export function strPeriodLabel(str: StrTrendAPI | null | undefined): string | null {
  if (!str) return null;
  if (str.report_month) return `TTM to ${str.report_month}`;
  // Sam #6 — the worker's resolved TTM period end (month precision or finer).
  const end = str.ttm_period_end && str.ttm_period_end.length >= 7 ? periodEndText(str.ttm_period_end) : null;
  if (end) return `TTM to ${end}`;
  if (str.report_year) return `STR ${str.report_year} TTM`;
  return null;
}

export interface MarketSeriesResult {
  series: YearSeries;
  sources: (CellSource | null)[];
  /** The chain-scale segment the historical half reads, or null = market-wide. */
  segment: string | null;
  /** "CBRE Horizons — <submarket> (<doc>, p.N)" when a forecast was placed. */
  forecastProvenance: string | null;
  /** Why the forecast half is "—", when it is. */
  forecastReason: string | null;
}

export const MARKET_FORECAST_NONE_REASON =
  'No market forecast source: upload a CBRE Hotel Horizons report for this submarket.';
export const MARKET_FORECAST_UNPLACED_REASON =
  'The CBRE Horizons extraction carries no calendar year for its forecast rows, so they cannot be placed on the deal’s calendar — re-extract the report (pipeline v7).';
export const MARKET_FORECAST_OUTSIDE_REASON =
  'The CBRE Horizons forecast years fall outside the model horizon shown here.';

/**
 * FON-95 — the broader market / chain-scale series. Historical: reported
 * actuals from the market study / STR market-level rows, by calendar year
 * (the market-wide figure; else the single segment the reports carry).
 * Forecast: CBRE Horizons, placed by its calendar year — never by assuming
 * Year 1 is "next year". Labelled Market, never comp set.
 */
export function buildMarketSeries(cols: IaColumns, market: MarketDataAPIResponse | null): MarketSeriesResult {
  const { columns, firstForecastIndex } = cols;
  const series = emptySeries(columns.length);
  const sources: (CellSource | null)[] = columns.map(() => null);
  const points = market?.market_history ?? [];

  // Segment choice: market-wide rows win; else the one segment on offer;
  // else the first segment alphabetically (named in the row label).
  const segments = Array.from(new Set(points.map((p) => p.segment ?? null).filter((s): s is string => !!s))).sort();
  const hasMarketWide = points.some((p) => !p.segment);
  const segment = hasMarketWide ? null : segments[0] ?? null;
  const chosen = points.filter((p) => (p.segment ?? null) === segment);

  const colByYear = new Map<number, number>();
  columns.forEach((c, i) => {
    if (c.calendarYear != null) colByYear.set(c.calendarYear, i);
  });

  const fill = (i: number, p: MarketHistoryPointAPI, source: CellSource) => {
    let touched = false;
    const occ = occFraction(p.occupancy_pct);
    const adr = posOrNull(p.adr_usd);
    const rev = posOrNull(p.revpar_usd);
    if (series.occupancy[i] == null && occ != null) { series.occupancy[i] = occ; touched = true; }
    if (series.adr[i] == null && adr != null) { series.adr[i] = adr; touched = true; }
    if (series.revpar[i] == null && rev != null) { series.revpar[i] = rev; touched = true; }
    if (series.revpar[i] == null && series.occupancy[i] != null && series.adr[i] != null) {
      series.revpar[i] = series.occupancy[i]! * series.adr[i]!;
    }
    if (touched && sources[i] == null) sources[i] = source;
  };
  const marketSource = (p: MarketHistoryPointAPI, when: string): CellSource => {
    const label = p.doc_type === 'MARKET_STUDY' ? 'Market study' : p.doc_type === 'PNL_BENCHMARK' ? 'Benchmark' : 'STR market';
    return {
      kind: 'market_actual',
      label,
      detail: `${segment ? `${segment.replace(/_/g, ' ')} segment` : 'Market-wide'} actual, ${when} (${(p.fields ?? []).join(', ') || 'market-level row'})${docRef(p.doc_name, p.page)}`,
      basis: 'actual',
      basisLabel: actualBasisLabel(label, when === 'trailing twelve months' ? 'TTM' : when === 'undated' ? null : when, p.doc_name ?? null),
    };
  };

  for (const p of chosen) {
    if (p.period === 'annual' && p.calendar_year != null) {
      const i = colByYear.get(p.calendar_year);
      if (i != null && columns[i].kind === 'historical') fill(i, p, marketSource(p, String(p.calendar_year)));
    }
  }
  // The TTM / undated market figure anchors the last historical column only
  // where no calendar-year actual filled it.
  const anchor = firstForecastIndex - 1;
  if (anchor >= 0) {
    for (const p of chosen) {
      if (p.period === 'ttm' || p.period === 'undated') {
        fill(anchor, p, marketSource(p, p.period === 'ttm' ? 'trailing twelve months' : 'undated'));
      }
    }
  }

  // Forecast — CBRE Horizons by calendar year.
  const cbre = market?.cbre_horizons ?? null;
  const cbreYears = cbre?.years ?? [];
  let forecastProvenance: string | null = null;
  let forecastReason: string | null = null;
  if (cbreYears.length === 0) {
    forecastReason = MARKET_FORECAST_NONE_REASON;
  } else {
    const pages = new Set<number>();
    let placed = 0;
    let placeable = 0;
    for (const y of cbreYears) {
      const cal = finiteInt(y.calendar_year);
      if (cal == null) continue;
      placeable++;
      const i = colByYear.get(cal);
      if (i == null) continue;
      const occ = occFraction(y.occupancy_pct);
      const adr = posOrNull(y.adr_usd);
      const rev = posOrNull(y.revpar_usd);
      if (occ == null && adr == null && rev == null) continue;
      // A reported actual already in a historical column wins over a forecast.
      if (columns[i].kind === 'historical' && sources[i] != null) continue;
      series.occupancy[i] = occ;
      series.adr[i] = adr;
      series.revpar[i] = rev ?? (occ != null && adr != null ? occ * adr : null);
      if (y.page) pages.add(y.page);
      sources[i] = {
        kind: 'cbre_forecast',
        label: 'CBRE',
        detail: `CBRE Hotel Horizons forecast year ${y.year_index} = ${cal}${cbre?.submarket ? `, ${cbre.submarket}` : ''}${docRef(cbre?.doc_name, y.page)}${cbre?.publication_date ? ` (report dated ${cbre.publication_date})` : ''}`,
        basis: 'forecast',
        basisLabel: cbreForecastBasisLabel(cal, cbre?.doc_name ?? null, y.page ?? null),
      };
      placed++;
    }
    if (placeable === 0) forecastReason = MARKET_FORECAST_UNPLACED_REASON;
    else if (placed === 0) forecastReason = MARKET_FORECAST_OUTSIDE_REASON;
    else {
      const pageList = Array.from(pages).sort((a, b) => a - b);
      const doc = cbre?.doc_name
        ? ` (${cbre.doc_name}${pageList.length ? `, p.${pageList.join(', ')}` : ''})`
        : '';
      forecastProvenance = `CBRE Horizons${cbre?.submarket ? ` — ${cbre.submarket}` : ''}${doc}`;
    }
  }
  return { series, sources, segment, forecastProvenance, forecastReason };
}

// ─────────────────────────── penetration (FON-95) ───────────────────────────

export interface PenetrationCell {
  value: number | null;
  text: string;
  /** The formula and inputs behind the number — on hover. Null for "—". */
  trace: string | null;
}
export interface PenetrationRow {
  key: string;
  label: string;
  cells: PenetrationCell[];
  /** Shown on hover of the row label when every cell is "—" for one reason. */
  reason?: string | null;
}

export interface PenetrationInput {
  cols: IaColumns;
  subject: YearSeries;
  comp: YearSeries;
  market: YearSeries;
  /** STR-published penetration indices (points), measured on the TTM window. */
  indices: { mpi: number | null; ari: number | null; rgi: number | null } | null;
  subjectKeys: number | null;
  /** Comp-set room count — the named roster, which EXCLUDES the subject. */
  compKeys: number | null;
  /** The analyst's all-years MPI / ARI targets (index points) from Index methodology. */
  targets: { mpi: number | null; ari: number | null } | null;
  /** FON-95 part 2 — the effective ANALYST-SET target per MODEL YEAR as the
   *  worker resolved it (index ordinal − 1; `by_year` beats the analyst's
   *  all-years `scalar`; null = no target that year). Forecast columns read
   *  these; historical columns and a missing entry fall back to `targets`. */
  targetsByYear?: { mpi: (YearTarget | null)[]; ari: (YearTarget | null)[] } | null;
  /** Why no target is applied even though one is set (e.g. "Use STR rates in
   *  the model" is off) — the at-target opportunity is gated exactly like the
   *  index rows, and says so. */
  targetGateReason?: string | null;
  /** The benchmark the analyst SELECTED on Index methodology. With the STR
   *  comp set (or none) the index rows are the STR-published indices against
   *  the comp set; with another method they index the subject against THAT
   *  benchmark (per column), and the at-target opportunity prices the target
   *  against it — the target is a penetration of the selected benchmark. */
  benchmark?: PenetrationBenchmark | null;
}

export interface PenetrationBenchmark {
  method: string;
  label: string;
  /** Benchmark occupancy (0..1) / ADR / RevPAR per column; null = none. */
  series: YearSeries;
  /** Where each column's benchmark came from — on hover. */
  details: (string | null)[];
  /** The current (TTM) penetration against this benchmark the worker computed
   *  (subject STR TTM ÷ benchmark × 100) — the anchor column. */
  current: { mpi: number | null; ari: number | null; trace: string | null };
}

/** One year's effective penetration target and which key it came from. */
export interface YearTarget {
  value: number;
  source: 'by_year' | 'scalar';
}

/** The MPI / ARI target that applies to one column — the per-year effective
 *  target for a forecast column that has one, else the all-years scalar. */
export function targetForColumn(
  col: IaColumn,
  metric: 'mpi' | 'ari',
  targets: PenetrationInput['targets'],
  byYear: PenetrationInput['targetsByYear'],
): YearTarget | null {
  // Every target is read through the worker's units (``@/lib/indexTarget``): a
  // ratio (1.10) is 110 points, an out-of-range value is not a target at all.
  if (col.kind === 'forecast' && col.ordinal != null && byYear) {
    const t = byYear[metric][col.ordinal - 1];
    const pts = t ? toIndexPoints(t.value) : null;
    if (t && pts != null) return { value: pts, source: t.source };
  }
  const s = toIndexPoints(targets?.[metric] ?? null);
  return s != null ? { value: s, source: 'scalar' } : null;
}

// (current / prior) - 1, returning null when either side is missing/zero.
export function growth(curr: number | null, prior: number | null): number | null {
  if (curr == null || prior == null || prior === 0) return null;
  return curr / prior - 1;
}

export function fmtPct(v: number | null, decimals = 1): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(decimals)}%`;
}
export function fmtDollar(v: number | null): string {
  if (v == null) return '—';
  return `$${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}
export function fmtDollar2(v: number | null): string {
  if (v == null) return '—';
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
export function fmtInt(v: number | null): string {
  if (v == null) return '—';
  return v.toLocaleString('en-US');
}
export function fmtIndex(v: number | null): string {
  if (v == null) return '—';
  return v.toFixed(1);
}
/** Signed dollars, e.g. "$12" / "−$5". */
export function fmtSignedDollar(v: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '' : '−'}$${Math.abs(v).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}
/** Signed percent, e.g. "+4.2%" / "−1.3%". */
export function fmtSignedPct(v: number | null, decimals = 1): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : '−'}${(Math.abs(v) * 100).toFixed(decimals)}%`;
}
/** Signed occupancy points, e.g. "+2.3 pts". */
export function fmtPts(v: number | null): string {
  if (v == null) return '—';
  return `${v >= 0 ? '+' : '−'}${Math.abs(v).toFixed(1)} pts`;
}
/** Share ratio (0..1) → percent with 2 decimals. */
export function fmtShare(v: number | null): string {
  if (v == null) return '—';
  return `${(v * 100).toFixed(2)}%`;
}

// Penetration index = subject ÷ benchmark × 100. 100 = at par.
export function pctIndex(subj: number | null, bench: number | null): number | null {
  if (subj == null || bench == null || bench === 0) return null;
  return (subj / bench) * 100;
}

const dash: PenetrationCell = { value: null, text: '—', trace: null };
const cell = (value: number | null, text: (v: number) => string, trace: string): PenetrationCell =>
  value == null || !Number.isFinite(value) ? dash : { value, text: text(value), trace };

const pctS = (v: number) => `${(v * 100).toFixed(1)}%`;
const usd = (v: number) => `$${v.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;

/**
 * Every penetration / share / variance row, each cell with its formula and
 * inputs. A cell renders only where BOTH inputs are reported.
 */
export function computePenetration(i: PenetrationInput): PenetrationRow[] {
  const { columns, firstForecastIndex } = i.cols;
  const n = columns.length;
  const anchor = firstForecastIndex - 1;
  const kv = i.subjectKeys != null && i.subjectKeys > 0 ? i.subjectKeys : null;
  const ckv = i.compKeys != null && i.compKeys > 0 ? i.compKeys : null;
  const idx = (k: number) => columns[k];

  // STR-published indices: the TTM anchor column; across the forecast the
  // analyst's penetration TARGET for that model year where one is set
  // (FON-95 part 2 — by-year beats the all-years scalar; RGI is derived from
  // the two), else the published index held flat where the subject is
  // projected (STR publishes no comp-set forecast). Pre-anchor history is
  // blank — STR anonymizes prior-year comps. Absent published indices,
  // subject ÷ comp where both exist.
  // The index rows read ONLY the per-year effective targets the worker resolved
  // (analyst-set: by-year, else the analyst's all-years scalar) — never the
  // document default, which is the published index itself.
  const forecastTarget = (k: number, metricKey: 'mpi' | 'ari' | 'rgi'): { value: number; trace: string } | null => {
    const col = idx(k);
    if (col.kind !== 'forecast' || !i.targetsByYear) return null;
    const yearLabel = col.ordinal != null ? `Year ${col.ordinal}` : col.label;
    const describe = (t: YearTarget, m: string) =>
      `${m} target ${t.value.toFixed(1)} (${t.source === 'by_year' ? `set for ${yearLabel}` : 'all-years target'})`;
    if (metricKey === 'rgi') {
      const m = targetForColumn(col, 'mpi', null, i.targetsByYear);
      const a = targetForColumn(col, 'ari', null, i.targetsByYear);
      if (!m || !a) return null;
      return {
        value: (m.value * a.value) / 100,
        trace: `RGI derived = ${describe(m, 'MPI')} × ${describe(a, 'ARI')} ÷ 100 — RGI is never an input`,
      };
    }
    const t = targetForColumn(col, metricKey, null, i.targetsByYear);
    if (!t) return null;
    return {
      value: t.value,
      trace: `Analyst ${describe(t, metricKey.toUpperCase())} — drives ${yearLabel} ${metricKey === 'mpi' ? 'occupancy' : 'ADR'} (benchmark × target ÷ 100) while "Use STR rates in the model" is on`,
    };
  };
  const publishedRow = (
    key: 'mpi' | 'ari' | 'rgi',
    label: string,
    published: number | null,
    subj: (number | null)[],
    comp: (number | null)[],
    metric: string,
  ): PenetrationRow => ({
    key,
    label,
    cells: Array.from({ length: n }, (_, k) => {
      const target = forecastTarget(k, key);
      if (target) return cell(target.value, (v) => v.toFixed(1), target.trace);
      if (published != null) {
        if (k < anchor) return dash;
        if (k === anchor) return cell(published, (v) => v.toFixed(1), `STR-published ${label} for the trailing-twelve-month window (${published.toFixed(1)})`);
        return subj[k] != null
          ? cell(published, (v) => v.toFixed(1), `STR-published ${label} (${published.toFixed(1)}) held flat — STR publishes no comp-set forecast`)
          : dash;
      }
      const v = pctIndex(subj[k], comp[k]);
      return cell(v, (x) => x.toFixed(1), `subject ${metric} ${subj[k]} ÷ comp-set ${metric} ${comp[k]} × 100`);
    }),
  });

  const vsMarketRow = (key: string, label: string, subj: (number | null)[], mkt: (number | null)[], fmt: (v: number) => string, metric: string): PenetrationRow => ({
    key,
    label,
    cells: Array.from({ length: n }, (_, k) => {
      const v = pctIndex(subj[k], mkt[k]);
      return cell(v, (x) => x.toFixed(1), `subject ${metric} ${subj[k] != null ? fmt(subj[k]!) : '—'} ÷ market ${metric} ${mkt[k] != null ? fmt(mkt[k]!) : '—'} × 100`);
    }),
  });

  // Sam #3 — the index rows follow the SELECTED benchmark. With a benchmark
  // other than the STR comp set, the STR-published MPI / ARI (which are
  // against the comp set) would index the subject against the wrong thing.
  const bench = i.benchmark && i.benchmark.method !== 'str_comp_set' ? i.benchmark : null;
  const benchmarkRow = (
    key: 'mpi' | 'ari' | 'rgi',
    label: string,
    subj: (number | null)[],
    benchSeries: (number | null)[],
    current: number | null,
    metric: string,
    b: PenetrationBenchmark,
  ): PenetrationRow => ({
    key,
    label: `${label} vs ${b.label}`,
    reason: `Indexed against the selected benchmark (${b.label}) — Index methodology`,
    cells: Array.from({ length: n }, (_, k) => {
      const target = forecastTarget(k, key);
      if (target) return cell(target.value, (v) => v.toFixed(1), target.trace);
      if (k < anchor) return dash;
      if (k === anchor && current != null) {
        return cell(current, (v) => v.toFixed(1), b.current.trace ?? `current ${label} against ${b.label}`);
      }
      const v = pctIndex(subj[k], benchSeries[k]);
      return cell(
        v,
        (x) => x.toFixed(1),
        `subject ${metric} ${subj[k]} ÷ ${b.label} ${metric} ${benchSeries[k]} × 100${b.details[k] ? ` (${b.details[k]})` : ''}`,
      );
    }),
  });
  const rgiCurrent = (b: PenetrationBenchmark): number | null =>
    b.current.mpi != null && b.current.ari != null ? (b.current.mpi * b.current.ari) / 100 : null;

  const rows: PenetrationRow[] = [
    bench
      ? benchmarkRow('mpi', 'Occupancy Index (MPI)', i.subject.occupancy, bench.series.occupancy, bench.current.mpi, 'occupancy', bench)
      : publishedRow('mpi', 'Occupancy Index (MPI)', i.indices?.mpi ?? null, i.subject.occupancy, i.comp.occupancy, 'occupancy'),
    bench
      ? benchmarkRow('ari', 'ADR Index (ARI)', i.subject.adr, bench.series.adr, bench.current.ari, 'ADR', bench)
      : publishedRow('ari', 'ADR Index (ARI)', i.indices?.ari ?? null, i.subject.adr, i.comp.adr, 'ADR'),
    bench
      ? benchmarkRow('rgi', 'RevPAR Index (RGI)', i.subject.revpar, bench.series.revpar, rgiCurrent(bench), 'RevPAR', bench)
      : publishedRow('rgi', 'RevPAR Index (RGI)', i.indices?.rgi ?? null, i.subject.revpar, i.comp.revpar, 'RevPAR'),
    vsMarketRow('mpi-market', 'Occupancy Index vs Market', i.subject.occupancy, i.market.occupancy, pctS, 'occupancy'),
    vsMarketRow('ari-market', 'ADR Index vs Market', i.subject.adr, i.market.adr, usd, 'ADR'),
    vsMarketRow('rgi-market', 'RevPAR Index vs Market', i.subject.revpar, i.market.revpar, usd, 'RevPAR'),
  ];

  // Variances to the comp set.
  rows.push({
    key: 'occ-var',
    label: 'Occupancy Variance to Comp Set (pts)',
    cells: Array.from({ length: n }, (_, k) => {
      const s = i.subject.occupancy[k];
      const c = i.comp.occupancy[k];
      const v = s != null && c != null ? (s - c) * 100 : null;
      return cell(v, fmtPts, `subject occupancy ${pctS(s ?? 0)} − comp-set occupancy ${pctS(c ?? 0)}`);
    }),
  });
  rows.push({
    key: 'adr-var',
    label: 'ADR Variance to Comp Set ($)',
    cells: Array.from({ length: n }, (_, k) => {
      const s = i.subject.adr[k];
      const c = i.comp.adr[k];
      const v = s != null && c != null ? s - c : null;
      return cell(v, fmtSignedDollar, `subject ADR ${usd(s ?? 0)} − comp-set ADR ${usd(c ?? 0)}`);
    }),
  });
  rows.push({
    key: 'adr-var-pct',
    label: 'ADR Variance to Comp Set (%)',
    cells: Array.from({ length: n }, (_, k) => {
      const s = i.subject.adr[k];
      const c = i.comp.adr[k];
      const v = s != null && c != null && c > 0 ? s / c - 1 : null;
      return cell(v, (x) => fmtSignedPct(x), `subject ADR ${usd(s ?? 0)} ÷ comp-set ADR ${usd(c ?? 0)} − 1`);
    }),
  });
  rows.push({
    key: 'revpar-var',
    label: 'RevPAR Variance to Comp Set',
    cells: Array.from({ length: n }, (_, k) => {
      const s = i.subject.revpar[k];
      const c = i.comp.revpar[k];
      const v = s != null && c != null ? s - c : null;
      return cell(v, fmtSignedDollar, `subject RevPAR ${usd(s ?? 0)} − comp-set RevPAR ${usd(c ?? 0)}`);
    }),
  });

  // Fair share — the subject IS part of the competitive market, so the
  // supply denominator is comp-set rooms + subject rooms (FON-95). The named
  // STR roster excludes the subject (its own row is skipped at extraction).
  const total = kv != null && ckv != null ? kv + ckv : null;
  const fairShare = kv != null && total != null ? kv / total : null;
  const noKeys = kv == null ? 'Subject room count unknown' : ckv == null ? 'Comp-set room count unknown — upload an STR Trend report' : null;
  rows.push({
    key: 'fair-share',
    label: 'Fair Share of Supply',
    reason: noKeys,
    cells: Array.from({ length: n }, () =>
      cell(fairShare, fmtShare, `subject rooms ${kv} ÷ (comp-set rooms ${ckv} + subject rooms ${kv}) = ${kv} ÷ ${total}`),
    ),
  });
  rows.push({
    key: 'fair-share-occ',
    label: 'Fair-Share Occupancy',
    reason: noKeys,
    cells: Array.from({ length: n }, (_, k) => {
      const so = i.subject.occupancy[k];
      const co = i.comp.occupancy[k];
      if (kv == null || ckv == null || so == null || co == null) return dash;
      const v = (kv * so + ckv * co) / (kv + ckv);
      return cell(
        v,
        pctS,
        `(subject rooms ${kv} × subject occupancy ${pctS(so)} + comp-set rooms ${ckv} × comp-set occupancy ${pctS(co)}) ÷ ${kv + ckv} rooms — the occupancy the subject would run at exactly its fair share of demand`,
      );
    }),
  });
  rows.push({
    key: 'actual-share',
    label: 'Actual Share of Demand',
    reason: noKeys,
    cells: Array.from({ length: n }, (_, k) => {
      const so = i.subject.occupancy[k];
      const co = i.comp.occupancy[k];
      if (kv == null || ckv == null || so == null || co == null) return dash;
      const demandS = kv * so;
      const demandC = ckv * co;
      if (demandS + demandC <= 0) return dash;
      const v = demandS / (demandS + demandC);
      return cell(
        v,
        fmtShare,
        `subject occupied rooms (${kv} × ${pctS(so)}) ÷ (subject ${kv} × ${pctS(so)} + comp set ${ckv} × ${pctS(co)})`,
      );
    }),
  });
  rows.push({
    key: 'fair-share-rev',
    label: 'Fair-Share Rooms Revenue',
    reason: noKeys,
    cells: Array.from({ length: n }, (_, k) => {
      const sr = i.subject.revpar[k];
      const cr = i.comp.revpar[k];
      if (kv == null || ckv == null || sr == null || cr == null) return dash;
      const days = daysForColumn(idx(k));
      const blended = (kv * sr + ckv * cr) / (kv + ckv);
      const v = kv * days * blended;
      return cell(
        v,
        (x) => `$${Math.round(x).toLocaleString('en-US')}`,
        `subject rooms ${kv} × ${days} days × blended RevPAR ${usd(blended)} (= (${kv} × ${usd(sr)} + ${ckv} × ${usd(cr)}) ÷ ${kv + ckv})`,
      );
    }),
  });
  rows.push({
    key: 'oppty-par',
    label: 'Revenue Opportunity at Par',
    reason: kv == null ? 'Subject room count unknown' : null,
    cells: Array.from({ length: n }, (_, k) => {
      const sr = i.subject.revpar[k];
      const cr = i.comp.revpar[k];
      if (kv == null || sr == null || cr == null) return dash;
      const days = daysForColumn(idx(k));
      const v = (cr - sr) * kv * days;
      return cell(
        v,
        (x) => `${x < 0 ? '−' : ''}$${Math.round(Math.abs(x)).toLocaleString('en-US')}`,
        `(comp-set RevPAR ${usd(cr)} − subject RevPAR ${usd(sr)}) × ${kv} rooms × ${days} days — signed: negative means the subject already out-earns par (RGI 100)`,
      );
    }),
  });
  // At-target opportunity, per column: the target that applies to THAT year
  // (by-year where set, else the all-years scalar).
  const anyTarget = columns.some(
    (c) => targetForColumn(c, 'mpi', i.targets, i.targetsByYear) && targetForColumn(c, 'ari', i.targets, i.targetsByYear),
  );
  const noTarget = anyTarget
    ? null
    : i.targetGateReason ??
      'No MPI / ARI penetration target set — set both on Index methodology (all years, or by year) to see the opportunity at target';
  // The target is a penetration of the SELECTED benchmark, so the at-target
  // RevPAR is priced on that benchmark (the comp set when it is the STR comp set).
  const tOcc = bench ? bench.series.occupancy : i.comp.occupancy;
  const tAdr = bench ? bench.series.adr : i.comp.adr;
  const tName = bench ? bench.label : 'comp-set';
  rows.push({
    key: 'oppty-target',
    label: 'Revenue Opportunity at Target',
    reason: noTarget ?? (kv == null ? 'Subject room count unknown' : null),
    cells: Array.from({ length: n }, (_, k) => {
      const sr = i.subject.revpar[k];
      const co = tOcc[k];
      const ca = tAdr[k];
      const mt = targetForColumn(idx(k), 'mpi', i.targets, i.targetsByYear);
      const at = targetForColumn(idx(k), 'ari', i.targets, i.targetsByYear);
      if (kv == null || sr == null || co == null || ca == null || !mt || !at) return dash;
      const days = daysForColumn(idx(k));
      const targetRevpar = co * (mt.value / 100) * ca * (at.value / 100);
      const v = (targetRevpar - sr) * kv * days;
      const which = (t: YearTarget) => (t.source === 'by_year' ? ', set for this year' : ', all-years');
      return cell(
        v,
        (x) => `${x < 0 ? '−' : ''}$${Math.round(Math.abs(x)).toLocaleString('en-US')}`,
        `(target RevPAR ${usd(targetRevpar)} = ${tName} occupancy ${pctS(co)} × MPI target ${mt.value.toFixed(1)}${which(mt)} ÷ 100 × ${tName} ADR ${usd(ca)} × ARI target ${at.value.toFixed(1)}${which(at)} ÷ 100 − subject RevPAR ${usd(sr)}) × ${kv} rooms × ${days} days`,
      );
    }),
  });
  return rows;
}

/** Calendar years the subject's documents report (STR annual + P&L fiscal). */
export function subjectDocumentYears(
  baseline: HistoricalBaselineResponse | null,
  str: StrTrendAPI | null | undefined,
): number[] {
  const out = new Set<number>();
  for (const y of baseline?.years ?? []) if (finiteInt(y.fiscal_year) != null) out.add(y.fiscal_year);
  for (const a of str?.subject_annual ?? []) {
    const y = finiteInt(a?.calendar_year);
    if (y != null) out.add(y);
  }
  return Array.from(out).sort((a, b) => a - b);
}
