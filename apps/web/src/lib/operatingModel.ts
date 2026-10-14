/**
 * R-025 — the intended operating model, captured in the New Project wizard and
 * persisted as `deals.operating_model`. Descriptive only: no engine reads it,
 * and nothing here changes model math.
 *
 * The ids mirror the worker's `OperatingModel` literal in
 * `apps/worker/app/api/deals.py`.
 */
import { rulesById } from '@/lib/varianceData';

export type OperatingModelId = 'owner_operated' | 'third_party' | 'brand_managed';

export const OPERATING_MODEL_OPTIONS: ReadonlyArray<{
  id: OperatingModelId;
  label: string;
  desc: string;
}> = [
  {
    id: 'owner_operated',
    label: 'Owner-operated / in-house',
    desc: 'The owner runs the hotel with its own management team.',
  },
  {
    id: 'third_party',
    label: 'Third-party operator',
    desc: 'An independent management company operates the hotel under contract.',
  },
  {
    id: 'brand_managed',
    label: 'Brand-managed / brand-operated',
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
 * sets `mgmt_fee_pct` themselves — nothing here changes it. The same text is
 * printed on /methodology so the product and its documentation cannot drift.
 */
export const MGMT_FEE_GUIDANCE_DISCLAIMER =
  'Directional guidance only — not an underwriting standard; actual fees vary by operator, brand, contract and asset. Nothing here changes the management fee assumption.';

export const MGMT_FEE_GUIDANCE_BY_MODEL: Readonly<Record<OperatingModelId, string>> = {
  third_party:
    'Third-party operators typically charge a base fee of roughly 2–4% of total revenue, with a possible incentive fee on top (often a share of GOP or NOI above a hurdle).',
  brand_managed:
    'Brand-managed hotels typically carry a base fee of roughly 2–4% of total revenue, a possible incentive fee, and brand-related charges (marketing / programme fees, reservation and loyalty costs) that sit outside the base fee.',
  owner_operated:
    'Owner-operated hotels pay no external management fee by definition; analysts may still include an imputed fee to normalise expenses against managed comparables.',
};

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
