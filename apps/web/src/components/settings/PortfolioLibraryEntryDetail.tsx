'use client';

/**
 * Side panel — read-only detail view for a single Portfolio Library entry.
 *
 * Wave 4 W4.1. Shows the full ``expense_ratios`` + ``revenue_mix``
 * breakdown so the analyst can verify the values they uploaded.
 * Mounted as a fixed right-side drawer (mirrors the ScenarioEditor
 * pattern in apps/web/src/components/project/ScenarioEditor.tsx).
 */

import { X, ToggleLeft, ToggleRight, Trash2 } from 'lucide-react';
import { useState } from 'react';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { Card } from '@/components/ui/Card';
import { useToast } from '@/components/ui/Toast';
import {
  api,
  WorkerError,
  type PortfolioLibraryEntry,
} from '@/lib/api';

const _RATIO_LABELS: Record<string, string> = {
  rooms_dept_pct: 'Rooms department',
  fb_dept_pct: 'F&B department',
  other_ops_dept_pct: 'Other ops department',
  admin_pct: 'Administrative & general',
  sales_pct: 'Sales & marketing',
  prop_ops_pct: 'Property ops & maintenance',
  utilities_pct: 'Utilities',
  marketing_pct: 'Marketing',
  mgmt_fee_pct: 'Management fee',
  property_tax_pct: 'Property tax',
  insurance_pct: 'Insurance',
  ffe_reserve_pct: 'FF&E reserve',
  gop_margin: 'GOP margin',
  noi_margin: 'NOI margin',
};

const _MIX_LABELS: Record<string, string> = {
  rooms_revenue_pct: 'Rooms revenue',
  fb_revenue_pct: 'F&B revenue',
  other_revenue_pct: 'Other revenue',
};

function _pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function _fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
    });
  } catch {
    return iso;
  }
}

export interface PortfolioLibraryEntryDetailProps {
  entry: PortfolioLibraryEntry;
  onClose: () => void;
  onMutated: () => void;
}

