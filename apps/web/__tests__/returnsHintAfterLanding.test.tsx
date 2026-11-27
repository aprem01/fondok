/**
 * Review 2026-11-27 — the Returns coach mark ("Why this number leads") is held
 * closed only WHILE the section is landing, and released when the landing
 * settles.
 *
 * Before: `hintsSuspended` was set for ANY URL carrying a Returns `sub` (a
 * reload, a bookmark) and nothing released it after the landing scroll
 * settled — only a view change inside the section did — so the mark never
 * opened on a reloaded `?sub=returns-summary`. Now `scrollToAnchorWhenReady`
 * fires `onSettled` once when the hold window ends, the user takes over, the
 * target never appears, or the landing is cancelled, and the section drops
 * the hold there.
 *
 * The component tests mount the real `ReturnsSection` with `CoachMark` mocked
 * to expose its `suspended` prop; the helper tests pin `onSettled` directly.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, act } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';
import { scrollToAnchorWhenReady } from '@/lib/anchorScroll';

// ── Harness (the same shape as returnsAnchorFirstClick.test.tsx) ─────
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
// The coach mark under test: render its `suspended` flag so the hold is observable.
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children, anchorId, suspended }: { children: React.ReactNode; anchorId: string; suspended?: boolean }) =>
    React.createElement(
      React.Fragment,
      null,
      React.createElement('span', { 'data-testid': `coach-${anchorId}`, 'data-suspended': suspended ? '1' : '0' }),
      children,
    ),
}));
vi.mock('@/components/help/Traced', () => ({
  Traced: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

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

const hint = () => screen.getByTestId('coach-returns-levered-irr');
const suspended = () => hint().getAttribute('data-suspended') === '1';

const originalScrollIntoView = Element.prototype.scrollIntoView;
beforeEach(() => {
  cleanup();
  Element.prototype.scrollIntoView = function () {};
  window.location.hash = '';
  nav.params = new URLSearchParams('');
  outputsRef.value = null;
});
afterEach(() => {
  cleanup();
  Element.prototype.scrollIntoView = originalScrollIntoView;
  window.location.hash = '';
  vi.useRealTimers();
});

describe('ReturnsSection — the coach mark is held only while landing', () => {
  it('reload on ?sub=returns-summary (outputs already in): held during the landing, released once the hold ends on the user\'s first wheel', async () => {
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    outputsRef.value = run();
    render(<ReturnsSection />);
    // The header landed at mount; the page is being held for it.
    expect(screen.getByTestId('overview-returns-header')).toBeInTheDocument();
    expect(suspended()).toBe(true);

    // The user takes over: the hold ends and the hint is free to open.
    act(() => { window.dispatchEvent(new Event('wheel')); });
    await waitFor(() => expect(suspended()).toBe(false));
  });

  it('reload on ?sub=returns-summary: released by itself when the hold window runs out, with no user action', async () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout', 'Date'] });
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    outputsRef.value = run();
    render(<ReturnsSection />);
    expect(suspended()).toBe(true);

    // Inside the window the hold stands...
    act(() => { vi.advanceTimersByTime(1000); vi.advanceTimersToNextFrame(); });
    expect(suspended()).toBe(true);
    // ...and at the deadline it settles: the hint opens on its own.
    act(() => { vi.advanceTimersByTime(2500); vi.advanceTimersToNextFrame(); vi.advanceTimersToNextFrame(); });
    expect(suspended()).toBe(false);
  });

  it('arriving by "View Returns →" (outputs not in yet): held while waiting and while landing, released after the landing settles', async () => {
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    const { rerender } = render(<ReturnsSection />);
    // No outputs: the placeholder renders, no hint in the DOM yet, the section waits for its header.
    expect(screen.queryByTestId('overview-returns-header')).toBeNull();

    outputsRef.value = run();
    rerender(<ReturnsSection />);
    await screen.findByTestId('overview-returns-header');
    // Landed, being held: the hint is still closed.
    expect(suspended()).toBe(true);

    act(() => { window.dispatchEvent(new Event('keydown')); });
    await waitFor(() => expect(suspended()).toBe(false));
  });

  it('plain ?tab=overview (no Returns view asked for): never held', () => {
    nav.params = new URLSearchParams('tab=overview');
    outputsRef.value = run();
    render(<ReturnsSection />);
    expect(suspended()).toBe(false);
  });
});

describe('scrollToAnchorWhenReady — onSettled', () => {
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

  it('fires once when the user takes over', () => {
    mount('a');
    const onSettled = vi.fn();
    const dispose = scrollToAnchorWhenReady('a', { onSettled });
    expect(onSettled).not.toHaveBeenCalled();
    window.dispatchEvent(new Event('wheel'));
    expect(onSettled).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new Event('touchstart'));
    dispose();
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it('fires once at the deadline while holding', () => {
    vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'setTimeout', 'clearTimeout', 'Date'] });
    mount('b');
    const onSettled = vi.fn();
    scrollToAnchorWhenReady('b', { onSettled, timeoutMs: 100 });
    vi.advanceTimersByTime(50);
    vi.advanceTimersToNextFrame();
    expect(onSettled).not.toHaveBeenCalled();
    vi.advanceTimersByTime(60);
    vi.advanceTimersToNextFrame();
    expect(onSettled).toHaveBeenCalledTimes(1);
    vi.advanceTimersToNextFrame();
    expect(onSettled).toHaveBeenCalledTimes(1);
  });

  it('fires once when the disposer cancels a pending landing, and once when the target never appears', () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
    const cancelled = vi.fn();
    const dispose = scrollToAnchorWhenReady('never-1', { onSettled: cancelled, timeoutMs: 100 });
    expect(cancelled).not.toHaveBeenCalled();
    dispose();
    dispose();
    expect(cancelled).toHaveBeenCalledTimes(1);

    const timedOut = vi.fn();
    scrollToAnchorWhenReady('never-2', { onSettled: timedOut, timeoutMs: 100 });
    vi.advanceTimersByTime(120);
    expect(timedOut).toHaveBeenCalledTimes(1);
  });

  it('a target inserted later lands, is held, and settles once on the user\'s first key', async () => {
    const root = document.createElement('section');
    document.body.appendChild(root);
    const onSettled = vi.fn();
    scrollToAnchorWhenReady('late', { root, onSettled });
    mount('late', root);
    await waitFor(() => expect(root.querySelector('#late')).not.toBeNull());
    // Give the MutationObserver its microtask.
    await new Promise((r) => setTimeout(r, 0));
    expect(onSettled).not.toHaveBeenCalled();
    window.dispatchEvent(new Event('keydown'));
    expect(onSettled).toHaveBeenCalledTimes(1);
  });
});
