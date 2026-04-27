'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  LayoutGrid, FolderKanban, Database, Settings, ChevronDown, Building2,
  Sparkles, Users, UserCog, LogOut, Plus, Check,
} from 'lucide-react';
import { useState, useRef, useEffect } from 'react';
import { workspace, currentUser } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { isWorkerConnected } from '@/lib/api';

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

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (wsRef.current && !wsRef.current.contains(e.target as Node)) setWsOpen(false);
      if (userRef.current && !userRef.current.contains(e.target as Node)) setUserOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

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
        'fixed left-0 top-0 h-screen w-[216px] bg-white border-r border-border flex flex-col z-40 transition-transform',
        'md:translate-x-0',
        mobileOpen ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
      )}
    >
      {/* Logo */}
      <div className="px-4 pt-5 pb-3">
        <Link href="/dashboard" className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shadow-sm">
            <Sparkles size={16} className="text-white" />
          </div>
          <div className="font-semibold text-[15px] text-ink-900">Fondok AI</div>
        </Link>
      </div>

      {/* Workspace switcher */}
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
                  ? 'bg-brand-50 text-brand-700 font-medium'
                  : 'text-ink-700 hover:bg-ink-300/15'
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
            <button className="w-full px-3 py-2 flex items-center gap-2 text-[12.5px] hover:bg-danger-50 text-danger-700 text-left">
              <LogOut size={13} /> Sign Out
            </button>
          </div>
        )}
      </div>
    </aside>
    </>
  );
}
