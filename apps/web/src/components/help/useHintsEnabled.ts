'use client';

/**
 * Global "Show contextual coach marks" preference.
 *
 * Stored under `fondok:coachmarks:disabled` so a single localStorage key
 * gates every CoachMark + GettingStartedSidebar render. We listen to the `storage` event
 * so toggling in one tab updates every open tab — and dispatch a custom
 * event for same-tab updates because `storage` only fires cross-tab.
 */

import { useCallback, useEffect, useState } from 'react';

const KEY = 'fondok:coachmarks:disabled';
/** Fired (same tab) whenever any hint preference changes. */
export const HINTS_CHANGED_EVENT = 'fondok:hints-changed';
const EVENT = HINTS_CHANGED_EVENT;

function readDisabled(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return window.localStorage.getItem(KEY) === 'true';
  } catch {
    return false;
  }
}

export function useHintsEnabled(): {
  enabled: boolean;
  setEnabled: (v: boolean) => void;
} {
  const [disabled, setDisabled] = useState(false);

  useEffect(() => {
    setDisabled(readDisabled());
    const sync = () => setDisabled(readDisabled());
    window.addEventListener('storage', sync);
    window.addEventListener(EVENT, sync as EventListener);
    return () => {
      window.removeEventListener('storage', sync);
      window.removeEventListener(EVENT, sync as EventListener);
    };
  }, []);

  const setEnabled = useCallback((v: boolean) => {
    try {
      window.localStorage.setItem(KEY, v ? 'false' : 'true');
      window.dispatchEvent(new Event(EVENT));
    } catch {
      // ignore
    }
    setDisabled(!v);
  }, []);

  return { enabled: !disabled, setEnabled };
}

/** Synchronous read for components that need a one-shot check (e.g. the
 *  GettingStartedSidebar before mount). Returns true if hints should render. */
export function hintsEnabled(): boolean {
  return !readDisabled();
}

/** Reset every dismissed coach mark — `fondok:coachmark:*` keys. */
export function resetAllCoachMarks(): number {
  if (typeof window === 'undefined') return 0;
  let removed = 0;
  try {
    const ls = window.localStorage;
    const toRemove: string[] = [];
    for (let i = 0; i < ls.length; i += 1) {
      const k = ls.key(i);
      if (k && k.startsWith('fondok:coachmark:')) toRemove.push(k);
      if (k && k.startsWith('fondok:tour:')) toRemove.push(k);
    }
    toRemove.forEach((k) => {
      ls.removeItem(k);
      removed += 1;
    });
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // ignore
  }
  return removed;
}

/** R-074 — "Hide all hints": the same global switch as Settings → Hints. */
export function hideAllHints(): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(KEY, 'true');
  } catch {
    // ignore — storage unavailable (private mode etc.)
  }
  try {
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // ignore
  }
}

/**
 * R-074 — "Show hints again": turn hints back on AND forget every per-hint
 * dismissal — coach marks (`fondok:coachmark:*`), tours (`fondok:tour:*`) and
 * intro cards (`fondok-intro-*`, kept in localStorage plus a cookie fallback).
 * Returns how many dismissals were cleared.
 */
export function showHintsAgain(): number {
  if (typeof window === 'undefined') return 0;
  let removed = 0;
  try {
    const ls = window.localStorage;
    ls.setItem(KEY, 'false');
    const toRemove: string[] = [];
    for (let i = 0; i < ls.length; i += 1) {
      const k = ls.key(i);
      if (k && (k.startsWith('fondok:coachmark:') || k.startsWith('fondok:tour:') || k.startsWith('fondok-intro-'))) {
        toRemove.push(k);
      }
    }
    toRemove.forEach((k) => {
      ls.removeItem(k);
      removed += 1;
    });
  } catch {
    // ignore
  }
  try {
    document.cookie.split(';').forEach((c) => {
      const name = c.split('=')[0]?.trim();
      if (name && name.startsWith('fondok-intro-')) {
        document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`;
      }
    });
  } catch {
    // ignore
  }
  try {
    window.dispatchEvent(new Event(EVENT));
  } catch {
    // ignore
  }
  return removed;
}
