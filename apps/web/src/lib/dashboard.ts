/**
 * Dashboard copy and derivations — kept out of the page file so they can be
 * unit-tested (a Next.js page may only export its component).
 */
import type { PipelineDealRow } from '@/lib/api';

/**
 * "1 of 1 deals with a target clears it · 15 have no target set" — says what
 * the fraction is over, so "1/1" among 16 deals never reads as one deal.
 */
export function meetingTargetSub(dealCount: number, withTarget: number, met: number): string {
  if (withTarget === 0) return dealCount === 0 ? '' : 'no deal has a target IRR set';
  const noTarget = Math.max(0, dealCount - withTarget);
  const head = `${met} of ${withTarget} deal${withTarget === 1 ? '' : 's'} with a target clear${met === 1 ? 's' : ''} it`;
  return noTarget > 0 ? `${head} · ${noTarget} ha${noTarget === 1 ? 's' : 've'} no target set` : head;
}

/** Wave 5 — the statuses the team sets, counted by name (lifecycle order). */
const TEAM_SET_STATUSES = ['Ready for Review', 'IC Ready', 'IC Approved', 'IC Not Approved'] as const;

/**
 * Pipeline health from what each deal has actually done — documents, a model
 * run — and the review / committee status the team set, rather than the
 * worker's onboarding state, which labelled every fully-modelled deal
 * "Onboarding". Statuses are matched exactly (the worker stores only the
 * seven values).
 */
export function pipelineHealth(deals: PipelineDealRow[]): Array<{ label: string; n: number }> {
  let noDocs = 0;
  let notModelled = 0;
  let modelled = 0;
  const byStatus = new Map<string, number>(TEAM_SET_STATUSES.map((s) => [s, 0]));
  for (const d of deals) {
    if (byStatus.has(d.status)) { byStatus.set(d.status, (byStatus.get(d.status) ?? 0) + 1); continue; }
    if ((d.document_count ?? 0) === 0) { noDocs += 1; continue; }
    if (d.levered_irr == null && !d.last_engine_run_at) { notModelled += 1; continue; }
    modelled += 1;
  }
  return [
    { label: 'Modelled', n: modelled },
    { label: 'Documents in, not yet modelled', n: notModelled },
    { label: 'No documents yet', n: noDocs },
    ...TEAM_SET_STATUSES.map((s) => ({ label: s as string, n: byStatus.get(s) ?? 0 })),
  ].filter((r) => r.n > 0);
}
