'use client';

/**
 * Side panel — upload a new Portfolio P&L document + metadata.
 *
 * Wave 4 W4.1. The form supports two flows:
 *   1. **Upload with extraction** — analyst picks a PORTFOLIO_PNL PDF;
 *      the worker runs the extractor and folds the surfaced ratios
 *      into a brand-new library entry.
 *   2. **Manual entry** — when there's no doc to upload, the analyst
 *      can type the ratios directly. The form POSTs to
 *      ``POST /portfolio-library`` (no upload) so the entry lands
 *      with their hand-typed values.
 */

import { Plus, Trash2, Upload, X } from 'lucide-react';
import { useState, type FormEvent } from 'react';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { useToast } from '@/components/ui/Toast';
import { cn } from '@/lib/format';
import {
  api,
  type CreatePortfolioLibraryEntryBody,
  type PortfolioLibraryEntry,
} from '@/lib/api';

const _RATIO_KEYS: Array<{ key: string; label: string }> = [
  { key: 'rooms_dept_pct', label: 'Rooms department %' },
  { key: 'fb_dept_pct', label: 'F&B department %' },
  { key: 'admin_pct', label: 'Administrative & general %' },
  { key: 'sales_pct', label: 'Sales & marketing %' },
  { key: 'utilities_pct', label: 'Utilities %' },
  { key: 'property_tax_pct', label: 'Property tax %' },
  { key: 'insurance_pct', label: 'Insurance %' },
  { key: 'mgmt_fee_pct', label: 'Management fee %' },
  { key: 'ffe_reserve_pct', label: 'FF&E reserve %' },
];

export interface PortfolioLibraryUploadPanelProps {
  onClose: () => void;
  onCreated: (entry: PortfolioLibraryEntry) => void;
}

