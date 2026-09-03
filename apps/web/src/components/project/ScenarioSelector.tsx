'use client';
/**
 * ScenarioSelector — Wave 3 W3.2 pill-row at the top of the project
 * workspace.
 *
 * "Base · Downside · Upside · + New scenario" — clicking a pill makes
 * that scenario active; hovering shows a popover with the override
 * delta from base. The "+ New scenario" pill opens the
 * ``ScenarioEditor`` side panel (NO modal — Wave 1 no-popups rule).
 *
 * Parent owns the active-scenario state and the editor open/close
 * state; this component is pure presentation + click delegation so a
 * page that wants to track the active scenario in the URL or a store
 * can do so without re-implementing the pill row.
 */
import { useEffect, useState } from 'react';
import { Plus, Pencil } from 'lucide-react';
import type { ScenarioRecord } from '@/lib/api';
import { cn } from '@/lib/format';

interface Props {
  scenarios: ScenarioRecord[];
  activeScenarioId: string | null;
  onSelect: (scenarioId: string) => void;
  onCreate: () => void;
  /** FON-69 — open the editor for a saved scenario. */
  onEdit?: (scenario: ScenarioRecord) => void;
  loading?: boolean;
}

export default function ScenarioSelector({
  scenarios,
  activeScenarioId,
  onSelect,
  onCreate,
  onEdit,
  loading,
}: Props) {
  const base = scenarios.find((s) => s.is_base) ?? null;
  const named = scenarios.filter((s) => !s.is_base);

  if (loading && scenarios.length === 0) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex items-center gap-2 px-4 py-2 text-[12px] text-ink-500"
      >
        Loading scenarios…
      </div>
    );
  }

  return (
    <div
      role="tablist"
      aria-label="What-if scenarios"
      className="flex flex-wrap items-center gap-2 px-4 py-2 border-b border-border bg-surface-muted"
    >
      <span className="text-[11px] uppercase tracking-wide text-ink-500 mr-2">
        Scenario
      </span>
      {base && (
        <ScenarioPill
          scenario={base}
          isActive={activeScenarioId === base.id}
          onSelect={() => onSelect(base.id)}
          baseScenario={base}
        />
      )}
      {named.map((s) => (
        <ScenarioPill
          key={s.id}
          scenario={s}
          isActive={activeScenarioId === s.id}
          onSelect={() => onSelect(s.id)}
          onEdit={onEdit ? () => onEdit(s) : undefined}
          baseScenario={base}
        />
      ))}
      <button
        type="button"
        onClick={onCreate}
        aria-label="Add scenario"
        className={cn(
          'flex items-center gap-1.5 px-3 py-1 text-[12px] rounded-full border border-dashed',
          'border-ink-300 text-ink-600 hover:border-brand-500 hover:text-brand-700 transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
        )}
      >
        <Plus size={12} aria-hidden="true" />
        New scenario
      </button>
      {/* FON-69 — model tabs always render Base (canonical). Scenarios are a
          comparison lens seen in Scenario Analysis, not a mode that changes the
          numbers on other tabs. */}
      <span className="ml-auto inline-flex items-center gap-1.5 text-[11px] text-ink-500">
        <span className="w-1.5 h-1.5 rounded-full bg-success-500" />
        Model tabs show <span className="font-medium text-ink-700">Base</span> · compare scenarios in Scenario Analysis
      </span>
    </div>
  );
}

interface PillProps {
  scenario: ScenarioRecord;
  isActive: boolean;
  onSelect: () => void;
  onEdit?: () => void;
  baseScenario: ScenarioRecord | null;
}

