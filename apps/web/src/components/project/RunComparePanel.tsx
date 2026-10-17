'use client';

/**
 * Compare runs — regression baselines (Sam, 2026-10-16).
 *
 * Engine runs are append-only, so every earlier run on the deal is a
 * preserved baseline. This panel picks a baseline run (default: the newest
 * complete, full-chain, non-scenario run completed BEFORE the chosen UTC
 * date — prefilled with the last deploy before Sam's QA changes) and a head
 * run (default: latest canonical), then lists every headline field that
 * moved, grouped by engine, with absolute and % deltas and each side's
 * provenance. Export writes the same table to .xlsx via the worker.
 *
 * Read-only: nothing here runs or edits an engine.
 */

import { useCallback, useEffect, useState } from 'react';
import { Download, GitCompare } from 'lucide-react';
import { api, WorkerError } from '@/lib/api';
import type { EngineRunListResponse, RunCompareResponse } from '@/lib/api';
import {
  DEFAULT_BASELINE_CUTOFF,
  cutoffToIso,
  fmtDeltaAbs,
  fmtDeltaPct,
  fmtValue,
  groupFields,
  provenanceText,
  runLabel,
} from '@/lib/runCompare';

interface Props {
  dealId: string;
}

function errText(err: unknown): string {
  return err instanceof WorkerError
    ? `${err.message}${err.body ? `: ${err.body}` : ''}`
    : (err as Error).message;
}

