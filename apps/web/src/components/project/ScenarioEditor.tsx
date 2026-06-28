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
import { useEffect, useState } from 'react';
import { X, Plus, Trash2, Save, Play, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Toast';
import {
  api,
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

  // Reset whenever the editor opens onto a new scenario.
  useEffect(() => {
    if (!open) return;
    if (scenario) {
      setName(scenario.name);
      setDescription(scenario.description ?? '');
      setRows(
        scenario.overrides.map((o) => ({
          field_path: o.field_path,
          value: stringifyValue(o.value),
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

  if (!open) return null;

  const isBase = scenario?.is_base ?? false;

  const overridesPayload: ScenarioOverride[] = rows
    .filter((r) => r.field_path.trim())
    .map((r) => ({
      field_path: r.field_path.trim(),
      value: coerceValue(r.value),
    }));

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
                  <input
                    type="text"
                    value={row.field_path}
                    placeholder="exit_cap_rate"
                    onChange={(e) =>
                      setRows((rs) =>
                        rs.map((r, i) =>
                          i === idx ? { ...r, field_path: e.target.value } : r,
                        ),
                      )
                    }
                    className="flex-1 px-2 py-1 text-[12.5px] font-mono border border-border rounded focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
                  <input
                    type="text"
                    value={row.value}
                    placeholder="0.085"
                    onChange={(e) =>
                      setRows((rs) =>
                        rs.map((r, i) =>
                          i === idx ? { ...r, value: e.target.value } : r,
                        ),
                      )
                    }
                    className="w-24 px-2 py-1 text-[12.5px] tabular-nums border border-border rounded focus:outline-none focus:ring-2 focus:ring-brand-500"
                  />
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
              Field paths use the same canonical names as the
              assumption-source badges: <code>starting_occupancy</code>,
              <code> exit_cap_rate</code>, <code>pip_displacement.brand</code>,
              <code> segments.transient_ota.adr</code>, etc.
            </p>
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
