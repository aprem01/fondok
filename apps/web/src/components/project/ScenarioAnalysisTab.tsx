'use client';

/**
 * Scenario Analysis (FON-53) — the sensitivity experience, moved off Overview
 * into its own tab and reworked into a flexible, one-at-a-time explorer.
 *
 * The investor picks a sensitivity from the dropdown and sees a single compact
 * grid: the two driving assumptions on the axes, the resulting investment
 * metric in each cell, the base case highlighted, and a green → yellow → red
 * heat treatment (green = better outcome). No giant Excel-style wall of tables,
 * and no horizontal scrolling. Every value is live — the worker's sensitivity
 * engine re-runs the returns model across each grid, so this always reflects
 * the current underwriting.
 */

import { useMemo, useState } from 'react';
import { ChevronDown, Grid3x3 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn, fmtPct, fmtCurrency } from '@/lib/format';
import { useEngineOutputs, getEngineField } from '@/lib/hooks/useEngineOutputs';

interface SensCell {
  row_value: number;
  col_value: number;
  value: number;
  is_base: boolean;
}
interface SensMatrix {
  key: string;
  label: string;
  metric: string;
  row_variable: string;
  col_variable: string;
  rows: number[];
  cols: number[];
  cells: SensCell[];
}

const VAR_LABEL: Record<string, string> = {
  exit_cap_rate: 'Exit Cap',
  revpar_growth: 'RevPAR Growth',
  purchase_price: 'Purchase Price',
  loan_amount: 'Loan Amount',
  ltv: 'Leverage (LTV)',
  interest_rate: 'Loan Rate',
  hold_years: 'Hold Period',
};

function fmtAxis(variable: string, v: number): string {
  if (variable === 'purchase_price' || variable === 'loan_amount') {
    return fmtCurrency(v, { compact: true });
  }
  if (variable === 'hold_years') return `${Math.round(v)}y`;
  // Exit cap wants 2 decimals (6.50%), growth 1 (3.5%).
  return fmtPct(v, variable === 'exit_cap_rate' ? 2 : 1);
}

function fmtMetric(metric: string, v: number): string {
  if (metric === 'equity_multiple') return `${v.toFixed(2)}x`;
  if (metric === 'gross_sale_price') return fmtCurrency(v, { compact: true });
  return fmtPct(v, 1); // levered_irr / unlevered_irr / year_one_coc
}

// Heat treatment — all supported metrics are "higher is better", so scale each
// cell's value across the matrix min..max onto a red(0) → yellow(60) → green(120)
// hue. Pastel background keeps the numbers readable in both themes.
function heatBg(v: number, min: number, max: number): string {
  if (!(max > min)) return 'hsl(140 45% 88%)';
  const t = Math.max(0, Math.min(1, (v - min) / (max - min)));
  const hue = t * 120;
  return `hsl(${hue.toFixed(0)} 62% 86%)`;
}

