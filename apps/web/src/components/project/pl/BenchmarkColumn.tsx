'use client';

/**
 * FON-97 — the Future P&L's in-table "Benchmark" sub-column.
 *
 * Sam / Rani: each P&L line's projected expense ratio should sit beside the
 * CBRE / HotStats comp-set ratio on the SAME row, not in a panel underneath.
 * So the Year-1 (Base year) column group grows one sub-column when the deal
 * carries a benchmark block, and every statement row prints the benchmark
 * ratio for its line — on the row's own basis (departmental expense ÷ that
 * department's revenue; everything else ÷ total revenue, exactly the basis
 * the "% Rev" cell uses) — plus the model-minus-benchmark difference in
 * points.
 *
 * Like the E-016 MethodChip, the column is wired through a context so
 * ``FullRow`` only names its line (``benchmarkKey``) and the cell looks the
 * benchmark up itself. No provider (no benchmark on the deal, or the header
 * switch is off) = the cell renders nothing and the column does not exist.
 *
 * Honesty rules: a row with no benchmark mapping, a line the report did not
 * publish, or a basis that disagrees with the row's all render "—" with the
 * reason in the tooltip. The delta's colour marks DIRECTION only (model
 * above / below the benchmark) — no "good / bad" threshold is invented.
 *
 * FON-109 — the benchmark never feeds an engine on its own. On the rows the
 * worker serves a suggestion for (Rooms and F&B departmental expense,
 * Management Fees) the compared cell carries an "Apply benchmark" action
 * (``BenchmarkSuggestion.tsx``): it writes the analyst override with a
 * prefilled, editable note and re-runs the model. The cell itself writes
 * nothing.
 */

import { createContext, useContext } from 'react';
import { cn } from '@/lib/format';
import { ApplyBenchmarkAction, CATEGORY_TO_SUGGESTION_KEY } from './BenchmarkSuggestion';
import {
  SOURCE_NOTE,
  benchmarkBasisLabel,
  type BenchmarkBlock,
  type BenchmarkCategory,
  type BenchmarkRatioBasis,
} from './BenchmarkComparison';

export interface BenchmarkColumnCtx {
  block: BenchmarkBlock;
  byKey: ReadonlyMap<string, BenchmarkCategory>;
}

export const BenchmarkColumnContext = createContext<BenchmarkColumnCtx | null>(null);

/** Build the provider value once per block. */
export function benchmarkColumnCtx(block: BenchmarkBlock | null): BenchmarkColumnCtx | null {
  if (!block) return null;
  return { block, byKey: new Map(block.categories.map((c) => [c.key, c])) };
}

export function useBenchmarkColumn(): BenchmarkColumnCtx | null {
  return useContext(BenchmarkColumnContext);
}

// ── Header switch persistence (per device) ──────────────────────────────

export const BENCHMARK_TOGGLE_STORAGE_KEY = 'fondok.futurePl.showBenchmark';

/** The remembered switch state, or null when nothing is stored / storage is
 *  unavailable (private mode, SSR) — the caller then defaults to ON. */
export function readStoredBenchmarkToggle(): boolean | null {
  try {
    if (typeof window === 'undefined') return null;
    const raw = window.localStorage.getItem(BENCHMARK_TOGGLE_STORAGE_KEY);
    return raw === '1' ? true : raw === '0' ? false : null;
  } catch {
    return null;
  }
}

export function storeBenchmarkToggle(show: boolean): void {
  try {
    if (typeof window === 'undefined') return;
    window.localStorage.setItem(BENCHMARK_TOGGLE_STORAGE_KEY, show ? '1' : '0');
  } catch {
    // Storage blocked — the switch still works for this page view.
  }
}

// ── Copy ────────────────────────────────────────────────────────────────

export const NOT_PUBLISHED_IN_BENCHMARK = 'not published in the benchmark';

const pct1 = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;
const usd = (v: number | null) =>
  v == null ? '—' : `$${v.toLocaleString('en-US', { maximumFractionDigits: v < 100 ? 2 : 0 })}`;

/** Model − benchmark, in percentage points. Null when either side is missing. */
export function benchmarkDeltaPts(modelPct: number | null, benchmarkRatio: number | null): number | null {
  if (modelPct == null || benchmarkRatio == null) return null;
  if (!Number.isFinite(modelPct) || !Number.isFinite(benchmarkRatio)) return null;
  return modelPct - benchmarkRatio * 100;
}

export type BenchmarkDirection = 'above' | 'below' | 'level';

/** Direction of the model vs the benchmark, at the printed precision (0.1 pt). */
export function benchmarkDirection(deltaPts: number): BenchmarkDirection {
  const rounded = Math.round(deltaPts * 10) / 10;
  if (rounded > 0) return 'above';
  if (rounded < 0) return 'below';
  return 'level';
}

