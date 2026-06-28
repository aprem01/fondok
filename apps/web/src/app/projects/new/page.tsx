'use client';
import { useCallback, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Check, ChevronDown, Target, TrendingUp, Rocket, Tag, Search,
  Sparkles, Crown, DollarSign, Pencil, AlertTriangle, ArrowLeft, ChevronRight,
  Loader2, Star, Award,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { dealStages, returnProfiles, positioningTiers, brandFamilies, sourcingChannels } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { api, isWorkerConnected, WizardFile } from '@/lib/api';
import { useToast } from '@/components/ui/Toast';
import { DocumentsStep } from '@/components/project/wizard/DocumentsStep';
import { DocumentsChecklist } from '@/components/project/wizard/DocumentsChecklist';
import { CoachMark } from '@/components/help/CoachMark';

const steps = [
  { n: 1, label: 'Deal Details' },
  { n: 2, label: 'Return Profile' },
  { n: 3, label: 'Documents' },
  { n: 4, label: 'Brand' },
  { n: 5, label: 'Positioning' },
  { n: 6, label: 'Review' },
];

const iconForReturn: Record<string, any> = { core: Target, 'value-add': TrendingUp, opportunistic: Rocket };
// Every positioningTiers id must map here. A missing id renders <undefined />
// and crashes the wizard with React #130 — the `?? Sparkles` fallback at the
// call site is belt-and-suspenders against future tier additions.
const iconForPos: Record<string, any> = {
  default: Sparkles,
  economy: DollarSign,
  midscale: Tag,
  'upper-midscale': Star,
  upscale: TrendingUp,
  'upper-upscale': Award,
  luxury: Crown,
};

export default function NewProjectPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [savedLocally, setSavedLocally] = useState(false);
  const [data, setData] = useState({
    dealName: '', city: '', keys: '', stage: 'Teaser', hotelName: '', price: '',
    returnProfile: 'value-add',
    docs: [] as WizardFile[],
    brand: 'agnostic',
    brandSearch: '',
    expandedFamilies: ['Hilton'] as string[],
    positioning: 'default',
    sourcing: 'Broker',
  });
  // Gate for Step 3 → Step 4: financials are required per locked Wave 1
  // product decision. ``DocumentsStep`` reports this back via
  // onCanContinueChange whenever the WizardFile[] changes.
  const [docsCanContinue, setDocsCanContinue] = useState(false);

  const update = (patch: Partial<typeof data>) => setData(d => ({ ...d, ...patch }));
  const setDocs = useCallback(
    (docs: WizardFile[]) => setData(d => ({ ...d, docs })),
    [],
  );
  // Step 3 (Documents) is gated on financials being present. Other steps
  // advance freely. Wrapping in useCallback so the child can be a pure
  // component on this prop.
  const next = () => {
    if (step === 3 && !docsCanContinue) return;
    setStep(s => Math.min(6, s + 1));
  };
  const back = () => setStep(s => Math.max(1, s - 1));
  const nextDisabled = step === 3 && !docsCanContinue;

  const onCreate = async () => {
    setSubmitError(null);
    if (!data.dealName.trim()) {
      setSubmitError('Deal name is required.');
      toast('Deal name is required', { type: 'error' });
      setStep(1);
      return;
    }
    if (!isWorkerConnected()) {
      // No worker configured — accept the deal locally and continue.
      setSavedLocally(true);
      toast(`Saved · ${data.dealName.trim()}`, { type: 'success' });
      setTimeout(() => router.push('/projects'), 600);
      return;
    }
    setSubmitting(true);
    try {
      // Keys is now optional — the wizard's expectation is that the
      // OM extraction fills it in (`property_overview.keys`). Send
      // null when the analyst hasn't typed a number so the worker
      // schema flows through cleanly instead of pinning a placeholder
      // 100-key value that the deal will then surface as if it were
      // real metadata.
      const parsedKeys = Number.parseInt(data.keys, 10);
      const keysInt =
        Number.isFinite(parsedKeys) && parsedKeys > 0 ? parsedKeys : null;
      const brandValue = data.brand === 'agnostic' ? null : data.brand;

      // The worker's `NewDealBody` schema (apps/worker/app/api/deals.py) is
      // narrow today, but we send the wizard's full intent: extra fields are
      // either persisted by newer worker builds (Phase 6+) or harmlessly
      // ignored. Cast at the call site so we don't have to touch lib/api.ts.
      const body = {
        name: data.dealName.trim(),
        city: data.city.trim() || null,
        keys: keysInt,
        service: null,
        brand: brandValue,
        return_profile: data.returnProfile,
        positioning: data.positioning,
        // Sourcing channel for pipeline analytics. Send the canonical
        // lower-snake-case id (e.g. "capital_partner") not the display
        // label so the worker can use it as an enum.
        sourcing_channel:
          (sourcingChannels.find(s => s.label === data.sourcing)?.id)
          ?? data.sourcing.toLowerCase().replace(/\s+/g, '_'),
      };
      const created = await api.deals.create(body as Parameters<typeof api.deals.create>[0]);
      toast(`Deal created · ${created.name}`, { type: 'success' });

      // If the wizard collected files in step 3, upload them to the
      // new deal. The worker's upload route now auto-chains
      // parse → extract via a background task (see
      // apps/worker/.../documents.py::_run_parse_and_extract). We
      // intentionally DO NOT fire a separate /extract call here —
      // doing so races with the auto-chain (flipping status from
      // PARSING to CLASSIFYING mid-parse) and was the root cause of
      // Sam QA 2026-05-13 #1: "T-12 uploaded in wizard renders 0
      // fields, OM stuck in extracting/processing".
      if (data.docs.length > 0) {
        toast(
          `Uploading ${data.docs.length} document${data.docs.length === 1 ? '' : 's'}…`,
          { type: 'info' },
        );
        try {
          // Send the wizard payload — the worker reads
          // ``user_doc_types[]`` + ``fiscal_years[]`` index-aligned with
          // ``files[]`` and persists them onto the document row, so the
          // Router agent's downstream classification can flag a mismatch
          // against the analyst's intent instead of silently
          // overwriting it.
          const uploaded = await api.documents.upload(
            String(created.id),
            data.docs,
          );
          toast(
            `Uploaded ${uploaded.length} · parsing + extraction running in the background`,
            { type: 'success' },
          );
        } catch (uErr) {
          const uMsg = uErr instanceof Error ? uErr.message : String(uErr);
          toast(`Deal created, but upload failed: ${uMsg}`, { type: 'error' });
        }
      }

      router.push(`/projects/${created.id}`);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      setSubmitError(msg);
      toast(`Couldn't create deal: ${msg}`, { type: 'error' });
      setSubmitting(false);
    }
  };

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
        {step === 3 && (
          <Step3Documents
            files={data.docs}
            onChange={setDocs}
            onCanContinueChange={setDocsCanContinue}
          />
        )}
        {step === 4 && <Step4 data={data} update={update} />}
        {step === 5 && <Step5 data={data} update={update} />}
        {step === 6 && <Step6 data={data} jumpTo={setStep} />}
      </Card>

      {submitError && (
        <Card className="mt-4 p-4 border-danger-500/30 bg-danger-50">
          <div className="flex items-start gap-3">
            <AlertTriangle size={15} className="text-danger-700 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-[12.5px] font-semibold text-danger-700">Couldn’t create deal</div>
              <p className="text-[12px] text-danger-700/85 mt-1">{submitError}</p>
            </div>
            <Button variant="secondary" size="sm" onClick={onCreate}>Retry</Button>
          </div>
        </Card>
      )}

      {savedLocally && (
        <Card className="mt-4 p-4 border-success-500/30 bg-success-50">
          <div className="flex items-center gap-2 text-[12.5px] text-success-700">
            <Check size={14} /> Deal saved.
          </div>
        </Card>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between mt-5">
        <Button variant="secondary" onClick={back} disabled={step === 1 || submitting}>
          <ArrowLeft size={13} /> Back
        </Button>
        {step < 6 ? (
          <Button
            variant="primary"
            onClick={next}
            disabled={nextDisabled}
            title={
              nextDisabled
                ? 'Add at least one financial document to continue'
                : undefined
            }
          >
            Next <ChevronRight size={13} />
          </Button>
        ) : (
          <Button variant="primary" onClick={onCreate} disabled={submitting}>
            {submitting && <Loader2 size={13} className="animate-spin" />}
            {submitting ? 'Creating…' : 'Create Deal'}
          </Button>
        )}
      </div>
    </div>
  );
}

type WizardData = {
  dealName: string; city: string; keys: string; stage: string; hotelName: string; price: string;
  returnProfile: string; docs: WizardFile[]; brand: string; brandSearch: string;
  expandedFamilies: string[]; positioning: string; sourcing: string;
};
type StepProps = { data: WizardData; update: (patch: Partial<WizardData>) => void };

function Step1({ data, update }: StepProps) {
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Create New Deal</h2>
      <p className="text-[12.5px] text-ink-500 mb-3">Capture deal identifiers for pipeline tracking. Supporting documentation can be attached at any point.</p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-6">
        Provide the deal identifiers (asset name, market, acquisition stage). Property metadata
        — key count, year built, gross building area, brand — is extracted from the Offering
        Memorandum when uploaded. All fields remain editable.
      </div>

      <div className="space-y-4">
        <Field label="Deal Name *" value={data.dealName} onChange={v => update({ dealName: v })} placeholder="Chicago Downtown Acquisition" />
        <Field label="City / Submarket *" value={data.city} onChange={v => update({ city: v })} placeholder="Chicago, IL" />
        <div className="grid grid-cols-2 gap-4">
          <Field label="Keys (optional)" value={data.keys} onChange={v => update({ keys: v })} placeholder="auto-detected from OM" type="number"
            help="Guest room count. Leave blank to source from the OM's `property_overview.keys` field on extraction." />
          <Select label="How far along are you in the acquisition process? *" value={data.stage} onChange={v => update({ stage: v })} options={dealStages}
            help="Teaser — pre-NDA screening. Under NDA — accessing the data room. LOI — letter of intent submitted. PSA — under purchase & sale agreement." />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Hotel Name (Optional)" value={data.hotelName} onChange={v => update({ hotelName: v })} placeholder="Marriott Chicago Downtown" />
          <Field label="Indicative Price (Optional)" value={data.price} onChange={v => update({ price: v })} placeholder="$120-140M" />
        </div>
        <CoachMark
          anchorId="wizard-step1-sourcing"
          viewKey="wizard-step1"
          order={0}
          title="Why we ask for sourcing channel"
          body="Sourcing channel tracks deal origin (broker, lender, franchisor, capital partner, proprietary) so we can analyze your pipeline by source over time. Pick the closest match — Fondok rolls these up on the Dashboard."
          side="right"
          learnMoreHref="/methodology#sources"
        >
          <Select label="Sourcing channel *"
            value={data.sourcing}
            onChange={v => update({ sourcing: v })}
            options={sourcingChannels.map(s => s.label)}
            help="Deal origination channel for pipeline attribution — broker network, lender relationship, franchisor direct, operator, capital partner, or proprietary." />
        </CoachMark>
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
  // Institutional example for each return profile. Sam's v2: refine
  // platform language to match institutional hotel-investment workflows
  // rather than retail-investor primers.
  const example: Record<string, string> = {
    core: 'Stabilized institutional-quality asset in a primary market — risk-adjusted income.',
    'value-add': 'Underperforming property with a credible PIP / repositioning thesis.',
    opportunistic: 'Adaptive reuse, ground-up development, or distressed acquisition with execution risk.',
  };
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Return Requirements</h2>
      <p className="text-[12.5px] text-ink-500 mb-3">Select the investment strategy that matches your return targets.</p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-6">
        Selecting an investment profile calibrates the default capital structure (leverage,
        debt cost), exit assumptions (hold, cap rate), and waterfall hurdles. Used as the
        benchmark against which the deal&apos;s underwritten returns are evaluated.
      </div>
      <CoachMark
        anchorId="wizard-step2-profile-cards"
        viewKey="wizard-step2"
        title="What this picks"
        body={<>
          Return profile sets target IRR thresholds and the default capital structure.
          <span className="block mt-1.5"><b>Core</b> 8–12% · <b>Value-Add</b> 12–18% · <b>Opportunistic</b> 18%+.</span>
          You can fine-tune leverage and exit cap on the Returns tab.
        </>}
        side="top"
        learnMoreHref="/methodology#engines"
      >
      <div className="grid grid-cols-3 gap-4">
        {returnProfiles.map(p => {
          const Icon = iconForReturn[p.id] ?? Target;
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
              {example[p.id] && (
                <p className="text-[11px] text-ink-700 mt-2 leading-relaxed">
                  <span className="font-medium text-ink-900">Example: </span>{example[p.id]}
                </p>
              )}
            </button>
          );
        })}
      </div>
      </CoachMark>
    </div>
  );
}

