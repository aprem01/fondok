'use client';

import { useEffect, useRef, useState } from 'react';

/**
 * Adds a 600ms `value-flash` highlight when `value` changes from its
 * previous render. Used on KPI cards across the engine tabs so live
 * updates from the worker feel visceral instead of silently swapping.
 *
 * Skips the initial render so we don't flash on first mount.
 */
export function useFlash<T>(value: T, durationMs = 600): boolean {
  const [flash, setFlash] = useState(false);
  const previous = useRef<T>(value);
  const initialized = useRef(false);

  useEffect(() => {
    if (!initialized.current) {
      initialized.current = true;
      previous.current = value;
      return;
    }
    if (previous.current !== value) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), durationMs);
      previous.current = value;
      return () => clearTimeout(t);
    }
  }, [value, durationMs]);

  return flash;
}
