/**
 * FON-80 — the gaps Sam found on the Projects screen (QA 2026-11-16).
 *
 *  Owner    the avatar names the creator when `created_by` is in the roster;
 *           a creator the worker never recorded (the pre-Clerk "system" rows,
 *           sent as null) reads "—" and says why — never a guessed person.
 *  Stage    the list's STAGE cell binds to `deal_stage` — a set stage renders
 *           its badge; an unset one reads "—" with a tooltip saying how to set it.
 *  Updated  the later of the row's `updated_at` and the pipeline row's
 *           `last_engine_run_at` (b67bfdee: row 11/4, re-run 11/16 → 11/16).
 *  Layout   the list card scrolls sideways below its rows' minimum width, so
 *           the PROJECT header can never slide under STAGE (gap 5).
 *
 * Same harness as projectsListDash.test.tsx; the roster and the pipeline
 * payload are the two extra stubs.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within } from '@testing-library/react';
import React from 'react';
import type { PipelineDealRow, WorkerDeal } from '@/lib/api';

const hoisted = vi.hoisted(() => ({
  deals: [] as unknown[],
  members: [] as { id: string; userId: string; name: string; email: string; initials: string; role: string; pending: boolean }[],
  pipelineRows: [] as unknown[],
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/projects',
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock('@/lib/hooks/useDeals', () => ({
  useDeals: () => ({
    deals: hoisted.deals,
    loading: false,
    error: null,
    workerConfigured: true,
    refresh: vi.fn(),
  }),
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      ...actual.api,
      deals: {
        ...actual.api.deals,
        pipeline: vi.fn(async () => ({ deals: hoisted.pipelineRows, summary: null })),
      },
    },
  };
});
vi.mock('@/lib/auth', () => ({
  useCurrentRole: () => 'org:member',
  useOrgMembers: () => ({ members: hoisted.members, loading: false, demo: false }),
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/help/IntroCard', () => ({ IntroCard: () => null }));
vi.mock('@/components/help/GetStartedHero', () => ({
  GetStartedHero: () => null,
  useGetStartedHeroVisible: () => false,
}));

import ProjectsPage from '@/app/projects/page';

const SAM = 'user_3CzqIioopfRt1cluTKDfOFrYwHl';

function deal(over: Partial<WorkerDeal>): WorkerDeal {
  return {
    id: 'deal-uuid-1',
    tenant_id: 't',
    name: 'Project Harbor',
    city: 'Boston, MA',
    keys: 120,
    service: null,
    brand: null,
    status: 'In Progress',
    deal_stage: null,
    risk: null,
    ai_confidence: null,
    document_count: 2,
    created_by: null,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-11-04T18:32:51Z',
    ...over,
  } as WorkerDeal;
}

function pipelineRow(over: Partial<PipelineDealRow>): PipelineDealRow {
  return {
    deal_id: 'deal-uuid-1',
    name: 'Project Harbor',
    state: 'VALIDATING',
    status: 'In Progress',
    city: 'Boston, MA',
    brand: null,
    deal_stage: null,
    keys: 120,
    purchase_price: 30_000_000,
    price_per_key: 250_000,
    noi_y1: 4_000_000,
    noi_stabilized: 5_000_000,
    exit_cap_rate: 0.075,
    levered_irr: 0.157,
    equity_multiple: 2.1,
    dscr_y1: 1.4,
    document_count: 2,
    last_engine_run_at: null,
    last_activity_at: '2026-11-04T18:32:51Z',
    pip_total_usd: null,
    target_irr: null,
    target_irr_met: null,
    ...over,
  } as PipelineDealRow;
}

const toList = () => fireEvent.click(screen.getByRole('button', { name: 'List view' }));

beforeEach(() => {
  hoisted.deals = [];
  hoisted.members = [];
  hoisted.pipelineRows = [];
});
afterEach(cleanup);

describe('Owner (gap 1)', () => {
  it('names the creator when created_by is a roster member', () => {
    hoisted.members = [{ id: 'm1', userId: SAM, name: 'Sam Cicconi', email: 's@x.com', initials: 'SC', role: 'org:admin', pending: false }];
    hoisted.deals = [deal({ created_by: SAM })];
    render(<ProjectsPage />);
    const avatar = screen.getByTestId('deal-owner');
    expect(avatar).toHaveTextContent('SC');
    expect(avatar).toHaveAttribute('title', 'Owner: Sam Cicconi');
  });

  it('reads "—" and says the creator was never recorded when the worker sends none (the pre-Clerk "system" rows)', () => {
    hoisted.members = [{ id: 'm1', userId: SAM, name: 'Sam Cicconi', email: 's@x.com', initials: 'SC', role: 'org:admin', pending: false }];
    hoisted.deals = [deal({ created_by: null })];
    render(<ProjectsPage />);
    const avatar = screen.getByTestId('deal-owner');
    expect(avatar).toHaveTextContent('—');
    expect(avatar.getAttribute('title')).toMatch(/not recorded/);
    expect(avatar.getAttribute('title')).toMatch(/before Fondok recorded/);
    expect(avatar).not.toHaveTextContent('SC');
  });

  it('reads "—" (roster wording) for a creator the workspace roster cannot name', () => {
    hoisted.members = [];
    hoisted.deals = [deal({ created_by: 'user_left_the_workspace' })];
    render(<ProjectsPage />);
    const avatar = screen.getByTestId('deal-owner');
    expect(avatar).toHaveTextContent('—');
    expect(avatar.getAttribute('title')).toMatch(/roster/);
  });
});

describe('Stage (gap 2)', () => {
  it('binds the list STAGE cell to deal_stage — a set stage renders its badge', () => {
    hoisted.deals = [deal({ deal_stage: 'LOI' })];
    render(<ProjectsPage />);
    toList();
    expect(within(screen.getByTestId('deal-stage')).getByText('LOI')).toBeInTheDocument();
  });

  it('an unset stage reads "—" and the cell says how to set it — never an invented "Teaser"', () => {
    hoisted.deals = [deal({ deal_stage: null })];
    const { container } = render(<ProjectsPage />);
    toList();
    const cell = screen.getByTestId('deal-stage');
    expect(cell).toHaveTextContent('—');
    expect(cell.querySelector('[title]')?.getAttribute('title')).toMatch(/set it from the deal header/);
    expect(container.textContent).not.toMatch(/\bTeaser\b/);
  });
});

describe('Updated (gap 4)', () => {
  it('shows the later of the row updated_at and the latest engine run (b67bfdee: 11/4 row, 11/16 re-run)', async () => {
    hoisted.deals = [deal({ updated_at: '2026-11-04T18:32:51Z' })];
    hoisted.pipelineRows = [pipelineRow({ last_engine_run_at: '2026-11-16T19:59:27Z' })];
    render(<ProjectsPage />);
    toList();
    expect(await screen.findByText('11/16/2026')).toBeInTheDocument();
    expect(screen.queryByText('11/4/2026')).toBeNull();
  });

  it('keeps the row updated_at when it is newer than the last run (assumptions changed after the run)', async () => {
    hoisted.deals = [deal({ updated_at: '2026-11-16T10:00:00Z' })];
    hoisted.pipelineRows = [pipelineRow({ last_engine_run_at: '2026-11-10T09:00:00Z', purchase_price: 31_000_000 })];
    render(<ProjectsPage />);
    toList();
    // The pipeline row has landed once its price renders.
    expect(await screen.findByText('$31.0M')).toBeInTheDocument();
    expect(screen.getByText('11/16/2026')).toBeInTheDocument();
  });

  it('falls back to the row updated_at before the pipeline answers, never "—" for a dated row', () => {
    hoisted.deals = [deal({ updated_at: '2026-11-04T18:32:51Z' })];
    render(<ProjectsPage />);
    toList();
    expect(screen.getByText('11/4/2026')).toBeInTheDocument();
  });
});

describe('Layout (gap 5)', () => {
  it('the list card scrolls sideways and every row carries the same minimum width, so PROJECT cannot slide under STAGE', () => {
    hoisted.deals = [deal({ id: 'a', name: 'A' }), deal({ id: 'b', name: 'B' })];
    render(<ProjectsPage />);
    toList();
    const card = screen.getByTestId('projects-list');
    expect(card.className).toMatch(/overflow-x-auto/);
    const rows = Array.from(card.children) as HTMLElement[];
    expect(rows.length).toBe(3); // header + 2 rows
    const minWidths = new Set(rows.map((r) => r.className.match(/min-w-\[\d+px\]/)?.[0]));
    expect(minWidths.size).toBe(1);
    expect([...minWidths][0]).toBeDefined();
    // The Project cell keeps a real minimum, not min-w-0, in the header and the rows.
    expect(screen.getByText('Project').className).toMatch(/min-w-\[\d+px\]/);
  });
});
