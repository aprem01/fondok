'use client';

/**
 * DocumentsChecklist — the onboarding wizard's right-rail.
 *
 * FON-31: previously a standalone radial "IC coverage" ring that scored
 * a runnable deal as sub-100% and never told the user the ONE thing that
 * matters mid-upload — "can I run the model yet?". It now renders the
 * shared {@link DealReadinessSummary} (rail variant) from the same
 * {@link CompletenessResponse} shape the worker-fed workspace card uses,
 * computed client-side from the in-memory uploads. Wizard and workspace
 * can no longer drift.
 *
 * Locked product decision — only Financials hard-gate the wizard; that
 * gate lives in DocumentsStep + page.tsx. This rail only reflects it.
 */

import type { WizardCategory, WizardFile } from '@/lib/api';
import { WIZARD_CATEGORIES } from './DocumentsStep';
import {
  DealReadinessSummary,
  readinessFromWizardFiles,
} from './DealReadinessSummary';

export interface DocumentsChecklistProps {
  files: WizardFile[];
  /** Kept for API parity with the previous list — the collapsed rail
   *  doesn't expose per-row jump targets (the sidebar owns navigation),
   *  but the prop is accepted so the page wiring doesn't have to change. */
  onJumpTo?: (category: WizardCategory) => void;
  /** Accepted for API parity (the previous rail mirrored the active
   *  category from the sidebar). Unused in the collapsed rail. */
  activeCategory?: WizardCategory | null;
}

export function DocumentsChecklist(_props: DocumentsChecklistProps) {
  const counts: Record<string, number> = {};
  for (const c of WIZARD_CATEGORIES) counts[c.id] = 0;
  for (const f of _props.files) {
    if (counts[f.category] !== undefined) counts[f.category] += 1;
  }

  const data = readinessFromWizardFiles(WIZARD_CATEGORIES, counts);
  return <DealReadinessSummary data={data} variant="rail" />;
}
