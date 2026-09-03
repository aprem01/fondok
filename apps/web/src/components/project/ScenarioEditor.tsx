'use client';
/**
 * ScenarioEditor — Wave 3 W3.2 side panel for creating / editing a
 * named scenario.
 *
 * Strict Wave-1 rule: NO modal. The editor slides in from the right
 * and is dismissed by clicking the backdrop, hitting ESC, or pressing
 * the X button. The parent owns the open/close state — this panel
 * just renders the form.
 *
 * The override editor is intentionally lightweight: name + value
 * inputs per row, add/remove buttons, freeform "Run with these
 * overrides" CTA that calls the scenario-run endpoint and polls the
 * result. Validation is deferred to the worker (which already runs
 * Pydantic on every field path the engine accepts).
 */
import { useEffect, useMemo, useState } from 'react';
import { X, Plus, Trash2, Save, Play, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  isWorkerConnected,
  type ReturnsPreviewResponse,
  type ScenarioOverride,
  type ScenarioRecord,
} from '@/lib/api';
import { cn } from '@/lib/format';

interface Props {
  open: boolean;
  dealId: string;
  scenario: ScenarioRecord | null;
  onClose: () => void;
  onSaved: (scenario: ScenarioRecord) => void;
  /** Optional: parent gets notified after a successful Run so it can
   *  refresh engine outputs / advance the active scenario. */
  onRan?: (scenario: ScenarioRecord) => void;
}

interface OverrideRow {
  field_path: string;
  value: string; // string in the input; coerced on save
}

// FON-69 — a business-friendly catalog so analysts never touch raw field paths
// or raw fractions. Each entry maps a human label + unit to the canonical
// override path the engines accept. `pct` fields let the user type "8.5" and
// store 0.085; `usd` accepts "$36.4M"-style numbers; `years` are integers.
//
// Exported (FON-53 canonical alignment) so the inline per-scenario override
// table in ScenarioComparePanel reads labels / units / the source tab from the
// SAME source of truth — the override table and this drawer never drift.
export type ScenarioUnit = 'pct' | 'usd' | 'years' | 'number';
export type ScenarioSourceTab =
  | 'financials'
  | 'investment'
  | 'debt'
  | 'partnership'
  | 'market';
export interface AssumptionMeta {
  path: string;
  label: string;
  unit: ScenarioUnit;
  group: string;
  /** The model tab that owns this assumption (the override table's Source →). */
  source: ScenarioSourceTab;
  sourceLabel: string;
  /** Decimal places for pct display (exit cap / rate want 2). */
  dec?: number;
}
export const ASSUMPTION_CATALOG: AssumptionMeta[] = [
  { path: 'starting_occupancy', label: 'Year-1 Occupancy', unit: 'pct', group: 'Revenue', source: 'financials', sourceLabel: 'Financials' },
  { path: 'starting_adr', label: 'Year-1 ADR', unit: 'usd', group: 'Revenue', source: 'financials', sourceLabel: 'Financials' },
  { path: 'revpar_growth', label: 'RevPAR Growth', unit: 'pct', group: 'Revenue', source: 'financials', sourceLabel: 'Financials' },
  { path: 'adr_growth', label: 'ADR Growth', unit: 'pct', group: 'Revenue', source: 'financials', sourceLabel: 'Financials' },
  { path: 'occupancy_growth', label: 'Occupancy Growth', unit: 'pct', group: 'Revenue', source: 'financials', sourceLabel: 'Financials' },
  { path: 'expense_growth', label: 'Expense Growth', unit: 'pct', group: 'Expenses', source: 'financials', sourceLabel: 'Financials' },
  { path: 'mgmt_fee_pct', label: 'Management Fee', unit: 'pct', group: 'Expenses', source: 'financials', sourceLabel: 'Financials' },
  { path: 'ffe_reserve_pct', label: 'FF&E Reserve', unit: 'pct', group: 'Expenses', source: 'financials', sourceLabel: 'Financials' },
  { path: 'purchase_price', label: 'Purchase Price', unit: 'usd', group: 'Acquisition', source: 'investment', sourceLabel: 'Investment' },
  { path: 'exit_cap_rate', label: 'Exit Cap Rate', unit: 'pct', group: 'Exit', source: 'investment', sourceLabel: 'Investment', dec: 2 },
  { path: 'hold_years', label: 'Hold Period', unit: 'years', group: 'Exit', source: 'investment', sourceLabel: 'Investment' },
  { path: 'ltv', label: 'LTV', unit: 'pct', group: 'Debt', source: 'debt', sourceLabel: 'Debt' },
  { path: 'interest_rate', label: 'Interest Rate', unit: 'pct', group: 'Debt', source: 'debt', sourceLabel: 'Debt', dec: 2 },
  { path: 'amortization_years', label: 'Amortization (yrs)', unit: 'years', group: 'Debt', source: 'debt', sourceLabel: 'Debt' },
  { path: 'term_years', label: 'Loan Term (yrs)', unit: 'years', group: 'Debt', source: 'debt', sourceLabel: 'Debt' },
  { path: 'pref_rate', label: 'Preferred Return', unit: 'pct', group: 'Partnership', source: 'partnership', sourceLabel: 'Partnership' },
];
export const CATALOG_BY_PATH = new Map(ASSUMPTION_CATALOG.map((a) => [a.path, a]));
const CATALOG_GROUPS = Array.from(new Set(ASSUMPTION_CATALOG.map((a) => a.group)));

