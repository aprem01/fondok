/**
 * IC Memo — the narrative and the figures describe ONE underwriting (FON-54).
 *
 * Sam, 2026-09-14: "After the Base Case changes, the model-driven IC Memo
 * figures update, but previously generated AI content can remain based on the
 * old underwriting. Regenerating updated the Investment Thesis to the current
 * 32.2% IRR / 3.44x EM, but the Key Highlights still referenced the old -2.5%
 * IRR / 0.88x EM / 0.78x DSCR."
 *
 * Two causes, both in `ICMemoTab.tsx`:
 *   1. `regenThesis` regenerated the THESIS ONLY — highlights and risks had no
 *      regenerate path at all;
 *   2. the FIRST list interaction (`commitList`) snapshotted the whole
 *      generated draft into `field_overrides.memo_highlights`, where
 *      `highlights ?? rec.highlights` served it for ever, with nothing
 *      recording which run had produced it.
 *
 * This file pins the contract that replaced them:
 *   • one Regenerate redrafts thesis + highlights + risks together, in a
 *     single persisted patch, all stamped with the same run;
 *   • prose that cannot be shown to match the current run is FLAGGED — with
 *     the shipped `stale_run` vocabulary — rather than presented as current;
 *   • prose the analyst wrote is never replaced without asking;
 *   • regenerating twice writes the same thing twice.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-uuid-1' }),
}));

// The run the figures on screen come from, and the run the stale prose was
// drafted against. FON-73 serves one canonical run id across every engine.
const RUN_NOW = 'run-2026-09-14';
const RUN_THEN = 'run-2026-09-01';

/** Live Base Case — Sam's post-change underwriting: 32.2% IRR, 3.44x EM. */
function outputsFor(runId: string): EngineOutputsResponse {
  const row = (engine: string, out: Record<string, unknown>) => ({
    deal_id: 'deal-uuid-1', engine, status: 'complete', summary: '',
    outputs: out, inputs: {}, error: null, runtime_ms: 1,
    started_at: null, completed_at: null, run_id: runId,
  });
  return {
    deal_id: 'deal-uuid-1',
    engines: {
      capital: row('capital', {
        purchase_price: 34_000_000, price_per_key: 257_576, entry_cap_rate: 0.075,
        total_capital: 43_000_000, equity_amount: 17_000_000, debt_amount: 26_000_000,
        uses: [
          { label: 'Purchase Price', amount: 34_000_000 },
          { label: 'Renovation Budget', amount: 4_620_000 },
        ],
      }),
      returns: row('returns', {
        levered_irr: 0.322, unlevered_irr: 0.145, equity_multiple: 3.44,
        hold_years: 5, gross_sale_price: 52_000_000,
      }),
      expense: row('expense', { years: [{ year: 1, noi: 2_550_000 }] }),
      revenue: row('revenue', {
        years: [{ year: 1, revpar: 204, adr: 280, occupancy: 0.73, total_revenue: 14_000_000 }],
        total_revenue_cagr: 0.03,
      }),
      debt: row('debt', { year_one_dscr: 1.59, year_one_debt_yield: 0.11, interest_rate: 0.0766 }),
    },
  } as unknown as EngineOutputsResponse;
}

// The prose the OLD underwriting produced — the exact figures Sam saw survive.
const OLD_THESIS =
  'At a $34.0M basis ($257.6K/key), the deal underwrites to a -2.5% levered IRR and a '
  + '0.88x equity multiple over a 5-year hold. On the current underwriting the deal does not '
  + 'clear our return or coverage hurdles.';
const OLD_HIGHLIGHTS = [
  { t: 'Levered IRR of -2.5% sits below a 15% target over a 5-year hold.', ai: true },
  { t: 'Equity multiple of 0.88x returns 0.88× invested capital across the hold.', ai: true },
  { t: 'Year-1 DSCR of 0.78x provides thin debt-service coverage.', ai: true },
];
const OLD_RISKS = [
  { t: 'Levered IRR of -2.5% is below our return hurdle — limited margin for underwriting slippage.', ai: true },
  { t: 'Year-1 DSCR of 0.78x is tight; a NOI shortfall would pressure debt service.', ai: true },
];

