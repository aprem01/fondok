'use client';
/**
 * HistoricalBaselinePanel — Wave 2 P2.6 multi-year P&L walk.
 *
 * Sam's June 2026 ask (Wave 2 P2.6): "Institutional IC analysts will
 * not approve a deal without seeing the multi-year trend." Today
 * Fondok only renders the forward proforma (Y1..Y5); this panel
 * stacks the property's OWN historical actuals (3-5 years from
 * uploaded P&Ls) side-by-side with the Y1 forecast.
 *
 * Layout
 * ------
 *
 * Compact horizontal table. Rows = USALI lines (Rooms Rev, F&B Rev,
 * Total Rev, ..., GOP, NOI). Columns = each historical year + a final
 * "Y1 Forecast" column pulled from the existing model output. YoY
 * arrows + colored % chips next to each cell — green for benign
 * increases, amber/red for declines on revenue lines (inverted for
 * expense lines: an expense increase is amber).
 *
 * Header
 * ------
 *
 * "Coverage 3/5 yrs · Missing 2020-2021" chip. Renders nothing when
 * ``coverage_pct === 0`` (no historical docs uploaded — caller is
 * expected to filter at that level too, but defense-in-depth). The
 * coverage chip states its own denominator on hover: "3 of the 5 fiscal
 * years in the lookback window ending 2024" — the number was previously
 * a bare ratio with nothing saying what the 5 was (FON-44 §4).
 *
 * Year-over-year
 * --------------
 *
 * The panel does NOT compute year-over-year growth. Every percentage in
 * the historical columns is the engine's own ``walk`` entry for that
 * (line, year), looked up by key. That matters: the engine refuses to
 * divide across incomparable periods — a gap in the history, a
 * year-to-date statement against a full fiscal year, a statement with no
 * top line — and a panel doing its own division would put the refused
 * number back on the screen. Sam, August 2026: "Fixed Expenses +11,170%,
 * F&B Dept Expense +4,476% … driven by incomplete/partial or
 * inconsistently classified historical periods." A refused comparison
 * renders a dash carrying its reason, never a zero.
 *
 * The one exception is the Y1 Forecast column, which compares a FORECAST
 * to the most recent actual. That is not a historical year-over-year and
 * the engine's walk does not carry it.
 *
 * Walk panel (below the table)
 * ----------------------------
 *
 * The top 5 YoY swings rendered as chips. Each chip routes back to
 * the Validation tab's Broker Questions panel where the analyst can
 * create + send the broker question (Wave 1 #4 — reuse, don't
 * duplicate the question lifecycle).
 *
 * Source badges
 * -------------
 *
 * AssumptionBadge with source='t12_actual' for every historical cell
 * (extracted from an uploaded P&L). The Y1 Forecast column uses the
 * caller-supplied forecastSource so the same column can render
 * different provenance per cell.
 */
import { useMemo } from 'react';
import { useRouter } from 'next/navigation';
import { TrendingUp, TrendingDown } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';
import { Refused, asReasonCode, type ReasonCode } from '@/components/help/Refused';
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import type {
  HistoricalBaselineResponse,
  HistoricalYear,
  YoYDelta,
} from '@/lib/api';

// The wire carries two FON-44 §4 additions the shared interfaces in
// `lib/api.ts` do not name yet: the refusal code on a walk entry
// (`YoYDelta.reason`) and the period each year's statement covers
// (`HistoricalYear.period_basis` / `is_partial`). Both are read
// structurally so this panel does not depend on those interfaces being
// widened first; the worker's Pydantic models
// (`api/documents.py YoYDeltaOut` / `HistoricalYearOut`) are the contract.
type WalkEntry = YoYDelta & { reason?: string | null };
type YearRow = HistoricalYear & {
  period_basis?: string | null;
  is_partial?: boolean | null;
};

// Canonical USALI line catalog the panel walks through. Order matches
// the engine's ``WALK_LINES`` so the panel and the walk chips agree.
// Each entry: ``[fieldKey, label, isExpense]``. ``isExpense`` flips
// the trend color (expense increases render amber, decreases green).
const ROW_CATALOG: Array<[keyof HistoricalYear, string, boolean]> = [
  ['rooms_revenue', 'Rooms Revenue', false],
  ['fnb_revenue', 'F&B Revenue', false],
  ['other_revenue', 'Other Revenue', false],
  ['total_revenue', 'Total Revenue', false],
  ['rooms_dept_expense', 'Rooms Dept Expense', true],
  ['fnb_dept_expense', 'F&B Dept Expense', true],
  ['other_dept_expense', 'Other Dept Expense', true],
  ['undistributed', 'Undistributed', true],
  ['gop', 'GOP', false],
  ['fixed_expenses', 'Fixed Expenses', true],
  ['noi', 'NOI', false],
];

