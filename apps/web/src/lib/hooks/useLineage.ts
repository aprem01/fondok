'use client';

/**
 * useLineage — on-demand loader + walker for a deal's value lineage graph
 * (Phase 2.4: "walk any number down to the document page it came from").
 *
 * Deliberately NOT a provider like ``useDealProvenance`` / ``useValueTrace``:
 * the lineage graph is the heaviest provenance payload we serve, and an
 * analyst only ever needs it when they ask a specific number where it came
 * from. So nothing is fetched on tab mount — the hook stays idle until a
 * consumer is ``enabled`` (the drawer opening) or calls ``load()``.
 *
 * The response is cached per ``(dealId, runId)`` at module scope, so
 * re-opening the drawer on the same run is instant and a second consumer on
 * the same deal shares the in-flight request.
 *
 * ``GET /deals/{id}/lineage`` is additive and may not exist yet — ``api``
 * resolves ``null`` on 404/405/501, so ``record === null && settled`` is the
 * ordinary "no lineage on this build" state, never an error.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, isWorkerConnected } from '@/lib/api';
import type { LineageEdge, LineageNode, LineageRecord, LineageRefusal } from '@/lib/api';
import { isReasonCode } from '@/lib/ontology/reasons.generated';

/* ─────────────────────────── walk shapes ─────────────────────────── */

/** One step of a walk: the node, and the edge that reached it. */
export interface LineageStep {
  node: LineageNode;
  /** Edge from the previous step to this node; null on the root. */
  edge: LineageEdge | null;
  /** 0 for the root, +1 per hop — lets the drawer indent branches. */
  depth: number;
  /** True when the edge pointed at a node the record does not carry. */
  missing: boolean;
  /** The refusal that explains this step's dash, when one applies. */
  refusal: LineageRefusal | null;
}

/** The ordered chain from one root down to its terminal nodes. */
export interface LineageWalk {
  rootId: string;
  steps: LineageStep[];
  /** Refusals that apply somewhere in this chain, in record order. */
  unresolved: LineageRefusal[];
  /** The record's staleness, carried so the drawer can notice it. */
  stale: boolean;
  runId: string | null;
}

export interface UseLineageOptions {
  /** Cache/fetch key. Omit for "whatever run the worker serves". */
  runId?: string | null;
  /** Fetch when true. Defaults to false — nothing loads on tab mount. */
  enabled?: boolean;
}

export interface UseLineageResult {
  record: LineageRecord | null;
  loading: boolean;
  error: string | null;
  /** True once a fetch has finished (success OR failure), or when there is
   *  nothing to fetch (no worker / mock deal). Distinguishes "still
   *  checking" from "this build serves no lineage". */
  settled: boolean;
  /** Imperatively start the fetch (for consumers that are not ``enabled``). */
  load: () => void;
  /** Ordered chain from ``rootId`` down to its terminals, or null when the
   *  record knows nothing about that root. */
  walk: (rootId: string) => LineageWalk | null;
}

/* ─────────────────────────── module cache ─────────────────────────── */

const cacheKey = (dealId: string, runId?: string | null) => `${dealId}::${runId ?? ''}`;

const RESOLVED = new Map<string, LineageRecord | null>();
const PENDING = new Map<string, Promise<LineageRecord | null>>();

/** Drop every cached lineage payload. Exposed for tests + a hard refresh. */
export function clearLineageCache(): void {
  RESOLVED.clear();
  PENDING.clear();
}

/** Defensive read of a partially-populated payload — the endpoint is being
 *  built in parallel, so never assume a field survived the wire. */
function normalizeRecord(r: LineageRecord): LineageRecord {
  return {
    ...r,
    roots: Array.isArray(r.roots) ? r.roots : [],
    nodes: (Array.isArray(r.nodes) ? r.nodes : []).map((n) => ({
      ...n,
      value: n?.value ?? null,
      unit: n?.unit ?? null,
      concept: n?.concept ?? null,
      source: n?.source ?? null,
      state: n?.state ?? null,
      reason: n?.reason ?? null,
      meta: n?.meta ?? {},
    })),
    edges: (Array.isArray(r.edges) ? r.edges : []).map((e) => ({
      ...e,
      formula: e?.formula ?? null,
    })),
    unresolved: Array.isArray(r.unresolved) ? r.unresolved : [],
    stale: r.stale === true,
  };
}

