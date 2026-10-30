/**
 * Create-deal wizard — external tester workbook (Linear FON-41), round 2, and
 * the server-side draft (FON-79, go-live plan ITEM A).
 *
 *   R-011  autosave to localStorage (debounced) + restore banner with
 *          Discard; staged files are listed for re-attach; draft cleared on
 *          successful create. This is the behaviour on a worker that cannot
 *          hold a draft (feature-detected: no ``draft_count`` on the pipeline
 *          summary) — the "legacy" mode below.
 *   R-012  City / Submarket suggestions (tenant deals + US markets) and
 *          normalization on blur / save.
 *   R-015  "Set as default" Return Profile, preselected on new deals.
 *   R-018  work-in-progress note at the Proposed brand picker.
 *   R-047  Adaptive Reuse is a selectable deal type and is submitted.
 *   FON-79 the draft row is created on the first debounced autosave after a
 *          non-blank Deal Name; every save PATCHes columns + wizard payload
 *          through a serial queue; resume from ``?draft=`` / the pointer;
 *          Finish uploads what is still staged, then activates; Discard calls
 *          the worker and shows its 409 reason; documents upload on add.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, act } from '@testing-library/react';
import React from 'react';

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => nav,
}));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void };
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const worker = vi.hoisted(() => {
  const state = {
    /** The worker can hold drafts (pipeline summary carries ``draft_count``). */
    drafts: false,
    /** Rows ``GET /deals/{id}/documents`` returns. */
    docs: [] as unknown[],
    /** What the resume read returns, keyed by id. */
    draftRows: {} as Record<string, unknown>,
  };
  const create = vi.fn(async (body: { name: string; lifecycle?: string; draft_wizard?: unknown }) =>
    body.lifecycle === 'draft'
      ? { id: 'draft-1', name: body.name, lifecycle: 'draft', status: 'Draft', draft_wizard: body.draft_wizard, updated_at: '2026-10-30T10:00:00Z' }
      : { id: 'deal-new-1', name: body.name });
  return {
    state,
    create,
    update: vi.fn(async (_id: string, patch: Record<string, unknown>) => ({ id: 'draft-1', lifecycle: 'draft', ...patch })),
    getDraft: vi.fn(async (id: string) => {
      const row = state.draftRows[id];
      if (!row) throw Object.assign(new Error(`GET /deals/${id} → 404`), { name: 'WorkerError', status: 404, body: '' });
      return row;
    }),
    activate: vi.fn(async (id: string) => ({ id, name: 'Server Draft', lifecycle: 'active', status: 'In Review' })),
    discardDraft: vi.fn(async () => undefined),
    pipeline: vi.fn(async () => ({ deals: [], summary: state.drafts ? { deal_count: 0, draft_count: 0 } : { deal_count: 0 } })),
    upload: vi.fn(async (_dealId: string, _files: unknown[]): Promise<unknown[]> => []),
    list: vi.fn(async () => [
      { id: 'd1', name: 'A', city: 'Bozeman,MT' },
      { id: 'd2', name: 'B', city: 'washington dc' },
      { id: 'd3', name: 'C', city: null },
    ]),
    docList: vi.fn(async () => state.docs),
    docDelete: vi.fn(async () => undefined),
    reclassify: vi.fn(async (_d: string, _id: string, patch: Record<string, unknown>) => ({ ...(state.docs[0] as object), ...patch })),
  };
});
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    isWorkerConnected: () => true,
    api: {
      deals: {
        create: worker.create, list: worker.list, update: worker.update, getDraft: worker.getDraft,
        activate: worker.activate, discardDraft: worker.discardDraft, pipeline: worker.pipeline,
      },
      documents: { upload: worker.upload, list: worker.docList, delete: worker.docDelete, reclassify: worker.reclassify },
    },
  };
});
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import NewProjectPage from '@/app/projects/new/page';
import { DRAFT_KEY, DRAFT_POINTER_KEY, DEFAULT_RETURN_PROFILE_KEY } from '@/lib/wizardDraft';
import { WorkerError } from '@/lib/api';
import { wizardCategoryForDocument, isFinancialDocument, hasFinancialStatement } from '@/lib/wizardDocuments';
import { DRAFT_RETRY_MS } from '@/lib/hooks/useServerDraft';

