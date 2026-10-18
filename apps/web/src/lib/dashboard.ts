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

/**
 * Pipeline health from what each deal has actually done — documents, a model
 * run, an IC-Ready status — rather than the worker's onboarding state, which
 * labelled every fully-modelled deal "Onboarding".
 */
export function pipelineHealth(deals: PipelineDealRow[]): Array<{ label: string; n: number }> {
  let noDocs = 0;
  let notModelled = 0;
  let modelled = 0;
  let icReady = 0;
  for (const d of deals) {
    if ((d.status ?? '').toLowerCase() === 'ic ready') { icReady += 1; continue; }
    if ((d.document_count ?? 0) === 0) { noDocs += 1; continue; }
    if (d.levered_irr == null && !d.last_engine_run_at) { notModelled += 1; continue; }
    modelled += 1;
  }
  return [
    { label: 'Modelled', n: modelled },
    { label: 'Documents in, not yet modelled', n: notModelled },
    { label: 'No documents yet', n: noDocs },
    { label: 'IC Ready', n: icReady },
  ].filter((r) => r.n > 0);
}
