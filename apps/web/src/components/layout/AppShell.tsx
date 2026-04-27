'use client';
import { useState } from 'react';
import { Menu, Sparkles } from 'lucide-react';
import Sidebar from './Sidebar';

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex min-h-screen">
      <Sidebar mobileOpen={mobileOpen} onCloseMobile={() => setMobileOpen(false)} />
      <main className="flex-1 md:ml-[216px] min-h-screen bg-bg">
        {/* Mobile top bar */}
        <div className="md:hidden sticky top-0 z-20 flex items-center gap-3 px-4 py-3 bg-white border-b border-border">
          <button
            aria-label="Open menu"
            onClick={() => setMobileOpen(true)}
            className="p-1.5 rounded-md hover:bg-ink-300/20 text-ink-700"
          >
            <Menu size={18} />
          </button>
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shadow-sm">
              <Sparkles size={13} className="text-white" />
            </div>
            <div className="font-semibold text-[14px] text-ink-900">Fondok AI</div>
          </div>
        </div>
        {children}
      </main>
    </div>
  );
}
