/**
 * FON-80 — the Projects list carries decision-useful deal metrics.
 *
 *  Purchase Price and Equity Multiple join $/Key, IRR and NOI, all from the
 *  pipeline payload's latest Base Case run — "—" when absent, never $0 or
 *  0.00x. "AI Confidence" became Model/Data Readiness: a processing step
 *  read from the worker's facts (documents → extraction → model run →
 *  validated), not a score. The Owner cell names the deal's creator from the
 *  workspace roster, and an owner filter appears once there is a creator to
 *  filter on. Search matches the extracted property name beside the project
 *  name and city. A draft keeps no model cells at all.
 *
 * The page is rendered for real; `useDeals`, the api client's pipeline
 * call, auth (role + roster) and the toast are stubbed.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, within, waitFor } from '@testing-library/react';
import React from 'react';
import type { WorkerDeal, PipelineDealRow } from '@/lib/api';
import type { OrgMember } from '@/lib/auth';

const hoisted = vi.hoisted(() => ({
  deals: [] as unknown[],
  rows: [] as unknown[],
  members: [] as unknown[],
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
      deals: { ...actual.api.deals, pipeline: vi.fn(async () => ({ deals: hoisted.rows, summary: null })) },
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
  GetStartedHero: () => React.createElement('div', { 'data-testid': 'get-started-hero' }),
  useGetStartedHeroVisible: () => false,
}));

import ProjectsPage from '@/app/projects/page';
import { modelDataReadiness } from '@/lib/project';

function deal(over: Partial<WorkerDeal>): WorkerDeal {
  return {
    id: 'deal-1',
    tenant_id: 't',
    name: 'Project Harbor',
    city: 'Boston, MA',
    keys: 120,
    service: null,
    brand: null,
    status: 'In Progress',
    deal_stage: 'LOI',
    risk: null,
    ai_confidence: 0.87,
    document_count: 3,
    lifecycle: 'active',
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-02T00:00:00Z',
    ...over,
  } as WorkerDeal;
}

function row(over: Partial<PipelineDealRow>): PipelineDealRow {
  return {
    deal_id: 'deal-1',
    name: 'Project Harbor',
    state: 'VALIDATING',
    status: 'In Progress',
    city: 'Boston, MA',
    brand: null,
    deal_stage: 'LOI',
    keys: 120,
    purchase_price: 30_000_000,
    price_per_key: 250_000,
    noi_y1: 4_500_000,
    noi_stabilized: null,
    exit_cap_rate: null,
    levered_irr: 0.157,
    equity_multiple: 2.13,
    dscr_y1: null,
    document_count: 3,
    last_engine_run_at: '2026-10-02T00:00:00Z',
    last_activity_at: '2026-10-02T00:00:00Z',
    pip_total_usd: null,
    target_irr: null,
    target_irr_met: null,
    ...over,
  };
}

const ANA: OrgMember = {
  id: 'orgmem_1', userId: 'user_ana', name: 'Ana Ruiz', email: 'ana@example.com',
  initials: 'AR', role: 'org:member', pending: false,
};

/** The grid/list toggle — the list view is where the columns live. */
function switchToList() {
  const [, listToggle] = screen
    .getAllByRole('button')
    .filter((b) => b.querySelector('svg.lucide-list') || b.querySelector('svg.lucide-layout-grid'));
  fireEvent.click(listToggle);
}

beforeEach(() => {
  hoisted.deals = [];
  hoisted.rows = [];
  hoisted.members = [];
});
afterEach(cleanup);

