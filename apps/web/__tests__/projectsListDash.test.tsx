/**
 * Go-live Wave 0 — the deal list shows a dash, never a zero or a fixture.
 *
 *  A10  a wizard deal has `keys: null` until the OM fills it → "— keys",
 *       never "0 keys".
 *  A11  the worker persists `ai_confidence: 0.0` on create → the list renders
 *       the dash, never a "0%" confidence bar; a real score still renders.
 *  C4   with no worker URL the page renders the "Worker not configured"
 *       state — none of the four former sample deals, no skeleton, no hero.
 *
 * The page is rendered for real; only `useDeals`, the api client, auth and
 * the toast are stubbed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import type { WorkerDeal } from '@/lib/api';

const hoisted = vi.hoisted(() => ({
  connected: true,
  deals: [] as unknown[],
  loading: false,
  error: null as string | null,
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/projects',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/hooks/useDeals', () => ({
  useDeals: () => ({
    deals: hoisted.deals,
    loading: hoisted.loading,
    error: hoisted.error,
    workerConfigured: hoisted.connected,
    refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => hoisted.connected,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, pipeline: vi.fn(async () => ({ deals: [], summary: null })) },
    },
  };
});
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:member' }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/help/GetStartedHero', () => ({
  GetStartedHero: () => React.createElement('div', { 'data-testid': 'get-started-hero' }),
  useGetStartedHeroVisible: () => true,
}));

import ProjectsPage from '@/app/projects/page';

const FORMER_SAMPLE_DEALS = ['Hilton Garden Inn Downtown', 'Kimpton Angler', 'Marriott Magnificent Mile', 'Hyatt Regency Waterfront'];

function deal(over: Partial<WorkerDeal>): WorkerDeal {
  return {
    id: 'deal-uuid-1',
    tenant_id: 't',
    name: 'Project Harbor',
    city: 'Boston, MA',
    keys: null,
    service: null,
    brand: null,
    status: 'Draft',
    deal_stage: 'LOI',
    risk: null,
    ai_confidence: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-02T00:00:00Z',
    ...over,
  } as WorkerDeal;
}

beforeEach(() => {
  hoisted.connected = true;
  hoisted.deals = [];
  hoisted.loading = false;
  hoisted.error = null;
});
afterEach(cleanup);

describe('Projects list — no worker URL', () => {
  it('renders the one "Worker not configured" state and none of the former sample deals', () => {
    hoisted.connected = false;
    const { container } = render(<ProjectsPage />);
    expect(screen.getByTestId('worker-not-configured')).toHaveTextContent('Worker not configured');
    expect(screen.getByTestId('worker-not-configured')).toHaveTextContent(/deal list/);
    for (const name of FORMER_SAMPLE_DEALS) {
      expect(container.textContent ?? '').not.toContain(name);
    }
    expect(screen.queryByTestId('get-started-hero')).toBeNull();
    // The subtitle counts nothing, because there is nothing.
    expect(container.textContent).toContain('0 total projects');
  });
});

describe('Projects list — a wizard deal before extraction', () => {
  it('prints "— keys" for a null key count, never "0 keys"', () => {
    hoisted.deals = [deal({ keys: null, document_count: 1 })];
    const { container } = render(<ProjectsPage />);
    expect(container.textContent).toContain('— keys');
    expect(container.textContent).not.toMatch(/\b0 keys/);
  });

  it('a stored 0.0 confidence renders no "0%" bar (grid: hidden; it is "not scored", not a score)', () => {
    hoisted.deals = [deal({ ai_confidence: 0, document_count: 2 })];
    const { container } = render(<ProjectsPage />);
    expect(container.textContent).not.toMatch(/\b0%/);
    expect(screen.queryByText('AI Confidence')).toBeNull();
  });

  it('a null confidence renders no bar either, while a real score still renders', () => {
    hoisted.deals = [
      deal({ id: 'a', name: 'Unscored', ai_confidence: null, document_count: 2 }),
      deal({ id: 'b', name: 'Scored', ai_confidence: 0.87, keys: 132, document_count: 4 }),
    ];
    const { container } = render(<ProjectsPage />);
    expect(screen.getAllByText('AI Confidence')).toHaveLength(1);
    expect(container.textContent).toContain('87%');
    expect(container.textContent).toContain('132 keys');
    expect(container.textContent).not.toMatch(/\b0%/);
  });

  it('a worker that does not report a document count shows "— docs", not "0 docs", and no missing-docs warning', () => {
    hoisted.deals = [deal({ document_count: undefined })];
    const { container } = render(<ProjectsPage />);
    expect(container.textContent).toContain('— docs');
    expect(container.textContent).not.toContain('0 docs');
    expect(screen.queryByText('No documents uploaded')).toBeNull();
  });

  it('a reported zero document count still warns — zero is known, not unknown', () => {
    hoisted.deals = [deal({ document_count: 0 })];
    render(<ProjectsPage />);
    expect(screen.getByText('No documents uploaded')).toBeInTheDocument();
  });
});
