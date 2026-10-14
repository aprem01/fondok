'use client';

/**
 * R-067 — the Future P&L's "Benchmark" comparison column.
 *
 * When the deal carries a P&L benchmark report (CBRE Benchmarker / "Trends in
 * the Hotel Industry" / HotStats — doc type PNL_BENCHMARK, uploaded in the
 * Comp Set / Market slot), the worker maps its comp-set cost lines onto the
 * model's expense categories (``services/pnl_benchmark_map.py`` → the
 * ``pnl_benchmark.categories`` block of ``GET /deals/{id}/market-data``).
 * This panel sets each benchmark ratio beside the model's own Year-1 ratio
 * for the same line, on the same basis the projections table's "% Rev" cell
 * uses (departmental expense ÷ that department's revenue; everything else ÷
 * total revenue), plus the benchmark's POR / PAR.
 *
 * Read-only: nothing here writes an override or feeds an engine. Every value
 * is either the engine's Year-1 output or a number the worker extracted from
 * the report; anything missing renders "—". With no benchmark uploaded the
 * panel renders nothing at all.
 *
 * FON-97 — the per-row comparison now lives INSIDE the projections table
 * (``BenchmarkColumn.tsx``: a "Benchmark" sub-column in the Base-year group).
 * This panel stays as the fuller detail (POR / PAR per line), collapsed by
 * default under a "Benchmark detail" disclosure. The data is fetched once by
 * ``useDealBenchmark`` in ProjectionsSection and handed to both.
 */

import { Card } from '@/components/ui/Card';
import { cn } from '@/lib/format';

export type BenchmarkRatioBasis = 'department_revenue' | 'total_revenue';

export interface BenchmarkCategory {
  key: string;
  label: string;
  benchmark_line: string;
  /** Fraction (0.246 = 24.6%) on ``ratio_basis``; null when only POR / PAR. */
  ratio: number | null;
  ratio_basis: BenchmarkRatioBasis;
  ratio_source: string | null;
  par_usd: number | null;
  por_usd: number | null;
}

export interface BenchmarkBlock {
  peer_set_size: number | null;
  /** FON-97 — the benchmark report's filename (most recent extraction); null
   *  when the worker did not record one. */
  source_filename: string | null;
  categories: BenchmarkCategory[];
}

/** The slice of a projection year this panel reads. Structural, so the
 *  projections table's own year type satisfies it without an import. */
export interface BenchmarkModelYear {
  roomsRevenue?: number;
  fbRevenue?: number;
  otherOperatedRevenue?: number;
  totalRevenue?: number;
  availableRooms?: number;
  occupiedRooms?: number;
  deptRoomsExpense?: number;
  deptFbExpense?: number;
  deptOtherExpense?: number;
  undistAdminGeneral?: number;
  undistInfoTelecom?: number;
  undistSalesMarketing?: number;
  undistPropertyOps?: number;
  undistUtilities?: number;
  gop?: number;
  mgmtFee?: number;
  fixedPropertyTaxes?: number;
  fixedInsurance?: number;
  fixedRent?: number;
}

type LineSpec = {
  amount: (y: BenchmarkModelYear) => number | undefined;
  /** Department revenue for departmental lines; absent = total revenue. */
  departmentRevenue?: (y: BenchmarkModelYear) => number | undefined;
  basisLabel: string;
};

