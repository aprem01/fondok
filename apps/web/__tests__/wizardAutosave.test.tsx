/**
 * Create-deal wizard — external tester workbook (Linear FON-41), round 2.
 *
 *   R-011  autosave to localStorage (debounced) + restore banner with
 *          Discard; staged files are listed for re-attach; draft cleared on
 *          successful create.
 *   R-012  City / Submarket suggestions (tenant deals + US markets) and
 *          normalization on blur / save.
 *   R-015  "Set as default" Return Profile, preselected on new deals.
 *   R-018  work-in-progress note at the Proposed brand picker.
 *   R-047  Adaptive Reuse is a selectable deal type and is submitted.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor } from '@testing-library/react';
import React from 'react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
}));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

const worker = vi.hoisted(() => ({
  create: vi.fn(async (body: { name: string }) => ({ id: 'deal-new-1', name: body.name })),
  upload: vi.fn(async () => []),
  list: vi.fn(async () => [
    { id: 'd1', name: 'A', city: 'Bozeman,MT' },
    { id: 'd2', name: 'B', city: 'washington dc' },
    { id: 'd3', name: 'C', city: null },
  ]),
}));
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  api: {
    deals: { create: worker.create, list: worker.list },
    documents: { upload: worker.upload },
  },
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

import NewProjectPage from '@/app/projects/new/page';
import { DRAFT_KEY, DEFAULT_RETURN_PROFILE_KEY } from '@/lib/wizardDraft';

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
  worker.create.mockClear();
  worker.upload.mockClear();
  worker.list.mockClear();
  window.localStorage.clear();
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
    expect(screen.getByText('Review & Create Deal')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
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
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
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

describe('R-018 — brand work-in-progress note', () => {
  it('shows the exact note at the Proposed brand picker', () => {
    seedDraft({ step: 4, files: [] });
    render(<NewProjectPage />);
    expect(screen.getByRole('heading', { name: 'Brand and Positioning' })).toBeInTheDocument();
    expect(screen.getByTestId('brand-wip-note').textContent?.replace(/\s+/g, ' ').trim()).toBe(
      "This section is a work in progress. The intent is to use the selected brand's preliminary programming requirements when assessing the property improvement plan and required CapEx.",
    );
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
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ name: 'Old Mill Conversion', deal_type: 'adaptive_reuse' });
  });
});
