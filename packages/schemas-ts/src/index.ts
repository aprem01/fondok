import { z } from "zod";

// ─────────────────────────── Common ───────────────────────────

export const Severity = z.enum(["Critical", "Warn", "Info"]);
export type Severity = z.infer<typeof Severity>;

export const Risk = z.enum(["Low", "Medium", "High"]);
export type Risk = z.infer<typeof Risk>;

export const TenantScoped = z.object({
  tenant_id: z.string().min(1),
});
export type TenantScoped = z.infer<typeof TenantScoped>;

export const Money = z.object({
  amount: z.number(),
  currency: z.string().regex(/^[A-Z]{3}$/).default("USD"),
});
export type Money = z.infer<typeof Money>;

export const ModelCall = z.object({
  model: z.string(),
  input_tokens: z.number().int(),
  output_tokens: z.number().int(),
  cost_usd: z.number(),
  trace_id: z.string(),
  started_at: z.string().datetime(),
  completed_at: z.string().datetime(),
  cache_creation_input_tokens: z.number().int().nonnegative().default(0),
  cache_read_input_tokens: z.number().int().nonnegative().default(0),
  agent_name: z.string().nullable().optional(),
});
export type ModelCall = z.infer<typeof ModelCall>;

// ─────────────────────────── Confidence ───────────────────────────

export const ConfidenceReport = z.object({
  overall: z.number().min(0).max(1),
  by_field: z.record(z.string(), z.number().min(0).max(1)).default({}),
  low_confidence_fields: z.array(z.string()).default([]),
  requires_human_review: z.boolean().default(false),
});
export type ConfidenceReport = z.infer<typeof ConfidenceReport>;

// ─────────────────────────── Deal ───────────────────────────

export const DealStatus = z.enum(["Draft", "In Review", "IC Ready", "Archived"]);
export type DealStatus = z.infer<typeof DealStatus>;

export const DealStage = z.enum(["Teaser", "Under NDA", "LOI", "PSA"]);
export type DealStage = z.infer<typeof DealStage>;

export const Service = z.enum([
  "Select Service",
  "Full Service",
  "Lifestyle",
  "Luxury",
  "Limited Service",
  "Extended Stay",
]);
export type Service = z.infer<typeof Service>;

export const ReturnProfile = z.enum(["Core", "Value Add", "Opportunistic"]);
export type ReturnProfile = z.infer<typeof ReturnProfile>;

export const PositioningTier = z.enum(["Default", "Luxury", "Upscale", "Economy"]);
export type PositioningTier = z.infer<typeof PositioningTier>;

export const Deal = z.object({
  id: z.string().uuid(),
  tenant_id: z.string().min(1),
  name: z.string().min(1).max(200),
  city: z.string().min(1).max(120),
  keys: z.number().int().positive(),
  service: Service,
  status: DealStatus,
  deal_stage: DealStage,
  risk: Risk,
  ai_confidence: z.number().min(0).max(1),
  created_at: z.string().datetime(),
  updated_at: z.string().datetime(),
  assignee_id: z.string().uuid().nullable().optional(),
  return_profile: ReturnProfile.nullable().optional(),
  positioning: PositioningTier.nullable().optional(),
  brand: z.string().nullable().optional(),
  purchase_price: z.number().nonnegative().nullable().optional(),
});
export type Deal = z.infer<typeof Deal>;

export const DealSummary = z.object({
  id: z.string().uuid(),
  name: z.string(),
  city: z.string(),
  keys: z.number().int().positive(),
  service: Service,
  status: DealStatus,
  deal_stage: DealStage,
  risk: Risk,
  ai_confidence: z.number().min(0).max(1),
  revpar: z.number().nullable().optional(),
  irr: z.number().nullable().optional(),
  noi: z.number().nullable().optional(),
  docs_complete: z.number().int().nullable().optional(),
  docs_total: z.number().int().nullable().optional(),
  assignee_initials: z.string().nullable().optional(),
  updated_at: z.string().datetime(),
});
export type DealSummary = z.infer<typeof DealSummary>;

// ─────────────────────────── Document ───────────────────────────

export const DocType = z.enum([
  "OM",
  "T12",
  "STR",
  "PNL",
  "RENT_ROLL",
  "MARKET_STUDY",
  "CONTRACT",
]);
export type DocType = z.infer<typeof DocType>;

export const DocumentStatus = z.enum(["Pending", "Processing", "Extracted", "Failed"]);
export type DocumentStatus = z.infer<typeof DocumentStatus>;