function ScenarioPill({ scenario, isActive, onSelect, onEdit, baseScenario }: PillProps) {
  const [hovering, setHovering] = useState(false);
  const baseOverrides = baseScenario?.overrides ?? [];
  const delta = computeDelta(baseOverrides, scenario.overrides);

  // Close the hover popover when the keyboard focus leaves the pill.
  useEffect(() => {
    if (!isActive) return;
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setHovering(false);
    };
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [isActive]);

  return (
    <div
      className="relative"
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      onFocus={() => setHovering(true)}
      onBlur={() => setHovering(false)}
    >
      <button
        type="button"
        role="tab"
        aria-selected={isActive}
        onClick={onSelect}
        className={cn(
          // FON-69 — selecting a scenario is a COMPARISON lens, not a mode you
          // enter (model tabs always show Base). So the active pill reads as
          // "selected for comparison" (outline), not the old gradient fill that
          // implied the whole app switched modes.
          'flex items-center gap-1.5 px-3 py-1 text-[12px] rounded-full border transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          isActive
            ? 'bg-brand-50 border-brand-500 text-brand-700 font-semibold'
            : 'bg-white border-border text-ink-700 hover:border-brand-400 hover:text-brand-700',
        )}
      >
        <span>{scenario.name}</span>
        {scenario.is_base && (
          // Canonical "SOURCE OF TRUTH" badge — Base is the one deal the model
          // tabs render; scenarios are a comparison lens seen only here.
          <span
            title="The canonical underwriting — edited in Financials, Investment, Debt and Partnership"
            className="text-[9px] font-bold uppercase tracking-[0.06em] text-ink-500 bg-ink-100 rounded-[3px] px-1 py-0.5 leading-none"
          >
            SOURCE OF TRUTH
          </span>
        )}
        {!scenario.is_base && delta.length > 0 && (
          <span
            className={cn(
              'text-[10px] px-1 rounded',
              isActive
                ? 'bg-brand-100 text-brand-700'
                : 'bg-ink-100 text-ink-600',
            )}
          >
            {delta.length} change{delta.length === 1 ? '' : 's'}
          </span>
        )}
      </button>
      {hovering && !scenario.is_base && (
        <div
          role="tooltip"
          className="absolute left-0 top-full mt-1 z-30 min-w-[260px] max-w-[360px] bg-white border border-border rounded-md shadow-card-hover p-3"
        >
          <div className="text-[11px] font-semibold text-ink-700 mb-1.5">
            Δ from base
          </div>
          {delta.length === 0 && (
            <p className="text-[11px] text-ink-500">No overrides yet — matches Base.</p>
          )}
          <dl className="space-y-1">
            {delta.slice(0, 8).map((row) => (
              <div key={row.field_path} className="flex justify-between gap-2">
                <dt className="text-[11px] text-ink-600 font-mono truncate">
                  {row.field_path}
                </dt>
                <dd className="text-[11px] text-ink-900 tabular-nums">
                  {formatDeltaValue(row.value)}
                </dd>
              </div>
            ))}
            {delta.length > 8 && (
              <div className="text-[11px] text-ink-500 pt-1">
                +{delta.length - 8} more…
              </div>
            )}
          </dl>
          {scenario.description && (
            <p className="text-[11px] text-ink-600 mt-2 pt-2 border-t border-border">
              {scenario.description}
            </p>
          )}
          {onEdit && (
            <button
              type="button"
              onClick={onEdit}
              className="mt-2 pt-2 w-full border-t border-border text-left text-[11px] font-medium text-brand-700 hover:text-brand-800 inline-flex items-center gap-1"
            >
              <Pencil size={11} aria-hidden="true" /> Edit scenario
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface DeltaRow {
  field_path: string;
  value: unknown;
}

function computeDelta(
  baseOverrides: { field_path: string; value: unknown }[],
  scenarioOverrides: { field_path: string; value: unknown }[],
): DeltaRow[] {
  const baseMap = new Map(
    baseOverrides.map((o) => [o.field_path, o.value]),
  );
  const out: DeltaRow[] = [];
  for (const o of scenarioOverrides) {
    const baseVal = baseMap.get(o.field_path);
    if (baseVal === undefined || baseVal !== o.value) {
      out.push({ field_path: o.field_path, value: o.value });
    }
  }
  return out;
}

function formatDeltaValue(value: unknown): string {
  if (typeof value === 'number') {
    // Heuristic: anything 0-1 reads as a percentage; bigger numbers
    // get tabular formatting.
    if (Math.abs(value) <= 1) {
      return `${(value * 100).toFixed(1)}%`;
    }
    if (Math.abs(value) >= 1000) {
      return value.toLocaleString();
    }
    return String(value);
  }
  if (Array.isArray(value)) {
    return `[${value.length} entries]`;
  }
  return String(value ?? '—');
}
