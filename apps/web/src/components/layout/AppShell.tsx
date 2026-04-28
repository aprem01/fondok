'use client';
import { useState } from 'react';
import { Menu } from 'lucide-react';
import Sidebar from './Sidebar';
import FondokMark from '@/components/brand/FondokMark';
import { ToastProvider } from '@/components/ui/Toast';
import SourceDocPane from '@/components/citations/SourceDocPane';

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <ToastProvider>
    <div className="flex min-h-screen">
      <Sidebar mobileOpen={mobileOpen} onCloseMobile={() => setMobileOpen(false)} />
      <main role="main" className="flex-1 md:ml-[216px] min-h-screen bg-bg flex flex-col">
        {/* Mobile top bar */}
        <div className="md:hidden sticky top-0 z-20 flex items-center gap-3 px-4 py-3 bg-white border-b hairline">
          <button
            type="button"
            aria-label="Open menu"
            onClick={() => setMobileOpen(true)}
            className="p-1.5 rounded-md hover:bg-ink-100 text-ink-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          <FondokMark size="sm" />
        </div>
        <div className="flex-1">{children}</div>
      </main>
      {/* Globally mounted citation viewer — listens for fondok:citation-focus
          events from anywhere in the app and slides in from the right. */}
      <SourceDocPane />
    </div>
    </ToastProvider>
  );
}
