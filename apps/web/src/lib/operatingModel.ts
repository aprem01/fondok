/**
 * R-025 — the intended operating model, captured in the New Project wizard and
 * persisted as `deals.operating_model`. Descriptive only: no engine reads it,
 * and nothing here changes model math.
 *
 * The ids mirror the worker's `OperatingModel` literal in
 * `apps/worker/app/api/deals.py`.
 */
import { rulesById } from '@/lib/variance';
import {
  MGMT_FEE_GUIDANCE_BY_MODEL,
  MGMT_FEE_GUIDANCE_DISCLAIMER,
  OPERATING_MODEL_LABELS,
} from '@/lib/methodology/managementFeeGuidance.generated';

export type OperatingModelId = 'owner_operated' | 'third_party' | 'brand_managed';

export const OPERATING_MODEL_OPTIONS: ReadonlyArray<{
  id: OperatingModelId;
  label: string;
  desc: string;
}> = [
  {
    id: 'owner_operated',
    label: OPERATING_MODEL_LABELS.owner_operated,
    desc: 'The owner runs the hotel with its own management team.',
  },
  {
    id: 'third_party',
    label: OPERATING_MODEL_LABELS.third_party,
    desc: 'An independent management company operates the hotel under contract.',
  },
  {
    id: 'brand_managed',
    label: OPERATING_MODEL_LABELS.brand_managed,
    desc: 'The brand itself manages and operates the hotel.',
  },
];

export function isOperatingModelId(v: unknown): v is OperatingModelId {
  return typeof v === 'string' && OPERATING_MODEL_OPTIONS.some((o) => o.id === v);
}

/** Display label, or null when not captured / unknown. */
export function operatingModelLabel(v: string | null | undefined): string | null {
  return OPERATING_MODEL_OPTIONS.find((o) => o.id === v)?.label ?? null;
}

/**
 * FON-102 — Sam's directional management-fee guidance, by operating model.
 *
 * DIRECTIONAL / ILLUSTRATIVE ONLY. This is not an underwriting standard: the
 * actual fee varies by operator, brand, contract and asset, and the analyst
 * sets `mgmt_fee_pct` (and, Sam #9, the opt-in
 * `incentive_mgmt_fee_pct_of_gop`) themselves — nothing here changes either.
 *
 * FON-120: the text is authored ONCE in
 * `packages/methodology/data/management_fee_guidance.yaml` and generated
 * here (`scripts/gen_methodology.py`), so the product, the Methodology page
 * (`{{slot:mgmt-fee-guidance}}`) and the text Atlas reads cannot drift.
 */
export { MGMT_FEE_GUIDANCE_BY_MODEL, MGMT_FEE_GUIDANCE_DISCLAIMER };

/**
 * The management-fee context line shown beside the Management fee assumption.
 *
 * Reads: the operating model, Sam's directional guidance for that model
 * (clearly marked directional / illustrative), and the one range the repo
 * actually validates against — the USALI rule `MGMT_FEE_RANGE` (base fee as a
 * share of Total Revenue), which applies to every operating model. Returns
 * null when no operating model is captured — nothing is shown then.
 */
export function mgmtFeeOperatingModelHint(v: string | null | undefined): string | null {
  const label = operatingModelLabel(v);
  if (!label || !isOperatingModelId(v)) return null;
  const guidance = MGMT_FEE_GUIDANCE_BY_MODEL[v];
  const rule = rulesById.MGMT_FEE_RANGE;
  const usali =
    rule && rule.threshold_min != null && rule.threshold_max != null
      ? ` Validation range for the base fee is ${Math.round(rule.threshold_min * 1000) / 10}–${Math.round(rule.threshold_max * 1000) / 10}% of total revenue (USALI check, all operating models).`
      : '';
  return `Operating model: ${label}. ${guidance} ${MGMT_FEE_GUIDANCE_DISCLAIMER}${usali}`;
}
