// Expense engine — computes opex, mgmt fee, FF&E reserve and NOI.
// opexRatio is the *pure* operating expense ratio (excludes mgmt fee + FF&E reserve).
// Total deductions from revenue = opexRatio + mgmtFeePct + ffeReservePct.
// Pure opex grows at expenseGrowth; mgmt fee and FF&E remain percent-of-revenue.
import { Assumptions } from './types';
import { RevenueLine } from './revenue';

export interface ExpenseLine {
  year: number;
  opex: number;
  mgmtFee: number;
  ffeReserve: number;
  totalExpenses: number;
  noi: number;
}

export function projectExpenses(a: Assumptions, revenue: RevenueLine[]): ExpenseLine[] {
  const lines: ExpenseLine[] = [];
  const y1Revenue = revenue[0]?.totalRevenue ?? 0;
  const y1Opex = y1Revenue * a.y1OpexRatio;

  for (const r of revenue) {
    const expGrowthFactor = Math.pow(1 + a.expenseGrowth, r.year - 1);
    const opex = y1Opex * expGrowthFactor;
    const mgmtFee = r.totalRevenue * a.mgmtFeePct;
    const ffe = r.totalRevenue * a.ffeReservePct;
    const noi = r.totalRevenue - opex - mgmtFee - ffe;
    lines.push({
      year: r.year,
      opex,
      mgmtFee,
      ffeReserve: ffe,
      totalExpenses: opex + mgmtFee + ffe,
      noi,
    });
  }
  return lines;
}
