'use client';

import Link from 'next/link';
import { useCallback, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { ArrowRight, Check, FileText, Presentation, Table2 } from 'lucide-react';
import { REQUEST_ACCESS_HREF } from './requestAccess';

// The public home page is ONE screen on desktop (phones scroll) and nothing
// on it moves without the visitor: no carousel, no timers, no entry
// animation. Left: the thesis and one CTA. Right: the sample-hotel card with
// four click-only tabs. Footer: one line of true data-handling facts.
// Say what Fondok does for an investor (benefit first), never how it works
// inside (engines, rules, screens and metrics stay behind sign-in). No
// customer names, pilot mentions, usage numbers, time-saved claims or
// certifications. Every figure in the visuals is an illustrative sample
// hotel, labelled so, and internally consistent: 132 keys, T-12 78% occ ×
// $285 ADR ≈ $10.71M rooms revenue, NOI $4.1M; OM 82% × $295, NOI $4.6M
// (12.2% over); ask $58.0M. Design record: docs/superpowers/specs/2026-11-07-home-page-design.md

type Proof = {
  id: string;
  tab: string;
  caption: string;
  visual: ReactNode;
};

const PROOFS: Proof[] = [
  {
    id: 'comparison',
    tab: 'OM vs T-12',
    caption: 'Four lines where the OM runs ahead of the trailing twelve months, flagged before they become your basis.',
    visual: <BrokerVisual />,
  },
  {
    id: 'sourced',
    tab: 'Cited to the page',
    caption: 'Year 1 rooms revenue traced to T-12, page 4. Your IC checks the work instead of questioning it.',
    visual: <SourcedVisual />,
  },
  {
    id: 'pricing',
    tab: 'Walk-away price',
    caption: 'The price that clears your hurdle, beside the ask. Bid with conviction, walk away without second-guessing.',
    visual: <PricingVisual />,
  },
  {
    id: 'package',
    tab: 'IC package',
    caption: 'Memo, deck and model from one underwriting. Nothing retyped, nothing contradicting itself at committee.',
    visual: <OutputsVisual />,
  },
];

/** True today; each sentence maps to a shipped control (see the design record). */
export const DATA_HANDLING = [
  'Each firm’s data is isolated at the database level.',
  'Every action is recorded in the firm’s activity log.',
  'Fondok’s access to a firm’s deals is time-limited and recorded.',
  'Documents are not used to train AI models.',
];

export default function Landing() {
  const [index, setIndex] = useState(0);
  // The card only crossfades after a deliberate tab change, never on first paint.
  const [swapped, setSwapped] = useState(false);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const baseId = useId();
  const proof = PROOFS[index];

  const select = useCallback((i: number, focus = false) => {
    const next = (i + PROOFS.length) % PROOFS.length;
    setIndex(next);
    setSwapped(true);
    if (focus) tabRefs.current[next]?.focus();
  }, []);

  const onTabKey = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const keys: Record<string, () => void> = {
      ArrowRight: () => select(i + 1, true),
      ArrowLeft: () => select(i - 1, true),
      Home: () => select(0, true),
      End: () => select(PROOFS.length - 1, true),
    };
    const run = keys[e.key];
    if (run) {
      e.preventDefault();
      run();
    }
  };

  return (
    <div className="flex min-h-[100svh] flex-col bg-[#f4f7f5] text-[#0d1f1a] lg:h-[100svh] lg:min-h-0 lg:overflow-hidden">
      <header className="border-b border-[#dfe7e3] bg-white/60 backdrop-blur">
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
              className="whitespace-nowrap rounded-md px-3 py-2 text-[13.5px] font-medium text-[#3d4f49] transition-colors hover:text-[#0d1f1a] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0b5d45] focus-visible:ring-offset-2"
            >
              Sign in
            </Link>
            <a
              href={REQUEST_ACCESS_HREF}
              className="whitespace-nowrap rounded-md bg-[#0b5d45] px-3.5 py-2 text-[13.5px] font-semibold text-white transition-colors hover:bg-[#094d3a] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0b5d45] focus-visible:ring-offset-2"
            >
              Request access
            </a>
          </div>
        </div>
      </header>

      <main
        id="main"
        className="mx-auto grid w-full max-w-[1240px] flex-1 grid-cols-1 items-center gap-10 px-4 py-8 sm:px-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-16 lg:px-10 lg:py-4"
      >
        <div>
          <p className="text-[12px] font-semibold uppercase tracking-[0.16em] text-[#0b5d45]">Hotel acquisition underwriting</p>
          <h1 className="home-display mt-4 text-balance text-[34px] font-medium leading-[1.06] tracking-[-0.02em] sm:text-[46px] lg:text-[clamp(40px,6.4vh,60px)]">
            Pay the right price for every hotel you <em className="italic text-[#0b5d45]">buy.</em>
          </h1>
          <p className="mt-5 max-w-[34rem] text-[16px] leading-[1.6] text-[#4a5b55] lg:text-[17px]">
            Fondok reads the OM, T-12 and STR reports and builds the underwriting, the walk-away price and the IC
            package. Every figure is cited to the source page. Your analysts can override any assumption, and the
            change carries through to the memo, deck and model.
          </p>
          <div className="mt-7">
            <a
              href={REQUEST_ACCESS_HREF}
              className="group inline-flex items-center gap-2 rounded-md bg-[#0b5d45] px-5 py-3 text-[14.5px] font-semibold text-white shadow-[0_10px_24px_-12px_rgba(11,93,69,0.7)] transition-colors hover:bg-[#094d3a] focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0b5d45] focus-visible:ring-offset-2"
            >
              Request access
              <ArrowRight size={15} aria-hidden="true" className="transition-transform group-hover:translate-x-0.5" />
            </a>
          </div>
          <p className="mt-4 text-[13px] text-[#6b7c76]">Fondok onboards your firm. Your team joins by invitation.</p>
        </div>

        <div className="min-w-0">
          <div
            key={swapped ? proof.id : 'first'}
            role="tabpanel"
            id={`${baseId}-panel`}
            aria-labelledby={`${baseId}-tab-${proof.id}`}
            className={swapped ? 'home-swap' : undefined}
          >
            {proof.visual}
          </div>
          <div
            role="tablist"
            aria-label="What Fondok shows you"
            className="mx-auto mt-5 flex max-w-[520px] gap-1 overflow-x-auto border-b border-[#dfe7e3] [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {PROOFS.map((p, i) => {
              const active = i === index;
              return (
                <button
                  key={p.id}
                  ref={(el) => {
                    tabRefs.current[i] = el;
                  }}
                  type="button"
                  role="tab"
                  id={`${baseId}-tab-${p.id}`}
                  aria-selected={active}
                  aria-controls={`${baseId}-panel`}
                  tabIndex={active ? 0 : -1}
                  onClick={() => select(i)}
                  onKeyDown={(e) => onTabKey(e, i)}
                  className={`-mb-px shrink-0 whitespace-nowrap border-b-2 px-3 py-2.5 text-[13px] font-medium transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-[#0b5d45] ${
                    active ? 'border-[#0b5d45] text-[#0d1f1a]' : 'border-transparent text-[#7c8b86] hover:text-[#3d4f49]'
                  }`}
                >
                  {p.tab}
                </button>
              );
            })}
          </div>
          <p aria-live="polite" className="mx-auto mt-3 max-w-[520px] text-[12.5px] leading-[1.5] text-[#6b7c76]">
            {proof.caption}
          </p>
        </div>
      </main>

      <footer className="border-t border-[#dfe7e3] bg-white/70">
        <p className="mx-auto max-w-[1240px] px-4 py-3 text-[12.5px] leading-[1.5] text-[#6b7c76] sm:px-6 lg:px-10">
          {DATA_HANDLING.join(' ')}
        </p>
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

