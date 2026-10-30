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
  status: ProjectStatus;
  dealStage: DealStage;
  risk: Risk;
  /** Whole-percent confidence, or null before any field has been scored. */
  aiConfidence: number | null;
  assignee: string;
  docs: string;
  createdAt?: string;
  updatedAt: string;
  noDocs?: boolean;
}

/**
 * The deal row's `ai_confidence` (0..1) as a whole percent for display, or
 * null when there is nothing to show. The worker persists 0.0 on create and
 * only a scored extraction moves it, so a 0 is "no field scored yet", not a
 * measured 0% — it renders as the awaiting state, never as a "0%" bar.
 */
export function displayConfidence(aiConfidence: number | null | undefined): number | null {
  if (typeof aiConfidence !== 'number' || !Number.isFinite(aiConfidence) || aiConfidence <= 0) return null;
  return Math.round(aiConfidence * 100);
}
