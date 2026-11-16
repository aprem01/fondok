/**
 * FON-80 (QA 2026-11-16, gap 4) — the "Updated" a list row / deal header
 * prints is the later of the deal row's `updated_at` and the latest engine
 * run. A re-run leaves `deals.updated_at` alone (lineage reads it as "the
 * deal's inputs changed"), so the screen combines the two.
 */
import { describe, it, expect } from 'vitest';
import { lastActivityAt, readinessText } from '@/lib/project';

describe('lastActivityAt', () => {
  it('takes the newer engine run over a stale row timestamp (b67bfdee: row 11/4, run 11/16)', () => {
    expect(lastActivityAt('2026-11-04T18:32:51Z', '2026-11-16T19:59:27Z')).toBe('2026-11-16T19:59:27Z');
  });

  it('keeps the row timestamp when the assumptions changed after the last run', () => {
    expect(lastActivityAt('2026-11-16T10:00:00Z', '2026-11-10T09:00:00Z')).toBe('2026-11-16T10:00:00Z');
  });

  it('uses whichever side is known, and null when neither is', () => {
    expect(lastActivityAt('2026-11-04T00:00:00Z', null)).toBe('2026-11-04T00:00:00Z');
    expect(lastActivityAt(null, '2026-11-16T00:00:00Z')).toBe('2026-11-16T00:00:00Z');
    expect(lastActivityAt(undefined, undefined)).toBeNull();
    expect(lastActivityAt('', null)).toBeNull();
  });

  it('treats an unparsable timestamp as unknown rather than inventing an order', () => {
    expect(lastActivityAt('not a date', '2026-11-16T00:00:00Z')).toBe('2026-11-16T00:00:00Z');
    expect(lastActivityAt('not a date', null)).toBeNull();
  });
});

describe('readinessText — one wording for the header pill and the list cell', () => {
  it('prints the step, then the extraction confidence once scored, and "—" when nothing is known', () => {
    expect(readinessText(null)).toBe('—');
    expect(readinessText({ step: 'uploaded', index: 1, label: 'Documents uploaded', extractionConfidence: null }))
      .toBe('Documents uploaded');
    expect(readinessText({ step: 'model_run', index: 3, label: 'Model run', extractionConfidence: 82 }))
      .toBe('Model run · extraction 82%');
  });
});
