// Partnership engine — GP/LP waterfall through preferred return + promote tiers.
// Simple American-style waterfall implemented annually:
//   1. Return of capital pro-rata
//   2. Preferred return to LP (and pro-rata to GP if any)
//   3. Promote tiers based on overall deal IRR achieved by LP
import { Assumptions, PartnershipReturn } from './types';
import { irr } from './returns';

export function computePartnership(
  a: Assumptions,
  totalEquity: number,
  leveredCashflows: number[],
): PartnershipReturn {
  const lpEquity = totalEquity * a.lpPct;
  const gpEquity = totalEquity * a.gpPct;

  // The deal-level IRR determines which promote tier we land in.
  // Once a tier is reached, distributions above the LP-pref are split per that tier.
  const dealIrr = irr(leveredCashflows);

  // Find the highest-tier-met tier (tiers ordered ascending by hurdle).
  const sortedTiers = [...a.hurdleTiers].sort((x, y) => x.hurdle - y.hurdle);
  let currentTier = sortedTiers[0]; // default: lowest tier
  for (const t of sortedTiers) {
    if (dealIrr >= t.hurdle) currentTier = t;
  }

  // Approximate distributions:
  // Total LP distributions = (lp_pref share of pref-bucket) + (lpSplit of promote-bucket)
  // We split the cumulative cash flow above the pref hurdle per the active tier's split.
  const totalCashOut = leveredCashflows.slice(1).reduce((s, x) => s + x, 0);

  // Pref bucket: equity * (1 + pref)^holdYears - equity, distributed pro-rata
  const prefBucket = totalEquity * (Math.pow(1 + a.preferredReturn, a.holdYears) - 1);
  const prefAndROC = totalEquity + prefBucket;

  let lpDist: number;
  let gpDist: number;
  if (totalCashOut <= prefAndROC) {
    // All goes pro-rata
    lpDist = totalCashOut * a.lpPct;
    gpDist = totalCashOut * a.gpPct;
  } else {
    // Pro-rata for return of capital + pref, then promote split on the residual
    const lpProrata = prefAndROC * a.lpPct;
    const gpProrata = prefAndROC * a.gpPct;
    const promoteBucket = totalCashOut - prefAndROC;
    const lpFromPromote = promoteBucket * (currentTier?.lpSplit ?? a.lpPct);
    const gpFromPromote = promoteBucket * (currentTier?.gpSplit ?? a.gpPct);
    lpDist = lpProrata + lpFromPromote;
    gpDist = gpProrata + gpFromPromote;
  }

  const lpMultiple = lpEquity > 0 ? lpDist / lpEquity : 0;
  const gpMultiple = gpEquity > 0 ? gpDist / gpEquity : 0;

  // For LP/GP IRR we approximate by scaling the deal cash flow series proportionally.
  const lpDealRatio = lpEquity > 0 ? lpDist / lpEquity : 0;
  const gpDealRatio = gpEquity > 0 ? gpDist / gpEquity : 0;
  const lpCfs = scaleEndingDist(leveredCashflows, lpEquity, lpDealRatio);
  const gpCfs = scaleEndingDist(leveredCashflows, gpEquity, gpDealRatio);

  return {
    lpEquity,
    gpEquity,
    totalEquity,
    lpIrr: irr(lpCfs),
    gpIrr: irr(gpCfs),
    lpMultiple,
    gpMultiple,
  };
}

function scaleEndingDist(deal: number[], equity: number, multiple: number): number[] {
  if (deal.length < 2 || equity <= 0) return [-equity];
  // Distribute cash flows proportionally based on the deal's distribution shape.
  const dealEquity = -deal[0];
  if (dealEquity <= 0) return [-equity];
  const scale = equity / dealEquity;
  const cfs: number[] = [-equity];
  // Operating cash flows scale with equity; terminal year gets the residual
  // to make total = equity * multiple.
  const targetTotal = equity * multiple;
  let opTotal = 0;
  for (let t = 1; t < deal.length - 1; t++) {
    const v = deal[t] * scale;
    cfs.push(v);
    opTotal += v;
  }
  cfs.push(targetTotal - opTotal);
  return cfs;
}