function Step3Documents({
  files,
  onChange,
  onCanContinueChange,
}: {
  files: WizardFile[];
  onChange: (files: WizardFile[]) => void;
  onCanContinueChange: (ok: boolean) => void;
}) {
  const { toast } = useToast();
  // Layout: the DocumentsStep owns its own internal sidebar + content
  // column (11 categories don't fit cleanly into 4/8 columns at the
  // page level). The right-rail checklist hangs off the page so it
  // can stay sticky during long category drilldowns.
  return (
    <div className="grid grid-cols-12 gap-6">
      <div className="col-span-12 lg:col-span-9">
        <DocumentsStep
          files={files}
          onChange={onChange}
          onCanContinueChange={onCanContinueChange}
          onUnsupportedFile={(filename) =>
            toast(
              `${filename}: unsupported file type — Fondok accepts PDF, Excel, CSV, Word.`,
              { type: 'error' },
            )
          }
        />
      </div>
      <aside className="col-span-12 lg:col-span-3">
        <CoachMark
          anchorId="wizard-step3-checklist"
          viewKey="wizard-step3"
          order={2}
          title="Your IC readiness scorecard"
          body="This is the same checklist your IC reviewer will see. The percentage in the workspace later reflects how many categories you've covered. Aim for ≥80% before generating the memo."
          side="left"
        >
          <DocumentsChecklist files={files} />
        </CoachMark>
      </aside>
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

  // Re-clicking the active brand drops back to the agnostic default.
  // Keeps the wizard recoverable without a separate "Clear" affordance.
  const onBrandClick = (name: string) => {
    update({ brand: data.brand === name ? 'agnostic' : name });
  };

  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Select Brand</h2>
      <p className="text-[12.5px] text-ink-500 mb-3">Choose a hotel brand or select brand agnostic for independent analysis.</p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-6">
        Hotel brands work like franchises — each one has different fees, standards, and
        customer expectations (think Marriott vs. Holiday Inn vs. an indie boutique). Pick the
        brand that matches the deal so we can pull in the right benchmarks, or choose
        <span className="font-medium"> Brand Agnostic</span> for an independent hotel.
      </div>

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
            <p className="text-[12px] text-ink-500 mt-1">Analyze without brand constraints — positioning tier selected manually below.</p>
          </div>
          {isAgnostic && <Check size={18} className="text-brand-500" />}
        </div>
      </button>

      <div className="flex items-center gap-3 mb-4">
        <div className="flex-1 h-px bg-border" />
        <span className="text-[11px] text-ink-500 uppercase tracking-wider font-medium">OR SELECT A BRAND</span>
        <div className="flex-1 h-px bg-border" />
      </div>

      {!isAgnostic && (
        <div className="mb-4 px-3 py-2 rounded-md border border-brand-500/40 bg-brand-50 flex items-center gap-2">
          <Check size={14} className="text-brand-500" />
          <div className="text-[12px] text-ink-900">
            Selected: <span className="font-semibold">{data.brand}</span>
          </div>
          <button
            type="button"
            onClick={() => update({ brand: 'agnostic' })}
            className="ml-auto text-[11.5px] text-brand-700 hover:text-brand-500 font-medium"
          >
            Clear
          </button>
        </div>
      )}

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
                  {fam.brands.map(b => {
                    const selected = data.brand === b.name;
                    return (
                      <button
                        key={b.name}
                        onClick={() => onBrandClick(b.name)}
                        aria-pressed={selected}
                        className={cn(
                          'relative p-2.5 rounded-md text-left border-2 transition-colors',
                          selected
                            ? 'border-brand-500 bg-brand-50'
                            : 'border-border hover:border-ink-300'
                        )}
                      >
                        {selected && (
                          <Check size={12} className="absolute top-1.5 right-1.5 text-brand-500" />
                        )}
                        <div className="text-[12px] font-medium text-ink-900 pr-3">{b.name}</div>
                        <div className="text-[10px] text-ink-500 mt-0.5">{b.tier}</div>
                      </button>
                    );
                  })}
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
  // Anchor each tier to consumer-recognizable brands so the choice is concrete.
  const tierExample: Record<string, string> = {
    luxury: 'Ritz-Carlton, Four Seasons, St. Regis.',
    upscale: 'Westin, Marriott full-service, Hyatt Regency.',
    midscale: 'Holiday Inn Express, Hampton Inn, Courtyard.',
    economy: 'Motel 6, Days Inn, Super 8.',
    default: 'No specific tier — Fondok picks based on the brand and ADR.',
  };
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Market Positioning</h2>
      <p className="text-[12.5px] text-ink-500 mb-3">Select the market segment for your analysis.</p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-6">
        Hotels are graded into tiers — luxury, upscale, midscale, economy — based on price
        point and amenities. The tier shapes what comp set we benchmark against and which
        operating ratios are reasonable.
      </div>
      <CoachMark
        anchorId="wizard-step5-tier"
        viewKey="wizard-step5"
        title="Why tier matters"
        body="Position the asset on the chain-scale ladder. Affects which USALI benchmarks, F&B ratios, and labor productivity expectations Fondok applies — getting this wrong skews expense ratios materially."
        side="top"
        learnMoreHref="/methodology#projection"
      >
      <div className="grid grid-cols-2 gap-4">
        {positioningTiers.map(p => {
          const Icon = iconForPos[p.id] ?? Sparkles;
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
              {tierExample[p.id] && (
                <p className="text-[11px] text-ink-700 mt-2 leading-relaxed">
                  <span className="font-medium text-ink-900">Examples: </span>{tierExample[p.id]}
                </p>
              )}
            </button>
          );
        })}
      </div>
      </CoachMark>
    </div>
  );
}

