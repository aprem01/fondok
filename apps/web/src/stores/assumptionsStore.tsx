'use client';
// Lightweight React Context store for the live underwriting assumptions.
// useAssumptions() returns the current assumptions, a setter, and the
// engine-computed model (memoized). No external state lib.

import {
  createContext, useContext, useMemo, useState, useCallback, type ReactNode,
} from 'react';
import {
  Assumptions, EngineOutputs, runModel, KIMPTON_ASSUMPTIONS,
} from '@/lib/engines';

interface AssumptionsContextValue {
  assumptions: Assumptions;
  setAssumption: <K extends keyof Assumptions>(key: K, value: Assumptions[K]) => void;
  setAssumptions: (next: Partial<Assumptions>) => void;
  resetAssumptions: () => void;
  model: EngineOutputs;
}

const AssumptionsContext = createContext<AssumptionsContextValue | null>(null);

export function AssumptionsProvider({
  children,
  initial = KIMPTON_ASSUMPTIONS,
}: {
  children: ReactNode;
  initial?: Assumptions;
}) {
  const [assumptions, setAssumptionsState] = useState<Assumptions>(initial);

  const setAssumption = useCallback(<K extends keyof Assumptions>(key: K, value: Assumptions[K]) => {
    setAssumptionsState(prev => ({ ...prev, [key]: value }));
  }, []);

  const setAssumptions = useCallback((next: Partial<Assumptions>) => {
    setAssumptionsState(prev => ({ ...prev, ...next }));
  }, []);

  const resetAssumptions = useCallback(() => {
    setAssumptionsState(initial);
  }, [initial]);

  const model = useMemo(() => runModel(assumptions), [assumptions]);

  const value = useMemo(
    () => ({ assumptions, setAssumption, setAssumptions, resetAssumptions, model }),
    [assumptions, setAssumption, setAssumptions, resetAssumptions, model],
  );

  return (
    <AssumptionsContext.Provider value={value}>{children}</AssumptionsContext.Provider>
  );
}

export function useAssumptions(): AssumptionsContextValue {
  const ctx = useContext(AssumptionsContext);
  if (!ctx) {
    throw new Error('useAssumptions must be used inside <AssumptionsProvider>');
  }
  return ctx;
}

/** Optional accessor — returns null if no provider, never throws. Useful for
 *  components that may render outside a Kimpton-only provider. */
export function useAssumptionsOptional(): AssumptionsContextValue | null {
  return useContext(AssumptionsContext);
}
