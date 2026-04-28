'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  api,
  EngineName,
  EngineOutputResponse,
  EngineOutputsResponse,
  isWorkerConnected,
} from '@/lib/api';

// ────────────────────────────────────────────────────────────────────
// Lightweight pubsub so `useEngineRun` can poke every mounted
// `useEngineOutputs(dealId)` consumer to re-fetch when a run completes.
// Avoids a heavier React context for what's effectively an event bus.
// ────────────────────────────────────────────────────────────────────

type Listener = () => void;
const listeners = new Map<string, Set<Listener>>();

export function subscribeEngineOutputs(dealId: string, fn: Listener): () => void {
  const key = String(dealId);
  let bucket = listeners.get(key);
  if (!bucket) {
    bucket = new Set();
    listeners.set(key, bucket);
  }
  bucket.add(fn);
  return () => {
    bucket?.delete(fn);
    if (bucket && bucket.size === 0) listeners.delete(key);
  };
}

export function notifyEngineOutputsChanged(dealId: string): void {
  const bucket = listeners.get(String(dealId));
  bucket?.forEach((fn) => fn());
}

// ────────────────────────────────────────────────────────────────────
// Run history (in-memory, per-tab) — populated as runs complete.
// Surfaced by the "Last run" footer + run history popover.
// ────────────────────────────────────────────────────────────────────

export interface EngineRunRecord {
  runId: string;
  startedAt: Date;
  completedAt: Date | null;
  engineCount: number;
  status: 'running' | 'complete' | 'failed';
  summary: string;
  costUsd: number;
}

const runHistory = new Map<string, EngineRunRecord[]>();
const historyListeners = new Map<string, Set<Listener>>();

export function recordEngineRun(dealId: string, run: EngineRunRecord): void {
  const key = String(dealId);
  const list = runHistory.get(key) ?? [];
  // Replace existing record if same runId, otherwise prepend.
  const existing = list.findIndex((r) => r.runId === run.runId);
  if (existing >= 0) {
    list[existing] = run;
  } else {
    list.unshift(run);
  }
  runHistory.set(key, list.slice(0, 20));
  historyListeners.get(key)?.forEach((fn) => fn());
}

export function getEngineRunHistory(dealId: string): EngineRunRecord[] {
  return runHistory.get(String(dealId)) ?? [];
}

function subscribeHistory(dealId: string, fn: Listener): () => void {
  const key = String(dealId);
  let bucket = historyListeners.get(key);
  if (!bucket) {
    bucket = new Set();
    historyListeners.set(key, bucket);
  }
  bucket.add(fn);
  return () => {
    bucket?.delete(fn);
    if (bucket && bucket.size === 0) historyListeners.delete(key);
  };
}

// ────────────────────────────────────────────────────────────────────
// Hooks
// ────────────────────────────────────────────────────────────────────

export interface UseEngineOutputsResult {
  outputs: EngineOutputsResponse | null;
  /** Previous outputs snapshot — used to compute "what just changed". */
  previous: EngineOutputsResponse | null;
  loading: boolean;
  lastRunAt: Date | null;
  refresh: () => Promise<void>;
}

export function useEngineOutputs(dealId: string | number): UseEngineOutputsResult {
  const [outputs, setOutputs] = useState<EngineOutputsResponse | null>(null);
  const previousRef = useRef<EngineOutputsResponse | null>(null);
  const [previous, setPrevious] = useState<EngineOutputsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [lastRunAt, setLastRunAt] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    if (!isWorkerConnected()) return;
    if (!dealId) return;
    setLoading(true);
    try {
      const data = await api.engines.getAll(String(dealId));
      // Snapshot previous before swapping.
      setPrevious(previousRef.current);
      previousRef.current = data;
      setOutputs(data);
      const latest = Math.max(
        0,
        ...Object.values(data.engines || {}).map((o: EngineOutputResponse) =>
          o?.completed_at ? new Date(o.completed_at).getTime() : 0,
        ),
      );
      if (latest > 0) setLastRunAt(new Date(latest));
    } catch {
      // Silent — fall back to mock data in callers.
    } finally {
      setLoading(false);
    }
  }, [dealId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Cross-component refresh: when `useEngineRun` finishes, every mounted
  // consumer for this deal re-fetches.
  useEffect(() => {
    if (!dealId) return;
    return subscribeEngineOutputs(String(dealId), () => {
      void refresh();
    });
  }, [dealId, refresh]);

  return { outputs, previous, loading, lastRunAt, refresh };
}

export function useEngineRunHistory(dealId: string | number): EngineRunRecord[] {
  const [, force] = useState(0);
  useEffect(() => {
    if (!dealId) return;
    return subscribeHistory(String(dealId), () => force((n) => n + 1));
  }, [dealId]);
  return getEngineRunHistory(String(dealId));
}

// ────────────────────────────────────────────────────────────────────
// Field accessor — outputs from the worker are loosely-typed JSON, so
// we expose a tiny safe getter that callers can use to override mock
// values when the worker has data.
// ────────────────────────────────────────────────────────────────────

export function getEngineField<T = unknown>(
  outputs: EngineOutputsResponse | null,
  engine: EngineName,
  ...path: string[]
): T | undefined {
  if (!outputs) return undefined;
  const row = outputs.engines?.[engine];
  if (!row || !row.outputs) return undefined;
  let cur: unknown = row.outputs;
  for (const p of path) {
    if (cur && typeof cur === 'object' && p in (cur as Record<string, unknown>)) {
      cur = (cur as Record<string, unknown>)[p];
    } else {
      return undefined;
    }
  }
  return cur as T;
}
