'use client';

/**
 * "What just happened" — small expandable panel that surfaces above the
 * KPI strip after a successful run. Diffs the current engine outputs
 * against the previous snapshot so the user feels the model changing.
 *
 * Auto-collapses 30s after entry. Falls back to nothing when no diffs
 * could be computed (first run, fresh page, no previous snapshot).
 */

import { useEffect, useMemo, useState } from 'react';
import { Check, X } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import {
  EngineName,
  EngineOutputResponse,
  EngineOutputsResponse,
} from '@/lib/api';
import { cn, fmtPct } from '@/lib/format';

interface Diff {
  label: string;
  beforeFmt: string;
  afterFmt: string;
  delta: number; // signed (after - before) / before
  unit: 'pct' | 'mult' | 'currency' | 'raw';
}

interface Props {
  engine: EngineName;
  /** Headline shown after "✓ {Engine} engine ran". */
  engineLabel: string;
  outputs: EngineOutputsResponse | null;
  previous: EngineOutputsResponse | null;
  /** Bumps each successful run; used to re-show the panel. */
  runToken?: number | null;
  /** Auto-collapse delay in ms (default 30_000). */
  collapseAfterMs?: number;
}

const FIELDS_BY_ENGINE: Partial<
  Record<
    EngineName,
    { key: string; label: string; unit: Diff['unit'] }[]
  >
> = {
  returns: [
    { key: 'levered_irr', label: 'Levered IRR', unit: 'pct' },
    { key: 'unlevered_irr', label: 'Unlevered IRR', unit: 'pct' },
    { key: 'equity_multiple', label: 'Equity Multiple', unit: 'mult' },
    { key: 'cash_on_cash_year_one', label: 'Y1 Cash-on-Cash', unit: 'pct' },
    { key: 'gross_sale_price', label: 'Exit Value', unit: 'currency' },
  ],
  debt: [
    { key: 'loan_amount', label: 'Loan Amount', unit: 'currency' },
    { key: 'year_one_dscr', label: 'DSCR', unit: 'mult' },
    { key: 'year_one_debt_yield', label: 'Debt Yield', unit: 'pct' },
  ],
  capital: [
    { key: 'equity_amount', label: 'Equity', unit: 'currency' },
    { key: 'debt_amount', label: 'Debt', unit: 'currency' },
    { key: 'total_capital', label: 'Total Capital', unit: 'currency' },
    { key: 'ltc', label: 'LTC', unit: 'pct' },
  ],
  partnership: [
    { key: 'gp_irr', label: 'GP IRR', unit: 'pct' },
    { key: 'lp_irr', label: 'LP IRR', unit: 'pct' },
    { key: 'promote_amount', label: 'GP Promote', unit: 'currency' },
  ],
  expense: [
    { key: 'noi', label: 'NOI', unit: 'currency' },
    { key: 'noi_cagr', label: 'NOI CAGR', unit: 'pct' },
  ],
};

export default function WhatJustHappened({
  engine,
  engineLabel,
  outputs,
  previous,
  runToken,
  collapseAfterMs = 30_000,
}: Props) {
  const [open, setOpen] = useState(true);

  const diffs = useMemo<Diff[]>(() => {
    const fields = FIELDS_BY_ENGINE[engine];
    if (!fields || !outputs) return [];
    const cur = outputs.engines?.[engine]?.outputs as
      | Record<string, unknown>
      | undefined;
    const prev = previous?.engines?.[engine]?.outputs as
      | Record<string, unknown>
      | undefined;
    if (!cur || !prev) return [];
    const out: Diff[] = [];
    for (const f of fields) {
      const a = toNumber(prev[f.key]);
      const b = toNumber(cur[f.key]);
      if (a == null || b == null) continue;
      if (Math.abs(b - a) < 1e-9) continue;
      const delta = a === 0 ? 0 : (b - a) / Math.abs(a);
      out.push({
        label: f.label,
        beforeFmt: formatValue(a, f.unit),
        afterFmt: formatValue(b, f.unit),
        delta,
        unit: f.unit,
      });
    }
    return out;
  }, [engine, outputs, previous]);

  // Re-open + restart the auto-collapse timer on each successful run.
  useEffect(() => {
    if (runToken == null) return;
    setOpen(true);
    const t = setTimeout(() => setOpen(false), collapseAfterMs);
    return () => clearTimeout(t);
  }, [runToken, collapseAfterMs]);

  if (!open || diffs.length === 0) return null;

  return (
    <Card
      tone="luxe"
      className="p-4 mb-5 fade-in-up"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <Check size={14} className="text-success-700" />
          <div className="text-[13px] font-semibold text-ink-900">
            {engineLabel} engine ran
          </div>
          <span className="text-[11.5px] text-ink-500">
            — {diffs.length} of {FIELDS_BY_ENGINE[engine]?.length ?? diffs.length}{' '}
            outputs changed
          </span>
        </div>
        <button
          onClick={() => setOpen(false)}
          className="text-[11px] text-ink-500 hover:text-ink-900 inline-flex items-center gap-1"
          type="button"
        >
          <X size={11} /> Hide
        </button>
      </div>
      <ul className="mt-3 space-y-1.5 text-[12px]">
        {diffs.map((d) => (
          <li key={d.label} className="flex items-center gap-3">
            <span className="text-ink-500 w-32 flex-shrink-0">{d.label}</span>
            <span className="tabular-nums text-ink-700">{d.beforeFmt}</span>
            <span className="text-ink-300">→</span>
            <span className="tabular-nums font-medium text-ink-900">
              {d.afterFmt}
            </span>
            <span
              className={cn(
                'tabular-nums text-[11px] font-medium',
                d.delta > 0 ? 'text-success-700' : 'text-danger-700',
              )}
            >
              ({d.delta >= 0 ? '+' : ''}
              {fmtPct(d.delta, 1)})
            </span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function toNumber(v: unknown): number | null {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (typeof v === 'string') {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

function formatValue(v: number, unit: Diff['unit']): string {
  if (unit === 'pct') return `${(v * 100).toFixed(2)}%`;
  if (unit === 'mult') return `${v.toFixed(2)}x`;
  if (unit === 'currency') {
    if (Math.abs(v) >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
    if (Math.abs(v) >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
    return `$${v.toFixed(0)}`;
  }
  return v.toFixed(2);
}
