/**
 * Wave 5 — the deal header's Collaborators popover lists the workspace's real
 * members (the Clerk organization roster — every member of the tenant can open
 * its deals), never placeholder people. It used to hard-code "Sarah Chen /
 * Mike Johnson / Alex Wong" on every deal.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import React from 'react';
import type { OrgMembersState } from '@/lib/auth';

const hoisted = vi.hoisted(() => ({
  state: { members: [], loading: false, demo: false } as OrgMembersState,
}));
vi.mock('@/lib/auth', () => ({ useOrgMembers: () => hoisted.state }));

import DealCollaborators from '@/components/project/DealCollaborators';

const FABRICATED = ['Sarah Chen', 'Mike Johnson', 'Alex Wong'];

beforeEach(() => {
  hoisted.state = { members: [], loading: false, demo: false };
});
afterEach(cleanup);

describe('DealCollaborators', () => {
  it('lists the organization’s real members with their role', () => {
    hoisted.state = {
      loading: false,
      demo: false,
      members: [
        { id: 'm1', name: 'Prem K', email: 'p@x.co', initials: 'PK', role: 'org:admin', pending: false },
        { id: 'm2', name: 'Sam Analyst', email: 's@x.co', initials: 'SA', role: 'org:member', pending: false },
      ],
    };
    const { container } = render(<DealCollaborators />);
    expect(screen.getByText('Prem K')).toBeInTheDocument();
    expect(screen.getByText('Admin')).toBeInTheDocument();
    expect(screen.getByText('Sam Analyst')).toBeInTheDocument();
    expect(screen.getByText('Member')).toBeInTheDocument();
    for (const n of FABRICATED) expect(container.textContent).not.toContain(n);
  });

  it('with no organization roster it says so — no placeholder people', () => {
    hoisted.state = { members: [], loading: false, demo: true };
    const { container } = render(<DealCollaborators />);
    expect(screen.getByTestId('collaborators-empty')).toBeInTheDocument();
    for (const n of FABRICATED) expect(container.textContent).not.toContain(n);
  });

  it('an empty organization says it has no members yet', () => {
    const { container } = render(<DealCollaborators />);
    expect(screen.getByTestId('collaborators-empty')).toHaveTextContent(/no members/i);
    for (const n of FABRICATED) expect(container.textContent).not.toContain(n);
  });

  it('while the roster loads it says so', () => {
    hoisted.state = { members: [], loading: true, demo: false };
    render(<DealCollaborators />);
    expect(screen.getByTestId('collaborators-loading')).toBeInTheDocument();
  });
});
