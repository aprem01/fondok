'use client';
import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import {
  LayoutGrid, FolderKanban, Database, Settings, ChevronDown, Building2,
  Users, UserCog, LogOut, Plus, Check, BookOpen, LineChart, History, Library, Bell,
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { OrganizationSwitcher } from '@clerk/nextjs';
import { cn } from '@/lib/format';
import FondokMark from '@/components/brand/FondokMark';
import { useToast } from '@/components/ui/Toast';
import {
  isClerkConfigured,
  setCurrentOrgId,
  useCurrentOrg,
  useCurrentRole,
  useCurrentUser,
  useSignOut,
} from '@/lib/auth';

const navItems = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutGrid },
  { href: '/projects', label: 'Projects', icon: FolderKanban },
  // Wave 3 W3.5 — multi-deal Pipeline view. Sortable + filterable
  // analyst table with portfolio KPIs (median IRR, p25/p75, deals
  // meeting target). Sits between Projects (single-deal drill-down)
  // and Data Library (global benchmarks).
  { href: '/pipeline', label: 'Pipeline', icon: LineChart },
  // Wave 4 W4.3 — tenant-wide Compliance Explorer over the append-only
  // audit log. Sits between Pipeline (multi-deal) and Data Library
  // (global benchmarks) since both are tenant-scoped surfaces.
  { href: '/audit', label: 'Audit', icon: History },
  { href: '/data-library', label: 'Data Library', icon: Database },
  { href: '/methodology', label: 'Methodology', icon: BookOpen },
  // Wave 4 W4.5 — recurring Slack/email pipeline summaries. Grouped
  // under Settings in concept (it's a tenant-level config surface)
  // but mounted as a top-level nav item so executives who only ever
  // touch digests don't have to dive through Settings to find them.
  { href: '/pipeline-digests', label: 'Digests', icon: Bell },
  { href: '/settings', label: 'Settings', icon: Settings },
];

