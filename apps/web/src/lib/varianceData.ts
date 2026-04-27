// Variance fixtures for the Kimpton Angler golden-set deal.
// Source of truth: /evals/golden-set/kimpton-angler/expected/variance.json
//                  /evals/golden-set/usali-rules.csv
// Re-exported as TS so the UI can render the broker-vs-T12 variance flow
// without hitting the worker / API.

export type Severity = 'CRITICAL' | 'WARN' | 'INFO';

export interface USALIRule {
  rule_id: string;
  name: string;
  category: string;
  formula_or_check: string;
  threshold_min: number;
  threshold_max: number;
  severity: Severity;
  description: string;
}

export interface VarianceSourceDoc {
  document_id: string;
  page: number;
  field: string;
}

export interface VarianceFlag {
  flag_id: string;
  rule_id: string;
  severity: Severity;
  metric: string;
  /** Human-friendly field label rendered in the comparison table. */
  field_label: string;
  /** Broker pro-forma value (may be undefined for single-source flags). */
  broker_value?: number;
  /** T-12 actual value (may be undefined for single-source flags). */
  t12_value?: number;
  /** Generic value for non-comparison rules (e.g. insurance per key). */
  value?: number;
  threshold_min?: number;
  threshold_max?: number;
  /** Absolute variance (broker - t12) where applicable. */
  variance_abs?: number;
  /** Relative variance (broker - t12) / t12. Negative => broker overstated. */
  variance_pct?: number;
  /** Variance in percentage points (for occupancy etc). */
  variance_pct_pts?: number;
  /** Display format for the row: 'currency' | 'percent' | 'currency_per_key' | 'index'. */
  format: 'currency' | 'percent' | 'currency_per_key' | 'index';
  /** True when broker direction is favorable (i.e. overstated NOI/Occ/ADR). */
  broker_overstates: boolean;
  /** Estimated $ impact on underwritten NOI if broker number is taken at face. */
  noi_impact_usd: number;
  explanation: string;
  recommended_action: string;
  source_documents: VarianceSourceDoc[];
}

// --- Severity counts (kept in sync with flags array below) ----------------
export const varianceSummary = {
  total_flags: 7,
  critical: 3,
  warn: 3,
  info: 1,
  evaluated_at: '2026-04-19T14:35:00Z',
  deal_id: 'kimpton-angler-2026',
  broker_noi_usd: 5_200_000,
  t12_noi_usd: 4_181_000,
  noi_overstate_usd: 1_019_000,
  noi_overstate_pct: -0.196,
  sources_reconciled: 3,
  sources_total: 3,
};

