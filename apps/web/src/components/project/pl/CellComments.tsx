'use client';

/**
 * FON-41 E-011 — Excel-like comments on individual P&L cells.
 *
 * Tester finding: "No Excel-like way to add a note or comment to an individual
 * financial cell during review." Expected: cell-level comments, comment
 * history, and commented cells easy to revisit or filter.
 *
 *   • <CellCommentsProvider> — one per P&L view; loads the deal's comments once
 *     (scoped to that view's key prefix) and hosts the thread side panel.
 *   • <CommentMarker> — the small speech-bubble on a cell: count + amber while
 *     the thread has open comments, grey once resolved, hover-only "add" when
 *     the cell has none.
 *   • <CommentedCellsToggle> — the "Commented cells" filter. On, the host view
 *     shows only rows with a commented cell (via `useCommentFilter`) and a list
 *     of every commented cell to jump back into its thread.
 *
 * Keys come from `lib/cellComments.ts` — a stable id of the cell (document +
 * field, or engine path + year), never a screen position. Comments are stored
 * by the worker (`/deals/{id}/comments`), tenant-scoped; resolving never
 * deletes, so the history stays.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { CheckCircle2, Loader2, MessageSquare, RotateCcw, X } from 'lucide-react';
import { api, type CellComment } from '@/lib/api';
import { groupThreads, type CommentThread } from '@/lib/cellComments';
import { cn } from '@/lib/format';

type Scope = 'hist' | 'proj';

interface CommentsCtx {
  threads: Map<string, CommentThread>;
  available: boolean;
  filterOn: boolean;
  setFilterOn: (on: boolean) => void;
  openThread: (cellKey: string, label: string) => void;
}

const Ctx = createContext<CommentsCtx | null>(null);

export interface CellCommentsState extends CommentsCtx {
  dealId: string;
  comments: CellComment[];
  setComments: (next: CellComment[]) => void;
  active: { key: string; label: string } | null;
  closeThread: () => void;
}

/**
 * The comment state for one P&L view. The host view owns it (so it can filter
 * its own rows on `threads` / `filterOn`) and hands it to
 * <CellCommentsProvider>, which shares it with markers and the thread panel.
 */
export function useCellCommentsState(dealId: string, scope: Scope): CellCommentsState {
  const [comments, setComments] = useState<CellComment[]>([]);
  // False when the worker could not serve the comments (offline / older
  // worker): markers and the filter then render nothing rather than a control
  // that silently does nothing.
  const [available, setAvailable] = useState(false);
  const [filterOn, setFilterOn] = useState(false);
  const [active, setActive] = useState<{ key: string; label: string } | null>(null);

  const load = useCallback(async () => {
    if (!dealId) return;
    try {
      const list = await api.comments.list(dealId);
      setComments(Array.isArray(list) ? list : []);
      setAvailable(true);
    } catch {
      setAvailable(false);
    }
  }, [dealId]);
  useEffect(() => { void load(); }, [load]);

  const threads = useMemo(() => {
    const all = groupThreads(comments);
    for (const k of [...all.keys()]) if (!k.startsWith(`${scope}:`)) all.delete(k);
    return all;
  }, [comments, scope]);

  return useMemo<CellCommentsState>(
    () => ({
      dealId,
      comments,
      setComments,
      threads,
      available,
      filterOn,
      setFilterOn,
      active,
      openThread: (key, label) => setActive({ key, label }),
      closeThread: () => setActive(null),
    }),
    [dealId, comments, threads, available, filterOn, active],
  );
}

export function CellCommentsProvider({
  state,
  children,
}: {
  state: CellCommentsState;
  children: ReactNode;
}) {
  return (
    <Ctx.Provider value={state}>
      {children}
      {state.active && (
        <CommentThreadPanel
          dealId={state.dealId}
          cellKey={state.active.key}
          label={state.active.label}
          thread={state.threads.get(state.active.key) ?? null}
          onClose={state.closeThread}
          onChanged={state.setComments}
          comments={state.comments}
        />
      )}
    </Ctx.Provider>
  );
}

/** `{ has(key) }` while the "Commented cells" filter is on, else `null`. */
export function useCommentFilter(): { has: (cellKey: string) => boolean } | null {
  const ctx = useContext(Ctx);
  if (!ctx || !ctx.available || !ctx.filterOn) return null;
  return { has: (k) => ctx.threads.has(k) };
}

