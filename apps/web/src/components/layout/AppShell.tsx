'use client';
import { useEffect, useState } from 'react';
import { Menu } from 'lucide-react';
import Sidebar from './Sidebar';
import FondokMark from '@/components/brand/FondokMark';
import { api, isWorkerConnected, workerUrl } from '@/lib/api';
import { cn } from '@/lib/format';
import { ToastProvider } from '@/components/ui/Toast';

type WorkerHealth = 'unknown' | 'green' | 'red' | 'offline';

export default function AppShell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false);
  const [health, setHealth] = useState<WorkerHealth>('unknown');

  // Lightweight health probe — runs on mount, then every 30s.
  useEffect(() => {
    if (!isWorkerConnected()) {
      setHealth('offline');
      return;
    }
    let cancelled = false;
    const probe = async () => {
      try {
        await api.health();
        if (!cancelled) setHealth('green');
      } catch {
        if (!cancelled) setHealth('red');
      }
    };
    probe();
    const t = setInterval(probe, 30_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  const buildSha = process.env.NEXT_PUBLIC_BUILD_SHA?.slice(0, 7) ?? 'dev';
  const env = process.env.NEXT_PUBLIC_VERCEL_ENV ?? process.env.NODE_ENV ?? 'development';

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

        {/* Observability footer */}
        <footer
          aria-label="Build and worker status"
          className="border-t hairline bg-white px-4 md:px-8 py-2 flex items-center justify-between text-[10.5px] text-ink-700"
        >
          <div className="flex items-center gap-3">
            <span className="font-medium text-ink-900">Fondok</span>
            <span className="tabular-nums">build {buildSha}</span>
            <span className="uppercase tracking-wide">{env}</span>
          </div>
          <div className="flex items-center gap-2" title={workerUrl() || 'no worker URL configured'}>
            <span
              role="img"
              aria-label={`worker status: ${health}`}
              className={cn(
                'w-2 h-2 rounded-full',
                health === 'green' && 'bg-success-500',
                health === 'red' && 'bg-danger-500',
                health === 'offline' && 'bg-ink-300',
                health === 'unknown' && 'bg-warn-500 animate-pulse',
              )}
            />
            <span>
              worker {health === 'green' ? 'online'
                : health === 'red' ? 'unreachable'
                : health === 'offline' ? 'not configured'
                : 'checking…'}
            </span>
          </div>
        </footer>
      </main>
    </div>
    </ToastProvider>
  );
}
