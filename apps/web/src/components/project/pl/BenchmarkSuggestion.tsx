'use client';

/**
 * FON-109 — "Apply benchmark": a CBRE / HotStats ratio is a SUGGESTION.
 *
 * Sam (2026-10-15): "implement the change before this re-test so CBRE/HotStats
 * benchmarks are suggestions that require analyst approval rather than
 * automatically flowing into the model."
 *
 * The worker no longer applies a P&L benchmark to engine input. It serves,
 * per assumption (rooms / F&B departmental ratio, management fee, FF&E
 * reserve), the benchmark's ratio with its line, peer-set size, document and
 * page beside the value in use (``GET /deals/{id}/benchmark-suggestions``).
 * This module fetches that once per override state and renders the per-line
 * action: "Apply benchmark" opens the justification prefilled with
 * "Applied CBRE benchmark <line> <value> from <doc> p.<n>" — editable — and
 * Save writes the ordinary ``field_overrides`` entry and re-runs the model
 * (the host's ``apply``). An applied suggestion then reads as an analyst
 * override carrying that note. No suggestion → nothing renders.
 */

import { createContext, useContext, useEffect, useState } from 'react';
import { InlineEditControls } from '@/components/design';
import { api, isWorkerConnected, type BenchmarkSuggestion } from '@/lib/api';

/** Future P&L benchmark category key → the override key its suggestion writes. */
export const CATEGORY_TO_SUGGESTION_KEY: Readonly<Record<string, string>> = {
  rooms: 'rooms_dept_pct',
  food_beverage: 'fb_dept_pct',
  mgmt_fee: 'mgmt_fee_pct',
};

/** Source ids → the words the action uses for "what the model is on now". */
const CURRENT_SOURCE_LABEL: Readonly<Record<string, string>> = {
  analyst_override: 'analyst override',
  portfolio_pnl: 'portfolio P&L',
  seed: 'platform default',
};

export function fmtSuggestionPct(fraction: number | null | undefined): string {
  if (fraction == null || !Number.isFinite(fraction)) return '—';
  const s = (fraction * 100).toFixed(2).replace(/0+$/, '').replace(/\.$/, '');
  return `${s}%`;
}

export function currentSourceLabel(source: string | null | undefined): string {
  if (!source) return 'source not stated';
  return CURRENT_SOURCE_LABEL[source] ?? source.replace(/_/g, ' ');
}

// ── Fetch ────────────────────────────────────────────────────────────────

export interface BenchmarkSuggestionsState {
  byKey: ReadonlyMap<string, BenchmarkSuggestion>;
  settled: boolean;
}

const EMPTY: ReadonlyMap<string, BenchmarkSuggestion> = new Map();

/**
 * The deal's benchmark suggestions. ``version`` is anything that changes when
 * the deal's overrides change (the host passes its ``field_overrides``
 * object), so an applied suggestion re-reads as applied. A failed read, an
 * offline fixture or a worker without the route resolve to no suggestions —
 * never a placeholder.
 */
