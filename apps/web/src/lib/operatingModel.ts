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
 * The management-fee context line shown beside the Management fee assumption.
 *
 * The only management-fee range documented in this repo is the USALI
 * validation rule `MGMT_FEE_RANGE` (base fee as a share of Total Revenue). It
 * is NOT specific to an operating model, so the hint says so rather than
 * presenting it as a per-operator benchmark. Returns null when no operating
 * model is captured — nothing is shown then.
 */
export function mgmtFeeOperatingModelHint(v: string | null | undefined): string | null {
  const label = operatingModelLabel(v);
  if (!label) return null;
  const rule = rulesById.MGMT_FEE_RANGE;
  if (!rule || rule.threshold_min == null || rule.threshold_max == null) {
    return `Operating model: ${label}.`;
  }
  const lo = Math.round(rule.threshold_min * 1000) / 10;
  const hi = Math.round(rule.threshold_max * 1000) / 10;
  return (
    `Operating model: ${label}. Validation range for the base fee is ${lo}–${hi}% of ` +
    'total revenue (USALI check, all operating models) — no operator-specific range is documented.'
  );
}
