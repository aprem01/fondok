'use client';
/**
 * Renovation displacement schedule — Sam, 2026-10-15.
 *
 * "Renovation impacts should be driven by explicit assumptions around timing,
 * room displacement, and rate adjustments." This table is where the analyst
 * states those three things, period by period: the months (1-based from the
 * acquisition close, inclusive), the share of rooms offline and the rate
 * discount on the rooms that keep selling. It is saved as ONE `field_overrides`
 * key — `renovation_displacement_schedule`, a list of periods — with ONE
 * justification note (an engine input, so the FON-74 note rule applies).
 *
 * OFF by default: with no period saved the worker runs every deal exactly as
 * before. When a schedule is saved it is the only displacement the revenue
 * engine applies — the scalar Y1 percentages further up the section are NOT
 * applied on top (the worker's status line says so).
 *
 * Nothing numeric is derived here. The per-year factors shown under the table
 * are the worker's (`displacement_status.schedule_by_year`), the same ones the
 * revenue engine ran on; the browser only formats them.
 */
import { useEffect, useMemo, useState, type CSSProperties, type ReactNode } from 'react';
import { Plus, X } from 'lucide-react';
import type { RenovationDisplacementPeriod, RenovationDisplacementYear } from '@/lib/api';
import { NOTE_PLACEHOLDER, NOTE_REQUIRED_MESSAGE, requiresNote } from '@/lib/overrideNote';
import { palette } from '@/components/design';

/** The `field_overrides` key the table writes — mirrors the worker's
 *  `engine_runner.KEY_RENOVATION_SCHEDULE`. */
export const RENOVATION_SCHEDULE_KEY = 'renovation_displacement_schedule';

/** Worker schema bounds (`RenovationDisplacementPeriod`): months 1–240, shares 0–1. */
const MAX_MONTH = 240;

/** The saved override value → typed periods (tolerant of the `{value, note}`
 *  envelope and of a worker that stored nothing). */
export function parseRenovationSchedule(raw: unknown): RenovationDisplacementPeriod[] {
  const value = raw != null && typeof raw === 'object' && 'value' in (raw as Record<string, unknown>)
    ? (raw as { value: unknown }).value
    : raw;
  if (!Array.isArray(value)) return [];
  const out: RenovationDisplacementPeriod[] = [];
  for (const e of value) {
    if (!e || typeof e !== 'object') continue;
    const r = e as Record<string, unknown>;
    const start = Number(r.start_month);
    const end = Number(r.end_month);
    if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
    out.push({
      start_month: start,
      end_month: end,
      rooms_offline_pct: Number.isFinite(Number(r.rooms_offline_pct)) ? Number(r.rooms_offline_pct) : 0,
      rate_discount_pct: Number.isFinite(Number(r.rate_discount_pct)) ? Number(r.rate_discount_pct) : 0,
      note: typeof r.note === 'string' && r.note.trim() ? r.note.trim() : undefined,
    });
  }
  return out;
}

/** One row while editing — strings, so a half-typed cell is never coerced. */
export interface PeriodDraft {
  start_month: string;
  end_month: string;
  /** Percent, as typed (25 = 25% of rooms offline). */
  rooms_offline_pct: string;
  /** Percent, as typed (10 = 10% off the rate). */
  rate_discount_pct: string;
  note: string;
}

function toDraft(p: RenovationDisplacementPeriod): PeriodDraft {
  return {
    start_month: String(p.start_month),
    end_month: String(p.end_month),
    rooms_offline_pct: String(Math.round(p.rooms_offline_pct * 1000) / 10),
    rate_discount_pct: String(Math.round(p.rate_discount_pct * 1000) / 10),
    note: p.note ?? '',
  };
}

const EMPTY_DRAFT: PeriodDraft = { start_month: '', end_month: '', rooms_offline_pct: '', rate_discount_pct: '', note: '' };