function Step6({ data, jumpTo }: { data: WizardData; jumpTo: (step: number) => void }) {
  const profile = returnProfiles.find(r => r.id === data.returnProfile);
  const positioning = positioningTiers.find(p => p.id === data.positioning);

  // Per-category counts power the inline checklist summary so the
  // analyst can see what they staged + which years they covered before
  // committing. Wave 1 (June 2026): we collapse the 11 categories into
  // four headline groups for the review page so the summary stays
  // scannable — the deal workspace surfaces a full CompletenessCard.
  const omCount = data.docs.filter(f => f.category === 'om').length;
  const financialCount = data.docs.filter(
    f => f.category === 't12' || f.category === 'historical_pnl',
  ).length;
  const strCount = data.docs.filter(f => f.category === 'str').length;
  // Everything else (insurance / taxes / room mix / capex / property
  // info / leases / surveys) rolls up under "Other supporting docs"
  // for the summary headline.
  const otherCount = data.docs.length - omCount - financialCount - strCount;
  const financialYears = Array.from(
    new Set(
      data.docs
        .filter(f => f.category === 't12' || f.category === 'historical_pnl')
        .map(f => f.fiscal_year)
        .filter((y): y is number => typeof y === 'number'),
    ),
  ).sort((a, b) => b - a);

  const docsSummary =
    financialCount > 0
      ? `${data.docs.length} file${data.docs.length === 1 ? '' : 's'} · ${financialCount} financial${financialCount === 1 ? '' : 's'}${financialYears.length > 0 ? ` (${financialYears.join(', ')})` : ''}`
      : 'No financials staged';

  const rows = [
    { label: 'Deal Name', value: data.dealName || 'Untitled', step: 1 },
    { label: 'Hotel Name', value: data.hotelName || 'Not specified', step: 1 },
    { label: 'Location', value: data.city || 'Not specified', step: 1 },
    { label: 'Keys / Indicative Price', value: `${data.keys || '—'} keys / ${data.price || '—'}`, step: 1 },
    { label: 'Deal Stage', value: data.stage, step: 1 },
    { label: 'Return Requirements', value: profile ? `${profile.label} (${profile.target})` : '—', step: 2 },
    { label: 'Documents', value: docsSummary, step: 3 },
    { label: 'Brand', value: data.brand === 'agnostic' ? 'Brand Agnostic' : data.brand, step: 4 },
    { label: 'Positioning', value: positioning?.label || '—', step: 5 },
  ];

  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Review & Create Deal</h2>
      <p className="text-[12.5px] text-ink-500 mb-3">Last check before we create the deal.</p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-6">
        Confirm everything looks right. Click any row to edit a section. Once you click
        <span className="font-medium"> Create Deal</span>, the deal goes into your pipeline,
        files start uploading in the background, and Fondok routes each one to the right extractor.
      </div>

      {financialCount === 0 && (
        <div className="bg-warn-50 border border-warn-500/30 rounded-lg p-4 mb-5 flex gap-3">
          <AlertTriangle size={16} className="text-warn-700 flex-shrink-0 mt-0.5" />
          <div>
            <div className="text-[12.5px] font-semibold text-warn-700">No financials staged</div>
            <p className="text-[12px] text-warn-700/90 mt-1 leading-relaxed">
              Financials are required for engine output. Go back to Step 3 to add at least one
              P&amp;L — or proceed and upload from the Data Room (modeling stays locked until they
              land).
            </p>
          </div>
        </div>
      )}

      {/* Document checklist summary — mirrors Step 3's right-rail so the
          analyst can confirm coverage at a glance without bouncing back. */}
      <Card className="p-4 mb-4" aria-label="Document staging summary">
        <div className="flex items-center justify-between mb-3">
          <div className="text-[12px] uppercase tracking-wider text-ink-500 font-semibold">
            Staged documents
          </div>
          <button
            onClick={() => jumpTo(3)}
            className="flex items-center gap-1 text-[11.5px] text-brand-500 hover:text-brand-700 font-medium"
            aria-label="Edit documents step"
          >
            <Pencil size={10} /> Edit
          </button>
        </div>
        <ul className="grid grid-cols-2 gap-x-6 gap-y-2.5" role="list">
          <SummaryRow
            label="Offering Memorandum"
            count={omCount}
            required={false}
            detail={null}
          />
          <SummaryRow
            label="Financials by year"
            count={financialCount}
            required
            detail={
              financialYears.length > 0 ? (
                <div className="flex flex-wrap gap-1 mt-1">
                  {financialYears.map(y => (
                    <span
                      key={y}
                      className="inline-flex items-center px-1.5 py-0 rounded text-[10.5px] tabular-nums font-medium bg-success-50 text-success-700 border border-success-500/30"
                    >
                      {y}
                    </span>
                  ))}
                </div>
              ) : null
            }
          />
          <SummaryRow
            label="STR comp-set"
            count={strCount}
            required={false}
            detail={null}
          />
          <SummaryRow
            label="Other supporting docs"
            count={otherCount}
            required={false}
            detail={null}
          />
        </ul>
      </Card>

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
    </div>
  );
}

