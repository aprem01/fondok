// Returns engine — IRR via Newton's method, equity multiple, cash-on-cash.

/**
 * Newton's method IRR solver. Returns NaN if non-convergent.
 * @param cashflows array starting with t=0 (typically negative initial outflow).
 */
export function irr(cashflows: number[], guess = 0.1): number {
  if (cashflows.length < 2) return NaN;
  // Guard: need at least one positive and one negative cash flow.
  let hasPos = false, hasNeg = false;
  for (const cf of cashflows) {
    if (cf > 0) hasPos = true;
    if (cf < 0) hasNeg = true;
  }
  if (!hasPos || !hasNeg) return NaN;

  let rate = guess;
  for (let i = 0; i < 200; i++) {
    let npv = 0;
    let dnpv = 0;
    for (let t = 0; t < cashflows.length; t++) {
      const denom = Math.pow(1 + rate, t);
      npv += cashflows[t] / denom;
      if (t > 0) dnpv -= (t * cashflows[t]) / Math.pow(1 + rate, t + 1);
    }
    if (dnpv === 0) {
      // Bump and retry
      rate += 0.01;
      continue;
    }
    const next = rate - npv / dnpv;
    if (!isFinite(next)) {
      // Try a different seed
      if (i < 20) { rate = -0.5 + (i * 0.05); continue; }
      return NaN;
    }
    if (Math.abs(next - rate) < 1e-7) return next;
    // Damp wild swings
    if (next < -0.99) rate = -0.5;
    else rate = next;
  }
  return rate;
}

/** Equity multiple = sum of distributions / equity invested. */
export function equityMultiple(cashflows: number[]): number {
  if (cashflows.length === 0) return 0;
  const equity = -cashflows[0];
  if (equity <= 0) return 0;
  let dist = 0;
  for (let t = 1; t < cashflows.length; t++) dist += cashflows[t];
  return dist / equity;
}

/** Year-1 cash-on-cash. Caller passes the equity outflow as a positive number. */
export function cashOnCash(year1Cf: number, equity: number): number {
  if (equity <= 0) return 0;
  return year1Cf / equity;
}

/** Average cash-on-cash across the hold period (excludes terminal sale gain). */
export function avgCashOnCash(operatingCfs: number[], equity: number): number {
  if (equity <= 0 || operatingCfs.length === 0) return 0;
  const sum = operatingCfs.reduce((s, x) => s + x, 0);
  return sum / operatingCfs.length / equity;
}