export default function RunComparePanel({ dealId }: Props) {
  const [cutoff, setCutoff] = useState(DEFAULT_BASELINE_CUTOFF);
  const [runs, setRuns] = useState<EngineRunListResponse | null>(null);
  const [base, setBase] = useState<string>('');
  const [head, setHead] = useState<string>('latest');
  const [showAll, setShowAll] = useState(false);
  const [result, setResult] = useState<RunCompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Runs + the default baseline for the chosen cutoff.
  useEffect(() => {
    const ctl = new AbortController();
    setError(null);
    api.engines
      .runs(dealId, cutoff ? cutoffToIso(cutoff) : undefined, ctl.signal)
      .then((res) => {
        setRuns(res);
        setBase(res.baseline_run_id ?? '');
      })
      .catch((err) => {
        if ((err as Error).name === 'AbortError') return;
        setError(errText(err));
      });
    return () => ctl.abort();
  }, [dealId, cutoff]);

  const runCompare = useCallback(
    async (signal?: AbortSignal) => {
      if (!base) {
        setResult(null);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const res = await api.engines.compare(dealId, base, head, {
          all: showAll,
          signal,
        });
        setResult(res);
      } catch (err) {
        if ((err as Error).name === 'AbortError') return;
        setError(errText(err));
      } finally {
        setLoading(false);
      }
    },
    [dealId, base, head, showAll],
  );

  useEffect(() => {
    const ctl = new AbortController();
    void runCompare(ctl.signal);
    return () => ctl.abort();
  }, [runCompare]);

  const onExport = async () => {
    if (!result) return;
    setExporting(true);
    try {
      await api.engines.downloadCompareXlsx(
        dealId,
        result.base.run_id,
        result.head.run_id,
        `fondok-run-compare-${result.base.run_id.slice(0, 8)}-vs-${result.head.run_id.slice(0, 8)}.xlsx`,
        showAll,
      );
    } catch (err) {
      setError(errText(err));
    } finally {
      setExporting(false);
    }
  };

  const runList = runs?.runs ?? [];
  const groups = result ? groupFields(result.fields) : [];

  return (
    <section
      className="mb-6 rounded-md border border-border bg-white p-4"
      data-testid="run-compare-panel"
      aria-label="Compare runs"
    >
      <div className="flex items-center gap-2">
        <GitCompare size={14} className="text-ink-500" />
        <h3 className="text-[13px] font-semibold text-ink-900">Compare runs</h3>
        <span className="text-[11.5px] text-ink-500">
          Headline outputs of a baseline run vs. a later run. Every run is kept, so
          any earlier run can serve as the baseline.
        </span>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-[auto_1fr_1fr_auto] md:items-end">
        <label className="flex flex-col gap-1 text-[11.5px] text-ink-500">
          Baseline = newest run before (UTC)
          <input
            type="datetime-local"
            value={cutoff}
            onChange={(e) => setCutoff(e.target.value)}
            className="rounded-md border border-border px-2 py-1 text-[12px] text-ink-900"
            data-testid="run-compare-cutoff"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11.5px] text-ink-500">
          Baseline run
          <select
            value={base}
            onChange={(e) => setBase(e.target.value)}
            className="rounded-md border border-border px-2 py-1 text-[12px] text-ink-900"
            data-testid="run-compare-base"
          >
            <option value="">
              {runs && !runs.baseline_run_id
                ? '— no complete run before this date'
                : 'Select a run'}
            </option>
            {runList.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {runLabel(r)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-[11.5px] text-ink-500">
          Head run
          <select
            value={head}
            onChange={(e) => setHead(e.target.value)}
            className="rounded-md border border-border px-2 py-1 text-[12px] text-ink-900"
            data-testid="run-compare-head"
          >
            <option value="latest">Latest (canonical)</option>
            {runList.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {runLabel(r)}
              </option>
            ))}
          </select>
        </label>
        <div className="flex items-center gap-3">
          <label className="inline-flex items-center gap-1 text-[11.5px] text-ink-700">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
            />
            Show unchanged
          </label>
          <button
            type="button"
            onClick={onExport}
            disabled={!result || exporting}
            className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-1 text-[12px] text-ink-900 hover:bg-ink-50 disabled:opacity-50"
            data-testid="run-compare-export"
          >
            <Download size={12} /> {exporting ? 'Exporting…' : 'Export .xlsx'}
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-3 rounded-md border border-warn-200 bg-warn-50 p-3 text-[12px] text-warn-900">
          Couldn&apos;t compare runs: {error}
        </div>
      )}

      {!base && runs && !error && (
        <p className="mt-3 text-[12px] text-ink-500">
          {runList.length === 0
            ? 'This deal has no engine runs yet.'
            : 'Pick a baseline run, or move the date to one after the deal’s first complete run.'}
        </p>
      )}

      {result && !error && (
        <div className="mt-4">
          <div className="mb-2 text-[11.5px] text-ink-500 tabular-nums" data-testid="run-compare-summary">
            {loading
              ? 'Comparing…'
              : `${result.fields_changed} of ${result.fields_compared} headline fields changed · baseline ${result.base.run_id.slice(0, 8)} → head ${result.head.run_id.slice(0, 8)}`}
          </div>
          {result.fields.length === 0 ? (
            <p className="text-[12px] text-ink-700" data-testid="run-compare-empty">
              No headline field differs between these two runs.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]" data-testid="run-compare-table">
                <thead>
                  <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-ink-500">
                    <th className="py-1 pr-3">Field</th>
                    <th className="py-1 pr-3 text-right">Baseline</th>
                    <th className="py-1 pr-3 text-right">Head</th>
                    <th className="py-1 pr-3 text-right">Δ</th>
                    <th className="py-1 pr-3 text-right">Δ %</th>
                    <th className="py-1">Source (baseline → head)</th>
                  </tr>
                </thead>
                {groups.map((g) => (
                  <tbody key={g.group}>
                    <tr className="bg-ink-50">
                      <td colSpan={6} className="py-1 px-1 text-[11px] font-semibold uppercase tracking-wide text-ink-700">
                        {g.group}
                      </td>
                    </tr>
                    {g.rows.map((f) => {
                      const bs = provenanceText(f.base_provenance);
                      const hs = provenanceText(f.head_provenance);
                      return (
                        <tr key={f.key} className="border-b border-border/60" data-testid={`run-compare-row-${f.key}`}>
                          <td className="py-1 pr-3 text-ink-900">
                            {f.label}
                            {f.status !== 'changed' && (
                              <span className="ml-1 text-[10.5px] text-ink-500">({f.status})</span>
                            )}
                          </td>
                          <td className="py-1 pr-3 text-right tabular-nums">{fmtValue(f.base_value, f.unit)}</td>
                          <td className="py-1 pr-3 text-right tabular-nums">{fmtValue(f.head_value, f.unit)}</td>
                          <td className="py-1 pr-3 text-right tabular-nums">{fmtDeltaAbs(f.delta_abs, f.unit)}</td>
                          <td
                            className="py-1 pr-3 text-right tabular-nums"
                            title={f.delta_pct === null && f.delta_reason ? f.delta_reason : undefined}
                          >
                            {fmtDeltaPct(f.delta_pct)}
                            {f.delta_pct === null && f.delta_reason && (
                              <span className="ml-1 text-[10.5px] text-ink-500">{f.delta_reason}</span>
                            )}
                          </td>
                          <td className="py-1 text-[11px] text-ink-500">
                            {bs || hs
                              ? bs === hs
                                ? bs
                                : `${bs || '—'} → ${hs || '—'}`
                              : '— engine records no provenance for this field'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                ))}
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
