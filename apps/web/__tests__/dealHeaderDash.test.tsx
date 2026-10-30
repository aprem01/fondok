/**
 * Go-live Wave 0 — the deal header shows a dash, never a zero or a fixture.
 *
 *  A10  `keys: null` (the wizard sends none; the OM fills it later) → the
 *       meta line reads "— keys", never "0 keys".
 *  A11  `ai_confidence: 0.0` (persisted on create) → the pill reads
 *       "Awaiting docs" and the breakdown reads "—", never "0%".
 *  C4   with no worker URL the page renders the "Worker not configured"
 *       state — no sample deal, no skeleton.
 *
 * Same harness as `useDeal.test.tsx`: `useDeal` is stubbed per case, heavy
 * tabs are not mounted, and the page chrome renders for real.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';

const hoisted = vi.hoisted(() => ({ connected: true }));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'fff00000-0000-0000-0000-000000000aaa' }),
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/projects/fff00000-0000-0000-0000-000000000aaa',
}));

const mockedUseDeal = vi.fn();
vi.mock('@/lib/hooks/useDeal', () => ({ useDeal: (id: string) => mockedUseDeal(id) }));
vi.mock('@/lib/hooks/useDocuments', () => ({ useDocuments: () => ({ documents: [] }) }));
vi.mock('@/lib/hooks/useEngineOutputs', () => ({
  useEngineOutputs: () => ({ outputs: null, error: null, refresh: vi.fn() }),
  getEngineField: () => null,
}));
vi.mock('@/lib/hooks/useEngineRun', () => ({
  useEngineRun: () => ({ run: vi.fn(), status: 'idle' }),
}));
vi.mock('@/lib/assumptions/fromDeal', () => ({ assumptionsFromDeal: () => ({}) }));
vi.mock('@/stores/assumptionsStore', () => ({
  AssumptionsProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}));
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return {
    ...actual,
    isWorkerConnected: () => hoisted.connected,
    workerUrl: () => (hoisted.connected ? 'http://test-worker.local' : ''),
    api: { ...actual.api, scenarios: { list: vi.fn(async () => []) } },
  };
});
vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:member' }));

import ProjectDetailPage from '@/app/projects/[id]/page';

const DEAL = {
  id: 'fff00000-0000-0000-0000-000000000aaa',
  tenant_id: 't',
  name: 'Project Harbor',
  city: 'Boston, MA',
  keys: null,
  service: null,
  brand: null,
  status: 'Draft',
  deal_stage: 'LOI',
  risk: null,
  ai_confidence: 0,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-02T00:00:00Z',
};

function loaded(deal: Record<string, unknown>) {
  mockedUseDeal.mockReturnValue({
    deal,
    status: null,
    loading: false,
    error: null,
    workerConfigured: hoisted.connected,
    refresh: vi.fn(),
  });
}

beforeEach(() => {
  hoisted.connected = true;
  mockedUseDeal.mockReset();
});
afterEach(cleanup);

describe('Deal header — a wizard deal before extraction', () => {
  it('reads "— keys" for a null key count, never "0 keys"', () => {
    loaded(DEAL);
    const { container } = render(<ProjectDetailPage />);
    expect(container.textContent).toMatch(/—\s*keys/);
    expect(container.textContent).not.toMatch(/\b0\s*keys/);
  });

  it('a persisted 0.0 confidence reads "Awaiting docs" and the breakdown "—", never "0%"', () => {
    loaded(DEAL);
    const { container } = render(<ProjectDetailPage />);
    expect(screen.getByRole('button', { name: 'Awaiting documents' })).toHaveTextContent('Awaiting docs');
    expect(container.textContent).not.toMatch(/\b0%/);
  });

  it('a scored deal still prints its confidence', () => {
    loaded({ ...DEAL, ai_confidence: 0.87, keys: 132 });
    const { container } = render(<ProjectDetailPage />);
    expect(screen.getByRole('button', { name: 'AI confidence 87%' })).toBeInTheDocument();
    expect(container.textContent).toMatch(/132\s*keys/);
  });
});

describe('Deal page — no worker URL', () => {
  it('renders the one "Worker not configured" state, never a sample deal or a skeleton', () => {
    hoisted.connected = false;
    mockedUseDeal.mockReturnValue({
      deal: null,
      status: null,
      loading: false,
      error: null,
      workerConfigured: false,
      refresh: vi.fn(),
    });
    const { container } = render(<ProjectDetailPage />);
    expect(screen.getByTestId('worker-not-configured')).toHaveTextContent('Worker not configured');
    expect(screen.queryByTestId('deal-load-skeleton')).toBeNull();
    expect(screen.queryByTestId('deal-load-error')).toBeNull();
    for (const name of ['Hilton Garden Inn', 'Kimpton Angler', 'Marriott Magnificent Mile', 'Hyatt Regency Waterfront']) {
      expect(container.textContent ?? '').not.toContain(name);
    }
  });
});
