/**
 * FON-63 — the negative-NOI warning sentence.
 *
 * When a projection year's NOI is negative the debt engine cannot cover its
 * debt service from operations: DSCR stops being meaningful (the engine emits
 * `dscr: null`) and the uncovered debt service is a shortfall. The engine
 * publishes its own sentence (`debt.outputs.noi_warning`), which every tab
 * renders VERBATIM. This module only composes the fallback when a run carries
 * the structured fields but no sentence, from engine numbers alone — nothing is
 * estimated here.
 *
 * Pure: no React, no hooks. `readNoiWarning` reads a plain engine-outputs
 * envelope so the Debt / Cash Flow / Returns tabs share one reading.
 */

const MINUS = '−';

const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** Whole-dollar USD with thousands separators and a leading (true) minus:
 *  `-69983.4 → "−$69,983"`. */
export function fmtUsdSigned(n: number): string {
  const abs = Math.round(Math.abs(n)).toLocaleString('en-US');
  return `${n < 0 && Math.round(Math.abs(n)) !== 0 ? MINUS : ''}$${abs}`;
}

/** A shortfall amount: `$1.92M` from a million up, whole dollars below. */
export function fmtUsdShort(n: number): string {
  if (Math.abs(n) >= 1e6) {
    return `${n < 0 ? MINUS : ''}$${(Math.abs(n) / 1e6).toFixed(2)}M`;
  }
  return fmtUsdSigned(n);
}

/** `[1] → "Year 1"`, `[1,2] → "Years 1 & 2"`, `[1,2,3] → "Years 1, 2 & 3"`. */
export function yearList(years: readonly number[]): string {
  const ys = [...new Set(years)].sort((a, b) => a - b);
  if (ys.length === 0) return '';
  if (ys.length === 1) return `Year ${ys[0]}`;
  return `Years ${ys.slice(0, -1).join(', ')} & ${ys[ys.length - 1]}`;
}

export interface NoiWarningInput {
  /** 1-based years whose NOI is negative. */
  negativeYears: readonly number[];
  /** NOI for a 1-based year, when the run carries it. */
  noiForYear?: (year: number) => number | undefined;
  /** Σ uncovered debt service. Omitted from the sentence when absent or 0. */
  totalShortfallUsd?: number | null;
  /** 1-based years whose DSCR is not meaningful; defaults to `negativeYears`. */
  dscrNaYears?: readonly number[];
}

/**
 * `Year 1 NOI is negative (−$69,983) · debt service shortfall $1.92M · DSCR N/A for Year 1`
 *
 * Returns null when no year is negative — there is nothing to warn about.
 */
export function composeNoiWarning(input: NoiWarningInput): string | null {
  const neg = [...new Set(input.negativeYears)].filter(isNum).sort((a, b) => a - b);
  if (neg.length === 0) return null;

  const nois = neg.map((y) => input.noiForYear?.(y));
  const known = nois.every(isNum);
  const head =
    `${yearList(neg)} NOI is negative` +
    (known ? ` (${(nois as number[]).map(fmtUsdSigned).join(' / ')})` : '');

  const parts = [head];
  if (isNum(input.totalShortfallUsd) && input.totalShortfallUsd > 0) {
    parts.push(`debt service shortfall ${fmtUsdShort(input.totalShortfallUsd)}`);
  }
  const na = input.dscrNaYears && input.dscrNaYears.length > 0 ? input.dscrNaYears : neg;
  parts.push(`DSCR N/A for ${yearList(na)}`);
  return parts.join(' · ');
}

// ─── Reading the envelope ───────────────────────────────────────────────

/** The minimal outputs shape this module reads (an `EngineOutputsResponse`). */
interface OutputsLike {
  engines?: Partial<Record<string, { outputs?: Record<string, unknown> | null } | undefined>>;
}

export interface NoiShortfallReading {
  /** The run carries the FON-63 fields (`negative_noi_years` is an array). */
  present: boolean;
  negativeYears: number[];
  totalShortfallUsd: number | null;
  /** The sentence to show, or null when nothing is negative. */
  warning: string | null;
}

const out = (o: OutputsLike | null | undefined, engine: string): Record<string, unknown> | null =>
  (o?.engines?.[engine]?.outputs as Record<string, unknown> | null | undefined) ?? null;

/**
 * Read the debt engine's negative-NOI fields. A run that predates them reads
 * as `present: false, warning: null` — callers render exactly what they did
 * before.
 */
export function readNoiWarning(outputs: OutputsLike | null | undefined): NoiShortfallReading {
  const debt = out(outputs, 'debt');
  const rawYears = debt?.negative_noi_years;
  if (!Array.isArray(rawYears)) {
    return { present: false, negativeYears: [], totalShortfallUsd: null, warning: null };
  }
  const negativeYears = rawYears.filter(isNum);
  const total = isNum(debt?.total_shortfall_usd) ? (debt!.total_shortfall_usd as number) : null;
  if (negativeYears.length === 0) {
    return { present: true, negativeYears, totalShortfallUsd: total, warning: null };
  }

  const verbatim = debt?.noi_warning;
  if (typeof verbatim === 'string' && verbatim.trim()) {
    return { present: true, negativeYears, totalShortfallUsd: total, warning: verbatim.trim() };
  }

  // NOI per year: the expense engine's projection (what DSCR divides), else
  // the returns engine's NOI vector. Never estimated.
  const expYears = out(outputs, 'expense')?.years;
  const retNoi = out(outputs, 'returns')?.noi_by_year;
  const noiForYear = (y: number): number | undefined => {
    if (Array.isArray(expYears)) {
      const row = expYears[y - 1] as { noi?: unknown } | undefined;
      if (row && isNum(row.noi)) return row.noi;
    }
    if (Array.isArray(retNoi) && isNum(retNoi[y - 1])) return retNoi[y - 1] as number;
    return undefined;
  };

  // DSCR is N/A exactly where the schedule says so.
  const schedule = Array.isArray(debt?.schedule) ? (debt!.schedule as { year?: unknown; dscr?: unknown }[]) : [];
  const dscrNaYears = schedule
    .filter((r) => isNum(r.year) && r.dscr === null)
    .map((r) => r.year as number);

  return {
    present: true,
    negativeYears,
    totalShortfallUsd: total,
    warning: composeNoiWarning({ negativeYears, noiForYear, totalShortfallUsd: total, dscrNaYears }),
  };
}