export function unitFor(path: string): ScenarioUnit | null {
  return CATALOG_BY_PATH.get(path)?.unit ?? null;
}
/** Human label for a field path (falls back to the raw path). */
export function labelForPath(path: string): string {
  return CATALOG_BY_PATH.get(path)?.label ?? path;
}
/** Format a stored (canonical) value for read-only display, keyed off unit. */
export function fmtAssumption(path: string, v: unknown): string {
  if (typeof v !== 'number' || !Number.isFinite(v)) {
    return v == null || v === '' ? '—' : String(v);
  }
  const meta = CATALOG_BY_PATH.get(path);
  const u = meta?.unit ?? 'number';
  if (u === 'pct') return `${(v * 100).toFixed(meta?.dec ?? 1)}%`;
  if (u === 'usd') {
    return Math.abs(v) >= 1_000_000
      ? `$${(v / 1_000_000).toFixed(1)}M`
      : `$${Math.round(v).toLocaleString('en-US')}`;
  }
  if (u === 'years') return `${v} yr${Math.abs(v) === 1 ? '' : 's'}`;
  return String(v);
}
/** Change string (scenario − base), keyed off unit: bps / $M / yrs. */
export function fmtAssumptionChange(
  path: string,
  base: number,
  scenario: number,
): string {
  const u = CATALOG_BY_PATH.get(path)?.unit ?? 'number';
  const diff = scenario - base;
  const sign = diff >= 0 ? '+' : '−';
  if (u === 'pct') return `${sign}${Math.abs(diff * 10000).toFixed(0)} bps`;
  if (u === 'usd') return `${sign}$${Math.abs(diff / 1_000_000).toFixed(1)}M`;
  if (u === 'years')
    return `${diff >= 0 ? '+' : ''}${diff} yr${Math.abs(diff) === 1 ? '' : 's'}`;
  return `${diff >= 0 ? '+' : ''}${diff}`;
}
// Stored (canonical) → what the input shows.
function toDisplay(path: string, stored: unknown): string {
  if (unitFor(path) === 'pct' && typeof stored === 'number') {
    return String(Number((stored * 100).toFixed(4)));
  }
  return stringifyValue(stored);
}
// Input string → canonical value the engine stores.
function fromDisplay(path: string, input: string): unknown {
  const u = unitFor(path);
  const t = input.trim();
  if (t === '') return '';
  if (u === 'pct') {
    const n = parseFloat(t.replace('%', ''));
    return Number.isFinite(n) ? n / 100 : coerceValue(t);
  }
  if (u === 'usd') {
    const n = parseFloat(t.replace(/[$,\s]/g, ''));
    return Number.isFinite(n) ? n : coerceValue(t);
  }
  return coerceValue(t);
}

