/**
 * Sam #6 / FON-96 — Market / Submarket in the New Deal wizard.
 *
 *   - City, Market and Submarket are three separate, clearly labelled fields.
 *   - The CoStar list NOT available (withheld by licensing, or not loaded):
 *     Market suggests the public name-only STR market names, labelled so;
 *     Submarket suggests nothing; a typed market / submarket is sent with
 *     null keys.
 *   - The list available: Market suggests CoStar's markets, a pick resolves
 *     to the list's spelling + key (hidden — the hint says "CoStar market"),
 *     Submarket suggests only that market's submarkets ("submarket · market"
 *     until a market is picked), and both keys are sent on create.
 *
 * Every key / name here is a synthetic TEST value (fictional markets).
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
  market_type?: string | null;
};
const ref = vi.hoisted(() => ({ rows: [] as Row[] }));
const worker = vi.hoisted(() => ({
  create: vi.fn(async (body: { name: string }) => ({ id: 'deal-new-1', name: body.name })),
  upload: vi.fn(async () => []),
  list: vi.fn(async () => []),
  strMarkets: vi.fn(async (params: {
    q?: string; str_market_id?: string; market_key?: string; kind?: 'market' | 'submarket'; limit?: number;
  } = {}) => {
    const q = (params.q ?? '').trim().toLowerCase();
    const key = params.market_key ?? params.str_market_id;
    return ref.rows
      .filter((r) => !key || r.str_market_id === key)
      .filter((r) => params.kind !== 'market' || r.submarket_id === null)
      .filter((r) => params.kind !== 'submarket' || r.submarket_id !== null)
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

const marketInput = () => screen.getByPlaceholderText('Market, e.g. Chicago, IL') as HTMLInputElement;
const submarketInput = () => screen.getByPlaceholderText('Submarket') as HTMLInputElement;
const options = (testId: string) =>
  Array.from(screen.getByTestId(testId).querySelectorAll('option')).map((o) => o.value);
const seedReviewDraft = (fields: Record<string, string>) =>
  window.localStorage.setItem(DRAFT_KEY, JSON.stringify({
    v: 1, savedAt: Date.now() - 60_000, step: 5, files: [],
    fields: { dealName: 'Market Deal', ...fields },
  }));

const NORTH = 'costar:northhaven-ts-usa';
const SOUTH = 'costar:southport-tz-usa';
const STR_ROWS: Row[] = [
  { str_market_id: NORTH, market_name: 'Northhaven - TS USA', submarket_id: null, submarket_name: null, state: 'TS', country: 'USA', market_type: 'metro' },
  { str_market_id: NORTH, market_name: 'Northhaven - TS USA', submarket_id: `${NORTH}/harbor-district`, submarket_name: 'Harbor District', state: 'TS', country: 'USA', market_type: 'metro' },
  { str_market_id: NORTH, market_name: 'Northhaven - TS USA', submarket_id: `${NORTH}/riverside`, submarket_name: 'Riverside', state: 'TS', country: 'USA', market_type: 'metro' },
  { str_market_id: SOUTH, market_name: 'Southport - TZ USA', submarket_id: null, submarket_name: null, state: 'TZ', country: 'USA', market_type: 'metro' },
  { str_market_id: SOUTH, market_name: 'Southport - TZ USA', submarket_id: `${SOUTH}/riverside`, submarket_name: 'Riverside', state: 'TZ', country: 'USA', market_type: 'metro' },
];

beforeEach(() => {
  ref.rows = [];
  worker.create.mockClear();
  worker.strMarkets.mockClear();
  window.localStorage.clear();
});
afterEach(cleanup);

describe('FON-96 — the CoStar list is not available to this workspace', () => {
  it('Market falls back to the public name-only STR market names (no key); Submarket suggests nothing', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.strMarkets).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText(/the CoStar market list is not available to this workspace/)).toBeInTheDocument());
    expect(options('market-suggestions')).toContain('Chicago, IL');
    expect(options('submarket-suggestions')).toEqual([]);
    expect(screen.getByText(/No suggestions — the CoStar submarket list is not available/)).toBeInTheDocument();
    expect(marketInput().getAttribute('list')).toBe('wizard-market-suggestions');

    fireEvent.change(marketInput(), { target: { value: 'Somewhere Typed' } });
    fireEvent.blur(marketInput());
    await waitFor(() => expect(screen.getByTestId('market-str-id')).toHaveTextContent('Analyst-entered · saved as typed'));
    expect(screen.queryByText(/STR id/)).toBeNull();
  });

  it('sends a typed market and submarket with null keys; city is unchanged', async () => {
    seedReviewDraft({ city: 'Austin, TX', market: ' Typed  Market ', submarket: ' Typed Sub ' });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({
      city: 'Austin, TX', market: 'Typed Market', submarket: 'Typed Sub', str_market_id: null, submarket_id: null,
    });
  });

  it('sends the acquisition stage the wizard collected as deal_stage (go-live checklist)', async () => {
    seedReviewDraft({ stage: 'LOI' });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ deal_stage: 'LOI' });
  });

  it('sends null market / submarket when left blank', async () => {
    seedReviewDraft({});
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ market: null, submarket: null, str_market_id: null, submarket_id: null });
  });
});

describe('FON-96 — the CoStar list is available', () => {
  it('suggests CoStar markets, resolves "City, ST" to the list spelling + key, narrows submarkets to it', async () => {
    ref.rows = STR_ROWS;
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Suggestions: CoStar market list')).toBeInTheDocument());
    await waitFor(() => expect(options('market-suggestions')).toEqual(['Northhaven - TS USA', 'Southport - TZ USA']));
    expect(options('market-suggestions')).not.toContain('Chicago, IL');
    // With no market picked, submarkets are "submarket · market" so the two
    // Riversides stay distinguishable.
    await waitFor(() => expect(options('submarket-suggestions')).toEqual([
      'Harbor District · Northhaven - TS USA', 'Riverside · Northhaven - TS USA', 'Riverside · Southport - TZ USA',
    ]));

    fireEvent.change(marketInput(), { target: { value: 'northhaven, ts' } });
    fireEvent.blur(marketInput());
    await waitFor(() => expect(marketInput().value).toBe('Northhaven - TS USA'));
    expect(screen.getByTestId('market-str-id')).toHaveTextContent('CoStar market');
    expect(screen.getByTestId('market-str-id')).not.toHaveTextContent('costar:');
    await waitFor(() => expect(options('submarket-suggestions')).toEqual(['Harbor District', 'Riverside']));
    expect(submarketInput().getAttribute('list')).toBe('wizard-submarket-suggestions');

    fireEvent.change(submarketInput(), { target: { value: 'riverside' } });
    fireEvent.blur(submarketInput());
    await waitFor(() => expect(submarketInput().value).toBe('Riverside'));
    expect(screen.getByTestId('submarket-key')).toHaveTextContent('CoStar submarket of Northhaven - TS USA');

    // Typing over the pick drops the key until it resolves again.
    fireEvent.change(marketInput(), { target: { value: 'Not In The List' } });
    expect(screen.getByTestId('market-str-id')).toHaveTextContent('Analyst-entered · not in the CoStar list');
  });

  it('a "submarket · market" pick made before any market sets the market too', async () => {
    ref.rows = STR_ROWS;
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Suggestions: CoStar market list')).toBeInTheDocument());
    fireEvent.change(submarketInput(), { target: { value: 'Riverside · Southport - TZ USA' } });
    fireEvent.blur(submarketInput());
    await waitFor(() => expect(submarketInput().value).toBe('Riverside'));
    expect(marketInput().value).toBe('Southport - TZ USA');
    expect(screen.getByTestId('market-str-id')).toHaveTextContent('CoStar market');
    expect(screen.getByTestId('submarket-key')).toHaveTextContent('CoStar submarket of Southport - TZ USA');
  });

  it('sends both keys on create when market and submarket match the list; an ambiguous submarket sends none', async () => {
    ref.rows = STR_ROWS;
    seedReviewDraft({ market: 'Southport, TZ', submarket: 'Riverside' });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({
      market: 'Southport - TZ USA', str_market_id: SOUTH, submarket: 'Riverside', submarket_id: `${SOUTH}/riverside`,
    });

    worker.create.mockClear();
    cleanup();
    // A typed submarket that is not one of the picked market's → no key.
    seedReviewDraft({ market: 'Southport, TZ', submarket: 'Harbor District' });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({
      market: 'Southport - TZ USA', str_market_id: SOUTH, submarket: 'Harbor District', submarket_id: null,
    });
  });
});
