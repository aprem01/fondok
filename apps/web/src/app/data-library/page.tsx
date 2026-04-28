'use client';
import { useEffect, useRef, useState } from 'react';
import { Plus, Search, Star, EyeOff, Database, MapPinned, FileText } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import KebabMenu from '@/components/ui/KebabMenu';
import Modal from '@/components/ui/Modal';
import { useToast } from '@/components/ui/Toast';
import { compSets, marketDataLib, templates } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { IntroCard } from '@/components/help/IntroCard';

const tabs = ['Comp Sets', 'Market Data', 'Templates'];

type ModalKind = null | 'comp-set' | 'market' | 'template';

export default function DataLibraryPage() {
  const [tab, setTab] = useState('Comp Sets');
  const [search, setSearch] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [modal, setModal] = useState<ModalKind>(null);
  const addBtnRef = useRef<HTMLDivElement>(null);
  const { toast } = useToast();

  // Close the "+ Add Data" popover when clicking outside / pressing Escape.
  useEffect(() => {
    if (!addOpen) return;
    const onClick = (e: MouseEvent) => {
      if (addBtnRef.current && !addBtnRef.current.contains(e.target as Node)) {
        setAddOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setAddOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [addOpen]);

  const openModal = (kind: Exclude<ModalKind, null>) => {
    setAddOpen(false);
    setModal(kind);
  };
  const closeModal = () => setModal(null);
  const onSaved = (label: string) => {
    toast(`${label} saved`, { type: 'success' });
    closeModal();
  };

  // Comp-set star/hidden toggles persist in local state for the session.
  // Seed from mock so the initial render matches the rest of the app.
  const [compState, setCompState] = useState<Record<string, { starred: boolean; hidden: boolean }>>(() =>
    Object.fromEntries(
      compSets.map((c) => [c.name, { starred: !!c.starred, hidden: !!c.hidden }]),
    ),
  );

  const toggleStar = (name: string) => {
    setCompState((prev) => {
      const cur = prev[name] ?? { starred: false, hidden: false };
      const next = { ...cur, starred: !cur.starred };
      toast(
        next.starred ? `${name} favorited` : `${name} removed from favorites`,
        { type: 'info' },
      );
      return { ...prev, [name]: next };
    });
  };

  const toggleHidden = (name: string) => {
    setCompState((prev) => {
      const cur = prev[name] ?? { starred: false, hidden: false };
      const next = { ...cur, hidden: !cur.hidden };
      toast(next.hidden ? `${name} hidden` : `${name} visible`, { type: 'info' });
      return { ...prev, [name]: next };
    });
  };

  const q = search.toLowerCase().trim();
  const filteredComps = !q ? compSets : compSets.filter(c =>
    c.name.toLowerCase().includes(q) || (c.description?.toLowerCase().includes(q) ?? false)
  );
  const filteredMarkets = !q ? marketDataLib : marketDataLib.filter(m =>
    m.market.toLowerCase().includes(q) || m.submarket.toLowerCase().includes(q) || m.source.toLowerCase().includes(q)
  );
  const filteredTemplates = !q ? templates : templates.filter(t =>
    t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q)
  );

  // Per-row kebab actions — toasts name the action + row label so users
  // get explicit feedback (matches the section-3 spec).
  const makeCardMenu = (label: string) => [
    { label: 'Edit', onSelect: () => toast(`Edit ${label}`, { type: 'info' }) },
    { label: 'Duplicate', onSelect: () => toast(`Duplicated ${label}`, { type: 'success' }) },
    { label: 'Delete', onSelect: () => toast(`Deleted ${label}`, { type: 'info' }), danger: true },
  ];

  // The "+ Add Data" trigger is wrapped in a relative div so the popover
  // can anchor to it. Click toggles; selecting an item opens the right modal.
  const addDataAction = (
    <div className="relative" ref={addBtnRef}>
      <Button variant="primary" onClick={() => setAddOpen(o => !o)}>
        <Plus size={14} /> Add Data
      </Button>
      {addOpen && (
        <div className="absolute right-0 top-full mt-1 bg-white border border-border rounded-lg shadow-lg py-1 z-40 min-w-[180px]">
          <PopoverItem
            icon={<Database size={13} className="text-brand-500" />}
            label="Add Comp Set"
            onClick={() => openModal('comp-set')}
          />
          <PopoverItem
            icon={<MapPinned size={13} className="text-brand-500" />}
            label="Add Market"
            onClick={() => openModal('market')}
          />
          <PopoverItem
            icon={<FileText size={13} className="text-brand-500" />}
            label="Import Template"
            onClick={() => openModal('template')}
          />
        </div>
      )}
    </div>
  );

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        title="Data Library"
        subtitle="Manage shared data across your underwriting projects"
        action={addDataAction}
      />

      <IntroCard
        dismissKey="data-library-intro"
        title="Reusable assets across all your deals"
        body={
          <>
            <span className="font-semibold">Comp Sets</span> are groups of competing hotels you
            benchmark against. <span className="font-semibold">Market Data</span> is submarket
            performance (RevPAR, ADR, occupancy). <span className="font-semibold">Templates</span> are
            saved sets of underwriting assumptions you reuse for similar deal types — so you
            don&apos;t re-enter LTV / hold / exit cap on every new deal.
          </>
        }
      />

      <div className="relative mb-5 max-w-md">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search data library..."
          className="w-full pl-9 pr-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
        />
      </div>

      <div className="flex items-center gap-1 mb-5 bg-white border border-border rounded-md p-1 inline-flex">
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn(
              'px-3.5 py-1.5 text-[12.5px] rounded transition-colors',
              tab === t ? 'bg-brand-50 text-brand-700 font-medium' : 'text-ink-500 hover:text-ink-900'
            )}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'Comp Sets' && (
        <div className="grid grid-cols-3 gap-4">
          {filteredComps.map(c => {
            const state = compState[c.name] ?? { starred: !!c.starred, hidden: !!c.hidden };
            return (
            <Card key={c.name} className="p-5">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[14px] font-semibold text-ink-900">{c.name}</h3>
                    <button
                      type="button"
                      onClick={() => toggleStar(c.name)}
                      aria-label={state.starred ? `Unfavorite ${c.name}` : `Favorite ${c.name}`}
                      aria-pressed={state.starred}
                      className="p-0.5 rounded hover:bg-ink-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      <Star size={13} className={state.starred ? 'text-warn-500 fill-warn-500' : 'text-ink-400'} />
                    </button>
                    <button
                      type="button"
                      onClick={() => toggleHidden(c.name)}
                      aria-label={state.hidden ? `Show ${c.name}` : `Hide ${c.name}`}
                      aria-pressed={state.hidden}
                      className="p-0.5 rounded hover:bg-ink-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
                    >
                      <EyeOff size={13} className={state.hidden ? 'text-ink-700' : 'text-ink-300'} />
                    </button>
                  </div>
                  <div className="text-[11.5px] text-ink-500 mt-0.5">{c.properties} properties</div>
                </div>
                <KebabMenu items={makeCardMenu(c.name)} />
              </div>
              <p className="text-[12px] text-ink-700 mb-3 leading-relaxed">{c.description}</p>
              {c.usedIn && (
                <div className="text-[11px] text-ink-500 mb-3">
                  <div className="font-medium text-ink-700 mb-0.5">Used in:</div>
                  {c.usedIn.join(', ')}
                </div>
              )}
              <div className="text-[11px] text-ink-500 pt-3 border-t border-border">Updated {c.updated}</div>
            </Card>
            );
          })}
          <button
            onClick={() => openModal('comp-set')}
            className="text-left"
          >
            <Card className="p-5 border-2 border-dashed border-ink-300 bg-transparent shadow-none flex flex-col items-center justify-center text-center hover:border-brand-500 hover:bg-brand-50/40 transition-colors h-full">
              <Plus size={20} className="text-ink-400 mb-2" />
              <div className="text-[13px] font-medium text-ink-700">Create Comp Set</div>
            </Card>
          </button>
        </div>
      )}

      {tab === 'Market Data' && (
        <Card className="overflow-hidden">
          <div className="px-5 py-4 border-b border-border flex items-center justify-between">
            <h3 className="text-[14px] font-semibold text-ink-900">Saved Market Data</h3>
            <Button variant="primary" size="sm" onClick={() => openModal('market')}>
              <Plus size={12} /> Add Market
            </Button>
          </div>
          <table className="w-full text-[12.5px]">
            <thead>
              <tr className="text-ink-500 text-[10.5px] border-b border-border bg-ink-300/5">
                <th className="text-left font-medium px-5 py-2">Market / Submarket</th>
                <th className="text-right font-medium px-3 py-2">RevPAR</th>
                <th className="text-right font-medium px-3 py-2">ADR</th>
                <th className="text-right font-medium px-3 py-2">Occupancy</th>
                <th className="text-right font-medium px-3 py-2">YoY</th>
                <th className="text-left font-medium px-3 py-2">Source</th>
                <th className="px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {filteredMarkets.map(m => (
                <tr key={m.market + m.submarket} className="border-b border-border/50 hover:bg-ink-300/10">
                  <td className="px-5 py-3">
                    <div className="font-medium text-ink-900">{m.market}</div>
                    <div className="text-[11.5px] text-ink-500">{m.submarket}</div>
                  </td>
                  <td className="text-right tabular-nums px-3">${m.revpar}</td>
                  <td className="text-right tabular-nums px-3">${m.adr}</td>
                  <td className="text-right tabular-nums px-3">{m.occ}%</td>
                  <td className={`text-right tabular-nums px-3 ${m.yoy > 0 ? 'text-success-700' : 'text-danger-700'}`}>
                    {m.yoy > 0 ? '+' : ''}{m.yoy}%
                  </td>
                  <td className="px-3">{m.source}</td>
                  <td className="px-3"><KebabMenu items={makeCardMenu(`${m.market} · ${m.submarket}`)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {tab === 'Templates' && (
        <div className="grid grid-cols-3 gap-4">
          {filteredTemplates.map(t => (
            <Card key={t.name} className="p-5">
              <div className="flex items-start justify-between mb-3">
                <h3 className="text-[14px] font-semibold text-ink-900">{t.name}</h3>
                <KebabMenu items={makeCardMenu(t.name)} />
              </div>
              <p className="text-[12px] text-ink-500 mb-4 leading-relaxed">{t.description}</p>
              <div className="grid grid-cols-3 gap-2 mb-4">
                {[['Hold', t.hold], ['LTV', t.ltv], ['Exit Cap', t.exitCap]].map(([k, v]) => (
                  <div key={k}>
                    <div className="text-[10px] text-ink-500 uppercase">{k}</div>
                    <div className="text-[12.5px] font-semibold tabular-nums">{v}</div>
                  </div>
                ))}
              </div>
              <div className="text-[11px] text-ink-500 pt-3 border-t border-border">Used in {t.usedIn} projects</div>
            </Card>
          ))}
          <button onClick={() => openModal('template')} className="text-left">
            <Card className="p-5 border-2 border-dashed border-ink-300 bg-transparent shadow-none flex flex-col items-center justify-center text-center hover:border-brand-500 hover:bg-brand-50/40 transition-colors h-full">
              <Plus size={20} className="text-ink-400 mb-2" />
              <div className="text-[13px] font-medium text-ink-700">Create Template</div>
              <div className="text-[11px] text-ink-500">Save reusable assumptions</div>
            </Card>
          </button>
        </div>
      )}

      {/* ── Modals ────────────────────────────────────────── */}
      <AddCompSetModal
        open={modal === 'comp-set'}
        onClose={closeModal}
        onSave={() => onSaved('Comp set')}
      />
      <AddMarketModal
        open={modal === 'market'}
        onClose={closeModal}
        onSave={() => onSaved('Market')}
      />
      <ImportTemplateModal
        open={modal === 'template'}
        onClose={closeModal}
      />
    </div>
  );
}

// ──────────────────────────────────────────────────────────
// Small popover row used inside the "+ Add Data" menu.
// ──────────────────────────────────────────────────────────
function PopoverItem({
  icon, label, onClick,
}: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="w-full flex items-center gap-2 px-3 py-2 text-[12.5px] text-left text-ink-900 hover:bg-ink-300/10"
    >
      {icon}
      {label}
    </button>
  );
}

// ──────────────────────────────────────────────────────────
// Modal bodies — local state only.
// ──────────────────────────────────────────────────────────
function AddCompSetModal({
  open, onClose, onSave,
}: { open: boolean; onClose: () => void; onSave: () => void }) {
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [count, setCount] = useState(5);
  return (
    <Modal open={open} onClose={onClose} title="Add Comp Set">
      <div className="p-5 space-y-3">
        <Field label="Name">
          <input
            value={name} onChange={e => setName(e.target.value)}
            placeholder="South Beach Lifestyle Comp"
            className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          />
        </Field>
        <Field label="Description">
          <textarea
            value={desc} onChange={e => setDesc(e.target.value)}
            placeholder="What this comp set covers and how to use it"
            rows={3}
            className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500 resize-none"
          />
        </Field>
        <Field label="Properties">
          <input
            type="number" min={1}
            value={count}
            onChange={e => setCount(parseInt(e.target.value, 10) || 0)}
            className="w-32 px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
          />
        </Field>
      </div>
      <ModalFooter onClose={onClose} onSave={onSave} />
    </Modal>
  );
}

function AddMarketModal({
  open, onClose, onSave,
}: { open: boolean; onClose: () => void; onSave: () => void }) {
  const [market, setMarket] = useState('');
  const [submarket, setSubmarket] = useState('');
  const [source, setSource] = useState('STR');
  const [revpar, setRevpar] = useState('');
  const [adr, setAdr] = useState('');
  const [occ, setOcc] = useState('');
  const [yoy, setYoy] = useState('');
  return (
    <Modal open={open} onClose={onClose} title="Add Market" maxWidth="max-w-lg">
      <div className="p-5 space-y-3">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Market">
            <input value={market} onChange={e => setMarket(e.target.value)} placeholder="Miami, FL"
              className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
          </Field>
          <Field label="Submarket">
            <input value={submarket} onChange={e => setSubmarket(e.target.value)} placeholder="South Beach"
              className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
          </Field>
        </div>
        <Field label="Source">
          <select value={source} onChange={e => setSource(e.target.value)}
            className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500">
            <option>STR</option>
            <option>Kalibri</option>
            <option>CoStar</option>
            <option>Manual</option>
          </select>
        </Field>
        <div className="grid grid-cols-4 gap-3">
          <Field label="RevPAR ($)">
            <input value={revpar} onChange={e => setRevpar(e.target.value)} placeholder="293" inputMode="decimal"
              className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
          </Field>
          <Field label="ADR ($)">
            <input value={adr} onChange={e => setAdr(e.target.value)} placeholder="385" inputMode="decimal"
              className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
          </Field>
          <Field label="Occupancy (%)">
            <input value={occ} onChange={e => setOcc(e.target.value)} placeholder="76.2" inputMode="decimal"
              className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
          </Field>
          <Field label="YoY (%)">
            <input value={yoy} onChange={e => setYoy(e.target.value)} placeholder="3.4" inputMode="decimal"
              className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
          </Field>
        </div>
      </div>
      <ModalFooter onClose={onClose} onSave={onSave} />
    </Modal>
  );
}

function ImportTemplateModal({
  open, onClose,
}: { open: boolean; onClose: () => void }) {
  return (
    <Modal open={open} onClose={onClose} title="Import Template">
      <div className="p-6 text-center">
        <div className="w-12 h-12 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
          <FileText size={20} className="text-brand-500" />
        </div>
        <div className="text-[14px] font-semibold text-ink-900">Template import — Enterprise plan</div>
        <p className="text-[12px] text-ink-500 mt-1">
          Import .xlsx or YAML underwriting templates. Available on enterprise plans.
        </p>
      </div>
      <div className="px-5 py-3 border-t border-border flex justify-end">
        <Button variant="secondary" size="sm" onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="block text-[11px] font-medium text-ink-700 uppercase tracking-wide mb-1.5">{label}</label>
      {children}
    </div>
  );
}

function ModalFooter({ onClose, onSave }: { onClose: () => void; onSave: () => void }) {
  return (
    <div className="px-5 py-3 border-t border-border flex justify-end gap-2">
      <Button variant="secondary" size="sm" onClick={onClose}>Cancel</Button>
      <Button variant="primary" size="sm" onClick={onSave}>Save</Button>
    </div>
  );
}
