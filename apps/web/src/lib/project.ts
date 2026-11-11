/**
 * Project — the deal page's header view-model, built from the worker's deal
 * row (`WorkerDeal`) in `app/projects/[id]/page.tsx` and handed to the tabs
 * that still take it (IC Memo, Export).
 *
 * Standing rule (no invented data): every numeric field is `number | null`,
 * and `null` means the worker has not produced the value. A null never
 * renders as 0 — the header prints a dash (`formatKeys`, the confidence pill
 * reads "AI Confidence —"). Nothing here is ever built from a fixture.
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
  /** The deal row's risk, or null when it has none (renders "—", never a default). */
  risk: Risk | null;
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

/**
 * FON-80 — Model/Data Readiness: where a deal is in Fondok's processing
 * pipeline, read from facts the worker already records. It replaced the
 * "AI Confidence" score on the Projects list: a 0–100 number on a card
 * read as a verdict on the deal, when it only ever measured extraction.
 *
 * The steps, each backed by one fact:
 *   0 `no_documents`  — the worker reports zero documents.
 *   1 `uploaded`      — documents exist, no extraction has scored yet.
 *   2 `extracted`     — `deals.ai_confidence` is set (the rollup of every
 *                       extracted document's confidence_report.overall).
 *   3 `model_run`     — the pipeline row carries `last_engine_run_at`.
 *   4 `validated`     — the deal's onboarding state is READY.
 * Nothing is interpolated: when none of the facts is known (an older worker
 * that reports no document count and no run) the result is null → "—".
 */
export type ReadinessStep = 'no_documents' | 'uploaded' | 'extracted' | 'model_run' | 'validated';

export interface Readiness {
  step: ReadinessStep;
  /** 0–4, the step's position — for a step indicator, never a percentage. */
  index: number;
  label: string;
  /** The extraction confidence as a whole percent, when the deal has one. */
  extractionConfidence: number | null;
}

export const READINESS_STEPS = 5;

export function modelDataReadiness(input: {
  documentCount: number | null | undefined;
  aiConfidence: number | null | undefined;
  lastEngineRunAt: string | null | undefined;
  state: string | null | undefined;
}): Readiness | null {
  const extractionConfidence = displayConfidence(input.aiConfidence);
  const docs = typeof input.documentCount === 'number' ? input.documentCount : null;
  const build = (step: ReadinessStep, index: number, label: string): Readiness => ({
    step, index, label, extractionConfidence,
  });
  if (input.state === 'READY') return build('validated', 4, 'Validated');
  if (input.lastEngineRunAt) return build('model_run', 3, 'Model run');
  if (extractionConfidence != null) return build('extracted', 2, 'Extracted');
  if (docs != null && docs > 0) return build('uploaded', 1, 'Documents uploaded');
  if (docs === 0) return build('no_documents', 0, 'No documents');
  return null;
}