export function useBenchmarkSuggestions(dealId: string, version: unknown): BenchmarkSuggestionsState {
  const [state, setState] = useState<BenchmarkSuggestionsState>({ byKey: EMPTY, settled: false });
  const liveMode = isWorkerConnected() && !!dealId && !/^\d+$/.test(dealId);
  // Keyed on the CONTENT, not the object identity — a host that rebuilds an
  // equal overrides object every render must not trigger a refetch loop.
  let versionKey: string;
  try {
    versionKey = JSON.stringify(version ?? null);
  } catch {
    versionKey = String(version);
  }
  useEffect(() => {
    const fetchSuggestions = api?.deals?.benchmarkSuggestions;
    if (!liveMode || typeof fetchSuggestions !== 'function') {
      setState({ byKey: EMPTY, settled: true });
      return;
    }
    const ctrl = new AbortController();
    let cancelled = false;
    Promise.resolve(fetchSuggestions(dealId, ctrl.signal))
      .then((json) => {
        if (cancelled) return;
        const list = Array.isArray(json?.suggestions) ? json.suggestions : [];
        const valid = list.filter(
          (s) => s && typeof s.key === 'string' && typeof s.suggested_value === 'number' && Number.isFinite(s.suggested_value),
        );
        setState({ byKey: new Map(valid.map((s) => [s.key, s])), settled: true });
      })
      .catch(() => {
        if (!cancelled) setState({ byKey: EMPTY, settled: true });
      });
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [dealId, liveMode, versionKey]);
  return state;
}

// ── Context ──────────────────────────────────────────────────────────────

export interface BenchmarkSuggestionCtx {
  byKey: ReadonlyMap<string, BenchmarkSuggestion>;
  /** Write the override (value + the analyst's note) and re-run the model. */
  apply: (key: string, value: number, note: string) => Promise<void>;
  running: boolean;
}

export const BenchmarkSuggestionContext = createContext<BenchmarkSuggestionCtx | null>(null);

export function useBenchmarkSuggestion(key: string | undefined): {
  suggestion: BenchmarkSuggestion | null;
  ctx: BenchmarkSuggestionCtx | null;
} {
  const ctx = useContext(BenchmarkSuggestionContext);
  const suggestion = ctx && key ? ctx.byKey.get(key) ?? null : null;
  return { suggestion, ctx };
}

// ── The action ───────────────────────────────────────────────────────────

/**
 * One suggestion's action. Renders nothing without a suggestion for
 * ``overrideKey``. ``compact`` is the in-table form (button only until it
 * opens); the Assumptions panel shows the suggestion line beside the input.
 */
export function ApplyBenchmarkAction({
  overrideKey,
  compact = false,
  label,
}: {
  overrideKey: string;
  compact?: boolean;
  /** Names the assumption when no input sits beside the action (FF&E reserve). */
  label?: string;
}) {
  const { suggestion: s, ctx } = useBenchmarkSuggestion(overrideKey);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState('');
  const [saving, setSaving] = useState(false);
  if (!s || !ctx) return null;

  const value = fmtSuggestionPct(s.suggested_value);
  const where = `${s.filename ?? 'the uploaded P&L benchmark report'}${s.page != null ? ` p.${s.page}` : ' (page not stated)'}`;
  const peer = s.peer_set_size != null ? `${s.peer_set_size} hotels` : 'peer-set size not stated';
  const current = `In use: ${fmtSuggestionPct(s.current_value)} (${currentSourceLabel(s.current_source)})`;
  const t12 = s.year1_from_t12
    ? ' Year 1 uses the T-12 amount as stated; the ratio drives the later years.'
    : '';
  const title =
    `CBRE / HotStats benchmark ${s.benchmark_line_label ?? s.label} ${value} — line "${s.benchmark_line ?? '—'}", ${peer}, ${where}.\n` +
    `${current}.${t12}\nThe benchmark changes the model only when you apply it.`;

  if (s.applied) {
    return (
      <span
        data-testid={`benchmark-applied-${overrideKey}`}
        title={`Applied as an analyst override.${s.current_note ? `\nNote: ${s.current_note}` : ''}`}
        style={{
          display: 'inline-block', marginTop: compact ? 2 : 0, fontSize: 10, fontWeight: 700,
          color: '#2f4a8c', background: 'rgba(47,74,140,.10)', borderRadius: 4, padding: '1px 5px',
          whiteSpace: 'nowrap',
        }}
      >
        {label ? `${label}: ` : ''}Benchmark {value} applied · analyst override
      </span>
    );
  }

  const start = () => {
    setNote(s.suggested_note);
    setOpen(true);
  };
  const cancel = () => {
    setOpen(false);
    setNote('');
  };
  const save = async () => {
    if (!note.trim() || saving) return;
    setSaving(true);
    try {
      await ctx.apply(s.key, s.suggested_value, note.trim());
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <span
      data-testid={`benchmark-suggestion-${overrideKey}`}
      style={{ display: 'inline-flex', flexDirection: 'column', alignItems: compact ? 'flex-end' : 'stretch', gap: 4, textAlign: 'left' }}
    >
      {!compact && (
        <span style={{ fontSize: 11, color: '#6b6f76', lineHeight: 1.4 }} title={title}>
          {label && <strong style={{ color: '#1a2233', fontWeight: 600 }}>{label}: </strong>}
          Benchmark {value} · {s.filename ?? 'P&L benchmark'}{s.page != null ? ` p.${s.page}` : ''} · {current}
        </span>
      )}
      {!open ? (
        <button
          type="button"
          data-testid={`apply-benchmark-${overrideKey}`}
          disabled={ctx.running}
          title={title}
          onClick={start}
          style={{
            alignSelf: compact ? 'flex-end' : 'flex-start', fontSize: 10.5, fontWeight: 600, color: '#2f4a8c',
            background: 'none', border: '1px solid rgba(47,74,140,.35)', borderRadius: 5, padding: '1px 6px',
            cursor: ctx.running ? 'default' : 'pointer', fontFamily: 'inherit', whiteSpace: 'nowrap',
          }}
        >
          Apply benchmark {value}
        </button>
      ) : (
        <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 4, width: 240 }}>
          <span style={{ fontSize: 10.5, color: '#6b6f76', lineHeight: 1.4 }}>
            Sets {s.label} to {value} as an analyst override and re-runs the model. Edit the note if needed.
          </span>
          <InlineEditControls
            onSave={() => void save()}
            onCancel={cancel}
            saving={saving}
            saveLabel="Apply"
            saveTestId={`apply-benchmark-save-${overrideKey}`}
            note={note}
            onNote={setNote}
            noteTestId={`apply-benchmark-note-${overrideKey}`}
            noteLabel={`${s.label} — benchmark override justification`}
          />
        </span>
      )}
    </span>
  );
}