export default function PortfolioLibraryUploadPanel({
  onClose,
  onCreated,
}: PortfolioLibraryUploadPanelProps) {
  const { toast } = useToast();

  const [mode, setMode] = useState<'upload' | 'manual'>('manual');
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [vintageYear, setVintageYear] = useState(
    String(new Date().getFullYear()),
  );
  const [assetCount, setAssetCount] = useState('5');
  const [totalRooms, setTotalRooms] = useState('1000');
  const [chainScalesInput, setChainScalesInput] = useState('');
  const [chainScales, setChainScales] = useState<string[]>(['Upper Upscale']);
  const [msaInput, setMsaInput] = useState('');
  const [msaList, setMsaList] = useState<string[]>([]);
  const [ratios, setRatios] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);

  const addChainScale = () => {
    const s = chainScalesInput.trim();
    if (!s) return;
    if (!chainScales.includes(s)) {
      setChainScales([...chainScales, s]);
    }
    setChainScalesInput('');
  };
  const removeChainScale = (s: string) =>
    setChainScales(chainScales.filter((c) => c !== s));

  const addMsa = () => {
    const s = msaInput.trim();
    if (!s) return;
    if (!msaList.includes(s)) {
      setMsaList([...msaList, s]);
    }
    setMsaInput('');
  };
  const removeMsa = (s: string) => setMsaList(msaList.filter((m) => m !== s));

  // Parse a single ratio field (analyst types "28" or "28%" or "0.28").
  const parseRatio = (raw: string): number | null => {
    if (!raw) return null;
    const trimmed = raw.replace('%', '').trim();
    const n = Number(trimmed);
    if (!Number.isFinite(n)) return null;
    // If > 1 we assume percentage form.
    return n > 1 ? n / 100 : n;
  };

  const collectRatios = (): Record<string, number> => {
    const out: Record<string, number> = {};
    for (const [k, v] of Object.entries(ratios)) {
      const parsed = parseRatio(v);
      if (parsed !== null && parsed > 0 && parsed < 1) {
        out[k] = parsed;
      }
    }
    return out;
  };

  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!name.trim()) {
      toast('Name is required', { type: 'error' });
      return;
    }
    if (chainScales.length === 0) {
      toast('Add at least one chain scale (or use Upload Doc)', {
        type: 'error',
      });
      return;
    }
    const vYear = Number(vintageYear);
    const aCount = Number(assetCount);
    const tRooms = Number(totalRooms);
    if (
      !Number.isInteger(vYear) ||
      vYear < 1900 ||
      vYear > 2100 ||
      !Number.isInteger(aCount) ||
      aCount < 1 ||
      !Number.isInteger(tRooms) ||
      tRooms < 1
    ) {
      toast('Vintage year / asset count / total rooms must be valid integers', {
        type: 'error',
      });
      return;
    }
    setBusy(true);
    try {
      let created: PortfolioLibraryEntry;
      if (mode === 'upload') {
        if (!file) {
          toast('Pick a PDF to upload', { type: 'error' });
          setBusy(false);
          return;
        }
        const form = new FormData();
        form.set('file', file);
        form.set('name', name.trim());
        form.set('vintage_year', String(vYear));
        form.set('asset_count', String(aCount));
        form.set('total_rooms_modeled', String(tRooms));
        form.set('chain_scales_covered', JSON.stringify(chainScales));
        if (description.trim()) form.set('description', description.trim());
        if (msaList.length > 0) form.set('msa_coverage', JSON.stringify(msaList));
        created = await api.portfolioLibrary.upload(form);
      } else {
        const expenseRatios = collectRatios();
        if (Object.keys(expenseRatios).length === 0) {
          toast('Add at least one expense ratio', { type: 'error' });
          setBusy(false);
          return;
        }
        const body: CreatePortfolioLibraryEntryBody = {
          name: name.trim(),
          description: description.trim() || undefined,
          vintage_year: vYear,
          asset_count: aCount,
          total_rooms_modeled: tRooms,
          chain_scales_covered: chainScales,
          msa_coverage: msaList.length > 0 ? msaList : undefined,
          expense_ratios: expenseRatios,
        };
        created = await api.portfolioLibrary.create(body);
      }
      toast(`Added "${created.name}" to the library`, { type: 'success' });
      onCreated(created);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      toast(`Couldn't save: ${msg}`, { type: 'error' });
    } finally {
      setBusy(false);
    }
  };

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
        aria-label="Add portfolio benchmark"
        className="fixed right-0 top-0 bottom-0 z-50 w-[560px] bg-white border-l border-border shadow-card-hover flex flex-col"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div>
            <div className="text-[13.5px] font-semibold text-ink-900">
              Add portfolio benchmark
            </div>
            <div className="text-[11px] text-ink-500">
              Firm-level roll-up applied across every deal that matches the
              chain scale.
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
        <form onSubmit={onSubmit} className="flex-1 flex flex-col min-h-0">
          <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
            <div className="flex items-center gap-1 bg-ink-100 rounded-md p-1 inline-flex">
              {(['manual', 'upload'] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => setMode(m)}
                  className={cn(
                    'px-3 py-1.5 text-[12.5px] rounded transition-colors',
                    mode === m
                      ? 'bg-white text-ink-900 font-medium shadow-card'
                      : 'text-ink-500 hover:text-ink-900',
                  )}
                >
                  {m === 'manual' ? 'Manual entry' : 'Upload P&L'}
                </button>
              ))}
            </div>

            <Card className="p-3 space-y-3">
              <Labeled label="Name" required>
                <input
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Apollo Select-Service Marriott 2024 portfolio"
                  className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                />
              </Labeled>
              <Labeled label="Description (optional)">
                <textarea
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="What does this roll-up cover?"
                  rows={2}
                  className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                />
              </Labeled>
              <div className="grid grid-cols-3 gap-3">
                <Labeled label="Vintage year" required>
                  <input
                    type="number"
                    min="1900"
                    max="2100"
                    value={vintageYear}
                    onChange={(e) => setVintageYear(e.target.value)}
                    className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                  />
                </Labeled>
                <Labeled label="Asset count" required>
                  <input
                    type="number"
                    min="1"
                    value={assetCount}
                    onChange={(e) => setAssetCount(e.target.value)}
                    className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                  />
                </Labeled>
                <Labeled label="Rooms modeled" required>
                  <input
                    type="number"
                    min="1"
                    value={totalRooms}
                    onChange={(e) => setTotalRooms(e.target.value)}
                    className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                  />
                </Labeled>
              </div>
            </Card>

            <Card className="p-3 space-y-2">
              <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500">
                Chain scales covered
              </div>
              <div className="flex flex-wrap gap-1.5">
                {chainScales.map((cs) => (
                  <span
                    key={cs}
                    className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded-md bg-brand-50 text-brand-700 border border-brand-100"
                  >
                    {cs}
                    <button
                      type="button"
                      aria-label={`Remove ${cs}`}
                      onClick={() => removeChainScale(cs)}
                      className="hover:text-danger-700"
                    >
                      <X size={10} />
                    </button>
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-2">
                <input
                  value={chainScalesInput}
                  onChange={(e) => setChainScalesInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addChainScale();
                    }
                  }}
                  placeholder="e.g. Upper Upscale, Upscale, Independent…"
                  className="flex-1 px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={addChainScale}
                >
                  <Plus size={12} aria-hidden="true" />
                  Add
                </Button>
              </div>
            </Card>

            <Card className="p-3 space-y-2">
              <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500">
                MSA coverage (optional)
              </div>
              {msaList.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {msaList.map((m) => (
                    <span
                      key={m}
                      className="inline-flex items-center gap-1 px-2 py-1 text-[12px] rounded-md bg-ink-100 text-ink-700 border border-ink-200"
                    >
                      {m}
                      <button
                        type="button"
                        aria-label={`Remove ${m}`}
                        onClick={() => removeMsa(m)}
                        className="hover:text-danger-700"
                      >
                        <X size={10} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
              <div className="flex items-center gap-2">
                <input
                  value={msaInput}
                  onChange={(e) => setMsaInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      addMsa();
                    }
                  }}
                  placeholder="e.g. Atlanta, Nashville, Charlotte…"
                  className="flex-1 px-3 py-1.5 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                />
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={addMsa}
                >
                  <Plus size={12} aria-hidden="true" />
                  Add
                </Button>
              </div>
            </Card>

            {mode === 'upload' ? (
              <Card className="p-3 space-y-2">
                <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500">
                  Portfolio P&L document
                </div>
                <p className="text-[12px] text-ink-500 leading-relaxed">
                  Upload a PDF or Excel roll-up. We&rsquo;ll extract the op-ratio
                  benchmarks and pre-fill the entry.
                </p>
                <label className="flex items-center gap-2 px-3 py-2 border border-dashed border-border rounded-md cursor-pointer hover:bg-ink-50">
                  <Upload size={14} aria-hidden="true" />
                  <span className="text-[12.5px] text-ink-700">
                    {file ? file.name : 'Choose file…'}
                  </span>
                  <input
                    type="file"
                    accept=".pdf,.xlsx,.xls,.csv"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                    className="hidden"
                  />
                </label>
              </Card>
            ) : (
              <Card className="p-3 space-y-2">
                <div className="text-[11px] font-medium uppercase tracking-wide text-ink-500">
                  Expense ratios
                </div>
                <p className="text-[12px] text-ink-500">
                  Enter as decimal (0.28) or percent (28). Leave blank to skip.
                </p>
                <div className="grid grid-cols-2 gap-2">
                  {_RATIO_KEYS.map(({ key, label }) => (
                    <div key={key}>
                      <label className="block text-[11.5px] text-ink-700 mb-0.5">
                        {label}
                      </label>
                      <input
                        type="text"
                        value={ratios[key] ?? ''}
                        onChange={(e) =>
                          setRatios({ ...ratios, [key]: e.target.value })
                        }
                        placeholder="—"
                        className="w-full px-2.5 py-1.5 text-[13px] font-mono bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
                      />
                    </div>
                  ))}
                </div>
              </Card>
            )}
          </div>
          <div className="border-t border-border px-4 py-3 flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              loading={busy}
              disabled={busy}
            >
              {mode === 'upload' ? 'Upload & create' : 'Create entry'}
            </Button>
          </div>
        </form>
      </aside>
    </>
  );
}

function Labeled({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div>
      <label className="block text-[11.5px] font-medium text-ink-700 mb-1">
        {label}
        {required && <span className="text-danger-700 ml-0.5">*</span>}
      </label>
      {children}
    </div>
  );
}
