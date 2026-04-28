'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutGrid, FolderKanban, Database, Settings, ChevronDown, Building2,
  Users, UserCog, LogOut, Plus, Check,
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { OrganizationSwitcher } from '@clerk/nextjs';
import { cn } from '@/lib/format';
import { isWorkerConnected } from '@/lib/api';
import FondokMark from '@/components/brand/FondokMark';
import {
  isClerkConfigured,
  setCurrentOrgId,
  useCurrentOrg,
  useCurrentUser,
  useSignOut,
} from '@/lib/auth';

const navItems = [
  { href: '/dashboard', label: 'Dashboard', icon: LayoutGrid },
  { href: '/projects', label: 'Projects', icon: FolderKanban },
  { href: '/data-library', label: 'Data Library', icon: Database },
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
  const [wsOpen, setWsOpen] = useState(false);
  const [userOpen, setUserOpen] = useState(false);
  const wsRef = useRef<HTMLDivElement>(null);
  const userRef = useRef<HTMLDivElement>(null);

  // Auth-aware user + org. In demo mode these resolve to the static
  // Brookfield Real Estate / Eshan Mehta persona (mockData.ts). When
  // Clerk is configured they hydrate from the active session/org.
  const currentUser = useCurrentUser();
  const workspace = useCurrentOrg();
  const signOut = useSignOut();

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
      // Demo mode — no auth backend to sign out of. Surface a tiny
      // affordance so the user knows the click registered.
      if (typeof window !== 'undefined') {
        window.alert('Sign out (demo mode) — auth is not configured.');
      }
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
          otherwise the static Brookfield Real Estate pill from mockData. */}
      {isClerkConfigured ? (
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
              <button className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-ink-300/10 text-left">
                <Plus size={13} className="text-ink-500" />
                Create Workspace
              </button>
            </div>
          )}
        </div>
      )}

      {/* Nav */}
      <nav className="flex-1 px-3 pt-2">
        {navItems.map(it => {
          const isActive = pathname === it.href || (it.href !== '/dashboard' && pathname.startsWith(it.href));
          const Icon = it.icon;
          return (
            <Link key={it.href} href={it.href}
              className={cn(
                'flex items-center gap-3 px-3 py-2 rounded-md text-[13px] mb-0.5 transition-colors',
                isActive
                  ? 'bg-brand-50 text-brand-700 font-semibold'
                  : 'text-ink-700 hover:bg-ink-100'
              )}>
              <Icon size={16} className={isActive ? 'text-brand-500' : 'text-ink-500'} />
              {it.label}
            </Link>
          );
        })}
      </nav>

      {/* Data source indicator */}
      <div className="px-3 pt-2 pb-1">
        {isWorkerConnected() ? (
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-success-50 border border-success-500/20">
            <span className="w-1.5 h-1.5 rounded-full bg-success-500" />
            <span className="text-[10.5px] text-success-700 font-medium">Connected to live worker</span>
          </div>
        ) : (
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-warn-50 border border-warn-500/30">
            <span className="w-1.5 h-1.5 rounded-full bg-warn-500" />
            <span className="text-[10.5px] text-warn-700 font-medium">Offline · using sample data</span>
          </div>
        )}
      </div>

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
            <div className="text-[12.5px] font-semibold text-ink-900 truncate">{currentUser.name}</div>
            <div className="text-[11px] text-ink-500">{currentUser.role}</div>
          </div>
          <ChevronDown size={13} className="text-ink-400" />
        </button>
        {userOpen && (
          <div className="absolute left-3 right-3 bottom-full mb-1 bg-white border border-border rounded-lg shadow-lg py-1 z-40">
            <button className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-ink-300/10 text-left">
              <Users size={13} className="text-ink-500" /> Team Members
            </button>
            <button className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-ink-300/10 text-left">
              <UserCog size={13} className="text-ink-500" /> Account Settings
            </button>
            <div className="border-t border-border my-1" />
            <button
              onClick={handleSignOut}
              className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-danger-50 text-danger-700 text-left"
            >
              <LogOut size={13} /> {isClerkConfigured ? 'Sign Out' : 'Sign out (demo)'}
            </button>
          </div>
        )}
      </div>
    </aside>
    </>
  );
}
