/**
 * FON-96 carry-overs from the Wave 2 drafts review (Wave 3, Task 14) — two
 * request-sequence contracts on the New Deal wizard's market / submarket keys.
 *
 *   1. Submarket blur race. The Submarket blur resolves the typed text within
 *      a market. If the analyst re-picks the market while that resolution is
 *      still in flight, the late answer belongs to the OLD market: it is
 *      discarded, never written into the fields (and so never PATCHed as a
 *      submarket key under the new market).
 *   2. Finish → activate fails. Finish resolves the market / submarket keys
 *      for its final save. When activate then fails, the wizard (and the
 *      draft payload a resume restores) keeps those keys — the next autosave
 *      must not downgrade the row to null keys.
 *
 * Every key / name here is a synthetic TEST value (fictional markets).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
}));
vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));

type Row = {
  str_market_id: string; market_name: string; submarket_id: string | null;
  submarket_name: string | null; state: string | null; country: string | null;
};
type Params = { q?: string; market_key?: string; str_market_id?: string; kind?: 'market' | 'submarket'; limit?: number };

const NORTH = 'costar:northhaven-ts-usa';
const SOUTH = 'costar:southport-tz-usa';
const ROWS: Row[] = [
  { str_market_id: NORTH, market_name: 'Northhaven - TS USA', submarket_id: null, submarket_name: null, state: 'TS', country: 'USA' },
  { str_market_id: NORTH, market_name: 'Northhaven - TS USA', submarket_id: `${NORTH}/riverside`, submarket_name: 'Riverside', state: 'TS', country: 'USA' },
  { str_market_id: SOUTH, market_name: 'Southport - TZ USA', submarket_id: null, submarket_name: null, state: 'TZ', country: 'USA' },
  { str_market_id: SOUTH, market_name: 'Southport - TZ USA', submarket_id: `${SOUTH}/riverside`, submarket_name: 'Riverside', state: 'TZ', country: 'USA' },
];

const w = vi.hoisted(() => {
  const state = {
    /** While set, a submarket lookup inside this market waits on `gate`. */
    holdMarketKey: null as string | null,
    gate: null as null | Promise<void>,
    release: () => {},
    draftRows: {} as Record<string, unknown>,
    activateFails: false,
  };
  return {
    state,
    strMarkets: vi.fn(async (_p: unknown) => [] as unknown[]),
    update: vi.fn(async (_id: string, patch: Record<string, unknown>) => ({ id: 'd-77', lifecycle: 'draft', ...patch })),
    create: vi.fn(async (body: { name: string; lifecycle?: string }) => ({
      id: 'd-77', name: body.name, lifecycle: body.lifecycle ?? 'active', status: 'Draft', updated_at: '2026-10-30T10:00:00Z',
    })),
    getDraft: vi.fn(async (id: string) => state.draftRows[id]),
    activate: vi.fn(async (id: string) => {
      if (state.activateFails) {
        throw Object.assign(new Error(`POST /deals/${id}/activate → 503`), { name: 'WorkerError', status: 503, body: '' });
      }
      return { id, name: 'Server Draft', lifecycle: 'active', status: 'In Review' };
    }),
    pipeline: vi.fn(async () => ({ deals: [], summary: { deal_count: 0, draft_count: 0 } })),
    list: vi.fn(async () => []),
    docList: vi.fn(async () => []),
  };
});
w.strMarkets.mockImplementation(async (raw: unknown) => {
  const p = (raw ?? {}) as Params;
  const key = p.market_key ?? p.str_market_id;
  if (p.kind === 'submarket' && key && key === w.state.holdMarketKey && w.state.gate) await w.state.gate;
  const q = (p.q ?? '').trim().toLowerCase();
  return ROWS
    .filter((r) => !key || r.str_market_id === key)
    .filter((r) => p.kind !== 'market' || r.submarket_id === null)
    .filter((r) => p.kind !== 'submarket' || r.submarket_id !== null)
    .filter((r) => !q || `${r.market_name} ${r.submarket_name ?? ''}`.toLowerCase().includes(q))
    .slice(0, p.limit ?? 50);
});
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      deals: {
        create: w.create, list: w.list, update: w.update, getDraft: w.getDraft,
        activate: w.activate, discardDraft: vi.fn(), pipeline: w.pipeline,
      },
      documents: { upload: vi.fn(async () => []), list: w.docList, delete: vi.fn(), reclassify: vi.fn() },
      reference: { strMarkets: w.strMarkets },
    },
  };
});
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import NewProjectPage from '@/app/projects/new/page';

const marketInput = () => screen.getByPlaceholderText('Market, e.g. Chicago, IL') as HTMLInputElement;
const submarketInput = () => screen.getByPlaceholderText('Submarket') as HTMLInputElement;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
type Body = { str_market_id?: string | null; submarket_id?: string | null; draft_wizard?: { fields?: Record<string, string> } };
const bodies = (): Body[] => [
  ...w.create.mock.calls.map((c) => c[0] as Body),
  ...w.update.mock.calls.map((c) => c[1] as Body),
];

