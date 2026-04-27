'use client';
import { useState } from 'react';
import Link from 'next/link';
import {
  Check, ChevronDown, Target, TrendingUp, Rocket, UploadCloud, Tag, Search,
  Sparkles, Crown, DollarSign, Pencil, AlertTriangle, ArrowLeft, ChevronRight,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { dealStages, returnProfiles, positioningTiers, brandFamilies } from '@/lib/mockData';
import { cn } from '@/lib/format';

const steps = [
  { n: 1, label: 'Deal Details' },
  { n: 2, label: 'Return Profile' },
  { n: 3, label: 'Documents' },
  { n: 4, label: 'Branding' },
  { n: 5, label: 'Positioning' },
  { n: 6, label: 'Review' },
];

const iconForReturn: Record<string, any> = { core: Target, 'value-add': TrendingUp, opportunistic: Rocket };
const iconForPos: Record<string, any> = { default: Sparkles, luxury: Crown, upscale: TrendingUp, economy: DollarSign };

export default function NewProjectPage() {
  const [step, setStep] = useState(1);
  const [data, setData] = useState({
    dealName: '', city: '', keys: '', stage: 'Teaser', hotelName: '', price: '',
    returnProfile: 'value-add',
    docs: [] as string[],
    brand: 'agnostic',
    brandSearch: '',
    expandedFamilies: ['Hilton'] as string[],
    positioning: 'default',
  });

  const update = (patch: Partial<typeof data>) => setData(d => ({ ...d, ...patch }));
  const next = () => setStep(s => Math.min(6, s + 1));
  const back = () => setStep(s => Math.max(1, s - 1));

  return (
    <div className="px-8 py-8 max-w-[1100px] mx-auto">
      <div className="mb-6">
        <Link href="/projects" className="inline-flex items-center gap-1 text-[12.5px] text-ink-500 hover:text-ink-900 mb-3">
          <ArrowLeft size={13} /> Back to Projects
        </Link>
        <h1 className="text-[24px] font-semibold text-ink-900">New Project</h1>
      </div>

      {/* Stepper */}
      <Card className="p-5 mb-5">
        <div className="flex items-center justify-between">
          {steps.map((s, i) => {
            const done = step > s.n;
            const active = step === s.n;
            return (
              <div key={s.n} className="flex items-center flex-1">
                <div className="flex items-center gap-3 flex-1">
                  <div className={cn(
                    'w-8 h-8 rounded-full flex items-center justify-center text-[12px] font-semibold border-2 flex-shrink-0',
                    done ? 'bg-success-500 border-success-500 text-white' :
                    active ? 'bg-brand-500 border-brand-500 text-white' :
                    'bg-white border-ink-300 text-ink-400'
                  )}>
                    {done ? <Check size={14} /> : s.n}
                  </div>
                  <div className={cn('text-[12.5px]', active ? 'font-semibold text-ink-900' : 'text-ink-500')}>{s.label}</div>
                </div>
                {i < steps.length - 1 && (
                  <div className={cn('h-0.5 flex-1 mx-3', done ? 'bg-success-500' : 'bg-ink-300')} />
                )}
              </div>
            );
          })}
        </div>
      </Card>

      {/* Step body */}
      <Card className="p-7">
        {step === 1 && <Step1 data={data} update={update} />}
        {step === 2 && <Step2 data={data} update={update} />}
        {step === 3 && <Step3 data={data} update={update} />}
        {step === 4 && <Step4 data={data} update={update} />}
        {step === 5 && <Step5 data={data} update={update} />}
        {step === 6 && <Step6 data={data} jumpTo={setStep} />}
      </Card>

      {/* Footer */}
      <div className="flex items-center justify-between mt-5">
        <Button variant="secondary" onClick={back} disabled={step === 1}>
          <ArrowLeft size={13} /> Back
        </Button>
        {step < 6 ? (
          <Button variant="primary" onClick={next}>Next <ChevronRight size={13} /></Button>
        ) : (
          <Link href="/projects"><Button variant="primary">Create Shell Deal</Button></Link>
        )}
      </div>
    </div>
  );
}

type WizardData = {
  dealName: string; city: string; keys: string; stage: string; hotelName: string; price: string;
  returnProfile: string; docs: string[]; brand: string; brandSearch: string;
  expandedFamilies: string[]; positioning: string;
};
type StepProps = { data: WizardData; update: (patch: Partial<WizardData>) => void };

function Step1({ data, update }: StepProps) {
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Create New Deal</h2>
      <p className="text-[12.5px] text-ink-500 mb-6">Enter deal details for pipeline tracking. Documents can be added later.</p>

      <div className="space-y-4">
        <Field label="Deal Name *" value={data.dealName} onChange={v => update({ dealName: v })} placeholder="Chicago Downtown Acquisition" />
        <Field label="City / Submarket *" value={data.city} onChange={v => update({ city: v })} placeholder="Chicago, IL" />
        <div className="grid grid-cols-2 gap-4">
          <Field label="Keys *" value={data.keys} onChange={v => update({ keys: v })} placeholder="312" type="number" />
          <Select label="Deal Stage *" value={data.stage} onChange={v => update({ stage: v })} options={dealStages} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Hotel Name (Optional)" value={data.hotelName} onChange={v => update({ hotelName: v })} placeholder="Marriott Chicago Downtown" />
          <Field label="Indicative Price (Optional)" value={data.price} onChange={v => update({ price: v })} placeholder="$120-140M" />
        </div>
      </div>

      <div className="mt-6 bg-warn-50 border border-warn-500/30 rounded-lg p-4 flex gap-3">
        <AlertTriangle size={16} className="text-warn-700 flex-shrink-0 mt-0.5" />
        <div>
          <div className="text-[12.5px] font-semibold text-warn-700">Shell Deal</div>
          <p className="text-[12px] text-warn-700/90 mt-1 leading-relaxed">
            Deals can be created at the screening stage without documents. Financial modeling and IC-ready outputs require supporting documentation.
          </p>
        </div>
      </div>
    </div>
  );
}

