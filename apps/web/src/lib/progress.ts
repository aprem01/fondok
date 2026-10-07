/**
 * progress — pure helpers behind the progress-visibility fixes
 * (FON-41 / FON-63 external-tester findings E-001, R-039, E-024, R-032).
 *
 * Every number these helpers produce is MEASURED: elapsed time is the
 * difference between two wall-clock samples, "last run took" is the sum of
 * the worker's own ``runtime_ms`` on a completed run. Nothing here estimates
 * or predicts — there is deliberately no ETA.
 *
 * Kept free of React so the derivations can be unit-tested without a DOM.
 */

// ─── Time formatting ────────────────────────────────────────────────────

/** Whole seconds → ``m:ss`` (``h:mm:ss`` once an hour has passed).
 *  ``0 → "0:00"``, ``61 → "1:01"``, ``3725 → "1:02:05"``. Negative or
 *  non-finite input clamps to ``0:00``. */
export function formatElapsed(seconds: number): string {
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, '0');
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${ss}`;
  return `${m}:${ss}`;
}

/** Milliseconds → a compact spoken duration for "last run took":
 *  ``42000 → "42s"``, ``118000 → "1m 58s"``, ``3_725_000 → "1h 2m 5s"``. */
export function formatDuration(ms: number): string {
  const total = Number.isFinite(ms) ? Math.max(0, Math.round(ms / 1000)) : 0;
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

/** Seconds elapsed between an ISO timestamp and ``now`` (ms epoch). ``null``
 *  when the timestamp is missing or unparseable. */
export function elapsedSince(iso: string | null | undefined, now: number): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (!Number.isFinite(t)) return null;
  return Math.max(0, (now - t) / 1000);
}

// ─── Document pipeline stages (E-001) ────────────────────────────────────

/** Worker document statuses that mean "still moving through the
 *  pipeline", in pipeline order. Anything else is terminal. */
export const DOC_STAGE_LABEL: Record<string, string> = {
  UPLOADED: 'Uploaded',
  PARSING: 'Parsing',
  CLASSIFYING: 'Classifying',
  EXTRACTING: 'Extracting',
  // Legacy catch-all some worker builds still emit between stages.
  PROCESSING: 'Processing',
};

export function isProcessingStatus(status: string | null | undefined): boolean {
  return Object.prototype.hasOwnProperty.call(
    DOC_STAGE_LABEL,
    (status ?? '').toUpperCase(),
  );
}

/** Stage word for an in-flight document (``"Parsing"``), or ``null`` when the
 *  document is not processing. */
export function docStageLabel(status: string | null | undefined): string | null {
  const key = (status ?? '').toUpperCase();
  return isProcessingStatus(key) ? DOC_STAGE_LABEL[key] : null;
}

export interface DocProcessingSummary {
  /** Documents currently in a processing status. */
  processing: number;
  /** All documents considered. */
  total: number;
  /** Longest elapsed (seconds since ``uploaded_at``) among processing
   *  documents; ``null`` when none carries a usable timestamp. */
  longestSeconds: number | null;
}

export function summarizeDocProcessing(
  docs: ReadonlyArray<{ status?: string | null; uploaded_at?: string | null }>,
  now: number,
): DocProcessingSummary {
  let processing = 0;
  let longest: number | null = null;
  for (const d of docs) {
    if (!isProcessingStatus(d.status)) continue;
    processing += 1;
    const e = elapsedSince(d.uploaded_at, now);
    if (e != null && (longest == null || e > longest)) longest = e;
  }
  return { processing, total: docs.length, longestSeconds: longest };
}

/** ``"Processing 3 of 17 · longest 4:12"`` — ``null`` when nothing is
 *  processing (the caller renders nothing). The "longest" part is omitted
 *  when no processing document carries an ``uploaded_at``. */
export function formatProcessingSummary(s: DocProcessingSummary): string | null {
  if (s.processing <= 0) return null;
  const head = `Processing ${s.processing} of ${s.total}`;
  return s.longestSeconds == null
    ? head
    : `${head} · longest ${formatElapsed(s.longestSeconds)}`;
}

// ─── Engine run stage (E-024) ────────────────────────────────────────────

/** Display labels for the worker's engine names. Shared by the floating
 *  run strip and the Data Room inline strip so both say the same thing. */
export const ENGINE_LABEL: Record<string, string> = {
  revenue: 'Revenue',
  fb: 'F&B',
  expense: 'Expense',
  capital: 'Capital',
  debt: 'Debt',
  returns: 'Returns',
  sensitivity: 'Sensitivity',
  partnership: 'Partnership',
  cash_flow: 'Cash Flow',
};

export function engineLabel(name: string): string {
  return ENGINE_LABEL[name] ?? name;
}

export interface EngineRunRowLike {
  engine: string;
  status: string;
  runtime_ms?: number | null;
  run_id?: string | null;
  completed_at?: string | null;
}

export interface EngineStageInput {
  /** Engines the run kicked off, in dependency order. */
  expected: ReadonlyArray<string>;
  /** Latest polled rows for the in-flight run. */
  rows: ReadonlyArray<EngineRunRowLike>;
  /** Wall-clock (ms epoch) when the run was kicked off; ``null`` → 0:00. */
  startedAt: number | null;
  /** Current wall-clock sample (ms epoch). */
  now: number;
}

export interface EngineStage {
  /** Engine currently ``running``, or the next ``queued`` one after the last
   *  completed engine; ``null`` once everything is terminal. */
  currentEngine: string | null;
  currentLabel: string | null;
  completed: number;
  failed: number;
  total: number;
  elapsedSeconds: number;
  allDone: boolean;
  /** ``"Running Revenue · 2 of 9 complete · 0:42"`` while in flight;
   *  ``"Finished · 9 of 9 complete · 1:58"`` once terminal. */
  copy: string;
}

export function deriveEngineStage(input: EngineStageInput): EngineStage {
  const { rows, startedAt, now } = input;
  // Fall back to the engines the worker actually reported if the kickoff
  // list was empty — never a hard-coded count.
  const order: string[] =
    input.expected.length > 0
      ? [...input.expected]
      : Array.from(new Set(rows.map((r) => r.engine)));
  for (const r of rows) if (!order.includes(r.engine)) order.push(r.engine);

  const byEngine = new Map<string, EngineRunRowLike>();
  for (const r of rows) byEngine.set(r.engine, r);

  let completed = 0;
  let failed = 0;
  for (const name of order) {
    const s = byEngine.get(name)?.status;
    if (s === 'complete') completed += 1;
    else if (s === 'failed') failed += 1;
  }
  const total = order.length;
  const allDone = total > 0 && completed + failed === total;

  // Prefer the engine the worker says is running; otherwise the first one
  // (in dependency order) that hasn't reached a terminal state — i.e. the
  // next queued engine after the last completed one.
  let currentEngine: string | null = null;
  for (const name of order) {
    if (byEngine.get(name)?.status === 'running') {
      currentEngine = name;
      break;
    }
  }
  if (currentEngine == null && !allDone) {
    for (const name of order) {
      const s = byEngine.get(name)?.status;
      if (s !== 'complete' && s !== 'failed') {
        currentEngine = name;
        break;
      }
    }
  }

  const elapsedSeconds = startedAt == null ? 0 : Math.max(0, (now - startedAt) / 1000);
  const currentLabel = currentEngine == null ? null : engineLabel(currentEngine);
  const progress = `${completed} of ${total} complete`;
  const tail = `${progress} · ${formatElapsed(elapsedSeconds)}`;
  const copy = allDone
    ? `Finished · ${tail}`
    : currentLabel
      ? `Running ${currentLabel} · ${tail}`
      : `Running · ${tail}`;

  return {
    currentEngine,
    currentLabel,
    completed,
    failed,
    total,
    elapsedSeconds,
    allDone,
    copy,
  };
}

/**
 * Wall time the last completed run took, summed from the worker's measured
 * per-engine ``runtime_ms`` — or ``null`` when there is nothing measured.
 *
 * Rows are grouped by ``run_id`` and the run with the latest
 * ``completed_at`` wins (rows with no ``run_id`` form one group). Only
 * ``complete`` rows count, and if ANY complete row in that run lacks a
 * ``runtime_ms`` the result is ``null`` — a partial sum would understate the
 * run and read like an estimate.
 */
export function lastRunTookMs(
  rows: ReadonlyArray<EngineRunRowLike> | null | undefined,
): number | null {
  if (!rows || rows.length === 0) return null;
  const groups = new Map<string, EngineRunRowLike[]>();
  for (const r of rows) {
    if (r.status !== 'complete') continue;
    const key = r.run_id ?? '';
    const g = groups.get(key);
    if (g) g.push(r);
    else groups.set(key, [r]);
  }
  if (groups.size === 0) return null;

  let bestKey: string | null = null;
  let bestAt = -Infinity;
  for (const [key, g] of groups) {
    const at = Math.max(
      ...g.map((r) => {
        const t = r.completed_at ? new Date(r.completed_at).getTime() : NaN;
        return Number.isFinite(t) ? t : -Infinity;
      }),
    );
    if (bestKey == null || at > bestAt) {
      bestKey = key;
      bestAt = at;
    }
  }
  const run = groups.get(bestKey as string) ?? [];
  let sum = 0;
  for (const r of run) {
    if (r.runtime_ms == null || !Number.isFinite(r.runtime_ms)) return null;
    sum += r.runtime_ms;
  }
  return sum;
}

/** ``"last run took 1m 58s"`` or ``null`` when nothing was measured. */
export function formatLastRunTook(ms: number | null): string | null {
  return ms == null ? null : `last run took ${formatDuration(ms)}`;
}

// ─── Document year (R-032) ───────────────────────────────────────────────

export interface DocYearInput {
  /** The analyst's tag from the wizard / reclassify. */
  fiscalYear?: number | null;
  /** The year the Extractor read off the statement's period ending. */
  extractedPeriodYear?: number | null;
  /** Worker flag: analyst tag and detected year disagree. ``false`` means
   *  the analyst already resolved it ("Use Fondok's" / "Keep mine");
   *  ``undefined`` means the worker didn't say. */
  yearMismatch?: boolean;
}

export interface DocYearView {
  /** The year to show as THE year for the row — detected first. */
  year: number | null;
  /** ``"you said 2025"`` when the analyst typed a different year and the
   *  disagreement is unresolved; otherwise ``null``. */
  note: string | null;
  /** ``"FY 2024"`` / ``"FY 2024 (you said 2025)"`` / ``null`` when no year
   *  came from either the document or the analyst. */
  label: string | null;
}

/**
 * Which year to display for a document, and whether to mention the
 * analyst's differing tag. Rules:
 *   - detected year wins when present;
 *   - the analyst's year is shown alone only when nothing was detected;
 *   - when both exist and differ, and the worker hasn't recorded the
 *     disagreement as resolved (``yearMismatch !== false``), both are shown;
 *   - when the analyst resolved it by keeping theirs (``yearMismatch ===
 *     false`` with differing values) their year is the row's year;
 *   - no year is ever invented.
 */
export function describeDocYear(d: DocYearInput): DocYearView {
  const user = d.fiscalYear ?? null;
  const detected = d.extractedPeriodYear ?? null;
  if (detected == null && user == null) return { year: null, note: null, label: null };
  if (detected == null) return { year: user, note: null, label: `FY ${user}` };
  if (user == null || user === detected) {
    return { year: detected, note: null, label: `FY ${detected}` };
  }
  if (d.yearMismatch === false) {
    // Resolved in the analyst's favour — their tag is the row's year.
    return { year: user, note: null, label: `FY ${user}` };
  }
  const note = `you said ${user}`;
  return { year: detected, note, label: `FY ${detected} (${note})` };
}
