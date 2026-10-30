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
 *
 * E-023 (FON-63): for validation failures the worker now writes the sentence
 * itself — "Debt: NOI for year 1 is −$4,879,453, below the $0 minimum …" —
 * with the raw pydantic text after "Technical detail:". Those are shown
 * VERBATIM; the keyword heuristics in `humanizeEngineError` apply only to
 * errors the worker did not already phrase.
 *
 * Go-live Wave 0 (D4): the run / engine-outputs payload also carries a
 * per-engine failure list, `failures: [{engine, reason, message}]`. The banner
 * merges it with the failed rows (one entry per engine — the list's reason and
 * message win) and names the reason beside the engine, so Returns, Cash Flow
 * and Scenario Analysis can render it in place of blank or zero figures.
 */

import { AlertTriangle, RefreshCw, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import type { EngineFailure, EngineName, EngineOutputsResponse } from '@/lib/api';
import { useEngineRun } from '@/lib/hooks/useEngineRun';
import { ENGINE_MODEL_LABEL } from '@/lib/fieldLabels';

// One registry of engine → human name, shared with the stale-run banner and
// every other surface that names a model (lib/fieldLabels).
const ENGINE_LABEL: Record<string, string> = ENGINE_MODEL_LABEL;

/**
 * Engine names the worker writes at the head of a runner-formatted error
 * ("Debt: …"). Mirror of `_ENGINE_LABELS` in
 * apps/worker/app/services/engine_runner.py — keep the two in sync.
 */
const RUNNER_ENGINE_LABELS = [
  'Revenue',
  'F&B',
  'Expense',
  'Capital',
  'Debt',
  'Returns',
  'Sensitivity',
  'Partnership',
  'Cash Flow',
] as const;

const escapeRegExp = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const RUNNER_LABEL_PREFIX = new RegExp(
  `^(${RUNNER_ENGINE_LABELS.map(escapeRegExp).join('|')}): `,
);
/** Literal the worker puts between the human sentence and the raw text. */
const TECHNICAL_DETAIL_MARKER = 'Technical detail:';

export interface HumanizedEngineError {
  /** What the analyst reads inline. Verbatim when the worker phrased it. */
  message: string;
  /** What goes under the "Technical detail" disclosure; null when there is none. */
  technical: string | null;
  /** True when `message` came from the worker's plain-language formatter. */
  runnerFormatted: boolean;
}

const FALLBACK_MESSAGE = 'The model hit an unexpected error.';

/** Turn a raw engine error into something an analyst can act on. */
export function humanizeEngineError(raw: string | null | undefined): HumanizedEngineError {
  if (!raw) return { message: FALLBACK_MESSAGE, technical: null, runnerFormatted: false };

  // Runner-formatted (E-023): "<Engine>: <sentence>\n\nTechnical detail: <raw>".
  // Show the sentence verbatim; everything after the marker is the detail.
  const markerAt = raw.indexOf(TECHNICAL_DETAIL_MARKER);
  if (markerAt >= 0 || RUNNER_LABEL_PREFIX.test(raw)) {
    const message = (markerAt >= 0 ? raw.slice(0, markerAt) : raw).trim();
    const technical =
      markerAt >= 0 ? raw.slice(markerAt + TECHNICAL_DETAIL_MARKER.length).trim() : '';
    return {
      message: message || FALLBACK_MESSAGE,
      technical: technical.length > 0 ? technical : null,
      runnerFormatted: true,
    };
  }

  // Legacy heuristics — only for errors the worker did not phrase itself.
  const r = raw.toLowerCase();
  let message: string;
  if (r.includes('greater_than_equal') || r.includes('validation error')) {
    message =
      'The model computed a value outside its expected range — usually a deeply negative-return (underwater) scenario. Re-run to recompute with the loosened guard.';
  } else if (r.includes('division') || r.includes('zero') || r.includes('divide')) {
    message =
      'A required input was zero (e.g. equity, key count, or a revenue base). Check the deal’s purchase price, key count, and financing, then re-run.';
  } else if (
    r.includes('missing') ||
    r.includes('required') ||
    r.includes('none') ||
    r.includes('null')
  ) {
    message =
      'A required input was missing. Upload the outstanding financials or set the assumption, then re-run.';
  } else {
    message = raw.length > 220 ? `${raw.slice(0, 220)}…` : raw;
  }
  return { message, technical: raw, runnerFormatted: false };
}

/** A failed engine as the banner renders it: merged from the failure list
 *  and the failed rows. `reason` is null for a row the list does not name. */
export interface EngineFailureEntry {
  engine: string;
  reason: string | null;
  /** The raw message / error text, humanised at render time. */
  raw: string | null;
}

/**
 * Every engine that did not complete on this snapshot — from the worker's
 * `failures` list first, then any `status: 'failed'` row the list does not
 * already cover. Empty when every engine is healthy.
 */
export function engineFailures(outputs: EngineOutputsResponse | null): EngineFailureEntry[] {
  if (!outputs) return [];
  const byEngine = new Map<string, EngineFailureEntry>();
  for (const f of (outputs.failures ?? []) as EngineFailure[]) {
    if (!f || typeof f.engine !== 'string' || !f.engine) continue;
    byEngine.set(f.engine, {
      engine: f.engine,
      reason: typeof f.reason === 'string' && f.reason.trim() ? f.reason.trim() : null,
      raw: typeof f.message === 'string' && f.message.trim() ? f.message : null,
    });
  }
  for (const [engine, row] of Object.entries(outputs.engines ?? {}) as [
    EngineName,
    EngineOutputsResponse['engines'][EngineName],
  ][]) {
    if (row?.status !== 'failed' || byEngine.has(engine)) continue;
    byEngine.set(engine, { engine, reason: null, raw: row.error ?? null });
  }
  return [...byEngine.values()];
}

/** `missing_input` → "missing input" — the stable code, readable. */
const humanizeReason = (reason: string) => reason.replace(/[_-]+/g, ' ').trim();

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

  const failed = engineFailures(outputs);

  if (failed.length === 0) return null;

  return (
    <Card className="p-4 mb-5 border-danger-500/40 bg-danger-50" role="alert" data-testid="engine-failures-banner">
      <div className="flex items-start gap-3">
        <AlertTriangle size={18} className="text-danger-700 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-[13.5px] font-semibold text-danger-700">
            {failed.length === 1
              ? `The ${ENGINE_LABEL[failed[0].engine] ?? failed[0].engine} model didn’t finish`
              : `${failed.length} models didn’t finish`}
          </div>
          <p className="text-[12px] text-danger-700/80 mt-0.5">
            These numbers are showing as “—” because the model errored, not because data is missing.
          </p>
          <ul className="mt-2 space-y-2">
            {failed.map(({ engine, reason, raw }) => {
              const { message, technical, runnerFormatted } = humanizeEngineError(raw);
              // A runner-formatted sentence already opens with its own
              // "<Engine>: " — bold that opener instead of prefixing the row
              // label a second time ("Debt: Debt: …"). The text stays verbatim.
              const opener = runnerFormatted ? RUNNER_LABEL_PREFIX.exec(message) : null;
              const lead = opener ? `${opener[1]}:` : `${ENGINE_LABEL[engine] ?? engine}:`;
              const body = opener ? message.slice(opener[0].length) : message;
              return (
                <li key={engine} className="text-[12px]" data-testid={`engine-failure-${engine}`}>
                  <span className="font-semibold text-danger-700" data-testid="engine-failure-lead">
                    {lead}
                  </span>{' '}
                  <span className="text-danger-700/90" data-testid="engine-failure-message">
                    {body}
                  </span>
                  {reason && (
                    <span
                      className="ml-2 inline-flex items-center rounded-sm border border-danger-500/30 bg-white/60 px-1.5 py-0.5 text-[10.5px] text-danger-700/80"
                      data-testid="engine-failure-reason"
                      title={`Reason code: ${reason}`}
                    >
                      reason: {humanizeReason(reason)}
                    </span>
                  )}
                  {technical && (
                    <details className="mt-1">
                      <summary className="text-[11px] text-danger-700/60 cursor-pointer select-none">
                        Technical detail
                      </summary>
                      <pre
                        className="mt-1 text-[10.5px] text-danger-700/70 whitespace-pre-wrap break-words"
                        data-testid="engine-failure-technical"
                      >
                        {technical}
                      </pre>
                    </details>
                  )}
                </li>
              );
            })}
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