function Step2({ data, update }: StepProps) {
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Return Requirements</h2>
      <p className="text-[12.5px] text-ink-500 mb-6">Select the investment strategy that matches your return targets.</p>
      <div className="grid grid-cols-3 gap-4">
        {returnProfiles.map(p => {
          const Icon = iconForReturn[p.id];
          const selected = data.returnProfile === p.id;
          return (
            <button key={p.id} onClick={() => update({ returnProfile: p.id })}
              className={cn(
                'p-5 rounded-lg border-2 text-left transition-colors',
                selected ? 'border-brand-500 bg-brand-50' : 'border-border bg-white hover:border-ink-300'
              )}>
              <div className="flex items-start justify-between mb-3">
                <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center', selected ? 'bg-brand-500 text-white' : 'bg-ink-300/30 text-ink-700')}>
                  <Icon size={18} />
                </div>
                {selected && <Check size={18} className="text-brand-500" />}
              </div>
              <div className="text-[14px] font-semibold text-ink-900">{p.label}</div>
              <div className="text-[12px] text-brand-700 font-medium mt-1">Target IRR: {p.target}</div>
              <p className="text-[11.5px] text-ink-500 mt-2 leading-relaxed">{p.desc}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Step3({ data, update }: StepProps) {
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Upload Documents</h2>
      <p className="text-[12.5px] text-ink-500 mb-6">Add your deal documents to the data room for AI extraction.</p>

      <div className="border-2 border-dashed border-ink-300 rounded-lg py-12 px-6 text-center">
        <UploadCloud size={36} className="text-ink-400 mx-auto mb-3" />
        <div className="text-[14px] font-medium text-ink-900">Drag & drop files here</div>
        <div className="text-[12px] text-ink-500 mt-1">or click to browse</div>
        <Button variant="primary" size="sm" className="mt-4">Select Files</Button>
        <div className="text-[11px] text-ink-500 mt-3">Supported: PDF, Excel, CSV, Word documents</div>
      </div>

      <p className="text-[12px] text-ink-500 mt-4 text-center">You can skip this step and upload documents later</p>
    </div>
  );
}

function Step4({ data, update }: StepProps) {
  const isAgnostic = data.brand === 'agnostic';
  const q = data.brandSearch.toLowerCase().trim();
  const filtered = q
    ? brandFamilies
        .map(f => ({ ...f, brands: f.brands.filter(b => b.name.toLowerCase().includes(q)) }))
        .filter(f => f.family.toLowerCase().includes(q) || f.brands.length > 0)
    : brandFamilies;

  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Select Brand</h2>
      <p className="text-[12.5px] text-ink-500 mb-6">Choose a hotel brand or select brand agnostic for independent analysis.</p>

      <button onClick={() => update({ brand: 'agnostic' })}
        className={cn(
          'w-full p-5 rounded-lg border-2 text-left mb-5 transition-colors',
          isAgnostic ? 'border-brand-500 bg-brand-50' : 'border-border bg-white hover:border-ink-300'
        )}>
        <div className="flex items-start gap-3">
          <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center flex-shrink-0',
            isAgnostic ? 'bg-brand-500 text-white' : 'bg-ink-300/30 text-ink-700'
          )}>
            <Tag size={18} />
          </div>
          <div className="flex-1">
            <div className="text-[14px] font-semibold text-ink-900">Brand Agnostic</div>
            <p className="text-[12px] text-ink-500 mt-1">Analyze without brand constraints — you'll select positioning manually</p>
          </div>
          {isAgnostic && <Check size={18} className="text-brand-500" />}
        </div>
      </button>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[11px] text-ink-500 uppercase tracking-wider font-medium">OR SELECT A BRAND</span>
        <div className="flex-1 h-px bg-border" />
      </div>

      <div className="relative mb-4">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-400" />
        <input
          value={data.brandSearch} onChange={e => update({ brandSearch: e.target.value })}
          placeholder="Search brands..."
          className="w-full pl-9 pr-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500"
        />
      </div>

      <div className="space-y-2 max-h-[400px] overflow-y-auto scrollbar-thin">
        {filtered.map(fam => {
          const expanded = data.expandedFamilies.includes(fam.family);
          return (
            <div key={fam.family} className="border border-border rounded-md">
              <button
                onClick={() => update({
                  expandedFamilies: expanded
                    ? data.expandedFamilies.filter((f: string) => f !== fam.family)
                    : [...data.expandedFamilies, fam.family]
                })}
                className="w-full px-4 py-3 flex items-center justify-between hover:bg-ink-300/10"
              >
                <div className="text-[13px] font-medium text-ink-900">
                  {fam.family} <span className="text-ink-500 font-normal">({fam.count} brands)</span>
                </div>
                <ChevronDown size={14} className={cn('text-ink-400 transition-transform', expanded && 'rotate-180')} />
              </button>
              {expanded && fam.brands.length > 0 && (
                <div className="grid grid-cols-3 gap-2 p-3 border-t border-border">
                  {fam.brands.map(b => (
                    <button key={b.name} onClick={() => update({ brand: b.name })}
                      className={cn(
                        'p-2.5 rounded-md text-left border transition-colors',
                        data.brand === b.name ? 'border-brand-500 bg-brand-50' : 'border-border hover:border-ink-300'
                      )}>
                      <div className="text-[12px] font-medium text-ink-900">{b.name}</div>
                      <div className="text-[10px] text-ink-500 mt-0.5">{b.tier}</div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Step5({ data, update }: StepProps) {
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Market Positioning</h2>
      <p className="text-[12.5px] text-ink-500 mb-6">Select the market segment for your analysis.</p>
      <div className="grid grid-cols-2 gap-4">
        {positioningTiers.map(p => {
          const Icon = iconForPos[p.id];
          const selected = data.positioning === p.id;
          return (
            <button key={p.id} onClick={() => update({ positioning: p.id })}
              className={cn(
                'p-5 rounded-lg border-2 text-left transition-colors',
                selected ? 'border-brand-500 bg-brand-50' : 'border-border bg-white hover:border-ink-300'
              )}>
              <div className="flex items-center gap-3 mb-2">
                <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center',
                  selected ? 'bg-brand-500 text-white' : 'bg-ink-300/30 text-ink-700'
                )}>
                  <Icon size={18} />
                </div>
                <div className="text-[14px] font-semibold text-ink-900">{p.label}</div>
                {selected && <Check size={16} className="text-brand-500 ml-auto" />}
              </div>
              <p className="text-[12px] text-ink-500 leading-relaxed">{p.desc}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function Step6({ data, jumpTo }: { data: WizardData; jumpTo: (step: number) => void }) {
  const profile = returnProfiles.find(r => r.id === data.returnProfile);
  const positioning = positioningTiers.find(p => p.id === data.positioning);
  const noDocs = data.docs.length === 0;

  const rows = [
    { label: 'Deal Name', value: data.dealName || 'Untitled', step: 1 },
    { label: 'Hotel Name', value: data.hotelName || 'Not specified', step: 1 },
    { label: 'Location', value: data.city || 'Not specified', step: 1 },
    { label: 'Keys / Indicative Price', value: `${data.keys || '—'} keys / ${data.price || '—'}`, step: 1 },
    { label: 'Deal Stage', value: data.stage, step: 1 },
    { label: 'Return Requirements', value: profile ? `${profile.label} (${profile.target})` : '—', step: 2 },
    { label: 'Documents', value: noDocs ? 'No documents (can add later)' : `${data.docs.length} files`, step: 3 },
    { label: 'Brand', value: data.brand === 'agnostic' ? 'Brand Agnostic' : data.brand, step: 4 },
    { label: 'Positioning', value: positioning?.label || '—', step: 5 },
  ];

  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Review & Create Deal</h2>
      <p className="text-[12.5px] text-ink-500 mb-6">Review your selections before creating the deal.</p>

      {noDocs && (
        <div className="bg-warn-50 border border-warn-500/30 rounded-lg p-4 mb-5 flex gap-3">
          <AlertTriangle size={16} className="text-warn-700 flex-shrink-0 mt-0.5" />
          <div>
            <div className="text-[12.5px] font-semibold text-warn-700">Shell Deal</div>
            <p className="text-[12px] text-warn-700/90 mt-1 leading-relaxed">
              No documents uploaded. The deal will be created for pipeline tracking. Financial modeling will unlock after document upload.
            </p>
          </div>
        </div>
      )}

      <div className="space-y-2">
        {rows.map(r => (
          <div key={r.label} className="flex items-center justify-between px-4 py-3 bg-ink-300/10 rounded-md">
            <div>
              <div className="text-[11px] text-ink-500 uppercase tracking-wide">{r.label}</div>
              <div className="text-[13px] text-ink-900 font-medium mt-0.5">{r.value}</div>
            </div>
            <button onClick={() => jumpTo(r.step)}
              className="flex items-center gap-1 text-[12px] text-brand-500 hover:text-brand-700 font-medium">
              <Pencil size={11} /> Edit
            </button>
          </div>
        ))}
      </div>

      <div className="mt-6 text-center text-[12px] text-ink-500">
        Ready to create Shell Deal — Add documents from the Data Room to unlock modeling capabilities
      </div>
    </div>
  );
}

function Field({ label, value, onChange, placeholder, type = 'text' }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string;
}) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
    </div>
  );
}

function Select({ label, value, onChange, options }: {
  label: string; value: string; onChange: (v: string) => void; options: readonly string[];
}) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500">
        {options.map(o => <option key={o}>{o}</option>)}
      </select>
    </div>
  );
}