const clickNext = () => fireEvent.click(screen.getByRole('button', { name: /^next\s*$/i }));
const dealNameInput = () => screen.getByPlaceholderText('Chicago Downtown Acquisition') as HTMLInputElement;
const cityInput = () => screen.getByPlaceholderText('Chicago, IL') as HTMLInputElement;
const readDraft = () => {
  const raw = window.localStorage.getItem(DRAFT_KEY);
  return raw ? JSON.parse(raw) : null;
};
const seedDraft = (over: Record<string, unknown> = {}) =>
  window.localStorage.setItem(DRAFT_KEY, JSON.stringify({
    v: 1,
    savedAt: Date.now() - 5 * 60_000,
    step: 2,
    fields: { dealName: 'Restored Deal', city: 'Austin, TX', dealType: 'development', returnProfile: 'core' },
    files: [
      { name: 'T12-Mar-2026.xlsx', category: 'financials', fiscal_year: 2026 },
      { name: 'Anglers OM.pdf', category: 'om' },
    ],
    ...over,
  }));

beforeEach(() => {
  for (const fn of [worker.create, worker.upload, worker.list, worker.update, worker.getDraft, worker.activate,
    worker.discardDraft, worker.pipeline, worker.docList, worker.docDelete, worker.reclassify, nav.push, nav.replace]) {
    fn.mockClear();
  }
  worker.state.drafts = false;
  worker.state.docs = [];
  worker.state.draftRows = {};
  window.localStorage.clear();
  window.history.replaceState({}, '', '/projects/new');
});
afterEach(cleanup);

