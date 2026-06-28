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
 * expected to filter at that level too, but defense-in-depth).
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
import { fmtCurrency, fmtPct, cn } from '@/lib/format';
import type { HistoricalBaselineResponse, HistoricalYear } from '@/lib/api';

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

  const years = baseline.years;
  const hasForecast =
    forecastY1 !== undefined && Object.keys(forecastY1).length > 0;

  const coverageNum = Math.round(baseline.coverage_pct * baseline.look_back_years);
  const coverageDenom = baseline.look_back_years;

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
  // Filter out the None-pct entries (no YoY signal) before slicing.
  const walkTop = useMemo(() => {
    return baseline.walk
      .filter(w => w.yoy_pct !== null)
      .slice(0, WALK_TOP_N);
  }, [baseline.walk]);

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
                {years.map((y, idx) => {
                  const val = y[key] as number | null | undefined;
                  const prior =
                    idx === 0
                      ? null
                      : (years[idx - 1][key] as number | null | undefined);
                  return (
                    <Cell
                      key={`${y.fiscal_year}-${String(key)}`}
                      value={val ?? null}
                      prior={prior ?? null}
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
                    prior={
                      // For Y1 forecast YoY, compare to the most-recent
                      // historical year (last entry in ``years``).
                      years.length
                        ? ((years[years.length - 1][key] as
                            | number
                            | null
                            | undefined) ?? null)
                        : null
                    }
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


/** One value cell in the historical table.
 *
 * Renders the dollar amount with a small YoY arrow + percent chip
 * when ``prior`` is non-null and non-zero. Em-dash when the value is
 * null (extractor didn't ship that line).
 */
function Cell({
  value,
  prior,
  isExpense,
  source,
  dealId,
  documentId,
  className,
}: {
  value: number | null;
  prior: number | null;
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

  const yoyPct =
    prior !== null && prior !== 0 ? (value - prior) / prior : null;

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
        {yoyPct !== null && Math.abs(yoyPct) >= 0.005 && (
          <span className={cn('text-[10px] tabular-nums', trendTone)}>
            {yoyPct > 0 ? '+' : ''}
            {fmtPct(yoyPct, 1)}
          </span>
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
