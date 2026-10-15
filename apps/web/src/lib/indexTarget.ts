/**
 * Penetration-target units — the ONE web reading of an MPI / ARI target,
 * mirroring the worker's ``validate_index_target`` / ``_index_points``
 * (apps/worker/app/services/index_methodology.py).
 *
 * A target is typed either as index points (100 = parity with the benchmark)
 * or as a ratio (1.0 = parity). The accepted bands do not overlap, so a value
 * names its own unit: 0.25–2.5 is a ratio (× 100), 25–250 is points. Anything
 * between or beyond them ("5", "12", "300") is ambiguous or implausible and is
 * REFUSED — never read as 5 index points. The Index Methodology panel, the
 * penetration table and the at-target revenue opportunity all read targets
 * through here, so a ratio stored as 1.10 is 110 everywhere and never prices
 * an opportunity 100× off.
 */

export const INDEX_POINTS_RANGE: readonly [number, number] = [25, 250];
export const INDEX_RATIO_RANGE: readonly [number, number] = [0.25, 2.5];

function coerce(raw: unknown): number | null {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  if (typeof raw === 'string') {
    const t = raw.replace(/[\s,%]/g, '');
    if (!t) return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

/** One target as typed or stored → `{ points }`, or `{ points: null, reason }`. */
export function validateIndexTarget(raw: unknown): { points: number | null; reason: string | null } {
  const f = coerce(raw);
  if (f == null) return { points: null, reason: 'not a number' };
  const [loR, hiR] = INDEX_RATIO_RANGE;
  const [loP, hiP] = INDEX_POINTS_RANGE;
  if (f >= loR && f <= hiR) return { points: f * 100, reason: null };
  if (f >= loP && f <= hiP) return { points: f, reason: null };
  return {
    points: null,
    reason: `${f} is outside the accepted range — index points ${loP}–${hiP} (100 = parity with the benchmark) or a ratio ${loR}–${hiR} (1.0 = parity)`,
  };
}

/** Index points for a target, or null when it is unset or out of range. */
export function toIndexPoints(raw: unknown): number | null {
  return validateIndexTarget(raw).points;
}
