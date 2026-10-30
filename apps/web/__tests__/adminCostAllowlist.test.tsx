/**
 * Go-live checklist — the Cost Dashboard gate reads its allowlist from
 * `NEXT_PUBLIC_ADMIN_COST_EMAILS` (comma-separated). It never hard-codes a
 * person, and an unset / empty list means NOBODY gets in.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';

const hoisted = vi.hoisted(() => ({ role: 'org:admin', email: 'ops@fondok.test' }));

vi.mock('@/lib/auth', () => ({
  useCurrentRole: () => hoisted.role,
  useCurrentUser: () => ({ name: 'Ops', role: 'Admin', initials: 'OP', email: hoisted.email, id: 'u1' }),
}));
vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      admin: { cost: vi.fn(() => new Promise(() => {})) },
      deals: { ...actual.api.deals, list: vi.fn(async () => []) },
    },
  };
});

import AdminCostPage from '@/app/admin/cost/page';
import { adminCostEmails } from '@/lib/adminCostEmails';

const ENV = 'NEXT_PUBLIC_ADMIN_COST_EMAILS';
let saved: string | undefined;

beforeEach(() => {
  saved = process.env[ENV];
  delete process.env[ENV];
  hoisted.role = 'org:admin';
  hoisted.email = 'ops@fondok.test';
});
afterEach(() => {
  cleanup();
  if (saved === undefined) delete process.env[ENV];
  else process.env[ENV] = saved;
});

describe('adminCostEmails', () => {
  it('parses a comma-separated list, trimming and lower-casing', () => {
    expect(adminCostEmails(' Ops@Fondok.test, second@fondok.test ,')).toEqual(['ops@fondok.test', 'second@fondok.test']);
  });
  it('an unset or blank variable is an empty list — nobody', () => {
    expect(adminCostEmails(undefined)).toEqual([]);
    expect(adminCostEmails('  ,  ')).toEqual([]);
  });
});

describe('Cost Dashboard gate', () => {
  it('with no allowlist configured, even an org admin is refused and told why', () => {
    render(<AdminCostPage />);
    expect(screen.getByText('Access restricted')).toBeInTheDocument();
    expect(screen.getByText('No cost-dashboard emails are configured for this build.')).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('kpremks');
  });

  it('an admin whose email is on the list gets the dashboard', () => {
    process.env[ENV] = 'other@fondok.test, OPS@fondok.test';
    render(<AdminCostPage />);
    expect(screen.queryByText('Access restricted')).toBeNull();
    expect(screen.getByText('Model usage and cost for this workspace, by deal.')).toBeInTheDocument();
  });

  it('an admin whose email is NOT on the list is refused', () => {
    process.env[ENV] = 'other@fondok.test';
    render(<AdminCostPage />);
    expect(screen.getByText('Access restricted')).toBeInTheDocument();
    expect(screen.getByText('Cost data is available only to the configured admin emails.')).toBeInTheDocument();
  });

  it('a non-admin is refused before the allowlist is consulted', () => {
    process.env[ENV] = 'ops@fondok.test';
    hoisted.role = 'org:member';
    render(<AdminCostPage />);
    expect(screen.getByText('Admin only')).toBeInTheDocument();
  });
});