export const ExtractionField = z.object({
  field_name: z.string().min(1).max(200),
  value: z.union([z.string(), z.number(), z.boolean(), z.null()]).optional(),
  unit: z.string().nullable().optional(),
  source_page: z.number().int().min(1),
  confidence: z.number().min(0).max(1),
  raw_text: z.string().max(4000).nullable().optional(),
});
export type ExtractionField = z.infer<typeof ExtractionField>;

export const Document = z.object({
  id: z.string().uuid(),
  deal_id: z.string().uuid(),
  filename: z.string().min(1).max(300),
  doc_type: DocType,
  status: DocumentStatus,
  size: z.number().int().nonnegative(),
  uploaded_at: z.string().datetime(),
  fields_extracted: z.number().int().nonnegative().default(0),
  confidence: z.number().min(0).max(1).default(0),
  populates: z.array(z.string()).default([]),
  fields: z.array(ExtractionField).default([]),
});
export type Document = z.infer<typeof Document>;

// ─────────────────────────── Financial (USALI) ───────────────────────────

export const DepartmentalExpenses = z.object({
  rooms: z.number().nonnegative().default(0),
  food_beverage: z.number().nonnegative().default(0),
  other_operated: z.number().nonnegative().default(0),
  total: z.number().nonnegative().default(0),
});
export type DepartmentalExpenses = z.infer<typeof DepartmentalExpenses>;

export const UndistributedExpenses = z.object({
  administrative_general: z.number().nonnegative().default(0),
  information_telecom: z.number().nonnegative().default(0),
  sales_marketing: z.number().nonnegative().default(0),
  property_operations: z.number().nonnegative().default(0),
  utilities: z.number().nonnegative().default(0),
  total: z.number().nonnegative().default(0),
});
export type UndistributedExpenses = z.infer<typeof UndistributedExpenses>;

export const FixedCharges = z.object({
  property_taxes: z.number().nonnegative().default(0),
  insurance: z.number().nonnegative().default(0),
  rent: z.number().nonnegative().default(0),
  other_fixed: z.number().nonnegative().default(0),
  total: z.number().nonnegative().default(0),
});
export type FixedCharges = z.infer<typeof FixedCharges>;

export const USALIFinancials = z.object({
  period_label: z.string().min(1).max(80),
  rooms_revenue: z.number().nonnegative(),
  fb_revenue: z.number().nonnegative().default(0),
  other_revenue: z.number().nonnegative().default(0),
  total_revenue: z.number().nonnegative(),
  dept_expenses: DepartmentalExpenses.default({
    rooms: 0,
    food_beverage: 0,
    other_operated: 0,
    total: 0,
  }),
  undistributed: UndistributedExpenses.default({
    administrative_general: 0,
    information_telecom: 0,
    sales_marketing: 0,
    property_operations: 0,
    utilities: 0,
    total: 0,
  }),
  mgmt_fee: z.number().nonnegative().default(0),
  ffe_reserve: z.number().nonnegative().default(0),
  fixed_charges: FixedCharges.default({
    property_taxes: 0,
    insurance: 0,
    rent: 0,
    other_fixed: 0,
    total: 0,
  }),
  gop: z.number(),
  noi: z.number(),
  opex_ratio: z.number().min(0).max(2),
  occupancy: z.number().min(0).max(1).nullable().optional(),
  adr: z.number().nonnegative().nullable().optional(),
  revpar: z.number().nonnegative().nullable().optional(),
});
export type USALIFinancials = z.infer<typeof USALIFinancials>;

export const ModelAssumptions = z.object({
  purchase_price: z.number().positive(),
  price_per_key: z.number().positive().nullable().optional(),
  ltv: z.number().min(0).max(1),
  interest_rate: z.number().min(0).max(1),
  amortization_years: z.number().int().min(0).max(40).default(30),
  loan_term_years: z.number().int().min(1).max(40).default(5),
  hold_years: z.number().int().min(1).max(20),
  exit_cap_rate: z.number().positive().max(0.3),
  entry_cap_rate: z.number().positive().max(0.3).nullable().optional(),
  revpar_growth: z.number().min(-0.5).max(0.5),
  expense_growth: z.number().min(-0.5).max(0.5).default(0.03),
  selling_costs_pct: z.number().min(0).max(0.1).default(0.02),
  closing_costs_pct: z.number().min(0).max(0.1).default(0.02),
});
export type ModelAssumptions = z.infer<typeof ModelAssumptions>;

