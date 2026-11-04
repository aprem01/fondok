'use client';

/**
 * FON-116 / FON-117 — the topline on screen (plan §4.2, §6, PR 5–7).
 *
 * The Future P&L runs every deal on ONE method: the benchmark the worker
 * resolved (competitive set, else submarket, else market) × the stabilized
 * penetration × the ramp, grown by the market forecast year by year. These
 * components print that resolution and edit its inputs:
 *
 *   • ToplineBasisLine     — "Benchmark: … · Growth: … · Stabilized Year 3 · Ramp 85/95/100"
 *   • ToplineGrowthBanner  — "No market forecast — set growth manually" (+ the
 *                            close-date entry when the forecast cannot be placed,
 *                            ranked above the worker's labelled 4.5% suggestion)
 *   • ToplineNotices       — re-extract, calendar anchor, legacy keys, run predates v2
 *   • ToplineCard          — benchmark level, penetration, ramp (+ the stabilized year)
 *   • GrowthByYearTable    — occupancy / ADR growth INTO each year, editable
 *   • PenetrationByYearGrid — benchmark × penetration × ramp per year (Index Analysis)
 *   • RevenueBasisEntry    — no Year-1 basis: the worker's sentence + the entry
 *
 * Every number and label is the worker's (`topline_status`, the revenue
 * engine's `topline_by_year`). Every edit goes through ONE callback,
 * `onSave(patch, note)`, which the host routes to the deal's
 * `field_overrides` PATCH and a re-run; each number-moving key carries the
 * analyst's note (FON-74, review I-6). Nothing here assumes a default.
 */

