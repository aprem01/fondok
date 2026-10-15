/**
 * EngineHeader — "Last run" is the persisted completion time, never a
 * placeholder. The old header invented "3 mins ago" whenever a tab was seeded
 * complete, on every page load, forever.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';

vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => ({ status: 'idle', run: vi.fn(), complete: false, summary: '' }) }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/api', () => ({ isWorkerConnected: () => true, workerUrl: () => 'http://worker.test', api: {} }));

import EngineHeader, { latestCompletedAt } from '@/components/project/EngineHeader';

afterEach(cleanup);

const base = { name: 'Financial Statements', desc: 'Models revenue and expenses.', outputs: ['NOI'], dependsOn: null as string | null };

describe('EngineHeader last-run label', () => {
  it('shows no "Last run" when the tab is seeded complete but no dated row exists', () => {
    render(<EngineHeader {...base} complete />);
    expect(screen.queryByText(/Last run/)).toBeNull();
  });

  it('reads the latest completed_at from the persisted rows', () => {
    const tenMinAgo = new Date(Date.now() - 10 * 60_000).toISOString();
    const rows = [
      { engine: 'revenue', status: 'complete', completed_at: new Date(Date.now() - 40 * 60_000).toISOString() },
      { engine: 'expense', status: 'complete', completed_at: tenMinAgo },
    ] as never[];
    render(<EngineHeader {...base} complete lastRunRows={rows} />);
    expect(screen.getByText(/Last run 10 mins ago/)).toBeInTheDocument();
  });

  it('latestCompletedAt ignores undated and malformed rows', () => {
    expect(latestCompletedAt(null)).toBeNull();
    expect(latestCompletedAt([{ completed_at: null }, { completed_at: 'not a date' }] as never[])).toBeNull();
    expect(latestCompletedAt([{ completed_at: '2026-10-15T11:52:34Z' }] as never[])?.toISOString()).toBe('2026-10-15T11:52:34.000Z');
  });
});
