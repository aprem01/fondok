/**
 * FON-82 (Sam, 2026-11-11) — "Stabilized NOI: switch to AFTER FF&E reserve,
 * to match the ticket, the exit NOI and how lenders read it. Same for yield
 * on cost." The shared selectors every surface reads (Overview, Returns' Yield
 * on Cost, IC memo, Scenario Analysis) resolve the stabilized year's Cash NOI,
 * and the labels say so.
 *
 * FON-44 — the bare "NOI" headline (before the reserve) carries its definition:
 * after property tax and insurance, before the FF&E reserve.
 */
import { describe, it, expect } from 'vitest';
import {
  CASH_NOI_LABEL,
  NOI_BEFORE_RESERVE_DEFINITION,
  NOI_BEFORE_RESERVE_LABEL,
  STABILIZED_NOI_LABEL,
  STABILIZED_NOI_MARGIN_LABEL,
  YIELD_ON_COST_FORMULA,
  YIELD_ON_COST_LABEL,
  stabilizedCashNoiMargin,
  stabilizedNoiAfterReserve,
  stabilizedNoiBeforeReserve,
  type StabilizedYearBlock,
} from '@/lib/engines/noi';

const BLOCK: StabilizedYearBlock = {
  stabilized_year_index: 2,
  stabilized_year: 3,
  source: 'default_year_3',
  stabilized_revenue: 20_000_000,
  stabilized_noi_before_reserve: 5_200_000,
  stabilized_cash_noi: 4_400_000,
  stabilized_noi_margin: 0.26,
  stabilized_cash_noi_margin: 0.22,
};
const ENGINES = { expense: { outputs: { stabilization: BLOCK } } };

describe('FON-82 — the headline stabilized figures are after the FF&E reserve', () => {
  it('stabilizedNoiAfterReserve reads stabilized_cash_noi; the before-reserve selector still works', () => {
    expect(stabilizedNoiAfterReserve(ENGINES)).toBe(4_400_000);
    expect(stabilizedNoiBeforeReserve(ENGINES)).toBe(5_200_000);
    // No block → null, never a borrowed year or the other basis.
    expect(stabilizedNoiAfterReserve({ expense: { outputs: {} } })).toBeNull();
    expect(stabilizedNoiAfterReserve(null)).toBeNull();
    // A block that carries only the before-reserve figure does NOT pass it off
    // as the after-reserve one.
    expect(
      stabilizedNoiAfterReserve({
        expense: { outputs: { stabilization: { ...BLOCK, stabilized_cash_noi: null } } },
      }),
    ).toBeNull();
  });

  it('the headline margin is the worker field, else Cash NOI ÷ revenue off the SAME block', () => {
    expect(stabilizedCashNoiMargin(BLOCK)).toBe(0.22);
    // A run made before the field existed: formed from the same block's
    // Cash NOI and revenue (same year index) — not the before-reserve margin.
    const older = { ...BLOCK, stabilized_cash_noi_margin: undefined };
    expect(stabilizedCashNoiMargin(older)).toBeCloseTo(4_400_000 / 20_000_000, 12);
    expect(stabilizedCashNoiMargin(older)).not.toBe(0.26);
    expect(stabilizedCashNoiMargin({ ...older, stabilized_revenue: 0 })).toBeNull();
    expect(stabilizedCashNoiMargin({ ...older, stabilized_cash_noi: null })).toBeNull();
    expect(stabilizedCashNoiMargin(null)).toBeNull();
  });

  it('every label names the basis', () => {
    expect(STABILIZED_NOI_LABEL).toBe('Stabilized NOI (after FF&E reserve)');
    expect(STABILIZED_NOI_MARGIN_LABEL).toBe('Stabilized NOI Margin (after FF&E reserve)');
    expect(YIELD_ON_COST_LABEL).toBe('Yield on Cost (on NOI after FF&E reserve)');
    expect(YIELD_ON_COST_FORMULA).toContain('after FF&E reserve');
    // The FON-67 #2 convention for the after-reserve line is unchanged.
    expect(CASH_NOI_LABEL).toBe('Cash NOI (after FF&E reserve)');
  });
});

describe('FON-44 — the bare "NOI" headline states its definition', () => {
  it('NOI (before FF&E reserve) is after property tax and insurance', () => {
    expect(NOI_BEFORE_RESERVE_LABEL).toBe('NOI (before FF&E reserve)');
    expect(NOI_BEFORE_RESERVE_DEFINITION).toBe(
      'After property tax and insurance, before the FF&E reserve',
    );
  });
});