/**
 * Validate the drafts the way the worker will (`RenovationDisplacementPeriod`):
 * whole months 1–240 with last ≥ first, shares 0–100%. Returns the typed
 * periods or the first problem, so the Save button can refuse with a reason
 * instead of letting the API 422.
 */
export function draftsToPeriods(
  drafts: PeriodDraft[],
): { periods: RenovationDisplacementPeriod[]; error: null } | { periods: null; error: string } {
  const periods: RenovationDisplacementPeriod[] = [];
  for (let i = 0; i < drafts.length; i++) {
    const d = drafts[i];
    const n = i + 1;
    const start = Number(d.start_month);
    const end = Number(d.end_month);
    if (!Number.isInteger(start) || start < 1 || start > MAX_MONTH) {
      return { periods: null, error: `Period ${n}: first month must be a whole number from 1 to ${MAX_MONTH} (months after close).` };
    }
    if (!Number.isInteger(end) || end < start || end > MAX_MONTH) {
      return { periods: null, error: `Period ${n}: last month must be a whole number ≥ the first month and ≤ ${MAX_MONTH}.` };
    }
    const offline = d.rooms_offline_pct.trim() === '' ? 0 : Number(d.rooms_offline_pct);
    const discount = d.rate_discount_pct.trim() === '' ? 0 : Number(d.rate_discount_pct);
    if (!Number.isFinite(offline) || offline < 0 || offline > 100) {
      return { periods: null, error: `Period ${n}: rooms offline must be 0–100%.` };
    }
    if (!Number.isFinite(discount) || discount < 0 || discount > 100) {
      return { periods: null, error: `Period ${n}: rate discount must be 0–100%.` };
    }
    const note = d.note.trim();
    periods.push({
      start_month: start,
      end_month: end,
      rooms_offline_pct: offline / 100,
      rate_discount_pct: discount / 100,
      ...(note ? { note } : {}),
    });
  }
  return { periods, error: null };
}

export interface RenovationDisplacementScheduleProps {
  /** The saved schedule (from `field_overrides.renovation_displacement_schedule`). */
  periods: RenovationDisplacementPeriod[];
  /** The saved list-level note, shown beside the table. */
  savedNote?: string | null;
  /** The worker's per-year factors for the saved schedule (status payload). */
  byYear?: RenovationDisplacementYear[] | null;
  editable: boolean;
  /**
   * Save `periods` with the analyst's `note` (required — an engine input), or
   * `null` to clear the schedule (a revert to source — no note needed).
   */
  onSave: (periods: RenovationDisplacementPeriod[] | null, note: string) => Promise<void> | void;
}

const cell: CSSProperties = { padding: '3px 6px', fontSize: 11.5, verticalAlign: 'middle' };
const head: CSSProperties = { ...cell, fontWeight: 600, color: palette.textMuted, fontSize: 10.5, textAlign: 'left', whiteSpace: 'nowrap' };
const inputStyle: CSSProperties = {
  width: 56, fontSize: 11.5, padding: '2px 5px', border: `1px solid ${palette.border}`, borderRadius: 5,
  background: 'white', color: palette.ink,
};

function pct(v: number): string {
  const p = v * 100;
  return `${Number.isInteger(p) ? p : p.toFixed(1)}%`;
}

