/**
 * Sam QA 2026-11-27 (FON-67, deals b67bfdee / d0bfe816) — following the IC
 * Memo's "View Returns →" opened the "Why this number leads" hint on its own,
 * over a page that was still scrolling into place.
 *
 * Why it opened: `CoachMark` renders the first un-dismissed mark of a view
 * as soon as it mounts (`fondok:coachmark:<anchorId>:dismissed` absent in
 * localStorage — a first visit), whatever brought the user there. The
 * Returns section now holds its mark closed (`suspended`, not dismissed)
 * while it was reached by link, and releases it on the first view change
 * made inside the section. The REAL `CoachMark` is mounted here.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import React from 'react';
import type { EngineOutputsResponse } from '@/lib/api';

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
vi.mock('@/components/help/Traced', () => ({
  Traced: ({ children }: { children: React.ReactNode }) => React.createElement(React.Fragment, null, children),
}));
vi.mock('@/lib/hooks/useFlash', () => ({ useFlash: () => false }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));

import { ReturnsSection } from '@/components/project/ReturnsTab';

const HINT_TITLE = 'Why this number leads';

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

const hint = () => screen.queryByRole('dialog', { name: HINT_TITLE });
const originalScrollIntoView = Element.prototype.scrollIntoView;

beforeEach(() => {
  cleanup();
  window.localStorage.clear(); // a first visit: nothing dismissed, hints on
  Element.prototype.scrollIntoView = function () {};
  window.location.hash = '';
  nav.params = new URLSearchParams('');
  outputsRef.value = run();
});
afterEach(() => {
  cleanup();
  Element.prototype.scrollIntoView = originalScrollIntoView;
  window.localStorage.clear();
});

describe('Sam 2026-11-27 — the Returns hint does not open on its own when the section is reached by link', () => {
  it('control: on a plain Overview visit the first-visit hint opens (the behaviour that is being held, not removed)', async () => {
    nav.params = new URLSearchParams('tab=overview');
    render(<ReturnsSection />);
    await screen.findByTestId('overview-returns-header');
    expect(hint()).not.toBeNull();
  });

  it('IC Memo entry (`?tab=overview&sub=returns-summary`): the hint stays closed, and is NOT dismissed', async () => {
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    render(<ReturnsSection />);
    await screen.findByTestId('overview-returns-header');
    expect(hint()).toBeNull();
    // Held, not dismissed: the dismissal key is not written.
    expect(window.localStorage.getItem('fondok:coachmark:returns-levered-irr:dismissed')).toBeNull();
    // The IRR tile the hint belongs to is on screen — the mark is just closed.
    expect(screen.getByText('Levered IRR')).toBeInTheDocument();
  });

  it('outputs arriving after the link was followed: still closed once the Summary renders', async () => {
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    outputsRef.value = null;
    const { rerender } = render(<ReturnsSection />);
    expect(screen.queryByTestId('overview-returns-header')).toBeNull();
    outputsRef.value = run();
    rerender(<ReturnsSection />);
    await screen.findByTestId('overview-returns-header');
    expect(hint()).toBeNull();
  });

  it('the hold ends on the first view change made inside the section', async () => {
    nav.params = new URLSearchParams('tab=overview&sub=returns-summary');
    render(<ReturnsSection />);
    await screen.findByTestId('overview-returns-header');
    expect(hint()).toBeNull();
    // The user acts inside the section: Sensitivities, then back to the Summary.
    fireEvent.click(screen.getByRole('tab', { name: 'Sensitivities' }));
    fireEvent.click(screen.getByRole('tab', { name: 'Returns Summary' }));
    expect(await screen.findByRole('dialog', { name: HINT_TITLE })).toBeInTheDocument();
  });
});