import { useEffect, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { InlineEditControls } from '@/components/design';
import type { ToplineStatus } from '@/lib/api';
import { calendarMixText } from '@/lib/dealYears';
import { NOTE_REQUIRED_MESSAGE, requiresNote } from '@/lib/overrideNote';
import { TAB_LABEL } from '@/lib/projectTabs';
import {
  BENCHMARK_LEVEL_OPTIONS,
  TOPLINE_KEYS,
  calendarAnchorNote,
  growthBanner,
  growthPercentList,
  isGrowthOnly,
  legacyKeysToClear,
  overrideList,
  parseGrowthPercentList,
  parseRampPercentList,
  rampModeLabel,
  rampSummary,
  reextractNotice,
  toplineBasisClauses,
  toplineYearChain,
  type ToplineYearRecord,
} from '@/lib/topline';
import CloseDateField from '../CloseDateField';
import { BasisChip } from './IndexMethodology';

/** Persist a `field_overrides` patch with the analyst's note and re-run; resolves whether it landed. */
export type ToplineSave = (patch: Record<string, unknown>, note: string) => Promise<boolean>;

const pct1 = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? '—' : `${(v * 100).toFixed(1)}%`;
const signedPct = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`;
/** FON-121 review M1 — a growth figure at the precision the model applies
 *  (a blend of two calendar years is +3.05%, not +3.1%): up to 3 decimals,
 *  trailing zeros dropped, at least one. */
export const appliedPct = (v: number | null | undefined): string => {
  if (v == null || !Number.isFinite(v)) return '—';
  const t = Math.abs(v * 100).toFixed(3).replace(/0+$/, '').replace(/\.$/, '.0');
  return `${v < 0 && Number(t) !== 0 ? '−' : '+'}${t}%`;
};
const usd0 = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? '—' : `$${Math.round(v).toLocaleString('en-US')}`;
const pts = (v: number | null | undefined): string =>
  v == null || !Number.isFinite(v) ? '—' : (Math.round(v * 10) / 10).toString();

const SOURCE_SHORT: Record<string, string> = {
  analyst_override: 'Your override',
  cbre_horizons: 'CBRE',
  costar_submarket_forecast: 'CoStar',
  carried_forward: 'Past the forecast',
  held_at_stabilization: 'Held after stabilization',
  withheld_by_policy: 'Withheld',
  unavailable: 'No figure',
};

// ─────────────────────────── the note gate ───────────────────────────

/**
 * The justification row + Save · Cancel for an edit that is already staged.
 * Save is refused (with the shared message) until a note is typed when any
 * key of the patch needs one.
 */
function NoteGate({
  label, keys, onSave, onCancel, saving, testId, prefill = '',
}: {
  label: string;
  keys: string[];
  onSave: (note: string) => void;
  onCancel: () => void;
  saving?: boolean;
  testId: string;
  prefill?: string;
}) {
  const [note, setNote] = useState(prefill);
  const [error, setError] = useState<string | null>(null);
  const needs = keys.some((k) => requiresNote(k));
  return (
    <div className="mt-1.5 space-y-1">
      <InlineEditControls
        onSave={() => {
          if (needs && !note.trim()) { setError(NOTE_REQUIRED_MESSAGE); return; }
          onSave(note.trim());
        }}
        onCancel={onCancel}
        saving={saving}
        note={note}
        onNote={(v) => { setNote(v); setError(null); }}
        noteTestId={`${testId}-note`}
        noteLabel={`${label} — override justification`}
        saveTestId={`${testId}-save`}
        cancelTestId={`${testId}-cancel`}
      />
      {error && <div className="text-[10.5px] text-danger-700" data-testid={`${testId}-error`}>{error}</div>}
    </div>
  );
}

// ─────────────────────────── basis line ───────────────────────────

export function ToplineBasisLine({ status }: { status: ToplineStatus | null }) {
  const clauses = toplineBasisClauses(status);
  if (!clauses.length) return null;
  return (
    <p className="text-[11px] leading-snug text-ink-600 flex flex-wrap gap-x-2 gap-y-0.5" data-testid="topline-basis-line">
      <span className="font-semibold text-ink-700">Topline basis</span>
      {clauses.map((c, i) => (
        <span
          key={c.id}
          data-testid={`topline-clause-${c.id}`}
          data-tone={c.tone}
          title={c.title}
          className={c.tone === 'warn' ? 'text-warning-700' : undefined}
        >
          {i > 0 && <span className="text-ink-300 mr-2" aria-hidden="true">·</span>}
          {c.text}
        </span>
      ))}
    </p>
  );
}

// ─────────────────────────── growth banner ───────────────────────────

export function ToplineGrowthBanner({
  status, closeDateIso, closeDateEditable, onSaveCloseDate, onSave, running, dealId,
}: {
  status: ToplineStatus | null;
  /** For the banner's re-extract link (Data Room). */
  dealId?: string;
  closeDateIso: string | null;
  closeDateEditable: boolean;
  /** The ONE close-date save path (`CloseDateField` → `closeDatePatch`). */
  onSaveCloseDate: (iso: string, note: string) => void | Promise<void>;
  onSave: ToplineSave;
  running: boolean;
}) {
  const banner = growthBanner(status);
  const [applying, setApplying] = useState(false);
  if (!banner) return null;
  const s = banner.suggestion;
  return (
    <div
      role="status"
      data-testid="topline-growth-unavailable"
      data-state={banner.state}
      className="rounded-md border border-warning-500/40 bg-warning-50 px-3 py-2 text-[11.5px] text-ink-700 space-y-1.5"
    >
      <div className="font-semibold text-warning-700">{banner.title}</div>
      <div className="text-ink-600">{banner.detail}</div>
      {banner.state === 'reextract' && dealId && (
        <Link
          href={`/projects/${dealId}`}
          className="text-brand-700 font-semibold underline decoration-dotted"
          data-testid="topline-banner-reextract-action"
        >
          Re-extract in the {TAB_LABEL.dataRoom} →
        </Link>
      )}
      {banner.askCloseDate && (
        <div className="flex items-center gap-2" data-testid="topline-close-date-entry">
          <span className="text-ink-600">Acquisition close date:</span>
          <CloseDateField
            iso={closeDateIso}
            editable={closeDateEditable}
            onSave={onSaveCloseDate}
            emptyLabel="Set the close date"
            testId="topline-close-date"
          />
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <a href="#topline-growth-by-year" className="text-brand-700 underline decoration-dotted">
          Set growth by year ↓
        </a>
        {s && !applying && (
          <button
            type="button"
            data-testid="topline-suggestion-apply"
            disabled={running}
            onClick={() => setApplying(true)}
            className="text-brand-700 font-semibold underline decoration-dotted disabled:opacity-50"
            title="A labelled platform suggestion, not a market forecast. Applying it writes your override with the note below."
          >
            {s.label}
          </button>
        )}
      </div>
      {s && applying && (
        <NoteGate
          label="Platform suggestion"
          keys={[s.key]}
          prefill={s.note_prefill}
          testId="topline-suggestion"
          saving={running}
          onCancel={() => setApplying(false)}
          onSave={(note) => {
            void onSave({ [s.key]: s.value }, note).then((ok) => { if (ok) setApplying(false); });
          }}
        />
      )}
    </div>
  );
}

// ─────────────────────────── notices ───────────────────────────

export function ToplineNotices({
  status, dealId, migrationNotice, onClearLegacy, onRerun, running,
}: {
  status: ToplineStatus | null;
  dealId: string;
  /** `toplineMigrationNotice(...)` for the run on screen, or null. */
  migrationNotice: string | null;
  onClearLegacy: (keys: string[]) => void;
  onRerun: () => void;
  running: boolean;
}) {
  // Final review M-1 — when the growth banner already headlines the
  // re-extract (with its Data Room link), the notice is not repeated.
  const reextract = growthBanner(status)?.state === 'reextract' ? null : reextractNotice(status);
  const anchor = calendarAnchorNote(status);
  const legacy = status?.legacy_flags ?? [];
  const clearable = legacyKeysToClear(status);
  const problems = [...(status?.problems ?? [])];
  const notes = (status?.notes ?? []).filter((n) => n !== status?.calendar_anchor?.label);
  if (!reextract && !anchor && !legacy.length && !problems.length && !notes.length && !migrationNotice) return null;
  return (
    <div className="space-y-1.5 text-[11px] leading-snug">
      {migrationNotice && (
        <div
          data-testid="topline-migration-notice"
          className="rounded-md border border-brand-500/30 bg-brand-50 px-3 py-1.5 text-ink-700 flex flex-wrap items-center gap-2"
        >
          <span className="font-semibold">{migrationNotice}</span>
          <button
            type="button"
            onClick={onRerun}
            disabled={running}
            className="text-brand-700 font-semibold underline decoration-dotted disabled:opacity-50"
          >
            Re-run the model
          </button>
          <Link href={`/projects/${dealId}?tab=activity`} className="text-brand-700 underline decoration-dotted">
            Compare runs ({TAB_LABEL.activity}) →
          </Link>
        </div>
      )}
      {reextract && (
        <div data-testid="topline-reextract" className="rounded-md border border-warning-500/40 bg-warning-50 px-3 py-1.5">
          <div className="font-semibold text-warning-700">{reextract.title}</div>
          {reextract.lines.map((l) => (
            <div key={l} className="text-ink-600">{l}</div>
          ))}
          <Link
            href={`/projects/${dealId}`}
            className="text-brand-700 font-semibold underline decoration-dotted"
            data-testid="topline-reextract-action"
          >
            Re-extract in the {TAB_LABEL.dataRoom} →
          </Link>
        </div>
      )}
      {anchor && (
        <div data-testid="topline-calendar-anchor" data-source={anchor.source} className="text-ink-500">
          {anchor.text}
          {anchor.askCloseDate && (
            <>
              {' — '}
              <Link href={`/projects/${dealId}?tab=investment`} className="text-brand-700 underline decoration-dotted">
                set the acquisition close date ({TAB_LABEL.investment}) →
              </Link>
            </>
          )}
        </div>
      )}
      {legacy.length > 0 && (
        <div data-testid="topline-legacy-flags" className="rounded-md border border-border bg-bg/40 px-3 py-1.5 text-ink-600">
          <div className="font-semibold text-ink-700">Settings from retired controls — read under the current method</div>
          <ul className="list-disc pl-4">
            {legacy.map((f) => (
              <li key={`${f.key}-${f.treatment}`}>{f.detail}</li>
            ))}
          </ul>
          {clearable.length > 0 && (
            <button
              type="button"
              data-testid="topline-clear-legacy"
              disabled={running}
              onClick={() => onClearLegacy(clearable)}
              className="mt-1 text-brand-700 font-semibold underline decoration-dotted disabled:opacity-50"
            >
              Clear legacy keys
            </button>
          )}
        </div>
      )}
      {problems.length > 0 && (
        <div data-testid="topline-problems" className="text-warning-700">
          {problems.map((p) => <div key={p}>{p}</div>)}
        </div>
      )}
      {notes.length > 0 && (
        <div data-testid="topline-notes" className="text-ink-500">
          {notes.map((n) => <div key={n}>{n}</div>)}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────── the Topline card ───────────────────────────

function readLevel(overrides: Record<string, unknown>): string {
  const raw = overrides[TOPLINE_KEYS.level];
  const v = raw != null && typeof raw === 'object' && 'value' in (raw as object) ? (raw as { value?: unknown }).value : raw;
  return typeof v === 'string' && BENCHMARK_LEVEL_OPTIONS.some((o) => o.value === v) ? v : 'auto';
}

function LevelField({ status, overrides, onSave, running }: { status: ToplineStatus; overrides: Record<string, unknown>; onSave: ToplineSave; running: boolean }) {
  const saved = readLevel(overrides);
  const [draft, setDraft] = useState(saved);
  useEffect(() => setDraft(saved), [saved]);
  return (
    <div className="space-y-0.5" data-testid="topline-level">
      <div className="flex items-center justify-between gap-2">
        <label htmlFor="topline-level-select" className="text-[12px] text-ink-500">Benchmark level</label>
        <select
          id="topline-level-select"
          aria-label="Benchmark level"
          value={draft}
          disabled={running}
          onChange={(e) => setDraft(e.target.value)}
          className="text-[12px] border border-border rounded px-1.5 py-1 max-w-[230px]"
        >
          {BENCHMARK_LEVEL_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
      </div>
      <div className="text-[11px] text-ink-600" data-testid="topline-level-in-use">
        In use: {status.benchmark_level === 'none' ? 'no benchmark' : status.benchmark_label}
      </div>
      {status.benchmark_fallback_reason && (
        <div className="text-[11px] text-warning-700" data-testid="topline-level-fallback">{status.benchmark_fallback_reason}</div>
      )}
      {status.bucket_note && <div className="text-[11px] text-ink-500">{status.bucket_note}</div>}
      {draft !== saved && (
        <NoteGate
          label="Benchmark level"
          keys={[TOPLINE_KEYS.level]}
          testId="topline-level"
          saving={running}
          onCancel={() => setDraft(saved)}
          onSave={(note) => void onSave({ [TOPLINE_KEYS.level]: draft }, note)}
        />
      )}
    </div>
  );
}

function PenetrationField({
  label, metric, status, overrides, onSave, running,
}: {
  label: string;
  metric: 'occ' | 'adr';
  status: ToplineStatus;
  overrides: Record<string, unknown>;
  onSave: ToplineSave;
  running: boolean;
}) {
  const key = metric === 'occ' ? TOPLINE_KEYS.mpiTarget : TOPLINE_KEYS.ariTarget;
  const value = metric === 'occ' ? status.penetration_occ : status.penetration_adr;
  const source = metric === 'occ' ? status.penetration_occ_source : status.penetration_adr_source;
  const current = metric === 'occ' ? status.current_mpi : status.current_ari;
  const shown = value == null ? '' : String(Math.round(value * 10) / 10);
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const parsed = Number(draft);
  const dirty = draft.trim() !== '' && Number.isFinite(parsed) && Math.abs(parsed - Number(shown)) > 1e-9;
  const invalid = draft.trim() !== '' && (!Number.isFinite(parsed) || parsed < 25 || parsed > 250);
  return (
    <div className="space-y-0.5" data-testid={`topline-penetration-${metric}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[12px] text-ink-500">{label}</span>
        <span className="inline-flex items-center gap-1.5">
          <input
            type="number"
            aria-label={label}
            value={draft}
            disabled={running}
            onChange={(e) => setDraft(e.target.value)}
            className="w-[64px] text-right text-[13px] font-semibold border border-border rounded px-1.5 py-1 tabular-nums"
          />
          <span
            className="text-[10px] font-semibold uppercase tracking-wide text-ink-500"
            data-testid={`topline-penetration-${metric}-source`}
          >
            {source === 'analyst_override' ? 'Your override' : source === 'hold_index' ? 'Holds today’s index' : 'Default'}
          </span>
        </span>
      </div>
      <div className="text-[11px] text-ink-500" data-testid={`topline-current-${metric}`}>{current?.label ?? '—'}</div>
      {source === 'analyst_override' && !dirty && (
        <button
          type="button"
          disabled={running}
          className="text-[10.5px] font-semibold text-brand-700"
          onClick={() => void onSave({ [key]: null }, '')}
        >
          Reset to the default
        </button>
      )}
      {invalid && <div className="text-[10.5px] text-danger-700">Index points between 25 and 250 (100 = parity with the benchmark).</div>}
      {dirty && !invalid && (
        <NoteGate
          label={label}
          keys={[key]}
          testId={`topline-penetration-${metric}`}
          saving={running}
          onCancel={() => setDraft(shown)}
          onSave={(note) => void onSave({ [key]: parsed }, note)}
        />
      )}
    </div>
  );
}