function BrokerVisual() {
  const rows = [
    { k: 'Occupancy', om: '82.0%', t12: '78.0%', d: '+4.0 pts' },
    { k: 'ADR', om: '$295', t12: '$285', d: '+3.5%' },
    { k: 'RevPAR', om: '$242', t12: '$222', d: '+8.8%' },
    { k: 'NOI', om: '$4.6M', t12: '$4.1M', d: '+12.2%' },
  ];
  return (
    <Card title="Sample hotel · OM vs T-12">
      <table className="w-full table-fixed text-left">
        <colgroup>
          <col className="w-[31%]" />
          <col className="w-[21%]" />
          <col className="w-[21%]" />
          <col className="w-[27%]" />
        </colgroup>
        <thead>
          <tr className="text-[10.5px] uppercase tracking-[0.08em] text-[#7c8b86]">
            <th className="px-3 py-2.5 font-medium sm:px-5">Line</th>
            <th className="px-2 py-2.5 text-right font-medium sm:px-3">OM</th>
            <th className="px-2 py-2.5 text-right font-medium sm:px-3">T-12</th>
            <th className="px-3 py-2.5 text-right font-medium sm:px-5">OM over</th>
          </tr>
        </thead>
        <tbody className="home-mono text-[12.5px] sm:text-[13px]">
          {rows.map((r) => (
            <tr key={r.k} className="border-t border-[#edf1ef]">
              <td className="px-3 py-3 font-sans text-[12.5px] font-medium sm:px-5 sm:text-[13px]">{r.k}</td>
              <td className="px-2 py-3 text-right text-[#7c8b86] sm:px-3">{r.om}</td>
              <td className="px-2 py-3 text-right sm:px-3">{r.t12}</td>
              <td className="px-3 py-3 text-right sm:px-5">
                <span className="whitespace-nowrap rounded-sm bg-[#fdf6ec] px-1.5 py-0.5 text-[#9a4808]">{r.d}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1.5 border-t border-[#edf1ef] px-3 py-3 text-[12px] text-[#4a5b55] sm:px-5">
        <span>4 lines flagged where the OM runs ahead of the T-12.</span>
        <SourceChip>FY T-12, page 4</SourceChip>
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
          <p className="mt-3 text-[12px] leading-[1.5] text-[#4a5b55]">132 keys × 78% occupancy × $285 ADR × 365 nights.</p>
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
