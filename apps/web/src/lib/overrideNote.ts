/**
 * The analyst-justification contract — FON-74.
 *
 * The June 2026 founder rule: *an analyst who overrides a value must attach a
 * justification.* Until now that rule was implemented once, in a drawer
 * (`help/OverridePanel`) nothing ever mounted, while every path an analyst can
 * actually reach either labelled the note "(optional)" and pre-filled a
 * software-authored string, or wrote no note at all. This module is the ONE
 * place the rule lives, so there can never again be a second implementation to
 * invert against the first.
 *
 * ── The rule ────────────────────────────────────────────────────────────────
 *
 *   A note is required IFF the key routes into ENGINE INPUT.
 *
 * The worker already owns that predicate: `_OVERRIDE_NON_ENGINE_KEYS` in
 * `apps/worker/app/services/engine_runner.py` names the `field_overrides` keys
 * the override-routing loop skips BY NAME, and everything else lands on `base`
 * and moves a number. This module MIRRORS that constant rather than re-deriving
 * the split, and `__tests__/overrideNote.test.ts` reads the Python source to pin
 * the mirror.
 *
 * A handful of keys reach `base` but move no number — a name, prose, the shape
 * of the waterfall, a qualitative covenant status. They are listed here
 * explicitly, each with the evidence for why it is not a number, and the worker
 * half of the gate (`apps/worker/app/api/deals.py::_NOTE_EXEMPT_KEYS`) carries
 * the same list so the browser and the API cannot disagree. The same test pins
 * that half too.
 *
 * ── What it is NOT ──────────────────────────────────────────────────────────
 *
 * The note never reaches an engine. `_normalize_override_shape`
 * (`engine_runner.py`) flattens `{value, note}` to the scalar before any engine
 * sees it — *"Engines only need the scalar."* Adopting this contract therefore
 * cannot move a single modelled number.
 */

/** What the analyst is told when Save is refused for want of a justification. */
export const NOTE_REQUIRED_MESSAGE =
  'Add a short note explaining this override — it is stored with the number and shown to reviewers.';

/** The placeholder every note field carries, so the affordance reads the same everywhere. */
export const NOTE_PLACEHOLDER = 'Why this number? (required)';

/**
 * Keys that reach the deal's `field_overrides` but are NOT a number an engine
 * runs on. Each entry states the evidence; nothing lands here for convenience.
 */
const NOTE_EXEMPT_KEYS: ReadonlySet<string> = new Set([
  // The worker's own `_OVERRIDE_NON_ENGINE_KEYS` — the Grounded Worksheet's
  // per-deal row layout (relabel / split / reorder / memo). Presentation only;
  // the worksheet's numbers come from the engines regardless of row order.
  // Excluding it is load-bearing: without it every drag-to-reorder would 422.
  'worksheet_layout',
  // The worker's `_OVERRIDE_NON_ENGINE_KEYS` again — which transactions the
  // analyst counts as comparable. Curation, not a number the engines run on;
  // the worker skips it, and the Market tab writes it with its own standing
  // note. Added 2026-09-12 when the mirror test caught it missing here after
  // the Market slice extended the worker's list.
  'market.selected_comps',
  // DISPLAY-ONLY in the model: it selects which projection year the stabilized
  // figures are read from. It moves no return — pinned by the worker's
  // `test_stabilization_year.py::test_stabilization_year_does_not_move_returns`.
  'stabilization_year',
  // The Property Name. Text, not a number; the worker reads it back as
  // `property_name` and keeps the extracted value for Restore.
  'property_overview.name',
  // Lender Completion Guarantee status. The debt engine's own field comment:
  // "qualitative; no numeric covenant math" — it is echoed, never computed on.
  'debt.completion_guarantee',
  // The SHAPE of the waterfall, not a value in it: how many promote tiers
  // exist. Editing a tier's hurdle or split is a value change and does require
  // a note.
  'partnership.waterfall.tier_count',
]);

/**
 * Deal COLUMNS. They never enter `field_overrides` at all, so the API gate
 * never sees them — but editors ask `requiresNote` by key without caring where
 * the key is persisted, and the answer for a column is always no.
 *
 * `target_irr` / `target_moic` are the genuine edge, and the founder's call:
 * they are STATED OBJECTIVES — the benchmark the returns are read against and
 * the hurdle the Max Price Solver prices to — not replacements for a value
 * Fondok sourced from a document. There is nothing to justify overriding,
 * because nothing was overridden. `methodology/page.tsx` §3 says so out loud,
 * so the claim and the behaviour match.
 */
