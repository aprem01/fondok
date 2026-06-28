'use client';

/**
 * GettingStartedSidebar — non-blocking onboarding checklist.
 *
 * Replaces AppTour (Wave 1 UX refactor, June 2026). The old tour walked
 * the user through 5 blocking popovers — modern onboarding (Linear's
 * checklist, Stripe's "Welcome to Stripe" widget, Notion's "Getting
 * Started" sidebar) does not block, does not pop, and lives off to the
 * side until the user is ready for it.
 *
 * Behavior:
 *   - Floating widget anchored bottom-right (`fixed bottom-4 right-4`).
 *   - Collapsed by default to a single FAB-style pill; click to expand
 *     into the 5-row checklist.
 *   - Each row has a checkbox, title, description, and a "Show me" link
 *     that pulses the target nav item (`ring-2 ring-brand-500`) for 2s.
 *     No popovers, no overlays — just a quiet visual nudge.
 *   - X dismisses the widget entirely. Reopening requires resetting via
 *     the existing `resetAllCoachMarks` helper on the Settings page.
 *   - Persists per-step + dismissal in localStorage.
 *   - Hidden when global hints are disabled.
 *
 * Storage keys:
 *   fondok:gettingstarted:dismissed             "true" once X is clicked
 *   fondok:gettingstarted:collapsed             "true" if collapsed to FAB
 *   fondok:gettingstarted:steps:{stepId}        "true" when checked off
 */

import { useCallback, useEffect, useState } from 'react';
import { CheckCircle2, Circle, X, ChevronDown, ChevronUp, Sparkles } from 'lucide-react';
import { cn } from '@/lib/format';
import { hintsEnabled } from './useHintsEnabled';

const DISMISS_KEY = 'fondok:gettingstarted:dismissed';
const COLLAPSE_KEY = 'fondok:gettingstarted:collapsed';
const STEP_KEY = (id: string) => `fondok:gettingstarted:steps:${id}`;

interface Step {
  id: string;
  selector: string; // CSS selector for the target nav item
  title: string;
  description: string;
}

const STEPS: Step[] = [
  {
    id: 'sidebar',
    selector: '[data-tour="sidebar"]',
    title: 'Tour your workspace',
    description: 'Dashboard, Projects, and Methodology live in the left rail.',
  },
  {
    id: 'new-deal',
    selector: '[data-tour="new-deal"]',
    title: 'Start a new underwriting',
    description: 'Click New Project to walk the 6-step wizard. Fondok extracts the OM and T-12 automatically.',
  },
  {
    id: 'project-card',
    selector: '[data-tour="project-card"]',
    title: 'Open a deal workspace',
    description: 'Click any deal card to enter Data Room, Validation, Overview, Returns, and IC memo.',
  },
  {
    id: 'methodology',
    selector: '[data-tour="methodology"]',
    title: 'See how Fondok thinks',
    description: 'Every formula, default, and precedence chain — documented and link-anchored.',
  },
  {
    id: 'settings',
    selector: '[data-tour="settings"]',
    title: 'Customize at any time',
    description: 'Account, team, and a master switch to silence coach marks once you have the hang of it.',
  },
];

function readBool(key: string): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(key) === 'true';
  } catch {
    return false;
  }
}