// --- USALI rules (subset most relevant to broker-vs-T12 reconciliation) ---
export const usaliRules: USALIRule[] = [
  {
    rule_id: 'BROKER_VS_T12_NOI_VARIANCE',
    name: 'Broker vs T-12 NOI Variance',
    category: 'Variance',
    formula_or_check: 'abs(broker_noi - t12_noi) / t12_noi',
    threshold_min: 0,
    threshold_max: 0.05,
    severity: 'CRITICAL',
    description:
      'Broker proforma NOI variance from T-12 actual exceeding 5 percent must be reconciled.',
  },
  {
    rule_id: 'BROKER_VS_T12_OCC_VARIANCE',
    name: 'Broker vs T-12 Occupancy Variance',
    category: 'Variance',
    formula_or_check: 'abs(broker_occupancy - t12_occupancy)',
    threshold_min: 0,
    threshold_max: 0.02,
    severity: 'WARN',
    description:
      'Broker proforma occupancy variance from T-12 actual exceeding 200 bps must be reconciled.',
  },
  {
    rule_id: 'BROKER_VS_T12_ADR_VARIANCE',
    name: 'Broker vs T-12 ADR Variance',
    category: 'Variance',
    formula_or_check: 'abs(broker_adr - t12_adr) / t12_adr',
    threshold_min: 0,
    threshold_max: 0.05,
    severity: 'WARN',
    description:
      'Broker proforma ADR variance from T-12 actual exceeding 5 percent must be reconciled.',
  },
  {
    rule_id: 'NOI_MARGIN_RANGE',
    name: 'NOI Margin Range',
    category: 'Profitability',
    formula_or_check: 'noi / total_revenue',
    threshold_min: 0.10,
    threshold_max: 0.45,
    severity: 'WARN',
    description:
      'Net Operating Income margin should fall between 10 and 45 percent of Total Revenue.',
  },
  {
    rule_id: 'GOP_MARGIN_RANGE',
    name: 'GOP Margin Range',
    category: 'Profitability',
    formula_or_check: 'gop / total_revenue',
    threshold_min: 0.15,
    threshold_max: 0.55,
    severity: 'WARN',
    description:
      'Gross Operating Profit margin should fall between 15 and 55 percent of Total Revenue.',
  },
  {
    rule_id: 'MGMT_FEE_RANGE',
    name: 'Management Fee Range',
    category: 'Fees',
    formula_or_check: 'mgmt_fee / total_revenue',
    threshold_min: 0.02,
    threshold_max: 0.06,
    severity: 'WARN',
    description: 'Base management fee typically 2 to 6 percent of Total Revenue.',
  },
  {
    rule_id: 'FFE_RESERVE_RANGE',
    name: 'FF&E Reserve Range',
    category: 'Reserves',
    formula_or_check: 'ffe_reserve / total_revenue',
    threshold_min: 0.03,
    threshold_max: 0.05,
    severity: 'WARN',
    description:
      'FF&E Reserve should be 3 to 5 percent of Total Revenue per industry standard.',
  },
  {
    rule_id: 'INSURANCE_PER_KEY',
    name: 'Insurance Per Key',
    category: 'Fixed Charges',
    formula_or_check: 'insurance_expense / keys',
    threshold_min: 500,
    threshold_max: 2500,
    severity: 'WARN',
    description:
      'Property insurance per key should fall between $500 and $2,500 outside of coastal markets.',
  },
  {
    rule_id: 'INSURANCE_PER_KEY_COASTAL',
    name: 'Insurance Per Key Coastal Flag',
    category: 'Fixed Charges',
    formula_or_check: 'insurance_expense / keys',
    threshold_min: 2500,
    threshold_max: 8000,
    severity: 'INFO',
    description:
      'Coastal market property insurance commonly exceeds $2,500 per key after wind and flood loadings.',
  },
  {
    rule_id: 'UTILITIES_PER_KEY',
    name: 'Utilities Per Key',
    category: 'Operations',
    formula_or_check: 'utilities_expense / keys',
    threshold_min: 800,
    threshold_max: 2500,
    severity: 'WARN',
    description: 'Annual utilities expense per key should fall between $800 and $2,500.',
  },
  {
    rule_id: 'REVPAR_VS_COMPSET',
    name: 'RevPAR vs Comp Set Index',
    category: 'Market',
    formula_or_check: 'revpar / compset_revpar',
    threshold_min: 0.85,
    threshold_max: 1.30,
    severity: 'WARN',
    description:
      'Subject RevPAR Generation Index (RGI) should fall between 0.85 and 1.30.',
  },
  {
    rule_id: 'OPEX_RATIO_BROKER_VS_T12',
    name: 'Broker vs T-12 OpEx Ratio',
    category: 'Variance',
    formula_or_check: 'broker_opex_ratio - t12_opex_ratio',
    threshold_min: -0.04,
    threshold_max: 0.04,
    severity: 'CRITICAL',
    description:
      'Broker proforma operating expense ratio variance from T-12 actual exceeding 400 bps must be reconciled.',
  },
];

// Helper map for quick lookup (rule_id -> rule).
export const rulesById: Record<string, USALIRule> = usaliRules.reduce(
  (acc, r) => {
    acc[r.rule_id] = r;
    return acc;
  },
  {} as Record<string, USALIRule>,
);

