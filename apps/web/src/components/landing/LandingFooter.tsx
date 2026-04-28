import Link from 'next/link';

export default function LandingFooter() {
  return (
    <footer className="bg-white border-t border-border">
      <div className="max-w-6xl mx-auto px-6 md:px-10 py-10">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-6">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 flex items-center justify-center shadow-sm">
              <span className="text-white font-semibold text-[14px]">F</span>
            </div>
            <div>
              <div className="text-[13px] font-semibold text-ink-900">Fondok AI</div>
              <div className="text-[11px] text-ink-500">Hotel acquisition underwriting</div>
            </div>
          </div>
          <nav className="flex flex-wrap items-center gap-x-5 gap-y-2 text-[12.5px] text-ink-700">
            <Link href="/dashboard" className="hover:text-ink-900">Demo</Link>
            <a
              href="https://github.com/aprem01/fondok"
              target="_blank"
              rel="noreferrer noopener"
              className="hover:text-ink-900"
            >
              GitHub
            </a>
            <a
              href="https://docs.anthropic.com"
              target="_blank"
              rel="noreferrer noopener"
              className="hover:text-ink-900"
            >
              Anthropic docs
            </a>
            <Link href="/diag" className="hover:text-ink-900">Diagnostics</Link>
          </nav>
        </div>
        <div className="mt-8 pt-6 border-t border-border flex items-center justify-between text-[11px] text-ink-500">
          <span>© {new Date().getFullYear()} Fondok</span>
          <span>Built with Next.js, FastAPI, LangGraph, and Anthropic</span>
        </div>
      </div>
    </footer>
  );
}
