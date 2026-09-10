/**
 * ontology/adapters — the ONE seam between the web and the generated
 * concept registry (Phase 1.4).
 *
 * Before this module the web carried nine hand-maintained copies of the
 * worker's vocabulary: the ``buildHistYear`` alias lists, the worksheet
 * ``ROWS`` keys, the ``histValue`` switch, the provenance label /
 * explanation maps and the ``AssumptionSource`` union. Each was an explicit
 * duplicate with a "FUTURE DRIFT WARNING" comment on it. They now all read
 * ``concepts.generated.ts``, which is generated from
 * ``apps/worker/app/ontology/concepts.yaml`` and gated in CI
 * (``scripts/gen_ontology.py --check``).
 *
 * Rules this module keeps:
 *   • ADDITIVE ONLY. Every derived list is a SUPERSET of what the web used
 *     at 08bf554 — pinned in ``__tests__/fixtures/ontology/web_aliases_pre_registry.json``
 *     and asserted in ``__tests__/ontologyAliases.test.ts``. Nothing that
 *     resolved before stops resolving.
 *   • Where the registry and the old hand-written map DISAGREE, the OLD
 *     behaviour wins and the case is recorded in ``DRIFT_NOTES.web.md``.
 *     Those pins live here, named and commented, not scattered.
 *   • Layout stays hand-authored. Row ORDER, labels, tones, icons and
 *     tooltip copy are the UI's call; only the VOCABULARY comes from the
 *     registry.
 */
import {
  CONCEPTS,
  FAMILIES,
  SOURCES,
  SUBORDINATE_NAMESPACES,
  type ConceptId,
  type SourceId,
  type SourceKind as RegistrySourceKind,
  type WorksheetBinding,
} from './concepts.generated';

// ───────────────────────────── aliases ─────────────────────────────

/**
 * Alias-map keys that a P&L / T-12 extraction can carry, in resolution
 * order: the PNL family block, then each member doc type's own block, then
 * the document-agnostic ``"*"`` block.
 *
 * ``findField`` matches on set membership (it iterates FIELDS, not aliases),
 * so this order is documentation rather than precedence — but it keeps the
 * derived list readable next to the registry.
 */
const PNL_ALIAS_KEYS: readonly string[] = ['PNL_FAMILY', ...FAMILIES.PNL_FAMILY, '*'];

/**
 * Flatten one or more concepts' P&L-family alias paths, de-duplicated, in
 * registry order.
 *
 * Templated paths (``p_and_l_usali.{year}.adr_usd``, ``str_segmentation.{n}…``)
 * are dropped: ``findField`` is a literal matcher with no placeholder
 * expander, and none of them were in the hand-written lists.
 */
export function pnlAliases(...ids: ConceptId[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const id of ids) {
    const aliases = CONCEPTS[id].aliases;
    for (const key of PNL_ALIAS_KEYS) {
      for (const a of aliases[key] ?? []) {
        if (a.path.includes('{')) continue;
        if (seen.has(a.path)) continue;
        seen.add(a.path);
        out.push(a.path);
      }
    }
  }
  return out;
}

/**
 * ``buildHistYear``'s alias lists, keyed by its ``pick()`` key.
 *
 * ``misc`` is the ONE collapse the registry records rather than resolves:
 * the registry keeps ``other_revenue`` (Other Operated Departments) and
 * ``misc_revenue`` (Miscellaneous Income) as two concepts, while Historicals
 * has always shown a single "Misc. Income" column. Per the handover
 * (DRIFT_NOTES.md §3.2) the adapter must not pick one and drop the other, so
 * the column reads the UNION — exactly what the hand-written list did.
 */
export const HISTORICALS_ALIASES: Readonly<Record<string, string[]>> = {
  occ: pnlAliases('occupancy'),
  adr: pnlAliases('adr'),
  revpar: pnlAliases('revpar'),
  rooms: pnlAliases('rooms_revenue'),
  fb: pnlAliases('fb_revenue'),
  misc: pnlAliases('other_revenue', 'misc_revenue'),
  rooms_dept: pnlAliases('rooms_dept_expense'),
  fb_dept: pnlAliases('fb_dept_expense'),
  other_dept: pnlAliases('other_dept_expense'),
  undistributed: pnlAliases('undistributed_expenses'),
  gop: pnlAliases('gop'),
  property_tax: pnlAliases('property_taxes'),
  insurance: pnlAliases('insurance'),
  mgmt_fee: pnlAliases('mgmt_fee'),
  noi: pnlAliases('noi'),
};

/**
 * WEB-ONLY aliases — paths the hand-written lists carried that the registry
 * does not (yet). They stay inline so nothing that resolved before stops
 * resolving; the registry builder folds them in as a follow-up. See
 * ``DRIFT_NOTES.web.md`` → "web_only_aliases".
 */
const WEB_ONLY_PERIOD_ENDING = ['period_end', 'statement_period_end'];

/** ``deriveYearLabel``'s period aliases. */
export const PERIOD_ALIASES: Readonly<Record<string, string[]>> = {
  period_ending: [...pnlAliases('period_ending'), ...WEB_ONLY_PERIOD_ENDING],
  period_type: pnlAliases('period_type'),
  period_label: pnlAliases('period_label'),
};

