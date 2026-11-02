/**
 * Wave 5 — "this deal changed on the server": one signal every `useDeal` for
 * that deal listens to, so two components on the same page (the header
 * status pill, the IC Memo card) never hold different copies of the deal
 * after a write. Writers call `notifyDealChanged(id)` after the request
 * settles; each `useDeal(id)` re-reads the row.
 */
type Listener = (dealId: string) => void;

const listeners = new Set<Listener>();

export function notifyDealChanged(dealId: string): void {
  for (const l of [...listeners]) l(dealId);
}

/** Subscribe; returns the unsubscribe function. */
export function onDealChanged(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
