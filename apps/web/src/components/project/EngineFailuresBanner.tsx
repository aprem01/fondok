'use client';

/**
 * EngineFailuresBanner — makes engine failures LOUD instead of silent.
 *
 * When an engine errors, the tabs that read its output just render dashes,
 * which reads identically to "not run yet" — the analyst has no idea a model
 * crashed or why. This banner reads the per-engine status/error the worker
 * already returns (EngineOutputResponse.status/error) and, when any engine is
 * `failed`, surfaces a prominent, plain-language explanation with a one-click
 * Re-run. Renders nothing when every engine is healthy.
 */

import { AlertTriangle, RefreshCw, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import type { EngineName, EngineOutputsResponse } from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';

const ENGINE_LABEL: Record<string, string> = {
  returns: 'Returns',
  debt: 'Debt',
  revenue: 'Revenue',
  expense: 'Expense / NOI',
  fb: 'F&B revenue',
  capital: 'Sources & Uses',
  partnership: 'Partnership waterfall',
  sensitivity: 'Sensitivity',
  cashflow: 'Cash flow',
};

/** Turn a raw engine error into something an analyst can act on. */
function humanizeEngineError(raw: string | null | undefined): string {
  if (!raw) return 'The model hit an unexpected error.';
  const r = raw.toLowerCase();
  if (r.includes('greater_than_equal') || r.includes('validation error')) {
    return 'The model computed a value outside its expected range — usually a deeply negative-return (underwater) scenario. Re-run to recompute with the loosened guard.';
  }
  if (r.includes('division') || r.includes('zero') || r.includes('divide')) {
    return 'A required input was zero (e.g. equity, key count, or a revenue base). Check the deal’s purchase price, key count, and financing, then re-run.';
  }
  if (r.includes('missing') || r.includes('required') || r.includes('none') || r.includes('null')) {
    return 'A required input was missing. Upload the outstanding financials or set the assumption, then re-run.';
  }
  return raw.length > 220 ? `${raw.slice(0, 220)}…` : raw;
}

export function EngineFailuresBanner({
  outputs,
  dealId,
}: {
  outputs: EngineOutputsResponse | null;
  dealId: string;
}) {
  // Re-run the full chain (run-all) so dependent engines recompute in order.
  const { run, status } = useEngineRun(dealId, 'returns', { runMode: 'all' });
  const running = status === 'running' || status === 'queued';

  const failed = Object.entries(outputs?.engines ?? {}).filter(
    ([, row]) => row?.status === 'failed',
  ) as [EngineName, EngineOutputsResponse['engines'][EngineName]][];

  if (failed.length === 0) return null;

  return (
    <Card className="p-4 mb-5 border-danger-500/40 bg-danger-50" role="alert">
      <div className="flex items-start gap-3">
        <AlertTriangle size={18} className="text-danger-700 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-[13.5px] font-semibold text-danger-700">
            {failed.length === 1
              ? `The ${ENGINE_LABEL[failed[0][0]] ?? failed[0][0]} model didn’t finish`
              : `${failed.length} models didn’t finish`}
          </div>
          <p className="text-[12px] text-danger-700/80 mt-0.5">
            These numbers are showing as “—” because the model errored, not because data is missing.
          </p>
          <ul className="mt-2 space-y-2">
            {failed.map(([engine, row]) => (
              <li key={engine} className="text-[12px]">
                <span className="font-semibold text-danger-700">
                  {ENGINE_LABEL[engine] ?? engine}:
                </span>{' '}
                <span className="text-danger-700/90">{humanizeEngineError(row?.error)}</span>
                {row?.error && (
                  <details className="mt-1">
                    <summary className="text-[11px] text-danger-700/60 cursor-pointer select-none">
                      Technical detail
                    </summary>
                    <pre className="mt-1 text-[10.5px] text-danger-700/70 whitespace-pre-wrap break-words">
                      {row.error}
                    </pre>
                  </details>
                )}
              </li>
            ))}
          </ul>
        </div>
        <button
          type="button"
          onClick={() => { void run(); }}
          disabled={running}
          className="inline-flex items-center gap-1.5 rounded-md bg-danger-700 px-3 py-1.5 text-[12px] font-medium text-white hover:bg-danger-800 disabled:opacity-60 flex-shrink-0"
        >
          {running ? <Loader2 size={12} className="animate-spin" /> : <RefreshCw size={12} />}
          {running ? 'Re-running…' : 'Re-run models'}
        </button>
      </div>
    </Card>
  );
}