// ─────────────────── subordinate namespaces ───────────────────

/**
 * The subordinate namespaces the WEB enforces today.
 *
 * The registry's ``SUBORDINATE_NAMESPACES`` is the UNION across every
 * resolver (17 entries; DRIFT_NOTES.md §3.6) — it adds ``ytd``, ``weekly``,
 * ``daily``, ``mtd``, ``qtd``, ``prior_year``, ``day_of_week``, ``forecast``
 * and ``budget`` on top of the eight the web has always rejected. Turning
 * those on would BLANK cells that render today, which this phase is not
 * allowed to do, so the web subset is pinned here and the registry list is
 * still the source it is filtered from. Widening it is a one-line change
 * once the coordinator wants that behaviour — see ``DRIFT_NOTES.web.md``.
 */
const LEGACY_WEB_SUBORDINATE = new Set([
  'monthly', 'page', 'per_month', 'quarterly', 'q1', 'q2', 'q3', 'q4',
]);

export const WEB_SUBORDINATE_NAMESPACES: readonly string[] =
  SUBORDINATE_NAMESPACES.filter((ns) => LEGACY_WEB_SUBORDINATE.has(ns));

/**
 * ``true`` for a field path that sits under a subordinate-period namespace
 * and must never be matched as a period total.
 *
 * ``page`` names a NUMBERED segment (``.page5.``), so it matches as a
 * prefix; every other namespace is matched as a whole dotted segment —
 * byte-identical to the literal checks this replaced.
 */
export function isSubordinatePath(key: string): boolean {
  const lowered = key.toLowerCase();
  return WEB_SUBORDINATE_NAMESPACES.some((ns) =>
    ns === 'page' ? lowered.includes('.page') : lowered.includes(`.${ns}.`),
  );
}

// ───────────────────────── worksheet ─────────────────────────

type RowEntry = { conceptId: ConceptId; binding: WorksheetBinding };

const ROW_INDEX: Record<string, RowEntry> = (() => {
  const out: Record<string, RowEntry> = {};
  for (const id of Object.keys(CONCEPTS) as ConceptId[]) {
    const binding = CONCEPTS[id].bindings.worksheet;
    if (binding) out[binding.row] = { conceptId: id, binding };
  }
  return out;
})();

/** The registry's worksheet binding for a row id. Throws on an unknown row
 *  so a renamed row fails the build rather than silently losing its keys. */
export function worksheetBinding(row: string): WorksheetBinding {
  const entry = ROW_INDEX[row];
  if (!entry) throw new Error(`ontology: no worksheet binding for row "${row}"`);
  return entry.binding;
}

/** The concept a worksheet row renders. */
export function worksheetConcept(row: string): ConceptId {
  const entry = ROW_INDEX[row];
  if (!entry) throw new Error(`ontology: no worksheet binding for row "${row}"`);
  return entry.conceptId;
}

/** Row ids the registry binds (order is NOT layout — ``ROWS`` owns that). */
export const WORKSHEET_ROW_IDS: readonly string[] = Object.keys(ROW_INDEX);

/**
 * Worksheet row id → the ``HistYear`` field its historical cell reads.
 *
 * ``reviewState.histValue`` used a hand-written switch over exactly these
 * pairs. Rows whose historical value is a SUM of other fields (``total_rev``)
 * carry no ``hist_key`` and stay hand-computed in ``histValue``.
 */
export const HIST_KEY_BY_ROW: Readonly<Record<string, string>> = (() => {
  const out: Record<string, string> = {};
  for (const [row, entry] of Object.entries(ROW_INDEX)) {
    if (entry.binding.hist_key) out[row] = entry.binding.hist_key;
  }
  return out;
})();

// ───────────────────────── provenance ─────────────────────────

/** Every source label the worker can emit — 18 as of REGISTRY_VERSION 1. */
export const SOURCE_IDS: readonly SourceId[] = Object.keys(SOURCES) as SourceId[];

/** Long label (the Provenance Ledger / hover title). */
export const SOURCE_LABEL_FROM_REGISTRY: Readonly<Record<string, string>> =
  Object.fromEntries(SOURCE_IDS.map((id) => [id, SOURCES[id].label]));

/** Short badge text (the inline ``AssumptionBadge`` pill). */
export const SOURCE_BADGE_FROM_REGISTRY: Readonly<Record<string, string>> =
  Object.fromEntries(SOURCE_IDS.map((id) => [id, SOURCES[id].badge]));

/** One-line explanation (the hover body / badge tooltip). */
export const SOURCE_EXPLANATION_FROM_REGISTRY: Readonly<Record<string, string>> =
  Object.fromEntries(SOURCE_IDS.map((id) => [id, SOURCES[id].explanation]));

/** Registry classification, before the web's 3-colour mapping. */
export const SOURCE_REGISTRY_KIND: Readonly<Record<string, RegistrySourceKind>> =
  Object.fromEntries(SOURCE_IDS.map((id) => [id, SOURCES[id].kind]));
