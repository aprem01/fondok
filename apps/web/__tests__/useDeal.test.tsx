/**
 * Wave 4 reliability fix — Bug #1 regression suite (web).
 *
 * The deal-detail page used to fall back to ``mockProjects[0]`` (=
 * Hilton Garden Inn Downtown, Austin) whenever a UUID deal's worker
 * fetch was in flight or errored. The result: an analyst visiting
 * ``/projects/<their-uuid>`` saw a completely different deal's name /
 * city / brand. These tests guarantee the page never renders a mock
 * deal's identifiers when the requested id is a UUID.
 *
 * Strategy: mock the ``useDeal`` hook directly and render the page,
 * then assert that the rendered tree contains the deal-id placeholder
 * (loading skeleton) or the error card, NEVER a mock-data name.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';

// next/navigation is invoked by the page through useParams /
// useSearchParams / useRouter. Replace them with deterministic stubs.
vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'fff00000-0000-0000-0000-000000000aaa' }),
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// useDeal is the unit under behavioral test. We swap the implementation
// per test case via ``mockedUseDeal``.
const mockedUseDeal = vi.fn();
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: (id: string) => mockedUseDeal(id),
}));

vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({ documents: [] }),
}));

vi.mock('@/lib/hooks/useEngineOutputs', () => ({
  useEngineOutputs: () => ({ outputs: null }),
  getEngineField: () => null,
}));

// ``DealAssumptionsProvider`` runs ``assumptionsFromDeal`` which
// imports the live engine-outputs helpers. The page chrome never
// reads the assumption store on the loading/error path, so a no-op
// stub is enough to satisfy the import graph.
vi.mock('@/lib/assumptions/fromDeal', () => ({
  assumptionsFromDeal: () => ({}),
}));

// ``AssumptionsProvider`` just needs to render its children — no
// actual store wiring required for the gate tests.
vi.mock('@/stores/assumptionsStore', () => ({
  AssumptionsProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}));

vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>(
    '@/lib/api',
  );
  return {
    ...actual,
    isWorkerConnected: () => true,
    workerUrl: () => 'http://test-worker.local',
    api: {
      ...actual.api,
      scenarios: { list: vi.fn(async () => []) },
    },
  };
});

// next/dynamic returns a passthrough component so heavy tabs don't
// fail to resolve under jsdom. We never render past the gate in these
// tests, so the components themselves never mount.
vi.mock('next/dynamic', () => ({
  default: () => () => null,
}));

import ProjectDetailPage from '@/app/projects/[id]/page';
import { projects as mockProjects } from '@/lib/mockData';

describe('Bug #1 — UUID deal load gate (data-leak guard)', () => {
  beforeEach(() => {
    mockedUseDeal.mockReset();
  });

  it('renders a deal-id-scoped skeleton while the worker fetch is in flight (never a mock fallback)', () => {
    mockedUseDeal.mockReturnValue({
      deal: null,
      status: null,
      loading: true,
      error: null,
      fromMock: false,
      refresh: vi.fn(),
    });

    const { container } = render(<ProjectDetailPage />);

    // The skeleton is rendered and labelled with the requested id so
    // an analyst can confirm the URL.
    expect(screen.getByTestId('deal-load-skeleton')).toBeInTheDocument();
    expect(container.textContent).toContain(
      'fff00000-0000-0000-0000-000000000aaa',
    );

    // CRITICAL — no mock-data name leaks through. We sweep every
    // mock project's name AND city to catch the regression where the
    // page synthesized a project from ``mockProjects[0]``.
    for (const m of mockProjects) {
      expect(container.textContent ?? '').not.toContain(m.name);
      if (m.city) {
        expect(container.textContent ?? '').not.toContain(m.city);
      }
    }
    expect(container.textContent ?? '').not.toContain('Hilton');
    expect(container.textContent ?? '').not.toContain('Hilton Garden Inn');
  });

  it('renders an error card with a Retry button on fetch failure (no Hilton fallback)', () => {
    const refresh = vi.fn();
    mockedUseDeal.mockReturnValue({
      deal: null,
      status: null,
      loading: false,
      error: 'timeout',
      fromMock: false,
      refresh,
    });

    const { container } = render(<ProjectDetailPage />);

    const errorCard = screen.getByTestId('deal-load-error');
    expect(errorCard).toBeInTheDocument();
    // Surfaces the requested id + a Retry control.
    expect(container.textContent).toContain(
      'fff00000-0000-0000-0000-000000000aaa',
    );
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();

    // No mock-data name leaks through on the error path either.
    expect(container.textContent ?? '').not.toContain('Hilton');
    for (const m of mockProjects) {
      expect(container.textContent ?? '').not.toContain(m.name);
    }
  });

  it('renders the friendly TimeoutError copy when the underlying error is a worker timeout', () => {
    mockedUseDeal.mockReturnValue({
      deal: null,
      status: null,
      loading: false,
      error:
        'TimeoutError: The worker is busy — your upload is still extracting. Try again in 30 seconds.',
      fromMock: false,
      refresh: vi.fn(),
    });

    const { container } = render(<ProjectDetailPage />);

    expect(container.textContent).toContain(
      'The worker is busy — your upload is still extracting.',
    );
    expect(container.textContent ?? '').not.toContain('Hilton');
  });
});
