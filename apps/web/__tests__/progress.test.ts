/**
 * progress helpers — FON-41 / FON-63 progress-visibility findings.
 *
 * Locks the measured-only contract behind the three progress surfaces:
 *   - E-001: per-document stage word + elapsed since ``uploaded_at``;
 *   - E-024: "Running <Engine> · k of n complete · m:ss" derived from the
 *     polled per-engine statuses, and "last run took" ONLY from the worker's
 *     ``runtime_ms`` (never an estimate);
 *   - R-032: the displayed year comes from the document, or the analyst,
 *     never a default.
 */
import { describe, it, expect } from 'vitest';
import {
  formatElapsed,
  formatDuration,
  elapsedSince,
  docStageLabel,
  isProcessingStatus,
  summarizeDocProcessing,
  formatProcessingSummary,
  deriveEngineStage,
  lastRunTookMs,
  formatLastRunTook,
  describeDocYear,
  engineLabel,
} from '@/lib/progress';

describe('formatElapsed', () => {
  it('formats m:ss and promotes to h:mm:ss past an hour', () => {
    expect(formatElapsed(0)).toBe('0:00');
    expect(formatElapsed(61)).toBe('1:01');
    expect(formatElapsed(3725)).toBe('1:02:05');
  });

  it('floors fractional seconds and clamps negatives / NaN to 0:00', () => {
    expect(formatElapsed(59.9)).toBe('0:59');
    expect(formatElapsed(-5)).toBe('0:00');
    expect(formatElapsed(Number.NaN)).toBe('0:00');
  });
});

describe('formatDuration', () => {
  it('speaks seconds, minutes and hours', () => {
    expect(formatDuration(42_000)).toBe('42s');
    expect(formatDuration(118_000)).toBe('1m 58s');
    expect(formatDuration(3_725_000)).toBe('1h 2m 5s');
  });
});

describe('elapsedSince', () => {
  it('measures seconds between an ISO timestamp and now', () => {
    const start = Date.parse('2026-10-07T10:00:00Z');
    expect(elapsedSince('2026-10-07T10:00:00Z', start + 252_000)).toBe(252);
  });
  it('returns null for a missing or unparseable timestamp', () => {
    expect(elapsedSince(null, 0)).toBeNull();
    expect(elapsedSince('not-a-date', 0)).toBeNull();
  });
});

describe('document stage labels', () => {
  it('maps the pipeline statuses to stage words in order', () => {
    expect(docStageLabel('UPLOADED')).toBe('Uploaded');
    expect(docStageLabel('PARSING')).toBe('Parsing');
    expect(docStageLabel('CLASSIFYING')).toBe('Classifying');
    expect(docStageLabel('EXTRACTING')).toBe('Extracting');
    expect(docStageLabel('extracting')).toBe('Extracting');
  });

  it('is null for terminal statuses', () => {
    expect(docStageLabel('EXTRACTED')).toBeNull();
    expect(docStageLabel('FAILED')).toBeNull();
    expect(docStageLabel('PARSE_FAILED')).toBeNull();
    expect(docStageLabel(undefined)).toBeNull();
    expect(isProcessingStatus('EXTRACTED')).toBe(false);
    expect(isProcessingStatus('PARSING')).toBe(true);
  });
});

describe('processing summary', () => {
  const t0 = Date.parse('2026-10-07T10:00:00Z');
  const iso = (secondsAgo: number) => new Date(t0 - secondsAgo * 1000).toISOString();

  it('counts processing docs out of all docs and reports the longest elapsed', () => {
    const docs = [
      { status: 'EXTRACTED', uploaded_at: iso(900) },
      { status: 'PARSING', uploaded_at: iso(252) },
      { status: 'EXTRACTING', uploaded_at: iso(61) },
      { status: 'UPLOADED', uploaded_at: iso(5) },
      { status: 'FAILED', uploaded_at: iso(400) },
    ];
    const s = summarizeDocProcessing(docs, t0);
    expect(s).toEqual({ processing: 3, total: 5, longestSeconds: 252 });
    expect(formatProcessingSummary(s)).toBe('Processing 3 of 5 · longest 4:12');
  });

  it('renders nothing when nothing is processing', () => {
    const s = summarizeDocProcessing([{ status: 'EXTRACTED', uploaded_at: iso(10) }], t0);
    expect(formatProcessingSummary(s)).toBeNull();
  });

  it('omits "longest" when no processing doc has a timestamp', () => {
    const s = summarizeDocProcessing([{ status: 'PARSING', uploaded_at: null }], t0);
    expect(s.longestSeconds).toBeNull();
    expect(formatProcessingSummary(s)).toBe('Processing 1 of 1');
  });
});