/** `field_overrides` for a deal whose narrative was drafted against RUN_THEN. */
function staleOverrides(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    memo_thesis: OLD_THESIS,
    memo_thesis_run_id: RUN_THEN,
    memo_highlights: OLD_HIGHLIGHTS,
    memo_highlights_run_id: RUN_THEN,
    memo_risks: OLD_RISKS,
    memo_risks_run_id: RUN_THEN,
    ...extra,
  };
}

const fx = vi.hoisted(() => ({
  fieldOverrides: {} as Record<string, unknown>,
  outputs: null as unknown,
}));

vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: fx.outputs, previous: null, loading: false, settled: true,
      lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});

vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: {
      id: 'deal-uuid-1', name: 'Kimpton Angler', city: 'Miami Beach, FL',
      keys: 132, brand: 'Kimpton', field_overrides: fx.fieldOverrides,
    },
    status: null, loading: false, error: null, fromMock: false, refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({ flags: [], critical: 0, warn: 0, info: 0, note: null, loading: false, error: null }),
}));

const updateSpy = vi.fn(async () => ({ id: 'deal-uuid-1' }));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, update: (...a: unknown[]) => updateSpy(...(a as [])) },
      scenarios: {
        ...actual.api.scenarios,
        list: vi.fn(async () => []),
        compare: vi.fn(async () => ({ deal_id: 'deal-uuid-1', base_scenario_id: null, scenarios: [] })),
      },
    },
  };
});

import ICMemoTab from '@/components/project/ICMemoTab';
import { REASONS } from '@/lib/ontology/reasons.generated';
import type { Project } from '@/lib/mockData';

const PROJECT = { id: 0, name: 'Kimpton Hotel' } as unknown as Project;

/** The `field_overrides` object the Nth (default: last) write persisted. */
function overridesAt(i: number): Record<string, unknown> {
  const call = updateSpy.mock.calls[i] as unknown[];
  const patch = call?.[1] as { field_overrides?: Record<string, unknown> } | undefined;
  return patch?.field_overrides ?? {};
}
const lastOverrides = () => overridesAt(updateSpy.mock.calls.length - 1);

/** Every distinct levered-IRR figure printed anywhere on the memo. */
function leveredIrrsOnScreen(): string[] {
  const text = document.body.textContent ?? '';
  const found = new Set<string>();
  // The lookbehind keeps "Unlevered IRR" out — it is a different figure.
  for (const m of text.matchAll(/(?<![A-Za-z])[Ll]evered IRR(?: of)?\s*(-?\d+\.\d)%/g)) found.add(m[1]);
  for (const m of text.matchAll(/(-?\d+\.\d)% levered IRR/g)) found.add(m[1]);
  return [...found].sort();
}

/** Every distinct equity-multiple figure printed anywhere on the memo. */
function equityMultiplesOnScreen(): string[] {
  const text = document.body.textContent ?? '';
  const found = new Set<string>();
  for (const m of text.matchAll(/[Ee]quity [Mm]ultiple(?: of)?\s*(\d+\.\d\d)x/g)) found.add(m[1]);
  for (const m of text.matchAll(/(\d+\.\d\d)x equity multiple/g)) found.add(m[1]);
  return [...found].sort();
}

const clickRegenerateAll = () => fireEvent.click(screen.getAllByText('Regenerate all')[0]);

beforeEach(() => {
  cleanup();
  updateSpy.mockClear();
  fx.fieldOverrides = {};
  fx.outputs = outputsFor(RUN_NOW);
});

