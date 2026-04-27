// Shared types for the Fondok hotel underwriting engines.
// These run client-side for instant slider feedback and mirror the Python engines.

export interface HurdleTier {
  hurdle: number;   // IRR threshold (e.g. 0.12)
  gpSplit: number;  // GP share of cash flow above hurdle (e.g. 0.20)
  lpSplit: number;  // LP share of cash flow above hurdle (e.g. 0.80)
}

export interface Assumptions {
  // Property
  keys: number;
  // Acquisition
  purchasePrice: number;
  closingCostsPct: number;
  workingCapital: number;
  renovationBudget: number;
  // Operating (Y1 baseline post-PIP)
  y1Adr: number;
  y1Occupancy: number;
  y1FbRevenue: number;
  y1OtherRevenue: number;
  y1OpexRatio: number;       // total opex including mgmt + ffe as fraction of total revenue
  mgmtFeePct: number;
  ffeReservePct: number;
  // Growth
  revparGrowth: number;      // also applied to FB and other revenue
  expenseGrowth: number;
  // Financing
  ltv: number;
  interestRate: number;
  loanTermYears: number;
  amortizationYears: number;
  ioPeriodYears: number;
  // Hold + exit
  holdYears: number;
  exitCapRate: number;
  sellingCostsPct: number;
  // Partnership
  gpPct: number;
  lpPct: number;
  preferredReturn: number;
  hurdleTiers: HurdleTier[];
}

export interface YearProjection {
  year: number;
  roomsRevenue: number;
  fbRevenue: number;
  otherRevenue: number;
  totalRevenue: number;
  opex: number;          // operating expenses (excludes mgmt + ffe)
  mgmtFee: number;
  ffeReserve: number;
  noi: number;
  debtService: number;
  cashFlow: number;      // NOI - debt service (operating cash flow)
}

export interface ScenarioReturn {
  irr: number;
  multiple: number;
  coc: number;
  unleveredIrr?: number;
  exitValue?: number;
}

export interface PartnershipReturn {
  lpEquity: number;
  gpEquity: number;
  totalEquity: number;
  lpIrr: number;
  gpIrr: number;
  lpMultiple: number;
  gpMultiple: number;
}

export interface SourcesAndUses {
  uses: { label: string; amount: number; total?: boolean }[];
  sources: { label: string; amount: number; pct: number; total?: boolean }[];
  totalCapital: number;
  loanAmount: number;
  equity: number;
  closingCosts: number;
  loanCosts: number;
}

export interface EngineOutputs {
  years: YearProjection[];
  exitValue: number;
  netSaleProceeds: number;
  loanAmount: number;
  loanBalanceAtExit: number;
  equity: number;
  totalCapital: number;
  leveredIrr: number;
  unleveredIrr: number;
  equityMultiple: number;
  cashOnCash: number;          // Y1 CoC
  avgCashOnCash: number;
  dscrY1: number;
  debtYieldY1: number;
  goingInCapRate: number;
  scenarios: { downside: ScenarioReturn; base: ScenarioReturn; upside: ScenarioReturn };
  partnership: PartnershipReturn;
  sourcesAndUses: SourcesAndUses;
  // Cash flow series including initial outflow at t=0
  leveredCashFlows: number[];
  unleveredCashFlows: number[];
}

export interface SensitivityCell {
  value: number;
  rowVal: number;
  colVal: number;
  isBase: boolean;
}

export interface SensitivityMatrix {
  rowLabel: string;
  colLabel: string;
  rows: number[];
  cols: number[];
  cells: SensitivityCell[][];
  unit: 'pct' | 'multiple';
  baseRow: number;
  baseCol: number;
}
