'use client';
import { useState } from 'react';
import Link from 'next/link';
import {
  LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  Legend, ResponsiveContainer,
} from 'recharts';
import { Calendar, Download, MapPinned } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';
import { miamiMarket } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { IntroCard } from '@/components/help/IntroCard';

const subTabs = ['Market Overview', 'Transaction Comps'];

const tooltipStyle = {
  contentStyle: { background: '#fff', border: '1px solid #e5e7eb', borderRadius: 6, fontSize: 12 },
  labelStyle: { color: '#64748b', fontSize: 11 },
};

export default function MarketTab({ projectId }: { projectId: number | string }) {
  const [tab, setTab] = useState('Market Overview');
  const m = miamiMarket;
  const isKimptonDemo = projectId === 7;
  const { toast } = useToast();

  // Sales-data export streams the demo's CoStar-style table to a CSV the
  // analyst can paste into Excel. We build it client-side from the same
  // miamiMarket fixture the page renders, so what's downloaded matches
  // what the user sees byte-for-byte.
  const onExportSales = () => {
    const headers = ['Property', 'Keys', 'Sale Date', 'Sale Price', '$/Key', 'Cap Rate', 'Buyer'];
    const rows = m.sales.map((s) =>
      [s.name, s.keys, s.date, s.price, s.perKey, s.cap, s.buyer]
        .map((cell) => {
          const str = String(cell ?? '');
          return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
        })
        .join(','),
    );
    const csv = [headers.join(','), ...rows].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `miami-beach-sales-${m.asOf.replace(/\s+/g, '-')}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    toast(`Downloaded ${rows.length} sales as CSV`, { type: 'success' });
  };

  if (!isKimptonDemo) {
    return (
      <div>
        <IntroCard
          dismissKey="market-intro"
          title="The Market view"
          body={
            <>
              What&apos;s happening in this submarket — recent performance trends, new hotels being
              built (the supply pipeline), what&apos;s driving demand, and recent sales of comparable
              hotels. The basis for your projections and exit valuation.
            </>
          }
        />
        <Card className="p-5 mb-5">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-[15px] font-semibold text-ink-900">Market Data</h2>
              <p className="text-[12.5px] text-ink-500 mt-1">
                Submarket performance, supply pipeline, demand drivers, and recent transaction comparables.
              </p>
            </div>
          </div>
        </Card>
        <Card className="p-16 text-center">
          <div className="w-12 h-12 rounded-lg bg-ink-300/20 flex items-center justify-center mx-auto mb-4">
            <MapPinned size={20} className="text-ink-400" />
          </div>
          <h3 className="text-[15px] font-semibold text-ink-900">No market data yet</h3>
          <p className="text-[12.5px] text-ink-500 mt-1 max-w-md mx-auto leading-relaxed">
            We don&apos;t have benchmark data for this submarket yet. Open the
            <span className="font-medium"> Data Library</span> to add it (paste in an STR report or
            attach a saved market).
          </p>
          <Link href="/data-library?tab=market" className="inline-block mt-4">
            <Button variant="primary" size="sm">Open Data Library</Button>
          </Link>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <IntroCard
        dismissKey="market-intro"
        title="The Market view"
        body={
          <>
            What&apos;s happening in this submarket — recent performance trends, new hotels being
            built (the supply pipeline), what&apos;s driving demand, and recent sales of comparable
            hotels. The basis for your projections and exit valuation.
          </>
        }
      />
      <Card className="p-5 mb-5">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-[15px] font-semibold text-ink-900">Market Data</h2>
            <p className="text-[12.5px] text-ink-500 mt-1">Submarket performance, supply pipeline, demand drivers, and recent transaction comparables.</p>
          </div>
          <div className="flex items-center gap-2">
            <Badge tone="gray"><Calendar size={11} /> As of {m.asOf}</Badge>
            <Button variant="secondary" size="sm" onClick={onExportSales}><Download size={12} /> Export Sales Data</Button>
          </div>
        </div>
      </Card>

      <div className="flex items-center gap-1 mb-5 border-b border-border">
        {subTabs.map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={cn(
              'px-4 py-2 text-[12.5px] border-b-2 transition-colors -mb-px',
              tab === t ? 'border-brand-500 text-brand-700 font-medium' : 'border-transparent text-ink-500 hover:text-ink-900'
            )}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'Market Overview' && (
        <div className="space-y-5">
          <div className="text-[13px] text-ink-700 font-medium mb-2">{m.submarket}</div>

          <div className="grid grid-cols-6 gap-3">
            <KPI label="Inventory" value={m.kpis.inventory.rooms.toLocaleString()} sub={`${m.kpis.inventory.hotels} hotels · +${m.kpis.inventory.yoy}% YoY`} />
            <KPI label="Occupancy" value={`${m.kpis.occupancy.value}%`} sub={`+${m.kpis.occupancy.deltaPts} pts`} positive />
            <KPI label="ADR" value={`$${m.kpis.adr.value}`} sub={`+${m.kpis.adr.yoy}%`} positive />
            <KPI label="RevPAR" value={`$${m.kpis.revpar.value}`} sub={`+${m.kpis.revpar.yoy}%`} positive />
            <KPI label="Demand Growth" value={`+${m.kpis.demandGrowth}%`} sub="YoY" positive />
            <KPI label="Supply Growth" value={`+${m.kpis.supplyGrowth}%`} sub="YoY" />
          </div>

          <div className="grid grid-cols-2 gap-5">
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-1">Historical Performance</h3>
              <div className="text-[11px] text-ink-500 mb-3">5-Year Trend</div>
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={m.historical} margin={{ top: 5, right: 25, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 2" stroke="#e5e7eb" />
                  <XAxis dataKey="year" stroke="#64748b" fontSize={11} />
                  <YAxis yAxisId="left" stroke="#64748b" fontSize={11} unit="%" />
                  <YAxis yAxisId="right" orientation="right" stroke="#64748b" fontSize={11} unit="$" />
                  <Tooltip {...tooltipStyle} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line yAxisId="left" type="monotone" dataKey="occ" name="Occupancy %" stroke="#3b82f6" strokeWidth={2} />
                  <Line yAxisId="right" type="monotone" dataKey="revpar" name="RevPAR" stroke="#10b981" strokeWidth={2} />
                  <Line yAxisId="right" type="monotone" dataKey="adr" name="ADR" stroke="#f59e0b" strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </Card>

            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-1">Monthly Performance</h3>
              <div className="text-[11px] text-ink-500 mb-3">TTM</div>
              <ResponsiveContainer width="100%" height={240}>
                <LineChart data={m.monthly} margin={{ top: 5, right: 25, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 2" stroke="#e5e7eb" />
                  <XAxis dataKey="m" stroke="#64748b" fontSize={11} />
                  <YAxis yAxisId="left" stroke="#64748b" fontSize={11} unit="%" />
                  <YAxis yAxisId="right" orientation="right" stroke="#64748b" fontSize={11} unit="$" />
                  <Tooltip {...tooltipStyle} />
                  <Legend wrapperStyle={{ fontSize: 11 }} />
                  <Line yAxisId="left" type="monotone" dataKey="occ" name="Occupancy %" stroke="#3b82f6" strokeWidth={2} />
                  <Line yAxisId="right" type="monotone" dataKey="revpar" name="RevPAR" stroke="#10b981" strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </Card>
          </div>

          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-1">Subject vs. Comp Set Index</h3>
            <div className="text-[11px] text-ink-500 mb-3">TTM · RGI 1.12 · ARI 1.08 · MPI 1.04</div>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={m.index} margin={{ top: 5, right: 25, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 2" stroke="#e5e7eb" />
                <XAxis dataKey="m" stroke="#64748b" fontSize={11} />
                <YAxis stroke="#64748b" fontSize={11} domain={[0.95, 1.2]} />
                <Tooltip {...tooltipStyle} />
                <Legend wrapperStyle={{ fontSize: 11 }} />
                <Line type="monotone" dataKey="RGI" stroke="#3b82f6" strokeWidth={2} />
                <Line type="monotone" dataKey="ARI" stroke="#10b981" strokeWidth={2} />
                <Line type="monotone" dataKey="MPI" stroke="#f59e0b" strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </Card>

          <div className="grid grid-cols-2 gap-5">
            <Card className="p-5">
              <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Demand Segmentation</h3>
              {m.segmentation.map(s => (
                <div key={s.name} className="mb-3 last:mb-0">
                  <div className="flex justify-between text-[12px] mb-1">
                    <span className="text-ink-700 font-medium">{s.name}</span>
                    <span className="text-ink-700 tabular-nums">{s.pct}% <span className="text-success-700 ml-1">+{s.deltaPts} pts</span></span>
                  </div>
                  <div className="h-2 bg-ink-300/30 rounded-full overflow-hidden">
                    <div className="h-full bg-brand-500" style={{ width: `${s.pct}%` }} />
                  </div>
                </div>
              ))}
              <div className="text-[11px] text-ink-500 mt-3">
                Diversified demand mix with leisure-led growth and stable corporate group demand.
              </div>
            </Card>

            <Card className="p-5">
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-[13px] font-semibold text-ink-900">Supply Pipeline</h3>
                <span className="text-[11px] text-ink-500">414 rooms (2.2% of inventory)</span>
              </div>
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-ink-500 text-[10.5px] border-b border-border">
                    <th className="text-left font-medium pb-2">Property</th>
                    <th className="text-right font-medium pb-2">Rooms</th>
                    <th className="text-left font-medium pb-2">Status</th>
                    <th className="text-right font-medium pb-2">Opening</th>
                  </tr>
                </thead>
                <tbody>
                  {m.pipeline.map(p => (
                    <tr key={p.property} className="border-b border-border/50">
                      <td className="py-1.5">{p.property}</td>
                      <td className="text-right tabular-nums">{p.rooms}</td>
                      <td className="py-1.5">
                        <Badge tone={p.status === 'Construction' ? 'amber' : 'gray'}>{p.status}</Badge>
                      </td>
                      <td className="text-right text-ink-700">{p.opening}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="text-[11px] text-ink-500 mt-3">
                Construction: 141 rooms · Planning: 273 rooms
              </div>
            </Card>
          </div>

          <Card className="p-5">
            <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Top Demand Generators</h3>
            <div className="space-y-2 text-[12.5px]">
              {m.demandGenerators.map(d => (
                <div key={d.name} className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
                  <div>
                    <div className="font-medium text-ink-900">{d.name}</div>
                    <div className="text-[11px] text-ink-500">{d.type}</div>
                  </div>
                  <div className="text-[12px] text-ink-700 tabular-nums">{d.volume}</div>
                </div>
              ))}
            </div>
          </Card>

          <Card className="p-5">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-[13px] font-semibold text-ink-900">Recent Hotel Sales</h3>
              <span className="text-[11px] text-ink-500">Source: CoStar Group, As of {m.asOf}</span>
            </div>
            <div className="grid grid-cols-4 gap-3 mb-4">
              <KPI label="TTM Volume" value={m.salesTotals.ttmVolume} />
              <KPI label="Transactions" value={m.salesTotals.txns.toString()} />
              <KPI label="Avg $/Key" value={m.salesTotals.avgPerKey} />
              <KPI label="Avg Cap Rate" value={m.salesTotals.avgCap} />
            </div>
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-ink-500 text-[10.5px] border-b border-border">
                  <th className="text-left font-medium pb-2">Property</th>
                  <th className="text-right font-medium pb-2">Keys</th>
                  <th className="text-left font-medium pb-2">Sale Date</th>
                  <th className="text-right font-medium pb-2">Sale Price</th>
                  <th className="text-right font-medium pb-2">$/Key</th>
                  <th className="text-right font-medium pb-2">Cap Rate</th>
                  <th className="text-left font-medium pb-2">Buyer</th>
                </tr>
              </thead>
              <tbody>
                {m.sales.map(s => (
                  <tr key={s.name} className="border-b border-border/50">
                    <td className="py-2 font-medium">{s.name}</td>
                    <td className="text-right tabular-nums">{s.keys}</td>
                    <td className="text-ink-700">{s.date}</td>
                    <td className="text-right tabular-nums">{s.price}</td>
                    <td className="text-right tabular-nums text-brand-700 font-medium">{s.perKey}</td>
                    <td className="text-right tabular-nums">{s.cap}</td>
                    <td className="text-ink-700">{s.buyer}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[10.5px] text-ink-500 mt-3 italic">
              Source: CoStar Group · Includes sales of 100+ keys within Miami Beach / South Beach submarket
            </div>
          </Card>
        </div>
      )}

      {tab === 'Transaction Comps' && (
        <Card className="p-5">
          <h3 className="text-[13px] font-semibold text-ink-900 mb-3">Transaction Comparables</h3>
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-ink-500 text-[10.5px] border-b border-border">
                <th className="text-left font-medium pb-2">Property</th>
                <th className="text-right font-medium pb-2">Keys</th>
                <th className="text-right font-medium pb-2">Sale Price</th>
                <th className="text-right font-medium pb-2">$/Key</th>
                <th className="text-right font-medium pb-2">Cap Rate</th>
                <th className="text-left font-medium pb-2">Buyer Type</th>
              </tr>
            </thead>
            <tbody>
              {m.sales.map(s => (
                <tr key={s.name} className="border-b border-border/50">
                  <td className="py-2 font-medium">{s.name}</td>
                  <td className="text-right tabular-nums">{s.keys}</td>
                  <td className="text-right tabular-nums">{s.price}</td>
                  <td className="text-right tabular-nums">{s.perKey}</td>
                  <td className="text-right tabular-nums">{s.cap}</td>
                  <td className="text-ink-700">{s.buyer}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}

function KPI({ label, value, sub, positive }: { label: string; value: string; sub?: string; positive?: boolean }) {
  return (
    <Card className="p-3">
      <div className="text-[10px] text-ink-500 uppercase tracking-wide">{label}</div>
      <div className="text-[18px] font-semibold tabular-nums mt-0.5 text-ink-900">{value}</div>
      {sub && <div className={`text-[10.5px] mt-0.5 ${positive ? 'text-success-700' : 'text-ink-500'}`}>{sub}</div>}
    </Card>
  );
}