// ─────────────────────────── Underwriting Engines ───────────────────────────

export const SourceUseLine = z.object({
  label: z.string().min(1).max(120),
  amount: z.number().nonnegative(),
  pct: z.number().min(0).max(1).nullable().optional(),
  is_total: z.boolean().default(false),
});
export type SourceUseLine = z.infer<typeof SourceUseLine>;

export const InvestmentEngineInput = z.object({
  deal_id: z.string().uuid(),
  purchase_price: z.number().positive(),
  keys: z.number().int().positive(),
  closing_costs: z.number().nonnegative().default(0),
  working_capital: z.number().nonnegative().default(0),
  renovation_budget: z.number().nonnegative().default(0),
  hard_costs_per_key: z.number().nonnegative().default(0),
  soft_costs: z.number().nonnegative().default(0),
  contingency: z.number().nonnegative().default(0),
});
export type InvestmentEngineInput = z.infer<typeof InvestmentEngineInput>;

export const InvestmentEngineOutput = z.object({
  deal_id: z.string().uuid(),
  total_capital: z.number().positive(),
  price_per_key: z.number().positive(),
  sources: z.array(SourceUseLine),
  uses: z.array(SourceUseLine),
});
export type InvestmentEngineOutput = z.infer<typeof InvestmentEngineOutput>;

export const RevenueEngineInput = z.object({
  deal_id: z.string().uuid(),
  keys: z.number().int().positive(),
  starting_occupancy: z.number().min(0).max(1),
  starting_adr: z.number().positive(),
  occupancy_growth: z.number().min(-0.5).max(0.5).default(0),
  adr_growth: z.number().min(-0.5).max(0.5).default(0.03),
  fb_revenue_per_occupied_room: z.number().nonnegative().default(0),
  other_revenue_pct_of_rooms: z.number().min(0).max(1).default(0),
  hold_years: z.number().int().min(1).max(20),
});
export type RevenueEngineInput = z.infer<typeof RevenueEngineInput>;

export const RevenueProjectionYear = z.object({
  year: z.number().int().min(1),
  occupancy: z.number().min(0).max(1),
  adr: z.number().positive(),
  revpar: z.number().nonnegative(),
  rooms_revenue: z.number().nonnegative(),
  fb_revenue: z.number().nonnegative().default(0),
  other_revenue: z.number().nonnegative().default(0),
  total_revenue: z.number().nonnegative(),
});
export type RevenueProjectionYear = z.infer<typeof RevenueProjectionYear>;

export const RevenueEngineOutput = z.object({
  deal_id: z.string().uuid(),
  years: z.array(RevenueProjectionYear),
  total_revenue_cagr: z.number(),
});
export type RevenueEngineOutput = z.infer<typeof RevenueEngineOutput>;

export const PLEngineInput = z.object({
  deal_id: z.string().uuid(),
  historical_periods: z.array(USALIFinancials).default([]),
  revenue_projection: RevenueEngineOutput.nullable().optional(),
  expense_growth: z.number().min(-0.5).max(0.5).default(0.03),
  mgmt_fee_pct: z.number().min(0).max(0.1).default(0.03),
  ffe_reserve_pct: z.number().min(0).max(0.1).default(0.04),
});
export type PLEngineInput = z.infer<typeof PLEngineInput>;

export const PLEngineOutput = z.object({
  deal_id: z.string().uuid(),
  projected_periods: z.array(USALIFinancials),
  noi_cagr: z.number(),
});
export type PLEngineOutput = z.infer<typeof PLEngineOutput>;

export const DebtEngineInput = z.object({
  deal_id: z.string().uuid(),
  loan_amount: z.number().positive(),
  ltv: z.number().min(0).max(1),
  interest_rate: z.number().min(0).max(1),
  term_years: z.number().int().min(1).max(40),
  amortization_years: z.number().int().min(0).max(40).default(30),
  interest_only_years: z.number().int().min(0).max(10).default(0),
});
export type DebtEngineInput = z.infer<typeof DebtEngineInput>;

export const DebtServiceYear = z.object({
  year: z.number().int().min(1),
  interest: z.number().nonnegative(),
  principal: z.number().nonnegative(),
  debt_service: z.number().nonnegative(),
  ending_balance: z.number().nonnegative(),
  dscr: z.number().nonnegative().nullable().optional(),
});
export type DebtServiceYear = z.infer<typeof DebtServiceYear>;