describe('R-011 — autosave + restore', () => {
  it('a fresh, untouched wizard writes no draft and shows no banner', async () => {
    render(<NewProjectPage />);
    await new Promise((r) => setTimeout(r, 700));
    expect(readDraft()).toBeNull();
    expect(screen.queryByTestId('draft-restored-banner')).toBeNull();
    expect(screen.queryByTestId('draft-save-status')).toBeNull();
  });

  it('autosaves the fields + step (debounced) and shows Saving… → Saved locally', async () => {
    render(<NewProjectPage />);
    fireEvent.change(dealNameInput(), { target: { value: 'Autosaved Deal' } });
    expect(screen.getByTestId('draft-save-status').textContent).toBe('Saving…');
    expect(readDraft()).toBeNull(); // debounced, not written synchronously
    await waitFor(() => expect(screen.getByTestId('draft-save-status').textContent).toBe('Saved locally'), { timeout: 2000 });
    clickNext();
    await waitFor(() => expect(readDraft()?.step).toBe(2), { timeout: 2000 });
    const d = readDraft();
    expect(d.v).toBe(1);
    expect(d.fields.dealName).toBe('Autosaved Deal');
    expect(d.files).toEqual([]);
  });

  it('records staged file NAMES (not bytes) per slot', async () => {
    render(<NewProjectPage />);
    fireEvent.change(dealNameInput(), { target: { value: 'Files Deal' } });
    clickNext();
    clickNext();
    fireEvent.click(screen.getByRole('button', { name: 'Financial Statements (missing)' }));
    const t12 = new File(['t12'], 'T12-Mar-2026.xlsx', { type: 'application/vnd.ms-excel' });
    fireEvent.change(screen.getByLabelText('Add Financial Statements files'), { target: { files: [t12] } });
    await waitFor(() => expect(readDraft()?.files?.[0]?.name).toBe('T12-Mar-2026.xlsx'), { timeout: 2000 });
    expect(readDraft().files[0].category).toBe('financials');
    expect(readDraft().step).toBe(3);
  });

  it('restores fields + step from a draft and lists the files to re-attach', () => {
    seedDraft();
    render(<NewProjectPage />);
    const banner = screen.getByTestId('draft-restored-banner');
    expect(banner.textContent).toContain('Restored your unfinished deal from 5 minutes ago');
    expect(screen.getByRole('button', { name: 'Discard' })).toBeInTheDocument();
    const reattach = screen.getByTestId('draft-reattach');
    expect(reattach.textContent).toContain('T12-Mar-2026.xlsx');
    expect(reattach.textContent).toContain('Financial Statements');
    expect(reattach.textContent).toContain('Anglers OM.pdf');
    // Step 2 restored with the saved profile selected.
    expect(screen.getByText('Return Requirements')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Core/, pressed: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    expect(dealNameInput().value).toBe('Restored Deal');
    expect(cityInput().value).toBe('Austin, TX');
    expect(screen.getByRole('button', { name: /^Development/, pressed: true })).toBeInTheDocument();
  });

  it('a re-attached file drops off the re-attach list', () => {
    seedDraft({ step: 3 });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Financial Statements (missing)' }));
    const t12 = new File(['t12'], 'T12-Mar-2026.xlsx', { type: 'application/vnd.ms-excel' });
    fireEvent.change(screen.getByLabelText('Add Financial Statements files'), { target: { files: [t12] } });
    const reattach = screen.getByTestId('draft-reattach');
    expect(reattach.textContent).not.toContain('T12-Mar-2026.xlsx');
    expect(reattach.textContent).toContain('Anglers OM.pdf');
  });

  it('Discard clears the draft and resets the wizard to step 1', async () => {
    seedDraft();
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    expect(screen.queryByTestId('draft-restored-banner')).toBeNull();
    expect(readDraft()).toBeNull();
    expect(dealNameInput().value).toBe('');
    await new Promise((r) => setTimeout(r, 700));
    expect(readDraft()).toBeNull();
  });

  it('clears the draft on a successful create and never re-saves it', async () => {
    seedDraft({ step: 5, files: [] });
    render(<NewProjectPage />);
    expect(screen.getByText('Review & Finish')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(readDraft()).toBeNull());
    await new Promise((r) => setTimeout(r, 700));
    expect(readDraft()).toBeNull();
  });

  it('ignores a corrupt draft', () => {
    window.localStorage.setItem(DRAFT_KEY, '{not json');
    render(<NewProjectPage />);
    expect(screen.queryByTestId('draft-restored-banner')).toBeNull();
    expect(screen.getByText('Create New Deal')).toBeInTheDocument();
  });
});

describe('R-012 — City / Submarket suggestions + normalization', () => {
  it('suggests tenant deal cities (normalized) then US markets', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.list).toHaveBeenCalled());
    await waitFor(() => {
      const opts = Array.from(screen.getByTestId('city-suggestions').querySelectorAll('option')).map((o) => o.value);
      expect(opts[0]).toBe('Bozeman, MT');
      expect(opts[1]).toBe('Washington, DC');
      expect(opts).toContain('Chicago, IL');
    });
    expect(cityInput().getAttribute('list')).toBe('wizard-city-suggestions');
  });

  it('normalizes on blur and sends the canonical spelling on create', async () => {
    render(<NewProjectPage />);
    fireEvent.change(dealNameInput(), { target: { value: 'City Deal' } });
    fireEvent.change(cityInput(), { target: { value: '  washington   dc ' } });
    fireEvent.blur(cityInput());
    expect(cityInput().value).toBe('Washington, DC');
    fireEvent.change(cityInput(), { target: { value: 'River North ,Chicago' } });
    fireEvent.blur(cityInput());
    expect(cityInput().value).toBe('River North, Chicago');
  });

  it('normalizes the city in the create payload even without a blur', async () => {
    seedDraft({ step: 6, files: [], fields: { dealName: 'Payload Deal', city: 'Washington,DC' } });
    render(<NewProjectPage />);
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ city: 'Washington, DC' });
  });
});

describe('R-015 — default Return Profile', () => {
  it('Set as default stores it, marks it Default, and preselects it on the next new deal; Clear removes it', () => {
    render(<NewProjectPage />);
    fireEvent.change(dealNameInput(), { target: { value: 'x' } });
    clickNext();
    // Value-Add is the built-in preselection; nothing is a saved default yet.
    expect(screen.queryByText('Default')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Set Core as default' }));
    expect(window.localStorage.getItem(DEFAULT_RETURN_PROFILE_KEY)).toBe('core');
    expect(screen.getByTestId('profile-default-core').textContent).toContain('Default');
    cleanup();
    window.localStorage.removeItem(DRAFT_KEY);

    render(<NewProjectPage />);
    fireEvent.change(dealNameInput(), { target: { value: 'y' } });
    clickNext();
    expect(screen.getByRole('button', { name: /^Core/, pressed: true })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Value Add/, pressed: false })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Clear default' }));
    expect(window.localStorage.getItem(DEFAULT_RETURN_PROFILE_KEY)).toBeNull();
    expect(screen.queryByText('Default')).toBeNull();
  });

  it('ignores an unknown stored default', () => {
    window.localStorage.setItem(DEFAULT_RETURN_PROFILE_KEY, 'bogus');
    render(<NewProjectPage />);
    fireEvent.change(dealNameInput(), { target: { value: 'z' } });
    clickNext();
    expect(screen.getByRole('button', { name: /^Value Add/, pressed: true })).toBeInTheDocument();
  });
});

