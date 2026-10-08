/**
 * FON-41 / R-011 + R-015 — browser-local persistence for the create-deal
 * wizard.
 *
 *  - R-011: the in-progress wizard (every step field + the current step) is
 *    autosaved so a refresh / closed tab doesn't lose the deal. File objects
 *    cannot be serialized, so only each staged file's NAME and slot are
 *    recorded — on restore the analyst is told which files to re-attach.
 *  - R-015: the analyst's default Return Profile, preselected on new deals.
 *
 * Everything lives in `localStorage` (this browser only; nothing is sent to
 * the worker) and every access is wrapped in try/catch — private windows /
 * blocked storage simply mean no autosave, never a broken wizard.
 */

export const DRAFT_KEY = 'fondok:new-deal-draft:v1';
export const DEFAULT_RETURN_PROFILE_KEY = 'fondok:wizard:default-return-profile:v1';
export const DRAFT_DEBOUNCE_MS = 500;

/** A staged file as remembered by the draft (no bytes). */
export interface DraftFileRef {
  name: string;
  /** WizardCategory id of the slot it was staged in. */
  category: string;
  fiscal_year?: number | null;
}

export interface WizardDraft<F extends Record<string, unknown> = Record<string, unknown>> {
  v: 1;
  savedAt: number;
  step: number;
  fields: F;
  files: DraftFileRef[];
}

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function loadDraft<F extends Record<string, unknown>>(): WizardDraft<F> | null {
  try {
    const raw = storage()?.getItem(DRAFT_KEY);
    if (!raw) return null;
    const d = JSON.parse(raw) as Partial<WizardDraft<F>>;
    if (
      !d || d.v !== 1 || typeof d.savedAt !== 'number' || typeof d.step !== 'number'
      || !d.fields || typeof d.fields !== 'object'
    ) return null;
    const files = Array.isArray(d.files)
      ? d.files.filter((f): f is DraftFileRef =>
          !!f && typeof f.name === 'string' && typeof f.category === 'string')
      : [];
    return { v: 1, savedAt: d.savedAt, step: d.step, fields: d.fields as F, files };
  } catch {
    return null;
  }
}

export function saveDraft<F extends Record<string, unknown>>(draft: Omit<WizardDraft<F>, 'v'>): boolean {
  try {
    const s = storage();
    if (!s) return false;
    s.setItem(DRAFT_KEY, JSON.stringify({ v: 1, ...draft }));
    return true;
  } catch {
    return false;
  }
}

export function clearDraft(): void {
  try {
    storage()?.removeItem(DRAFT_KEY);
  } catch {
    /* storage unavailable — nothing to clear */
  }
}

export function loadDefaultReturnProfile(): string | null {
  try {
    const v = storage()?.getItem(DEFAULT_RETURN_PROFILE_KEY);
    return v && v.trim() ? v : null;
  } catch {
    return null;
  }
}

export function saveDefaultReturnProfile(id: string | null): void {
  try {
    const s = storage();
    if (!s) return;
    if (id) s.setItem(DEFAULT_RETURN_PROFILE_KEY, id);
    else s.removeItem(DEFAULT_RETURN_PROFILE_KEY);
  } catch {
    /* storage unavailable — default just isn't remembered */
  }
}

/** "just now" / "5 minutes ago" / "2 hours ago" / "3 days ago". */
export function relativeTime(then: number, now: number = Date.now()): string {
  const s = Math.max(0, Math.round((now - then) / 1000));
  if (s < 45) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} minute${m === 1 ? '' : 's'} ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} hour${h === 1 ? '' : 's'} ago`;
  const d = Math.round(h / 24);
  return `${d} day${d === 1 ? '' : 's'} ago`;
}