// --- Variance flags --------------------------------------------------------
// 7 flags total. Three CRITICAL, three WARN, one INFO — matching the
// instruction set. NOI is the headline.
export const varianceFlags: VarianceFlag[] = [
  {
    flag_id: 'VF-001',
    rule_id: 'BROKER_VS_T12_NOI_VARIANCE',
    severity: 'CRITICAL',
    metric: 'NOI',
    field_label: 'Net Operating Income (Y1)',
    broker_value: 5_200_000,
    t12_value: 4_181_000,
    variance_abs: 1_019_000,
    variance_pct: -0.196,
    format: 'currency',
    broker_overstates: true,
    noi_impact_usd: 1_019_000,
    explanation:
      'Broker proforma NOI of $5.20M is 19.6% above T-12 actual of $4.18M, exceeding the 5% CRITICAL threshold. Driven by optimistic occupancy uplift and PIP-driven ADR growth assumptions before any renovation work has been completed.',
    recommended_action:
      'Apply T-12 actual NOI as base case in underwriting; treat broker proforma as upside case only after PIP completion in Year 2.',
    source_documents: [
      { document_id: 'kimpton-angler-om-2026', page: 34, field: 'broker_proforma.noi_usd' },
      { document_id: 'kimpton-angler-t12-2026q1', page: 4, field: 'p_and_l_usali.net_operating_income.noi_usd' },
    ],
  },
  {
    flag_id: 'VF-002',
    rule_id: 'OPEX_RATIO_BROKER_VS_T12',
    severity: 'CRITICAL',
    metric: 'OpEx Ratio',
    field_label: 'OpEx Ratio (% of Revenue)',
    broker_value: 0.7422, // 1 - NOI margin 25.78%
    t12_value: 0.7770, // 1 - NOI margin 22.30%
    variance_abs: -0.0348,
    variance_pct: -0.045,
    format: 'percent',
    broker_overstates: true,
    noi_impact_usd: 702_000,
    explanation:
      'Broker proforma operating expense ratio of 74.2% understates T-12 actual of 77.7% by 348 bps. Broker assumes labor productivity gains and franchise-fee restructuring that have not been negotiated. Implied OpEx savings of ~$702K flow straight to NOI.',
    recommended_action:
      'Re-underwrite using T-12 OpEx ratio of 77.7% until management contract amendment and labor plan are executed.',
    source_documents: [
      { document_id: 'kimpton-angler-om-2026', page: 34, field: 'broker_proforma.undistributed_expenses_usd' },
      { document_id: 'kimpton-angler-t12-2026q1', page: 3, field: 'p_and_l_usali.undistributed_operating_expenses.total_undistributed_expenses' },
    ],
  },
  {
    flag_id: 'VF-003',
    rule_id: 'INSURANCE_PER_KEY',
    severity: 'CRITICAL',
    metric: 'Insurance / Key',
    field_label: 'Insurance per Key (FL coastal)',
    broker_value: 2_400, // implied per OM proforma fixed charges
    t12_value: 3_803,
    variance_abs: -1_403,
    variance_pct: -0.369,
    format: 'currency_per_key',
    broker_overstates: true,
    noi_impact_usd: 185_196, // 1403 * 132 keys
    explanation:
      'Broker proforma insurance load of $2,400 per key understates T-12 actual of $3,803 per key by 37%. Miami Beach wind/flood exposure has driven coastal insurance markets up 40-60% over the last two renewal cycles. Underwriting at the broker number ignores ~$185K of recurring fixed charges.',
    recommended_action:
      'Apply T-12 insurance + 5% renewal escalator. Stress-test 25% spike in renewal year given Florida market dislocation.',
    source_documents: [
      { document_id: 'kimpton-angler-om-2026', page: 34, field: 'broker_proforma.fixed_charges_usd' },
      { document_id: 'kimpton-angler-t12-2026q1', page: 4, field: 'p_and_l_usali.fixed_charges.insurance' },
    ],
  },
  {
    flag_id: 'VF-004',
    rule_id: 'BROKER_VS_T12_OCC_VARIANCE',
    severity: 'WARN',
    metric: 'Occupancy',
    field_label: 'Year-1 Occupancy',
    broker_value: 0.80,
    t12_value: 0.762,
    variance_abs: 0.038,
    variance_pct_pts: 3.8,
    format: 'percent',
    broker_overstates: true,
    noi_impact_usd: 415_000,
    explanation:
      'Broker proforma occupancy of 80.0% is 380 bps above T-12 actual of 76.2%. Subject already at MPI 101.7 versus comp set, so further occupancy gains require market-level demand growth that is not yet evident.',
    recommended_action:
      'Cap base case occupancy at 77.5% (T-12 + 130 bps) reflecting modest market growth.',
    source_documents: [
      { document_id: 'kimpton-angler-om-2026', page: 34, field: 'broker_proforma.occupancy_pct' },
      { document_id: 'kimpton-angler-t12-2026q1', page: 1, field: 'occupancy_pct' },
    ],
  },
  {
    flag_id: 'VF-005',
    rule_id: 'BROKER_VS_T12_ADR_VARIANCE',
    severity: 'WARN',
    metric: 'ADR',
    field_label: 'Year-1 ADR',
    broker_value: 395,
    t12_value: 385,
    variance_abs: 10,
    variance_pct: -0.026,
    format: 'currency',
    broker_overstates: true,
    noi_impact_usd: 360_000,
    explanation:
      'Broker proforma ADR of $395 is 2.6% above T-12 actual of $385. Within the 5% threshold but worth noting as PIP-driven ADR uplift assumption that requires Year-2 renovation completion.',
    recommended_action:
      'Acceptable for upside case. Underwrite ADR uplift to $395 in Year 2 post-PIP only.',
    source_documents: [
      { document_id: 'kimpton-angler-om-2026', page: 34, field: 'broker_proforma.adr_usd' },
      { document_id: 'kimpton-angler-t12-2026q1', page: 1, field: 'adr_usd' },
    ],
  },
  {
    flag_id: 'VF-006',
    rule_id: 'MGMT_FEE_RANGE',
    severity: 'WARN',
    metric: 'Mgmt Fee',
    field_label: 'Management Fee (% Revenue)',
    broker_value: 0.030, // 605K / 20.18M
    t12_value: 0.030,
    variance_abs: 0,
    variance_pct: 0,
    format: 'percent',
    broker_overstates: false,
    noi_impact_usd: 90_000,
    explanation:
      'Both broker and T-12 reflect 3.0% base management fee, within the 2-6% range. However, broker proforma omits incentive management fee accrual that becomes payable once GOP exceeds the 35% hurdle implied by the broker case.',
    recommended_action:
      'Add 5% IMF on GOP-above-hurdle to base case (~$90K Y1 impact at broker numbers).',
    source_documents: [
      { document_id: 'kimpton-angler-om-2026', page: 34, field: 'broker_proforma.mgmt_fee_usd' },
      { document_id: 'kimpton-angler-t12-2026q1', page: 4, field: 'p_and_l_usali.fees_and_reserves.base_management_fee' },
    ],
  },
  {
    flag_id: 'VF-007',
    rule_id: 'INSURANCE_PER_KEY_COASTAL',
    severity: 'INFO',
    metric: 'Coastal Insurance Flag',
    field_label: 'Coastal Insurance Range Check',
    value: 3_803,
    threshold_min: 2_500,
    threshold_max: 8_000,
    format: 'currency_per_key',
    broker_overstates: false,
    noi_impact_usd: 0,
    explanation:
      'T-12 insurance of $3,803 per key sits within the coastal-market range of $2,500–$8,000. Miami Beach wind/flood loading is consistent with the FL Panhandle and Outer Banks comps reviewed.',
    recommended_action:
      'Acceptable for Miami Beach coastal exposure. Hold quote-driven sensitivity for renewal cycle.',
    source_documents: [
      { document_id: 'kimpton-angler-t12-2026q1', page: 4, field: 'p_and_l_usali.fixed_charges.insurance' },
    ],
  },
];

// Convenience selectors used by the UI.
export const criticalCount = varianceFlags.filter(f => f.severity === 'CRITICAL').length;
export const warnCount = varianceFlags.filter(f => f.severity === 'WARN').length;
export const infoCount = varianceFlags.filter(f => f.severity === 'INFO').length;
