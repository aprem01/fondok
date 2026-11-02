/**
 * Wave 5 — the one status control on the deal header.
 *
 *  - It shows the current status and, opened, offers exactly the five manual
 *    statuses (Draft follows the wizard; Archived has its own action).
 *  - IC Ready / IC Approved / IC Not Approved are disabled with the reason
 *    until the model has run; Ready for Review never needs a run.
 *  - A choice is written through `api.deals.setStatus` (POST
 *    /deals/{id}/status) — never a PATCH — and the page refreshes.
 *  - The worker's refusal sentence is shown verbatim.
 *  - An archived deal offers only "Restore to In Progress"; a draft has no
 *    menu at all.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import React from 'react';

const hoisted = vi.hoisted(() => ({
  setStatus: vi.fn(async (..._a: unknown[]) => ({ id: 'deal-1' })),
  update: vi.fn(async (..._a: unknown[]) => ({ id: 'deal-1' })),
  toast: vi.fn(),
}));

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      deals: { ...actual.api.deals, setStatus: hoisted.setStatus, update: hoisted.update },
    },
  };
});
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: hoisted.toast }) }));

import DealStatusMenu from '@/components/project/DealStatusMenu';
import { WorkerError } from '@/lib/api';
import { hasCompletedModelRun, onboardingStateLabel, settableStatuses } from '@/lib/dealStatus';
import { DEAL_STATUSES, projectStatuses } from '@/lib/catalogs';

beforeEach(() => {
  hoisted.setStatus.mockClear();
  hoisted.update.mockClear();
  hoisted.toast.mockClear();
});
afterEach(cleanup);

function open(status: string) {
  fireEvent.click(screen.getByRole('button', { name: `Deal status: ${status}` }));
  return screen.getByRole('menu');
}

describe('DealStatusMenu', () => {
  it('offers exactly the five manual statuses, the current one checked', () => {
    render(<DealStatusMenu dealId="deal-1" status="In Progress" hasCompletedRun onChanged={vi.fn()} />);
    const menu = open('In Progress');
    const items = within(menu).getAllByRole('menuitemradio');
    expect(items.map((i) => i.getAttribute('data-status'))).toEqual([
      'In Progress', 'Ready for Review', 'IC Ready', 'IC Approved', 'IC Not Approved',
    ]);
    expect(items[0]).toHaveAttribute('aria-checked', 'true');
    expect(within(menu).queryByText('Draft')).toBeNull();
    expect(within(menu).queryByText('Archived')).toBeNull();
  });

  it('disables the three IC statuses until the model has run, and says why', () => {
    render(<DealStatusMenu dealId="deal-1" status="In Progress" hasCompletedRun={false} onChanged={vi.fn()} />);
    const menu = open('In Progress');
    for (const s of ['IC Ready', 'IC Approved', 'IC Not Approved']) {
      const item = within(menu).getByRole('menuitemradio', { name: new RegExp(`^${s}`) });
      expect(item).toBeDisabled();
      expect(item).toHaveTextContent('Run the model first — needs a completed model run with no failed engine.');
    }
    expect(within(menu).getByRole('menuitemradio', { name: /^Ready for Review/ })).not.toBeDisabled();
  });

  it('writes a choice through POST /deals/{id}/status, never a PATCH, then refreshes', async () => {
    const onChanged = vi.fn();
    render(<DealStatusMenu dealId="deal-1" status="In Progress" hasCompletedRun onChanged={onChanged} />);
    fireEvent.click(within(open('In Progress')).getByRole('menuitemradio', { name: /^IC Approved/ }));
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    // The write carries the status the control was showing (stale-write guard).
    expect(hoisted.setStatus).toHaveBeenCalledWith('deal-1', 'IC Approved', 'In Progress');
    expect(hoisted.update).not.toHaveBeenCalled();
    expect(hoisted.toast).toHaveBeenCalledWith('Status set to IC Approved', { type: 'success' });
  });

  it("shows the worker's refusal sentence verbatim and does not refresh", async () => {
    hoisted.setStatus.mockRejectedValueOnce(new WorkerError('409', 409, JSON.stringify({
      detail: { code: 'model_run_required', message: 'Run the model first — IC Ready needs a completed model run.' },
    })));
    const onChanged = vi.fn();
    render(<DealStatusMenu dealId="deal-1" status="Ready for Review" hasCompletedRun onChanged={onChanged} />);
    fireEvent.click(within(open('Ready for Review')).getByRole('menuitemradio', { name: /^IC Ready/ }));
    await waitFor(() => expect(hoisted.toast).toHaveBeenCalled());
    expect(hoisted.toast).toHaveBeenCalledWith(
      "Couldn't change the status — Run the model first — IC Ready needs a completed model run.",
      { type: 'error' },
    );
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('choosing the current status does nothing', () => {
    render(<DealStatusMenu dealId="deal-1" status="IC Ready" hasCompletedRun onChanged={vi.fn()} />);
    fireEvent.click(within(open('IC Ready')).getByRole('menuitemradio', { name: /^IC Ready/ }));
    expect(hoisted.setStatus).not.toHaveBeenCalled();
  });

  it('an archived deal offers only Restore to In Progress', async () => {
    const onChanged = vi.fn();
    render(<DealStatusMenu dealId="deal-1" status="Archived" hasCompletedRun onChanged={onChanged} />);
    const items = within(open('Archived')).getAllByRole('menuitemradio');
    expect(items).toHaveLength(1);
    expect(items[0]).toHaveTextContent('Restore to In Progress');
    fireEvent.click(items[0]);
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(hoisted.setStatus).toHaveBeenCalledWith('deal-1', 'In Progress', 'Archived');
  });

  it('a draft shows its badge with no menu', () => {
    render(<DealStatusMenu dealId="deal-1" status="Draft" hasCompletedRun={false} onChanged={vi.fn()} />);
    expect(screen.getByText('Draft')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Deal status/ })).toBeNull();
  });
});

describe('deal status vocabulary (web)', () => {
  it('is exactly the seven statuses, and the filter list is All + the seven', () => {
    expect(DEAL_STATUSES).toEqual([
      'Draft', 'In Progress', 'Ready for Review', 'IC Ready', 'IC Approved', 'IC Not Approved', 'Archived',
    ]);
    expect(projectStatuses).toEqual(['All Status', ...DEAL_STATUSES]);
  });

  it('the Pipeline state column never borrows a status word (review M3)', () => {
    expect(onboardingStateLabel('ONBOARDING')).toBe('Onboarding');
    expect(onboardingStateLabel('VALIDATING')).toBe('Validating');
    expect(onboardingStateLabel('READY')).toBe('Ready');
    for (const s of ['ONBOARDING', 'VALIDATING', 'READY']) {
      expect(DEAL_STATUSES as readonly string[]).not.toContain(onboardingStateLabel(s));
    }
  });

  it('settableStatuses follows the lifecycle', () => {
    expect(settableStatuses('Draft')).toEqual([]);
    expect(settableStatuses(null)).toEqual([]);
    expect(settableStatuses('Archived')).toEqual(['In Progress']);
    expect(settableStatuses('IC Approved')).toHaveLength(5);
  });

  it('hasCompletedModelRun: a finished run with no failed engine — never an empty, in-flight or failed one', () => {
    const env = (statuses: string[]) => ({
      deal_id: 'd',
      engines: Object.fromEntries(statuses.map((s, i) => [`e${i}`, { status: s }])),
    }) as unknown as Parameters<typeof hasCompletedModelRun>[0];
    expect(hasCompletedModelRun(null)).toBe(false);
    expect(hasCompletedModelRun(env([]))).toBe(false);
    expect(hasCompletedModelRun(env(['complete', 'running']))).toBe(false);
    expect(hasCompletedModelRun(env(['failed']))).toBe(false);
    expect(hasCompletedModelRun(env(['complete', 'failed', 'not_applicable']))).toBe(false);
    expect(hasCompletedModelRun(env(['complete', 'not_applicable']))).toBe(true);
  });
});
