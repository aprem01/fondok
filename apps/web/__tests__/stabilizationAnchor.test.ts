/**
 * FON-98 — the stabilization anchor as the web states it.
 *
 * Rani + Eshan: the stabilized year defaults to Year 3 of operations; for a
 * material repositioning / re-flag it counts from the reopening date, else
 * from the acquisition close. The worker publishes which anchor it used on
 * `expense.stabilization` (`anchor`, `source`, `reopening_date`,
 * `anchor_fallback_reason`); these helpers turn that into the exact copy the
 * Overview rows and the Future P&L Stabilization card print
 * (`ProjectionsSection` renders `stabilizationSignalNote` as its note).
 */
import { describe, it, expect } from 'vitest';
import {
  stabilizationAnchorLabel,
  stabilizationAnchorFallbackNote,
  stabilizationSignalNote,
  stabilizationYearLabel,
  fmtMonthYear,
  reopeningBeforeProjectionNote,
  type StabilizedYearBlock,
} from '@/lib/engines/noi';

const close = (patch: Partial<StabilizedYearBlock> = {}): StabilizedYearBlock => ({
  stabilized_year_index: 2,
  stabilized_year: 3,
  source: 'default_year_3',
  anchor: 'acquisition_close',
  default_year: 3,
  clamped: false,
  reopening_date: null,
  anchor_fallback_reason: null,
  ...patch,
});

const reopening = (patch: Partial<StabilizedYearBlock> = {}): StabilizedYearBlock => ({
  stabilized_year_index: 5,
  stabilized_year: 6,
  source: 'default_year_3_reopening',
  anchor: 'reopening',
  default_year: 3,
  clamped: false,
  reopening_date: '2027-03-15',
  anchor_fallback_reason: null,
  ...patch,
});

describe('stabilizationAnchorLabel', () => {
  it('reads "Year 3 after close" without a reopening date', () => {
    expect(stabilizationAnchorLabel(close())).toBe('Year 3 after close');
  });

  it('reads "Year 3 after reopening (Mar 2027)" when the reopening anchored the default', () => {
    expect(stabilizationAnchorLabel(reopening())).toBe('Year 3 after reopening (Mar 2027)');
    expect(fmtMonthYear('2027-03-15')).toBe('Mar 2027');
  });

  it('an analyst override still names the default it replaced', () => {
    expect(stabilizationAnchorLabel(reopening({ source: 'analyst_override', stabilized_year: 4, stabilized_year_index: 3 })))
      .toBe('Year 3 after reopening (Mar 2027)');
    expect(stabilizationAnchorLabel(close({ source: 'analyst_override' }))).toBe('Year 3 after close');
  });

  it('a reopening date the worker could not use falls back to the close anchor AND says why', () => {
    const block = close({ reopening_date: '2027-03-15', anchor_fallback_reason: 'no_close_date' });
    expect(stabilizationAnchorLabel(block)).toBe('Year 3 after close');
    expect(stabilizationAnchorFallbackNote(block)).toMatch(/reopening date \(Mar 2027\) could not be placed .* no acquisition close date/);
    // Sam #7 (2026-10-15) — close is the MVP fallback, with this exact note.
    expect(
      stabilizationAnchorFallbackNote(close({ reopening_date: '2019-06-01', anchor_fallback_reason: 'reopening_before_projection' })),
    ).toBe(
      'Exception: Year 3 after the reopening date (Jun 2019) falls before the first projected year, so stabilization is measured from the acquisition close instead.',
    );
    expect(stabilizationAnchorFallbackNote(close())).toBeNull();
    expect(stabilizationAnchorFallbackNote(null)).toBeNull();
  });
});

describe('stabilizationYearLabel / stabilizationSignalNote carry the anchor', () => {
  it('close anchor is unchanged from R-057', () => {
    expect(stabilizationYearLabel(close())).toBe('Year 3 after close (default)');
    expect(stabilizationSignalNote(close())).toBe('Default: Year 3 after close. Confirm or change it.');
  });

  it('reopening anchor names the year, the rule and the date', () => {
    expect(stabilizationYearLabel(reopening())).toBe('Year 6 — Year 3 after reopening (Mar 2027) (default)');
    expect(stabilizationSignalNote(reopening())).toBe(
      'Default: Year 3 after reopening (Mar 2027). Confirm or change it.',
    );
  });

  it('a clamped reopening default says the rule ran past the hold', () => {
    expect(stabilizationYearLabel(reopening({ clamped: true, stabilized_year: 5, stabilized_year_index: 4 })))
      .toBe('Year 5 (Year 3 after reopening (Mar 2027) is past the 5-year hold)');
    expect(stabilizationSignalNote(reopening({ clamped: true }))).toMatch(/^Default is Year 3 after reopening \(Mar 2027\), clamped to the last projected year\./);
  });

  it('the fallback reason is part of the Future P&L note (never a silent fallback)', () => {
    const note = stabilizationSignalNote(close({ reopening_date: '2027-03-15', anchor_fallback_reason: 'no_close_date' }));
    expect(note).toMatch(/^Default: Year 3 after close\. The reopening date \(Mar 2027\) could not be placed/);
  });

  it('Sam #7 — the reopening-before-projection exception is in the Future P&L note (default and override)', () => {
    const block = close({ reopening_date: '2019-06-01', anchor_fallback_reason: 'reopening_before_projection' });
    expect(stabilizationSignalNote(block)).toBe(
      'Default: Year 3 after close. Exception: Year 3 after the reopening date (Jun 2019) falls before the first projected year, so stabilization is measured from the acquisition close instead. Confirm or change it.',
    );
    expect(stabilizationSignalNote({ ...block, source: 'analyst_override' })).toBe(
      'Analyst-selected. Default would be Year 3 after close. Exception: Year 3 after the reopening date (Jun 2019) falls before the first projected year, so stabilization is measured from the acquisition close instead.',
    );
    expect(reopeningBeforeProjectionNote(3, null)).toBe(
      'Exception: Year 3 after the reopening date falls before the first projected year, so stabilization is measured from the acquisition close instead.',
    );
  });

  it('an override names the default it replaced', () => {
    expect(stabilizationSignalNote(reopening({ source: 'analyst_override' }))).toBe(
      'Analyst-selected. Default would be Year 3 after reopening (Mar 2027).',
    );
  });
});
