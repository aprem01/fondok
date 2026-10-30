/**
 * Sam #6 — Market / Submarket in the New Deal wizard.
 *
 *   - City, Market and Submarket are three separate, clearly labelled fields.
 *   - STR's market list NOT loaded (the state until STR's file arrives):
 *     Market suggests the name-only STR market names, labelled as having no
 *     STR id; Submarket suggests nothing; a typed market is sent with
 *     `str_market_id: null`.
 *   - STR's list loaded: Market suggests STR's markets, a pick resolves to
 *     STR's spelling + id, Submarket suggests only that market's submarkets,
 *     and the id is sent on create.
 *
 * Every STR id / name here is a synthetic TEST value — none is real.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
}));
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

type Row = {
  str_market_id: string; market_name: string; submarket_id: string | null;
  submarket_name: string | null; state: string | null; country: string | null;
};
const ref = vi.hoisted(() => ({ rows: [] as Row[] }));
const worker = vi.hoisted(() => ({
  create: vi.fn(async (body: { name: string }) => ({ id: 'deal-new-1', name: body.name })),
  upload: vi.fn(async () => []),
  list: vi.fn(async () => []),
  strMarkets: vi.fn(async (params: { q?: string; str_market_id?: string; limit?: number } = {}) => {
    const q = (params.q ?? '').trim().toLowerCase();
    return ref.rows
      .filter((r) => !params.str_market_id || r.str_market_id === params.str_market_id)
      .filter((r) => !q || `${r.market_name} ${r.submarket_name ?? ''}`.toLowerCase().includes(q))
      .slice(0, params.limit ?? 50);
  }),
}));
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  api: {
    deals: { create: worker.create, list: worker.list },
    documents: { upload: worker.upload },
    reference: { strMarkets: worker.strMarkets },
  },
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import NewProjectPage from '@/app/projects/new/page';
import { DRAFT_KEY } from '@/lib/wizardDraft';

const marketInput = () => screen.getByPlaceholderText('STR market, e.g. Chicago, IL') as HTMLInputElement;
const submarketInput = () => screen.getByPlaceholderText('Submarket') as HTMLInputElement;
const options = (testId: string) =>
  Array.from(screen.getByTestId(testId).querySelectorAll('option')).map((o) => o.value);
const seedReviewDraft = (fields: Record<string, string>) =>
  window.localStorage.setItem(DRAFT_KEY, JSON.stringify({
    v: 1, savedAt: Date.now() - 60_000, step: 5, files: [],
    fields: { dealName: 'Market Deal', ...fields },
  }));

const STR_ROWS: Row[] = [
  { str_market_id: 'TEST-M1', market_name: 'Test Market One', submarket_id: null, submarket_name: null, state: 'TS', country: 'Testland' },
  { str_market_id: 'TEST-M1', market_name: 'Test Market One', submarket_id: 'TEST-S1', submarket_name: 'Test Sub Alpha', state: 'TS', country: 'Testland' },
  { str_market_id: 'TEST-M2', market_name: 'Test Market Two', submarket_id: 'TEST-S2', submarket_name: 'Test Sub Beta', state: 'TS', country: 'Testland' },
];

beforeEach(() => {
  ref.rows = [];
  worker.create.mockClear();
  worker.strMarkets.mockClear();
  window.localStorage.clear();
});
afterEach(cleanup);

describe('Sam #6 — STR market list not loaded (today)', () => {
  it('Market falls back to the name-only STR market names (no id); Submarket suggests nothing', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.strMarkets).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/no STR id until STR’s market list is loaded/)).toBeInTheDocument());
    expect(options('market-suggestions')).toContain('Chicago, IL');
    expect(options('submarket-suggestions')).toEqual([]);
    expect(screen.getByText(/No suggestions until STR’s submarket list is loaded/)).toBeInTheDocument();
    expect(marketInput().getAttribute('list')).toBe('wizard-market-suggestions');

    fireEvent.change(marketInput(), { target: { value: 'Somewhere Typed' } });
    fireEvent.blur(marketInput());
    await waitFor(() => expect(screen.getByTestId('market-str-id')).toHaveTextContent('without an STR id'));
  });

  it('sends a typed market and submarket with str_market_id null; city is unchanged', async () => {
    seedReviewDraft({ city: 'Austin, TX', market: ' Typed  Market ', submarket: ' Typed Sub ' });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({
      city: 'Austin, TX', market: 'Typed Market', submarket: 'Typed Sub', str_market_id: null,
    });
  });

  it('sends the acquisition stage the wizard collected as deal_stage (go-live checklist)', async () => {
    seedReviewDraft({ stage: 'LOI' });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ deal_stage: 'LOI' });
  });

  it('sends null market / submarket when left blank', async () => {
    seedReviewDraft({});
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ market: null, submarket: null, str_market_id: null });
  });
});

describe('Sam #6 — STR market list loaded', () => {
  it('suggests STR markets, resolves a pick to STR spelling + id, narrows submarkets to it', async () => {
    ref.rows = STR_ROWS;
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Suggestions: STR market list')).toBeInTheDocument());
    await waitFor(() => expect(options('market-suggestions')).toEqual(['Test Market One', 'Test Market Two']));
    expect(options('market-suggestions')).not.toContain('Chicago, IL');

    fireEvent.change(marketInput(), { target: { value: 'test market one' } });
    fireEvent.blur(marketInput());
    await waitFor(() => expect(marketInput().value).toBe('Test Market One'));
    expect(screen.getByTestId('market-str-id')).toHaveTextContent('STR id TEST-M1');
    await waitFor(() => expect(options('submarket-suggestions')).toEqual(['Test Sub Alpha']));
    expect(submarketInput().getAttribute('list')).toBe('wizard-submarket-suggestions');

    // Typing over the pick drops the id until it resolves again.
    fireEvent.change(marketInput(), { target: { value: 'Not In The List' } });
    expect(screen.getByTestId('market-str-id')).toHaveTextContent('not in STR’s market list');
  });

  it('sends the STR id on create when the market matches STR’s list', async () => {
    ref.rows = STR_ROWS;
    seedReviewDraft({ market: 'Test Market Two', submarket: 'Test Sub Beta' });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({
      market: 'Test Market Two', str_market_id: 'TEST-M2', submarket: 'Test Sub Beta',
    });
  });
});