// ───────────────────────────────────────────────────────────────────────────
describe('IC Memo — prose that predates the run is flagged, never presented as current', () => {
  it('flags every section drafted against an earlier run, by name, in the stale_run vocabulary', () => {
    fx.fieldOverrides = staleOverrides();
    render(<ICMemoTab project={PROJECT} />);

    const banner = screen.getByTestId('memo-narrative-stale');
    expect(banner).toHaveTextContent('3 narrative sections cannot be shown to match the current model run');
    // The copy is the shared ontology's, not banner-local prose (FON-75 pattern).
    expect(banner).toHaveTextContent(REASONS.stale_run.explanation);

    const listed = within(screen.getByTestId('memo-narrative-stale-sections'))
      .getAllByRole('listitem')
      .map((li) => li.textContent);
    expect(listed).toEqual([
      'Investment Thesis — drafted against an earlier model run',
      'Key highlights — drafted against an earlier model run',
      'Key risks & considerations — drafted against an earlier model run',
    ]);

    // Each section wears the flag where the reader is actually looking.
    expect(screen.getByTestId('memo-stale-thesis')).toHaveTextContent('Earlier model run');
    expect(screen.getByTestId('memo-stale-highlights')).toHaveTextContent('Earlier model run');
    expect(screen.getByTestId('memo-stale-risks')).toHaveTextContent('Earlier model run');

    // The contradiction Sam reported is real and, until a regenerate, on screen
    // — which is exactly why it has to be disclosed rather than hidden.
    expect(leveredIrrsOnScreen()).toEqual(['-2.5', '32.2']);
  });

  it('says "run not recorded" — not "earlier run" — for prose it cannot date', () => {
    // Legacy rows: prose persisted before the stamps existed. Not provably
    // stale, not provably current; the memo must claim neither.
    fx.fieldOverrides = {
      memo_thesis: OLD_THESIS,
      memo_highlights: OLD_HIGHLIGHTS,
      memo_risks: OLD_RISKS,
    };
    render(<ICMemoTab project={PROJECT} />);

    expect(screen.getByTestId('memo-stale-thesis')).toHaveTextContent('Run not recorded');
    const listed = within(screen.getByTestId('memo-narrative-stale-sections'))
      .getAllByRole('listitem')
      .map((li) => li.textContent);
    expect(listed).toEqual([
      'Investment Thesis — the run it was drafted against was never recorded',
      'Key highlights — the run it was drafted against was never recorded',
      'Key risks & considerations — the run it was drafted against was never recorded',
    ]);
  });

  it('shows no banner on a deal whose narrative is the current run', () => {
    fx.fieldOverrides = staleOverrides({
      memo_thesis_run_id: RUN_NOW,
      memo_highlights_run_id: RUN_NOW,
      memo_risks_run_id: RUN_NOW,
    });
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.queryByTestId('memo-narrative-stale')).toBeNull();
    expect(screen.queryByTestId('memo-stale-highlights')).toBeNull();
  });

  it('shows no banner on a deal with no persisted prose — the draft IS this run', () => {
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.queryByTestId('memo-narrative-stale')).toBeNull();
    expect(leveredIrrsOnScreen()).toEqual(['32.2']);
    expect(equityMultiplesOnScreen()).toEqual(['3.44']);
  });

  it('warns on the deliverables card, where the memo leaves the building', () => {
    fx.fieldOverrides = staleOverrides();
    render(<ICMemoTab project={PROJECT} />);
    expect(screen.getByTestId('memo-export-stale-note')).toHaveTextContent(
      'The narrative does not: Investment Thesis, Key highlights, Key risks & considerations '
      + 'cannot be shown to match this run.',
    );
  });
});

