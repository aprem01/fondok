/**
 * Header room count (E-003 / FON-59).
 *
 * The create-deal wizard sends `keys` as null and the worker fills it from
 * the Offering Memorandum extraction a minute later. Until then there is no
 * number to show — render the dash, never a fabricated 0. A stored 0 is
 * treated the same way: no hotel has zero rooms, so 0 means "not set yet".
 */
export function formatKeys(keys: number | null | undefined): string {
  return typeof keys === 'number' && Number.isFinite(keys) && keys > 0 ? String(keys) : '—';
}
