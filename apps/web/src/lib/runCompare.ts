/**
 * runCompare.ts — formatting + grouping for the Activity tab's "Compare runs"
 * panel (regression baselines, 2026-10-16).
 *
 * Every value shown comes straight from the worker's
 * ``GET /deals/{id}/engines/compare`` response. A value the response does not
 * carry renders "—" with the worker's reason; nothing is computed or filled
 * in here beyond display formatting.
 */
import type { RunCompareField, EngineRunSummary } from './api';

/** Last deploy before Sam's QA changes — the default "baseline before" cut. */
export const DEFAULT_BASELINE_CUTOFF = '2026-10-15T21:40';

/** ``YYYY-MM-DDTHH:MM`` (UTC, as typed in the picker) → ISO with ``Z``. */
export function cutoffToIso(local: string): string {
  return `${local}:00Z`;
}

export function fmtValue(v: number | string | null, unit: string): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  if (!Number.isFinite(v)) return '—';
  switch (unit) {
    case 'pct':
      return `${(v * 100).toFixed(2)}%`;
    case 'x':
      return `${v.toFixed(2)}x`;
    case 'year':
      return String(v);
    case 'usd': {
      const decimals = Math.abs(v) < 1000 ? 2 : 0;
      return `${v < 0 ? '-' : ''}$${Math.abs(v).toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}`;
    }
    default:
      return v.toLocaleString('en-US', { maximumFractionDigits: 4 });
  }
}

/** Absolute delta: percentages move in points, money in signed dollars. */
export function fmtDeltaAbs(d: number | null, unit: string): string {
  if (d === null || d === undefined || !Number.isFinite(d)) return '—';
  const sign = d > 0 ? '+' : d < 0 ? '-' : '';
  const a = Math.abs(d);
  switch (unit) {
    case 'pct':
      return `${sign}${(a * 100).toFixed(2)} pts`;
    case 'x':
      return `${sign}${a.toFixed(2)}x`;
    case 'usd': {
      const decimals = a < 1000 ? 2 : 0;
      return `${sign}$${a.toLocaleString('en-US', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}`;
    }
    default:
      return `${sign}${a.toLocaleString('en-US', { maximumFractionDigits: 4 })}`;
  }
}

export function fmtDeltaPct(p: number | null): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return '—';
  return `${p > 0 ? '+' : ''}${(p * 100).toFixed(2)}%`;
}

/** Rows grouped by engine group, in the worker's order. */
export function groupFields(
  fields: RunCompareField[],
): { group: string; rows: RunCompareField[] }[] {
  const out: { group: string; rows: RunCompareField[] }[] = [];
  for (const f of fields) {
    const last = out[out.length - 1];
    if (last && last.group === f.group) last.rows.push(f);
    else out.push({ group: f.group, rows: [f] });
  }
  return out;
}

/** Compact one-line label for a run in the picker. */
export function runLabel(r: EngineRunSummary): string {
  const when = r.completed_at ?? r.started_at;
  const ts = when ? `${when.slice(0, 16).replace('T', ' ')} UTC` : 'time unknown';
  const tags: string[] = [];
  if (r.is_canonical) tags.push('canonical');
  if (!r.is_full_chain) tags.push('partial chain');
  if (r.status !== 'complete') tags.push(r.status);
  if (r.scenario && !r.scenario.is_base) tags.push(`scenario: ${r.scenario.name}`);
  return `${ts} · ${r.run_id.slice(0, 8)}${tags.length ? ` (${tags.join(', ')})` : ''}`;
}

/** A provenance side as one short line ("" when the engine recorded none). */
export function provenanceText(
  p: RunCompareField['base_provenance'],
): string {
  if (!p) return '';
  const parts: string[] = [];
  if (p.source) parts.push(typeof p.source === 'string' ? p.source : JSON.stringify(p.source));
  if (p.state) parts.push(p.state);
  if (p.formula) parts.push(p.formula);
  return parts.join(' · ');
}
