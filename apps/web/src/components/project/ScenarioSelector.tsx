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
import { Pin, Plus } from 'lucide-react';
import type { ScenarioRecord } from '@/lib/api';
import { cn } from '@/lib/format';

interface Props {
  scenarios: ScenarioRecord[];
  activeScenarioId: string | null;
  onSelect: (scenarioId: string) => void;
  onCreate: () => void;
  loading?: boolean;
}

export default function ScenarioSelector({
  scenarios,
  activeScenarioId,
  onSelect,
  onCreate,
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
    </div>
  );
}

interface PillProps {
  scenario: ScenarioRecord;
  isActive: boolean;
  onSelect: () => void;
  baseScenario: ScenarioRecord | null;
}

function ScenarioPill({ scenario, isActive, onSelect, baseScenario }: PillProps) {
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
          'flex items-center gap-1.5 px-3 py-1 text-[12px] rounded-full transition-colors',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
          isActive
            ? 'bg-gradient-to-r from-brand-600 to-brand-500 text-white font-semibold shadow-sm'
            : 'bg-white border border-border text-ink-700 hover:border-brand-400 hover:text-brand-700',
        )}
      >
        {scenario.is_base && <Pin size={11} aria-hidden="true" />}
        <span>{scenario.name}</span>
        {!scenario.is_base && delta.length > 0 && (
          <span
            className={cn(
              'text-[10px] px-1 rounded',
              isActive
                ? 'bg-white/20 text-white'
                : 'bg-ink-100 text-ink-600',
            )}
          >
            {delta.length} change{delta.length === 1 ? '' : 's'}
          </span>
        )}
      </button>
      {hovering && !scenario.is_base && delta.length > 0 && (
        <div
          role="tooltip"
          className="absolute left-0 top-full mt-1 z-30 min-w-[260px] max-w-[360px] bg-white border border-border rounded-md shadow-card-hover p-3"
        >
          <div className="text-[11px] font-semibold text-ink-700 mb-1.5">
            Δ from base
          </div>
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
