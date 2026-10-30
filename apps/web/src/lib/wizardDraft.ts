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
 *
 * FON-79 — once the wizard has a Deal Name and the worker can hold a draft,
 * the deal row on the worker is the truth (``lib/hooks/useServerDraft``).
 * This module then keeps only the POINTER to that row
 * (``{draftDealId, savedAt}``) so the wizard can reopen the right draft
 * without a round trip. The local draft above stays for the pre-name phase
 * and for a worker that cannot hold drafts.
 */

export const DRAFT_KEY = 'fondok:new-deal-draft:v1';
export const DRAFT_POINTER_KEY = 'fondok:new-deal-draft-id:v1';
export const DEFAULT_RETURN_PROFILE_KEY = 'fondok:wizard:default-return-profile:v1';
export const DRAFT_DEBOUNCE_MS = 500;
/** A draft nobody has touched for this many days carries a note on its
 *  Projects card (go-live plan A.6.2: a nudge, never an automatic deletion). */
export const STALE_DRAFT_DAYS = 30;

/** The wizard's resume payload as stored on the worker (``deals.draft_wizard``):
 *  the analyst's text as typed — a price range, a market without an id, UI
 *  state — so resume is lossless. The normalized deal columns travel beside
 *  it on every save, never instead of it. */
export interface DraftWizardPayload<F extends Record<string, unknown> = Record<string, unknown>> {
  v: 1;
  step: number;
  fields: F;
}

/** The browser's pointer to the server-side draft. */
export interface DraftPointer {
  draftDealId: string;
  savedAt: number;
}

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

export function loadDraftPointer(): DraftPointer | null {
  try {
    const raw = storage()?.getItem(DRAFT_POINTER_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Partial<DraftPointer>;
    if (!p || typeof p.draftDealId !== 'string' || !p.draftDealId.trim() || typeof p.savedAt !== 'number') {
      return null;
    }
    return { draftDealId: p.draftDealId, savedAt: p.savedAt };
  } catch {
    return null;
  }
}

export function saveDraftPointer(draftDealId: string, savedAt: number = Date.now()): boolean {
  try {
    const s = storage();
    if (!s) return false;
    s.setItem(DRAFT_POINTER_KEY, JSON.stringify({ draftDealId, savedAt } satisfies DraftPointer));
    return true;
  } catch {
    return false;
  }
}

export function clearDraftPointer(): void {
  try {
    storage()?.removeItem(DRAFT_POINTER_KEY);
  } catch {
    /* storage unavailable — nothing to clear */
  }
}

/** Whole days since ``updatedAt`` (an ISO timestamp), or null when it does
 *  not parse — the card then shows no note rather than a made-up age. */
export function daysUntouched(updatedAt: string | null | undefined, now: number = Date.now()): number | null {
  if (!updatedAt) return null;
  const t = new Date(updatedAt).getTime();
  if (!Number.isFinite(t)) return null;
  return Math.max(0, Math.floor((now - t) / 86_400_000));
}

/** "Draft · untouched for 31 days" once a draft has sat for ``STALE_DRAFT_DAYS``
 *  or longer; null before that. The number is the draft's own age. */
export function staleDraftNote(updatedAt: string | null | undefined, now: number = Date.now()): string | null {
  const days = daysUntouched(updatedAt, now);
  if (days == null || days < STALE_DRAFT_DAYS) return null;
  return `Draft · untouched for ${days} days`;
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