export default function Sidebar({
  mobileOpen = false,
  onCloseMobile,
}: {
  mobileOpen?: boolean;
  onCloseMobile?: () => void;
} = {}) {
  const pathname = usePathname();
  const router = useRouter();
  const { toast } = useToast();
  const [wsOpen, setWsOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const wsRef = useRef<HTMLDivElement>(null);
  const userRef = useRef<HTMLDivElement>(null);

  // Auth-aware user + org. In demo mode these resolve to the static
  // Brookfield Real Estate / Eshan Mehta persona (mockData.ts). When
  // Clerk is configured they hydrate from the active session/org.
  const currentUser = useCurrentUser();
  const workspace = useCurrentOrg();
  const currentRole = useCurrentRole();
  const signOut = useSignOut();

  // Wave 5 RBAC — human-facing badge next to the analyst's name. Demo
  // persona resolves to ``org:admin`` (see ``useCurrentRole`` note).
  // ``unknown`` maps to no badge — safer than surfacing "Member" for a
  // pre-hydrated Clerk state that hasn't loaded ``membership`` yet.
  const roleBadge =
    currentRole === 'org:admin' ? 'Admin' :
    currentRole === 'org:member' ? 'Member' :
    null;

  // Mirror the active org id into the api.ts singleton so X-Tenant-Id
  // is attached to outbound worker requests on every render where the
  // org changes (workspace switcher, etc.).
  useEffect(() => {
    setCurrentOrgId(workspace.id);
  }, [workspace.id]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (wsRef.current && !wsRef.current.contains(e.target as Node)) setWsOpen(false);
      if (userRef.current && !userRef.current.contains(e.target as Node)) setUserOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  const handleSignOut = async () => {
    setUserOpen(false);
    if (!isClerkConfigured) {
      // No auth backend configured — silently no-op.
      return;
    }
    await signOut();
  };

  return (
    <>
    {mobileOpen && (
      <div
        className="md:hidden fixed inset-0 bg-black/30 z-30"
        onClick={onCloseMobile}
        aria-hidden="true"
      />
    )}
    <aside
      className={cn(
        'fixed left-0 top-0 h-screen w-[216px] bg-white border-r hairline flex flex-col z-40 transition-transform',
        'md:translate-x-0',
        mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
      )}
    >
      {/* Logo */}
      <div className="px-4 pt-5 pb-3">
        <Link
          href="/dashboard"
          className="inline-flex focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded-md"
          aria-label="Fondok — go to dashboard"
        >
          <FondokMark size="md" />
        </Link>
      </div>

      {/* Workspace switcher — Clerk OrganizationSwitcher when configured,
          otherwise the static Brookfield Real Estate pill from mockData.
          The typeof guard defends against a Clerk bundle that loaded but
          where the component export came back undefined (dev-key against
          a prod origin can produce this) — without it, React throws #130
          and the whole app tree crashes. */}
      {isClerkConfigured && typeof OrganizationSwitcher === 'function' ? (
        <div className="px-3 pb-2">
          <div className="px-2 py-1">
            <OrganizationSwitcher
              hidePersonal={false}
              afterCreateOrganizationUrl="/dashboard"
              afterSelectOrganizationUrl="/dashboard"
              appearance={{
                elements: {
                  rootBox: 'w-full',
                  organizationSwitcherTrigger:
                    'w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-ink-300/20 transition-colors text-left',
                  organizationPreviewMainIdentifier:
                    'text-[12.5px] font-semibold text-ink-900 truncate',
                  organizationPreviewSecondaryIdentifier:
                    'text-[11px] text-ink-500',
                  organizationSwitcherTriggerIcon: 'text-ink-400 ml-auto',
                  organizationSwitcherPopoverCard:
                    'border border-border rounded-lg shadow-lg',
                },
              }}
            />
          </div>
        </div>
      ) : (
        <div className="px-3 pb-2 relative" ref={wsRef}>
          <button
            onClick={() => setWsOpen(!wsOpen)}
            className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-ink-300/20 transition-colors"
          >
            <div className="w-8 h-8 rounded-md bg-ink-300/30 flex items-center justify-center flex-shrink-0">
              <Building2 size={15} className="text-ink-700" />
            </div>
            <div className="flex-1 min-w-0 text-left">
              <div className="text-[12.5px] font-semibold text-ink-900 truncate">{workspace.name}</div>
              <div className="text-[11px] text-ink-500">{workspace.plan}</div>
            </div>
            <ChevronDown size={13} className="text-ink-400" />
          </button>
          {wsOpen && (
            <div className="absolute left-3 right-3 top-full mt-1 bg-white border border-border rounded-lg shadow-lg py-1 z-40">
              <div className="px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-ink-300/10">
                <Check size={13} className="text-brand-500" />
                <span className="font-medium">{workspace.name}</span>
              </div>
              <div className="border-t border-border my-1" />
              {/* Workspace creation is gated behind Clerk Organizations.
                  In demo mode (no Clerk) we surface an honest toast rather
                  than mounting a fake create-org dialog. */}
              <button
                type="button"
                onClick={() => {
                  setWsOpen(false);
                  toast(
                    'Workspace creation runs through Clerk Organizations — available on team plans',
                    { type: 'info' },
                  );
                }}
                className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-ink-300/10 text-left"
              >
                <Plus size={13} className="text-ink-500" />
                Create Workspace
              </button>
            </div>
          )}
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 px-3 pt-2" data-tour="sidebar">
        {navItems.map(it => {
          const isActive = pathname === it.href || (it.href !== '/dashboard' && pathname.startsWith(it.href));
          const Icon = it.icon;
          // Surface tour anchors on the two nav rows the GettingStarted
          // sidebar points its "Show me" pulse at (replaces AppTour).
          const tourAttr =
            it.href === '/methodology' ? 'methodology' :
            it.href === '/settings' ? 'settings' : undefined;
          return (
            <div key={it.href}>
              <Link href={it.href}
                data-tour={tourAttr}
                className={cn(
                  'flex items-center gap-3 px-3 py-2 rounded-md text-[13px] mb-0.5 transition-colors',
                  isActive
                    ? 'bg-brand-50 text-brand-700 font-semibold'
                    : 'text-ink-700 hover:bg-ink-100'
                )}>
                <Icon size={16} className={isActive ? 'text-brand-500' : 'text-ink-500'} />
                {it.label}
              </Link>
              {/* Wave 4 W4.1 — surface the Portfolio Library sub-link
                  when the analyst is on /settings/*. Keeps the top-level
                  nav uncluttered while making the new surface one click
                  from anywhere inside Settings. */}
              {it.href === '/settings' && pathname.startsWith('/settings') && (
                <div className="ml-5 mb-1 border-l border-border pl-3 space-y-0.5">
                  <Link
                    href="/settings/portfolio-library"
                    className={cn(
                      'flex items-center gap-2 px-2.5 py-1.5 rounded-md text-[12.5px] transition-colors',
                      pathname.startsWith('/settings/portfolio-library')
                        ? 'bg-brand-50 text-brand-700 font-medium'
                        : 'text-ink-700 hover:bg-ink-100'
                    )}
                  >
                    <Library
                      size={13}
                      className={
                        pathname.startsWith('/settings/portfolio-library')
                          ? 'text-brand-500'
                          : 'text-ink-500'
                      }
                    />
                    Portfolio Library
                  </Link>
                </div>
              )}
            </div>
          );
        })}
      </nav>

      {/* User menu */}
      <div className="px-3 pb-4 relative" ref={userRef}>
        <button
          onClick={() => setUserOpen(!userOpen)}
          className="w-full flex items-center gap-2 px-2 py-2 rounded-md hover:bg-ink-300/20 transition-colors"
        >
          <div className="w-8 h-8 rounded-full bg-ink-300/40 flex items-center justify-center text-[11px] font-semibold text-ink-700 flex-shrink-0">
            {currentUser.initials}
          </div>
          <div className="flex-1 min-w-0 text-left">
            <div className="flex items-center gap-1.5">
              <span className="text-[12.5px] font-semibold text-ink-900 truncate">{currentUser.name}</span>
              {roleBadge && (
                <span
                  className={cn(
                    'text-[9.5px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded',
                    roleBadge === 'Admin'
                      ? 'bg-brand-50 text-brand-700'
                      : 'bg-ink-300/30 text-ink-700',
                  )}
                  aria-label={`Role: ${roleBadge}`}
                  title={`Role: ${roleBadge}`}
                >
                  {roleBadge}
                </span>
              )}
            </div>
            {/* Sam QA 2026-07-02: hide the legacy publicMetadata.role
                text ("Analyst" by default) — it disagrees with the
                Clerk-role pill above (which reads
                useOrganization().membership.role). Two role sources
                gave contradictory signals in the same sidebar; keep
                the authoritative Clerk pill, drop the stale text. */}
            <div className="text-[11px] text-ink-500">{currentUser.email}</div>
          </div>
          <ChevronDown size={13} className="text-ink-400" />
        </button>
        {userOpen && (
          <div className="absolute left-3 right-3 bottom-full mb-1 bg-white border border-border rounded-lg shadow-lg py-1 z-40">
            {/* Team Members deep-links to the Settings → Team tab. */}
            <button
              type="button"
              onClick={() => {
                setUserOpen(false);
                router.push('/settings');
              }}
              className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-ink-300/10 text-left"
            >
              <Users size={13} className="text-ink-500" /> Team Members
            </button>
            {/* Account Settings → workspace tab; Clerk's account profile
                is reachable via the user button when Clerk is configured. */}
            <button
              type="button"
              onClick={() => {
                setUserOpen(false);
                router.push('/settings');
              }}
              className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-ink-300/10 text-left"
            >
              <UserCog size={13} className="text-ink-500" /> Account Settings
            </button>
            <div className="border-t border-border my-1" />
            <button
              onClick={handleSignOut}
              className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-danger-50 text-danger-700 text-left"
            >
              <LogOut size={13} /> {isClerkConfigured ? 'Sign Out' : 'Sign Out (demo · no auth backend)'}
            </button>
          </div>
        )}
      </div>
    </aside>
    </>
  );
}
