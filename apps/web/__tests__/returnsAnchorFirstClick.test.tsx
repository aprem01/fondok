/**
 * FON-67 — "Returns by exit year →" must land on the table on the FIRST click.
 *
 * Sam (deal d0bfe816): "My first click didn't register, and the second one
 * worked." The link is followed from the Cash Flow tab, so Overview — and
 * with it the Returns section — mounts fresh: the Returns outputs are still
 * loading when the section's effect reads the fragment, and the card renders
 * only after they land (and, here, one tick after that, to stand in for any
 * later commit). The old code tried the scroll twice — at mount, and once
 * more when the outputs flipped in — and gave up. The section must instead
 * land on the card whenever it appears, exactly once, and stop cleanly on
 * unmount / a URL change / its deadline.
 *
 * The component tests mount the real `ReturnsSection`; the card is wrapped
 * so it renders one tick after its parent (null first). The helper tests
 * exercise `scrollToAnchorWhenReady` directly: deadline, disposer, and the
 * bounded re-pin while content above the target is still loading.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { EXIT_SCHEDULE_ANCHOR_ID } from '@/lib/returnsSection';
import { scrollToAnchorWhenReady } from '@/lib/anchorScroll';

// ── Harness ─────────────────────────────────────────────────────────
const nav = vi.hoisted(() => ({ params: new URLSearchParams(''), push: vi.fn(), replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'deal-1' }),
  useRouter: () => ({ push: nav.push, replace: nav.replace, prefetch: vi.fn(), back: vi.fn() }),
  useSearchParams: () => nav.params,
  usePathname: () => '/projects/deal-1',
}));

const outputsRef = vi.hoisted(() => ({ value: null as unknown }));
vi.mock('@/lib/hooks/useEngineOutputs', async () => {
  const actual = await vi.importActual<typeof import('@/lib/hooks/useEngineOutputs')>('@/lib/hooks/useEngineOutputs');
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: outputsRef.value, previous: null, loading: false, settled: true, lastRunAt: null, error: null,
      refresh: vi.fn(async () => {}),
    }),
  };
});
vi.mock('@/lib/hooks/useDealProvenance', () => ({ useSource: () => null }));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(async () => {}), running: false, status: 'idle', error: null }),
}));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: { id: 'deal-1', name: 'Harbor Hotel', keys: 132, deal_type: 'acquisition', return_profile: 'value-add', field_overrides: {} },
    status: null, loading: false, error: null, workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/api', async () => {
  const { withQuietWorker } = await import('./helpers/quietWorker');
  const actual = withQuietWorker(await vi.importActual<typeof import('@/lib/api')>('@/lib/api'));
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: { ...actual.api, engines: { ...actual.api.engines, returnsPreview: vi.fn() } },
  };
});
vi.mock('@/components/project/PricingSensitivityPanel', () => ({ default: () => null }));
vi.mock('@/components/project/MaxPricePanel', () => ({ default: () => null }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/components/help/Traced', () => ({
  Traced: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

// The card renders one tick AFTER its parent commits — null first, then the
// real card. This is the shape that defeated the old one-retry approach: the
// retry keyed on "outputs arrived" ran in a commit the card was not part of.
vi.mock('@/components/project/ExitScheduleCard', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/project/ExitScheduleCard')>();
  const ReactMod = await import('react');
  const DelayedCard = (props: Parameters<typeof actual.ExitScheduleCard>[0]) => {
    const [ready, setReady] = ReactMod.useState(false);
    ReactMod.useEffect(() => {
      const t = setTimeout(() => setReady(true), 0);
      return () => clearTimeout(t);
    }, []);
    return ready ? ReactMod.createElement(actual.ExitScheduleCard, props) : null;
  };
  return { ...actual, ExitScheduleCard: DelayedCard };
});

import { ReturnsSection } from '@/components/project/ReturnsTab';

function run(): EngineOutputsResponse {
  return {
    deal_id: 'deal-1',
    engines: {
      returns: {
        deal_id: 'deal-1', engine: 'returns', status: 'complete', summary: '',
        outputs: { levered_irr: 0.2301, unlevered_irr: 0.161, equity_multiple: 2.37, hold_years: 5, exit_cap_rate: 0.07 },
        inputs: {}, error: null, runtime_ms: 1, started_at: null, completed_at: null, run_id: 'run-1',
      },
    },
  } as unknown as EngineOutputsResponse;
}

type ScrollCall = { el: Element; opts: unknown };
let calls: ScrollCall[];
const originalScrollIntoView = Element.prototype.scrollIntoView;
const onCard = () => calls.filter((c) => (c.el as HTMLElement).id === EXIT_SCHEDULE_ANCHOR_ID);
const onSection = () => calls.filter((c) => (c.el as HTMLElement).id === 'overview-returns');

beforeEach(() => {
  cleanup();
  calls = [];
  Element.prototype.scrollIntoView = function (this: Element, opts?: unknown) { calls.push({ el: this, opts }); };
  window.location.hash = '';
  nav.params = new URLSearchParams('');
  outputsRef.value = null;
});
afterEach(() => {
  cleanup();
  Element.prototype.scrollIntoView = originalScrollIntoView;
  window.location.hash = '';
});

describe('FON-67 — first click: the section mounts before its outputs and its card', () => {
  it('outputs arriving after the first render, card a tick later → the card is scrolled exactly once', async () => {
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    window.location.hash = `#${EXIT_SCHEDULE_ANCHOR_ID}`;
    const { rerender } = render(<ReturnsSection />);
    expect(screen.queryByTestId('exit-schedule-card')).toBeNull();
    // Meanwhile the section is brought into view, once.
    expect(onSection()).toHaveLength(1);
    expect(onCard()).toHaveLength(0);

    outputsRef.value = run();
    rerender(<ReturnsSection />);
    // The data flipped in but the card is not in this commit yet.
    expect(screen.queryByTestId('exit-schedule-card')).toBeNull();
    expect(onCard()).toHaveLength(0);

    const card = await screen.findByTestId('exit-schedule-card');
    await waitFor(() => expect(onCard()).toHaveLength(1));
    expect(onCard()[0].el).toBe(card);
    expect(onCard()[0].opts).toEqual({ behavior: 'smooth', block: 'start' });

    // Once. Further renders neither re-scroll the card nor the section.
    rerender(<ReturnsSection />);
    await new Promise((r) => setTimeout(r, 25));
    expect(onCard()).toHaveLength(1);
    expect(onSection()).toHaveLength(1);
  });

  it('card already in the DOM when the URL asks for it → scrolled exactly once, immediately, section untouched', async () => {
    // Mounted on Overview with no Returns view requested: nothing scrolls.
    nav.params = new URLSearchParams('tab=overview');
    outputsRef.value = run();
    const { rerender } = render(<ReturnsSection />);
    const card = await screen.findByTestId('exit-schedule-card');
    expect(calls).toHaveLength(0);

    // Now the URL asks for the card (the link followed while already mounted).
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    window.location.hash = `#${EXIT_SCHEDULE_ANCHOR_ID}`;
    rerender(<ReturnsSection />);
    expect(onCard()).toHaveLength(1);
    expect(onCard()[0].el).toBe(card);
    expect(onSection()).toHaveLength(0);

    rerender(<ReturnsSection />);
    await new Promise((r) => setTimeout(r, 25));
    expect(onCard()).toHaveLength(1);
  });

  it('unmounting before the outputs land cancels the pending request — nothing scrolls later', async () => {
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    window.location.hash = `#${EXIT_SCHEDULE_ANCHOR_ID}`;
    const { unmount } = render(<ReturnsSection />);
    expect(onSection()).toHaveLength(1);
    unmount();
    // A card with the anchor id appearing anywhere afterwards is not ours to scroll.
    const stray = document.createElement('div');
    stray.id = EXIT_SCHEDULE_ANCHOR_ID;
    document.body.appendChild(stray);
    await new Promise((r) => setTimeout(r, 25));
    expect(onCard()).toHaveLength(0);
    stray.remove();
  });
});

describe('scrollToAnchorWhenReady', () => {
  const mount = (id: string, parent: Element = document.body) => {
    const el = document.createElement('div');
    el.id = id;
    parent.appendChild(el);
    return el;
  };
  afterEach(() => {
    document.body.replaceChildren();
    vi.useRealTimers();
  });

  it('target present → one smooth scroll, no onPending', () => {
    const el = mount('present');
    const onPending = vi.fn();
    const dispose = scrollToAnchorWhenReady('present', { onPending });
    expect(onPending).not.toHaveBeenCalled();
    expect(calls).toEqual([{ el, opts: { behavior: 'smooth', block: 'start' } }]);
    dispose();
  });

  it('target absent → onPending once, then one scroll when it is inserted under root', async () => {
    const root = document.createElement('section');
    document.body.appendChild(root);
    const onPending = vi.fn();
    const dispose = scrollToAnchorWhenReady('late', { root, onPending });
    expect(onPending).toHaveBeenCalledTimes(1);
    expect(calls).toHaveLength(0);
    const el = mount('late', root);
    await waitFor(() => expect(calls).toHaveLength(1));
    expect(calls[0]).toEqual({ el, opts: { behavior: 'smooth', block: 'start' } });
    dispose();
  });

  it('the disposer cancels a pending request', async () => {
    const dispose = scrollToAnchorWhenReady('cancelled');
    dispose();
    mount('cancelled');
    await new Promise((r) => setTimeout(r, 25));
    expect(calls).toHaveLength(0);
  });

  it('gives up at the deadline — a target that appears afterwards is left alone', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    scrollToAnchorWhenReady('too-late', { timeoutMs: 50 });
    vi.advanceTimersByTime(60);
    vi.useRealTimers();
    mount('too-late');
    await new Promise((r) => setTimeout(r, 25));
    expect(calls).toHaveLength(0);
  });

  it('re-pins instantly while content above moves the target, and stops on the first user wheel', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout', 'Date'] });
    const el = mount('pinned');
    let top = 100;
    el.getBoundingClientRect = () => ({ top, bottom: top + 10, left: 0, right: 0, width: 0, height: 10, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
    const dispose = scrollToAnchorWhenReady('pinned');
    expect(calls).toHaveLength(1);

    // A frame with no layout change: no re-pin.
    vi.advanceTimersToNextFrame();
    expect(calls).toHaveLength(1);

    // The page above grew (the target's document position moved): re-pin, instantly.
    top = 900;
    vi.advanceTimersToNextFrame();
    expect(calls).toHaveLength(2);
    expect(calls[1]).toEqual({ el, opts: { behavior: 'auto', block: 'start' } });

    // The user takes over: no more pinning, however the layout moves.
    window.dispatchEvent(new Event('wheel'));
    top = 1500;
    vi.advanceTimersToNextFrame();
    vi.advanceTimersToNextFrame();
    expect(calls).toHaveLength(2);
    dispose();
  });

  it('stops pinning at the deadline', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout', 'Date'] });
    const el = mount('deadline');
    let top = 100;
    el.getBoundingClientRect = () => ({ top, bottom: top + 10, left: 0, right: 0, width: 0, height: 10, x: 0, y: top, toJSON: () => ({}) }) as DOMRect;
    scrollToAnchorWhenReady('deadline', { timeoutMs: 100 });
    expect(calls).toHaveLength(1);
    vi.advanceTimersByTime(120);
    top = 900;
    vi.advanceTimersToNextFrame();
    vi.advanceTimersToNextFrame();
    expect(calls).toHaveLength(1);
  });
});
