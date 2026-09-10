/**
 * Phase 1.2 — the web's ontology modules are GENERATED from
 * ``apps/worker/app/ontology/concepts.yaml`` by
 * ``apps/worker/scripts/gen_ontology.py``. These tests lock the shape the
 * rest of the web can rely on: the module and the JSON snapshot describe
 * the same registry version, every concept is labelled and reachable via
 * at least one alias, and the 16 refusal codes are all present.
 */
import { describe, it, expect } from 'vitest';
import {
  CONCEPTS,
  CONCEPT_IDS,
  DOC_TYPES,
  FAMILIES,
  PERIOD_TYPES,
  REGISTRY_VERSION,
  SOURCES,
  SUBORDINATE_NAMESPACES,
} from '@/lib/ontology/concepts.generated';
import { REASONS, REASON_CODES } from '@/lib/ontology/reasons.generated';
import snapshot from '@/lib/ontology/concepts.snapshot.json';

const EXPECTED_REASONS = [
  'no_document', 'no_source', 'unit_unknown', 'period_mismatch', 'basis_mismatch',
  'basis_excluded', 'awaiting_analyst', 'needs_review', 'pin_active', 'str_unavailable',
  'not_knowable_as_of', 'as_of_unknown', 'stale_run', 'engine_skipped', 'inconclusive',
  'not_applicable',
];

describe('ontology — generated concept registry', () => {
  it('REGISTRY_VERSION equals the snapshot version', () => {
    expect(REGISTRY_VERSION).toBe(snapshot.version);
    expect(typeof REGISTRY_VERSION).toBe('number');
  });

  it('module and snapshot describe the same concepts', () => {
    expect(Object.keys(CONCEPTS).sort()).toEqual(Object.keys(snapshot.concepts).sort());
    expect(CONCEPT_IDS).toEqual(Object.keys(CONCEPTS));
  });

  it('every concept has a label and at least one alias', () => {
    for (const id of CONCEPT_IDS) {
      const c = CONCEPTS[id];
      expect(c.id, id).toBe(id);
      expect(c.label.trim().length, `${id} label`).toBeGreaterThan(0);
      const aliases = Object.values(c.aliases).flat();
      expect(aliases.length, `${id} aliases`).toBeGreaterThan(0);
      for (const a of aliases) expect(a.path.trim().length, `${id} alias path`).toBeGreaterThan(0);
    }
  });

  it('alias-map keys are doc types, families or "*"', () => {
    const valid = new Set<string>([...DOC_TYPES, ...Object.keys(FAMILIES), '*']);
    for (const id of CONCEPT_IDS) {
      for (const key of Object.keys(CONCEPTS[id].aliases)) expect(valid.has(key), `${id}: ${key}`).toBe(true);
    }
  });

  it('carries the period ranks and subordinate namespaces the resolvers use', () => {
    expect(PERIOD_TYPES.annual).toBe(0);
    expect(PERIOD_TYPES.ttm).toBe(1);
    expect(PERIOD_TYPES.ytd).toBe(5);
    expect(PERIOD_TYPES.monthly).toBe(9);
    for (const ns of ['monthly', 'quarterly', 'ytd', 'per_month', 'page', 'q1', 'q4']) {
      expect(SUBORDINATE_NAMESPACES).toContain(ns);
    }
  });

  it('GOP carries the aliases the historicals tab and the scorer use today', () => {
    const paths = Object.values(CONCEPTS.gop.aliases).flat().map((a) => a.path);
    for (const p of [
      'p_and_l_usali.gross_operating_profit',
      'p_and_l_usali.gross_operating_profit_usd',
      'p_and_l_usali.gop.gross_operating_profit_usd',
      'ttm_summary_per_om.gop_usd',
      'gop', 'gop_usd', 'gross_operating_profit',
    ]) {
      expect(paths, p).toContain(p);
    }
    expect(CONCEPTS.gop.bindings.worksheet?.row).toBe('gop');
    expect(CONCEPTS.gop.identity).toBe('total_revenue - dept_expenses - undistributed_expenses');
  });

  it('provenance vocabulary covers every worker SOURCE_* label', () => {
    for (const s of [
      'seed', 'deal_row', 't12_actual', 'cbre_horizons', 'pnl_benchmark', 'portfolio_pnl', 'om_comps',
      'om_broker', 'analyst_override', 'str_forecast', 'str_forecast_unavailable',
      'derived_from_revpar_growth', 'partnership_doc',
    ]) {
      expect(SOURCES[s as keyof typeof SOURCES], s).toBeDefined();
    }
    expect(SOURCES.str_forecast_unavailable.kind).toBe('refusal');
    expect(SOURCES.str_forecast_unavailable.reason).toBe('str_unavailable');
  });
});

describe('ontology — generated refusal reasons', () => {
  it('exposes exactly the 16 shared codes with {label, ui, explanation}', () => {
    expect([...REASON_CODES].sort()).toEqual([...EXPECTED_REASONS].sort());
    expect(Object.keys(REASONS).sort()).toEqual([...EXPECTED_REASONS].sort());
    for (const code of REASON_CODES) {
      const meta = REASONS[code];
      expect(meta.label.length, code).toBeGreaterThan(0);
      expect(meta.ui.length, code).toBeGreaterThan(0);
      expect(meta.explanation.length, code).toBeGreaterThan(0);
    }
    expect(Object.keys(snapshot.reasons).sort()).toEqual([...EXPECTED_REASONS].sort());
  });
});