// Walk chips below the table show the top N swings.
const WALK_TOP_N = 5;


export interface HistoricalBaselinePanelProps {
  baseline: HistoricalBaselineResponse | null;
  dealId: string;
  /** Y1 forecast values keyed by the same field slugs as
   *  ``ROW_CATALOG`` so the rightmost column can be filled from the
   *  worker's revenue/expense engine output. Optional — when omitted
   *  the column shows em-dashes.
   *
   *  Wire shape: pass the engine output's Year-1 entry projected as
   *  ``{ rooms_revenue: 12_500_000, gop: 4_500_000, noi: 3_800_000, ... }``.
   */
  forecastY1?: Partial<Record<keyof HistoricalYear, number | null>>;
  /** Source label for the Y1 forecast column's badge. Defaults to
   *  ``'t12_actual'`` (the most common case — Y1 forecast = T-12
   *  actual extracted from the most-recent uploaded P&L). */
  forecastSource?: string;
}


export default function HistoricalBaselinePanel({
  baseline,
  dealId,
  forecastY1,
  forecastSource = 't12_actual',
}: HistoricalBaselinePanelProps) {
  const router = useRouter();

  // Hide the panel entirely when there's no baseline data yet — the
  // engine returns coverage_pct=0 for any deal with no historical
  // P&Ls. Showing an empty table would only confuse the analyst.
  if (!baseline || baseline.coverage_pct === 0) {
    return null;
  }

  const years = baseline.years as YearRow[];
  const hasForecast =
    forecastY1 !== undefined && Object.keys(forecastY1).length > 0;

  const coverageNum = Math.round(baseline.coverage_pct * baseline.look_back_years);
  const coverageDenom = baseline.look_back_years;
  // The denominator is the lookback WINDOW, not the number of documents —
  // "4/5" with nothing saying what the 5 is reads as a failure rate. The
  // window ends at the most recent year with data, which is also the year
  // the Missing chip counts back from.
  const latestYear = years.length ? years[years.length - 1].fiscal_year : null;
  const coverageTitle = latestYear
    ? `${coverageNum} of the ${coverageDenom} fiscal years in the lookback `
      + `window ending ${latestYear} carry an extracted P&L`
    : `${coverageDenom}-year lookback window`;

  // Build the gap label — "Missing 2020-2021" or "Missing 2022".
  // Defense-in-depth: empty gaps array → null chip.
  const gapLabel = useMemo(() => {
    if (!baseline.gaps.length) return null;
    if (baseline.gaps.length === 1) return `Missing ${baseline.gaps[0]}`;
    const sorted = [...baseline.gaps].sort((a, b) => a - b);
    const min = sorted[0];
    const max = sorted[sorted.length - 1];
    // Contiguous range → "Missing 2020-2021"; otherwise list.
    const contiguous = sorted.every((y, i) => i === 0 || y === sorted[i - 1] + 1);
    if (contiguous) return `Missing ${min}-${max}`;
    return `Missing ${sorted.join(', ')}`;
  }, [baseline.gaps]);

  // Walk top-N — already sorted by abs(yoy_pct) DESC by the engine.
  // Filter out the None-pct entries (no YoY signal) before slicing. A
  // comparison the engine refused as incomparable arrives with
  // ``yoy_pct === null`` and is dropped here with the rest, so a swing
  // across a gap year is never offered as a broker question (FON-44 §4).
  const walkTop = useMemo(() => {
    return baseline.walk
      .filter(w => w.yoy_pct !== null)
      .slice(0, WALK_TOP_N);
  }, [baseline.walk]);

  // The engine's walk, indexed by (line, year) — the single source of
  // every historical percentage this panel renders. An absent entry means
  // the engine produced no comparison for that cell: either the swing sat
  // under its noise floor or the line was not extracted that year.
  const walkIndex = useMemo(() => {
    const byKey = new Map<string, WalkEntry>();
    for (const w of baseline.walk as WalkEntry[]) {
      byKey.set(`${w.line}|${w.year}`, w);
    }
    return byKey;
  }, [baseline.walk]);

  // Why a year's comparisons were refused, resolved once per COLUMN — the
  // condition is a property of the period pair, identical for every line
  // in it.
  const detailByYear = useMemo(() => {
    const byYear = new Map<number, string | null>();
    for (const y of years) byYear.set(y.fiscal_year, refusalDetail(years, y));
    return byYear;
  }, [years]);

  // Click a walk chip → route to the Validation tab where the
  // Broker Questions panel lives. The user runs Refresh there to
  // pull this swing into the question queue.
  const openValidationTab = (_line: string) => {
    router.push(`/projects/${dealId}?tab=validation`);
  };

  return (
    <Card className="p-4">
      {/* ─── Header ─── */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wide text-ink-500 font-medium">
            Historical Baseline
          </span>
          <span
            title={coverageTitle}
            className={cn(
              'inline-flex items-center px-2 py-0.5 rounded text-[10.5px] font-medium border tabular-nums',
              baseline.coverage_pct >= 0.6
                ? 'bg-success-50 text-success-700 border-success-500/30'
                : 'bg-warn-50 text-warn-700 border-warn-500/30',
            )}
          >
            Coverage {coverageNum}/{coverageDenom} yrs
          </span>
          {gapLabel && (
            <span className="inline-flex items-center px-2 py-0.5 rounded text-[10.5px] font-medium border tabular-nums bg-ink-300/20 text-ink-700 border-ink-300/40">
              {gapLabel}
            </span>
          )}
        </div>
      </div>

      {/* ─── Multi-year table ─── */}
      <div className="overflow-x-auto -mx-1">
        <table className="w-full text-[12px] tabular-nums">
          <thead>
            <tr className="text-ink-500 text-[11px] uppercase tracking-wide">
              <th className="text-left font-medium px-2 py-1.5">Line</th>
              {years.map(y => (
                <th
                  key={y.fiscal_year}
                  className="text-right font-medium px-2 py-1.5"
                >
                  {y.fiscal_year}
                </th>
              ))}
              {hasForecast && (
                <th className="text-right font-medium px-2 py-1.5 border-l border-border">
                  Y1 Forecast
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {ROW_CATALOG.map(([key, label, isExpense]) => (
              <tr key={key} className="border-t border-border/50">
                <td className="text-left text-ink-700 px-2 py-1.5 whitespace-nowrap">
                  {label}
                </td>
                {years.map(y => {
                  const val = y[key] as number | null | undefined;
                  // The engine's verdict for this exact (line, year) —
                  // never a division of our own. Absent entry → no
                  // comparison was produced; a null pct WITH a reason →
                  // the engine refused this pair (gap year, partial
                  // period, missing top line) and the cell says so.
                  const delta = walkIndex.get(`${String(key)}|${y.fiscal_year}`);
                  return (
                    <Cell
                      key={`${y.fiscal_year}-${String(key)}`}
                      value={val ?? null}
                      yoyPct={delta?.yoy_pct ?? null}
                      refusal={asReasonCode(delta?.reason)}
                      refusalDetail={detailByYear.get(y.fiscal_year) ?? null}
                      isExpense={isExpense}
                      source="t12_actual"
                      dealId={dealId}
                      documentId={y.source_document_ids[0] ?? null}
                    />
                  );
                })}
                {hasForecast && (
                  <Cell
                    value={forecastY1?.[key] ?? null}
                    // The one comparison the engine's walk does NOT carry:
                    // Y1 FORECAST against the most recent historical year.
                    // Both sides are full-year by construction (the
                    // proforma year and the latest actual), so it is
                    // computed here and nowhere else.
                    yoyPct={forecastYoY(
                      forecastY1?.[key] ?? null,
                      years.length
                        ? ((years[years.length - 1][key] as
                            | number
                            | null
                            | undefined) ?? null)
                        : null,
                    )}
                    refusal={null}
                    refusalDetail={null}
                    isExpense={isExpense}
                    source={forecastSource}
                    dealId={dealId}
                    documentId={null}
                    className="border-l border-border"
                  />
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ─── Walk chips ─── */}
      {walkTop.length > 0 && (
        <div className="mt-4">
          <div className="text-[11px] uppercase tracking-wide text-ink-500 font-medium mb-2">
            Biggest YoY swings
          </div>
          <div className="flex flex-wrap gap-2">
            {walkTop.map(w => {
              const sign = (w.yoy_pct ?? 0) >= 0 ? '+' : '';
              const label = ROW_CATALOG.find(([k]) => k === w.line)?.[1]
                ?? w.line;
              return (
                <button
                  key={`${w.line}-${w.year}`}
                  type="button"
                  onClick={() => openValidationTab(w.line)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11.5px] font-medium border bg-white hover:bg-ink-50 border-border tabular-nums whitespace-nowrap transition-colors"
                  title="Open Validation tab to create a broker question for this swing"
                >
                  <span className="text-ink-700">{label}</span>
                  <span className="text-ink-500">{w.year}</span>
                  <span
                    className={cn(
                      'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded',
                      (w.yoy_pct ?? 0) >= 0
                        ? 'bg-success-50 text-success-700'
                        : 'bg-warn-50 text-warn-700',
                    )}
                  >
                    {(w.yoy_pct ?? 0) >= 0 ? (
                      <TrendingUp size={10} />
                    ) : (
                      <TrendingDown size={10} />
                    )}
                    {sign}
                    {fmtPct(w.yoy_pct ?? 0, 1)}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
    </Card>
  );
}


// ────────────────────────── helpers ──────────────────────────


/** Year-over-year for the Y1 FORECAST column only.
 *
 * The historical columns read the engine's walk; this compares a forecast
 * to the most recent actual, which the walk does not carry. Kept as a
 * named function so it is obvious there is exactly one place left in this
 * panel that divides two numbers.
 */
function forecastYoY(value: number | null, prior: number | null): number | null {
  if (value === null || prior === null || prior === 0) return null;
  const pct = (value - prior) / prior;
  // Same 0.5% floor the engine applies to the historical walk
  // (``_YOY_NOISE_FLOOR``): a sub-half-percent drift is rounding, not a
  // move, and this column has always suppressed it.
  return Math.abs(pct) < 0.005 ? null : pct;
}

/** Case-specific prose for a refused comparison, or null for the
 *  ontology's own explanation.
 *
 *  The reason CODE is the worker's (`period_mismatch` / `no_source`); this
 *  only names which of the year's two disqualifying conditions the analyst
 *  is looking at, from data already on the wire. It invents nothing: when
 *  neither condition is visible here it returns null and `<Refused>` shows
 *  the vocabulary's standard explanation alone.
 */
function refusalDetail(years: YearRow[], year: YearRow): string | null {
  const prior = years.find(y => y.fiscal_year === year.fiscal_year - 1);
  if (!prior) {
    return `This deal has no ${year.fiscal_year - 1} statement, so `
      + `${year.fiscal_year} has no prior year to grow from.`;
  }
  if (year.is_partial || prior.is_partial) {
    return `${year.fiscal_year} is a ${year.period_basis ?? 'partial'} `
      + `statement and ${prior.fiscal_year} is a `
      + `${prior.period_basis ?? 'partial'} statement — they do not cover `
      + `the same length of period.`;
  }
  return null;
}

/** One value cell in the historical table.
 *
 * Renders the dollar amount, then the engine's year-over-year percentage
 * when it produced one. Em-dash when the VALUE is null (the extractor
 * didn't ship that line); a second, smaller dash carrying the refusal
 * reason when the value is there but the COMPARISON was refused. Never a
 * zero standing in for a percentage that does not exist.
 */
function Cell({
  value,
  yoyPct,
  refusal,
  refusalDetail: detail,
  isExpense,
  source,
  dealId,
  documentId,
  className,
}: {
  value: number | null;
  /** The engine's ``yoy_pct`` for this cell, or null when there is none. */
  yoyPct: number | null;
  /** The engine's refusal code when a comparison was declined. */
  refusal: ReasonCode | null;
  refusalDetail: string | null;
  isExpense: boolean;
  source: string;
  dealId: string;
  documentId: string | null;
  className?: string;
}) {
  if (value === null) {
    return (
      <td className={cn('text-right text-ink-500 px-2 py-1.5', className)}>
        —
      </td>
    );
  }

  // Tone: revenue/profit lines treat increases as good (green), declines
  // as red. Expense lines invert (increases = amber). Sub-1% drifts
  // sit in muted ink so the eye skips them.
  let trendTone = 'text-ink-500';
  if (yoyPct !== null && Math.abs(yoyPct) >= 0.01) {
    const isUp = yoyPct > 0;
    const isGood = isExpense ? !isUp : isUp;
    trendTone = isGood ? 'text-success-700' : 'text-warn-700';
  }

  return (
    <td className={cn('text-right px-2 py-1.5', className)}>
      <div className="inline-flex items-center gap-1.5 justify-end">
        <span className="text-ink-900">
          {fmtCurrency(value, { compact: true })}
        </span>
        {yoyPct !== null && (
          <span className={cn('text-[10px] tabular-nums', trendTone)}>
            {yoyPct > 0 ? '+' : ''}
            {fmtPct(yoyPct, 1)}
          </span>
        )}
        {yoyPct === null && refusal !== null && (
          <Refused
            reason={refusal}
            detail={detail}
            className="text-[10px] text-ink-500"
          />
        )}
        <AssumptionBadge
          source={source}
          dealId={dealId}
          documentId={documentId}
        />
      </div>
    </td>
  );
}
