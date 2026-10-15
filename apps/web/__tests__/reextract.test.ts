/**
 * #18 — "Re-run extraction" rules (lib/reextract).
 *
 * Which documents can be re-run and through which worker route, why the
 * action is disabled while anything is processing, when a re-run batch is
 * finished (so the model auto-runs once), and that the confirm copy says
 * truthfully what happens to the analyst's field reviews.
 */
import { describe, it, expect } from 'vitest';
import {
  REEXTRACT_CONSEQUENCES,
  planReextractAll,
  reextractBatchDone,
  reextractEligibility,
  reextractFailureReason,
} from '@/lib/reextract';

describe('reextractEligibility', () => {
  it('re-runs an extracted document through /extract', () => {
    expect(reextractEligibility('EXTRACTED')).toEqual({ ok: true, endpoint: 'extract', reason: null });
  });
  it('re-parses a failed document through /reprocess', () => {
    expect(reextractEligibility('FAILED').endpoint).toBe('reprocess');
    expect(reextractEligibility('PARSE_FAILED').endpoint).toBe('reprocess');
  });
  it.each(['UPLOADED', 'PARSING', 'CLASSIFYING', 'EXTRACTING', 'PROCESSING'])(
    'is disabled with a reason while %s',
    (status) => {
      const e = reextractEligibility(status);
      expect(e.ok).toBe(false);
      expect(e.endpoint).toBeNull();
      expect(e.reason).toMatch(/still processing/i);
    },
  );
});

describe('planReextractAll', () => {
  it('re-runs every extracted or failed document', () => {
    const plan = planReextractAll([
      { id: 'a', status: 'EXTRACTED' },
      { id: 'b', status: 'FAILED' },
      { id: 'c', status: 'PARSE_FAILED' },
    ]);
    expect(plan).toEqual({ ids: ['a', 'b', 'c'], disabledReason: null });
  });
  it('is disabled while any document is processing, naming how many', () => {
    const plan = planReextractAll([
      { id: 'a', status: 'EXTRACTED' },
      { id: 'b', status: 'EXTRACTING' },
    ]);
    expect(plan.ids).toEqual([]);
    expect(plan.disabledReason).toBe('1 document is still processing — wait for it to finish.');
  });
  it('is disabled when nothing is eligible', () => {
    expect(planReextractAll([]).disabledReason).toMatch(/no extracted or failed/i);
  });
});

describe('reextractBatchDone', () => {
  const pending = (seen: boolean) => new Map([['a', { seenProcessing: seen }]]);
  it('waits until the document has been seen in the pipeline', () => {
    // the list still shows the OLD terminal status — not done yet
    expect(reextractBatchDone(pending(false), [{ id: 'a', status: 'EXTRACTED' }])).toBe(false);
  });
  it('waits while the document is processing', () => {
    expect(reextractBatchDone(pending(true), [{ id: 'a', status: 'EXTRACTING' }])).toBe(false);
  });
  it('is done once seen processing and terminal again (extracted or failed)', () => {
    expect(reextractBatchDone(pending(true), [{ id: 'a', status: 'EXTRACTED' }])).toBe(true);
    expect(reextractBatchDone(pending(true), [{ id: 'a', status: 'FAILED' }])).toBe(true);
  });
  it('does not wait on a document deleted mid-run', () => {
    expect(reextractBatchDone(pending(false), [])).toBe(true);
  });
  it('an empty batch is never "done" (nothing to auto-run for)', () => {
    expect(reextractBatchDone(new Map(), [])).toBe(false);
  });
});

describe('confirm copy', () => {
  const copy = REEXTRACT_CONSEQUENCES.join(' ');
  it('says the run is fresh under the current pipeline, not a cached copy', () => {
    expect(copy).toMatch(/current extraction pipeline/);
    expect(copy).toMatch(/not a cached copy/);
  });
  it('says field reviews are NOT carried over (they are on the superseded result)', () => {
    expect(copy).toMatch(/accepted, edited or rejected/);
    expect(copy).toMatch(/not carried over/);
    expect(copy).toMatch(/kept as history/);
  });
  it('says the model re-runs once when the re-run finishes', () => {
    expect(copy).toMatch(/model re-runs once/);
  });
});

describe('reextractFailureReason', () => {
  it('reads the worker 409 as "still processing" (FON-91), not a workspace error', () => {
    expect(reextractFailureReason({ name: 'WorkerError', status: 409 })).toMatch(/still processing/);
  });
  it('reads 410 as a missing stored file', () => {
    expect(reextractFailureReason({ status: 410 })).toMatch(/re-upload/);
  });
});
