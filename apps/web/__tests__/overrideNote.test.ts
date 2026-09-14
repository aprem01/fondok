/**
 * The analyst-justification contract — FON-74.
 *
 * The founder's June 2026 rule: an analyst who overrides a value must attach a
 * justification. It was implemented once, in a drawer nothing mounted, and
 * enforced nowhere an analyst could reach. `src/lib/overrideNote.ts` is now the
 * ONE place the rule lives in the browser, and `apps/worker/app/api/deals.py`
 * is the one place it lives in the API.
 *
 * The rule: a note is required IFF the key routes into ENGINE INPUT.
 *
 * Two halves have to agree or the browser and the API disagree about what an
 * analyst may save — the browser would collect a note the API does not want, or
 * (far worse) let a save through that the API 422s. So these tests do not
 * re-state the exempt list: they READ the Python and assert the mirror, the
 * same way the ontology suite pins the generated registry. When a key is added
 * to the worker's exemption list and not here, this suite fails.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  requiresNote,
  overrideEnvelope,
  applyOverridePatch,
  patchRequiresNote,
  noteOf,
  overrideNoteFor,
  NOTE_REQUIRED_MESSAGE,
} from '@/lib/overrideNote';

const WORKER = path.resolve(__dirname, '../../worker');
const readWorker = (rel: string): string => readFileSync(path.join(WORKER, rel), 'utf8');

/** The keys inside a `frozenset({...})` literal assigned to `name`. */
function frozensetLiteral(source: string, name: string): string[] {
  const decl = source.slice(source.indexOf(`${name}:`));
  const open = decl.indexOf('frozenset(');
  expect(open).toBeGreaterThan(-1);
  const close = decl.indexOf(')', open);
  const body = decl.slice(open, close);
  return [...body.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
}

describe('the mirror of the worker — read from the Python, not restated', () => {
  it("`_OVERRIDE_NON_ENGINE_KEYS` is exempt in the browser too", () => {
    const runner = readWorker('app/services/engine_runner.py');
    const keys = frozensetLiteral(runner, '_OVERRIDE_NON_ENGINE_KEYS');
    // The worker names at least `worksheet_layout`; whatever else it names,
    // the browser must not demand a note for it.
    expect(keys).toContain('worksheet_layout');
    for (const key of keys) expect(requiresNote(key)).toBe(false);
  });

  it("the API's `_NOTE_EXEMPT_KEYS` is exempt in the browser too", () => {
    const deals = readWorker('app/api/deals.py');
    const keys = frozensetLiteral(deals, '_NOTE_EXEMPT_KEYS');
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) expect(requiresNote(key)).toBe(false);
  });

  it("the API's pattern exemptions hold in the browser too", () => {
    const deals = readWorker('app/api/deals.py');
    const block = deals.slice(
      deals.indexOf('_NOTE_EXEMPT_PATTERNS'),
      deals.indexOf('def _override_note'),
    );
    const patterns = [...block.matchAll(/re\.compile\(r"([^"]+)"\)/g)].map((m) => m[1]);
    expect(patterns.length).toBeGreaterThan(0);
    // Python and JS regex agree on these shapes; a key the worker exempts by
    // pattern must be exempt here for a representative instance of it.
    const probes: Record<string, string> = {
      '^memo_': 'memo_thesis',
      '^partnership\\.waterfall\\.\\d+\\.removed$': 'partnership.waterfall.2.removed',
    };
    for (const pattern of patterns) {
      const probe = probes[pattern];
      expect(probe, `no probe for worker pattern ${pattern} — add one`).toBeTruthy();
      expect(new RegExp(pattern).test(probe)).toBe(true);
      expect(requiresNote(probe)).toBe(false);
    }
  });
});

describe('requiresNote — a note is required iff the key routes into engine input', () => {
  const engineInputs = [
    'starting_occupancy',
    'starting_adr',
    'revpar_growth',
    'expense_growth',
    'other_expense_growth',
    'resort_fee_per_night',
    'resort_fee_capture_y1',
    'mgmt_fee_pct',
    'purchase_price',
    'exit_cap_rate',
    'hold_years',
    'debt_stack.tranches.0.principal_usd',
    'debt_stack.tranches.0.rate_pct',
    'debt_stack.tranches.0.rate_type',
    'debt_stack.refi_test_year',
    'gp_equity_pct',
    'lp_equity_pct',
    'pref_rate',
    'partnership.waterfall.1.hurdle_rate',
    'partnership.waterfall.1.gp_split',
    'noi_override_by_year',
  ];
  it.each(engineInputs)('%s needs a justification', (key) => {
    expect(requiresNote(key)).toBe(true);
  });

  const exempt = [
    // The worksheet's row layout — presentation only. Without this exemption
    // every drag-to-reorder would be refused.
    'worksheet_layout',
    // Display-only: it selects the projection year the stabilized figures are
    // read from and moves no return.
    'stabilization_year',
    // A name, not a number.
    'property_overview.name',
    // "qualitative; no numeric covenant math" — the debt engine's own words.
    'debt.completion_guarantee',
    // The shape of the waterfall, not a value in it.
    'partnership.waterfall.tier_count',
    'partnership.waterfall.0.removed',
    // IC-memo prose and its UI state.
    'memo_thesis',
    'memo_thesis_edited',
    'memo_diligence',
    'memo_highlights',
    'memo_risks',
    'memo_recommendation_override',
    'memo_recommendation_confirmed',
    // FON-54 — which analyst owns a section, and which engine run its prose
    // was drafted against. Provenance OF an override, not an override; there
    // is nothing to justify, and a 422 here would block the regenerate that
    // stops the memo carrying two underwritings.
    'memo_highlights_edited',
    'memo_risks_edited',
    'memo_thesis_run_id',
    'memo_highlights_run_id',
    'memo_risks_run_id',
  ];
  it.each(exempt)('%s does not', (key) => {
    expect(requiresNote(key)).toBe(false);
  });

  it('deal COLUMNS are not overrides — target_irr / target_moic need no note', () => {
    // Founder decision: these are stated objectives (the benchmark and the Max
    // Price Solver's hurdle), not replacements for a sourced value. The
    // methodology page says so, so the claim and the behaviour match.
    expect(requiresNote('target_irr')).toBe(false);
    expect(requiresNote('target_moic')).toBe(false);
    expect(requiresNote('name')).toBe(false);
    expect(requiresNote('keys')).toBe(false);
  });
});

describe('overrideEnvelope — trims, refuses, and never invents', () => {
  it('attaches the trimmed note', () => {
    expect(overrideEnvelope('exit_cap_rate', 0.075, '  broker guidance  ')).toEqual({
      value: 0.075,
      note: 'broker guidance',
    });
  });

  it('refuses an empty note on a key that needs one', () => {
    expect(() => overrideEnvelope('exit_cap_rate', 0.075, '')).toThrow(NOTE_REQUIRED_MESSAGE);
    expect(() => overrideEnvelope('exit_cap_rate', 0.075, '   ')).toThrow(NOTE_REQUIRED_MESSAGE);
    expect(() => overrideEnvelope('exit_cap_rate', 0.075, null)).toThrow(NOTE_REQUIRED_MESSAGE);
  });

  it('omits the note on an exempt key — a blank note, never an invented one', () => {
    expect(overrideEnvelope('property_overview.name', 'The Angler’s', '')).toEqual({
      value: 'The Angler’s',
    });
    expect(overrideEnvelope('stabilization_year', 3, '')).toEqual({ value: 3 });
  });
});

describe('applyOverridePatch — one Save, one justification', () => {
  it('carries the note across every key in a multi-key change', () => {
    const next = applyOverridePatch(
      {},
      { gp_equity_pct: 0.12, lp_equity_pct: 0.88 },
      'JV amendment 3',
    );
    expect(next).toEqual({
      gp_equity_pct: { value: 0.12, note: 'JV amendment 3' },
      lp_equity_pct: { value: 0.88, note: 'JV amendment 3' },
    });
  });

  it('clearing an override is a revert, not an override — no note needed', () => {
    const next = applyOverridePatch({ exit_cap_rate: { value: 0.075, note: 'x' } }, { exit_cap_rate: null }, '');
    expect(next).toEqual({});
  });

  it('refuses to build a patch that would be rejected by the API', () => {
    expect(() => applyOverridePatch({}, { exit_cap_rate: 0.075 }, '')).toThrow(NOTE_REQUIRED_MESSAGE);
  });

  it('leaves an exempt key a bare scalar when nothing was said', () => {
    expect(applyOverridePatch({}, { 'debt.completion_guarantee': 'in_place' }, '')).toEqual({
      'debt.completion_guarantee': 'in_place',
    });
  });

  it('carries the rest of field_overrides through untouched', () => {
    const current = { worksheet_layout: { rows: [] }, purchase_price: 36_000_000 };
    const next = applyOverridePatch(current, { exit_cap_rate: 0.075 }, 'comps');
    expect(next.worksheet_layout).toBe(current.worksheet_layout);
    expect(next.purchase_price).toBe(36_000_000);
  });

  it('patchRequiresNote answers for the whole patch', () => {
    expect(patchRequiresNote({ exit_cap_rate: 0.075 })).toBe(true);
    expect(patchRequiresNote({ worksheet_layout: { rows: [] } })).toBe(false);
    expect(patchRequiresNote({ exit_cap_rate: null })).toBe(false);
    expect(patchRequiresNote({ 'partnership.waterfall.tier_count': 4 })).toBe(false);
  });
});

describe('reading the stored justification back', () => {
  it('returns what the analyst wrote', () => {
    expect(noteOf({ value: 0.075, note: ' comps set ' })).toBe('comps set');
    expect(overrideNoteFor({ exit_cap_rate: { value: 0.075, note: 'comps' } }, 'exit_cap_rate')).toBe('comps');
  });

  it('a legacy bare scalar carries none, and says so', () => {
    expect(noteOf(0.075)).toBeNull();
    expect(noteOf(null)).toBeNull();
    expect(noteOf({ value: 0.075 })).toBeNull();
    expect(noteOf({ value: 0.075, note: '   ' })).toBeNull();
    expect(overrideNoteFor(undefined, 'exit_cap_rate')).toBeNull();
    expect(overrideNoteFor({}, 'exit_cap_rate')).toBeNull();
  });
});