// ───────────────────────────────────────────────────────────────────────────
describe('IC Memo — one Regenerate moves the whole narrative', () => {
  it('redrafts thesis, highlights and risks together, in a single write, stamped with one run', async () => {
    fx.fieldOverrides = staleOverrides();
    render(<ICMemoTab project={PROJECT} />);

    clickRegenerateAll();
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());

    // ONE write — the three sections cannot half-land.
    expect(updateSpy).toHaveBeenCalledTimes(1);
    const ov = lastOverrides();
    expect(ov.memo_thesis_run_id).toBe(RUN_NOW);
    expect(ov.memo_highlights_run_id).toBe(RUN_NOW);
    expect(ov.memo_risks_run_id).toBe(RUN_NOW);
    expect(ov.memo_thesis_edited).toBe(false);
    expect(ov.memo_highlights_edited).toBe(false);
    expect(ov.memo_risks_edited).toBe(false);

    // Every section now quotes the current run.
    expect(String(ov.memo_thesis)).toContain('32.2% levered IRR');
    expect(String(ov.memo_thesis)).toContain('3.44x equity multiple');
    const hi = (ov.memo_highlights as { t: string; ai: boolean }[]).map((p) => p.t).join(' | ');
    expect(hi).toContain('Levered IRR of 32.2%');
    expect(hi).toContain('Equity multiple of 3.44x');
    expect(hi).toContain('Year-1 DSCR of 1.59x');
    expect((ov.memo_risks as { t: string }[]).length).toBeGreaterThan(0);
  });

  it('leaves no figure in one section contradicting another, and clears the flag', async () => {
    fx.fieldOverrides = staleOverrides();
    render(<ICMemoTab project={PROJECT} />);
    expect(leveredIrrsOnScreen()).toEqual(['-2.5', '32.2']);

    clickRegenerateAll();
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());

    // The whole rendered memo — banner, snapshot, thesis, highlights, risks,
    // underwriting summary — now states exactly one levered IRR and one EM.
    await waitFor(() => expect(leveredIrrsOnScreen()).toEqual(['32.2']));
    expect(equityMultiplesOnScreen()).toEqual(['3.44']);
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('0.88x');
    expect(text).not.toContain('0.78x');
    expect(screen.queryByTestId('memo-narrative-stale')).toBeNull();
    expect(screen.queryByTestId('memo-export-stale-note')).toBeNull();
  });

  it('is idempotent — regenerating twice writes the same narrative twice', async () => {
    fx.fieldOverrides = staleOverrides();
    render(<ICMemoTab project={PROJECT} />);

    clickRegenerateAll();
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));
    const first = overridesAt(0);

    clickRegenerateAll();
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(2));
    expect(overridesAt(1)).toEqual(first);
  });
});