describe('R-018 — brand note says what the proposed brand does today', () => {
  it('shows the exact note at the Proposed brand picker — never "work in progress"', () => {
    seedDraft({ step: 4, files: [] });
    render(<NewProjectPage />);
    expect(screen.getByRole('heading', { name: 'Brand and Positioning' })).toBeInTheDocument();
    const note = screen.getByTestId('brand-note').textContent?.replace(/\s+/g, ' ').trim() ?? '';
    expect(note).toBe(
      'The proposed brand sets the suggested positioning tier (STR chain scale) and labels the renovation plan. Brand programming requirements are not applied to CapEx.',
    );
    expect(note).not.toMatch(/work in progress/i);
    expect(screen.queryByTestId('brand-wip-note')).toBeNull();
  });
});

describe('R-047 — Adaptive Reuse deal type', () => {
  it('is selectable next to Acquisition and Development and is sent as deal_type "adaptive_reuse"', async () => {
    render(<NewProjectPage />);
    expect(screen.getByRole('button', { name: /^Acquisition/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^Development/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /^Adaptive Reuse/ }));
    expect(screen.getByRole('button', { name: /^Adaptive Reuse/, pressed: true })).toBeInTheDocument();
    fireEvent.change(dealNameInput(), { target: { value: 'Old Mill Conversion' } });
    // Reopen the autosaved draft on the Review step (skips the Documents
    // gate) and create from there.
    await waitFor(() => expect(readDraft()?.fields?.dealType).toBe('adaptive_reuse'), { timeout: 2000 });
    cleanup();
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...readDraft(), step: 5 }));
    render(<NewProjectPage />);
    const row = screen.getByText('Deal Type').nextElementSibling;
    expect(row?.textContent).toBe('Adaptive Reuse');
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ name: 'Old Mill Conversion', deal_type: 'adaptive_reuse' });
  });
});


// ─────────────────────────── FON-79 — server-side draft ───────────────────────────

const SERVER_DRAFT = {
  id: 'd-77', tenant_id: 't', name: 'Server Draft', city: 'Austin, TX', keys: null, service: null, brand: null,
  status: 'Draft', deal_stage: 'LOI', risk: null, ai_confidence: null, lifecycle: 'draft', activated_at: null,
  draft_wizard: { v: 1, step: 2, fields: { dealName: 'Server Draft', city: 'Austin, TX', dealType: 'development', returnProfile: 'core' } },
  created_at: '2026-10-30T09:00:00Z', updated_at: '2026-10-30T09:30:00Z',
};
const readPointer = () => {
  const raw = window.localStorage.getItem(DRAFT_POINTER_KEY);
  return raw ? (JSON.parse(raw) as { draftDealId: string; savedAt: number }) : null;
};
const statusText = () => screen.getByTestId('draft-save-status').textContent;
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const workerError = (status: number, detail: unknown) =>
  new WorkerError(`→ ${status}`, status, JSON.stringify({ detail }));

