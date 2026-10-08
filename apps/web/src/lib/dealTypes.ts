/**
 * FON-46 / FON-41 R-047 — the deal-type vocabulary shared by the create-deal
 * wizard and the Overview's Investment Profile toggle.
 *
 * The worker stores `deal_type` as free text (`CreateDealBody.deal_type`,
 * `UpdateDealBody.deal_type`: `str | None`, max_length 40) and no worker
 * engine branches on it today — the only consumer that changes behavior is
 * the Overview's section configuration. Adaptive Reuse is a conversion of
 * an existing (often non-hotel) building, so it takes the Development
 * configuration (planned keys / construction loan / permanent financing).
 */

export type DealTypeId = 'acquisition' | 'development' | 'adaptive_reuse';

export const DEAL_TYPE_OPTIONS: readonly { id: DealTypeId; label: string; desc: string }[] = [
  { id: 'acquisition', label: 'Acquisition', desc: 'Purchase of an existing hotel' },
  { id: 'development', label: 'Development', desc: 'Ground-up hotel development' },
  { id: 'adaptive_reuse', label: 'Adaptive Reuse', desc: 'Convert an existing building to hotel use' },
];

/**
 * Read a stored `deal_type` into the vocabulary. `redevelopment` is the
 * legacy wizard id whose option read "Redevelopment / Adaptive Reuse", so it
 * reads as Adaptive Reuse. Anything else (null, unknown) is an Acquisition —
 * the pre-FON-46 default.
 */
export function normalizeDealType(raw: string | null | undefined): DealTypeId {
  const v = (raw ?? '').trim().toLowerCase();
  if (v === 'development') return 'development';
  if (v === 'adaptive_reuse' || v === 'adaptive reuse' || v === 'redevelopment') return 'adaptive_reuse';
  return 'acquisition';
}

export function dealTypeLabel(raw: string | null | undefined): string {
  if (!raw) return '—';
  const id = normalizeDealType(raw);
  return DEAL_TYPE_OPTIONS.find((d) => d.id === id)?.label ?? '—';
}

/** Development and Adaptive Reuse share the build / convert configuration. */
export function isDevelopmentLike(raw: string | null | undefined): boolean {
  return normalizeDealType(raw) !== 'acquisition';
}