export const DebtEngineOutput = z.object({
  deal_id: z.string().uuid(),
  annual_debt_service: z.number().nonnegative(),
  schedule: z.array(DebtServiceYear),
  avg_dscr: z.number().nonnegative().nullable().optional(),
});
export type DebtEngineOutput = z.infer<typeof DebtEngineOutput>;

export const CashFlowEngineInput = z.object({
  deal_id: z.string().uuid(),
  pl: PLEngineOutput,
  debt: DebtEngineOutput,
});
export type CashFlowEngineInput = z.infer<typeof CashFlowEngineInput>;

export const CashFlowYear = z.object({
  year: z.number().int().min(1),
  noi: z.number(),
  debt_service: z.number(),
  cash_flow_after_debt: z.number(),
  capex: z.number().nonnegative().default(0),
});
export type CashFlowYear = z.infer<typeof CashFlowYear>;

export const CashFlowEngineOutput = z.object({
  deal_id: z.string().uuid(),
  years: z.array(CashFlowYear),
});
export type CashFlowEngineOutput = z.infer<typeof CashFlowEngineOutput>;

export const ReturnsEngineInput = z.object({
  deal_id: z.string().uuid(),
  assumptions: ModelAssumptions,
  cash_flow: CashFlowEngineOutput,
  terminal_noi: z.number().positive(),
});
export type ReturnsEngineInput = z.infer<typeof ReturnsEngineInput>;

export const ReturnsEngineOutput = z.object({
  deal_id: z.string().uuid(),
  levered_irr: z.number(),
  unlevered_irr: z.number(),
  equity_multiple: z.number().nonnegative(),
  year_one_coc: z.number(),
  avg_coc: z.number(),
  gross_sale_price: z.number().nonnegative(),
  selling_costs: z.number().nonnegative(),
  net_proceeds: z.number(),
  hold_years: z.number().int().min(1).max(20),
});
export type ReturnsEngineOutput = z.infer<typeof ReturnsEngineOutput>;

export const ScenarioName = z.object({
  name: z.string().min(1).max(80),
  probability: z.number().min(0).max(1).nullable().optional(),
  irr: z.number(),
  unlevered_irr: z.number().nullable().optional(),
  equity_multiple: z.number().nonnegative(),
  avg_coc: z.number(),
  exit_value: z.number().nonnegative().nullable().optional(),
  is_base: z.boolean().default(false),
});
export type ScenarioName = z.infer<typeof ScenarioName>;

// ─────────────────────────── Partnership ───────────────────────────

export const WaterfallTier = z
  .object({
    label: z.string().min(1).max(80),
    hurdle_rate: z.number().min(0).max(1),
    gp_split: z.number().min(0).max(1),
    lp_split: z.number().min(0).max(1),
  })
  .refine(
    (t) => Math.round((t.gp_split + t.lp_split) * 1_000_000) === 1_000_000,
    { message: "gp_split + lp_split must equal 1.0" },
  );
export type WaterfallTier = z.infer<typeof WaterfallTier>;

export const PartnershipInput = z.object({
  deal_id: z.string().uuid(),
  returns: ReturnsEngineOutput,
  total_equity: z.number().positive(),
  gp_equity_pct: z.number().min(0).max(1),
  lp_equity_pct: z.number().min(0).max(1),
  waterfall: z.array(WaterfallTier).min(1),
  catch_up: z.boolean().default(false),
});
export type PartnershipInput = z.infer<typeof PartnershipInput>;

export const PartnerReturn = z.object({
  partner: z.enum(["GP", "LP"]),
  contributed_equity: z.number().nonnegative(),
  distributions: z.number().nonnegative(),
  irr: z.number(),
  equity_multiple: z.number().nonnegative(),
});
export type PartnerReturn = z.infer<typeof PartnerReturn>;

export const PartnershipOutput = z.object({
  deal_id: z.string().uuid(),
  gp: PartnerReturn,
  lp: PartnerReturn,
  promote_earned: z.number().nonnegative().default(0),
});
export type PartnershipOutput = z.infer<typeof PartnershipOutput>;

// ─────────────────────────── Variance ───────────────────────────

export const VarianceFlag = z.object({
  id: z.string().uuid(),
  deal_id: z.string().uuid(),
  field: z.string().min(1).max(200),
  actual: z.number(),
  broker: z.number(),
  delta: z.number(),
  delta_pct: z.number().nullable().optional(),
  severity: Severity,
  rule_id: z.string().min(1).max(120),
  source_document_id: z.string().uuid().nullable().optional(),
  source_page: z.number().int().min(1).nullable().optional(),
  note: z.string().max(2000).nullable().optional(),
});
export type VarianceFlag = z.infer<typeof VarianceFlag>;

