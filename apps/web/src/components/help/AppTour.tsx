'use client';

/**
 * AppTour — first-visit product tour anchored to the dashboard.
 *
 * Five steps that walk the analyst through the workspace. Each step
 * highlights a real DOM target by `data-tour=` selector and renders a
 * card via React Portal. Persistence is local-only:
 *
 *   fondok:tour:dashboard:completed = 'true'    once skipped or finished
 *   fondok:tour:dashboard:current_step = '0..4' resume-from index
 *
 * The tour respects the global "hints disabled" preference + ESC closes.
 *
 * Mount once at the top of `/dashboard` — it renders nothing until
 * the user actually lands on the dashboard for the first time.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { X, ArrowRight } from 'lucide-react';
import { hintsEnabled } from './useHintsEnabled';

const COMPLETED_KEY = 'fondok:tour:dashboard:completed';
const STEP_KEY = 'fondok:tour:dashboard:current_step';

interface Step {
  selector: string;
  title: string;
  body: string;
  side: 'top' | 'right' | 'bottom' | 'left';
}

const STEPS: Step[] = [
  {
    selector: '[data-tour="sidebar"]',
    title: 'Your workspace',
    body: 'Dashboard for portfolio, Projects for the deal list, Methodology for every formula Fondok uses.',
    side: 'right',
  },
  {
    selector: '[data-tour="new-deal"]',
    title: 'Start a new underwriting',
    body: 'Click here to walk the 6-step wizard — Fondok extracts the OM and T-12 and pre-populates everything else.',
    side: 'bottom',
  },
  {
    selector: '[data-tour="project-card"]',
    title: 'Open any deal',
    body: 'Click a card to enter the workspace — Data Room, Validation, Overview, Returns, and the IC memo all live there.',
    side: 'top',
  },
  {
    selector: '[data-tour="methodology"]',
    title: 'How Fondok thinks',
    body: 'Every formula, every default, every projection precedence chain is documented here. Reference any time — links live inside every coach mark.',
    side: 'right',
  },
  {
    selector: '[data-tour="settings"]',
    title: 'Finally — turn this off if you want',
    body: 'Settings holds your account, team, and a master switch to silence coach marks once you have got the hang of it.',
    side: 'right',
  },
];

interface Pos {
  top: number;
  left: number;
  side: Step['side'];
}

function compute(target: DOMRect, panel: { width: number; height: number }, side: Step['side']): Pos {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const GAP = 16;
  const PAD = 16;

  const space = {
    top: target.top,
    bottom: vh - target.bottom,
    left: target.left,
    right: vw - target.right,
  };
  let s = side;
  const need = s === 'top' || s === 'bottom' ? panel.height : panel.width;
  if (space[s] < need + GAP + PAD) {
    const opp: Record<typeof s, typeof s> = {
      top: 'bottom',
      bottom: 'top',
      left: 'right',
      right: 'left',
    } as const;
    if (space[opp[s]] >= need + GAP + PAD) s = opp[s];
  }

  let top = 0;
  let left = 0;
  if (s === 'top') {
    top = target.top - panel.height - GAP;
    left = target.left + target.width / 2 - panel.width / 2;
  } else if (s === 'bottom') {
    top = target.bottom + GAP;
    left = target.left + target.width / 2 - panel.width / 2;
  } else if (s === 'left') {
    left = target.left - panel.width - GAP;
    top = target.top + target.height / 2 - panel.height / 2;
  } else {
    left = target.right + GAP;
    top = target.top + target.height / 2 - panel.height / 2;
  }
  left = Math.max(PAD, Math.min(vw - panel.width - PAD, left));
  top = Math.max(PAD, Math.min(vh - panel.height - PAD, top));
  return { top, left, side: s };
}

export function AppTour() {
  const [active, setActive] = useState(false);
  const [step, setStep] = useState(0);
  const [pos, setPos] = useState<Pos | null>(null);
  const [highlightRect, setHighlightRect] = useState<DOMRect | null>(null);
  const [mounted, setMounted] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => setMounted(true), []);

  // Mount-time guard: only start if hints are on and tour hasn't completed.
  useEffect(() => {
    if (!hintsEnabled()) return;
    try {
      if (window.localStorage.getItem(COMPLETED_KEY) === 'true') return;
      const saved = Number(window.localStorage.getItem(STEP_KEY) ?? '0');
      const validStep = Number.isFinite(saved) && saved >= 0 && saved < STEPS.length ? saved : 0;
      setStep(validStep);
      // Slight delay so the dashboard's content has rendered.
      const t = setTimeout(() => setActive(true), 600);
      return () => clearTimeout(t);
    } catch {
      // ignore
    }
  }, []);

  const measure = useCallback((stepIdx: number) => {
    const sel = STEPS[stepIdx]?.selector;
    if (!sel) return;
    const target = document.querySelector(sel) as HTMLElement | null;
    if (!target) {
      setHighlightRect(null);
      setPos(null);
      return;
    }
    const rect = target.getBoundingClientRect();
    setHighlightRect(rect);
    if (panelRef.current) {
      const p = panelRef.current.getBoundingClientRect();
      setPos(compute(rect, { width: p.width, height: p.height }, STEPS[stepIdx].side));
    }
  }, []);

  useLayoutEffect(() => {
    if (!active) return;
    measure(step);
  }, [active, step, measure]);

  useEffect(() => {
    if (!active) return;
    const onResize = () => measure(step);
    window.addEventListener('resize', onResize);
    window.addEventListener('scroll', onResize, true);
    return () => {
      window.removeEventListener('resize', onResize);
      window.removeEventListener('scroll', onResize, true);
    };
  }, [active, step, measure]);

  const finish = useCallback(() => {
    try {
      window.localStorage.setItem(COMPLETED_KEY, 'true');
      window.localStorage.removeItem(STEP_KEY);
    } catch {
      // ignore
    }
    setActive(false);
  }, []);

  const advance = useCallback(() => {
    if (step >= STEPS.length - 1) {
      finish();
      return;
    }
    const next = step + 1;
    try {
      window.localStorage.setItem(STEP_KEY, String(next));
    } catch {
      // ignore
    }
    setStep(next);
  }, [step, finish]);

  // ESC skips the tour entirely.
  useEffect(() => {
    if (!active) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') finish();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, finish]);

  if (!active || !mounted || typeof document === 'undefined') return null;

  const current = STEPS[step];

  return createPortal(
    <>
      {/* Spotlight overlay — dims everything except a rect around the target. */}
      {highlightRect && (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-[9990] pointer-events-none"
          style={{
            background:
              'radial-gradient(closest-side at var(--x) var(--y), transparent 0, rgba(15,23,42,0.55) 220%)',
            ['--x' as any]: `${highlightRect.left + highlightRect.width / 2}px`,
            ['--y' as any]: `${highlightRect.top + highlightRect.height / 2}px`,
          }}
        />
      )}
      {highlightRect && (
        <div
          aria-hidden="true"
          className="fixed z-[9991] pointer-events-none rounded-md ring-2 ring-brand-500 ring-offset-2 animate-pulse motion-reduce:animate-none"
          style={{
            top: highlightRect.top - 4,
            left: highlightRect.left - 4,
            width: highlightRect.width + 8,
            height: highlightRect.height + 8,
          }}
        />
      )}

      {/* Card */}
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="false"
        aria-labelledby="tour-title"
        className="fixed z-[9999] animate-in fade-in zoom-in-95 duration-150 motion-reduce:animate-none"
        style={pos ? { top: pos.top, left: pos.left } : { top: -9999, left: -9999, opacity: 0 }}
      >
        <div className="relative bg-white border border-brand-300 shadow-2xl rounded-lg p-4 max-w-sm">
          <button
            type="button"
            onClick={finish}
            aria-label="Skip tour"
            className="absolute top-2.5 right-2.5 text-ink-400 hover:text-ink-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded p-0.5"
          >
            <X size={13} />
          </button>
          <div className="pr-5">
            <div className="flex items-center gap-1.5 mb-1">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-brand-500" />
              <span className="text-[10px] uppercase tracking-wider text-brand-700 font-semibold">
                Quick tour · {step + 1} of {STEPS.length}
              </span>
            </div>
            <h4 id="tour-title" className="text-[16px] font-semibold text-ink-900 leading-snug">
              {current.title}
            </h4>
            <p className="mt-1.5 text-[12.5px] text-ink-700 leading-relaxed">{current.body}</p>
            <div className="mt-3 flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={finish}
                className="text-[11.5px] text-ink-500 hover:text-ink-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded px-1.5 py-1"
              >
                Skip tour
              </button>
              <button
                type="button"
                onClick={advance}
                className="inline-flex items-center gap-1 text-[11.5px] bg-brand-500 hover:bg-brand-700 text-white px-3 py-1.5 rounded font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-100"
              >
                {step >= STEPS.length - 1 ? 'Finish' : 'Next'}
                <ArrowRight size={11} aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>
      </div>
    </>,
    document.body,
  );
}

export default AppTour;