describe('deriveEngineStage', () => {
  const expected = [
    'revenue', 'fb', 'expense', 'capital', 'debt',
    'returns', 'sensitivity', 'partnership', 'cash_flow',
  ];
  const startedAt = 1_000_000;

  it('names the running engine, counts completed, and measures elapsed', () => {
    const rows = [
      { engine: 'revenue', status: 'complete', runtime_ms: 800 },
      { engine: 'fb', status: 'complete', runtime_ms: 400 },
      { engine: 'expense', status: 'running' },
      { engine: 'capital', status: 'queued' },
    ];
    const st = deriveEngineStage({ expected, rows, startedAt, now: startedAt + 42_000 });
    expect(st.currentEngine).toBe('expense');
    expect(st.currentLabel).toBe('Expense');
    expect(st.completed).toBe(2);
    expect(st.total).toBe(9);
    expect(st.elapsedSeconds).toBe(42);
    expect(st.allDone).toBe(false);
    expect(st.copy).toBe('Running Expense · 2 of 9 complete · 0:42');
  });

  it('falls back to the next queued engine after the last complete one', () => {
    const rows = [
      { engine: 'revenue', status: 'complete' },
      { engine: 'fb', status: 'complete' },
      { engine: 'expense', status: 'queued' },
    ];
    const st = deriveEngineStage({ expected, rows, startedAt, now: startedAt + 5_000 });
    expect(st.currentEngine).toBe('expense');
    expect(st.copy).toBe('Running Expense · 2 of 9 complete · 0:05');
  });

  it('starts at the first engine with no rows yet and 0:00 elapsed', () => {
    const st = deriveEngineStage({ expected, rows: [], startedAt, now: startedAt });
    expect(st.copy).toBe('Running Revenue · 0 of 9 complete · 0:00');
  });

  it('skips a failed engine when picking the next queued one', () => {
    const rows = [
      { engine: 'revenue', status: 'complete' },
      { engine: 'fb', status: 'failed' },
    ];
    const st = deriveEngineStage({ expected, rows, startedAt, now: startedAt + 1_000 });
    expect(st.currentEngine).toBe('expense');
    expect(st.failed).toBe(1);
    expect(st.copy).toBe('Running Expense · 1 of 9 complete · 0:01');
  });

  it('reports Finished once every engine is terminal', () => {
    const rows = expected.map((engine) => ({ engine, status: 'complete' }));
    const st = deriveEngineStage({ expected, rows, startedAt, now: startedAt + 118_000 });
    expect(st.allDone).toBe(true);
    expect(st.currentEngine).toBeNull();
    expect(st.copy).toBe('Finished · 9 of 9 complete · 1:58');
  });

  it('never hard-codes the engine count — uses the kickoff list, else the rows', () => {
    const st = deriveEngineStage({
      expected: [],
      rows: [
        { engine: 'revenue', status: 'complete' },
        { engine: 'returns', status: 'running' },
      ],
      startedAt,
      now: startedAt,
    });
    expect(st.total).toBe(2);
    expect(st.copy).toBe('Running Returns · 1 of 2 complete · 0:00');
  });

  it('shows 0:00 when the start time is unknown rather than inventing one', () => {
    const st = deriveEngineStage({ expected, rows: [], startedAt: null, now: 99_999_999 });
    expect(st.elapsedSeconds).toBe(0);
  });

  it('falls back to the raw engine name when no label is known', () => {
    expect(engineLabel('mystery')).toBe('mystery');
  });
});

describe('lastRunTookMs', () => {
  it('sums the measured runtime_ms of the last completed run', () => {
    const rows = [
      { engine: 'revenue', status: 'complete', runtime_ms: 60_000, run_id: 'r2', completed_at: '2026-10-07T10:02:00Z' },
      { engine: 'returns', status: 'complete', runtime_ms: 58_000, run_id: 'r2', completed_at: '2026-10-07T10:03:00Z' },
      // An older run — must not leak into the sum.
      { engine: 'revenue', status: 'complete', runtime_ms: 5_000, run_id: 'r1', completed_at: '2026-10-07T09:00:00Z' },
    ];
    expect(lastRunTookMs(rows)).toBe(118_000);
    expect(formatLastRunTook(lastRunTookMs(rows))).toBe('last run took 1m 58s');
  });

  it('is null when there is no runtime_ms — never an estimate', () => {
    expect(lastRunTookMs([])).toBeNull();
    expect(lastRunTookMs(null)).toBeNull();
    expect(lastRunTookMs([{ engine: 'revenue', status: 'complete', runtime_ms: null }])).toBeNull();
    // A run with one unmeasured engine is not reported as a partial sum.
    expect(
      lastRunTookMs([
        { engine: 'revenue', status: 'complete', runtime_ms: 1_000, run_id: 'r1' },
        { engine: 'returns', status: 'complete', runtime_ms: null, run_id: 'r1' },
      ]),
    ).toBeNull();
    expect(formatLastRunTook(null)).toBeNull();
  });

  it('ignores queued / running / failed rows', () => {
    expect(
      lastRunTookMs([
        { engine: 'revenue', status: 'running', runtime_ms: 1_000 },
        { engine: 'fb', status: 'failed', runtime_ms: 1_000 },
      ]),
    ).toBeNull();
  });
});

describe('describeDocYear', () => {
  it('shows the detected year as the year', () => {
    expect(describeDocYear({ extractedPeriodYear: 2024 })).toEqual({
      year: 2024, note: null, label: 'FY 2024',
    });
  });

  it('shows both when the analyst typed a different year', () => {
    expect(describeDocYear({ fiscalYear: 2025, extractedPeriodYear: 2024, yearMismatch: true })).toEqual({
      year: 2024, note: 'you said 2025', label: 'FY 2024 (you said 2025)',
    });
    // Worker didn't send the flag — the values alone disagree.
    expect(describeDocYear({ fiscalYear: 2025, extractedPeriodYear: 2024 }).label).toBe(
      'FY 2024 (you said 2025)',
    );
  });

  it('collapses to one year when both agree', () => {
    expect(describeDocYear({ fiscalYear: 2024, extractedPeriodYear: 2024, yearMismatch: false }).label).toBe('FY 2024');
  });

  it('keeps the analyst year once they resolved the mismatch in their favour', () => {
    expect(describeDocYear({ fiscalYear: 2025, extractedPeriodYear: 2024, yearMismatch: false })).toEqual({
      year: 2025, note: null, label: 'FY 2025',
    });
  });

  it('shows only the analyst year when nothing was detected, and nothing when neither exists', () => {
    expect(describeDocYear({ fiscalYear: 2023 }).label).toBe('FY 2023');
    expect(describeDocYear({})).toEqual({ year: null, note: null, label: null });
    expect(describeDocYear({ fiscalYear: null, extractedPeriodYear: null }).label).toBeNull();
  });
});