const SERVER_DRAFT = {
  id: 'd-77', tenant_id: 't', name: 'Server Draft', city: 'Austin, TX', keys: null, service: null, brand: null,
  status: 'Draft', deal_stage: 'LOI', risk: null, ai_confidence: null, lifecycle: 'draft', activated_at: null,
  market: 'Southport, TZ', submarket: 'Riverside', str_market_id: null, submarket_id: null,
  draft_wizard: {
    v: 1, step: 5,
    fields: { dealName: 'Server Draft', city: 'Austin, TX', dealType: 'acquisition', returnProfile: 'core', market: 'Southport, TZ', submarket: 'Riverside' },
  },
  created_at: '2026-10-30T09:00:00Z', updated_at: '2026-10-30T09:30:00Z',
};

beforeEach(() => {
  for (const fn of [w.strMarkets, w.update, w.create, w.getDraft, w.activate, w.pipeline, w.list, w.docList]) fn.mockClear();
  w.state.holdMarketKey = null;
  w.state.gate = null;
  w.state.activateFails = false;
  w.state.draftRows = { 'd-77': SERVER_DRAFT };
  window.localStorage.clear();
  window.history.replaceState({}, '', '/projects/new');
});
afterEach(cleanup);

describe('Submarket blur — a late resolution against a market the analyst has since changed is discarded', () => {
  it('market re-picked while the submarket lookup is in flight → no stale submarket key is written or PATCHed', async () => {
    window.history.replaceState({}, '', '/projects/new?draft=d-77');
    w.state.draftRows = {
      'd-77': { ...SERVER_DRAFT, draft_wizard: { v: 1, step: 1, fields: { ...SERVER_DRAFT.draft_wizard.fields, market: '', submarket: '' } } },
    };
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Suggestions: CoStar market list')).toBeInTheDocument());

    // Pick Northhaven.
    fireEvent.change(marketInput(), { target: { value: 'northhaven, ts' } });
    fireEvent.blur(marketInput());
    await waitFor(() => expect(marketInput().value).toBe('Northhaven - TS USA'));

    // Type Riverside and blur — its lookup within Northhaven is held.
    let release!: () => void;
    w.state.gate = new Promise<void>((r) => { release = r; });
    w.state.holdMarketKey = NORTH;
    fireEvent.change(submarketInput(), { target: { value: 'Riverside' } });
    fireEvent.blur(submarketInput());

    // Before it answers, the analyst re-picks the market: Southport.
    fireEvent.change(marketInput(), { target: { value: 'southport, tz' } });
    fireEvent.blur(marketInput());
    await waitFor(() => expect(marketInput().value).toBe('Southport - TZ USA'));
    await waitFor(() => expect(screen.getByTestId('submarket-key')).toHaveTextContent('CoStar submarket of Southport - TZ USA'));

    // Now the Northhaven answer lands — late.
    release();
    await wait(50);
    expect(screen.getByTestId('submarket-key')).toHaveTextContent('CoStar submarket of Southport - TZ USA');
    // Let the debounced autosave run.
    await wait(900);
    await waitFor(() => expect(w.update).toHaveBeenCalled());
    for (const b of bodies()) {
      expect(b.submarket_id ?? null, JSON.stringify(b)).not.toBe(`${NORTH}/riverside`);
      expect(b.draft_wizard?.fields?.submarketId ?? '', 'draft payload').not.toBe(`${NORTH}/riverside`);
    }
    const last = w.update.mock.calls.at(-1)![1] as Body;
    expect(last).toMatchObject({ str_market_id: SOUTH, submarket_id: `${SOUTH}/riverside` });
  });
});

describe('Finish → activate fails → the resolved keys stay with the draft', () => {
  it('the final save carries the keys in the draft payload, and the next autosave does not downgrade them', async () => {
    window.history.replaceState({}, '', '/projects/new?draft=d-77');
    w.state.activateFails = true;
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Review & Finish')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(w.activate).toHaveBeenCalledWith('d-77'));

    // Finish's own save: columns AND the payload a resume restores carry the keys.
    const finishSave = w.update.mock.calls.at(-1)![1] as Body;
    expect(finishSave).toMatchObject({ str_market_id: SOUTH, submarket_id: `${SOUTH}/riverside` });
    expect(finishSave.draft_wizard?.fields).toMatchObject({ strMarketId: SOUTH, submarketId: `${SOUTH}/riverside` });

    // The analyst keeps working after the failed activate — an edit autosaves.
    const before = w.update.mock.calls.length;
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    await waitFor(() => expect(w.update.mock.calls.length).toBeGreaterThan(before), { timeout: 2000 });
    const next = w.update.mock.calls.at(-1)![1] as Body;
    expect(next).toMatchObject({ str_market_id: SOUTH, submarket_id: `${SOUTH}/riverside` });
    expect(next.draft_wizard?.fields).toMatchObject({ strMarketId: SOUTH, submarketId: `${SOUTH}/riverside` });
  });
});
