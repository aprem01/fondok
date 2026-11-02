/**
 * Wave 5 — the seven-status deal lifecycle, web side.
 *
 * The vocabulary lives in `@/lib/catalogs` (`DEAL_STATUSES`,
 * `MANUAL_DEAL_STATUSES`, `RUN_GATED_DEAL_STATUSES`); this module holds what
 * the status control needs on top: the "has the model run" hint and the
 * explanatory copy. The worker (`POST /deals/{id}/status`) is the authority —
 * it refuses IC Ready / IC Approved / IC Not Approved before a completed run
 * with its own sentence, which the control shows verbatim.
 */
import { api, type EngineOutputsResponse, type WorkerDeal } from '@/lib/api';
import { notifyDealChanged } from '@/lib/dealEvents';
import {
  MANUAL_DEAL_STATUSES,
  RUN_GATED_DEAL_STATUSES,
  type ManualDealStatus,
  type ProjectStatus,
} from '@/lib/catalogs';

/** The served run finished with no engine failed: every engine complete or
 *  not applicable, at least one complete. Mirrors the worker's gate for IC
 *  Ready / IC Approved / IC Not Approved; a hint only — the worker decides. */
export function hasCompletedModelRun(outputs: EngineOutputsResponse | null | undefined): boolean {
  const rows = Object.values(outputs?.engines ?? {});
  if (rows.length === 0) return false;
  const statuses = rows.map((r) => r?.status);
  return statuses.includes('complete') && statuses.every((s) => s === 'complete' || s === 'not_applicable');
}

/** The one way the web writes a status: `POST /deals/{id}/status` carrying
 *  the status the control was showing (`expected`), then — success or
 *  refusal — every `useDeal` for the deal re-reads it, so the header pill
 *  and the IC Memo card can never disagree or act on a stale copy. */
export async function setDealStatus(
  dealId: string,
  next: ManualDealStatus,
  expected: ProjectStatus | null,
): Promise<WorkerDeal> {
  try {
    return await api.deals.setStatus(dealId, next, expected);
  } finally {
    notifyDealChanged(dealId);
  }
}

export function needsModelRun(status: ProjectStatus): boolean {
  return (RUN_GATED_DEAL_STATUSES as readonly string[]).includes(status);
}

/** The statuses the control offers for a deal at `current`: an archived deal
 *  is only restored (to In Progress); a draft offers nothing (it follows the
 *  wizard); every other deal offers the five manual statuses. */
export function settableStatuses(current: ProjectStatus | null): readonly ManualDealStatus[] {
  if (current === 'Draft' || current == null) return [];
  if (current === 'Archived') return ['In Progress'];
  return MANUAL_DEAL_STATUSES;
}

/** One line per status — the control's help text and Methodology §1 say the same. */
export const DEAL_STATUS_HELP: Readonly<Record<ProjectStatus, string>> = {
  'Draft': 'Setup in the New Deal wizard is not finished.',
  'In Progress': 'Set when setup is finished; the deal is being underwritten.',
  'Ready for Review': 'The analyst is done and asks for review.',
  'IC Ready': 'Cleared for the investment committee. Needs a completed model run with no failed engine.',
  'IC Approved': "The committee's decision: approved. Needs a completed model run with no failed engine.",
  'IC Not Approved': "The committee's decision: not approved. Needs a completed model run with no failed engine.",
  'Archived': 'Out of the active pipeline, not deleted. Restore it to In Progress.',
};

/** The Pipeline page's onboarding-state column (`deals.state`), labelled as
 *  its own filter labels it. It used to read "Draft" / "Active" — words that
 *  now belong to the deal status (Draft = the wizard is unfinished). */
export function onboardingStateLabel(state: string): string {
  if (state === 'ONBOARDING') return 'Onboarding';
  if (state === 'VALIDATING') return 'Validating';
  if (state === 'READY') return 'Ready';
  return state;
}
