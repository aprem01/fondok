'use client';
/**
 * PricingSensitivityPanel — Wave 2 P2.8
 *
 * 5x5 heatmap flexing exit cap rate × NOI multiplier. Rows = NOI
 * multiplier (1.15× at top, 0.85× at bottom). Cols = exit cap rate
 * (cheapest left, most expensive right). Cells colour-coded against
 * the deal's target IRR (default 15%):
 *
 *   green    — IRR >= target
 *   amber    — IRR within 200bp of target
 *   red      — IRR below target - 200bp
 *
 * Center cell is the base case and is rendered with a distinctive
 * outline + a "Base" pill. Hover any cell to see the full payload
 * (going-in cap, DSCR Y1, EM).
 *
 * Read-only: this panel never persists state. The grid is recomputed
 * server-side on each request.
 */
import { useEffect, useMemo, useState } from 'react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { cn, fmtPct } from '@/lib/format';
import { api } from '@/lib/api';
import type {
  PricingSensitivityCell,
  PricingSensitivityResponse,
} from '@/lib/api';

interface Props {
  dealId: string;
  /** IRR hurdle used for the breakeven sweep + heatmap colouring.
   *  Defaults to 15% — institutional hospitality value-add hurdle. */
  targetIrr?: number;
}

type CellTier = 'pass' | 'marginal' | 'fail';

function classifyCell(irr: number, target: number): CellTier {
  if (irr >= target) return 'pass';
  if (irr >= target - 0.02) return 'marginal';
  return 'fail';
}

const TIER_CLASSES: Record<CellTier, string> = {
  pass: 'bg-emerald-50 text-emerald-900 border-emerald-200',
  marginal: 'bg-amber-50 text-amber-900 border-amber-200',
  fail: 'bg-rose-50 text-rose-900 border-rose-200',
};

/** Build a 2D matrix indexed [rowIdx][colIdx]. Rows = NOI multiplier
 *  (descending — high at top), cols = exit cap (ascending — left to
 *  right). The worker already emits cells in this row-major order, but
 *  we re-derive for safety so a swap on the worker doesn't silently
 *  re-shape the heatmap. */
function gridify(grid: PricingSensitivityResponse) {
  const uniqueCaps = Array.from(
    new Set(grid.cells.map(c => c.exit_cap_pct)),
  ).sort((a, b) => a - b);
  const uniqueNois = Array.from(
    new Set(grid.cells.map(c => c.noi_multiplier)),
  ).sort((a, b) => b - a);

  const byKey = new Map<string, PricingSensitivityCell>();
  for (const c of grid.cells) {
    byKey.set(`${c.exit_cap_pct}__${c.noi_multiplier}`, c);
  }
  const rows: (PricingSensitivityCell | undefined)[][] = uniqueNois.map(nm =>
    uniqueCaps.map(cap => byKey.get(`${cap}__${nm}`)),
  );
  return { uniqueCaps, uniqueNois, rows };
}