function RampField({ status, overrides, onSave, running }: { status: ToplineStatus; overrides: Record<string, unknown>; onSave: ToplineSave; running: boolean }) {
  const S = status.stabilization_year;
  const years = Math.max(0, S - 1);
  const summary = rampSummary(status);
  const applied = growthPercentList(status.ramp_by_year, years);
  const [cells, setCells] = useState<string[]>(applied);
  const appliedKey = applied.join('|');
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => setCells(applied), [appliedKey]);
  const dirty = cells.join('|') !== appliedKey;
  const parsed = parseRampPercentList(cells);
  const hasOverride = overrideList(overrides, TOPLINE_KEYS.rampByYear) != null;
  return (
    <div className="space-y-1" data-testid="topline-ramp">
      <div className="text-[12px] text-ink-500">Ramp to stabilization</div>
      {rampModeLabel(status) && (
        <div className="text-[11px] font-medium text-ink-700" data-testid="topline-ramp-mode">
          {rampModeLabel(status)}
        </div>
      )}
      <div
        className={summary.tone === 'warn' ? 'text-[11px] text-warning-700' : 'text-[11px] text-ink-600'}
        data-testid="topline-ramp-summary"
      >
        {summary.text}
      </div>
      {status.ramp_flag && (
        <div className="text-[11px] text-warning-700" data-testid="topline-ramp-flag">{status.ramp_flag}</div>
      )}
      {years > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          {cells.map((c, i) => (
            <label key={i} className="inline-flex items-center gap-1 text-[11px] text-ink-500">
              Y{i + 1}
              <input
                type="number"
                aria-label={`Ramp Year ${i + 1} (%)`}
                value={c}
                placeholder="100"
                disabled={running}
                onChange={(e) => setCells(cells.map((x, j) => (j === i ? e.target.value : x)))}
                className="w-[52px] text-right text-[12px] border border-border rounded px-1 py-0.5 tabular-nums"
              />
              %
            </label>
          ))}
          <span className="text-[11px] text-ink-500">Y{S}+ 100%</span>
        </div>
      )}
      {hasOverride && !dirty && (
        <button
          type="button"
          disabled={running}
          className="text-[10.5px] font-semibold text-brand-700"
          onClick={() => void onSave({ [TOPLINE_KEYS.rampByYear]: null }, '')}
        >
          Reset to the default ramp
        </button>
      )}
      {dirty && !parsed.ok && <div className="text-[10.5px] text-danger-700">{parsed.error}</div>}
      {dirty && parsed.ok && (
        <NoteGate
          label="Ramp"
          keys={[TOPLINE_KEYS.rampByYear]}
          testId="topline-ramp"
          saving={running}
          onCancel={() => setCells(applied)}
          onSave={(note) => void onSave({ [TOPLINE_KEYS.rampByYear]: parsed.value }, note)}
        />
      )}
    </div>
  );
}