export const VarianceReport = z.object({
  deal_id: z.string().uuid(),
  flags: z.array(VarianceFlag).default([]),
  critical_count: z.number().int().nonnegative().default(0),
  warn_count: z.number().int().nonnegative().default(0),
  info_count: z.number().int().nonnegative().default(0),
});
export type VarianceReport = z.infer<typeof VarianceReport>;

// ─────────────────────────── Memo ───────────────────────────

export const Citation = z.object({
  document_id: z.string().uuid(),
  page: z.number().int().min(1),
  field: z.string().max(200).nullable().optional(),
  excerpt: z.string().max(1000).nullable().optional(),
});
export type Citation = z.infer<typeof Citation>;

export const MemoSectionId = z.enum([
  "executive_summary",
  "deal_overview",
  "investment_thesis",
  "market_analysis",
  "financial_analysis",
  "debt_structure",
  "returns_summary",
  "partnership_terms",
  "risk_factors",
  "recommendation",
]);
export type MemoSectionId = z.infer<typeof MemoSectionId>;

export const MemoSection = z.object({
  section_id: MemoSectionId,
  title: z.string().min(1).max(200),
  body: z.string().min(1),
  citations: z.array(Citation).default([]),
  analyst_edits: z.string().nullable().optional(),
});
export type MemoSection = z.infer<typeof MemoSection>;

export const InvestmentMemo = z.object({
  deal_id: z.string().uuid(),
  sections: z.array(MemoSection),
  generated_at: z.string().datetime(),
  confidence: ConfidenceReport,
  analyst_id: z.string().uuid().nullable().optional(),
  version: z.number().int().min(1).default(1),
});
export type InvestmentMemo = z.infer<typeof InvestmentMemo>;

// ─────────────────────────── Market ───────────────────────────

export const BuyerType = z.enum([
  "REIT",
  "Institutional",
  "PE Fund",
  "Private",
  "Owner Operator",
  "Sovereign Wealth",
  "Family Office",
  "Other",
]);
export type BuyerType = z.infer<typeof BuyerType>;

export const MarketDataSource = z.enum(["STR", "Kalibri Labs", "CoStar", "Internal", "Other"]);
export type MarketDataSource = z.infer<typeof MarketDataSource>;

export const MarketData = z.object({
  submarket: z.string().min(1).max(200),
  market: z.string().min(1).max(200).nullable().optional(),
  occupancy: z.number().min(0).max(1),
  adr: z.number().nonnegative(),
  revpar: z.number().nonnegative(),
  supply_growth: z.number(),
  demand_growth: z.number(),
  yoy_revpar: z.number().nullable().optional(),
  inventory_rooms: z.number().int().nonnegative().nullable().optional(),
  inventory_hotels: z.number().int().nonnegative().nullable().optional(),
  as_of: z.string(), // ISO date
  source: MarketDataSource.default("STR"),
});
export type MarketData = z.infer<typeof MarketData>;

export const TransactionComp = z.object({
  name: z.string().min(1).max(200),
  market: z.string().min(1).max(200),
  date: z.string(), // ISO date
  keys: z.number().int().positive(),
  sale_price: z.number().positive(),
  price_per_key: z.number().positive(),
  cap_rate: z.number().min(0).max(0.3),
  buyer_type: BuyerType,
  buyer_name: z.string().nullable().optional(),
});
export type TransactionComp = z.infer<typeof TransactionComp>;

export const CompSet = z.object({
  id: z.string().uuid(),
  name: z.string().min(1).max(200),
  description: z.string().nullable().optional(),
  properties_count: z.number().int().nonnegative(),
  transactions: z.array(TransactionComp).default([]),
  used_in_deal_ids: z.array(z.string().uuid()).default([]),
  starred: z.boolean().default(false),
  hidden: z.boolean().default(false),
  updated_at: z.string(), // ISO date
});
export type CompSet = z.infer<typeof CompSet>;

// ─────────────────────────── Analysis ───────────────────────────

export const RiskTier = z.enum(["Low Risk", "Medium Risk", "High Risk"]);
export type RiskTier = z.infer<typeof RiskTier>;

