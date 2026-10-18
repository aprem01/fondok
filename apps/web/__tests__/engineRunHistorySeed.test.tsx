/**
 * EngineRunHistory's `seedDemo` fixture ("Last run · 3 minutes ago · 8
 * engines") exists for the worker-less demo build only. With the service
 * connected it must never seed: every tester was seeing a fabricated footer
 * on a fresh page load (same defect class as FON-105).
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

const connected = { value: true };
vi.mock('@/lib/api', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api')>('@/lib/api');
  return { ...actual, isWorkerConnected: () => connected.value };
});

import EngineRunHistory from '@/components/project/EngineRunHistory';

describe('EngineRunHistory seedDemo', () => {
  beforeEach(() => { connected.value = true; });

  it('renders nothing with the service connected and no real run in this session', () => {
    const { container } = render(<EngineRunHistory dealId="deal-connected-no-runs" seedDemo />);
    expect(container.textContent).toBe('');
    expect(screen.queryByText(/Last run/)).toBeNull();
  });

  it('still seeds the demo footer in the worker-less build', () => {
    connected.value = false;
    render(<EngineRunHistory dealId="deal-demo-build" seedDemo />);
    expect(screen.getByText(/Last run/)).toBeTruthy();
    expect(screen.getByText(/Run #1/)).toBeTruthy();
  });
});
