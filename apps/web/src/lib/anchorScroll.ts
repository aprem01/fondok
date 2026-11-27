/**
 * Scroll an in-page anchor into view once it exists — and hold it there
 * while the page around it is still loading.
 *
 * FON-67 — the Cash Flow IRR footnote deep-links to `#returns-by-exit-year`,
 * a card inside Overview's Returns section. Following that link MOUNTS
 * Overview fresh (the project page renders one tab at a time), so at the
 * moment the section's effect reads the fragment the card is not in the DOM
 * yet (its Returns outputs are still loading), and the sections above it are
 * still filling in from their own fetches. A one-shot
 * `getElementById(id)?.scrollIntoView()` misses; so does a single retry
 * keyed on "the data arrived", because the page above keeps growing after
 * that scroll and pushes the card back out of the viewport.
 *
 * This helper, for a bounded window (default 3 s):
 *
 *  1. scrolls the target as soon as it is in the DOM — now, or when a later
 *     commit inserts it (a MutationObserver on `root`, else a rAF poll),
 *     calling `onPending` once when it has to wait;
 *  2. then keeps it at the top while content above it is still loading: each
 *     frame it compares the target's DOCUMENT position (viewport top plus
 *     `scrollY`, so the user's own scrolling never counts as a move) and
 *     re-pins instantly when it moved; the user's first wheel / touch / key
 *     ends the pinning so the page never fights them;
 *  3. gives up at the deadline, and cancels on the returned disposer (the
 *     caller returns it from its effect, so an unmount or a URL change stops
 *     everything).
 */
export interface AnchorScrollOptions {
  /** Where to watch for the target being inserted (default: `document.body`). */
  root?: Element | null;
  /** Called once when the target is not yet in the DOM (e.g. scroll a parent meanwhile). */
  onPending?: () => void;
  /** How long to wait for the target and hold it in place (default 3000 ms). */
  timeoutMs?: number;
  /** The first scroll's behaviour (default `'smooth'`); re-pins are instant. */
  behavior?: ScrollBehavior;
  /**
   * Called exactly once when the landing is over and the page is the user's
   * again: the hold window ended (deadline), the user took over (first wheel
   * / touch / key), the target never appeared before the deadline, or the
   * caller's disposer cancelled it. Review 2026-11-27: the Returns coach mark
   * is held closed while landing and released here — before, a URL carrying
   * a Returns `sub` (a reload, a bookmark) held it closed for good.
   */
  onSettled?: () => void;
}

export const ANCHOR_SCROLL_TIMEOUT_MS = 3000;

const USER_INTENT_EVENTS = ['wheel', 'touchstart', 'keydown'] as const;

export function scrollToAnchorWhenReady(id: string, opts: AnchorScrollOptions = {}): () => void {
  if (typeof document === 'undefined' || typeof window === 'undefined') return () => {};
  const { root = document.body, onPending, onSettled, timeoutMs = ANCHOR_SCROLL_TIMEOUT_MS, behavior = 'smooth' } = opts;
  const deadline = Date.now() + timeoutMs;
  let cancelled = false;
  let observer: MutationObserver | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let frame: number | null = null;
  const hasRaf = typeof requestAnimationFrame === 'function';

  const stop = () => {
    if (cancelled) return; // settle exactly once, whichever path got here first
    cancelled = true;
    observer?.disconnect();
    observer = null;
    if (timer != null) clearTimeout(timer);
    timer = null;
    if (frame != null && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame);
    frame = null;
    for (const ev of USER_INTENT_EVENTS) window.removeEventListener(ev, stop);
    onSettled?.();
  };

  /** Document-relative top: unchanged by scrolling, changed by layout above. */
  const docTop = (el: Element) => el.getBoundingClientRect().top + window.scrollY;

  const hold = (el: Element) => {
    if (!hasRaf) {
      // Nothing to hold with: the landing is the one scroll, and it is over.
      stop();
      return;
    }
    let last = docTop(el);
    const tick = () => {
      if (cancelled) return;
      if (Date.now() >= deadline || !el.isConnected) {
        stop();
        return;
      }
      const now = docTop(el);
      if (Math.abs(now - last) > 1) {
        el.scrollIntoView?.({ behavior: 'auto', block: 'start' });
        last = docTop(el);
      }
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
  };

  const land = (el: Element) => {
    observer?.disconnect();
    observer = null;
    el.scrollIntoView?.({ behavior, block: 'start' });
    for (const ev of USER_INTENT_EVENTS) window.addEventListener(ev, stop, { passive: true });
    hold(el);
  };

  const now = document.getElementById(id);
  if (now) {
    land(now);
    return stop;
  }

  onPending?.();
  timer = setTimeout(stop, timeoutMs);
  if (typeof MutationObserver !== 'undefined' && root) {
    observer = new MutationObserver(() => {
      if (cancelled) return;
      const el = document.getElementById(id);
      if (el) land(el);
    });
    observer.observe(root, { childList: true, subtree: true });
  } else if (hasRaf) {
    const poll = () => {
      if (cancelled) return;
      const el = document.getElementById(id);
      if (el) {
        land(el);
        return;
      }
      frame = requestAnimationFrame(poll);
    };
    frame = requestAnimationFrame(poll);
  }
  return stop;
}