export default function PricingSensitivityPanel({
  dealId,
  targetIrr = 0.15,
}: Props) {
  const [grid, setGrid] = useState<PricingSensitivityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hoverCell, setHoverCell] = useState<PricingSensitivityCell | null>(
    null,
  );

  const fetchGrid = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.analysis.pricing.sensitivity(dealId, {
        target_irr: targetIrr,
      });
      setGrid(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load grid');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (dealId) void fetchGrid();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dealId, targetIrr]);

  const matrix = useMemo(() => (grid ? gridify(grid) : null), [grid]);

  return (
    <Card className="p-4">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <h3 className="text-[14px] font-semibold text-ink-900">
            Pricing Sensitivity
          </h3>
          <p className="text-[12px] text-ink-500">
            Exit cap × NOI multiplier. Cells coloured against{' '}
            {fmtPct(targetIrr)} target IRR.
          </p>
        </div>
        <Button
          variant="ghost"
          onClick={() => void fetchGrid()}
          disabled={loading}
        >
          {loading ? 'Recomputing…' : 'Refresh'}
        </Button>
      </div>

      {error && (
        <div className="text-[12.5px] text-rose-700 bg-rose-50 border border-rose-200 rounded px-3 py-2 mb-3">
          {error}
        </div>
      )}

      {matrix && grid && (
        <>
          <div className="overflow-x-auto">
            <table className="text-[11.5px] border-collapse">
              <thead>
                <tr>
                  <th className="p-1" />
                  {matrix.uniqueCaps.map(cap => (
                    <th
                      key={cap}
                      className="px-2 py-1 text-center text-ink-500 font-medium"
                    >
                      Cap {fmtPct(cap)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.rows.map((row, rIdx) => (
                  <tr key={rIdx}>
                    <td className="pr-2 text-right text-ink-500 font-medium whitespace-nowrap">
                      NOI ×{matrix.uniqueNois[rIdx].toFixed(3)}
                    </td>
                    {row.map((cell, cIdx) => {
                      if (!cell) return <td key={cIdx} className="p-1" />;
                      const tier = classifyCell(cell.levered_irr, targetIrr);
                      const isBase =
                        Math.abs(cell.exit_cap_pct - grid.base_exit_cap_pct) <
                          1e-9 &&
                        Math.abs(cell.noi_multiplier - 1.0) < 1e-9;
                      return (
                        <td key={cIdx} className="p-1">
                          <div
                            onMouseEnter={() => setHoverCell(cell)}
                            onMouseLeave={() => setHoverCell(null)}
                            className={cn(
                              'rounded border px-2 py-2 min-w-[72px] cursor-default',
                              TIER_CLASSES[tier],
                              isBase && 'ring-2 ring-brand-500',
                              cell.breaches_dscr_floor &&
                                'outline outline-1 outline-dashed outline-rose-400',
                            )}
                            title={
                              cell.breaches_dscr_floor
                                ? 'DSCR < 1.0x'
                                : undefined
                            }
                          >
                            <div className="text-[13px] font-semibold">
                              {fmtPct(cell.levered_irr)}
                            </div>
                            <div className="text-[10.5px] opacity-70">
                              {cell.equity_multiple.toFixed(2)}× EM
                            </div>
                            {isBase && (
                              <div className="text-[9px] uppercase tracking-wider font-semibold opacity-70 mt-0.5">
                                Base
                              </div>
                            )}
                          </div>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Hover detail */}
          {hoverCell && (
            <div className="mt-3 text-[12px] text-ink-700 bg-ink-50 border border-border rounded px-3 py-2">
              <span className="font-medium">
                Exit cap {fmtPct(hoverCell.exit_cap_pct)}, NOI ×
                {hoverCell.noi_multiplier.toFixed(3)}:
              </span>{' '}
              IRR {fmtPct(hoverCell.levered_irr)} · EM{' '}
              {hoverCell.equity_multiple.toFixed(2)}× · Going-in cap{' '}
              {fmtPct(hoverCell.going_in_cap_rate)} · DSCR Y1{' '}
              {hoverCell.dscr_y1.toFixed(2)}×
              {hoverCell.breaches_dscr_floor && (
                <span className="ml-2 text-rose-700 font-semibold">
                  ⚠ DSCR &lt; 1.0
                </span>
              )}
            </div>
          )}

          {/* Breakeven summary */}
          <div className="mt-3 grid grid-cols-2 gap-3 text-[12px]">
            <div className="border border-border rounded p-2">
              <div className="text-ink-500">
                Breakeven exit cap ({fmtPct(targetIrr)} IRR)
              </div>
              <div className="font-semibold text-ink-900">
                {grid.breakeven_exit_cap_pct != null
                  ? fmtPct(grid.breakeven_exit_cap_pct)
                  : '—'}
              </div>
            </div>
            <div className="border border-border rounded p-2">
              <div className="text-ink-500">
                Breakeven NOI multiplier ({fmtPct(targetIrr)} IRR)
              </div>
              <div className="font-semibold text-ink-900">
                {grid.breakeven_noi_multiplier != null
                  ? `×${grid.breakeven_noi_multiplier.toFixed(3)}`
                  : '—'}
              </div>
            </div>
          </div>
        </>
      )}
    </Card>
  );
}
