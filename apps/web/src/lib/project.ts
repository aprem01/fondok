/**
 * Project — the deal page's header view-model, built from the worker's deal
 * row (`WorkerDeal`) in `app/projects/[id]/page.tsx` and handed to the tabs
 * that still take it (IC Memo, Export).
 *
 * Standing rule (no invented data): every numeric field is `number | null`,
 * and `null` means the worker has not produced the value. A null never
 * renders as 0 — the header prints a dash (`formatKeys`, the confidence pill
 * reads "Awaiting docs"). Nothing here is ever built from a fixture.
 */
import type { DealStage, ProjectStatus, Risk } from '@/lib/catalogs';

export interface Project {
  /** 0 for a worker deal — the route id is the deal's real identifier. */
  id: number;
  name: string;
  city: string;
  /** Room count from the deal row; null until the OM extraction fills it. */
  keys: number | null;
  service: string;
  /** Null when the deal row carries none — no badge is shown, never an invented "Draft". */
  status: ProjectStatus | null;
  /** Null when the deal row carries none — no badge is shown, never an invented "Teaser". */
  dealStage: DealStage | null;
  risk: Risk;
  /** Whole-percent confidence, or null when the worker has not scored the deal. */
  aiConfidence: number | null;
  assignee: string;
  createdAt?: string;
  updatedAt: string;
}

/**
 * The deal row's `ai_confidence` (0..1) as a whole percent for display, or
 * null when the worker has not scored the deal (`null` / `undefined`). The
 * worker leaves the field null until an extraction scores it, so a number —
 * including a genuine 0 — is a measured value and renders as one; nothing
 * here guesses which numbers are "really" unscored.
 */
export function displayConfidence(aiConfidence: number | null | undefined): number | null {
  if (typeof aiConfidence !== 'number' || !Number.isFinite(aiConfidence)) return null;
  return Math.round(aiConfidence * 100);
}
