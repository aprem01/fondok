/**
 * E-003 (FON-59) — the deal header said "0 keys" while Overview said 132.
 *
 * The wizard creates the deal with keys = null and the worker fills it from
 * the OM a minute later. The old header built `keys: deal.keys ?? 0` and
 * rendered the 0 — a fabricated room count. The header now renders through
 * `formatKeys`, which has exactly one rule: a dash for anything unsourced.
 */
import { describe, it, expect } from 'vitest';
import { formatKeys } from '@/lib/formatKeys';

describe('formatKeys — the header never fabricates a room count (E-003 / FON-59)', () => {
  it('renders a dash while the wizard-created deal has no keys yet', () => {
    expect(formatKeys(null)).toBe('—');
    expect(formatKeys(undefined)).toBe('—');
  });

  it('treats 0 as "not set", not as a hotel with no rooms', () => {
    expect(formatKeys(0)).toBe('—');
  });

  it('renders the worker-extracted count once it lands', () => {
    expect(formatKeys(132)).toBe('132');
    expect(formatKeys(1)).toBe('1');
  });

  it('refuses a non-finite number rather than printing NaN', () => {
    expect(formatKeys(Number.NaN)).toBe('—');
    expect(formatKeys(Number.POSITIVE_INFINITY)).toBe('—');
  });
});
