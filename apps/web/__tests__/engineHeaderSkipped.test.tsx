/**
 * EngineHeader — a skipped engine says so (Sam, QA of deal b67bfdee).
 *
 * The deal carries no partnership terms, so every run writes a
 * `not_applicable` row for Partnership with the worker's own reason. The
 * Partnership card still read "Engine · Ready to run" and offered "Run" —
 * as if the engine had never been considered. The card now reads the
 * persisted row (`engineRow`, from `useEngineOutputs().outputs.engines
 * .partnership`) and the run hook's in-session verdict:
 *
 *   - eyebrow: "Engine · Skipped · no partnership terms on this deal" — the
 *     worker's `not_applicable.message` verbatim; "Skipped" alone when an
 *     older worker sent no sentence;
 *   - the button reads "Run anyway" (the worker re-checks the terms and
 *     writes another not_applicable row — nothing new for this engine);
 *   - a complete or never-run engine is unchanged.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import type { EngineOutputResponse } from '@/lib/api';

const hook = vi.hoisted(() => ({
  state: {
    status: 'idle' as string,
    output: null as unknown,
    run: vi.fn(),
    complete: false,
    summary: '',
    error: null as string | null,
    activeRunId: null as string | null,
  },
}));

vi.mock('@/lib/hooks/useEngineRun', () => ({ useEngineRun: () => hook.state }));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/lib/api', () => ({ isWorkerConnected: () => true, workerUrl: () => 'http://worker.test', api: {} }));

import EngineHeader from '@/components/project/EngineHeader';

const DEAL = 'b67bfdee-0000-4000-8000-000000000000';
const base = {
  name: 'Partnership Engine',
  desc: 'Models GP/LP waterfall structures.',
  outputs: ['GP IRR'],
  dependsOn: 'Returns',
  dealId: DEAL,
  engineName: 'partnership' as const,
  runMode: 'all' as const,
};

function skippedRow(message: string | null): EngineOutputResponse {
  return {
    deal_id: DEAL,
    engine: 'partnership',
    status: 'not_applicable',
    summary: '',
    outputs: null,
    inputs: null,
    error: null,
    failure: null,
    not_applicable:
      message == null
        ? null
        : { engine: 'partnership', reason: 'not_applicable', message, missing: ['split', 'pref_rate', 'tiers'], incomplete: [] },
    runtime_ms: 0,
    started_at: '2026-11-27T10:00:00Z',
    completed_at: '2026-11-27T10:00:00Z',
    run_id: 'run-1',
  };
}

afterEach(() => {
  cleanup();
  hook.state.status = 'idle';
  hook.state.output = null;
  hook.state.complete = false;
  hook.state.summary = '';
});

describe('EngineHeader — the persisted not_applicable row', () => {
  it('reads "Skipped · <worker reason>" and offers "Run anyway", not "Ready to run"', () => {
    render(<EngineHeader {...base} engineRow={skippedRow('no partnership terms on this deal')} />);

    const state = screen.getByTestId('engine-card-state');
    expect(state.textContent?.replace(/\s+/g, ' ').trim()).toBe(
      'Engine · Skipped · no partnership terms on this deal',
    );
    expect(screen.queryByText(/Ready to run/)).toBeNull();
    expect(screen.queryByText(/Complete/)).toBeNull();

    // Muted, and the worker's sentence in its own case (the eyebrow is
    // uppercase; the reason must not be shouted).
    const reason = screen.getByText('Skipped · no partnership terms on this deal');
    expect(reason.className).toContain('normal-case');
    expect(reason.className).toContain('text-ink-400');

    const button = screen.getByRole('button', { name: /Run anyway/ });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute('title', 'no partnership terms on this deal');
    expect(screen.queryByRole('button', { name: /^Run$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /Re-run/ })).toBeNull();
  });

  it('falls back to "Skipped" alone when the worker sent no sentence', () => {
    render(<EngineHeader {...base} engineRow={skippedRow(null)} />);
    expect(screen.getByTestId('engine-card-state').textContent?.replace(/\s+/g, ' ').trim()).toBe(
      'Engine · Skipped',
    );
    expect(screen.getByRole('button', { name: /Run anyway/ })).not.toHaveAttribute('title');
  });

  it('wins over a seeded complete flag — a skipped engine is not "Complete"', () => {
    render(<EngineHeader {...base} complete engineRow={skippedRow('no partnership terms on this deal')} />);
    expect(screen.getByTestId('engine-card-state').textContent).toContain('Skipped · no partnership terms on this deal');
    expect(screen.queryByText(/Model complete/)).toBeNull();
  });
});

describe('EngineHeader — the in-session verdict from the run hook', () => {
  it('reads the not_applicable row the run this session just produced', () => {
    hook.state.status = 'not_applicable';
    hook.state.output = skippedRow('no partnership terms on this deal');
    render(<EngineHeader {...base} />);
    expect(screen.getByTestId('engine-card-state').textContent?.replace(/\s+/g, ' ').trim()).toBe(
      'Engine · Skipped · no partnership terms on this deal',
    );
    expect(screen.getByRole('button', { name: /Run anyway/ })).toBeInTheDocument();
  });

  it('a run that completes this session clears the persisted skipped state', () => {
    hook.state.status = 'complete';
    hook.state.complete = true;
    hook.state.summary = 'GP IRR 24.1%';
    render(<EngineHeader {...base} engineRow={skippedRow('no partnership terms on this deal')} />);
    expect(screen.getByTestId('engine-card-state').textContent).toContain('Complete');
    expect(screen.queryByText(/Skipped/)).toBeNull();
    expect(screen.getByRole('button', { name: /Re-run/ })).toBeInTheDocument();
  });
});

describe('EngineHeader — unchanged states', () => {
  it('a never-run engine still reads "Ready to run" with a "Run" button', () => {
    render(<EngineHeader {...base} engineRow={null} />);
    expect(screen.getByTestId('engine-card-state').textContent).toContain('Ready to run');
    expect(screen.getByRole('button', { name: /^Run$/ })).toBeInTheDocument();
  });

  it('a persisted complete row does not read as skipped', () => {
    render(<EngineHeader {...base} complete engineRow={{ ...skippedRow(null), status: 'complete' }} />);
    expect(screen.getByTestId('engine-card-state').textContent).toContain('Complete');
    expect(screen.queryByText(/Skipped/)).toBeNull();
  });
});