/** A new hotel with no opening date: ask for it, as the close date is asked for. */
export function OpeningDatePrompt({
  status, editable, onSave,
}: {
  status: ToplineStatus | null;
  editable: boolean;
  onSave: (iso: string, note: string) => void | Promise<void>;
}) {
  if (!status?.opening_date_missing) return null;
  return (
    <div
      role="status"
      data-testid="topline-opening-date-entry"
      className="rounded-md border border-warning-500/40 bg-warning-50 px-3 py-2 text-[11.5px] text-ink-700 flex flex-wrap items-center gap-2"
    >
      <span className="text-ink-600">{status.ramp_flag ?? 'Enter the opening date.'}</span>
      <CloseDateField
        iso={null}
        editable={editable}
        onSave={onSave}
        emptyLabel="Set the opening date"
        ariaLabel="Opening date"
        hint="The 85/95/100 ramp and the stabilized year count from this date"
        testId="topline-opening-date"
      />
    </div>
  );
}

/** The Topline card (plan §4.2): benchmark level, penetration at stabilization, ramp, stabilized year. */
export function ToplineCard({
  status, overrides, onSave, running, stabilizationSlot, className,
}: {
  status: ToplineStatus | null;
  overrides: Record<string, unknown>;
  onSave: ToplineSave;
  running: boolean;
  /** The Stabilization Year editor (Future P&L only — it reads the expense block). */
  stabilizationSlot?: ReactNode;
  className?: string;
}) {
  if (!status) return null;
  if (isGrowthOnly(status)) {
    // FON-116 staged rollout — only the growth half is switched on: the
    // stabilized year (occupancy is flat after it) is the one topline input.
    return (
      <div className={className} data-testid="topline-card" data-mode="growth_only">
        <div className="text-[11px] font-bold text-ink-900 mb-2.5">Topline</div>
        <div className="flex flex-col gap-2.5">
          {stabilizationSlot}
          <div className="text-[11px] text-ink-500" data-testid="topline-growth-only-note">
            Year 1 is the subject&apos;s own rates (T-12 or your values), grown by the market growth by year;
            occupancy is flat after the stabilized year, ADR keeps growing. Benchmark, penetration and ramp are not
            switched on in this workspace.
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className={className} data-testid="topline-card">
      <div className="text-[11px] font-bold text-ink-900 mb-2.5">Topline</div>
      <div className="flex flex-col gap-2.5">
        <LevelField status={status} overrides={overrides} onSave={onSave} running={running} />
        {stabilizationSlot}
        <PenetrationField label="Occupancy penetration (MPI)" metric="occ" status={status} overrides={overrides} onSave={onSave} running={running} />
        <PenetrationField label="ADR penetration (ARI)" metric="adr" status={status} overrides={overrides} onSave={onSave} running={running} />
        <RampField status={status} overrides={overrides} onSave={onSave} running={running} />
        {status.explicit_metrics.length > 0 && (
          <div className="text-[11px] text-ink-500" data-testid="topline-explicit">
            Your Year-1 {status.explicit_metrics.map((m) => (m === 'adr' ? 'ADR' : 'occupancy')).join(' and ')} is used as
            typed — the benchmark, penetration and ramp do not set {status.explicit_metrics.length > 1 ? 'them' : 'it'}.
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────── growth by year ───────────────────────────

export function GrowthByYearTable({
  status, overrides, onSave, running,
}: {
  status: ToplineStatus | null;
  overrides: Record<string, unknown>;
  onSave: ToplineSave;
  running: boolean;
}) {
  const rows = status?.growth_by_year ?? [];
  const n = rows.length;
  const occSaved = growthPercentList(overrideList(overrides, TOPLINE_KEYS.occGrowthByYear), n);
  const adrSaved = growthPercentList(overrideList(overrides, TOPLINE_KEYS.adrGrowthByYear), n);
  const [editing, setEditing] = useState(false);
  const [occ, setOcc] = useState<string[]>(occSaved);
  const [adr, setAdr] = useState<string[]>(adrSaved);
  if (!status || !n) return null;
  const start = () => { setOcc(occSaved); setAdr(adrSaved); setEditing(true); };
  const po = parseGrowthPercentList(occ);
  const pa = parseGrowthPercentList(adr);
  const error = !po.ok ? po.error : !pa.ok ? pa.error : null;
  const cell = (g: ToplineStatus['growth_by_year'][number]['occupancy'], applied: boolean) => (
    <span title={g.label} className={g.value == null ? 'text-ink-400' : undefined}>
      {applied ? appliedPct(g.value) : 'n/a'}
      <span className="ml-1 text-[9.5px] uppercase tracking-wide text-ink-400">{applied ? SOURCE_SHORT[g.source] ?? g.source : 'as reported'}</span>
      {/* Decision 3 (2026-11-03) — the report's page is visible, not only in the tooltip. */}
      {applied && g.chip && (
        <span className="ml-1 text-[9.5px] text-ink-500" data-testid="topline-growth-chip">{g.chip}</span>
      )}
    </span>
  );
  return (
    <div id="topline-growth-by-year" data-testid="topline-growth-by-year" className="space-y-1">
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-ink-500">Market growth by year (into each year)</span>
        {!editing && (
          <button type="button" disabled={running} onClick={start} className="text-[10.5px] font-semibold text-brand-700" data-testid="topline-growth-edit">
            Edit by year
          </button>
        )}
      </div>
      <table className="w-full text-[11.5px]">
        <thead>
          <tr className="text-ink-500 text-[10px] border-b border-border">
            <th className="text-left font-medium py-0.5">Year</th>
            <th className="text-right font-medium py-0.5">Occupancy</th>
            <th className="text-right font-medium py-0.5">ADR</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.year} className="border-b border-border/40" data-testid={`topline-growth-row-${r.year}`}>
              <td className="py-0.5 text-ink-600" title={calendarMixText(r.deal_year) ?? undefined}>
                Year {r.year}{r.deal_year?.label ? ` · ${r.deal_year.label}` : ''}
              </td>
              <td className="py-0.5 text-right tabular-nums">
                {editing ? (
                  <input
                    aria-label={`Occupancy growth Year ${r.year} (%)`}
                    value={occ[i] ?? ''}
                    placeholder="—"
                    onChange={(e) => setOcc(occ.map((x, j) => (j === i ? e.target.value : x)))}
                    className="w-[56px] text-right border border-border rounded px-1"
                  />
                ) : cell(r.occupancy, r.applied)}
              </td>
              <td className="py-0.5 text-right tabular-nums">
                {editing ? (
                  <input
                    aria-label={`ADR growth Year ${r.year} (%)`}
                    value={adr[i] ?? ''}
                    placeholder="—"
                    onChange={(e) => setAdr(adr.map((x, j) => (j === i ? e.target.value : x)))}
                    className="w-[56px] text-right border border-border rounded px-1"
                  />
                ) : cell(r.adr, r.applied)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {editing && (
        <>
          <div className="text-[10.5px] text-ink-500">
            Percent per year; blank = the market forecast (or held flat where none exists).
          </div>
          {error && <div className="text-[10.5px] text-danger-700" data-testid="topline-growth-error">{error}</div>}
          {!error && (
            <NoteGate
              label="Growth by year"
              keys={[TOPLINE_KEYS.occGrowthByYear, TOPLINE_KEYS.adrGrowthByYear]}
              testId="topline-growth"
              saving={running}
              onCancel={() => setEditing(false)}
              onSave={(note) => {
                if (!po.ok || !pa.ok) return;
                void onSave({ [TOPLINE_KEYS.occGrowthByYear]: po.value, [TOPLINE_KEYS.adrGrowthByYear]: pa.value }, note)
                  .then((ok) => { if (ok) setEditing(false); });
              }}
            />
          )}
        </>
      )}
      <div className="text-[10.5px] text-ink-500" data-testid="topline-terminal-growth">
        Exit-year growth: {appliedPct(status.terminal_revpar_growth?.value)}
        {status.terminal_revpar_growth?.chip ? ` · ${status.terminal_revpar_growth.chip}` : ''}
        {status.terminal_revpar_growth?.label ? ` — ${status.terminal_revpar_growth.label}` : ''}
      </div>
    </div>
  );
}

// ─────────────────────────── per-year penetration (Index Analysis) ───────────────────────────

/** Index Analysis grid — benchmark × penetration × ramp by year (benchmark topline only). */
export function PenetrationByYearGrid({
  status, toplineByYear, overrides, onSave, running,
}: {
  status: ToplineStatus | null;
  toplineByYear: ToplineYearRecord[];
  overrides: Record<string, unknown>;
  onSave: ToplineSave;
  running: boolean;
}) {
  const bench = status?.benchmark_by_year ?? [];
  const n = Math.max(bench.length, toplineByYear.length);
  const mpiSaved = (overrideList(overrides, TOPLINE_KEYS.mpiByYear) ?? []).map((v) => (v == null ? '' : String(v)));
  const ariSaved = (overrideList(overrides, TOPLINE_KEYS.ariByYear) ?? []).map((v) => (v == null ? '' : String(v)));
  const pad = (a: string[]) => Array.from({ length: n }, (_, i) => a[i] ?? '');
  const [editing, setEditing] = useState(false);
  const [mpi, setMpi] = useState<string[]>([]);
  const [ari, setAri] = useState<string[]>([]);
  if (!status || n === 0) return null;
  const parse = (cells: string[]): { ok: true; value: (number | null)[] | null } | { ok: false; error: string } => {
    const out: (number | null)[] = [];
    for (const c of cells) {
      if (!c.trim()) { out.push(null); continue; }
      const v = Number(c);
      if (!Number.isFinite(v) || v < 25 || v > 250) return { ok: false, error: `${c} is not an index between 25 and 250.` };
      out.push(v);
    }
    return { ok: true, value: out.some((v) => v != null) ? out : null };
  };
  const pm = parse(mpi);
  const pa = parse(ari);
  const error = !pm.ok ? pm.error : !pa.ok ? pa.error : null;
  return (
    <div className="space-y-1.5" data-testid="topline-penetration-grid">
      <div className="flex items-center justify-between">
        <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-500">
          Benchmark × penetration × ramp, by year
        </div>
        {!editing && (
          <button
            type="button"
            disabled={running}
            onClick={() => { setMpi(pad(mpiSaved)); setAri(pad(ariSaved)); setEditing(true); }}
            className="text-[11px] font-semibold text-brand-700"
            data-testid="topline-penetration-grid-edit"
          >
            Set penetration by year
          </button>
        )}
      </div>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="text-ink-500 text-[10.5px] border-b border-border">
            <th className="text-left font-medium py-1">Year</th>
            <th className="text-right font-medium py-1">Benchmark occ.</th>
            <th className="text-right font-medium py-1">Benchmark ADR</th>
            <th className="text-right font-medium py-1" title="Penetration at stabilization, or your value for that year">MPI</th>
            <th className="text-right font-medium py-1" title="Penetration at stabilization, or your value for that year">ARI</th>
            <th className="text-right font-medium py-1">Ramp</th>
            <th className="text-right font-medium py-1">Before displacement</th>
            <th className="text-right font-medium py-1">Displacement</th>
            <th className="text-right font-medium py-1">Occupancy</th>
            <th className="text-right font-medium py-1">ADR</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: n }, (_, i) => {
            const b = bench[i];
            const t = toplineByYear[i];
            const y = i + 1;
            // The engine's own chain (its mpi_target / ari_target already
            // include the ramp; its occupancy / adr are after displacement).
            const co = toplineYearChain(status, toplineByYear, i, 'occupancy');
            const ca = toplineYearChain(status, toplineByYear, i, 'adr');
            const penCell = (c: ReturnType<typeof toplineYearChain>, raw: number | null | undefined) =>
              c == null ? pts(raw) : c.byYear ? `${pts(c.effectivePenetration)} (yours, no ramp)` : pts(c.stabilizedPenetration ?? c.effectivePenetration);
            const rampOf = (c: ReturnType<typeof toplineYearChain>) => (c?.rampApplied ? c.ramp : null);
            const ro = rampOf(co);
            const ra = rampOf(ca);
            const rampCell =
              ro == null && ra == null
                ? '—'
                : ro != null && ra != null
                  ? pct1(ro)
                  : `${pct1(ro ?? ra)} (${ro != null ? 'occupancy' : 'ADR'} only)`;
            const disp = (c: ReturnType<typeof toplineYearChain>, what: string) =>
              c?.displaced && c.pre > 0 ? `${signedPct(c.final / c.pre - 1)} ${what}` : null;
            const dispCell = [disp(co, 'occ'), disp(ca, 'ADR')].filter(Boolean).join(' · ') || '—';
            const title = [co?.text, ca?.text].filter(Boolean).join('\n') || undefined;
            return (
              <tr key={i} className="border-b border-border/40 align-top" data-testid={`topline-penetration-row-${y}`} title={title}>
                <td className="py-1 text-ink-600">Year {y}{b?.deal_year?.label ? ` · ${b.deal_year.label}` : ''}</td>
                <td className="py-1 text-right tabular-nums" title={b?.label}>
                  {pct1(b?.occupancy)} {b?.basis && b.occupancy != null && <BasisChip basis={b.basis} label={b.label} />}
                </td>
                <td className="py-1 text-right tabular-nums" title={b?.label}>{usd0(b?.adr)}</td>
                <td className="py-1 text-right tabular-nums" data-testid={`topline-grid-mpi-${y}`}>
                  {editing ? (
                    <input aria-label={`MPI Year ${y}`} value={mpi[i] ?? ''} placeholder={penCell(co, t?.mpi_target)}
                      onChange={(e) => setMpi(mpi.map((x, j) => (j === i ? e.target.value : x)))}
                      className="w-[52px] text-right border border-border rounded px-1" />
                  ) : penCell(co, t?.mpi_target)}
                </td>
                <td className="py-1 text-right tabular-nums" data-testid={`topline-grid-ari-${y}`}>
                  {editing ? (
                    <input aria-label={`ARI Year ${y}`} value={ari[i] ?? ''} placeholder={penCell(ca, t?.ari_target)}
                      onChange={(e) => setAri(ari.map((x, j) => (j === i ? e.target.value : x)))}
                      className="w-[52px] text-right border border-border rounded px-1" />
                  ) : penCell(ca, t?.ari_target)}
                </td>
                <td className="py-1 text-right tabular-nums" data-testid={`topline-grid-ramp-${y}`}>{rampCell}</td>
                <td className="py-1 text-right tabular-nums" data-testid={`topline-grid-pre-${y}`}>
                  {co ? pct1(co.pre) : '—'}{co?.capped ? ' (cap)' : ''} · {ca ? usd0(ca.pre) : '—'}
                </td>
                <td className="py-1 text-right tabular-nums" data-testid={`topline-grid-disp-${y}`}>{dispCell}</td>
                <td className="py-1 text-right tabular-nums" data-testid={`topline-grid-final-occ-${y}`}>{pct1(t?.occupancy)}</td>
                <td className="py-1 text-right tabular-nums" data-testid={`topline-grid-final-adr-${y}`}>{usd0(t?.adr)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <div className="text-[10.5px] text-ink-500" data-testid="topline-penetration-grid-footer">
        Occupancy before displacement = benchmark × MPI ÷ 100 × ramp (capped at 95%); ADR before displacement =
        benchmark × ARI ÷ 100 × ramp. MPI / ARI are the penetration at stabilization; the ramp applies in the years before it (an operating hotel moves in a straight line from today&apos;s index, so its ramp column is blank). A penetration
        you set for a single year runs with no ramp. Renovation displacement (the Year-1 assumptions or the
        displacement schedule) then applies to give the occupancy and ADR the model ran on.
      </div>
      {editing && (
        <>
          {error && <div className="text-[10.5px] text-danger-700" data-testid="topline-penetration-grid-error">{error}</div>}
          {!error && (
            <NoteGate
              label="Penetration by year"
              keys={[TOPLINE_KEYS.mpiByYear, TOPLINE_KEYS.ariByYear]}
              testId="topline-penetration-grid"
              saving={running}
              onCancel={() => setEditing(false)}
              onSave={(note) => {
                if (!pm.ok || !pa.ok) return;
                void onSave({ [TOPLINE_KEYS.mpiByYear]: pm.value, [TOPLINE_KEYS.ariByYear]: pa.value }, note)
                  .then((ok) => { if (ok) setEditing(false); });
              }}
            />
          )}
        </>
      )}
    </div>
  );
}

// ─────────────────────────── no Year-1 basis ───────────────────────────

/**
 * Review I-5 — the deal has no Year-1 occupancy / ADR basis. Prints the
 * worker's sentence verbatim and takes the missing rates (the purchase-price
 * pattern: one editor, one save path through `onSave`, then the run).
 */
export function RevenueBasisEntry({
  message, missing, onSave, running, editable,
}: {
  message: string;
  missing: string[];
  onSave: ToplineSave;
  running: boolean;
  editable: boolean;
}) {
  const needOcc = missing.length === 0 || missing.includes('starting_occupancy');
  const needAdr = missing.length === 0 || missing.includes('starting_adr');
  const [occ, setOcc] = useState('');
  const [adr, setAdr] = useState('');
  const occN = Number(occ);
  const adrN = Number(adr.replace(/[$,\s]/g, ''));
  const occOk = !needOcc || (occ.trim() !== '' && Number.isFinite(occN) && occN > 0 && occN <= 100);
  const adrOk = !needAdr || (adr.trim() !== '' && Number.isFinite(adrN) && adrN > 0);
  const ready = (needOcc || needAdr) && occOk && adrOk;
  const keys = [needOcc ? 'starting_occupancy' : null, needAdr ? 'starting_adr' : null].filter(Boolean) as string[];
  return (
    <div data-testid="revenue-basis-refusal" className="rounded-md border border-warning-500/40 bg-warning-50 px-4 py-3 text-left space-y-2">
      <div className="text-[13px] font-semibold text-warning-700">No Year-1 occupancy / ADR — the revenue model did not run</div>
      <p className="text-[12px] text-ink-700 leading-relaxed" data-testid="revenue-basis-message">{message}</p>
      {editable && (
        <div className="flex flex-wrap items-end gap-3">
          {needOcc && (
            <label className="flex flex-col text-[11px] text-ink-500">
              Year-1 occupancy (%)
              <input
                type="number"
                aria-label="Year-1 occupancy (%)"
                value={occ}
                onChange={(e) => setOcc(e.target.value)}
                className="w-[90px] text-right text-[13px] border border-border rounded px-1.5 py-1"
              />
            </label>
          )}
          {needAdr && (
            <label className="flex flex-col text-[11px] text-ink-500">
              Year-1 ADR ($)
              <input
                type="number"
                aria-label="Year-1 ADR ($)"
                value={adr}
                onChange={(e) => setAdr(e.target.value)}
                className="w-[100px] text-right text-[13px] border border-border rounded px-1.5 py-1"
              />
            </label>
          )}
        </div>
      )}
      {editable && ready && (
        <NoteGate
          label="Year-1 occupancy and ADR"
          keys={keys}
          testId="revenue-basis"
          saving={running}
          onCancel={() => { setOcc(''); setAdr(''); }}
          onSave={(note) => {
            const patch: Record<string, unknown> = {};
            if (needOcc) patch.starting_occupancy = Math.round(occN * 1000) / 100000;
            if (needAdr) patch.starting_adr = adrN;
            void onSave(patch, note);
          }}
        />
      )}
      {editable && !ready && (occ || adr) && (
        <div className="text-[10.5px] text-ink-500">Occupancy between 0 and 100%; ADR above $0.</div>
      )}
    </div>
  );
}