// Category key (worker ``CATEGORY_SPECS``) → the model's line for it.
const MODEL_LINES: Record<string, LineSpec> = {
  rooms: { amount: (y) => y.deptRoomsExpense, departmentRevenue: (y) => y.roomsRevenue, basisLabel: 'Rooms revenue' },
  food_beverage: { amount: (y) => y.deptFbExpense, departmentRevenue: (y) => y.fbRevenue, basisLabel: 'F&B revenue' },
  other_operated: {
    amount: (y) => y.deptOtherExpense,
    departmentRevenue: (y) => y.otherOperatedRevenue,
    basisLabel: 'Other Operated revenue',
  },
  administrative_general: { amount: (y) => y.undistAdminGeneral, basisLabel: 'Total revenue' },
  information_telecom: { amount: (y) => y.undistInfoTelecom, basisLabel: 'Total revenue' },
  sales_marketing: { amount: (y) => y.undistSalesMarketing, basisLabel: 'Total revenue' },
  property_operations: { amount: (y) => y.undistPropertyOps, basisLabel: 'Total revenue' },
  utilities: { amount: (y) => y.undistUtilities, basisLabel: 'Total revenue' },
  gop: { amount: (y) => y.gop, basisLabel: 'Total revenue' },
  mgmt_fee: { amount: (y) => y.mgmtFee, basisLabel: 'Total revenue' },
  property_taxes: { amount: (y) => y.fixedPropertyTaxes, basisLabel: 'Total revenue' },
  insurance: { amount: (y) => y.fixedInsurance, basisLabel: 'Total revenue' },
  rent: { amount: (y) => y.fixedRent, basisLabel: 'Total revenue' },
};

const num = (v: unknown): number | null =>
  typeof v === 'number' && Number.isFinite(v) ? v : null;

/** Read the ``pnl_benchmark`` block out of a market-data response. Returns
 *  null when the deal has no benchmark (or the worker predates R-067). */
export function parseBenchmarkBlock(marketData: unknown): BenchmarkBlock | null {
  const block = (marketData as { pnl_benchmark?: unknown } | null)?.pnl_benchmark as
    | { peer_set_size?: unknown; categories?: unknown; source_filename?: unknown }
    | null
    | undefined;
  if (!block || !Array.isArray(block.categories)) return null;
  const categories: BenchmarkCategory[] = [];
  for (const raw of block.categories as Record<string, unknown>[]) {
    if (!raw || typeof raw.key !== 'string' || typeof raw.label !== 'string') continue;
    categories.push({
      key: raw.key,
      label: raw.label,
      benchmark_line: typeof raw.benchmark_line === 'string' ? raw.benchmark_line : raw.key,
      ratio: num(raw.ratio),
      ratio_basis: raw.ratio_basis === 'department_revenue' ? 'department_revenue' : 'total_revenue',
      ratio_source: typeof raw.ratio_source === 'string' ? raw.ratio_source : null,
      par_usd: num(raw.par_usd),
      por_usd: num(raw.por_usd),
    });
  }
  if (categories.length === 0) return null;
  return {
    peer_set_size: num(block.peer_set_size),
    source_filename:
      typeof block.source_filename === 'string' && block.source_filename.trim() !== ''
        ? block.source_filename
        : null,
    categories,
  };
}

/** What the benchmark ratio divides by, in the words the table uses. */
export function benchmarkBasisLabel(category: Pick<BenchmarkCategory, 'key' | 'ratio_basis'>): string {
  return (
    MODEL_LINES[category.key]?.basisLabel ??
    (category.ratio_basis === 'department_revenue' ? 'department revenue' : 'Total revenue')
  );
}

/** The model's Year-1 ratio for a benchmark category, on the benchmark's
 *  basis. Null when the engine did not produce the line or its basis. */
export function modelRatioFor(
  category: Pick<BenchmarkCategory, 'key' | 'ratio_basis'>,
  year: BenchmarkModelYear | null | undefined,
): number | null {
  if (!year) return null;
  const spec = MODEL_LINES[category.key];
  if (!spec) return null;
  const amount = spec.amount(year);
  const denom =
    category.ratio_basis === 'department_revenue' && spec.departmentRevenue
      ? spec.departmentRevenue(year)
      : year.totalRevenue;
  if (typeof amount !== 'number' || !Number.isFinite(amount)) return null;
  if (typeof denom !== 'number' || !Number.isFinite(denom) || denom <= 0) return null;
  return amount / denom;
}

const pct = (r: number | null) => (r == null ? '—' : `${(r * 100).toFixed(1)}%`);
const usd = (v: number | null) =>
  v == null ? '—' : `$${v.toLocaleString('en-US', { maximumFractionDigits: v < 100 ? 2 : 0 })}`;