describe('FON-79 — the draft row is created on the first named autosave', () => {
  beforeEach(() => { worker.state.drafts = true; });

  it('creates the draft once the Deal Name is non-blank, keeps the pointer, drops the local draft', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.pipeline).toHaveBeenCalled());
    fireEvent.change(dealNameInput(), { target: { value: 'Autosaved Deal' } });
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1), { timeout: 2000 });
    const body = worker.create.mock.calls[0][0] as Record<string, unknown>;
    expect(body).toMatchObject({ name: 'Autosaved Deal', lifecycle: 'draft', deal_stage: 'Teaser' });
    expect(body.draft_wizard).toMatchObject({ v: 1, step: 1, fields: { dealName: 'Autosaved Deal' } });
    await waitFor(() => expect(statusText()).toBe('Saved to Fondok · just now'));
    expect(readPointer()?.draftDealId).toBe('draft-1');
    expect(readDraft()).toBeNull();
    // Nothing changed since the create → no PATCH follows it.
    await wait(700);
    expect(worker.update).not.toHaveBeenCalled();
  });

  it('a blank name never creates a row — the wizard stays browser-local', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.pipeline).toHaveBeenCalled());
    fireEvent.change(cityInput(), { target: { value: 'Austin, TX' } });
    await waitFor(() => expect(statusText()).toBe('Saved locally'), { timeout: 2000 });
    await wait(300);
    expect(worker.create).not.toHaveBeenCalled();
    expect(readPointer()).toBeNull();
    expect(readDraft()?.fields.city).toBe('Austin, TX');
  });

  it('issues ONE POST however many edits land while the create is in flight, then PATCHes the latest', async () => {
    const held = deferred<unknown>();
    worker.create.mockImplementationOnce(() => held.promise as Promise<never>);
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.pipeline).toHaveBeenCalled());
    fireEvent.change(dealNameInput(), { target: { value: 'One' } });
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1), { timeout: 2000 });
    fireEvent.change(dealNameInput(), { target: { value: 'One Two' } });
    await wait(700);
    fireEvent.change(dealNameInput(), { target: { value: 'One Two Three' } });
    await wait(700);
    expect(worker.create).toHaveBeenCalledTimes(1);
    expect(worker.update).not.toHaveBeenCalled();
    await act(async () => {
      held.resolve({ id: 'draft-1', name: 'One', lifecycle: 'draft', updated_at: '2026-10-30T10:00:00Z' });
    });
    await waitFor(() => expect(worker.update).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(worker.update.mock.calls[0][0]).toBe('draft-1');
    expect(worker.update.mock.calls[0][1]).toMatchObject({ name: 'One Two Three' });
    expect(worker.create).toHaveBeenCalledTimes(1);
  });
});

describe('FON-79 — every save is a PATCH through a serial queue', () => {
  beforeEach(() => {
    worker.state.drafts = true;
    worker.state.draftRows = { 'd-77': SERVER_DRAFT };
    window.localStorage.setItem(DRAFT_POINTER_KEY, JSON.stringify({ draftDealId: 'd-77', savedAt: Date.now() }));
  });

  it('debounces edits into one PATCH carrying the normalized columns and the wizard payload', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.getDraft).toHaveBeenCalledWith('d-77'));
    await waitFor(() => expect(screen.getByText('Return Requirements')).toBeInTheDocument());
    // Opening a draft is not an edit — nothing is written back unchanged.
    await wait(700);
    expect(worker.update).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    fireEvent.change(cityInput(), { target: { value: 'Austin' } });
    fireEvent.change(cityInput(), { target: { value: 'Austin, T' } });
    fireEvent.change(cityInput(), { target: { value: 'Austin, TX!' } });
    await waitFor(() => expect(worker.update).toHaveBeenCalledTimes(1), { timeout: 2000 });
    await wait(700);
    expect(worker.update).toHaveBeenCalledTimes(1);
    const [id, patch] = worker.update.mock.calls[0] as [string, Record<string, unknown>];
    expect(id).toBe('d-77');
    expect(patch).toMatchObject({ name: 'Server Draft', city: 'Austin, TX!', deal_type: 'development' });
    expect(patch.draft_wizard).toMatchObject({ v: 1, step: 1, fields: { city: 'Austin, TX!', dealName: 'Server Draft' } });
  });

  it('never has two PATCHes in flight — edits made meanwhile collapse into the next one', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Return Requirements')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    const held = deferred<unknown>();
    worker.update.mockImplementationOnce(() => held.promise as Promise<never>);
    fireEvent.change(cityInput(), { target: { value: 'A' } });
    await waitFor(() => expect(worker.update).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect(statusText()).toBe('Saving…');
    fireEvent.change(cityInput(), { target: { value: 'AB' } });
    await wait(700);
    fireEvent.change(cityInput(), { target: { value: 'ABC' } });
    await wait(700);
    expect(worker.update).toHaveBeenCalledTimes(1); // still only the held one
    await act(async () => { held.resolve({ id: 'd-77', lifecycle: 'draft' }); });
    await waitFor(() => expect(worker.update).toHaveBeenCalledTimes(2), { timeout: 2000 });
    expect(worker.update.mock.calls[1][1]).toMatchObject({ city: 'ABC' });
    await waitFor(() => expect(statusText()).toBe('Saved to Fondok · just now'));
    await wait(700);
    expect(worker.update).toHaveBeenCalledTimes(2);
  });

  it('a failed PATCH reads "Couldn’t save — retrying" and is retried', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Return Requirements')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    worker.update.mockImplementationOnce(async () => { throw workerError(500, 'boom'); });
    fireEvent.change(cityInput(), { target: { value: 'Failing' } });
    await waitFor(() => expect(worker.update).toHaveBeenCalledTimes(1), { timeout: 2000 });
    await waitFor(() => expect(statusText()).toBe('Couldn’t save — retrying'));
    await waitFor(() => expect(worker.update).toHaveBeenCalledTimes(2), { timeout: DRAFT_RETRY_MS + 2000 });
    expect(worker.update.mock.calls[1][1]).toMatchObject({ city: 'Failing' });
    await waitFor(() => expect(statusText()).toBe('Saved to Fondok · just now'));
  }, 10_000);
});

