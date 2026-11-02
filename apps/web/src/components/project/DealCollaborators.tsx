'use client';
/**
 * The deal header's Collaborators list — the workspace's real members.
 *
 * Deals are tenant-scoped: every member of the Clerk organization can open
 * every deal in it, so the deal's collaborators ARE the organization roster
 * (``useOrgMembers``, the same source Settings → Team Members reads). Wave 5:
 * this replaced three hard-coded placeholder people ("Sarah Chen / Mike
 * Johnson / Alex Wong") shown on every deal. With no roster (no Clerk
 * organization) it says so — it never invents a team.
 */
import { useOrgMembers } from '@/lib/auth';

function roleLabel(role: string): string {
  if (role === 'org:admin') return 'Admin';
  if (role === 'org:member') return 'Member';
  return '—';
}

export default function DealCollaborators() {
  const { members, loading, demo, personal, total } = useOrgMembers();

  if (loading) {
    return (
      <div className="text-[11.5px] text-ink-500" data-testid="collaborators-loading">
        Loading workspace members…
      </div>
    );
  }
  if (members.length === 0) {
    return (
      <div className="text-[11.5px] text-ink-500 leading-relaxed" data-testid="collaborators-empty">
        {personal
          ? 'Personal workspace — only you can open this deal.'
          : demo
            ? 'No workspace roster — sign in to an organization to see who can open this deal.'
            : 'This workspace has no members yet.'}
      </div>
    );
  }
  return (
    <div className="space-y-2" data-testid="collaborators-list">
      {members.map((m) => (
        <div key={m.id} className="flex items-center gap-2.5">
          <div className="w-7 h-7 rounded-full bg-ink-300/30 flex items-center justify-center text-[10.5px] font-semibold text-ink-700">
            {m.initials}
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-medium text-ink-900 truncate">{m.name}</div>
            <div className="text-[10.5px] text-ink-500">{roleLabel(m.role)}</div>
          </div>
        </div>
      ))}
      {total != null && total > members.length && (
        <div className="text-[11px] text-ink-500" data-testid="collaborators-more">
          +{total - members.length} more — see Settings → Team Members
        </div>
      )}
    </div>
  );
}