describe('Projects list — Purchase Price and Equity Multiple', () => {
  it('prints both from the pipeline row, on the card and in the list', async () => {
    hoisted.deals = [deal({})];
    hoisted.rows = [row({})];
    render(<ProjectsPage />);
    // The pipeline payload lands after mount.
    expect(await screen.findByText('$30.0M')).toBeInTheDocument();
    expect(screen.getByText('Purchase Price')).toBeInTheDocument();
    expect(screen.getByText('Equity Multiple')).toBeInTheDocument();
    expect(screen.getByText('2.13x')).toBeInTheDocument();
    expect(screen.getByText('$250,000')).toBeInTheDocument();
    expect(screen.getByText('15.7%')).toBeInTheDocument();
    switchToList();
    const line = screen.getByRole('link', { name: /Project Harbor/ });
    expect(line).toHaveTextContent('$30.0M');
    expect(line).toHaveTextContent('2.13x');
    expect(line).toHaveTextContent('$4.50M');
  });

  it('reads "—" for a missing or zero price / multiple — never $0 or 0.00x', async () => {
    hoisted.deals = [deal({})];
    hoisted.rows = [row({ purchase_price: 0, equity_multiple: null })];
    const { container } = render(<ProjectsPage />);
    expect(await screen.findByText('$250,000')).toBeInTheDocument();
    const price = screen.getByText('Purchase Price').nextElementSibling;
    const multiple = screen.getByText('Equity Multiple').nextElementSibling;
    expect(price).toHaveTextContent('—');
    expect(price).toHaveAttribute('title', 'Available after the model runs');
    expect(multiple).toHaveTextContent('—');
    expect(container.textContent).not.toMatch(/\$0\b/);
    expect(container.textContent).not.toContain('0.00x');
  });

  it('a deal with no pipeline row dashes every model cell', () => {
    hoisted.deals = [deal({})];
    const { container } = render(<ProjectsPage />);
    for (const label of ['Purchase Price', '$/Key', 'Levered IRR', 'Equity Multiple']) {
      expect(screen.getByText(label).nextElementSibling).toHaveTextContent('—');
    }
    expect(container.textContent).not.toMatch(/\$0\b|0\.0%|0\.00x/);
  });

  it('a draft keeps no model cells — not even dashes — on the card or in the list', async () => {
    const draft = deal({ id: 'draft-1', name: 'Harbor Draft', lifecycle: 'draft', status: 'Draft', ai_confidence: null, keys: null });
    hoisted.deals = [draft, deal({})];
    hoisted.rows = [row({}), row({ deal_id: 'draft-1', name: 'Harbor Draft' })];
    render(<ProjectsPage />);
    expect(await screen.findByText('$30.0M')).toBeInTheDocument();
    const card = screen.getByTestId('draft-card');
    for (const label of ['Purchase Price', '$/Key', 'Levered IRR', 'Equity Multiple', 'Model/Data Readiness']) {
      expect(within(card).queryByText(label)).toBeNull();
    }
    // No dashed metric cell either ("— keys" is the key count, which is fine).
    expect(within(card).queryAllByTitle('Available after the model runs')).toHaveLength(0);
    expect(within(card).queryByTestId('readiness')).toBeNull();
    expect(within(card).getByTestId('draft-panel')).toHaveTextContent('Setup not finished');
    switchToList();
    const draftRow = screen.getByTestId('draft-row');
    expect(draftRow).toHaveTextContent('Setup not finished — no model outputs');
    expect(draftRow.textContent).not.toContain('$30.0M');
    expect(draftRow.textContent).not.toContain('2.13x');
    expect(within(draftRow).queryByTestId('readiness')).toBeNull();
  });
});

describe('Model/Data Readiness — a step from the worker’s facts, never a score', () => {
  it('maps each fact to its step, and nothing known to null', () => {
    const base = { documentCount: undefined, aiConfidence: null, lastEngineRunAt: null, state: null };
    expect(modelDataReadiness(base)).toBeNull();
    expect(modelDataReadiness({ ...base, documentCount: 0 })).toMatchObject({ step: 'no_documents', index: 0, label: 'No documents' });
    expect(modelDataReadiness({ ...base, documentCount: 2 })).toMatchObject({ step: 'uploaded', index: 1, label: 'Documents uploaded', extractionConfidence: null });
    expect(modelDataReadiness({ ...base, documentCount: 2, aiConfidence: 0.5 })).toMatchObject({ step: 'extracted', index: 2, label: 'Extracted', extractionConfidence: 50 });
    expect(modelDataReadiness({ ...base, documentCount: 2, aiConfidence: 0.5, lastEngineRunAt: '2026-10-02T00:00:00Z' })).toMatchObject({ step: 'model_run', index: 3, label: 'Model run', extractionConfidence: 50 });
    expect(modelDataReadiness({ ...base, documentCount: 2, aiConfidence: 0.5, lastEngineRunAt: '2026-10-02T00:00:00Z', state: 'READY' })).toMatchObject({ step: 'validated', index: 4, label: 'Validated' });
    // A measured 0 is a value; an unscored deal never becomes "0%".
    expect(modelDataReadiness({ ...base, documentCount: 1, aiConfidence: 0 })).toMatchObject({ step: 'extracted', extractionConfidence: 0 });
    expect(modelDataReadiness({ ...base, documentCount: 1, aiConfidence: null })?.extractionConfidence).toBeNull();
  });

  it('renders the step with the extraction confidence beside it; "Model run" once the pipeline row carries a run', async () => {
    hoisted.deals = [
      deal({ id: 'a', name: 'Ran', ai_confidence: 0.87 }),
      deal({ id: 'b', name: 'Empty', ai_confidence: null, document_count: 0 }),
      deal({ id: 'c', name: 'Older worker', ai_confidence: null, document_count: undefined }),
    ];
    hoisted.rows = [row({ deal_id: 'a', name: 'Ran' })];
    const { container } = render(<ProjectsPage />);
    await waitFor(() => expect(screen.getAllByTestId('readiness-label')[0]).toHaveTextContent('Model run'));
    // The zero-document card keeps its "No documents uploaded" nudge in
    // place of the metrics, so the grid carries one readiness row.
    expect(screen.getAllByTestId('readiness-label').map((el) => el.textContent)).toEqual(['Model run · extraction 87%']);
    expect(screen.getByText('No documents uploaded')).toBeInTheDocument();
    // The older-worker row (no document count, no run) says "—", not a step.
    const olderCard = screen.getByRole('link', { name: /Older worker/ });
    expect(within(olderCard).getByTestId('readiness')).toHaveTextContent('—');
    expect(container.textContent).not.toContain('AI Confidence');
    switchToList();
    expect(screen.getByText('Readiness')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /^.*Ran/ })).toHaveTextContent('Model run · extraction 87%');
    expect(screen.getByRole('link', { name: /Empty/ })).toHaveTextContent('No documents');
    expect(within(screen.getByRole('link', { name: /Older worker/ })).getByTestId('readiness')).toHaveTextContent('—');
  });
});