describe('FON-79 — resume', () => {
  beforeEach(() => {
    worker.state.drafts = true;
    worker.state.draftRows = { 'd-77': SERVER_DRAFT };
  });

  it('?draft=<id> reads the row with include=draft_wizard and restores fields + step', async () => {
    window.history.replaceState({}, '', '/projects/new?draft=d-77');
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.getDraft).toHaveBeenCalledWith('d-77'));
    await waitFor(() => expect(screen.getByText('Return Requirements')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /^Core/, pressed: true })).toBeInTheDocument();
    const banner = screen.getByTestId('draft-restored-banner');
    expect(banner.textContent).toContain('Resumed your draft');
    // No "re-attach these files" for a connected session — the uploads are on the row.
    expect(screen.queryByTestId('draft-reattach')).toBeNull();
    expect(readPointer()?.draftDealId).toBe('d-77');
    // Opening the draft is not an edit: the header reads saved, nothing is written.
    expect(statusText()).toMatch(/^Saved to Fondok · /);
    await wait(700);
    expect(worker.update).not.toHaveBeenCalled();
    // Going back a step IS an edit — the step travels in the payload.
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    expect(dealNameInput().value).toBe('Server Draft');
    expect(cityInput().value).toBe('Austin, TX');
    expect(screen.getByRole('button', { name: /^Development/, pressed: true })).toBeInTheDocument();
    await waitFor(() => expect(worker.update).toHaveBeenCalledTimes(1), { timeout: 2000 });
    expect((worker.update.mock.calls[0][1] as { draft_wizard: { step: number } }).draft_wizard.step).toBe(1);
  });

  it('with no ?draft the stored pointer reopens the draft; a stale local draft does not restore over it', async () => {
    window.localStorage.setItem(DRAFT_POINTER_KEY, JSON.stringify({ draftDealId: 'd-77', savedAt: Date.now() }));
    seedDraft({ fields: { dealName: 'Stale Local' } });
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Return Requirements')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /back/i }));
    expect(dealNameInput().value).toBe('Server Draft');
    expect(readDraft()).toBeNull();
  });

  it('an id that is no longer a draft opens the deal page; a missing id starts fresh and clears the pointer', async () => {
    worker.state.draftRows = { 'd-77': { ...SERVER_DRAFT, lifecycle: 'active', draft_wizard: null } };
    window.history.replaceState({}, '', '/projects/new?draft=d-77');
    render(<NewProjectPage />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/projects/d-77'));
    cleanup();
    window.localStorage.setItem(DRAFT_POINTER_KEY, JSON.stringify({ draftDealId: 'gone', savedAt: Date.now() }));
    window.history.replaceState({}, '', '/projects/new');
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.getDraft).toHaveBeenCalledWith('gone'));
    await waitFor(() => expect(screen.getByText('Create New Deal')).toBeInTheDocument());
    expect(dealNameInput().value).toBe('');
    expect(readPointer()).toBeNull();
  });

  it('shows how many drafts the tenant already has, linking to Projects', async () => {
    worker.list.mockResolvedValueOnce([
      { id: 'x', name: 'X', city: null, lifecycle: 'draft' },
      { id: 'y', name: 'Y', city: null, lifecycle: 'draft' },
      { id: 'z', name: 'Z', city: null, lifecycle: 'active' },
    ] as never);
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByTestId('tenant-drafts-chip')).toHaveTextContent('You have 2 unfinished drafts'));
    expect(screen.getByRole('link', { name: 'See them on Projects' })).toHaveAttribute('href', '/projects?filter=Drafts');
  });
});