function tooltipFor(c: BenchmarkCategory, block: BenchmarkBlock): string {
  const parts = [
    `${c.label} — benchmark line "${c.benchmark_line}", % of ${benchmarkBasisLabel(c)}`,
    block.peer_set_size != null ? `Peer set: ${block.peer_set_size} hotels` : 'Peer-set size not stated in the report',
    `POR ${usd(c.por_usd)} · PAR ${usd(c.par_usd)}`,
    `Source: ${block.source_filename ?? 'the uploaded P&L benchmark report'}`,
    c.ratio_source ? SOURCE_NOTE[c.ratio_source] ?? c.ratio_source : 'The report publishes no ratio for this line',
  ];
  return parts.join('\n');
}

// ── Cells ───────────────────────────────────────────────────────────────

/** The sub-column header, rendered only inside a provider. */
export function BenchmarkHeaderCell({ className }: { className?: string }) {
  const ctx = useBenchmarkColumn();
  if (!ctx) return null;
  return (
    <th
      className={cn(className, 'text-brand-700')}
      data-testid="projection-benchmark-col-header"
      title={`CBRE / HotStats comp-set ratio for the line, on the same basis as % Rev, and the model's Year-1 difference in points.${
        ctx.block.source_filename ? ` Source: ${ctx.block.source_filename}.` : ''
      } The benchmark changes the model only where you apply it (Apply benchmark), as an analyst override with a note.`}
    >
      Benchmark
    </th>
  );
}

/**
 * One row's benchmark cell for the Year-1 column group.
 *
 * ``modelPct`` is the row's own "% Rev" value (0..100) on ``rowBasis`` — the
 * same number the % cell prints — so the delta compares like with like.
 */
export function BenchmarkCell({
  benchmarkKey,
  modelPct,
  rowBasis,
  className,
}: {
  /** The ``pnl_benchmark.categories[].key`` this row benchmarks; absent = unmapped row. */
  benchmarkKey?: string;
  modelPct: number | null;
  rowBasis: BenchmarkRatioBasis;
  className?: string;
}) {
  const ctx = useBenchmarkColumn();
  if (!ctx) return null;
  const base = cn(className, 'text-right tabular-nums');
  const c = benchmarkKey ? ctx.byKey.get(benchmarkKey) : undefined;

  if (!c) {
    return (
      <td
        className={cn(base, 'text-ink-400')}
        data-testid="benchmark-cell"
        data-benchmark-key={benchmarkKey ?? ''}
        data-benchmark-state="unmapped"
        title={`No benchmark for this line — ${NOT_PUBLISHED_IN_BENCHMARK}.`}
      >
        —
      </td>
    );
  }
  if (c.ratio_basis !== rowBasis) {
    // Should not happen for the 13 wired lines; refuse to compare rather than
    // print a ratio on the wrong denominator.
    return (
      <td
        className={cn(base, 'text-ink-400')}
        data-testid="benchmark-cell"
        data-benchmark-key={c.key}
        data-benchmark-state="basis_mismatch"
        title={`The benchmark ratio is % of ${benchmarkBasisLabel(c)}, but this row's ratio is on a different basis — not compared.`}
      >
        —
      </td>
    );
  }
  if (c.ratio == null) {
    return (
      <td
        className={cn(base, 'text-ink-400')}
        data-testid="benchmark-cell"
        data-benchmark-key={c.key}
        data-benchmark-state="no_ratio"
        title={`${tooltipFor(c, ctx.block)}\nThe report prices this line per room only — ratio ${NOT_PUBLISHED_IN_BENCHMARK}.`}
      >
        —
      </td>
    );
  }

  const delta = benchmarkDeltaPts(modelPct, c.ratio);
  const direction = delta == null ? null : benchmarkDirection(delta);
  const deltaClass =
    direction === 'above' ? 'text-warn-700' : direction === 'below' ? 'text-brand-700' : 'text-ink-500';
  const glyph = direction === 'above' ? '▲' : direction === 'below' ? '▼' : '';
  return (
    <td
      className={cn(base, 'text-ink-900')}
      data-testid="benchmark-cell"
      data-benchmark-key={c.key}
      data-benchmark-state="compared"
      data-benchmark-basis={c.ratio_basis}
      data-direction={direction ?? 'none'}
      title={`${tooltipFor(c, ctx.block)}${
        delta == null
          ? '\nThe model did not produce this line for Year 1, so there is no difference to show.'
          : `\nModel Year 1 ${modelPct!.toFixed(2)}% − benchmark ${(c.ratio * 100).toFixed(2)}% = ${delta >= 0 ? '+' : ''}${delta.toFixed(2)} pts (colour marks direction only)`
      }`}
    >
      <span className="font-medium text-brand-700" data-testid="benchmark-ratio">
        {pct1(c.ratio)}
      </span>
      <span
        className={cn('ml-1 text-[10px]', delta == null ? 'text-ink-400' : deltaClass)}
        data-testid="benchmark-delta"
      >
        {delta == null ? '—' : `${glyph}${delta >= 0 ? '+' : ''}${delta.toFixed(1)} pts`}
      </span>
      {CATEGORY_TO_SUGGESTION_KEY[c.key] && (
        <div>
          <ApplyBenchmarkAction overrideKey={CATEGORY_TO_SUGGESTION_KEY[c.key]} compact />
        </div>
      )}
    </td>
  );
}
