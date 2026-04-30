'use client';

/**
 * Map a real (worker-backed) deal + its persisted engine outputs onto
 * the in-page slider model's ``Assumptions`` shape.
 *
 * Sliders default to ``KIMPTON_ASSUMPTIONS`` when nothing better is
 * available. On a real deal we override every field we can derive from
 * either the ``deals`` row (purchase_price, keys) or a persisted engine
 * output (capital, debt, expense, fb, returns). Anything we can't cover
 * stays at the Kimpton default — better an institutional baseline than
 * a zero, and the user can still drag any slider to fix it.
 */

import { KIMPTON_ASSUMPTIONS } from '@/lib/engines';
import type { Assumptions } from '@/lib/engines/types';
import type { WorkerDeal, EngineOutputsResponse } from '@/lib/api';
import { getEngineField } from '@/lib/hooks/useEngineOutputs';

interface ExpenseYearLite {
  year: number;
  total_revenue: number;
  mgmt_fee: number;
  ffe_reserve: number;
  noi: number;
}

interface RevenueYearLite {
  year: number;
  occupancy: number;
  adr: number;
}

interface FBYearLite {
  year: number;
  fb_revenue: number;
  other_revenue: number;
}

export function assumptionsFromDeal(
  deal: WorkerDeal | null | undefined,
  outputs: EngineOutputsResponse | null | undefined,
): Assumptions {
  const next: Assumptions = { ...KIMPTON_ASSUMPTIONS };
  const out = outputs ?? null;

  if (deal?.keys && deal.keys > 0) {
    next.keys = deal.keys;
  }
  // ``purchase_price`` isn't on the WorkerDeal shape today (the worker
  // exposes it on the deals table but the API doesn't surface it on the
  // GET /deals/{id} payload); fall through to capital engine output.

  // Capital engine: purchase price + LTV.
  const capitalPurchase = getEngineField<number>(out, 'capital', 'purchase_price');
  if (capitalPurchase && capitalPurchase > 0) next.purchasePrice = capitalPurchase;
  const capitalLtv = getEngineField<number>(out, 'capital', 'ltv');
  if (capitalLtv && capitalLtv > 0 && capitalLtv <= 1) next.ltv = capitalLtv;
  const renoBudget = getEngineField<number>(out, 'capital', 'renovation_budget');
  if (renoBudget && renoBudget > 0) next.renovationBudget = renoBudget;

  // Debt engine: interest rate + amortization.
  const debtRate = getEngineField<number>(out, 'debt', 'interest_rate');
  if (debtRate && debtRate > 0 && debtRate < 0.25) next.interestRate = debtRate;
  const amort = getEngineField<number>(out, 'debt', 'amortization_years');
  if (amort && amort > 0) next.amortizationYears = amort;

  // Revenue engine: Y1 occupancy + ADR (the two hottest sliders).
  const revYears = getEngineField<RevenueYearLite[]>(out, 'revenue', 'years');
  if (revYears && revYears.length > 0) {
    const y1 = revYears[0];
    if (y1.occupancy > 0 && y1.occupancy <= 1) next.y1Occupancy = y1.occupancy;
    if (y1.adr > 0) next.y1Adr = y1.adr;
  }

  // F&B engine: Y1 F&B + other revenue dollars.
  const fbYears = getEngineField<FBYearLite[]>(out, 'fb', 'years');
  if (fbYears && fbYears.length > 0) {
    const y1 = fbYears[0];
    if (y1.fb_revenue > 0) next.y1FbRevenue = y1.fb_revenue;
    if (y1.other_revenue > 0) next.y1OtherRevenue = y1.other_revenue;
  }

  // Expense engine: derive Y1 OpEx ratio (excluding mgmt fee + FF&E
  // reserve) so the slider's NOI math reconciles with the engine.
  const expYears = getEngineField<ExpenseYearLite[]>(out, 'expense', 'years');
  if (expYears && expYears.length > 0) {
    const y1 = expYears[0];
    if (y1.total_revenue > 0) {
      // total_revenue minus NOI, minus mgmt fee, minus FF&E = pure opex.
      const opex = y1.total_revenue - y1.noi - y1.mgmt_fee - y1.ffe_reserve;
      const ratio = opex / y1.total_revenue;
      if (ratio > 0 && ratio < 1.0) next.y1OpexRatio = ratio;
      if (y1.mgmt_fee > 0) {
        const m = y1.mgmt_fee / y1.total_revenue;
        if (m > 0 && m <= 0.10) next.mgmtFeePct = m;
      }
      if (y1.ffe_reserve > 0) {
        const f = y1.ffe_reserve / y1.total_revenue;
        if (f > 0 && f <= 0.10) next.ffeReservePct = f;
      }
    }
  }

  // Returns engine: exit cap rate + hold period.
  const exitCap = getEngineField<number>(out, 'returns', 'exit_cap_rate');
  if (exitCap && exitCap > 0 && exitCap < 0.20) next.exitCapRate = exitCap;
  const hold = getEngineField<number>(out, 'returns', 'hold_years');
  if (hold && hold > 0 && hold <= 15) next.holdYears = hold;

  return next;
}