function SummaryRow({
  label,
  count,
  required,
  detail,
}: {
  label: string;
  count: number;
  required: boolean;
  detail: React.ReactNode;
}) {
  const done = count > 0;
  return (
    <li className="flex items-start gap-2" role="listitem">
      <span
        className={cn(
          'inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-semibold flex-shrink-0 mt-0.5',
          done
            ? 'bg-success-50 text-success-700 border border-success-500/30'
            : required
              ? 'bg-danger-50 text-danger-700 border border-danger-500/30'
              : 'bg-ink-100 text-ink-500 border border-border',
        )}
        aria-hidden="true"
      >
        {done ? <Check size={10} /> : count}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-[12.5px] font-medium text-ink-900">{label}</div>
        <div className="text-[11px] text-ink-500 tabular-nums">
          {done ? (
            `${count} file${count === 1 ? '' : 's'}`
          ) : required ? (
            <Badge tone="red">Required</Badge>
          ) : (
            'None'
          )}
        </div>
        {detail}
      </div>
    </li>
  );
}

function Field({ label, value, onChange, placeholder, type = 'text', help }: {
  label: string; value: string; onChange: (v: string) => void; placeholder?: string; type?: string; help?: string;
}) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500" />
      {help && <div className="mt-1 text-[11px] text-ink-500 leading-relaxed">{help}</div>}
    </div>
  );
}

function Select({ label, value, onChange, options, help }: {
  label: string; value: string; onChange: (v: string) => void; options: readonly string[]; help?: string;
}) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)}
        className="w-full px-3 py-2 text-[13px] bg-white border border-border rounded-md focus:outline-none focus:ring-2 focus:ring-brand-100 focus:border-brand-500">
        {options.map(o => <option key={o}>{o}</option>)}
      </select>
      {help && <div className="mt-1 text-[11px] text-ink-500 leading-relaxed">{help}</div>}
    </div>
  );
}