describe('Projects list — Owner', () => {
  it('names the creator from the workspace roster; "—" when unrecorded or not on the roster', () => {
    hoisted.members = [ANA];
    hoisted.deals = [
      deal({ id: 'a', name: 'Harbor Ana', created_by: 'user_ana' }),
      deal({ id: 'b', name: 'Harbor Nobody', created_by: null }),
      deal({ id: 'c', name: 'Harbor Gone', created_by: 'user_left' }),
    ];
    render(<ProjectsPage />);
    const owners = screen.getAllByTestId('deal-owner');
    expect(owners.map((o) => o.textContent)).toEqual(['AR', '—', '—']);
    expect(owners.map((o) => o.getAttribute('title'))).toEqual([
      'Owner: Ana Ruiz', 'Owner: not recorded', 'Owner: not in this workspace’s roster',
    ]);
  });

  it('offers an owner filter once a creator can be named, with "Unknown" for the rest', () => {
    hoisted.members = [ANA];
    hoisted.deals = [
      deal({ id: 'a', name: 'Harbor Ana', created_by: 'user_ana' }),
      deal({ id: 'b', name: 'Harbor Nobody', created_by: null }),
      deal({ id: 'c', name: 'Harbor Gone', created_by: 'user_left' }),
    ];
    render(<ProjectsPage />);
    fireEvent.click(screen.getByTestId('owner-filter'));
    const menu = screen.getByTestId('owner-filter-menu');
    expect(within(menu).getAllByRole('button').map((b) => b.textContent)).toEqual(['Any owner', 'Ana Ruiz', 'Unknown']);
    fireEvent.click(within(menu).getByRole('button', { name: 'Ana Ruiz' }));
    expect(screen.getByText('Harbor Ana')).toBeInTheDocument();
    expect(screen.queryByText('Harbor Nobody')).toBeNull();
    expect(screen.queryByText('Harbor Gone')).toBeNull();
    expect(screen.getByTestId('owner-filter')).toHaveTextContent('Owner: Ana Ruiz');
    fireEvent.click(screen.getByTestId('owner-filter'));
    fireEvent.click(within(screen.getByTestId('owner-filter-menu')).getByRole('button', { name: 'Unknown' }));
    expect(screen.queryByText('Harbor Ana')).toBeNull();
    expect(screen.getByText('Harbor Nobody')).toBeInTheDocument();
    expect(screen.getByText('Harbor Gone')).toBeInTheDocument();
    // The status filter still reads on its own ("All"), unchanged.
    expect(screen.getByRole('button', { name: /^All/ })).toBeInTheDocument();
  });

  it('shows no owner filter when no deal records a creator the roster can name', () => {
    hoisted.members = [];
    hoisted.deals = [deal({ id: 'a', created_by: 'user_ana' }), deal({ id: 'b', name: 'Two', created_by: null })];
    render(<ProjectsPage />);
    expect(screen.queryByTestId('owner-filter')).toBeNull();
    expect(screen.getAllByTestId('deal-owner').map((o) => o.textContent)).toEqual(['—', '—']);
  });
});

describe('Projects list — search', () => {
  it('matches the extracted property name beside the project name and city; the property name is never a fallback', () => {
    hoisted.deals = [
      deal({ id: 'a', name: 'Project Harbor', property_name: 'The Liberty Hotel', city: 'Boston, MA' }),
      deal({ id: 'b', name: 'Project Two', property_name: null, city: 'Austin, TX' }),
    ];
    const { container } = render(<ProjectsPage />);
    const input = screen.getByPlaceholderText(/Search by project, property or city/);
    fireEvent.change(input, { target: { value: 'liberty' } });
    expect(screen.getByText('Project Harbor')).toBeInTheDocument();
    expect(screen.queryByText('Project Two')).toBeNull();
    fireEvent.change(input, { target: { value: 'austin' } });
    expect(screen.queryByText('Project Harbor')).toBeNull();
    expect(screen.getByText('Project Two')).toBeInTheDocument();
    fireEvent.change(input, { target: { value: 'project' } });
    expect(screen.getByText('Project Harbor')).toBeInTheDocument();
    expect(screen.getByText('Project Two')).toBeInTheDocument();
    // The card still leads with the confidential project name.
    expect(container.textContent).not.toContain('The Liberty Hotel');
    fireEvent.change(input, { target: { value: 'nothing-matches' } });
    expect(screen.getByText('No deals match your filters')).toBeInTheDocument();
  });
});
