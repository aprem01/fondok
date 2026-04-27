// Debt engine — sizes loan, computes annual debt service, and tracks loan balance.
// Supports interest-only period followed by amortization.
import { Assumptions } from './types';

export interface DebtSchedule {
  loanAmount: number;
  annualDebtService: number;        // representative annual debt service (post-IO if applicable)
  ioAnnualDebtService: number;      // debt service during IO period
  amortAnnualDebtService: number;   // debt service after IO ends
  schedule: { year: number; debtService: number; principal: number; interest: number; endingBalance: number }[];
  loanBalanceAtExit: number;
}

export function buildDebtSchedule(a: Assumptions): DebtSchedule {
  const loan = a.purchasePrice * a.ltv;
  const r = a.interestRate;

  // IO debt service is interest only on full principal.
  const ioDs = loan * r;

  // Amortizing payment based on monthly compounding.
  let amortDs = 0;
  if (a.amortizationYears > 0 && r > 0) {
    const monthlyRate = r / 12;
    const totalMonths = a.amortizationYears * 12;
    const monthlyPmt = (loan * monthlyRate) / (1 - Math.pow(1 + monthlyRate, -totalMonths));
    amortDs = monthlyPmt * 12;
  } else if (r === 0 && a.amortizationYears > 0) {
    amortDs = loan / a.amortizationYears;
  }

  const schedule: DebtSchedule['schedule'] = [];
  let balance = loan;
  for (let y = 1; y <= a.holdYears; y++) {
    const isIo = y <= a.ioPeriodYears;
    const ds = isIo ? ioDs : amortDs;
    let interest = 0;
    let principal = 0;
    if (isIo) {
      interest = balance * r;
      principal = 0;
    } else {
      // Approximate annual amortization from monthly schedule
      const monthlyRate = r / 12;
      let yrInterest = 0;
      let yrPrincipal = 0;
      let monthlyBal = balance;
      const monthlyPmt = ds / 12;
      for (let m = 0; m < 12; m++) {
        const monthInt = monthlyBal * monthlyRate;
        const monthPrin = monthlyPmt - monthInt;
        yrInterest += monthInt;
        yrPrincipal += monthPrin;
        monthlyBal -= monthPrin;
      }
      interest = yrInterest;
      principal = yrPrincipal;
    }
    balance = Math.max(0, balance - principal);
    schedule.push({ year: y, debtService: ds, principal, interest, endingBalance: balance });
  }

  // Pick representative annual debt service: if any year is amortizing within hold, use that;
  // otherwise IO. This is what the UI displays as "annual debt service".
  const anyAmortInHold = a.holdYears > a.ioPeriodYears;
  const annualDs = anyAmortInHold ? amortDs : ioDs;

  return {
    loanAmount: loan,
    annualDebtService: annualDs,
    ioAnnualDebtService: ioDs,
    amortAnnualDebtService: amortDs,
    schedule,
    loanBalanceAtExit: balance,
  };
}