export default function PortfolioLibraryEntryDetail({
  entry,
  onClose,
  onMutated,
}: PortfolioLibraryEntryDetailProps) {
  const { toast } = useToast();
  const [busy, setBusy] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const onToggleActive = async () => {
    setBusy(true);
    try {
      if (entry.is_active) {
        await api.portfolioLibrary.deactivate(entry.id);
        toast(`Deactivated "${entry.name}"`, { type: 'success' });
      } else {
        await api.portfolioLibrary.activate(entry.id);
        toast(`Reactivated "${entry.name}"`, { type: 'success' });
      }
      onMutated();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Couldn't update: ${msg}`, { type: 'error' });
    } finally {
      setBusy(false);
    }
  };

  const onHardDelete = async () => {
    setBusy(true);
    try {
      await api.portfolioLibrary.delete(entry.id);
      toast(`Deleted "${entry.name}"`, { type: 'success' });
      onMutated();
      onClose();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Worker returns 409 when the entry's source_document_id is still
      // owned by a deal. Surface a friendlier hint.
      if (err instanceof WorkerError && err.status === 409) {
        toast(
          "Can't delete: this entry is referenced by a deal document. " +
            'Deactivate it instead.',
          { type: 'error' },
        );
      } else {
        toast(`Couldn't delete: ${msg}`, { type: 'error' });
      }
    } finally {
      setBusy(false);
      setConfirmDelete(false);
    }
  };

  const expenseRows = Object.entries(entry.expense_ratios).sort(
    (a, b) => (_RATIO_LABELS[a[0]] ?? a[0]).localeCompare(
      _RATIO_LABELS[b[0]] ?? b[0],
    ),
  );
  const mixRows = entry.revenue_mix
    ? Object.entries(entry.revenue_mix).sort(
        (a, b) => (_MIX_LABELS[a[0]] ?? a[0]).localeCompare(
          _MIX_LABELS[b[0]] ?? b[0],
        ),
      )
    : [];

  return (
    <>
      <div
        aria-hidden="true"
        className="fixed inset-0 bg-black/20 z-40"
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`Portfolio entry: ${entry.name}`}
        className="fixed right-0 top-0 bottom-0 z-50 w-[520px] bg-white border-l border-border shadow-card-hover flex flex-col"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="min-w-0 flex-1">
            <div className="text-[13.5px] font-semibold text-ink-900 truncate">
              {entry.name}
            </div>
            <div className="text-[11px] text-ink-500">
              Vintage {entry.vintage_year} · {entry.asset_count} assets ·{' '}
              {entry.total_rooms_modeled.toLocaleString()} rooms
            </div>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="p-1.5 rounded hover:bg-ink-50"
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          <div className="flex items-center gap-2">
            <Badge tone={entry.is_active ? 'green' : 'gray'}>
              {entry.is_active ? 'Active' : 'Inactive'}
            </Badge>
            {entry.chain_scales_covered.map((cs) => (
              <Badge key={cs} tone="blue">
                {cs}
              </Badge>
            ))}
          </div>

          {entry.description && (
            <p className="text-[12.5px] text-ink-700 leading-relaxed">
              {entry.description}
            </p>
          )}

          {entry.msa_coverage && entry.msa_coverage.length > 0 && (
            <Card className="p-3">
              <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500 mb-1.5">
                MSA coverage
              </div>
              <div className="flex flex-wrap gap-1.5">
                {entry.msa_coverage.map((msa) => (
                  <Badge key={msa} tone="gray">
                    {msa}
                  </Badge>
                ))}
              </div>
            </Card>
          )}

          <Card className="p-3">
            <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500 mb-2">
              Expense ratios
            </div>
            {expenseRows.length === 0 ? (
              <p className="text-[12px] text-ink-500">
                No expense ratios on this entry.
              </p>
            ) : (
              <div className="grid grid-cols-2 gap-y-1.5 gap-x-3">
                {expenseRows.map(([k, v]) => (
                  <div
                    key={k}
                    className="flex items-baseline justify-between text-[12.5px]"
                  >
                    <span className="text-ink-700">
                      {_RATIO_LABELS[k] ?? k}
                    </span>
                    <span className="font-mono font-medium text-ink-900">
                      {_pct(v)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {mixRows.length > 0 && (
            <Card className="p-3">
              <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500 mb-2">
                Revenue mix
              </div>
              <div className="grid grid-cols-2 gap-y-1.5 gap-x-3">
                {mixRows.map(([k, v]) => (
                  <div
                    key={k}
                    className="flex items-baseline justify-between text-[12.5px]"
                  >
                    <span className="text-ink-700">
                      {_MIX_LABELS[k] ?? k}
                    </span>
                    <span className="font-mono font-medium text-ink-900">
                      {_pct(v)}
                    </span>
                  </div>
                ))}
              </div>
            </Card>
          )}

          <div className="text-[11px] text-ink-500 space-y-0.5">
            <div>Created {_fmtDate(entry.created_at)}</div>
            <div>Updated {_fmtDate(entry.updated_at)}</div>
          </div>
        </div>
        <div className="border-t border-border px-4 py-3 flex items-center justify-between gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={onToggleActive}
            disabled={busy}
          >
            {entry.is_active ? (
              <>
                <ToggleLeft size={12} aria-hidden="true" />
                Deactivate
              </>
            ) : (
              <>
                <ToggleRight size={12} aria-hidden="true" />
                Reactivate
              </>
            )}
          </Button>
          {confirmDelete ? (
            <div className="flex items-center gap-2">
              <span className="text-[11.5px] text-danger-700">
                Permanently delete?
              </span>
              <Button
                variant="danger"
                size="sm"
                onClick={onHardDelete}
                disabled={busy}
              >
                Confirm
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setConfirmDelete(false)}
                disabled={busy}
              >
                Cancel
              </Button>
            </div>
          ) : (
            <Button
              variant="danger"
              size="sm"
              onClick={() => setConfirmDelete(true)}
              disabled={busy}
            >
              <Trash2 size={12} aria-hidden="true" />
              Delete
            </Button>
          )}
        </div>
      </aside>
    </>
  );
}