export default function ScenarioAnalysisTab({ dealId }: { dealId: string }) {
  const { outputs } = useEngineOutputs(dealId);

  const matrices = useMemo<SensMatrix[]>(() => {
    const raw = getEngineField<SensMatrix[]>(outputs, 'sensitivity', 'matrices');
    return Array.isArray(raw) ? raw : [];
  }, [outputs]);

  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const selected = useMemo(
    () => matrices.find((m) => m.key === selectedKey) ?? matrices[0] ?? null,
    [matrices, selectedKey],
  );

  if (matrices.length === 0 || !selected) {
    return (
      <Card className="p-8 text-center">
        <div className="w-12 h-12 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
          <Grid3x3 size={20} className="text-brand-500" />
        </div>
        <h3 className="text-[14px] font-semibold text-ink-900 mb-1">Scenario analysis not computed</h3>
        <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
          Run the underwriting engines on this deal and the sensitivity grids populate here —
          Levered IRR and Equity Multiple flexed across exit cap, RevPAR growth, and entry basis.
        </p>
      </Card>
    );
  }

  // Index cells by (row,col) for O(1) lookup + compute the value range for heat.
  const values = selected.cells.map((c) => c.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const cellAt = (r: number, c: number) =>
    selected.cells.find((x) => x.row_value === r && x.col_value === c) ?? null;

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-1">
          <div>
            <h2 className="text-[15px] font-semibold text-ink-900">Scenario Analysis</h2>
            <p className="text-[12.5px] text-ink-500 mt-0.5">
              How sensitive is this deal to its key underwriting assumptions? Pick a sensitivity;
              the base case is outlined.
            </p>
          </div>
          {/* Sensitivity selector */}
          <div className="relative">
            <select
              value={selected.key}
              onChange={(e) => setSelectedKey(e.target.value)}
              className="appearance-none pl-3 pr-9 py-2 text-[12.5px] font-medium rounded-md border border-border bg-white text-ink-900 focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500 cursor-pointer"
              aria-label="Select sensitivity"
            >
              {matrices.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
            <ChevronDown
              size={15}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-ink-400 pointer-events-none"
            />
          </div>
        </div>

        {/* Axis legend */}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-[11px] text-ink-500 mt-3 mb-4">
          <span>
            Rows: <span className="font-medium text-ink-700">{VAR_LABEL[selected.row_variable] ?? selected.row_variable}</span>
          </span>
          <span>
            Columns: <span className="font-medium text-ink-700">{VAR_LABEL[selected.col_variable] ?? selected.col_variable}</span>
          </span>
          <span>
            Cell: <span className="font-medium text-ink-700">{selected.label.split('—')[0].trim()}</span>
          </span>
          <span className="inline-flex items-center gap-1.5 ml-auto">
            <span className="inline-block w-3 h-3 rounded-sm" style={{ background: 'hsl(0 62% 86%)' }} /> worse
            <span className="inline-block w-3 h-3 rounded-sm" style={{ background: 'hsl(60 62% 86%)' }} />
            <span className="inline-block w-3 h-3 rounded-sm" style={{ background: 'hsl(120 62% 86%)' }} /> better
          </span>
        </div>

        {/* The grid — 1 corner + N columns; fits without horizontal scroll. */}
        <div className="w-full">
          <table className="w-full border-separate border-spacing-1 text-center">
            <thead>
              <tr>
                <th className="w-[15%]">
                  <span className="block text-[9.5px] uppercase tracking-wide text-ink-400 font-medium text-right pr-1">
                    {VAR_LABEL[selected.row_variable] ?? selected.row_variable}
                    {'  ↓'}
                  </span>
                </th>
                {selected.cols.map((c) => (
                  <th
                    key={c}
                    className="text-[11px] font-semibold text-ink-700 tabular-nums pb-0.5"
                  >
                    {fmtAxis(selected.col_variable, c)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {selected.rows.map((r) => (
                <tr key={r}>
                  <th className="text-[11px] font-semibold text-ink-700 tabular-nums text-right pr-1">
                    {fmtAxis(selected.row_variable, r)}
                  </th>
                  {selected.cols.map((c) => {
                    const cell = cellAt(r, c);
                    if (!cell) return <td key={c} />;
                    return (
                      <td
                        key={c}
                        className={cn(
                          'rounded-md py-2 text-[12px] font-semibold text-ink-900 tabular-nums transition-shadow',
                          cell.is_base && 'ring-2 ring-ink-900 ring-offset-1',
                        )}
                        style={{ background: heatBg(cell.value, min, max) }}
                        title={cell.is_base ? 'Base case' : undefined}
                      >
                        {fmtMetric(selected.metric, cell.value)}
                        {cell.is_base && (
                          <span className="block text-[8.5px] font-medium uppercase tracking-wide text-ink-500 leading-none mt-0.5">
                            base
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="text-[11px] text-ink-400 mt-3 leading-relaxed">
          Grids are computed live by the sensitivity engine, which re-runs the underwriting model
          across each cell — including the Debt engine for the loan-amount × rate grids.
          Partner-distribution (WDP) sensitivities are coming next.
        </p>
      </Card>
    </div>
  );
}
