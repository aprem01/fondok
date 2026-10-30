/**
 * FON-79 (go-live plan ITEM A, PR A-3) — a draft cannot be mistaken for an
 * underwriting anywhere it is listed.
 *
 *  Projects list: a DRAFT badge instead of the status badge; no $/Key, IRR
 *  or AI Confidence cell (not even a dash — rule 1: a figure is sourced or
 *  absent, and a draft has no model to source from); the card opens the
 *  wizard; "Drafts" in the status filter; the subtitle counts drafts beside
 *  active; drafts sort first within newest-first; the kebab offers Resume /
 *  Discard draft and not Archive / Delete Forever; a 30-day "untouched" note.
 *
 *  Deal page: a draft redirects to the wizard and renders no tabs.
 *
 *  Audit vocabulary: the three draft actions read as words.
 *
 * The pages are rendered for real; only `useDeals` / `useDeal`, the api
 * client, auth and the toast are stubbed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import React from 'react';
import type { WorkerDeal } from '@/lib/api';

const hoisted = vi.hoisted(() => ({
  deals: [] as unknown[],
  dealPage: null as unknown,
  nav: { push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() },
  discardDraft: vi.fn(async () => undefined),
  archive: vi.fn(async () => undefined),
  refresh: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => hoisted.nav,
  usePathname: () => '/projects',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({ id: 'fff00000-0000-0000-0000-000000000bbb' }),
}));
vi.mock('@/lib/hooks/useDeals', () => ({
  useDeals: () => ({
    deals: hoisted.deals,
    loading: false,
    error: null,
    workerConfigured: true,
    refresh: hoisted.refresh,
  }),
}));
vi.mock('@/lib/hooks/useDeal', () => ({
  useDeal: () => ({
    deal: hoisted.dealPage,
    status: null,
    loading: false,
    error: null,
    workerConfigured: true,
    refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useDocuments', () => ({ useDocuments: () => ({ documents: [] }) }));
vi.mock('@/lib/hooks/useVariance', () => ({
  useVariance: () => ({
    flags: null, critical: 0, warn: 0, info: 0, note: null, loading: false, error: null,
    workerConfigured: true, refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/hooks/useEngineOutputs', () => ({
  useEngineOutputs: () => ({ outputs: null }),
  getEngineField: () => null,
}));
vi.mock('@/lib/assumptions/fromDeal', () => ({ assumptionsFromDeal: () => ({}) }));
vi.mock('@/stores/assumptionsStore', () => ({
  AssumptionsProvider: ({ children }: { children: React.ReactNode }) =>
    React.createElement(React.Fragment, null, children),
}));
vi.mock('next/dynamic', () => ({ default: () => () => null }));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    workerUrl: () => 'http://test-worker.local',
    api: {
      ...actual.api,
      deals: {
        ...actual.api.deals,
        pipeline: vi.fn(async () => ({ deals: [], summary: null })),
        discardDraft: hoisted.discardDraft,
        archive: hoisted.archive,
      },
      scenarios: { list: vi.fn(async () => []) },
    },
  };
});
vi.mock('@/lib/auth', () => ({ useCurrentRole: () => 'org:admin' }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/help/GetStartedHero', () => ({
  GetStartedHero: () => React.createElement('div', { 'data-testid': 'get-started-hero' }),
  useGetStartedHeroVisible: () => false,
}));

import ProjectsPage from '@/app/projects/page';
import ProjectDetailPage from '@/app/projects/[id]/page';
import { actionLabel } from '@/lib/auditLabels';
import { staleDraftNote, STALE_DRAFT_DAYS } from '@/lib/wizardDraft';

const DAY = 86_400_000;
const iso = (daysAgo: number) => new Date(Date.now() - daysAgo * DAY).toISOString();

function deal(over: Partial<WorkerDeal>): WorkerDeal {
  return {
    id: 'deal-uuid-1',
    tenant_id: 't',
    name: 'Project Harbor',
    city: 'Boston, MA',
    keys: 120,
    service: null,
    brand: null,
    status: 'In Review',
    deal_stage: 'LOI',
    risk: null,
    ai_confidence: 0.72,
    document_count: 3,
    lifecycle: 'active',
    created_at: iso(3),
    updated_at: iso(2),
    ...over,
  } as WorkerDeal;
}
const ACTIVE = deal({ id: 'active-1', name: 'Harbor Active', updated_at: iso(1) });
const DRAFT = deal({
  id: 'draft-1', name: 'Harbor Draft', lifecycle: 'draft', status: 'Draft', deal_stage: 'Teaser',
  keys: null, ai_confidence: null, document_count: 1, updated_at: iso(2),
});

beforeEach(() => {
  hoisted.deals = [];
  hoisted.dealPage = null;
  hoisted.nav.push.mockClear();
  hoisted.nav.replace.mockClear();
  hoisted.discardDraft.mockClear();
  hoisted.archive.mockClear();
  hoisted.refresh.mockClear();
  window.history.replaceState({}, '', '/projects');
});
afterEach(cleanup);

describe('Projects list — a draft card', () => {
  it('wears a DRAFT badge instead of the status badge and shows no $/Key, IRR or AI Confidence cell', () => {
    hoisted.deals = [DRAFT];
    const { container } = render(<ProjectsPage />);
    const card = screen.getByTestId('draft-card');
    expect(within(card).getByTestId('deal-draft-badge')).toHaveTextContent('Draft');
    expect(within(card).queryByText('In Review')).toBeNull();
    expect(within(card).queryByText('$/Key')).toBeNull();
    expect(within(card).queryByText('Levered IRR')).toBeNull();
    expect(within(card).queryByText('AI Confidence')).toBeNull();
    expect(within(card).getByTestId('draft-panel')).toHaveTextContent('Setup not finished');
    // Not the "No documents uploaded" nudge either — it has one.
    expect(within(card).queryByText('No documents uploaded')).toBeNull();
    // The deal stage the analyst picked still shows; the key count stays a dash.
    expect(within(card).getByText('Teaser')).toBeInTheDocument();
    expect(container.textContent).toContain('— keys');
  });

  it('opens in the wizard, not the deal page', () => {
    hoisted.deals = [DRAFT, ACTIVE];
    render(<ProjectsPage />);
    expect(screen.getByTestId('draft-card')).toHaveAttribute('href', '/projects/new?draft=draft-1');
    expect(screen.getByRole('link', { name: /Harbor Active/ })).toHaveAttribute('href', '/projects/active-1');
  });

  it('an active deal keeps its status badge and return cells', () => {
    hoisted.deals = [ACTIVE];
    render(<ProjectsPage />);
    expect(screen.queryByTestId('deal-draft-badge')).toBeNull();
    expect(screen.getByText('In Review')).toBeInTheDocument();
    expect(screen.getByText('$/Key')).toBeInTheDocument();
    expect(screen.getByText('AI Confidence')).toBeInTheDocument();
  });

  it('counts drafts beside active in the subtitle', () => {
    hoisted.deals = [ACTIVE, DRAFT, deal({ id: 'arch', name: 'Old', status: 'Archived' })];
    const { container } = render(<ProjectsPage />);
    expect(container.textContent).toContain('3 total projects · 1 active · 1 draft');
  });

  it('sorts drafts first within newest-first', () => {
    hoisted.deals = [ACTIVE, DRAFT, deal({ id: 'draft-2', name: 'Second Draft', lifecycle: 'draft', updated_at: iso(5) })];
    render(<ProjectsPage />);
    const names = screen.getAllByRole('link').map((a) => a.textContent ?? '').filter((t) => /Harbor|Second Draft/.test(t));
    expect(names[0]).toContain('Harbor Draft');
    expect(names[1]).toContain('Second Draft');
    expect(names[2]).toContain('Harbor Active');
  });

  it('notes a draft untouched for 30 days or more — with its own age, never a fixed number', () => {
    hoisted.deals = [
      deal({ id: 'stale', name: 'Stale Draft', lifecycle: 'draft', updated_at: iso(41) }),
      deal({ id: 'fresh', name: 'Fresh Draft', lifecycle: 'draft', updated_at: iso(5) }),
    ];
    render(<ProjectsPage />);
    const notes = screen.getAllByTestId('draft-stale-note');
    expect(notes).toHaveLength(1);
    expect(notes[0]).toHaveTextContent('Draft · untouched for 41 days');
    expect(staleDraftNote(iso(STALE_DRAFT_DAYS - 1))).toBeNull();
    expect(staleDraftNote(iso(STALE_DRAFT_DAYS))).toBe(`Draft · untouched for ${STALE_DRAFT_DAYS} days`);
    expect(staleDraftNote(null)).toBeNull();
    expect(staleDraftNote('not a date')).toBeNull();
  });
});

describe('Projects list — the Drafts filter', () => {
  it('offers Drafts beside All and narrows to drafts; a status filter excludes drafts', () => {
    hoisted.deals = [ACTIVE, DRAFT];
    render(<ProjectsPage />);
    fireEvent.click(screen.getByRole('button', { name: /^All/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Drafts' }));
    expect(screen.getByTestId('draft-card')).toBeInTheDocument();
    expect(screen.queryByText('Harbor Active')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: /^Drafts/ }));
    fireEvent.click(screen.getByRole('button', { name: 'In Review' }));
    expect(screen.queryByTestId('draft-card')).toBeNull();
    expect(screen.getByText('Harbor Active')).toBeInTheDocument();
  });

  it('`?filter=Drafts` opens the list already narrowed', async () => {
    window.history.replaceState({}, '', '/projects?filter=Drafts');
    hoisted.deals = [ACTIVE, DRAFT];
    render(<ProjectsPage />);
    await waitFor(() => expect(screen.queryByText('Harbor Active')).toBeNull());
    expect(screen.getByTestId('draft-card')).toBeInTheDocument();
  });
});

describe('Projects list — the draft kebab', () => {
  it('offers Resume and Discard draft, not Archive or Delete Forever; Discard calls the worker', async () => {
    hoisted.deals = [DRAFT, ACTIVE];
    render(<ProjectsPage />);
    // The kebab lives on the list view.
    const [, listToggle] = screen.getAllByRole('button').filter((b) => b.querySelector('svg.lucide-list') || b.querySelector('svg.lucide-layout-grid'));
    fireEvent.click(listToggle);
    const draftRow = screen.getByTestId('draft-row');
    expect(within(draftRow).queryByText('$/Key')).toBeNull();
    expect(draftRow).toHaveTextContent('Setup not finished — no model outputs');
    fireEvent.click(within(draftRow).getByRole('button', { name: 'More actions' }));
    expect(within(draftRow).getByRole('button', { name: 'Resume' })).toBeInTheDocument();
    expect(within(draftRow).getByRole('button', { name: 'Discard draft' })).toBeInTheDocument();
    expect(within(draftRow).queryByRole('button', { name: 'Archive' })).toBeNull();
    expect(within(draftRow).queryByRole('button', { name: /Delete Forever/ })).toBeNull();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(within(draftRow).getByRole('button', { name: 'Discard draft' }));
    await waitFor(() => expect(hoisted.discardDraft).toHaveBeenCalledWith('draft-1'));
    expect(hoisted.archive).not.toHaveBeenCalled();
    await waitFor(() => expect(hoisted.refresh).toHaveBeenCalled());
    confirm.mockRestore();
    // The active row keeps its own menu.
    const activeRow = screen.getByRole('link', { name: /Harbor Active/ });
    fireEvent.click(within(activeRow).getByRole('button', { name: 'More actions' }));
    expect(within(activeRow).getByRole('button', { name: 'Archive' })).toBeInTheDocument();
    expect(within(activeRow).queryByRole('button', { name: 'Discard draft' })).toBeNull();
  });
});

describe('Deal page — a draft', () => {
  it('redirects to the wizard and renders no deal chrome', async () => {
    hoisted.dealPage = deal({ id: 'fff00000-0000-0000-0000-000000000bbb', name: 'Harbor Draft', lifecycle: 'draft' });
    render(<ProjectDetailPage />);
    expect(screen.getByTestId('deal-is-draft')).toHaveTextContent('This deal is still a draft');
    expect(screen.getByRole('link', { name: 'Resume setup' })).toHaveAttribute('href', '/projects/new?draft=fff00000-0000-0000-0000-000000000bbb');
    expect(screen.queryByRole('tab')).toBeNull();
    expect(screen.queryByText('Data Room')).toBeNull();
    await waitFor(() => expect(hoisted.nav.replace).toHaveBeenCalledWith('/projects/new?draft=fff00000-0000-0000-0000-000000000bbb'));
  });

  it('an active deal does not redirect', () => {
    hoisted.dealPage = deal({ id: 'fff00000-0000-0000-0000-000000000bbb', name: 'Harbor Active' });
    render(<ProjectDetailPage />);
    expect(screen.queryByTestId('deal-is-draft')).toBeNull();
    expect(hoisted.nav.replace).not.toHaveBeenCalled();
  });
});

describe('Audit vocabulary — the draft actions read as words', () => {
  it('labels deal.draft_created / deal.activated / deal.draft_discarded', () => {
    expect(actionLabel('deal.draft_created')).toBe('Deal draft started');
    expect(actionLabel('deal.activated')).toBe('Deal setup finished');
    expect(actionLabel('deal.draft_discarded')).toBe('Deal draft discarded');
  });
});