function writeBool(key: string, val: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    if (val) window.localStorage.setItem(key, 'true');
    else window.localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

// Pulse the target element with a ring for 2s. Pure DOM — no portal,
// no popover, no blocking overlay.
function pulse(selector: string): void {
  if (typeof document === 'undefined') return;
  const el = document.querySelector(selector) as HTMLElement | null;
  if (!el) return;
  // Scroll into view if needed.
  el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
  const previous = el.style.boxShadow;
  const previousTransition = el.style.transition;
  el.style.transition = 'box-shadow 200ms ease-out';
  el.style.boxShadow = '0 0 0 2px rgba(37, 99, 235, 0.85), 0 0 0 6px rgba(37, 99, 235, 0.15)';
  el.classList.add('fondok-gs-pulse');
  setTimeout(() => {
    el.style.boxShadow = previous;
    setTimeout(() => {
      el.style.transition = previousTransition;
    }, 250);
    el.classList.remove('fondok-gs-pulse');
  }, 2000);
}

export function GettingStartedSidebar() {
  const [mounted, setMounted] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [doneSteps, setDoneSteps] = useState<Record<string, boolean>>({});

  useEffect(() => {
    setMounted(true);
    setDismissed(readBool(DISMISS_KEY));
    setCollapsed(readBool(COLLAPSE_KEY));
    const next: Record<string, boolean> = {};
    STEPS.forEach((s) => {
      next[s.id] = readBool(STEP_KEY(s.id));
    });
    setDoneSteps(next);
  }, []);

  const toggleStep = useCallback((id: string) => {
    setDoneSteps((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      writeBool(STEP_KEY(id), next[id]);
      return next;
    });
  }, []);

  const toggleCollapse = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      writeBool(COLLAPSE_KEY, next);
      return next;
    });
  }, []);

  const dismiss = useCallback(() => {
    setDismissed(true);
    writeBool(DISMISS_KEY, true);
  }, []);

  const onShowMe = useCallback((step: Step) => {
    pulse(step.selector);
    // Auto-check the step once the user has been pointed at it.
    setDoneSteps((prev) => {
      if (prev[step.id]) return prev;
      writeBool(STEP_KEY(step.id), true);
      return { ...prev, [step.id]: true };
    });
  }, []);

  if (!mounted) return null;
  if (dismissed) return null;
  if (!hintsEnabled()) return null;

  const completedCount = STEPS.reduce(
    (n, s) => n + (doneSteps[s.id] ? 1 : 0),
    0,
  );
  const allDone = completedCount === STEPS.length;

  // Collapsed FAB-style pill — quiet, one-click expand.
  if (collapsed) {
    return (
      <div className="fixed bottom-4 right-4 z-30 motion-reduce:animate-none">
        <button
          type="button"
          onClick={toggleCollapse}
          className="inline-flex items-center gap-2 px-3 py-2 rounded-full bg-white border border-border shadow-card hover:shadow-card-hover text-[12.5px] text-ink-900 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          aria-label="Expand getting started checklist"
        >
          <Sparkles size={13} className="text-brand-500" aria-hidden="true" />
          <span>Getting started</span>
          <span className="tabular-nums text-[11px] px-1.5 py-0.5 rounded-full bg-brand-50 text-brand-700">
            {completedCount}/{STEPS.length}
          </span>
        </button>
      </div>
    );
  }

  return (
    <aside
      role="complementary"
      aria-label="Getting started checklist"
      className="fixed bottom-4 right-4 z-30 w-[320px] bg-white border border-border rounded-lg shadow-card motion-reduce:animate-none"
    >
      <header className="flex items-center justify-between gap-2 px-3.5 py-2.5 border-b border-border">
        <div className="flex items-center gap-2 min-w-0">
          <Sparkles size={13} className="text-brand-500 flex-shrink-0" aria-hidden="true" />
          <h3 className="text-[13px] font-semibold text-ink-900 leading-none">
            Getting started
          </h3>
          <span className="tabular-nums text-[11px] px-1.5 py-0.5 rounded-full bg-brand-50 text-brand-700">
            {completedCount}/{STEPS.length}
          </span>
        </div>
        <div className="flex items-center gap-0.5 flex-shrink-0">
          <button
            type="button"
            onClick={toggleCollapse}
            className="text-ink-500 hover:text-ink-900 rounded p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            aria-label="Collapse checklist"
          >
            <ChevronDown size={13} aria-hidden="true" />
          </button>
          <button
            type="button"
            onClick={dismiss}
            className="text-ink-500 hover:text-ink-900 rounded p-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
            aria-label="Dismiss getting started"
          >
            <X size={13} aria-hidden="true" />
          </button>
        </div>
      </header>

      <ul className="py-1 max-h-[55vh] overflow-y-auto">
        {STEPS.map((s) => {
          const done = !!doneSteps[s.id];
          return (
            <li key={s.id}>
              <StepRow step={s} done={done} onToggle={() => toggleStep(s.id)} onShowMe={() => onShowMe(s)} />
            </li>
          );
        })}
      </ul>

      {allDone && (
        <div className="px-3.5 py-2 border-t border-border bg-success-50/40 text-[12px] text-success-700 leading-relaxed">
          You're all set — close this when you're ready.
        </div>
      )}
    </aside>
  );
}

function StepRow({
  step,
  done,
  onToggle,
  onShowMe,
}: {
  step: Step;
  done: boolean;
  onToggle: () => void;
  onShowMe: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="px-3.5 py-2 hover:bg-ink-50/60 transition-colors">
      <div className="flex items-start gap-2">
        <button
          type="button"
          onClick={onToggle}
          className="flex-shrink-0 mt-0.5 text-brand-500 hover:text-brand-700 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          aria-pressed={done}
          aria-label={done ? `Mark ${step.title} incomplete` : `Mark ${step.title} complete`}
        >
          {done ? (
            <CheckCircle2 size={14} aria-hidden="true" />
          ) : (
            <Circle size={14} aria-hidden="true" />
          )}
        </button>
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex-1 text-left min-w-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded"
          aria-expanded={expanded}
        >
          <div className="flex items-center gap-1.5">
            <span
              className={cn(
                'text-[12.5px] font-medium leading-tight',
                done ? 'text-ink-500 line-through' : 'text-ink-900',
              )}
            >
              {step.title}
            </span>
            {expanded ? (
              <ChevronUp size={11} className="text-ink-400" aria-hidden="true" />
            ) : (
              <ChevronDown size={11} className="text-ink-400" aria-hidden="true" />
            )}
          </div>
          {expanded && (
            <p className="mt-1 text-[11.5px] text-ink-500 leading-relaxed">
              {step.description}
            </p>
          )}
        </button>
      </div>
      {expanded && (
        <div className="mt-1.5 ml-6">
          <button
            type="button"
            onClick={onShowMe}
            className="text-[11px] text-brand-700 hover:text-brand-900 font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 rounded px-1"
          >
            Show me →
          </button>
        </div>
      )}
    </div>
  );
}

export default GettingStartedSidebar;
