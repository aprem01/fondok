'use client';
import { useState } from 'react';
import { Plus, Search, Star, EyeOff } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import KebabMenu from '@/components/ui/KebabMenu';
import { compSets, marketDataLib, templates } from '@/lib/mockData';
import { cn } from '@/lib/format';

const tabs = ['Comp Sets', 'Market Data', 'Templates'];

export default function DataLibraryPage() {
  const [tab, setTab] = useState('Comp Sets');
  const [search, setSearch] = useState('');

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

  const cardMenu = [
    { label: 'Edit', onSelect: () => {} },
    { label: 'Duplicate', onSelect: () => {} },
    { label: 'Delete', onSelect: () => {}, danger: true },
  ];

  return (
    <div className="px-8 py-8 max-w-[1440px]">
      <PageHeader
        title="Data Library"
        subtitle="Manage shared data across your underwriting projects"
        action={<Button variant="primary"><Plus size={14} /> Add Data</Button>}
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
          {filteredComps.map(c => (
            <Card key={c.name} className="p-5">
              <div className="flex items-start justify-between mb-3">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="text-[14px] font-semibold text-ink-900">{c.name}</h3>
                    {c.starred && <Star size={13} className="text-warn-500 fill-warn-500" />}
                    {c.hidden && <EyeOff size={13} className="text-ink-400" />}
                  </div>
                  <div className="text-[11.5px] text-ink-500 mt-0.5">{c.properties} properties</div>
                </div>
                <KebabMenu items={cardMenu} />
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
          ))}
          <Card className="p-5 border-2 border-dashed border-ink-300 bg-transparent shadow-none flex flex-col items-center justify-center text-center">
            <Plus size={20} className="text-ink-400 mb-2" />
            <div className="text-[13px] font-medium text-ink-700">Create Comp Set</div>
          </Card>
        </div>
      )}

      {tab === 'Market Data' && (
        <Card className="overflow-hidden">
          <div className="px-5 py-4 border-b border-border flex items-center justify-between">
            <h3 className="text-[14px] font-semibold text-ink-900">Saved Market Data</h3>
            <Button variant="primary" size="sm"><Plus size={12} /> Add Market</Button>
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
                  <td className="px-3"><KebabMenu items={cardMenu} /></td>
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
                <KebabMenu items={cardMenu} />
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
          <Card className="p-5 border-2 border-dashed border-ink-300 bg-transparent shadow-none flex flex-col items-center justify-center text-center">
            <Plus size={20} className="text-ink-400 mb-2" />
            <div className="text-[13px] font-medium text-ink-700">Create Template</div>
            <div className="text-[11px] text-ink-500">Save reusable assumptions</div>
          </Card>
        </div>
      )}
    </div>
  );
}