// ───────────────────────────────────────────────────────────────────────────
describe('IC Memo — the analyst’s own writing is never replaced silently', () => {
  const analystThesis = 'The committee should weigh the sponsor relationship above the headline IRR.';
  const edited = () =>
    staleOverrides({ memo_thesis: analystThesis, memo_thesis_edited: true });

  it('asks before overwriting an edited section, and writes nothing until it is answered', () => {
    fx.fieldOverrides = edited();
    render(<ICMemoTab project={PROJECT} />);

    clickRegenerateAll();
    const prompt = screen.getByTestId('memo-regen-confirm');
    expect(prompt).toHaveTextContent('Investment Thesis is your own writing');
    expect(updateSpy).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Investment thesis')).toHaveTextContent(analystThesis);
  });

  it('“Keep my edits” keeps the words AND keeps the section flagged', async () => {
    fx.fieldOverrides = edited();
    render(<ICMemoTab project={PROJECT} />);

    clickRegenerateAll();
    fireEvent.click(screen.getByTestId('memo-regen-keep'));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));

    const ov = lastOverrides();
    // The analyst's prose and its original stamp are untouched…
    expect(ov.memo_thesis).toBe(analystThesis);
    expect(ov.memo_thesis_edited).toBe(true);
    expect(ov.memo_thesis_run_id).toBe(RUN_THEN);
    // …while the drafted sections moved to this run.
    expect(ov.memo_highlights_run_id).toBe(RUN_NOW);
    expect(ov.memo_risks_run_id).toBe(RUN_NOW);

    // Keeping the wording does not certify its figures: the thesis stays
    // flagged, the redrafted lists do not.
    expect(screen.getByLabelText('Investment thesis')).toHaveTextContent(analystThesis);
    expect(screen.getByTestId('memo-stale-thesis')).toHaveTextContent('Earlier model run');
    expect(screen.queryByTestId('memo-stale-highlights')).toBeNull();
    expect(screen.getByTestId('memo-narrative-stale')).toHaveTextContent(
      'Investment Thesis cannot be shown to match the current model run',
    );
  });

  it('“Replace my edits” redrafts the edited section too — after the analyst said so', async () => {
    fx.fieldOverrides = edited();
    render(<ICMemoTab project={PROJECT} />);

    clickRegenerateAll();
    fireEvent.click(screen.getByTestId('memo-regen-replace'));
    await waitFor(() => expect(updateSpy).toHaveBeenCalledTimes(1));

    const ov = lastOverrides();
    expect(ov.memo_thesis).not.toBe(analystThesis);
    expect(String(ov.memo_thesis)).toContain('32.2% levered IRR');
    expect(ov.memo_thesis_edited).toBe(false);
    expect(ov.memo_thesis_run_id).toBe(RUN_NOW);
    expect(screen.queryByTestId('memo-narrative-stale')).toBeNull();
  });

  it('Cancel writes nothing at all', () => {
    fx.fieldOverrides = edited();
    render(<ICMemoTab project={PROJECT} />);

    clickRegenerateAll();
    fireEvent.click(screen.getByTestId('memo-regen-cancel'));
    expect(screen.queryByTestId('memo-regen-confirm')).toBeNull();
    expect(updateSpy).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Investment thesis')).toHaveTextContent(analystThesis);
  });
});

// ───────────────────────────────────────────────────────────────────────────
describe('IC Memo — curating a list pins it, so the next run can notice', () => {
  it('stamps the run the curated draft describes, and flags it once the model moves', async () => {
    // Root cause #2, end to end. A single "+ Add point" used to freeze the
    // generated draft with no record of its run; it now records one.
    render(<ICMemoTab project={PROJECT} />);
    fireEvent.click(screen.getAllByText('+ Add point')[0]);
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());

    const persisted = lastOverrides();
    expect(persisted.memo_highlights_run_id).toBe(RUN_NOW);
    expect(persisted.memo_highlights_edited).toBe(true);
    expect((persisted.memo_highlights as { t: string }[])[0].t).toContain('Levered IRR of 32.2%');

    // Re-open the deal after the model has been re-run: same persisted list,
    // new run. The memo says so instead of serving both underwritings flat.
    cleanup();
    fx.fieldOverrides = persisted;
    fx.outputs = outputsFor('run-2026-09-20');
    render(<ICMemoTab project={PROJECT} />);

    expect(screen.getByTestId('memo-stale-highlights')).toHaveTextContent('Earlier model run');
    expect(screen.getByTestId('memo-narrative-stale')).toHaveTextContent(
      'Key highlights cannot be shown to match the current model run',
    );
  });

  it('editing prose that is already flagged does not re-date it', async () => {
    fx.fieldOverrides = staleOverrides();
    render(<ICMemoTab project={PROJECT} />);

    // Reorder a stale highlight: the analyst curated the order, they did not
    // re-underwrite the figures inside it.
    fireEvent.click(screen.getAllByTitle('More')[0]);
    fireEvent.click(screen.getByText('Move down'));
    await waitFor(() => expect(updateSpy).toHaveBeenCalled());

    expect(lastOverrides().memo_highlights_run_id).toBe(RUN_THEN);
    expect(screen.getByTestId('memo-stale-highlights')).toHaveTextContent('Earlier model run');
  });
});