export default function RenovationDisplacementSchedule({
  periods, savedNote, byYear, editable, onSave,
}: RenovationDisplacementScheduleProps) {
  const [editing, setEditing] = useState(false);
  const [drafts, setDrafts] = useState<PeriodDraft[]>([]);
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const noteRequired = requiresNote(RENOVATION_SCHEDULE_KEY);

  // A save elsewhere (or a refetch) replaces the saved periods; drop a stale
  // edit session rather than letting it overwrite them.
  useEffect(() => { setEditing(false); }, [periods]);

  const begin = () => {
    setDrafts(periods.length ? periods.map(toDraft) : [{ ...EMPTY_DRAFT }]);
    setNote('');
    setError(null);
    setEditing(true);
  };
  const update = (i: number, patch: Partial<PeriodDraft>) =>
    setDrafts((ds) => ds.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  const remove = (i: number) => setDrafts((ds) => ds.filter((_, j) => j !== i));
  const add = () => setDrafts((ds) => [...ds, { ...EMPTY_DRAFT }]);

  const save = async () => {
    setError(null);
    if (drafts.length === 0) {
      if (periods.length === 0) { setEditing(false); return; }
      // Clearing is a revert to source — nothing to justify.
      setSaving(true);
      try { await onSave(null, ''); setEditing(false); } finally { setSaving(false); }
      return;
    }
    const parsed = draftsToPeriods(drafts);
    if (parsed.error) { setError(parsed.error); return; }
    if (noteRequired && !note.trim()) { setError(NOTE_REQUIRED_MESSAGE); return; }
    setSaving(true);
    try {
      await onSave(parsed.periods, note.trim());
      setEditing(false);
    } finally {
      setSaving(false);
    }
  };

  const yearLines: ReactNode = useMemo(() => {
    if (!byYear || byYear.length === 0) return null;
    return (
      <div data-testid="renovation-schedule-by-year" style={{ fontSize: 10.5, color: palette.textSecondary, lineHeight: 1.5 }}>
        {byYear.map((y) => (
          <div key={y.year}>
            Year {y.year}: {y.months_in_period} of 12 months in a period · occupancy ×{y.occupancy_factor.toFixed(3)} · ADR ×{y.adr_factor.toFixed(3)}
          </div>
        ))}
      </div>
    );
  }, [byYear]);

  return (
    <div
      data-testid="renovation-displacement-schedule"
      data-schedule-state={periods.length ? 'on' : 'off'}
      style={{ marginTop: 10, border: `1px solid ${palette.border}`, borderRadius: 7, padding: '8px 11px', display: 'flex', flexDirection: 'column', gap: 6 }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <div>
          <span style={{ fontSize: 11.5, fontWeight: 600, color: palette.ink }}>Renovation displacement schedule</span>
          <span style={{ fontSize: 10.5, color: palette.textMuted, marginLeft: 6 }}>
            months after close · rooms offline · rate discount on open rooms
          </span>
        </div>
        {editable && !editing && (
          <button
            type="button"
            onClick={begin}
            data-testid="renovation-schedule-edit"
            style={{ fontSize: 11, fontWeight: 500, color: palette.hoverInk, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          >
            {periods.length ? 'Edit schedule' : 'Add a renovation period'}
          </button>
        )}
      </div>

      {!editing && periods.length === 0 && (
        <div data-testid="renovation-schedule-off" style={{ fontSize: 11, color: palette.textMuted, lineHeight: 1.5 }}>
          Off — no renovation period entered. The budget above is a capital cost only; enter the months rooms are offline and any rate discount to model an operating impact.
        </div>
      )}

      {!editing && periods.length > 0 && (
        <table style={{ borderCollapse: 'collapse', width: '100%' }}>
          <thead>
            <tr>
              <th style={head}>#</th><th style={head}>Months</th><th style={head}>Rooms offline</th><th style={head}>Rate discount</th><th style={head}>Note</th>
            </tr>
          </thead>
          <tbody>
            {periods.map((p, i) => (
              <tr key={i} data-testid="renovation-schedule-row">
                <td style={cell}>{i + 1}</td>
                <td style={cell}>{p.start_month === p.end_month ? `${p.start_month}` : `${p.start_month}–${p.end_month}`}</td>
                <td style={cell}>{pct(p.rooms_offline_pct)}</td>
                <td style={cell}>{pct(p.rate_discount_pct)}</td>
                <td style={{ ...cell, color: palette.textMuted }}>{p.note ?? savedNote ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {editing && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <table style={{ borderCollapse: 'collapse', width: '100%' }}>
            <thead>
              <tr>
                <th style={head}>#</th><th style={head}>First month</th><th style={head}>Last month</th><th style={head}>Rooms offline %</th><th style={head}>Rate discount %</th><th style={head}>Period note</th><th style={head} />
              </tr>
            </thead>
            <tbody>
              {drafts.map((d, i) => (
                <tr key={i} data-testid="renovation-schedule-draft-row">
                  <td style={cell}>{i + 1}</td>
                  <td style={cell}>
                    <input aria-label={`Period ${i + 1} first month`} type="number" min={1} max={MAX_MONTH} step={1} value={d.start_month}
                      onChange={(e) => update(i, { start_month: e.target.value })} style={inputStyle} />
                  </td>
                  <td style={cell}>
                    <input aria-label={`Period ${i + 1} last month`} type="number" min={1} max={MAX_MONTH} step={1} value={d.end_month}
                      onChange={(e) => update(i, { end_month: e.target.value })} style={inputStyle} />
                  </td>
                  <td style={cell}>
                    <input aria-label={`Period ${i + 1} rooms offline %`} type="number" min={0} max={100} step={0.1} value={d.rooms_offline_pct}
                      onChange={(e) => update(i, { rooms_offline_pct: e.target.value })} style={inputStyle} />
                  </td>
                  <td style={cell}>
                    <input aria-label={`Period ${i + 1} rate discount %`} type="number" min={0} max={100} step={0.1} value={d.rate_discount_pct}
                      onChange={(e) => update(i, { rate_discount_pct: e.target.value })} style={inputStyle} />
                  </td>
                  <td style={cell}>
                    <input aria-label={`Period ${i + 1} note`} type="text" value={d.note} placeholder="e.g. tower A guestrooms"
                      onChange={(e) => update(i, { note: e.target.value })} style={{ ...inputStyle, width: 150 }} />
                  </td>
                  <td style={cell}>
                    <button type="button" aria-label={`Remove period ${i + 1}`} onClick={() => remove(i)}
                      style={{ background: 'none', border: 'none', cursor: 'pointer', color: palette.textMuted, padding: 2 }}>
                      <X size={12} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div>
            <button type="button" onClick={add} data-testid="renovation-schedule-add"
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 11, color: palette.hoverInk, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}>
              <Plus size={12} /> Add period
            </button>
          </div>
          <textarea
            aria-label="Schedule justification"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder={NOTE_PLACEHOLDER}
            rows={2}
            style={{ fontSize: 11.5, padding: '4px 6px', border: `1px solid ${palette.border}`, borderRadius: 5, resize: 'vertical' }}
          />
          {error && (
            <div role="alert" data-testid="renovation-schedule-error" style={{ fontSize: 11, color: '#b42318' }}>{error}</div>
          )}
          <div style={{ display: 'flex', gap: 8 }}>
            <button type="button" aria-label="Save schedule" onClick={() => void save()} disabled={saving}
              style={{ fontSize: 11.5, fontWeight: 600, padding: '4px 10px', borderRadius: 6, border: `1px solid ${palette.border}`, background: 'white', cursor: 'pointer' }}>
              {drafts.length === 0 && periods.length > 0 ? 'Clear schedule' : 'Save schedule'}
            </button>
            <button type="button" aria-label="Cancel schedule edit" onClick={() => setEditing(false)} disabled={saving}
              style={{ fontSize: 11.5, padding: '4px 10px', borderRadius: 6, border: 'none', background: 'none', color: palette.textMuted, cursor: 'pointer' }}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {!editing && yearLines}
      {!editing && periods.length > 0 && (
        <div style={{ fontSize: 10.5, color: palette.textMuted, lineHeight: 1.5 }}>
          The schedule is the only renovation impact applied — the Y1 displacement percentages above are not applied on top.
        </div>
      )}
    </div>
  );
}
