'use client';

/**
 * GetStartedHero — the zero-deal first-run panel.
 *
 * A brand-new user otherwise lands on a wall of empty KPIs and a terse
 * "No deals yet". This replaces that with a warm, single-glance path:
 * the three steps from an empty workspace to a complete underwriting, plus
 * one prominent CTA. Shown only when the tenant has zero deals; the
 * non-blocking GettingStartedSidebar handles ongoing coach-marks after that.
 */

import Link from 'next/link';
import { Plus, FileText, LineChart, Sparkles, ArrowRight } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';

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
    <Card tone="luxe" className="p-7 mb-6">
      <div className="flex items-center gap-1.5 mb-2">
        <Sparkles size={14} className="text-gold-400" strokeWidth={1.75} />
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
