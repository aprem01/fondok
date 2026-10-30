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
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import React from 'react';

const hoisted = vi.hoisted(() => ({
  connected: true,
  tab: '',
  outputs: null as unknown,
  varianceFlags: null as unknown[] | null,
  /** Every id the page handed to useVariance, in render order. */
  varianceIds: [] as string[],
}));

vi.mock('next/navigation', () => ({
  useParams: () => ({ id: 'fff00000-0000-0000-0000-000000000aaa' }),
  useSearchParams: () => new URLSearchParams(hoisted.tab ? `tab=${hoisted.tab}` : ''),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => '/projects/fff00000-0000-0000-0000-000000000aaa',
}));
vi.mock('@/lib/hooks/useVariance', () => ({
  // Mirrors the real hook's guard: an empty id fetches nothing and answers
  // the empty state; a real id answers the fixture.
  useVariance: (id: string) => {
    hoisted.varianceIds.push(id);
    return {
      flags: id ? hoisted.varianceFlags : null,
      critical: 0, warn: 0, info: 0, note: null, loading: false, error: null,
      workerConfigured: hoisted.connected, refresh: vi.fn(),
    };
  },
}));

const mockedUseDeal = vi.fn();
vi.mock('@/lib/hooks/useDeal', () => ({ useDeal: (id: string) => mockedUseDeal(id) }));
vi.mock('@/lib/hooks/useDocuments', () => ({ useDocuments: () => ({ documents: [] }) }));
vi.mock('@/lib/hooks/useEngineOutputs', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/hooks/useEngineOutputs')>();
  return {
    ...actual,
    useEngineOutputs: () => ({
      outputs: hoisted.outputs, previous: null, loading: false, settled: true, error: null,
      lastRunAt: null, refresh: vi.fn(async () => {}),
    }),
  };
});
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
  ai_confidence: null,
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
  hoisted.tab = '';
  hoisted.outputs = null;
  hoisted.varianceFlags = null;
  hoisted.varianceIds = [];
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

  it('an unscored (null) confidence reads "Awaiting docs" and the breakdown "—", never "0%"', () => {
    loaded(DEAL);
    const { container } = render(<ProjectDetailPage />);
    expect(screen.getByRole('button', { name: 'Awaiting documents' })).toHaveTextContent('Awaiting docs');
    expect(container.textContent).not.toMatch(/\b0%/);
  });

  it('a scored deal still prints its confidence — and a measured 0 is a value, printed as 0%', () => {
    loaded({ ...DEAL, ai_confidence: 0.87, keys: 132 });
    const { container } = render(<ProjectDetailPage />);
    expect(screen.getByRole('button', { name: 'AI confidence 87%' })).toBeInTheDocument();
    expect(container.textContent).toMatch(/132\s*keys/);
    cleanup();
    loaded({ ...DEAL, ai_confidence: 0 });
    render(<ProjectDetailPage />);
    expect(screen.getByRole('button', { name: 'AI confidence 0%' })).toBeInTheDocument();
  });

  it('never asks the worker for the variance report on mount, while the deal loads, or on the load-error path', () => {
    mockedUseDeal.mockReturnValue({ deal: null, status: null, loading: true, error: null, workerConfigured: true, refresh: vi.fn() });
    render(<ProjectDetailPage />);
    expect(hoisted.varianceIds.length).toBeGreaterThan(0);
    expect(hoisted.varianceIds.every((id) => id === '')).toBe(true);
    cleanup();
    hoisted.varianceIds = [];
    mockedUseDeal.mockReturnValue({ deal: null, status: null, loading: false, error: 'timeout', workerConfigured: true, refresh: vi.fn() });
    render(<ProjectDetailPage />);
    expect(hoisted.varianceIds.every((id) => id === '')).toBe(true);
    cleanup();
    hoisted.varianceIds = [];
    // Loaded, tooltip closed: still nothing — the count is only shown inside the tooltip.
    loaded(DEAL);
    render(<ProjectDetailPage />);
    expect(hoisted.varianceIds.every((id) => id === '')).toBe(true);
  });

  it('asks for the variance report only once the confidence tooltip opens, and stops when it closes', () => {
    loaded(DEAL);
    render(<ProjectDetailPage />);
    const pill = screen.getByRole('button', { name: 'Awaiting documents' });
    fireEvent.focus(pill);
    expect(hoisted.varianceIds[hoisted.varianceIds.length - 1]).toBe(DEAL.id);
    fireEvent.blur(pill);
    expect(hoisted.varianceIds[hoisted.varianceIds.length - 1]).toBe('');
  });

  it('the confidence breakdown reads the variance count from the worker — "—" until it answers', () => {
    loaded(DEAL);
    render(<ProjectDetailPage />);
    // The breakdown is the pill's hover/focus tooltip.
    fireEvent.focus(screen.getByRole('button', { name: 'Awaiting documents' }));
    const tip = screen.getByRole('tooltip');
    expect(tip.textContent).toMatch(/Variance flags—/);
    expect(tip.textContent).not.toMatch(/Variance flags0/);
    cleanup();
    hoisted.varianceFlags = [{}, {}, {}];
    loaded(DEAL);
    render(<ProjectDetailPage />);
    fireEvent.focus(screen.getByRole('button', { name: 'Awaiting documents' }));
    expect(screen.getByRole('tooltip').textContent).toMatch(/Variance flags3/);
  });

  it('a deal row with no status and no stage shows no badge — never an invented "Draft" / "Teaser"', () => {
    loaded({ ...DEAL, status: null, deal_stage: null });
    const { container } = render(<ProjectDetailPage />);
    expect(container.textContent).not.toMatch(/\bDraft\b|\bTeaser\b/);
    expect(screen.queryByText('LOI')).toBeNull();
  });

  it('the page-level failure banner leaves out the engines the active tab reports itself', () => {
    loaded(DEAL);
    hoisted.outputs = {
      deal_id: DEAL.id,
      engines: {},
      failures: [
        { engine: 'returns', reason: 'missing_input', message: 'Returns: no purchase price.' },
        { engine: 'debt', reason: 'validation_error', message: 'Debt: negative NOI.' },
      ],
    };
    hoisted.tab = 'overview';
    render(<ProjectDetailPage />);
    // Overview's Returns section reports `returns` inline; the page-level
    // banner reports `debt`. Each failure appears exactly once on screen.
    expect(screen.getAllByTestId('engine-failure-returns')).toHaveLength(1);
    expect(screen.getAllByTestId('engine-failure-debt')).toHaveLength(1);
    const section = screen.getByTestId('overview-returns');
    expect(within(section).getByTestId('engine-failure-returns')).toBeInTheDocument();
    expect(within(section).queryByTestId('engine-failure-debt')).toBeNull();
    const pageBanner = screen
      .getAllByTestId('engine-failures-banner')
      .find((b) => !section.contains(b)) as HTMLElement;
    expect(within(pageBanner).getByTestId('engine-failure-debt')).toBeInTheDocument();
    expect(within(pageBanner).queryByTestId('engine-failure-returns')).toBeNull();
    cleanup();
    // A tab that owns no engine gets everything in the one page-level banner.
    hoisted.tab = 'debt';
    loaded(DEAL);
    render(<ProjectDetailPage />);
    expect(screen.getAllByTestId('engine-failures-banner')).toHaveLength(1);
    expect(screen.getByTestId('engine-failure-debt')).toBeInTheDocument();
    expect(screen.getByTestId('engine-failure-returns')).toBeInTheDocument();
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
