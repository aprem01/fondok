'use client';
/**
 * PIPDisplacementPanel — Wave 2 P2.4 structured PIP renovation input.
 *
 * Sam's June 2026 ask: "PIP displacement must scale by % rooms offline
 * + brand + closure strategy." This panel surfaces the four levers the
 * revenue engine reads from ``PIPDisplacement`` on the worker schema:
 *
 *   1. Closure strategy (rolling / full_closure / wing_by_wing / none)
 *   2. Brand (drives recovery-month + RevPAR-index multipliers)
 *   3. Month-by-month % rooms offline schedule (12 inputs, 0..1)
 *   4. Post-PIP RevPAR uplift index + occupancy recovery months
 *
 * Inline edit-in-place — NO modal (strict Wave 1 no-popups rule). A
 * one-line summary renders when not editing. The parent owns the
 * PATCH; this panel just emits ``onChange`` with the new state.
 */
import { useState } from 'react';
import { ChevronDown, ChevronUp, Pencil, Check, X } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { AssumptionBadge } from '@/components/help/AssumptionBadge';
import { cn } from '@/lib/format';

export type PIPClosureStrategy =
  | 'rolling'
  | 'full_closure'
  | 'wing_by_wing'
  | 'none';

export interface PIPDisplacementValue {
  closure_strategy: PIPClosureStrategy;
  pct_rooms_offline_by_month: number[];
  brand: string | null;
  revpar_index_post_reno: number;
  occupancy_recovery_months: number;
}

export const DEFAULT_PIP_DISPLACEMENT: PIPDisplacementValue = {
  closure_strategy: 'none',
  pct_rooms_offline_by_month: Array(12).fill(0),
  brand: null,
  revpar_index_post_reno: 1.05,
  occupancy_recovery_months: 12,
};

const STRATEGY_LABELS: Record<PIPClosureStrategy, string> = {
  none: 'No renovation',
  rolling: 'Rolling renovation',
  full_closure: 'Full closure',
  wing_by_wing: 'Wing-by-wing',
};

// Brand options — keep in sync with apps/worker/app/engines/revenue.py
// _BRAND_DISPLACEMENT_MULTIPLIERS.
const BRAND_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Independent / soft-brand' },
  { value: 'Marriott', label: 'Marriott' },
  { value: 'Hilton', label: 'Hilton' },
  { value: 'IHG', label: 'IHG' },
  { value: 'Hyatt', label: 'Hyatt' },
];

export interface PIPDisplacementPanelProps {
  value: PIPDisplacementValue | null;
  /** Provenance label for the assumption badge. Pass `'pip_user'` when
   *  the analyst has set the value, `'seed'` otherwise. */
  source?: string;
  /** Y1 rooms revenue impact in USD (negative). Optional; renders in
   *  the collapsed summary line when supplied. */
  y1ImpactUsd?: number | null;
  onChange?: (next: PIPDisplacementValue) => void;
  /** When false the panel shows the summary-only view and the user
   *  can't toggle into edit mode. Useful on read-only roles. */
  editable?: boolean;
}

