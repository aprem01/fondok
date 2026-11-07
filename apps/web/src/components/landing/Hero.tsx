'use client';

import Link from 'next/link';
import { useCallback, useRef, useState, type ReactNode } from 'react';
import { ArrowLeft, ArrowRight, Check, FileText, Presentation, Table2 } from 'lucide-react';
import { REQUEST_ACCESS_HREF } from './requestAccess';

// The public home page is ONE screen: no scrolling. "More detail" lives in
// slides inside that screen, not in sections below it. Say what Fondok does
// for an investor (benefit first), never how it works inside (engines, rules, screens and
// metrics stay behind sign-in). No customer names, pilot mentions, usage
// numbers, time-saved claims or certifications. Every figure in the visuals
// is an illustrative sample hotel, labelled so, and internally consistent:
// 132 keys, T-12 78% occ × $285 ADR ≈ $10.71M rooms revenue, NOI $4.1M;
// OM 82% × $295, NOI $4.6M (12.2% over); ask $58.0M.

type Slide = {
  id: string;
  tab: string;
  kicker: string;
  title: ReactNode;
  body: string;
  visual: ReactNode;
};

const SLIDES: Slide[] = [
  {
    id: 'overview',
    tab: 'Overview',
    kicker: 'Hotel acquisition underwriting',
    title: (
      <>
        Pay the right price for every hotel you <em className="italic text-[#0b5d45]">buy.</em>
      </>
    ),
    body: 'Fondok turns the OM, T-12s and STR reports into a sourced underwriting and an IC-ready package, so your team spends its time on judgment, not data entry.',
    visual: <OverviewVisual />,
  },
  {
    id: 'broker',
    tab: 'Avoid overpaying',
    kicker: 'Avoid overpaying',
    title: 'Catch the broker’s optimism before it becomes your basis.',
    body: 'See where the OM runs ahead of the trailing twelve months, line by line, and negotiate from the actuals instead of the pitch.',
    visual: <BrokerVisual />,
  },
  {
    id: 'sourced',
    tab: 'Defensible numbers',
    kicker: 'Defensible numbers',
    title: 'Walk into IC with numbers that hold up.',
    body: 'Every figure carries the document and page it came from. Your committee checks the work instead of questioning it.',
    visual: <SourcedVisual />,
  },
  {
    id: 'pricing',
    tab: 'Price discipline',
    kicker: 'Price discipline',
    title: 'Know your walk-away price before you bid.',
    body: 'See the price that clears your return targets next to the ask, so you bid with conviction and walk away without second-guessing.',
    visual: <PricingVisual />,
  },
  {
    id: 'ic',
    tab: 'IC-ready',
    kicker: 'IC-ready, without the rekeying',
    title: 'One underwriting. Memo, deck and model that agree.',
    body: 'The IC memo, deck and model all come from the same numbers, so nothing gets retyped and nothing contradicts itself in front of the committee.',
    visual: <OutputsVisual />,
  },
  {
    id: 'team',
    tab: 'Your team',
    kicker: 'More from the team you have',
    title: 'Look at more deals without adding headcount.',
    body: 'Analysts pressure-test deals instead of rebuilding spreadsheets, and every deal is underwritten the same disciplined way, whoever runs it.',
    visual: <TeamVisual />,
  },
];

const SLIDE_MS = 8000;

