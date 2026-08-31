'use client';

/**
 * GetStartedHero — the OPTIONAL zero-deal first-run panel.
 *
 * A brand-new user otherwise lands on a wall of empty KPIs and a terse
 * "No deals yet". This offers a warm, single-glance path: the three steps
 * from an empty workspace to a complete underwriting, plus one prominent CTA.
 *
 * It is opt-out, not forced:
 *   - Respects the global "Show contextual coach marks" toggle (hintsEnabled).
 *   - Dismissable via the X — persisted under a ``fondok:coachmark:*`` key so
 *     the Settings "Reset coach marks" action brings it back on demand.
 *   - When hidden, the parent falls back to the normal view / empty state.
 *
 * ``useGetStartedHeroVisible()`` lets the parent decide hero-vs-fallback.
 */

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { Plus, FileText, LineChart, Sparkles, ArrowRight, X } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { hintsEnabled } from './useHintsEnabled';

// A coachmark-namespaced key so Settings' resetAllCoachMarks() clears it.
const HERO_KEY = 'fondok:coachmark:getstarted-hero';
const HINTS_EVENT = 'fondok:hints-changed';

function heroDismissed(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(HERO_KEY) === '1';
  } catch {
    return false;
  }
}

function dismissHero(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(HERO_KEY, '1');
  } catch {
    /* private mode — non-fatal */
  }
  // Same event the hints toggle + reset use, so the parent hook + other tabs update.
  window.dispatchEvent(new Event(HINTS_EVENT));
}

/** Whether the getting-started hero should show: hints on AND not dismissed. */
export function useGetStartedHeroVisible(): boolean {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const compute = () => setVisible(hintsEnabled() && !heroDismissed());
    compute();
    window.addEventListener('storage', compute);
    window.addEventListener(HINTS_EVENT, compute);
    return () => {
      window.removeEventListener('storage', compute);
      window.removeEventListener(HINTS_EVENT, compute);
    };
  }, []);
  return visible;
}

const STEPS: { icon: typeof Plus; title: string; body: string }[] = [
  {
    icon: Plus,
    title: 'Create a deal',
    body: 'Name the hotel, its market, and key count — under a minute.',
  },
  {
    icon: FileText,
    title: 'Upload your documents',
    body: 'Drop in the OM, T-12 / P&L, and STR report. Fondok extracts and grounds every figure automatically.',
  },
  {
    icon: LineChart,
    title: 'Review the underwriting',
    body: 'Sources & uses, debt, returns, sensitivities, and an IC-ready memo — every number traced to its source.',
  },
];

export function GetStartedHero() {
  return (
    <Card tone="luxe" className="p-7 mb-6 relative">
      <button
        type="button"
        onClick={dismissHero}
        aria-label="Dismiss getting started"
        title="Dismiss — reappears via Settings → Reset coach marks"
        className="absolute top-3.5 right-3.5 text-ink-400 hover:text-ink-700 transition-colors"
      >
        <X size={15} />
      </button>

      <div className="flex items-center gap-1.5 mb-2 text-gold-400">
        <Sparkles size={14} strokeWidth={1.75} />
        <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-gold-600">
          Getting started
        </span>
      </div>

      <h2 className="font-display text-[22px] leading-tight font-semibold text-ink-900 tracking-tight text-balance">
        Underwrite your first hotel
      </h2>
      <p className="text-[13px] text-ink-500 mt-2 max-w-2xl leading-relaxed">
        Fondok turns an offering memorandum and a T-12 into a complete institutional
        underwriting — sources &amp; uses, debt, returns, and an IC memo — in minutes.
        Here&apos;s the path from an empty workspace to a decision:
      </p>

      <ol className="grid grid-cols-1 sm:grid-cols-3 gap-5 mt-6">
        {STEPS.map((s, i) => {
          const Icon = s.icon;
          return (
            <li key={s.title} className="relative">
              <div className="flex items-center gap-2.5 mb-2.5">
                <div className="w-9 h-9 rounded-lg border border-gold-200 bg-white/70 flex items-center justify-center text-gold-500 shadow-sm">
                  <Icon size={16} strokeWidth={1.75} />
                </div>
                <span className="text-[10.5px] font-semibold uppercase tracking-wider text-ink-400 tabular-nums">
                  Step {i + 1}
                </span>
              </div>
              <div className="text-[13px] font-semibold text-ink-900">{s.title}</div>
              <p className="text-[12px] text-ink-500 mt-1 leading-relaxed">{s.body}</p>
            </li>
          );
        })}
      </ol>

      <div className="mt-7 flex flex-wrap items-center gap-x-4 gap-y-2">
        <Link href="/projects/new" data-tour="new-deal">
          <Button variant="premium">
            <Plus size={14} /> Create your first deal
          </Button>
        </Link>
        <Link
          href="/methodology"
          className="text-[12.5px] text-brand-700 hover:text-brand-900 font-medium inline-flex items-center gap-1"
        >
          See how Fondok thinks <ArrowRight size={13} />
        </Link>
      </div>
    </Card>
  );
}