function fetchLineage(dealId: string, runId?: string | null): Promise<LineageRecord | null> {
  const key = cacheKey(dealId, runId);
  const pending = PENDING.get(key);
  if (pending) return pending;
  const p = api.deals
    .lineage(dealId)
    .then((r) => {
      const rec = r ? normalizeRecord(r) : null;
      RESOLVED.set(key, rec);
      // Also key it by the run the worker actually served, so a consumer
      // asking for that exact run hits the cache instead of refetching.
      if (rec?.run_id) RESOLVED.set(cacheKey(dealId, rec.run_id), rec);
      return rec;
    })
    .finally(() => {
      PENDING.delete(key);
    });
  PENDING.set(key, p);
  return p;
}

/* ─────────────────────────── id helpers ─────────────────────────── */

const ID_KINDS: Record<string, LineageNode['kind']> = {
  kpi: 'kpi',
  engine: 'engine_value',
  assumption: 'assumption',
  field: 'extracted_field',
  doc: 'document',
  page: 'page',
  override: 'override',
  seed: 'seed',
  benchmark: 'benchmark',
  memo: 'memo_section',
};

/** Kind implied by a namespaced node id — for links the record carries no
 *  node for. Falls back to `engine_value`. */
export function kindFromId(id: string): LineageNode['kind'] {
  const ns = id.split(':', 1)[0] ?? '';
  return ID_KINDS[ns] ?? 'engine_value';
}

/** A readable label for a link with no node: "kpi:returns.levered_irr"
 *  reads as "returns.levered_irr". Never an empty string. */
export function labelFromId(id: string): string {
  const rest = id.includes(':') ? id.slice(id.indexOf(':') + 1) : id;
  return rest.trim() || id;
}

/** ``doc:<document_id>`` / ``page:<document_id>:<n>`` → the document id. */
export function documentIdOf(node: Pick<LineageNode, 'id' | 'meta'>): string | null {
  const fromMeta = node.meta?.['document_id'];
  if (typeof fromMeta === 'string' && fromMeta) return fromMeta;
  const parts = node.id.split(':');
  if ((parts[0] === 'doc' || parts[0] === 'page') && parts[1]) return parts[1];
  return null;
}

