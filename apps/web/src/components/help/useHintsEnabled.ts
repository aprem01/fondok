'use client';

/**
 * Global "Show contextual coach marks" preference.
 *
 * Stored under `fondok:coachmarks:disabled` so a single localStorage key
 * gates every CoachMark + AppTour render. We listen to the `storage` event
 * so toggling in one tab updates every open tab — and dispatch a custom
 * event for same-tab updates because `storage` only fires cross-tab.
 */

import { useCallback, useEffect, useState } from 'react';

const KEY = 'fondok:coachmarks:disabled';
const EVENT = 'fondok:hints-changed';

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

/** Synchronous read for components that need a one-shot check (e.g. AppTour
 *  before mount). Returns true if hints should render. */
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
