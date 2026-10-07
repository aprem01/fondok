/**
 * renovationBasis — E-021 (FON-44): "the renovation placeholder could be
 * adjusted, but the basis for the initial amount was unclear."
 *
 * Pins each basis variant to RECORDED data only — the provenance map's
 * `sources` / `source_fields`, the deal row's override envelope, and the
 * run's contingency percent. Nothing here is a prototype number.
 */
import { describe, it, expect } from 'vitest';
import { renovationBasis } from '@/lib/renovationBasis';

const base = {
  amount: 5_280_000,
  keys: 132,
  source: 'seed' as string | null,
  field: null,
  overridden: false,
  overrideNote: null,
  contingencyPct: null as number | null,
};

describe('renovationBasis — the five recorded variants', () => {
  it('seed → the flat seed read per key over THIS deal’s key count', () => {
    const b = renovationBasis(base);
    expect(b.kind).toBe('seed');
    expect(b.text).toBe('Per-key assumption · $40,000 / key × 132 keys (seed)');
    expect(b.contingency).toBeNull();
  });

  it('seed with a different key count re-expresses the same seed amount per key (never a new amount)', () => {
    const b = renovationBasis({ ...base, keys: 100 });
    expect(b.text).toBe('Per-key assumption · $52,800 / key × 100 keys (seed)');
  });

  it('seed without a key count cannot be read per key — says seed, no per-key number', () => {
    const b = renovationBasis({ ...base, keys: undefined });
    expect(b.kind).toBe('seed');
    expect(b.text).toBe('Seed assumption (seed)');
  });

  it('OM → the extracted field and page off the recorded source row', () => {
    const b = renovationBasis({
      ...base,
      field: { field: 'broker_proforma.renovation_budget_usd', page: 14, doc_type: 'OM', filename: 'OM.pdf' },
    });
    expect(b.kind).toBe('document');
    expect(b.text).toBe('OM · broker_proforma.renovation_budget_usd p.14');
  });

  it('OM row wins over a stale "seed" label — the runner never flips the label for OM capital keys', () => {
    const b = renovationBasis({
      ...base,
      source: 'seed',
      field: { field: 'renovation_budget_usd', page: 2, doc_type: 'OM' },
    });
    expect(b.kind).toBe('document');
    expect(b.text).toBe('OM · renovation_budget_usd p.2');
  });

  it('CapEx document → named as such, with field and page', () => {
    const b = renovationBasis({
      ...base,
      field: { field: 'capex.renovation_budget_usd', page: 3, doc_type: 'CAPEX' },
    });
    expect(b.text).toBe('CapEx document · capex.renovation_budget_usd p.3');
  });

  it('a recorded row without a page number omits the page rather than inventing one', () => {
    const b = renovationBasis({
      ...base,
      field: { field: 'broker_proforma.renovation_budget_usd', page: null, doc_type: 'OM' },
    });
    expect(b.text).toBe('OM · broker_proforma.renovation_budget_usd');
  });

  it('override → the analyst’s own note off the deal row', () => {
    const b = renovationBasis({ ...base, overridden: true, overrideNote: 'GC bid 2026-09', source: 'analyst_override' });
    expect(b.kind).toBe('override');
    expect(b.text).toBe('Your override (GC bid 2026-09)');
  });

  it('override without a note still reads as the analyst’s override', () => {
    const b = renovationBasis({ ...base, overridden: true, overrideNote: '   ' });
    expect(b.text).toBe('Your override');
  });

  it('the deal row’s override wins even over a recorded OM row (the number no longer comes off the document)', () => {
    const b = renovationBasis({
      ...base,
      overridden: true,
      overrideNote: 'PIP scope revised',
      field: { field: 'broker_proforma.renovation_budget_usd', page: 14, doc_type: 'OM' },
    });
    expect(b.kind).toBe('override');
  });

  it('nothing recorded → "basis not recorded", never a guess', () => {
    expect(renovationBasis({ ...base, source: null }).text).toBe('basis not recorded');
    expect(renovationBasis({ ...base, source: 'pip_om' }).text).toBe('basis not recorded');
    expect(renovationBasis({ ...base, source: undefined, amount: undefined }).kind).toBe('unrecorded');
  });

  it('deal_row → entered on the deal record', () => {
    expect(renovationBasis({ ...base, source: 'deal_row' }).text).toBe('Deal record · $5,280,000');
  });
});

describe('renovationBasis — contingency %', () => {
  it('appends the contingency when the run carried one', () => {
    expect(renovationBasis({ ...base, contingencyPct: 0.1 }).contingency).toBe('contingency 10.0%');
  });
  it('a recorded 0% is still a recorded value', () => {
    expect(renovationBasis({ ...base, contingencyPct: 0 }).contingency).toBe('contingency 0.0%');
  });
  it('absent on a run that predates the field', () => {
    expect(renovationBasis({ ...base, contingencyPct: undefined }).contingency).toBeNull();
  });
});