/** 1-based page number for a ``page:`` node, from meta or the id tail. */
export function pageNumberOf(node: Pick<LineageNode, 'id' | 'meta'>): number | null {
  const fromMeta = node.meta?.['page'];
  if (typeof fromMeta === 'number' && Number.isFinite(fromMeta)) return fromMeta;
  const parts = node.id.split(':');
  if (parts[0] === 'page' && parts[2] != null) {
    const n = Number(parts[2]);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

/* ─────────────────────────── the walk ─────────────────────────── */

function refusalApplies(r: LineageRefusal, node: Pick<LineageNode, 'id' | 'concept' | 'meta'>): boolean {
  if (r.concept && (r.concept === node.concept || r.concept === node.id)) return true;
  if (r.document_id) {
    if (node.id === `doc:${r.document_id}`) return true;
    if (node.id.startsWith(`page:${r.document_id}:`)) return true;
    if (documentIdOf(node) === r.document_id) return true;
  }
  return false;
}

/**
 * Depth-first walk from ``rootId``, descending every edge whose ``src`` is
 * the current node — edges read "src <rel> dst", so ``dst`` is always the
 * step further towards the document. Edge order is preserved, a node is
 * never expanded twice (cycle guard), and an edge pointing at a node the
 * record does not carry still produces a step — a dash with its reason,
 * never a blank row.
 */
export function walkLineage(record: LineageRecord | null, rootId: string): LineageWalk | null {
  if (!record || !rootId) return null;

  const byId = new Map<string, LineageNode>();
  for (const n of record.nodes) byId.set(n.id, n);
  const out = new Map<string, LineageEdge[]>();
  for (const e of record.edges) {
    const list = out.get(e.src);
    if (list) list.push(e);
    else out.set(e.src, [e]);
  }

  const synth = (id: string): LineageNode => ({
    id,
    kind: kindFromId(id),
    label: labelFromId(id),
    value: null,
    unit: null,
    concept: null,
    source: null,
    state: null,
    reason: null,
    meta: {},
  });

  const known =
    byId.has(rootId) ||
    out.has(rootId) ||
    record.roots.includes(rootId) ||
    record.edges.some((e) => e.dst === rootId) ||
    record.unresolved.some((r) => r.concept === rootId);
  if (!known) return null;

  const steps: LineageStep[] = [];
  const applied: LineageRefusal[] = [];
  const seen = new Set<string>([rootId]);

  const visit = (id: string, edge: LineageEdge | null, depth: number) => {
    const node = byId.get(id);
    const resolved = node ?? synth(id);
    // A record-level refusal wins (it carries the detail); a node-level
    // `reason` is the fallback so a dashed node is never a blank row.
    const fromRecord = record.unresolved.find((r) => refusalApplies(r, resolved)) ?? null;
    const refusal: LineageRefusal | null =
      fromRecord ??
      (resolved.reason && isReasonCode(resolved.reason) ? { code: resolved.reason } : null);
    steps.push({ node: resolved, edge, depth, missing: !node, refusal });
    if (fromRecord && !applied.includes(fromRecord)) applied.push(fromRecord);
    if (!node) return; // a link with no node is terminal by definition
    for (const e of out.get(id) ?? []) {
      if (seen.has(e.dst)) continue;
      seen.add(e.dst);
      visit(e.dst, e, depth + 1);
    }
  };

  visit(rootId, null, 0);

  return { rootId, steps, unresolved: applied, stale: record.stale, runId: record.run_id };
}

/* ─────────────────────────── the hook ─────────────────────────── */

export function useLineage(
  dealId: string,
  { runId, enabled = false }: UseLineageOptions = {},
): UseLineageResult {
  const key = cacheKey(dealId, runId);
  // A mock (numeric) project id or an unconfigured worker has nothing to
  // fetch — those settle immediately with no record, exactly like a 404.
  const inert = !isWorkerConnected() || !dealId || /^\d+$/.test(dealId);
  const cached = RESOLVED.has(key) ? (RESOLVED.get(key) ?? null) : undefined;

  const [record, setRecord] = useState<LineageRecord | null>(cached ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settled, setSettled] = useState(inert || cached !== undefined);
  const [armed, setArmed] = useState(enabled);
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = useCallback(() => setArmed(true), []);
  useEffect(() => {
    if (enabled) setArmed(true);
  }, [enabled]);

  // Reset when the (deal, run) under the hook changes.
  useEffect(() => {
    const hit = RESOLVED.has(key) ? (RESOLVED.get(key) ?? null) : undefined;
    setRecord(hit ?? null);
    setError(null);
    setLoading(false);
    setSettled(inert || hit !== undefined);
  }, [key, inert]);

  useEffect(() => {
    if (!armed || inert) return;
    if (RESOLVED.has(key)) {
      setRecord(RESOLVED.get(key) ?? null);
      setSettled(true);
      return;
    }
    let cancelled = false;
    setLoading(true);
    fetchLineage(dealId, runId)
      .then((r) => {
        if (cancelled || !alive.current) return;
        setRecord(r);
        setError(null);
      })
      .catch((e: unknown) => {
        if (cancelled || !alive.current) return;
        // A genuine failure (network / 5xx / timeout). A 404 never lands
        // here — the api layer turns that into a null record.
        setError(e instanceof Error ? e.message : String(e));
        setRecord(null);
      })
      .finally(() => {
        if (cancelled || !alive.current) return;
        setLoading(false);
        setSettled(true);
      });
    return () => {
      cancelled = true;
    };
  }, [armed, inert, key, dealId, runId]);

  const walk = useCallback((rootId: string) => walkLineage(record, rootId), [record]);

  return useMemo(
    () => ({ record, loading, error, settled, load, walk }),
    [record, loading, error, settled, load, walk],
  );
}