export function CommentMarker({ cellKey, label }: { cellKey: string | null | undefined; label: string }) {
  const ctx = useContext(Ctx);
  if (!ctx || !ctx.available || !cellKey) return null;
  const t = ctx.threads.get(cellKey);
  const n = t?.comments.length ?? 0;
  return (
    <button
      type="button"
      data-testid={`comment-marker-${cellKey}`}
      data-comment-state={t ? (t.open > 0 ? 'open' : 'resolved') : 'none'}
      aria-label={t ? `${n} comment${n === 1 ? '' : 's'} on ${label} — open the thread` : `Comment on ${label}`}
      title={t ? `${n} comment${n === 1 ? '' : 's'}${t.open > 0 ? '' : ' · resolved'}` : 'Add a comment'}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        ctx.openThread(cellKey, label);
      }}
      className={cn(
        'shrink-0 inline-flex items-center gap-0.5 rounded px-0.5 text-[9.5px] font-semibold leading-none tabular-nums transition-opacity',
        t
          ? t.open > 0
            ? 'text-amber-700 bg-amber-100 hover:bg-amber-200'
            : 'text-ink-500 bg-ink-100 hover:bg-ink-200'
          : 'text-ink-400 opacity-0 group-hover:opacity-100 focus:opacity-100 hover:text-brand-700',
      )}
    >
      <MessageSquare size={10} aria-hidden="true" />
      {t ? n : null}
    </button>
  );
}

/** The "Commented cells" filter toggle + the list of commented cells. */
export function CommentedCellsToggle() {
  const ctx = useContext(Ctx);
  if (!ctx || !ctx.available) return null;
  const threads = [...ctx.threads.values()].sort((a, b) => b.lastAt.localeCompare(a.lastAt));
  const open = threads.filter((t) => t.open > 0).length;
  return (
    <span className="relative inline-flex">
      <button
        type="button"
        data-testid="commented-cells-toggle"
        aria-pressed={ctx.filterOn}
        onClick={() => ctx.setFilterOn(!ctx.filterOn)}
        title="Show only the rows with a commented cell, and list every comment thread"
        className={cn(
          'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11.5px] font-medium border transition-colors',
          ctx.filterOn ? 'border-ink-900 bg-ink-900 text-white' : 'border-border text-ink-600 hover:text-ink-900',
        )}
      >
        <MessageSquare size={11} aria-hidden="true" />
        Commented cells
        <span className="tabular-nums">· {threads.length}</span>
        {open > 0 && (
          <span className={cn('tabular-nums rounded-full px-1.5', ctx.filterOn ? 'bg-amber-400 text-ink-900' : 'bg-amber-100 text-amber-800')}>
            {open} open
          </span>
        )}
      </button>
      {ctx.filterOn && (
        <div
          role="list"
          data-testid="commented-cells-list"
          className="absolute right-0 top-full mt-1 z-40 w-80 max-h-80 overflow-auto rounded-md border border-border bg-white shadow-lg"
        >
          {threads.length === 0 ? (
            <p className="px-3 py-2.5 text-[11.5px] text-ink-500">
              No comments yet — hover a cell and click its speech bubble to add one.
            </p>
          ) : (
            threads.map((t) => {
              const last = t.comments[t.comments.length - 1];
              return (
                <button
                  key={t.cellKey}
                  type="button"
                  role="listitem"
                  onClick={() => ctx.openThread(t.cellKey, t.label ?? t.cellKey)}
                  className="w-full text-left px-3 py-2 border-b border-border/60 last:border-b-0 hover:bg-ink-100/50"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-[11.5px] font-medium text-ink-900 truncate">{t.label ?? t.cellKey}</span>
                    <span
                      className={cn(
                        'shrink-0 text-[10px] rounded-full px-1.5',
                        t.open > 0 ? 'bg-amber-100 text-amber-800' : 'bg-ink-100 text-ink-500',
                      )}
                    >
                      {t.open > 0 ? `${t.open} open` : 'resolved'}
                    </span>
                  </span>
                  <span className="block text-[11px] text-ink-500 truncate">{last?.body}</span>
                </button>
              );
            })
          )}
        </div>
      )}
    </span>
  );
}

function fmtWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function CommentThreadPanel({
  dealId,
  cellKey,
  label,
  thread,
  comments,
  onClose,
  onChanged,
}: {
  dealId: string;
  cellKey: string;
  label: string;
  thread: CommentThread | null;
  comments: CellComment[];
  onClose: () => void;
  onChanged: (next: CellComment[]) => void;
}) {
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const items = thread?.comments ?? [];
  const open = thread ? thread.open > 0 : false;

  const add = async () => {
    const text = body.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.comments.create(dealId, { cell_key: cellKey, body: text, cell_label: label });
      onChanged([...comments, created]);
      setBody('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save the comment');
    } finally {
      setBusy(false);
    }
  };

  const setResolved = async (resolved: boolean) => {
    setBusy(true);
    setError(null);
    try {
      await api.comments.resolveThread(dealId, cellKey, resolved);
      onChanged(await api.comments.list(dealId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not update the thread');
    } finally {
      setBusy(false);
    }
  };

  return (
    <aside
      role="dialog"
      aria-label={`Comments on ${label}`}
      data-testid="comment-thread-panel"
      className="fixed right-0 top-0 z-50 h-full w-[360px] max-w-full bg-white border-l border-border shadow-2xl flex flex-col"
    >
      <header className="flex items-start justify-between gap-2 px-4 py-3 border-b border-border">
        <div className="min-w-0">
          <p className="text-[10px] uppercase tracking-wider text-ink-500 font-semibold">Cell comments</p>
          <h4 className="text-[13px] font-semibold text-ink-900 truncate">{label}</h4>
          {thread && (
            <p className="text-[11px] text-ink-500">
              {items.length} comment{items.length === 1 ? '' : 's'} · {open ? 'open' : 'resolved'}
            </p>
          )}
        </div>
        <button type="button" aria-label="Close comments" onClick={onClose} className="text-ink-500 hover:text-ink-900">
          <X size={14} />
        </button>
      </header>
      <ol className="flex-1 overflow-auto px-4 py-3 space-y-3" data-testid="comment-history">
        {items.length === 0 && (
          <li className="text-[12px] text-ink-500">No comments on this cell yet.</li>
        )}
        {items.map((c) => (
          <li key={c.id} className={cn('rounded-md border px-3 py-2', c.resolved_at ? 'border-border bg-ink-100/40' : 'border-amber-300/60 bg-amber-50/50')}>
            <div className="flex items-center justify-between gap-2 text-[10.5px] text-ink-500">
              <span className="font-medium text-ink-700 truncate">{c.author_email ?? c.author_id ?? 'Analyst'}</span>
              <span className="shrink-0">{fmtWhen(c.created_at)}</span>
            </div>
            <p className="mt-1 text-[12.5px] text-ink-900 whitespace-pre-wrap">{c.body}</p>
            {c.resolved_at && (
              <p className="mt-1 text-[10.5px] text-ink-500">
                Resolved {fmtWhen(c.resolved_at)}{c.resolved_by ? ` by ${c.resolved_by}` : ''}
              </p>
            )}
          </li>
        ))}
      </ol>
      <footer className="border-t border-border px-4 py-3 space-y-2">
        {error && <p role="alert" className="text-[11.5px] text-danger-700">{error}</p>}
        <textarea
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder="Add a comment on this cell…"
          aria-label="New comment"
          rows={3}
          className="w-full rounded-md border border-border px-2 py-1.5 text-[12.5px] focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
        />
        <div className="flex items-center justify-between gap-2">
          {thread ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => setResolved(open)}
              className="inline-flex items-center gap-1 text-[11.5px] text-ink-600 hover:text-ink-900 disabled:opacity-50"
            >
              {open ? <><CheckCircle2 size={12} /> Resolve thread</> : <><RotateCcw size={12} /> Reopen thread</>}
            </button>
          ) : <span />}
          <button
            type="button"
            disabled={busy || !body.trim()}
            onClick={add}
            className="inline-flex items-center gap-1.5 rounded-md bg-ink-900 px-3 py-1.5 text-[12px] font-medium text-white disabled:opacity-40"
          >
            {busy && <Loader2 size={12} className="animate-spin" />} Add comment
          </button>
        </div>
      </footer>
    </aside>
  );
}
