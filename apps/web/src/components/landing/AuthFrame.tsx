import Link from 'next/link';
import type { ReactNode } from 'react';
import { body, display } from './fonts';

// The signed-out frame for /sign-in and /sign-up: same look as the home page
// (white-green ground, evergreen accent), no app chrome.
export const clerkAppearance = {
  layout: { unsafe_disableDevelopmentModeWarnings: true },
  variables: { colorPrimary: '#0b5d45', colorText: '#0d1f1a', borderRadius: '8px' },
  elements: {
    rootBox: 'w-full',
    cardBox: 'w-full shadow-none',
    card: 'shadow-none bg-transparent border-0',
    headerTitle: 'text-base font-semibold',
    headerSubtitle: 'text-xs',
    formButtonPrimary: 'bg-[#0b5d45] hover:bg-[#094d3a] text-white rounded-md font-medium',
    footerActionLink: 'text-[#0b5d45] hover:text-[#094d3a] font-medium',
  },
};

export default function AuthFrame({ subtitle, children }: { subtitle: string; children: ReactNode }) {
  return (
    <div
      className={`home-page ${display.variable} ${body.variable} relative flex min-h-[100svh] flex-col overflow-hidden bg-[#f4f7f5] text-[#0d1f1a] antialiased`}
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute right-[-15%] top-[-10%] h-[90%] w-[65%] rounded-full opacity-70 blur-3xl"
        style={{ background: 'radial-gradient(closest-side, rgba(31,163,106,0.16), transparent)' }}
      />
      <header className="relative z-10">
        <div className="mx-auto flex h-16 max-w-[1240px] items-center px-4 sm:px-6 lg:px-10">
          <Link href="/" aria-label="Fondok home" className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[#0b5d45]">
              <span className="home-display -mt-px text-[17px] font-semibold text-white">F</span>
            </span>
            <span className="text-[15px] font-semibold tracking-[-0.01em]">
              Fondok<span className="text-[#1fa36a]">.</span>
            </span>
          </Link>
        </div>
      </header>
      <main className="relative z-10 flex flex-1 items-center justify-center px-4 py-8">
        <div className="w-full max-w-[420px]">
          <div className="mb-6 text-center">
            <h1 className="home-display text-[30px] font-medium tracking-[-0.02em]">{subtitle}</h1>
          </div>
          <div className="rounded-2xl border border-[#dfe7e3] bg-white p-1 shadow-[0_30px_60px_-30px_rgba(13,31,26,0.35)]">
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}