describe('FON-79 — Finish and Discard', () => {
  beforeEach(() => {
    worker.state.drafts = true;
    worker.state.draftRows = { 'd-77': { ...SERVER_DRAFT, draft_wizard: { ...SERVER_DRAFT.draft_wizard, step: 5 } } };
    window.history.replaceState({}, '', '/projects/new?draft=d-77');
  });

  it('Finish flushes the last save, activates the draft and opens the deal — no second create', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Review & Finish')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.activate).toHaveBeenCalledWith('d-77'));
    expect(worker.create).not.toHaveBeenCalled();
    // The row took the final columns before activate.
    const lastUpdate = worker.update.mock.invocationCallOrder.at(-1) ?? 0;
    expect(lastUpdate).toBeLessThan(worker.activate.mock.invocationCallOrder[0]);
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/projects/d-77'));
    expect(readPointer()).toBeNull();
    expect(readDraft()).toBeNull();
  });

  it('Discard calls the worker, clears the pointer and resets the wizard', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Review & Finish')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(worker.discardDraft).toHaveBeenCalledWith('d-77'));
    await waitFor(() => expect(screen.getByText('Create New Deal')).toBeInTheDocument());
    expect(dealNameInput().value).toBe('');
    expect(readPointer()).toBeNull();
    expect(screen.queryByTestId('draft-restored-banner')).toBeNull();
    expect(nav.replace).toHaveBeenCalledWith('/projects/new');
  });

  it('a 409 documents_in_flight refusal is shown as the worker’s own message and the draft stays', async () => {
    worker.discardDraft.mockImplementationOnce(async () => {
      throw workerError(409, {
        code: 'documents_in_flight', count: 2,
        message: '2 document(s) are still being processed. Wait for extraction to finish, then delete the deal.',
      });
    });
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByText('Review & Finish')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }));
    await waitFor(() => expect(screen.getByTestId('draft-discard-error')).toHaveTextContent(
      '2 document(s) are still being processed. Wait for extraction to finish, then delete the deal.',
    ));
    expect(screen.getByText('Review & Finish')).toBeInTheDocument();
    expect(readPointer()?.draftDealId).toBe('d-77');
  });
});

