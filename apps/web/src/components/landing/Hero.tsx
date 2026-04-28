import Link from 'next/link';
import { ArrowRight, Github } from 'lucide-react';

export default function Hero() {
  return (
    <section className="relative overflow-hidden border-b border-border bg-gradient-to-b from-white to-bg">
      <div className="absolute inset-0 pointer-events-none opacity-[0.04]"
        style={{
          backgroundImage:
            'radial-gradient(circle at 1px 1px, #0f172a 1px, transparent 0)',
          backgroundSize: '24px 24px',
        }}
        aria-hidden="true"
      />
      <div className="relative max-w-6xl mx-auto px-6 md:px-10 pt-16 md:pt-24 pb-16 md:pb-20">
        <div className="inline-flex items-center gap-2 px-2.5 py-1 rounded-full bg-brand-50 border border-brand-500/20 text-[11.5px] font-medium text-brand-700 mb-5">
          <span className="w-1.5 h-1.5 rounded-full bg-brand-500" />
          Phase 5 complete · live demo available
        </div>
        <h1 className="text-[40px] md:text-[56px] leading-[1.05] font-semibold tracking-tight text-ink-900 max-w-3xl">
          AI-powered hotel acquisition underwriting.
        </h1>
        <p className="mt-5 text-[17px] md:text-[19px] text-ink-700 max-w-2xl leading-relaxed">
          From offering memorandum to investment committee memo in{' '}
          <span className="font-semibold text-ink-900">17 minutes</span>. Decision
          infrastructure for institutional hotel investors — built on Claude
          Haiku, Sonnet, and Opus 4.7.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-3">
          <Link
            href="/dashboard"
            className="inline-flex items-center gap-2 px-5 py-3 rounded-md bg-brand-500 hover:bg-brand-600 text-white text-[14px] font-medium shadow-sm transition-colors"
          >
            Try the demo
            <ArrowRight size={15} aria-hidden="true" />
          </Link>
          <a
            href="https://github.com/aprem01/fondok"
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-2 px-5 py-3 rounded-md bg-white hover:bg-ink-300/15 border border-border text-ink-900 text-[14px] font-medium transition-colors"
          >
            <Github size={15} aria-hidden="true" />
            View on GitHub
          </a>
        </div>
        <div className="mt-10 flex flex-wrap items-center gap-x-6 gap-y-2 text-[12px] text-ink-500">
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-success-500" />
            132-key Kimpton Angler · Miami Beach (sample deal)
          </span>
          <span className="hidden md:inline text-ink-300">·</span>
          <span>23.5% IRR base case · 6 IC memo sections · 14 source citations</span>
        </div>
      </div>
    </section>
  );
}
