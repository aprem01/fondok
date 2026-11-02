/**
 * Wave 5 (Sam #1) — the Projections "Implied Exit Value" row is valued on the
 * basis the Returns engine sells at, never on EBITDA.
 *
 * The row used to capitalise GOP − management fee (before property taxes,
 * insurance and the FF&E reserve), ~50% above the engine's own gross sale
 * price. Now an operating year capitalises that year's Cash NOI (after fixed
 * charges and the FF&E reserve) at the engine's exit cap rate, the Exit Year
 * column carries the engine's ``gross_sale_price`` itself, and a missing input
 * is a dash with the reason.
 */
import { describe, it, expect } from 'vitest';
import {
  impliedValueByYear,
  exitSaleCell,
  resolveEngineExitCap,
  IMPLIED_VALUE_LABEL,
} from '@/lib/impliedExitValue';

describe('impliedValueByYear', () => {
  it('capitalises each year’s Cash NOI at the exit cap', () => {
    // Prod copy b67bfdee Y5: Cash NOI 4,322,922 ÷ 7% = $61.76M (not the
    // EBITDA-based $93.89M).
    const cells = impliedValueByYear([4_322_922], 0.07);
    expect(cells[0].value).toBeCloseTo(61_756_028.57, 0);
    expect(cells[0].note).toMatch(/Cash NOI/);
    expect(cells[0].note).toMatch(/7\.00%/);
  });

  it('a year with no Cash NOI is a dash with the reason, never 0', () => {
    const cells = impliedValueByYear([undefined, 1_000_000], 0.08);
    expect(cells[0].value).toBeNull();
    expect(cells[0].note).toMatch(/no Cash NOI/i);
    expect(cells[1].value).toBe(12_500_000);
  });

  it('no engine exit cap rate dashes every year and says why', () => {
    const cells = impliedValueByYear([1_000_000, 1_100_000], null);
    expect(cells.every((c) => c.value == null)).toBe(true);
    expect(cells[0].note).toMatch(/exit cap rate/i);
  });

  it('a non-positive cap is not divided by', () => {
    expect(impliedValueByYear([1_000_000], 0)[0].value).toBeNull();
  });
});

describe('exitSaleCell', () => {
  it('is the Returns engine’s gross sale price, as published', () => {
    const c = exitSaleCell({ grossSalePrice: 61_160_000, terminalNoi: 4_281_200, exitCapRate: 0.07, exitNoiPeriodLabel: 'Forward 12-month NOI (Year 6)' });
    expect(c.value).toBe(61_160_000);
    expect(c.note).toMatch(/gross sale price/i);
    expect(c.note).toMatch(/Forward 12-month NOI \(Year 6\)/);
  });

  it('is a dash with the reason when the engine published no sale price', () => {
    const c = exitSaleCell({ grossSalePrice: null, terminalNoi: 4_000_000, exitCapRate: 0.07, exitNoiPeriodLabel: null });
    expect(c.value).toBeNull();
    expect(c.note).toMatch(/Returns engine/);
  });
});

describe('resolveEngineExitCap', () => {
  it('is the cap the Returns engine ran on', () => {
    expect(resolveEngineExitCap(0.075)).toBe(0.075);
  });
  it('is null until the Returns engine has run — no resolved-assumption or hard-coded stand-in', () => {
    expect(resolveEngineExitCap(null)).toBeNull();
    expect(resolveEngineExitCap(undefined)).toBeNull();
    expect(resolveEngineExitCap(Number.NaN)).toBeNull();
    expect(resolveEngineExitCap(0)).toBeNull();
  });
});

describe('label', () => {
  it('names the basis', () => {
    expect(IMPLIED_VALUE_LABEL).toMatch(/Cash NOI/);
    expect(IMPLIED_VALUE_LABEL).not.toMatch(/EBITDA/);
  });
});