describe('FON-79 — Documents step on a draft', () => {
  const T12_ROW = {
    id: 'doc-1', deal_id: 'd-77', tenant_id: 't', filename: 'T12-Mar-2026.xlsx', doc_type: null,
    status: 'PARSING', uploaded_at: '2026-10-30T09:40:00Z', content_hash: null, storage_key: null,
    size_bytes: 2048, page_count: null, parser: null, error_kind: null, error_message: null,
    user_provided_doc_type: 'T12', fiscal_year: 2026,
  };
  beforeEach(() => {
    worker.state.drafts = true;
    worker.state.draftRows = { 'd-77': { ...SERVER_DRAFT, draft_wizard: { ...SERVER_DRAFT.draft_wizard, step: 3 } } };
    window.history.replaceState({}, '', '/projects/new?draft=d-77');
  });

  it('uploads on add (one call per batch), lists the row with the Data Room status word, and the gate reads it', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByTestId('wizard-documents-step')).toBeInTheDocument());
    await waitFor(() => expect(worker.docList).toHaveBeenCalled());
    // Nothing uploaded yet → the gate is shut.
    expect(screen.getByRole('button', { name: /^next\s*$/i })).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Financial Statements (missing)' }));
    const t12 = new File(['t12'], 'T12-Mar-2026.xlsx', { type: 'application/vnd.ms-excel' });
    worker.upload.mockImplementationOnce(async () => { worker.state.docs = [T12_ROW]; return [T12_ROW] as never; });
    fireEvent.change(screen.getByLabelText('Add Financial Statements files'), { target: { files: [t12] } });
    await waitFor(() => expect(worker.upload).toHaveBeenCalledTimes(1));
    expect(worker.upload.mock.calls[0][0]).toBe('d-77');
    expect((worker.upload.mock.calls[0][1] as Array<{ file: File; category: string }>)[0]).toMatchObject({ category: 'financials' });
    const row = await screen.findByTestId('wizard-uploaded-doc');
    expect(row).toHaveTextContent('T12-Mar-2026.xlsx');
    expect(row).toHaveTextContent('Processing');
    expect(row.textContent).not.toContain('PARSING');
    // The staged copy is gone; the server row counts in the sidebar and clears the gate.
    expect(screen.getByRole('button', { name: 'Financial Statements (1 file)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^next\s*$/i })).not.toHaveAttribute('aria-disabled');
    // Remove calls the worker.
    fireEvent.click(screen.getByRole('button', { name: 'Remove T12-Mar-2026.xlsx' }));
    await waitFor(() => expect(worker.docDelete).toHaveBeenCalledWith('d-77', 'doc-1'));
  });

  it('Finish uploads a file whose on-add upload failed, then activates', async () => {
    render(<NewProjectPage />);
    await waitFor(() => expect(screen.getByTestId('wizard-documents-step')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'Financial Statements (missing)' }));
    worker.upload.mockImplementationOnce(async () => { throw workerError(500, 'storage down'); });
    const t12 = new File(['t12'], 'T12-Mar-2026.xlsx', { type: 'application/vnd.ms-excel' });
    fireEvent.change(screen.getByLabelText('Add Financial Statements files'), { target: { files: [t12] } });
    await waitFor(() => expect(worker.upload).toHaveBeenCalledTimes(1));
    // Still staged (Remove is offered again once the attempt settles).
    await screen.findByRole('button', { name: 'Remove T12-Mar-2026.xlsx' });
    fireEvent.click(screen.getByRole('button', { name: /^next\s*$/i })); // → 4 (gate: staged financial)
    fireEvent.click(screen.getByRole('button', { name: /^next\s*$/i })); // → 5
    await waitFor(() => expect(screen.getByText('Review & Finish')).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: /^finish$/i }));
    await waitFor(() => expect(worker.upload).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(worker.activate).toHaveBeenCalledWith('d-77'));
    expect(worker.upload.mock.invocationCallOrder[1]).toBeLessThan(worker.activate.mock.invocationCallOrder[0]);
  });
});

describe('FON-79 — feature detection', () => {
  it('a worker whose pipeline summary has no draft_count keeps the browser-local autosave and creates only on Finish', async () => {
    worker.state.drafts = false;
    render(<NewProjectPage />);
    await waitFor(() => expect(worker.pipeline).toHaveBeenCalled());
    fireEvent.change(dealNameInput(), { target: { value: 'Legacy Deal' } });
    await waitFor(() => expect(statusText()).toBe('Saved locally'), { timeout: 2000 });
    await wait(300);
    expect(worker.create).not.toHaveBeenCalled();
    expect(readDraft()?.fields.dealName).toBe('Legacy Deal');
  });
});

describe('FON-79 — a server document finds its wizard slot', () => {
  it('reads the analyst tag first, then the classification, then the CapEx lane', () => {
    expect(wizardCategoryForDocument({ user_provided_doc_type: 'T12', doc_type: null })).toBe('financials');
    expect(wizardCategoryForDocument({ user_provided_doc_type: null, doc_type: 'OM' })).toBe('om');
    expect(wizardCategoryForDocument({ user_provided_doc_type: 'STR_TREND', doc_type: 'PNL' })).toBe('str');
    expect(wizardCategoryForDocument({ user_provided_doc_type: 'CAPEX', doc_type: null, doc_subtype: 'future' })).toBe('future_capex');
    expect(wizardCategoryForDocument({ user_provided_doc_type: 'CAPEX', doc_type: null, doc_subtype: null })).toBe('pip_capex');
    expect(wizardCategoryForDocument({ user_provided_doc_type: null, doc_type: null })).toBe('auto');
  });
  it('the gate counts an uploaded financial, not an unclassified auto-zone upload', () => {
    expect(isFinancialDocument({ user_provided_doc_type: null, doc_type: 'PNL_YTD' })).toBe(true);
    expect(isFinancialDocument({ user_provided_doc_type: 'OM', doc_type: 'T12' })).toBe(false);
    expect(hasFinancialStatement([], [{ user_provided_doc_type: null, doc_type: null } as never])).toBe(false);
    expect(hasFinancialStatement([], [{ user_provided_doc_type: null, doc_type: 'T12' } as never])).toBe(true);
  });
});