const DEAL_COLUMN_KEYS: ReadonlySet<string> = new Set([
  'target_irr',
  'target_moic',
  'name',
  'keys',
]);

/** Pattern-shaped exemptions (indexed or namespaced keys). */
const NOTE_EXEMPT_PATTERNS: readonly RegExp[] = [
  // IC-memo prose and its UI state (`memo_thesis`, `memo_diligence`,
  // `memo_highlights`, `memo_risks`, `memo_recommendation_*`, the `*_edited`
  // flags). A justification for a justification is a note about a note.
  /^memo_/,
  // A removed promote tier's tombstone — again the waterfall's shape.
  /^partnership\.waterfall\.\d+\.removed$/,
];

/**
 * Does overriding `key` require an analyst justification?
 *
 * Answers for any key, including deal COLUMNS (`name`, `keys`, `target_irr`,
 * `target_moic`) which never enter `field_overrides` at all — those are analyst
 * inputs or stated objectives, not overrides of a sourced value, so they answer
 * false through the same door.
 */
export function requiresNote(key: string): boolean {
  if (!key) return false;
  if (NOTE_EXEMPT_KEYS.has(key) || DEAL_COLUMN_KEYS.has(key)) return false;
  return !NOTE_EXEMPT_PATTERNS.some((re) => re.test(key));
}

/** The `field_overrides` entry shape: the value, plus the analyst's note. */
export interface OverrideEnvelope<T> {
  value: T;
  note?: string;
}

/**
 * Build the `field_overrides` entry for one override.
 *
 * Trims the note. Throws `NOTE_REQUIRED_MESSAGE` when `key` needs one and none
 * was given — the last line of defence, so a caller that forgets the UI gate
 * fails loudly in the browser instead of silently writing an unjustified
 * override the API would 422 anyway.
 *
 * A blank note on an exempt key is OMITTED, never invented: `{value}`, not
 * `{value, note: 'Overridden on the Projections page'}`.
 *
 * Deviates from the plan's `overrideEnvelope(value, note)` by taking the key
 * first — without it the function cannot enforce its own contract.
 */
export function overrideEnvelope<T>(key: string, value: T, note: string | null | undefined): OverrideEnvelope<T> {
  const trimmed = (note ?? '').trim();
  if (!trimmed && requiresNote(key)) throw new Error(NOTE_REQUIRED_MESSAGE);
  return trimmed ? { value, note: trimmed } : { value };
}

/**
 * Fold one Save into a deal's `field_overrides`.
 *
 * The single place a tab turns "the analyst changed these keys, and said why"
 * into the blob that is PATCHed:
 *
 *   • `null` clears that override (a REVERT to source — never needs a note).
 *   • every other key is written with the analyst's note attached, so a
 *     multi-key save (LTV → senior principal, GP% → LP%, Fixed → rate_type +
 *     rate_pct) carries ONE justification across the whole change.
 *   • a blank note on a key that requires one throws `NOTE_REQUIRED_MESSAGE`
 *     rather than writing an unjustified override the API would 422.
 *   • with no note, an exempt key keeps its bare scalar shape — nothing is
 *     wrapped for the sake of being wrapped.
 */
export function applyOverridePatch(
  current: Record<string, unknown>,
  patch: Record<string, unknown>,
  note: string | null | undefined,
): Record<string, unknown> {
  const trimmed = (note ?? '').trim();
  const next = { ...current };
  for (const [path, value] of Object.entries(patch)) {
    if (value === null) { delete next[path]; continue; }
    if (!trimmed && requiresNote(path)) throw new Error(NOTE_REQUIRED_MESSAGE);
    next[path] = trimmed ? { value, note: trimmed } : value;
  }
  return next;
}

/** Does this patch contain a key that cannot be written without a justification? */
export function patchRequiresNote(patch: Record<string, unknown>): boolean {
  return Object.entries(patch).some(([path, value]) => value !== null && requiresNote(path));
}

/**
 * The analyst note stored on a `field_overrides` entry, or null.
 * Accepts both shapes: the structured `{value, note}` and the legacy bare
 * scalar (which never carries one).
 */
export function noteOf(entry: unknown): string | null {
  if (entry == null || typeof entry !== 'object') return null;
  const note = (entry as { note?: unknown }).note;
  return typeof note === 'string' && note.trim() ? note.trim() : null;
}

/** The analyst note on `overrides[key]`, or null. */
export function overrideNoteFor(
  overrides: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  if (!overrides) return null;
  return noteOf(overrides[key]);
}
