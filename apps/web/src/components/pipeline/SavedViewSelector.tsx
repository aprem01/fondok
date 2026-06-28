'use client';
//
// SavedViewSelector (Wave 4 W4.5)
//
// Dropdown that sits at the top of /pipeline. Lists every saved
// pipeline view in the tenant; clicking one applies its filter to the
// active table. The bottom row of the dropdown is "Save current
// filter as…" which opens a Modal capturing name + description and
// posts to ``POST /pipeline-views``.
//
// Per-view actions (pin as default, edit, delete) hang off a kebab
// next to each row. Pinning a default unsets the prior default for
// the same actor server-side; we re-fetch the list after every
// mutation rather than try to keep client state in lockstep.
//
import { useEffect, useRef, useState } from 'react';
import {
  Bookmark, BookmarkCheck, ChevronDown, Pin, Plus, Trash2, RefreshCw,
} from 'lucide-react';
import {
  api,
  isWorkerConnected,
  PipelineFilterBody,
  SavedViewRecord,
} from '@/lib/api';
import { Button } from '@/components/ui/Button';
import Modal from '@/components/ui/Modal';

interface Props {
  /** Filter currently applied to the live pipeline page. The "Save
   *  current filter as" panel persists this verbatim into a new view. */
  currentFilter: PipelineFilterBody;
  /** Called when the analyst picks a saved view from the dropdown
   *  — the page applies the view's filter to its own state. */
  onApply: (view: SavedViewRecord) => void;
}

export default function SavedViewSelector({ currentFilter, onApply }: Props) {
  const [open, setOpen] = useState(false);
  const [views, setViews] = useState<SavedViewRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveName, setSaveName] = useState('');
  const [saveDesc, setSaveDesc] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  const refresh = async () => {
    if (!isWorkerConnected()) {
      setViews([]);
      return;
    }
    setLoading(true);
    try {
      const list = await api.pipelineViews.list();
      setViews(list);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load views');
    } finally {
      setLoading(false);
    }
  };

  // Initial load + every reopen of the dropdown so a freshly-pinned
  // default elsewhere is reflected without a page refresh.
  useEffect(() => {
    if (open) {
      void refresh();
    }
  }, [open]);

  useEffect(() => {
    void refresh();
  }, []);

  const handleSave = async () => {
    if (!saveName.trim()) return;
    setSaving(true);
    setError(null);
    try {
      await api.pipelineViews.create({
        name: saveName.trim(),
        description: saveDesc.trim() || null,
        filter: currentFilter,
      });
      setSaveOpen(false);
      setSaveName('');
      setSaveDesc('');
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to save view');
    } finally {
      setSaving(false);
    }
  };

  const handleSetDefault = async (view: SavedViewRecord) => {
    try {
      await api.pipelineViews.setDefault(view.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to set default');
    }
  };

  const handleDelete = async (view: SavedViewRecord) => {
    if (!confirm(`Delete saved view "${view.name}"?`)) return;
    try {
      await api.pipelineViews.delete(view.id);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to delete view');
    }
  };

  const defaultView = views.find(v => v.is_owner_default) || null;
  const label = defaultView ? defaultView.name : 'Saved views';

  return (
    <div ref={ref} className="relative inline-block">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="inline-flex items-center gap-2 rounded-md border border-border bg-white px-3 py-1.5 text-sm font-medium text-ink-900 hover:bg-ink-100 hover:border-ink-300"
      >
        <Bookmark size={14} className="text-ink-500" />
        <span className="max-w-[180px] truncate">{label}</span>
        <ChevronDown size={14} className="text-ink-500" />
      </button>

      {open && (
        <div className="absolute left-0 z-30 mt-1 w-72 rounded-md border border-border bg-white shadow-lg">
          <div className="flex items-center justify-between border-b border-border px-3 py-2 text-xs text-ink-500">
            <span>Saved pipeline views</span>
            <button
              type="button"
              onClick={() => void refresh()}
              className="inline-flex items-center gap-1 text-ink-500 hover:text-ink-900"
              title="Refresh"
            >
              <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>
          <div className="max-h-72 overflow-auto py-1">
            {views.length === 0 && (
              <div className="px-3 py-4 text-center text-xs text-ink-500">
                {loading ? 'Loading…' : 'No saved views yet.'}
              </div>
            )}
            {views.map(v => (
              <div
                key={v.id}
                className="group flex items-center gap-1 px-2 py-1.5 text-sm hover:bg-ink-50"
              >
                <button
                  type="button"
                  onClick={() => {
                    onApply(v);
                    setOpen(false);
                  }}
                  className="flex flex-1 items-center gap-2 truncate text-left"
                >
                  {v.is_owner_default ? (
                    <BookmarkCheck size={14} className="text-brand-700" />
                  ) : (
                    <Bookmark size={14} className="text-ink-400" />
                  )}
                  <span className="truncate">{v.name}</span>
                </button>
                <button
                  type="button"
                  onClick={() => void handleSetDefault(v)}
                  title="Pin as default"
                  className="rounded p-1 text-ink-400 opacity-0 hover:bg-ink-100 hover:text-ink-700 group-hover:opacity-100"
                >
                  <Pin size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => void handleDelete(v)}
                  title="Delete view"
                  className="rounded p-1 text-ink-400 opacity-0 hover:bg-danger-100 hover:text-danger-700 group-hover:opacity-100"
                >
                  <Trash2 size={12} />
                </button>
              </div>
            ))}
          </div>
          <div className="border-t border-border">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setSaveOpen(true);
              }}
              className="flex w-full items-center gap-2 px-3 py-2 text-sm text-ink-700 hover:bg-ink-50"
            >
              <Plus size={14} className="text-ink-500" />
              Save current filter as…
            </button>
          </div>
          {error && (
            <div className="border-t border-border px-3 py-2 text-xs text-danger-700">
              {error}
            </div>
          )}
        </div>
      )}

      <Modal
        open={saveOpen}
        onClose={() => setSaveOpen(false)}
        title="Save current filter"
      >
        <div className="space-y-3 p-5">
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-700">
              Name
            </label>
            <input
              type="text"
              value={saveName}
              onChange={e => setSaveName(e.target.value)}
              placeholder="e.g. US Deals over $30M"
              className="w-full rounded-md border border-border bg-white px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
              maxLength={120}
              autoFocus
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-700">
              Description (optional)
            </label>
            <textarea
              value={saveDesc}
              onChange={e => setSaveDesc(e.target.value)}
              placeholder="What this filter captures…"
              rows={3}
              maxLength={2000}
              className="w-full rounded-md border border-border bg-white px-3 py-1.5 text-sm focus:border-brand-500 focus:outline-none"
            />
          </div>
          {error && (
            <div className="rounded-md border border-danger-300 bg-danger-50 px-3 py-2 text-xs text-danger-700">
              {error}
            </div>
          )}
          <div className="flex justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setSaveOpen(false)}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              loading={saving}
              onClick={() => void handleSave()}
              disabled={!saveName.trim()}
            >
              Save view
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}