export default function Landing() {
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const touchX = useRef<number | null>(null);
  const slide = SLIDES[index];

  const go = useCallback((i: number) => setIndex((i + SLIDES.length) % SLIDES.length), []);
  const next = useCallback(() => go(index + 1), [go, index]);
  const prev = useCallback(() => go(index - 1), [go, index]);

  return (
    <div
      className="relative flex h-[100svh] min-h-[600px] flex-col overflow-hidden bg-[#f4f7f5] text-[#0d1f1a]"
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight') next();
        if (e.key === 'ArrowLeft') prev();
      }}
    >
      {/* Ambient ground: a soft evergreen wash on the right, behind the visual */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute right-[-15%] top-[-10%] h-[90%] w-[65%] rounded-full opacity-70 blur-3xl"
        style={{ background: 'radial-gradient(closest-side, rgba(31,163,106,0.16), transparent)' }}
      />

      <header className="relative z-10 border-b border-[#dfe7e3] bg-white/60 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-[1240px] items-center justify-between px-4 sm:px-6 lg:px-10">
          <Link href="/" aria-label="Fondok home" className="flex items-center gap-2.5">
            <span className="flex h-8 w-8 items-center justify-center rounded-[9px] bg-[#0b5d45]">
              <span className="home-display -mt-px text-[17px] font-semibold text-white">F</span>
            </span>
            <span className="text-[15px] font-semibold tracking-[-0.01em]">
              Fondok<span className="text-[#1fa36a]">.</span>
            </span>
          </Link>
          <div className="flex items-center gap-2">
            <Link
              href="/sign-in"
              className="whitespace-nowrap rounded-md px-3 py-2 text-[13.5px] font-medium text-[#3d4f49] transition-colors hover:text-[#0d1f1a]"
            >
              Sign in
            </Link>
            <a
              href={REQUEST_ACCESS_HREF}
              className="whitespace-nowrap rounded-md bg-[#0b5d45] px-3.5 py-2 text-[13.5px] font-semibold text-white transition-colors hover:bg-[#094d3a]"
            >
              Request access
            </a>
          </div>
        </div>
      </header>

      <h1 className="sr-only">Fondok: hotel acquisition underwriting, cited to the source page</h1>

      <main
        id="main"
        aria-roledescription="carousel"
        aria-label="What Fondok does"
        className="relative z-10 mx-auto grid w-full max-w-[1240px] flex-1 grid-cols-[minmax(0,1fr)] items-center gap-10 px-4 py-6 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-16 lg:px-10"
        onMouseEnter={() => setPaused(true)}
        onMouseLeave={() => setPaused(false)}
        onFocus={() => setPaused(true)}
        onBlur={() => setPaused(false)}
        onTouchStart={(e) => {
          touchX.current = e.touches[0]?.clientX ?? null;
        }}
        onTouchEnd={(e) => {
          if (touchX.current === null) return;
          const dx = (e.changedTouches[0]?.clientX ?? touchX.current) - touchX.current;
          if (Math.abs(dx) > 50) (dx < 0 ? next : prev)();
          touchX.current = null;
        }}
      >
        <div key={`copy-${slide.id}`} className="home-enter" aria-live="polite">
          <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-[#0b5d45]">{slide.kicker}</p>
          <h2 className="home-display mt-4 text-balance text-[34px] font-medium leading-[1.06] tracking-[-0.02em] sm:text-[46px] lg:min-h-[2.15em] lg:text-[clamp(40px,6.4vh,60px)]">
            {slide.title}
          </h2>
          <p className="mt-5 max-w-[32rem] text-[16px] leading-[1.6] text-[#4a5b55] lg:min-h-[4.8em] lg:text-[17px]">
            {slide.body}
          </p>
          <div className="mt-7 flex flex-wrap items-center gap-3">
            <a
              href={REQUEST_ACCESS_HREF}
              className="group inline-flex items-center gap-2 rounded-md bg-[#0b5d45] px-5 py-3 text-[14.5px] font-semibold text-white shadow-[0_10px_24px_-12px_rgba(11,93,69,0.7)] transition-colors hover:bg-[#094d3a]"
            >
              Request access for your firm
              <ArrowRight size={15} aria-hidden="true" className="transition-transform group-hover:translate-x-0.5" />
            </a>
            {index < SLIDES.length - 1 ? (
              <button
                type="button"
                onClick={next}
                className="inline-flex items-center gap-2 rounded-md border border-[#cbd8d2] bg-white px-5 py-3 text-[14.5px] font-medium text-[#0d1f1a] transition-colors hover:border-[#0b5d45]"
              >
                {index === 0 ? 'See how it works' : 'Next'}
                <ArrowRight size={15} aria-hidden="true" />
              </button>
            ) : (
              <Link
                href="/sign-in"
                className="inline-flex items-center rounded-md border border-[#cbd8d2] bg-white px-5 py-3 text-[14.5px] font-medium text-[#0d1f1a] transition-colors hover:border-[#0b5d45]"
              >
                Sign in
              </Link>
            )}
          </div>
          <p className="mt-4 text-[13px] text-[#6b7c76]">Fondok onboards your firm. Your team joins by invitation.</p>
        </div>

        <div key={`visual-${slide.id}`} className="home-enter hidden sm:block">
          {slide.visual}
        </div>
      </main>

      {/* Slide navigation */}
      <footer className="relative z-10 border-t border-[#dfe7e3] bg-white/70 backdrop-blur">
        <div className="mx-auto flex max-w-[1240px] items-center gap-3 px-4 sm:px-6 lg:px-10">
          <button
            type="button"
            onClick={prev}
            aria-label="Previous slide"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[#cbd8d2] text-[#3d4f49] transition-colors hover:border-[#0b5d45] hover:text-[#0b5d45]"
          >
            <ArrowLeft size={15} aria-hidden="true" />
          </button>
          <ol className="flex flex-1 items-stretch justify-center gap-2 lg:gap-0" role="tablist" aria-label="Slides">
            {SLIDES.map((s, i) => {
              const active = i === index;
              return (
                <li key={s.id} className="lg:flex-1">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={active}
                    aria-label={`${i + 1} of ${SLIDES.length}: ${s.tab}`}
                    onClick={() => go(i)}
                    className="group relative flex w-full flex-col items-center py-4 lg:items-start lg:px-3"
                  >
                    <span
                      className={`hidden text-[13px] font-medium transition-colors lg:block ${
                        active ? 'text-[#0d1f1a]' : 'text-[#7c8b86] group-hover:text-[#3d4f49]'
                      }`}
                    >
                      {s.tab}
                    </span>
                    <span
                      className={`relative h-[3px] overflow-hidden rounded-full bg-[#dfe7e3] lg:mt-2 lg:w-full ${
                        active ? 'w-8' : 'w-3'
                      } transition-all`}
                    >
                      {active && (
                        <span
                          key={`bar-${index}`}
                          onAnimationEnd={next}
                          className="home-progress absolute inset-0 origin-left rounded-full bg-[#0b5d45]"
                          style={{ animationDuration: `${SLIDE_MS}ms`, animationPlayState: paused ? 'paused' : 'running' }}
                        />
                      )}
                      {i < index && <span className="absolute inset-0 rounded-full bg-[#9fc3b5]" />}
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          <button
            type="button"
            onClick={next}
            aria-label="Next slide"
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full border border-[#cbd8d2] text-[#3d4f49] transition-colors hover:border-[#0b5d45] hover:text-[#0b5d45]"
          >
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        </div>
      </footer>
    </div>
  );
}

/* ---------- Visuals (illustrative sample hotel) ---------- */

function Card({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <figure className="mx-auto w-full max-w-[520px]">
      <div className="overflow-hidden rounded-2xl border border-[#dfe7e3] bg-white shadow-[0_30px_60px_-30px_rgba(13,31,26,0.35)]">
        <div className="flex items-center justify-between gap-3 border-b border-[#edf1ef] px-5 py-3.5">
          <div className="min-w-0">
            <div className="truncate text-[13px] font-semibold">{title ?? 'Sample hotel'}</div>
            <div className="home-mono mt-0.5 truncate text-[11px] text-[#7c8b86]">132 keys · Full-Service · Acquisition</div>
          </div>
          <span className="shrink-0 rounded-sm bg-[#eef4f1] px-2 py-0.5 text-[10.5px] font-medium uppercase tracking-[0.1em] text-[#0b5d45]">
            Illustrative
          </span>
        </div>
        {children}
      </div>
      <figcaption className="mt-3 text-center text-[12px] text-[#7c8b86]">Illustrative. Sample hotel, not a real deal.</figcaption>
    </figure>
  );
}

function SourceChip({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-[#eaf6ef] px-2 py-1 text-[11.5px] font-medium text-[#13603f]">
      <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-[#1fa36a]" />
      {children}
    </span>
  );
}

function OverviewVisual() {
  return (
    <Card>
      <div className="grid grid-cols-2 divide-x divide-[#edf1ef]">
        <div className="px-5 py-5">
          <div className="text-[10.5px] uppercase tracking-[0.08em] text-[#7c8b86]">Broker NOI</div>
          <div className="home-mono mt-1.5 text-[28px] text-[#a3aeaa] line-through decoration-1">$4.6M</div>
          <div className="mt-1 text-[11.5px] text-[#7c8b86]">Offering memorandum</div>
        </div>
        <div className="px-5 py-5">
          <div className="text-[10.5px] uppercase tracking-[0.08em] text-[#7c8b86]">Underwritten NOI</div>
          <div className="home-mono mt-1.5 text-[28px]">$4.1M</div>
          <div className="mt-1 text-[11.5px] text-[#13603f]">From the T-12</div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 border-y border-[#f3e3cc] bg-[#fdf6ec] px-5 py-2.5">
        <span className="text-[12.5px] text-[#8a4a09]">
          Broker overstates NOI by <span className="home-mono">12.2%</span>
        </span>
        <span className="rounded-sm bg-[#b4540a]/10 px-1.5 py-0.5 text-[10.5px] font-medium uppercase tracking-[0.08em] text-[#9a4808]">
          Flagged
        </span>
      </div>
      <div className="flex items-center justify-between gap-3 px-5 py-3.5">
        <span className="text-[12.5px] text-[#4a5b55]">Source</span>
        <SourceChip>FY T-12, page 4</SourceChip>
      </div>
    </Card>
  );
}

function BrokerVisual() {
  const rows = [
    { k: 'Occupancy', om: '82.0%', t12: '78.0%', d: '+4.0 pts' },
    { k: 'ADR', om: '$295', t12: '$285', d: '+3.5%' },
    { k: 'RevPAR', om: '$242', t12: '$222', d: '+8.8%' },
    { k: 'NOI', om: '$4.6M', t12: '$4.1M', d: '+12.2%' },
  ];
  return (
    <Card title="Sample hotel · OM vs T-12">
      <table className="w-full text-left">
        <thead>
          <tr className="text-[10.5px] uppercase tracking-[0.08em] text-[#7c8b86]">
            <th className="px-5 py-2.5 font-medium">Line</th>
            <th className="px-3 py-2.5 text-right font-medium">OM</th>
            <th className="px-3 py-2.5 text-right font-medium">T-12</th>
            <th className="px-5 py-2.5 text-right font-medium">OM over</th>
          </tr>
        </thead>
        <tbody className="home-mono text-[13px]">
          {rows.map((r) => (
            <tr key={r.k} className="border-t border-[#edf1ef]">
              <td className="px-5 py-3 font-sans text-[13px] font-medium">{r.k}</td>
              <td className="px-3 py-3 text-right text-[#7c8b86]">{r.om}</td>
              <td className="px-3 py-3 text-right">{r.t12}</td>
              <td className="px-5 py-3 text-right">
                <span className="rounded-sm bg-[#fdf6ec] px-1.5 py-0.5 text-[#9a4808]">{r.d}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="border-t border-[#edf1ef] px-5 py-3 text-[12px] text-[#4a5b55]">
        4 lines flagged where the OM runs ahead of the trailing twelve months.
      </div>
    </Card>
  );
}

function SourcedVisual() {
  return (
    <Card title="Sample hotel · Year 1 revenue">
      <div className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4 p-5">
        <div className="rounded-lg border border-[#edf1ef] bg-[#fafcfb] p-3.5">
          <div className="flex items-center gap-1.5 text-[11px] font-medium text-[#7c8b86]">
            <FileText size={12} aria-hidden="true" /> FY T-12 · page 4
          </div>
          <div className="mt-3 space-y-2" aria-hidden="true">
            <div className="h-1.5 w-3/4 rounded bg-[#e3e9e6]" />
            <div className="h-1.5 w-1/2 rounded bg-[#e3e9e6]" />
          </div>
          <div className="mt-3 flex items-center justify-between rounded-md bg-[#eaf6ef] px-2 py-1.5 ring-1 ring-[#1fa36a]/40">
            <span className="text-[11.5px] font-medium text-[#13603f]">Rooms revenue</span>
            <span className="home-mono text-[11.5px] text-[#13603f]">10,710,414</span>
          </div>
          <div className="mt-3 space-y-2" aria-hidden="true">
            <div className="h-1.5 w-2/3 rounded bg-[#e3e9e6]" />
            <div className="h-1.5 w-5/6 rounded bg-[#e3e9e6]" />
            <div className="h-1.5 w-1/3 rounded bg-[#e3e9e6]" />
          </div>
        </div>
        <div className="flex flex-col justify-center">
          <div className="text-[10.5px] uppercase tracking-[0.08em] text-[#7c8b86]">Rooms revenue, Year 1</div>
          <div className="home-mono mt-1.5 text-[28px]">$10.71M</div>
          <div className="mt-3">
            <SourceChip>T-12, page 4</SourceChip>
          </div>
          <p className="mt-3 text-[12px] leading-[1.5] text-[#4a5b55]">
            132 keys × 78% occupancy × $285 ADR × 365 nights.
          </p>
        </div>
      </div>
    </Card>
  );
}

function PricingVisual() {
  return (
    <Card title="Sample hotel · Pricing">
      <div className="grid grid-cols-2 divide-x divide-[#edf1ef]">
        <div className="px-5 py-5">
          <div className="text-[10.5px] uppercase tracking-[0.08em] text-[#7c8b86]">Asking price</div>
          <div className="home-mono mt-1.5 text-[28px] text-[#a3aeaa]">$58.0M</div>
          <div className="mt-1 text-[11.5px] text-[#7c8b86]">7.1% cap on underwritten NOI</div>
        </div>
        <div className="px-5 py-5">
          <div className="text-[10.5px] uppercase tracking-[0.08em] text-[#7c8b86]">Clears your hurdle</div>
          <div className="home-mono mt-1.5 text-[28px]">$52.0M</div>
          <div className="mt-1 text-[11.5px] text-[#13603f]">7.9% cap on underwritten NOI</div>
        </div>
      </div>
      <div className="border-t border-[#edf1ef] px-5 py-4">
        <div className="flex items-center justify-between text-[11.5px] text-[#7c8b86]">
          <span>Your price</span>
          <span>Ask</span>
        </div>
        <div className="relative mt-2 h-2 rounded-full bg-[#edf1ef]">
          <div className="absolute inset-y-0 left-0 w-[90%] rounded-full bg-[#1fa36a]/70" />
          <div className="absolute inset-y-0 left-[90%] right-0 rounded-r-full bg-[#b4540a]/25" />
        </div>
        <div className="mt-3 text-[12.5px] text-[#8a4a09]">
          The ask is <span className="home-mono">$6.0M</span> above the price that clears your returns.
        </div>
      </div>
    </Card>
  );
}

function OutputsVisual() {
  const items = [
    { icon: FileText, name: 'IC memo', note: 'Thesis, risks and the numbers behind them' },
    { icon: Presentation, name: 'IC deck', note: 'The slides your committee reads' },
    { icon: Table2, name: 'Underwriting model', note: 'The full model your analysts work in' },
  ];
  return (
    <Card title="Sample hotel · IC package">
      <ul className="divide-y divide-[#edf1ef]">
        {items.map(({ icon: Icon, name, note }) => (
          <li key={name} className="flex items-center gap-3.5 px-5 py-4">
            <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#eef4f1] text-[#0b5d45]">
              <Icon size={17} aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-[13.5px] font-semibold">{name}</div>
              <div className="truncate text-[12px] text-[#7c8b86]">{note}</div>
            </div>
            <Check size={16} aria-hidden="true" className="shrink-0 text-[#1fa36a]" />
          </li>
        ))}
      </ul>
      <div className="border-t border-[#edf1ef] bg-[#fafcfb] px-5 py-3 text-[12px] text-[#4a5b55]">
        One underwriting. The same numbers on every page.
      </div>
    </Card>
  );
}

function TeamVisual() {
  const roles = [
    { role: 'Analysts', gain: 'Pressure-test the deal instead of rekeying it.' },
    { role: 'VPs', gain: 'Review sourced numbers, not a fresh spreadsheet.' },
    { role: 'Principals', gain: 'Decide on a case that is consistent deal to deal.' },
  ];
  return (
    <figure className="mx-auto w-full max-w-[520px]">
      <ul className="overflow-hidden rounded-2xl border border-[#dfe7e3] bg-white shadow-[0_30px_60px_-30px_rgba(13,31,26,0.35)]">
        {roles.map((r) => (
          <li key={r.role} className="flex items-start gap-4 border-b border-[#edf1ef] px-5 py-5 last:border-b-0">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[#eaf6ef] text-[#13603f]">
              <Check size={15} aria-hidden="true" />
            </span>
            <div>
              <div className="text-[14px] font-semibold">{r.role}</div>
              <div className="mt-0.5 text-[12.5px] text-[#5b6b66]">{r.gain}</div>
            </div>
          </li>
        ))}
        <li className="bg-[#fafcfb] px-5 py-3 text-[12px] text-[#4a5b55]">
          Fondok sets up your firm. Your team joins by invitation.
        </li>
      </ul>
    </figure>
  );
}
