/**
 * Partnership tab — "Allocation of Projected Proceeds" rows (FON-66).
 *
 * Sam (9-14): the allocation preview listed only the tiers that received
 * dollars (Return of Capital / Preferred / one promote band), so a stack whose
 * promote never climbed looked like it had no higher tiers at all. The engine
 * still reports only the rows that carry cash (its output on the Kimpton
 * golden is byte-identical), so the tab merges the configured promote stack
 * in here: every tier the analyst configured is listed, in stack order, and a
 * tier the waterfall never reached shows $0 with a "not reached" caption.
 *
 * Pure functions, so they are unit-tested without rendering the tab.
 */

export type AllocationKind = 'return_of_capital' | 'preferred' | 'catch_up' | 'promote';

/** One row of the dollar waterfall as the worker reports it. */
export interface TierAllocationRow {
  label: string;
  kind: AllocationKind;
  gp_amount: number;
  lp_amount: number;
  total_amount: number;
}

/** One promote band as the tab resolves it (analyst override, else the
 *  term the deal carries); undefined when the analyst has not set it yet. */
export interface ConfiguredTier {
  hurdle: number | undefined;
  gp: number | undefined;
}

export type AllocationRowStatus =
  /** The engine reported dollars through this row. */
  | 'reached'
  /** A complete, configured band the waterfall never climbed to — $0. */
  | 'not_reached'
  /** A half-entered band the engine omits (no hurdle or no split) — no amounts. */
  | 'incomplete';

export interface AllocationPreviewRow extends TierAllocationRow {
  status: AllocationRowStatus;
}

/**
 * Format a fraction as the worker's ``f"{x:.0%}"`` does: ``x × 100`` rounded
 * to an integer, ties to even (Python's float formatting), so the label built
 * here matches the label the engine keys its rows by.
 */
export function pct0(fraction: number): string {
  const x = fraction * 100;
  const floor = Math.floor(x);
  const diff = x - floor;
  let n: number;
  if (diff === 0.5) n = floor % 2 === 0 ? floor : floor + 1;
  else n = Math.round(x);
  return `${n}%`;
}

/**
 * Mirror of the worker's ``promote_tier_label`` (apps/worker/app/engines/
 * partnership.py): the first tier with no GP share is the preferred band
 * ("Preferred (to 10%)"); the top tier of a multi-tier stack is open-ended
 * above the previous hurdle ("Tier 6 (>30%)"); every other tier runs to its
 * hurdle ("Tier 2 (to 15%)").
 */
export function promoteTierLabel(
  position: number,
  hurdle: number,
  gpSplit: number,
  prevHurdle: number | undefined,
  isTop: boolean,
): string {
  if (position === 0 && gpSplit === 0) return `Preferred (to ${pct0(hurdle)})`;
  if (isTop && prevHurdle != null) return `Tier ${position + 1} (>${pct0(prevHurdle)})`;
  return `Tier ${position + 1} (to ${pct0(hurdle)})`;
}

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/**
 * The preview rows: the engine's structural rows (return of capital,
 * preferred, catch-up) in the order it emitted them, then one row per
 * configured promote band in stack order — the engine's row when it carries
 * dollars, a $0 "not reached" row when it does not, a no-amount "incomplete"
 * row for a half-entered band — and finally any engine promote row that no
 * configured band accounts for (an older run, or a stack edited since), so no
 * distributed dollar is ever dropped from the table.
 *
 * With no configured bands (or no engine rows at all) the engine's rows are
 * returned as they are.
 */
export function allocationPreviewRows(
  allocations: readonly TierAllocationRow[] | undefined,
  configured: readonly ConfiguredTier[],
): AllocationPreviewRow[] {
  const engineRows: AllocationPreviewRow[] = (allocations ?? []).map((a) => ({ ...a, status: 'reached' }));
  if (engineRows.length === 0 || configured.length === 0) return engineRows;

  // The packed stack the worker builds: complete bands only, in stack order,
  // labelled positionally exactly as the engine labels them.
  const complete = configured
    .map((t, i) => ({ t, i }))
    .filter(({ t }) => isNum(t.hurdle) && isNum(t.gp));
  const labelByIndex = new Map<number, string>();
  complete.forEach(({ t, i }, pos) => {
    const prev = pos > 0 ? complete[pos - 1].t.hurdle : undefined;
    labelByIndex.set(
      i,
      promoteTierLabel(pos, t.hurdle as number, t.gp as number, prev, pos === complete.length - 1),
    );
  });

  const structural = engineRows.filter((r) => r.kind !== 'promote');
  const promoteRows = engineRows.filter((r) => r.kind === 'promote');
  const used = new Set<number>();
  const merged: AllocationPreviewRow[] = [];
  configured.forEach((t, i) => {
    const label = labelByIndex.get(i);
    if (label == null) {
      merged.push({
        label: `Tier ${i + 1} · incomplete`,
        kind: 'promote',
        gp_amount: 0,
        lp_amount: 0,
        total_amount: 0,
        status: 'incomplete',
      });
      return;
    }
    const at = promoteRows.findIndex((r, k) => !used.has(k) && r.label === label);
    if (at >= 0) {
      used.add(at);
      merged.push(promoteRows[at]);
    } else {
      merged.push({ label, kind: 'promote', gp_amount: 0, lp_amount: 0, total_amount: 0, status: 'not_reached' });
    }
  });
  const leftovers = promoteRows.filter((_, k) => !used.has(k));
  return [...structural, ...merged, ...leftovers];
}
