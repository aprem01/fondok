/**
 * FON-91 — a watchdog-failed document reads "Timed out after N min — Retry"
 * in the Data Room stage badge instead of a flat "Processing Failed".
 */
import { describe, expect, it } from 'vitest';

import { timedOutLabel } from '@/components/project/DocumentCoverage';

describe('timedOutLabel (FON-91)', () => {
  it('reads the minutes off the worker message', () => {
    expect(
      timedOutLabel({
        errorKind: 'timeout',
        errorMessage:
          'Timed out after 37 min in Extracting — the extraction job stopped reporting progress (limit 30 min). Nothing is wrong with the file; click Retry to re-run.',
      }),
    ).toBe('Timed out after 37 min — Retry');
  });

  it('still names the timeout when the message has no minute count', () => {
    expect(timedOutLabel({ errorKind: 'timeout', errorMessage: null })).toBe('Timed out — Retry');
    expect(timedOutLabel({ errorKind: 'TIMEOUT', errorMessage: 'custom' })).toBe('Timed out — Retry');
  });

  it('is null for every other failure and for no meta', () => {
    expect(timedOutLabel({ errorKind: 'billing', errorMessage: 'Timed out after 5 min' })).toBeNull();
    expect(timedOutLabel({ errorKind: 'cancelled', errorMessage: null })).toBeNull();
    expect(timedOutLabel({ errorKind: null, errorMessage: null })).toBeNull();
    expect(timedOutLabel(undefined)).toBeNull();
  });
});