export const SOURCE_NOTE: Record<string, string> = {
  computed_from_totals: 'Computed from the report’s line total ÷ its revenue total',
  computed_from_par: 'Computed from the report’s line PAR ÷ its revenue PAR',
  reported: 'Ratio as printed in the report',
  legacy_summary: 'Peer-set summary ratio from the report',
};

export function BenchmarkComparisonTable({
  block,
  modelYear,
}: {
  block: BenchmarkBlock;
  modelYear: BenchmarkModelYear | null;
}) {
  const th = 'px-3 py-2 text-[10.5px] font-semibold uppercase tracking-wide text-ink-500 text-right';
  return (
    <table className="w-full text-[11.5px] border-collapse" data-testid="benchmark-comparison-table">
      <thead>
        <tr className="border-b border-border">
          <th className={cn(th, 'text-left')}>Line</th>
          <th className={th}>Model · Year 1</th>
          <th className={cn(th, 'bg-brand-50/60 text-brand-700')} data-testid="benchmark-column">
            Benchmark
          </th>
          <th className={th}>Δ vs benchmark</th>
          <th className={th}>Benchmark POR</th>
          <th className={th}>Benchmark PAR</th>
        </tr>
      </thead>
      <tbody>
        {block.categories.map((c) => {
          const model = modelRatioFor(c, modelYear);
          const delta = model != null && c.ratio != null ? (model - c.ratio) * 100 : null;
          const basis = benchmarkBasisLabel(c);
          return (
            <tr key={c.key} className="border-b border-border/60 last:border-0" data-benchmark-key={c.key}>
              <td className="px-3 py-1.5 text-left text-ink-900">
                {c.label}
                <span className="ml-1.5 text-[10.5px] text-ink-400">% of {basis}</span>
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink-900">{pct(model)}</td>
              <td
                className="px-3 py-1.5 text-right tabular-nums font-medium text-brand-700 bg-brand-50/40"
                title={c.ratio_source ? SOURCE_NOTE[c.ratio_source] : 'The report publishes no ratio for this line'}
              >
                {pct(c.ratio)}
              </td>
              <td
                className={cn(
                  'px-3 py-1.5 text-right tabular-nums',
                  delta == null ? 'text-ink-400' : 'text-ink-700',
                )}
              >
                {delta == null ? '—' : `${delta >= 0 ? '+' : ''}${delta.toFixed(1)} pts`}
              </td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink-700">{usd(c.por_usd)}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink-700">{usd(c.par_usd)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/**
 * The detail panel — collapsed by default (FON-97: the per-row comparison is
 * in the table's Benchmark column; this keeps POR / PAR and the full
 * side-by-side for whoever wants it). ``block`` comes from the host's single
 * ``useDealBenchmark`` read; null renders nothing at all.
 */
export default function BenchmarkComparison({
  block,
  modelYear,
}: {
  block: BenchmarkBlock | null;
  modelYear: BenchmarkModelYear | null;
}) {
  if (!block) return null;
  return (
    <Card className="mt-4 p-0 overflow-hidden" aria-label="P&L benchmark comparison">
      <details data-testid="benchmark-detail">
        <summary
          className="cursor-pointer select-none px-4 py-3 text-[12.5px] font-semibold text-ink-900 hover:bg-ink-300/5"
          data-testid="benchmark-detail-summary"
        >
          Benchmark detail
          <span className="ml-2 text-[11px] font-normal text-ink-500">
            model vs CBRE / HotStats comp set
            {block.peer_set_size != null ? ` · ${block.peer_set_size} hotels` : ''}
            {block.source_filename ? ` · ${block.source_filename}` : ''}
            {' · read-only'}
          </span>
        </summary>
        <div className="px-4 pb-3 border-t border-border">
          <p className="text-[11.5px] text-ink-500 mt-2 leading-snug">
            The model’s Year-1 cost ratios beside the uploaded P&amp;L benchmark, with the
            benchmark’s POR / PAR per line. The same ratios sit in the table’s Benchmark column.
            Read-only — the benchmark does not change the model.
          </p>
        </div>
        <div className="overflow-x-auto">
          <BenchmarkComparisonTable block={block} modelYear={modelYear} />
        </div>
      </details>
    </Card>
  );
}