export const RiskCategoryName = z.enum([
  "Overall",
  "RevPAR Volatility",
  "Market Supply Risk",
  "Operator Risk",
  "Capital Needs",
  "Debt Risk",
  "Brand Risk",
]);
export type RiskCategoryName = z.infer<typeof RiskCategoryName>;

export const RiskCategory = z.object({
  name: RiskCategoryName,
  tier: RiskTier,
  score: z.number().int().min(0).max(100),
  note: z.string().nullable().optional(),
});
export type RiskCategory = z.infer<typeof RiskCategory>;

export const RiskAssessment = z.object({
  deal_id: z.string().uuid(),
  overall: RiskTier,
  score: z.number().int().min(0).max(100),
  by_category: z.array(RiskCategory).default([]),
});
export type RiskAssessment = z.infer<typeof RiskAssessment>;

export const Insight = z.object({
  title: z.string().min(1).max(200),
  body: z.string().min(1).max(4000),
});
export type Insight = z.infer<typeof Insight>;

export const ScenarioSummary = z.object({
  name: z.string().min(1).max(80),
  probability: z.number().min(0).max(1),
  irr: z.number(),
  coc: z.number(),
  multiple: z.number().nonnegative(),
  exit_value: z.number().nonnegative(),
});
export type ScenarioSummary = z.infer<typeof ScenarioSummary>;

export const AnalysisReport = z.object({
  deal_id: z.string().uuid(),
  summary: z.array(z.string()).default([]),
  risks: RiskAssessment,
  insights: z.array(Insight).default([]),
  scenarios: z.array(ScenarioSummary).default([]),
});
export type AnalysisReport = z.infer<typeof AnalysisReport>;

// ─────────────────────────── Gates ───────────────────────────

const GateDecisionBase = z.object({
  deal_id: z.string().uuid(),
  tenant_id: z.string().min(1),
  decided_by: z.string().uuid(),
  decided_at: z.string().datetime(),
  approved: z.boolean(),
  decision: z.enum(["approve", "reject", "request_changes"]),
  comment: z.string().max(4000).nullable().optional(),
});
export const GateDecision = GateDecisionBase;
export type GateDecision = z.infer<typeof GateDecision>;

export const Gate1Decision = GateDecisionBase.extend({
  edits: z.record(z.string(), z.string()).default({}),
  reextract_documents: z.array(z.string().uuid()).default([]),
});
export type Gate1Decision = z.infer<typeof Gate1Decision>;

// ─────────────────────────── Cost ───────────────────────────

export const AgentCost = z.object({
  agent: z.string().min(1).max(80),
  calls: z.number().int().nonnegative().default(0),
  input_tokens: z.number().int().nonnegative().default(0),
  output_tokens: z.number().int().nonnegative().default(0),
  cache_read_tokens: z.number().int().nonnegative().default(0),
  cache_creation_tokens: z.number().int().nonnegative().default(0),
  // Decimals are serialized as strings by Pydantic; accept both.
  cost_usd: z.union([z.string(), z.number()]).transform((v) => Number(v)),
  avg_latency_ms: z.number().nonnegative().default(0),
});
export type AgentCost = z.infer<typeof AgentCost>;

export const DealCostReport = z.object({
  deal_id: z.string().uuid(),
  total_cost_usd: z.union([z.string(), z.number()]).transform((v) => Number(v)),
  budget_usd: z.union([z.string(), z.number()]).transform((v) => Number(v)),
  cache_hit_rate: z.number().min(0).max(1).default(0),
  by_agent: z.array(AgentCost).default([]),
  by_model: z.record(z.string(), AgentCost).default({}),
  timeline: z.array(ModelCall).default([]),
  generated_at: z.string().datetime(),
});
export type DealCostReport = z.infer<typeof DealCostReport>;

export const Gate2Decision = GateDecisionBase.extend({
  recommendation: z.enum([
    "Proceed_to_LOI",
    "Proceed_with_Conditions",
    "Pass",
    "Refer_Up",
  ]),
  edits: z.record(z.string(), z.string()).default({}),
  waivers_granted: z.array(z.string()).default([]),
  waiver_justification: z.string().max(2000).nullable().optional(),
}).superRefine((val, ctx) => {
  if (val.waivers_granted.length === 0) return;
  if (!val.waiver_justification || val.waiver_justification.trim().length < 50) {
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["waiver_justification"],
      message: "must be at least 50 characters when waivers_granted is non-empty",
    });
  }
});
export type Gate2Decision = z.infer<typeof Gate2Decision>;