export default function PIPDisplacementPanel({
  value,
  source = 'seed',
  y1ImpactUsd,
  onChange,
  editable = true,
}: PIPDisplacementPanelProps) {
  const [edit, setEdit] = useState(false);
  const v = value ?? DEFAULT_PIP_DISPLACEMENT;
  const isActive = v.closure_strategy !== 'none';

  // ─── Summary string ───
  const monthsOffline = v.pct_rooms_offline_by_month.filter(p => p > 0).length;
  const avgOfflineWhenActive = monthsOffline
    ? (v.pct_rooms_offline_by_month
        .filter(p => p > 0)
        .reduce((a, b) => a + b, 0) / monthsOffline) * 100
    : 0;
  const summary = isActive
    ? `${STRATEGY_LABELS[v.closure_strategy]}` +
      (v.brand ? `, ${v.brand}` : ', Independent') +
      `, ${monthsOffline} months @ ${avgOfflineWhenActive.toFixed(0)}% offline` +
      (y1ImpactUsd != null
        ? ` → ${formatCompactDollars(y1ImpactUsd)} Y1 rooms revenue`
        : '') +
      `, +${((v.revpar_index_post_reno - 1) * 100).toFixed(0)}% RevPAR Y3`
    : 'No renovation displacement modeled.';

  if (!edit) {
    return (
      <Card className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[11px] uppercase tracking-wide text-ink-500 font-medium">
                PIP Displacement
              </span>
              <AssumptionBadge source={source} />
            </div>
            <p className="text-[12.5px] text-ink-700 tabular-nums">{summary}</p>
          </div>
          {editable && (
            <button
              onClick={() => setEdit(true)}
              className="inline-flex items-center gap-1 text-[11.5px] text-brand-700 hover:text-brand-800 shrink-0"
              type="button"
            >
              <Pencil size={11} />
              Edit
            </button>
          )}
        </div>
      </Card>
    );
  }

  // ─── Edit-in-place form ───
  const update = (patch: Partial<PIPDisplacementValue>) => {
    onChange?.({ ...v, ...patch });
  };

  return (
    <Card className="p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wide text-ink-500 font-medium">
            PIP Displacement
          </span>
          <AssumptionBadge source={source} />
        </div>
        <button
          onClick={() => setEdit(false)}
          className="inline-flex items-center gap-1 text-[11.5px] text-ink-500 hover:text-ink-900"
          type="button"
        >
          <Check size={11} />
          Done
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <Field label="Closure Strategy">
          <select
            value={v.closure_strategy}
            onChange={e =>
              update({ closure_strategy: e.target.value as PIPClosureStrategy })
            }
            className="w-full text-[12.5px] border border-border rounded px-2 py-1 bg-white"
          >
            {(Object.keys(STRATEGY_LABELS) as PIPClosureStrategy[]).map(s => (
              <option key={s} value={s}>
                {STRATEGY_LABELS[s]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Brand">
          <select
            value={v.brand ?? ''}
            onChange={e => update({ brand: e.target.value || null })}
            className="w-full text-[12.5px] border border-border rounded px-2 py-1 bg-white"
          >
            {BRAND_OPTIONS.map(o => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Post-PIP RevPAR Index">
          <input
            type="number"
            step={0.01}
            min={0.5}
            max={2}
            value={v.revpar_index_post_reno}
            onChange={e =>
              update({
                revpar_index_post_reno: parseFloat(e.target.value) || 1.0,
              })
            }
            className="w-full text-[12.5px] border border-border rounded px-2 py-1 tabular-nums"
          />
        </Field>
        <Field label="Occupancy Recovery Months">
          <input
            type="number"
            step={1}
            min={0}
            max={36}
            value={v.occupancy_recovery_months}
            onChange={e =>
              update({
                occupancy_recovery_months: parseInt(e.target.value, 10) || 0,
              })
            }
            className="w-full text-[12.5px] border border-border rounded px-2 py-1 tabular-nums"
          />
        </Field>
      </div>

      <div className="text-[11px] uppercase tracking-wide text-ink-500 font-medium mb-1">
        % Rooms Offline by Month (Y1)
      </div>
      <div className="flex gap-1 overflow-x-auto pb-1">
        {v.pct_rooms_offline_by_month.map((pct, i) => (
          <div key={i} className="flex flex-col items-center min-w-[42px]">
            <span className="text-[9.5px] text-ink-500">M{i + 1}</span>
            <input
              type="number"
              step={0.05}
              min={0}
              max={1}
              value={pct}
              onChange={e => {
                const next = [...v.pct_rooms_offline_by_month];
                next[i] = clamp01(parseFloat(e.target.value) || 0);
                update({ pct_rooms_offline_by_month: next });
              }}
              className={cn(
                'w-full text-[11.5px] border border-border rounded px-1 py-0.5 tabular-nums text-center',
                pct > 0 && 'bg-warn-50 border-warn-500/30',
              )}
            />
          </div>
        ))}
      </div>
      <p className="text-[11px] text-ink-500 mt-2">
        1.0 = 100% closed for that month. Rolling = batches, Full closure
        requires 0.0 or 1.0 only, Wing-by-wing caps at 0.5.
      </p>
    </Card>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-[11px] uppercase tracking-wide text-ink-500 mb-1">
        {label}
      </label>
      {children}
    </div>
  );
}

function clamp01(n: number): number {
  if (n < 0) return 0;
  if (n > 1) return 1;
  return n;
}

function formatCompactDollars(n: number): string {
  const abs = Math.abs(n);
  const sign = n < 0 ? '-' : '';
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${sign}$${Math.round(abs / 1_000)}K`;
  return `${sign}$${Math.round(abs)}`;
}

// keep these exported so the parent page can validate before PATCH.
export const PIP_STRATEGY_LABELS = STRATEGY_LABELS;
export const PIP_BRAND_OPTIONS = BRAND_OPTIONS;

// Suppress unused-import lints for ChevronDown/ChevronUp/X if a future
// revision restores them. Kept imported so the lucide chunk warmup is
// shared with the parent tab.
void ChevronDown;
void ChevronUp;
void X;