export default function ScenarioEditor({
  open,
  dealId,
  scenario,
  onClose,
  onSaved,
  onRan,
}: Props) {
  const { toast } = useToast();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [rows, setRows] = useState<OverrideRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  // Pre-save Preview (FON-53 canonical) — projected metric deltas vs Base,
  // computed by the NON-persisting returns/preview endpoint. Never writes.
  const [preview, setPreview] = useState<{
    base: ReturnsPreviewResponse | null;
    scenario: ReturnsPreviewResponse | null;
  }>({ base: null, scenario: null });
  const [previewLoading, setPreviewLoading] = useState(false);

  // Reset whenever the editor opens onto a new scenario.
  useEffect(() => {
    if (!open) return;
    if (scenario) {
      setName(scenario.name);
      setDescription(scenario.description ?? '');
      setRows(
        scenario.overrides.map((o) => ({
          field_path: o.field_path,
          value: toDisplay(o.field_path, o.value),
        })),
      );
    } else {
      setName('');
      setDescription('');
      setRows([{ field_path: '', value: '' }]);
    }
  }, [open, scenario]);

  // ESC to close.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // The recognized, numeric override map to preview. Serialized so the effect
  // only re-runs when the actual values change (not on every keystroke object).
  const previewKey = useMemo(() => {
    const o: Record<string, number> = {};
    for (const r of rows) {
      const path = r.field_path.trim();
      if (!path) continue;
      const val = fromDisplay(path, r.value);
      if (typeof val === 'number' && Number.isFinite(val)) o[path] = val;
    }
    return JSON.stringify(o);
  }, [rows]);

  // Debounced dual preview: Base (no overrides) + this scenario's overrides.
  useEffect(() => {
    if (!open) return;
    if (!isWorkerConnected()) return;
    const overrides = JSON.parse(previewKey) as Record<string, number>;
    const ac = new AbortController();
    const t = setTimeout(() => {
      setPreviewLoading(true);
      Promise.all([
        api.engines.returnsPreview(dealId, { overrides: {} }, ac.signal),
        api.engines.returnsPreview(dealId, { overrides }, ac.signal),
      ])
        .then(([base, scenario]) => setPreview({ base, scenario }))
        .catch(() => {
          /* preview is best-effort; silence aborts / worker gaps */
        })
        .finally(() => setPreviewLoading(false));
    }, 350);
    return () => {
      clearTimeout(t);
      ac.abort();
    };
  }, [open, dealId, previewKey]);

  if (!open) return null;

  const isBase = scenario?.is_base ?? false;

  const overridesPayload: ScenarioOverride[] = rows
    .filter((r) => r.field_path.trim())
    .map((r) => ({
      field_path: r.field_path.trim(),
      value: fromDisplay(r.field_path.trim(), r.value),
    }));

  const previewTiles = buildPreviewTiles(preview.base, preview.scenario);

  async function handleSave() {
    if (!name.trim()) {
      toast(
        'Name required — give the scenario a label (e.g. "downside").',
        { type: 'error' },
      );
      return;
    }
    setSaving(true);
    try {
      let saved: ScenarioRecord;
      if (scenario) {
        saved = await api.scenarios.update(dealId, scenario.id, {
          name: name.trim(),
          description: description.trim() || null,
          overrides: overridesPayload,
        });
      } else {
        saved = await api.scenarios.create(dealId, {
          name: name.trim(),
          description: description.trim() || null,
          overrides: overridesPayload,
        });
      }
      onSaved(saved);
      toast(
        `Scenario saved: ${saved.name} — ${overridesPayload.length} override${overridesPayload.length === 1 ? '' : 's'}`,
        { type: 'success' },
      );
      onClose();
    } catch (e) {
      toast(
        `Save failed: ${e instanceof Error ? e.message : 'Unknown error'}`,
        { type: 'error' },
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleRun() {
    if (!scenario) return;
    setRunning(true);
    try {
      // Save first so any in-flight edits land before the run.
      const saved = await api.scenarios.update(dealId, scenario.id, {
        name: name.trim() || scenario.name,
        description: description.trim() || null,
        overrides: overridesPayload,
      });
      await api.scenarios.run(dealId, scenario.id);
      const refreshed = await api.scenarios.get(dealId, scenario.id);
      onSaved(refreshed);
      onRan?.(refreshed);
      toast(`Scenario ran: ${saved.name} — engines refreshed`, {
        type: 'success',
      });
      onClose();
    } catch (e) {
      toast(
        `Run failed: ${e instanceof Error ? e.message : 'Unknown error'}`,
        { type: 'error' },
      );
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <div
        className="fixed inset-0 z-40 bg-ink-900/30"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={scenario ? `Edit scenario ${scenario.name}` : 'New scenario'}
        className="fixed right-0 top-0 bottom-0 z-50 w-[480px] bg-white border-l border-border shadow-card-hover flex flex-col"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div>
            <div className="text-[13.5px] font-semibold text-ink-900">
              {scenario ? 'Edit scenario' : 'New scenario'}
            </div>
            <div className="text-[11px] text-ink-500">
              {isBase
                ? 'Base scenario — overrides apply on top of deal defaults'
                : 'Overrides layer on top of the deal’s base assumptions'}
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close editor"
            className="p-1 text-ink-500 hover:text-ink-900"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
          <div>
            <label className="block text-[11px] uppercase tracking-wide text-ink-500 mb-1">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="downside, IC stress, broker high case…"
              className="w-full px-2 py-1.5 text-[13px] border border-border rounded focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
          <div>
            <label className="block text-[11px] uppercase tracking-wide text-ink-500 mb-1">
              Description (optional)
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="What does this scenario assume?"
              className="w-full px-2 py-1.5 text-[13px] border border-border rounded resize-none focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <label className="text-[11px] uppercase tracking-wide text-ink-500">
                Overrides
              </label>
              <button
                type="button"
                onClick={() =>
                  setRows((rs) => [...rs, { field_path: '', value: '' }])
                }
                className="flex items-center gap-1 text-[11.5px] text-brand-700 hover:text-brand-900"
              >
                <Plus size={12} aria-hidden="true" />
                Add row
              </button>
            </div>
            <div className="space-y-2">
              {rows.length === 0 && (
                <div className="text-[12px] text-ink-500 italic">
                  No overrides — running this scenario matches the base.
                </div>
              )}
              {rows.map((row, idx) => (
                <div key={idx} className="flex items-center gap-2">
                  <select
                    aria-label="Assumption"
                    value={row.field_path}
                    onChange={(e) =>
                      setRows((rs) =>
                        rs.map((r, i) =>
                          // Clear the value when the assumption changes so a
                          // pct value can't linger under a $ field.
                          i === idx ? { field_path: e.target.value, value: '' } : r,
                        ),
                      )
                    }
                    className="flex-1 px-2 py-1 text-[12.5px] border border-border rounded bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
                  >
                    <option value="">Choose assumption…</option>
                    {row.field_path && !CATALOG_BY_PATH.has(row.field_path) && (
                      <option value={row.field_path}>{row.field_path} (advanced)</option>
                    )}
                    {CATALOG_GROUPS.map((g) => (
                      <optgroup key={g} label={g}>
                        {ASSUMPTION_CATALOG.filter((a) => a.group === g).map((a) => (
                          <option key={a.path} value={a.path}>{a.label}</option>
                        ))}
                      </optgroup>
                    ))}
                  </select>
                  <div className="relative w-28 shrink-0">
                    <input
                      type="text"
                      value={row.value}
                      placeholder={
                        unitFor(row.field_path) === 'pct' ? '8.5'
                        : unitFor(row.field_path) === 'usd' ? '36,400,000'
                        : unitFor(row.field_path) === 'years' ? '5' : 'value'
                      }
                      onChange={(e) =>
                        setRows((rs) =>
                          rs.map((r, i) =>
                            i === idx ? { ...r, value: e.target.value } : r,
                          ),
                        )
                      }
                      className="w-full px-2 py-1 pr-7 text-[12.5px] tabular-nums border border-border rounded focus:outline-none focus:ring-2 focus:ring-brand-500"
                    />
                    {unitFor(row.field_path) && (
                      <span className="absolute right-2 top-1/2 -translate-y-1/2 text-[10.5px] text-ink-400 pointer-events-none">
                        {unitFor(row.field_path) === 'pct' ? '%' : unitFor(row.field_path) === 'usd' ? '$' : 'yr'}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() =>
                      setRows((rs) => rs.filter((_, i) => i !== idx))
                    }
                    aria-label="Remove override"
                    className="p-1 text-ink-500 hover:text-red-600"
                  >
                    <Trash2 size={13} aria-hidden="true" />
                  </button>
                </div>
              ))}
            </div>
            <p className="text-[10.5px] text-ink-500 mt-2 leading-tight">
              Pick an assumption and enter its value — percentages as
              percentages (e.g. <code>8.5</code> for 8.5%), dollars as dollars.
              A scenario is these overrides layered on top of the Base case.
            </p>
          </div>

          {/* Pre-save Preview — projected metric deltas vs Base (FON-53). */}
          <div
            data-testid="scenario-preview"
            style={{
              border: `1px solid ${PV.border}`,
              borderRadius: 6,
              padding: '12px 14px',
              display: 'flex',
              flexDirection: 'column',
              gap: 8,
            }}
          >
            <div
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <span
                style={{
                  fontSize: 10,
                  fontWeight: 700,
                  letterSpacing: '.06em',
                  color: PV.eyebrow,
                  textTransform: 'uppercase',
                }}
              >
                Preview
              </span>
              {previewLoading && (
                <Loader2
                  size={12}
                  className="animate-spin"
                  style={{ color: PV.muted }}
                  aria-hidden="true"
                />
              )}
            </div>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(3,1fr)',
                gap: 10,
              }}
            >
              {previewTiles.map((t) => (
                <div
                  key={t.label}
                  style={{ display: 'flex', flexDirection: 'column', gap: 2 }}
                >
                  <span
                    style={{
                      fontSize: 10,
                      fontWeight: 700,
                      letterSpacing: '.04em',
                      color: PV.eyebrow,
                      textTransform: 'uppercase',
                    }}
                  >
                    {t.label}
                  </span>
                  <span
                    style={{
                      fontSize: 17,
                      fontWeight: 700,
                      color: PV.ink,
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {t.value}
                  </span>
                  <span
                    style={{
                      fontSize: 10.5,
                      color: t.color,
                      fontVariantNumeric: 'tabular-nums',
                    }}
                  >
                    {t.delta || ' '}
                  </span>
                </div>
              ))}
            </div>
            <span
              style={{ fontSize: 10.5, color: PV.muted, lineHeight: 1.5 }}
            >
              Projected live from the model — returns drivers (exit cap, growth,
              hold, LTV, rate) move the preview; other overrides apply on Save.
            </span>
          </div>
        </div>

        <div className="flex items-center justify-between gap-2 px-4 py-3 border-t border-border bg-surface-muted">
          <Button
            variant="ghost"
            onClick={onClose}
            disabled={saving || running}
          >
            Cancel
          </Button>
          <div className="flex items-center gap-2">
            {scenario && (
              <Button
                variant="secondary"
                onClick={handleRun}
                disabled={saving || running}
              >
                {running ? (
                  <Loader2 size={13} className="animate-spin" aria-hidden="true" />
                ) : (
                  <Play size={13} aria-hidden="true" />
                )}
                <span className="ml-1.5">
                  {running ? 'Running…' : 'Run with these overrides'}
                </span>
              </Button>
            )}
            <Button variant="primary" onClick={handleSave} disabled={saving || running}>
              {saving ? (
                <Loader2 size={13} className="animate-spin" aria-hidden="true" />
              ) : (
                <Save size={13} aria-hidden="true" />
              )}
              <span className="ml-1.5">
                {saving ? 'Saving…' : 'Save scenario'}
              </span>
            </Button>
          </div>
        </div>
      </aside>
    </>
  );
}

// Canonical Preview palette (Scenarios Tab.dc.html).
const PV = {
  border: '#e6e5e0',
  eyebrow: '#8a8a86',
  ink: '#1a2233',
  muted: '#b0afaa',
  green: 'oklch(45% 0.12 155)',
  red: 'oklch(50% 0.15 30)',
} as const;

interface PreviewTile {
  label: string;
  value: string;
  delta: string;
  color: string;
}

function buildPreviewTiles(
  base: ReturnsPreviewResponse | null,
  scenario: ReturnsPreviewResponse | null,
): PreviewTile[] {
  const tile = (
    label: string,
    bv: number | null | undefined,
    sv: number | null | undefined,
    kind: 'pct' | 'mult',
  ): PreviewTile => {
    if (sv == null) return { label, value: '—', delta: '', color: PV.muted };
    const value = kind === 'mult' ? `${sv.toFixed(2)}x` : `${(sv * 100).toFixed(1)}%`;
    if (bv == null) return { label, value, delta: '', color: PV.muted };
    const diff = sv - bv;
    const delta =
      kind === 'mult'
        ? `${diff >= 0 ? '+' : ''}${diff.toFixed(2)}x vs Base`
        : `${diff >= 0 ? '+' : ''}${(diff * 100).toFixed(1)} pts vs Base`;
    const color = Math.abs(diff) < 1e-9 ? PV.muted : diff >= 0 ? PV.green : PV.red;
    return { label, value, delta, color };
  };
  return [
    tile('Levered IRR', base?.levered_irr, scenario?.levered_irr, 'pct'),
    tile('Equity Multiple', base?.equity_multiple, scenario?.equity_multiple, 'mult'),
    tile('Year-1 CoC', base?.year_one_coc, scenario?.year_one_coc, 'pct'),
  ];
}

function stringifyValue(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string') return v;
  if (typeof v === 'number' || typeof v === 'boolean') return String(v);
  return JSON.stringify(v);
}

function coerceValue(raw: string): unknown {
  const trimmed = raw.trim();
  if (trimmed === '') return '';
  // JSON-looking values pass through (arrays, structured PIP fields).
  if (
    (trimmed.startsWith('[') && trimmed.endsWith(']')) ||
    (trimmed.startsWith('{') && trimmed.endsWith('}'))
  ) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return trimmed;
    }
  }
  // Numbers — keep the literal as a number so the engine routing sees
  // the right type.
  const asNum = Number(trimmed);
  if (Number.isFinite(asNum) && /^-?\d+(\.\d+)?$/.test(trimmed)) {
    return asNum;
  }
  if (trimmed === 'true') return true;
  if (trimmed === 'false') return false;
  return trimmed;
}
