/**
 * renovationBasis — "what is the renovation placeholder BASED on?" (E-021 /
 * FON-44: "the renovation placeholder could be adjusted, but the basis for
 * the initial amount was unclear").
 *
 * One pure function over RECORDED data only — the deal's provenance map
 * (``GET /deals/{id}/assumption_sources``: ``sources`` / ``source_fields``),
 * the deal row's ``field_overrides`` envelope, and the capital engine's own
 * outputs. It never invents a basis: when nothing on the wire says where the
 * number came from, it says ``basis not recorded``.
 *
 * What the worker records for ``renovation_budget`` (engine_runner.py):
 *   • seed       — the flat Kimpton seed (``_kimpton_assumptions``:
 *                  ``renovation_budget: 5_280_000``), label ``seed``.
 *   • OM         — ``_load_om_capital_actuals`` reads ``broker_proforma.
 *                  renovation_budget_usd`` / ``renovation_budget_usd`` /
 *                  ``renovation_budget`` (field_catalog.yaml ``om_capital``)
 *                  off the deal's OM and records the row in
 *                  ``__source_fields__`` (field_name, source_page,
 *                  document_id, doc_type). It does NOT flip the ``sources``
 *                  label off ``seed`` — so the recorded row, not the label,
 *                  is the document signal here.
 *   • override   — ``field_overrides.renovation_budget`` → label
 *                  ``analyst_override``; the justification note lives on the
 *                  deal row's ``{value, note}`` envelope (FON-74), not on the
 *                  provenance payload.
 *   • deal_row   — not currently written for this key (only ``keys`` and
 *                  ``purchase_price`` come off the deals row), handled anyway.
 */

import type { AssumptionSourceField } from '@/lib/api';
import { fmtCurrency, fmtPct } from '@/lib/format';

export type RenovationBasisKind = 'override' | 'document' | 'seed' | 'deal_row' | 'unrecorded';

export interface RenovationBasisInput {
  /** The renovation BASE budget the capital engine carried (before contingency). */
  amount: number | null | undefined;
  /** The deal's key count — the per-key reading of a seed needs it. */
  keys: number | null | undefined;
  /** ``assumption_sources.sources.renovation_budget``; null/undefined while unresolved. */
  source: string | null | undefined;
  /** ``assumption_sources.source_fields.renovation_budget`` — the extraction row behind it. */
  field: AssumptionSourceField | null | undefined;
  /** True when the deal row's ``field_overrides`` carries ``renovation_budget``. */
  overridden: boolean;
  /** The analyst's justification on that override, when one was written. */
  overrideNote: string | null | undefined;
  /** ``capital.renovation_contingency_pct`` (a fraction) when the run carried it. */
  contingencyPct: number | null | undefined;
}

export interface RenovationBasis {
  kind: RenovationBasisKind;
  /** The sentence shown after "Basis ·". */
  text: string;
  /** "contingency 10.0%" when the run recorded a contingency percent, else null. */
  contingency: string | null;
}

const has = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);

/** "OM" / "CapEx document" / "<DOC_TYPE> document" from the recorded row — never a guess. */
function documentLabel(field: AssumptionSourceField): string {
  const dt = (field.doc_type ?? '').trim().toUpperCase();
  if (dt === 'OM') return 'OM';
  if (/CAPEX|PIP|RENOV/.test(dt)) return 'CapEx document';
  if (dt) return `${dt} document`;
  const name = (field.filename ?? '').trim();
  return name || 'Document';
}

export function renovationBasis(input: RenovationBasisInput): RenovationBasis {
  const contingency = has(input.contingencyPct)
    ? `contingency ${fmtPct(input.contingencyPct, 1)}`
    : null;
  const done = (kind: RenovationBasisKind, text: string): RenovationBasis => ({ kind, text, contingency });

  // 1. The analyst's own number. The deal row is authoritative (the
  //    provenance payload may lag a save by one fetch); the label agrees once
  //    it catches up.
  if (input.overridden || input.source === 'analyst_override') {
    const note = (input.overrideNote ?? '').trim();
    return done('override', note ? `Your override (${note})` : 'Your override');
  }

  // 2. A recorded extraction row — the document signal, whatever the label.
  const f = input.field;
  if (f && (f.field || f.filename)) {
    const where = f.field ? f.field : (f.filename ?? '');
    const page = has(f.page) ? ` p.${f.page}` : '';
    return done('document', `${documentLabel(f)} · ${where}${page}`);
  }

  // 3. The seed, read per key over THIS deal's key count so the analyst can
  //    see what it implies ("$40,000 / key × 132 keys").
  if (input.source === 'seed') {
    if (has(input.amount) && has(input.keys) && input.keys > 0) {
      return done(
        'seed',
        `Per-key assumption · ${fmtCurrency(input.amount / input.keys)} / key × ${input.keys} keys (seed)`,
      );
    }
    return done('seed', 'Seed assumption (seed)');
  }

  // 4. Entered on the deal record (not written for this key today — kept so
  //    the line stays honest if the runner starts recording it).
  if (input.source === 'deal_row') {
    return done('deal_row', has(input.amount) ? `Deal record · ${fmtCurrency(input.amount)}` : 'Deal record');
  }

  return done('unrecorded', 'basis not recorded');
}
