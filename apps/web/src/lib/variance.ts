/**
 * Variance types + the USALI rule catalog the Variance tab and the operating-
 * model guidance read.
 *
 * `VarianceFlag` is the web's render shape for one broker-vs-T-12 finding;
 * `useVariance` maps the worker's `GET /analysis/{deal_id}/variance` rows onto
 * it. The rule catalog mirrors evals/golden-set/usali-rules.csv: rule names,
 * descriptions and thresholds — reference material, never a deal's figures.
 *
 * Standing rule (no invented data): this module carries NO variance flags, no
 * summary totals and no document names. The former Kimpton Angler fixture set
 * was removed 2026-10-30 — a deal with no worker report renders the
 * "awaiting inputs" / "Worker not configured" state, never a canned finding.
 */
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
  /**
   * Estimated $ impact on underwritten NOI if broker number is taken at face.
   * FON-54a: populated ONLY when `impact_basis === 'noi'` (NOI / GOP concepts);
   * a revenue- or expense-line delta is not an NOI impact and reads 0 here.
   */
  noi_impact_usd: number;
  explanation: string;
  recommended_action: string;
  source_documents: VarianceSourceDoc[];
  /** FON-54a — canonical concept key (`rooms_revenue`, `noi` …); the diligence-status key. */
  concept?: string;
  /** FON-54a — what the delta is: 'noi' | 'revenue' | 'expense' | 'other'. */
  impact_basis?: 'noi' | 'revenue' | 'expense' | 'other';
  /** FON-54a — raw extractor rows consolidated into this flag (Technical detail). */
  raw_fields?: VarianceRawField[];
  /** FON-54a — the broker and T-12 figures are not on the same basis; needs review, no severity. */
  basis_mismatch?: boolean;
  unit_note?: string | null;
  source_doc_type?: string | null;
}

/** FON-54a — one raw broker-field comparison behind a consolidated flag. */
export interface VarianceRawField {
  field: string;
  rule_id?: string | null;
  severity: string;
  broker?: number | null;
  actual?: number | null;
  delta?: number | null;
  delta_pct?: number | null;
  source_page?: number | null;
  source_doc_type?: string | null;
  source_document?: string | null;
  unit_note?: string | null;
  /** Set ⇒ the row was NOT admitted as the broker's claim (and why). */
  excluded_reason?: string | null;
  basis_mismatch?: boolean;
}

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
