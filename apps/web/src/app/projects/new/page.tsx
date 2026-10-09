'use client';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  Check, ChevronDown, Target, TrendingUp, Rocket, Tag, Search,
  Sparkles, Crown, DollarSign, Pencil, AlertTriangle, ArrowLeft, ChevronRight,
  Loader2, Star, Award, Info,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { dealStages, returnProfiles, positioningTiers, sourcingChannels, brandChain, brandFamilyShort, brandDefaultPositioning, searchBrandFamilies } from '@/lib/mockData';
import { cn } from '@/lib/format';
import { api, isWorkerConnected, WizardFile } from '@/lib/api';
import { useToast } from '@/components/ui/Toast';
import { DocumentsStep, WIZARD_CATEGORIES } from '@/components/project/wizard/DocumentsStep';
import { DocumentsChecklist } from '@/components/project/wizard/DocumentsChecklist';
import { CoachMark } from '@/components/help/CoachMark';
import { useNow } from '@/lib/hooks/useNow';
import { formatElapsed } from '@/lib/progress';
import { normalizeLocation, locationSuggestions } from '@/lib/markets';
import { DEAL_TYPE_OPTIONS, dealTypeLabel } from '@/lib/dealTypes';
import { OPERATING_MODEL_OPTIONS, isOperatingModelId, operatingModelLabel } from '@/lib/operatingModel';
import { unsupportedFileMessage } from '@/lib/uploadFormats';
import {
  loadDraft, saveDraft, clearDraft, relativeTime, DRAFT_DEBOUNCE_MS,
  loadDefaultReturnProfile, saveDefaultReturnProfile, type DraftFileRef,
} from '@/lib/wizardDraft';

const steps = [
  { n: 1, label: 'Deal Details' },
  { n: 2, label: 'Return Profile' },
  { n: 3, label: 'Documents' },
  // R-024 — one step: picking a brand pre-fills its default positioning,
  // which the analyst can still change for this property.
  { n: 4, label: 'Brand and Positioning' },
  { n: 5, label: 'Review' },
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

type WizardFields = Omit<WizardData, 'docs'>;

// The wizard's untouched state. `returnProfile` is replaced by the analyst's
// saved default (R-015) when one exists.
const INITIAL_FIELDS: WizardFields = {
  dealName: '', city: '', keys: '', stage: 'Teaser', hotelName: '', price: '',
  dealType: 'acquisition',
  returnProfile: 'value-add',
  // FON-59 / R-048 — `brand` is the picker state = the analyst's PROPOSED
  // brand (submitted as `proposed_brand`); `existingBrand` is the optional
  // current flag (submitted as `brand`; blank = sourced from the OM).
  brand: 'agnostic',
  existingBrand: '',
  // R-025 — intended operating model ('' = not chosen → sent as null).
  operatingModel: '',
  brandSearch: '',
  expandedFamilies: ['Hilton'],
  positioning: 'default',
  sourcing: 'Broker',
};

const isKnownProfile = (id: string | null | undefined): id is string =>
  !!id && returnProfiles.some((p) => p.id === id);

function freshFields(defaultProfile: string | null): WizardFields {
  return {
    ...INITIAL_FIELDS,
    expandedFamilies: [...INITIAL_FIELDS.expandedFamilies],
    returnProfile: isKnownProfile(defaultProfile) ? defaultProfile : INITIAL_FIELDS.returnProfile,
  };
}

/** Keep only draft keys the wizard knows, with the expected shape. */
function pickDraftFields(raw: Record<string, unknown>): Partial<WizardFields> {
  const out: Partial<WizardFields> = {};
  for (const k of Object.keys(INITIAL_FIELDS) as (keyof WizardFields)[]) {
    const v = raw[k];
    if (k === 'expandedFamilies') {
      if (Array.isArray(v) && v.every((x) => typeof x === 'string')) out.expandedFamilies = v as string[];
    } else if (typeof v === 'string') {
      (out as Record<string, string>)[k] = v;
    }
  }
  return out;
}

/** R-011 — only a wizard the analyst actually touched is worth a draft
 *  (opening the page must not leave a "Restored…" banner behind). UI-only
 *  state (brand search box, expanded chains) doesn't count. */
function draftIsMeaningful(fields: WizardFields, step: number, fileCount: number, defaultProfile: string | null): boolean {
  if (step > 1 || fileCount > 0) return true;
  const base = freshFields(defaultProfile);
  return (Object.keys(base) as (keyof WizardFields)[]).some(
    (k) => k !== 'brandSearch' && k !== 'expandedFamilies' && fields[k] !== base[k],
  );
}

const sameFile = (a: DraftFileRef, b: DraftFileRef) => a.name === b.name && a.category === b.category;

export default function NewProjectPage() {
  const router = useRouter();
  const { toast } = useToast();
  const [step, setStep] = useState(1);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [savedLocally, setSavedLocally] = useState(false);
  // R-039 — the upload is the long part of "Create Deal". Track when it
  // started and how many files are in flight so the loading state shows a
  // measured elapsed counter ("Uploading 12 files · 2:37") instead of an
  // anonymous spinner. Cleared the moment the upload settles.
  const [uploadStartedAt, setUploadStartedAt] = useState<number | null>(null);
  const [uploadingCount, setUploadingCount] = useState(0);
  const now = useNow(uploadStartedAt != null);
  // R-011 — warn before leaving only while files are still leaving the
  // browser; the deal itself already exists by then.
  useEffect(() => {
    if (uploadStartedAt == null) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', onBeforeUnload);
    return () => window.removeEventListener('beforeunload', onBeforeUnload);
  }, [uploadStartedAt]);
  const uploadCopy =
    uploadStartedAt != null
      ? `Uploading ${uploadingCount} file${uploadingCount === 1 ? '' : 's'} · ${formatElapsed((now - uploadStartedAt) / 1000)}`
      : null;
  const [data, setData] = useState<WizardData>(() => ({ ...freshFields(null), docs: [] }));
  // Gate for Step 3 → Step 4: financials are required per locked Wave 1
  // product decision. ``DocumentsStep`` reports this back via
  // onCanContinueChange whenever the WizardFile[] changes.
  const [docsCanContinue, setDocsCanContinue] = useState(false);
  // Step-3 gate-warning surfaces only after the analyst attempts a
  // disabled Next. Idle state = quiet panel; nudge state = WARN banner
  // appears under the panel exactly once per attempt.
  const [docsGateNudge, setDocsGateNudge] = useState(false);

  const update = (patch: Partial<typeof data>) => setData(d => ({ ...d, ...patch }));

  // ─── R-011 — autosave + restore (browser-local draft) ───────────────────
  // Read on mount (not in the state initializer) so the server render and
  // the first client render agree; autosave only starts after that read.
  const [hydrated, setHydrated] = useState(false);
  const [restoredAt, setRestoredAt] = useState<number | null>(null);
  // Files the restored draft had staged. Bytes can't survive a reload, so
  // they're listed for re-attach and drop off as the analyst re-adds them.
  const [pendingFiles, setPendingFiles] = useState<DraftFileRef[]>([]);
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved'>('idle');
  const createdRef = useRef(false);
  // R-015 — the analyst's default Return Profile (this browser).
  const [defaultProfile, setDefaultProfile] = useState<string | null>(null);

  useEffect(() => {
    const def = loadDefaultReturnProfile();
    const validDef = isKnownProfile(def) ? def : null;
    setDefaultProfile(validDef);
    const draft = loadDraft();
    if (draft) {
      setData({ ...freshFields(validDef), ...pickDraftFields(draft.fields), docs: [] });
      setStep(Math.min(steps.length, Math.max(1, Math.round(draft.step))));
      setRestoredAt(draft.savedAt);
      setPendingFiles(draft.files);
    } else if (validDef) {
      setData(d => ({ ...d, returnProfile: validDef }));
    }
    setHydrated(true);
  }, []);

  const remainingFiles = useMemo(
    () => pendingFiles.filter(p => !data.docs.some(d => sameFile(p, { name: d.file.name, category: d.category }))),
    [pendingFiles, data.docs],
  );

  useEffect(() => {
    if (!hydrated || createdRef.current) return;
    const { docs, ...fields } = data;
    const files: DraftFileRef[] = [
      ...docs.map(d => ({ name: d.file.name, category: d.category, fiscal_year: d.fiscal_year ?? null })),
      ...remainingFiles,
    ];
    if (!draftIsMeaningful(fields, step, files.length, defaultProfile)) {
      clearDraft();
      setSaveStatus('idle');
      return;
    }
    setSaveStatus('saving');
    const t = setTimeout(() => {
      if (createdRef.current) return;
      setSaveStatus(saveDraft({ savedAt: Date.now(), step, fields, files }) ? 'saved' : 'idle');
    }, DRAFT_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [data, step, hydrated, remainingFiles, defaultProfile]);

  const discardDraft = () => {
    clearDraft();
    setData({ ...freshFields(defaultProfile), docs: [] });
    setStep(1);
    setRestoredAt(null);
    setPendingFiles([]);
    setDocsGateNudge(false);
    setSaveStatus('idle');
  };

  const setDefaultReturnProfile = (id: string | null) => {
    saveDefaultReturnProfile(id);
    setDefaultProfile(id);
  };

  // ─── R-012 — City / Submarket suggestions ───────────────────────────────
  // Distinct cities already used on this tenant's deals, then the static
  // US market list. A failed / absent list just means market names only.
  const [tenantCities, setTenantCities] = useState<string[]>([]);
  useEffect(() => {
    if (!isWorkerConnected()) return;
    const ctrl = new AbortController();
    Promise.resolve()
      .then(() => api.deals.list(ctrl.signal))
      .then((deals) => {
        if (ctrl.signal.aborted || !Array.isArray(deals)) return;
        setTenantCities(
          deals.map(d => d.city).filter((c): c is string => typeof c === 'string' && c.trim().length > 0),
        );
      })
      .catch(() => { /* suggestions are best-effort */ });
    return () => ctrl.abort();
  }, []);
  const citySuggestions = useMemo(() => locationSuggestions(tenantCities), [tenantCities]);
  const normalizeCity = (v: string) => normalizeLocation(v, tenantCities);
  const setDocs = useCallback(
    (docs: WizardFile[]) => setData(d => ({ ...d, docs })),
    [],
  );
  // Step 3 (Documents) is gated on financials being present. Other steps
  // advance freely. When the analyst clicks Next on Step 3 without
  // financials, we don't silently no-op — we surface the WARN banner
  // inside DocumentsStep so the gate is visible (rather than the Next
  // button just refusing to move).
  const next = () => {
    if (step === 3 && !docsCanContinue) {
      setDocsGateNudge(true);
      return;
    }
    if (step === 3) setDocsGateNudge(false);
    setStep(s => Math.min(5, s + 1));
  };
  const back = () => setStep(s => Math.max(1, s - 1));
  // Visual disabled cue (color + cursor) stays, but the button still
  // fires the click handler so we can surface the WARN — `disabled`
  // would swallow the click and leave the analyst confused why
  // nothing happened.
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
      createdRef.current = true;
      clearDraft();
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
      // FON-59 / R-048 (Sam's decision 4) — two separate fields. The picker
      // is the PROPOSED brand ("agnostic" → none); the typed Existing brand
      // goes to `brand`, and blank leaves it for the OM to fill.
      const proposedBrandValue = data.brand === 'agnostic' ? null : data.brand;
      const existingBrandValue = data.existingBrand.trim() || null;
      // R-025 — the intended operating model; '' (not chosen) → null.
      const operatingModelValue = isOperatingModelId(data.operatingModel) ? data.operatingModel : null;

      // The worker's `NewDealBody` schema (apps/worker/app/api/deals.py) is
      // narrow today, but we send the wizard's full intent: extra fields are
      // either persisted by newer worker builds (Phase 6+) or harmlessly
      // ignored. Cast at the call site so we don't have to touch lib/api.ts.
      const body = {
        name: data.dealName.trim(),
        // R-012 — trim / comma-space / fold onto a known market spelling.
        city: normalizeCity(data.city) || null,
        keys: keysInt,
        service: null,
        deal_type: data.dealType,
        brand: existingBrandValue,
        proposed_brand: proposedBrandValue,
        operating_model: operatingModelValue,
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
      // R-011 — the deal exists now; a restored draft would only create a
      // duplicate, so drop it before the (long) upload starts.
      createdRef.current = true;
      clearDraft();
      setSaveStatus('idle');
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
        setUploadingCount(data.docs.length);
        setUploadStartedAt(Date.now());
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
        } finally {
          setUploadStartedAt(null);
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
        <div className="flex items-center justify-between gap-3">
          <h1 className="text-[24px] font-semibold text-ink-900">New Project</h1>
          {saveStatus !== 'idle' && (
            <span data-testid="draft-save-status" aria-live="polite" className="text-[11.5px] text-ink-500">
              {saveStatus === 'saving' ? 'Saving…' : 'Saved locally'}
            </span>
          )}
        </div>
      </div>

      {restoredAt != null && (
        <Card className="p-4 mb-5 border-brand-100 bg-brand-50" data-testid="draft-restored-banner">
          <div className="flex items-start gap-3">
            <Info size={15} className="text-brand-500 flex-shrink-0 mt-0.5" aria-hidden="true" />
            <div className="flex-1 min-w-0">
              <div className="text-[12.5px] text-ink-900">
                Restored your unfinished deal from {relativeTime(restoredAt)}
                <span className="text-ink-500"> · </span>
                <button
                  type="button"
                  onClick={discardDraft}
                  className="text-[12.5px] font-medium text-brand-700 hover:text-brand-500"
                >
                  Discard
                </button>
              </div>
              {remainingFiles.length > 0 && (
                <div className="mt-2 text-[12px] text-ink-700" data-testid="draft-reattach">
                  <div>
                    Re-attach {remainingFiles.length === 1 ? 'this file' : `these ${remainingFiles.length} files`} on
                    the Documents step — browsers can&apos;t keep files between visits:
                  </div>
                  <ul className="mt-1 list-disc pl-5">
                    {remainingFiles.map(f => (
                      <li key={`${f.category}::${f.name}`}>
                        {f.name}
                        <span className="text-ink-500">
                          {' '}· {WIZARD_CATEGORIES.find(c => c.id === f.category)?.label ?? f.category}
                          {typeof f.fiscal_year === 'number' ? ` · ${f.fiscal_year}` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

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
        {step === 1 && (
          <Step1 data={data} update={update} citySuggestions={citySuggestions} normalizeCity={normalizeCity} />
        )}
        {step === 2 && (
          <Step2 data={data} update={update} defaultProfile={defaultProfile} onSetDefault={setDefaultReturnProfile} />
        )}
        {step === 3 && (
          <Step3Documents
            files={data.docs}
            onChange={(files) => {
              setDocs(files);
              // Drop the gate-nudge as soon as something lands — the
              // banner only fires on the next gated attempt.
              if (docsGateNudge) setDocsGateNudge(false);
            }}
            onCanContinueChange={setDocsCanContinue}
            showGateWarning={docsGateNudge && !docsCanContinue}
          />
        )}
        {step === 4 && <Step4 data={data} update={update} />}
        {step === 5 && <Step6 data={data} jumpTo={setStep} />}
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

      {/* R-039 — visible elapsed counter for the whole upload, measured
          from the moment the files started leaving the browser. The deal
          page opens as soon as the upload settles. */}
      {submitting && uploadCopy && (
        <Card className="mt-4 p-4 border-brand-100 bg-brand-50">
          <div
            role="status"
            aria-live="polite"
            className="flex items-center gap-2 text-[12.5px] text-ink-700"
          >
            <Loader2 size={14} className="animate-spin text-brand-500" aria-hidden="true" />
            <span className="font-medium tabular-nums">{uploadCopy}</span>
            <span className="text-ink-500">· the deal opens when the upload finishes</span>
          </div>
        </Card>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between mt-5">
        <Button variant="secondary" onClick={back} disabled={step === 1 || submitting}>
          <ArrowLeft size={13} /> Back
        </Button>
        {step < 5 ? (
          <Button
            variant="primary"
            onClick={next}
            // Visual disabled (aria + styling) but click still fires
            // so the gated-Next click can trigger the WARN banner
            // inside DocumentsStep. The handler short-circuits when
            // gated — no spurious advances.
            aria-disabled={nextDisabled || undefined}
            className={cn(nextDisabled && 'opacity-60 cursor-not-allowed')}
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
            {submitting ? (uploadCopy ?? 'Creating…') : 'Create Deal'}
          </Button>
        )}
      </div>
    </div>
  );
}

type WizardData = {
  dealName: string; city: string; keys: string; stage: string; hotelName: string; price: string;
  dealType: string;
  returnProfile: string; docs: WizardFile[]; brand: string; existingBrand: string; operatingModel: string; brandSearch: string;
  expandedFamilies: string[]; positioning: string; sourcing: string;
};

// FON-46 — Deal Type classifies the project so Fondok applies the right
// engines, assumptions, and workflows downstream. FON-41 / R-047 — shared
// with the Overview toggle: Acquisition · Development · Adaptive Reuse.
const DEAL_TYPES = DEAL_TYPE_OPTIONS;
type StepProps = { data: WizardData; update: (patch: Partial<WizardData>) => void };

function Step1({ data, update, citySuggestions, normalizeCity }: StepProps & {
  citySuggestions: string[];
  normalizeCity: (v: string) => string;
}) {
  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Create New Deal</h2>
      <p className="text-[12.5px] text-ink-500 mb-3">Capture deal identifiers for pipeline tracking. Supporting documentation can be attached at any point.</p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-6">
        Provide the deal identifiers (asset name, market, acquisition stage). Property metadata
        — key count, year built, gross building area, brand — is extracted from the Offering
        Memorandum when uploaded. All fields remain editable.
      </div>
      {/* R-013 — legend for the starred labels below; unstarred = optional. */}
      <p className="text-[11px] text-ink-500 leading-relaxed mb-3">* Required field</p>

      <div className="space-y-4">
        <div>
          <label className="block text-[12.5px] font-medium text-ink-700 mb-1.5">Deal Type *</label>
          <div className="grid grid-cols-3 gap-2">
            {DEAL_TYPES.map((dt) => {
              const active = data.dealType === dt.id;
              return (
                <button
                  key={dt.id}
                  type="button"
                  onClick={() => update({ dealType: dt.id })}
                  aria-pressed={active}
                  className={cn(
                    'text-left rounded-md border p-3 transition-colors',
                    active ? 'border-brand-500 bg-brand-50 ring-1 ring-brand-500' : 'border-border hover:border-ink-300',
                  )}
                >
                  <div className={cn('text-[12.5px] font-semibold', active ? 'text-brand-700' : 'text-ink-900')}>{dt.label}</div>
                  <div className="text-[11px] text-ink-500 mt-0.5 leading-snug">{dt.desc}</div>
                </button>
              );
            })}
          </div>
          <p className="text-[11px] text-ink-400 mt-1.5">Classifies the deal so Fondok applies the right engines and assumptions.</p>
        </div>
        <Field label="Deal Name *" value={data.dealName} onChange={v => update({ dealName: v })} placeholder="Chicago Downtown Acquisition" />
        {/* R-012 — suggestions from this tenant's deals + US hotel markets;
            the typed value is tidied / folded onto a known spelling on blur
            (and again on save). Free text is always allowed. */}
        <Field
          label="City / Submarket *"
          value={data.city}
          onChange={v => update({ city: v })}
          onBlur={v => {
            const n = normalizeCity(v);
            if (n !== v) update({ city: n });
          }}
          placeholder="Chicago, IL"
          listId="wizard-city-suggestions"
        />
        <datalist id="wizard-city-suggestions" data-testid="city-suggestions">
          {citySuggestions.map(c => <option key={c} value={c} />)}
        </datalist>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Keys" value={data.keys} onChange={v => update({ keys: v })} placeholder="auto-detected from OM" type="number"
            help="Guest room count. Leave blank to source from the OM's `property_overview.keys` field on extraction." />
          <Select label="How far along are you in the acquisition process? *" value={data.stage} onChange={v => update({ stage: v })} options={dealStages}
            help="Teaser — pre-NDA screening. Under NDA — accessing the data room. LOI — letter of intent submitted. PSA — under purchase & sale agreement." />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <Field label="Hotel Name" value={data.hotelName} onChange={v => update({ hotelName: v })} placeholder="Marriott Chicago Downtown" />
          <Field label="Indicative Price" value={data.price} onChange={v => update({ price: v })} placeholder="$120-140M" />
        </div>
        <CoachMark
          anchorId="wizard-step1-sourcing"
          viewKey="wizard-step1"
          order={0}
          title="Why we ask for sourcing channel"
          body="Sourcing channel tracks deal origin (broker, lender, franchisor, capital partner, direct) so we can analyze your pipeline by source over time. Pick the closest match — Fondok rolls these up on the Dashboard."
          side="right"
          learnMoreHref="/methodology#sources"
        >
          <Select label="Sourcing channel *"
            value={data.sourcing}
            onChange={v => update({ sourcing: v })}
            options={sourcingChannels.map(s => s.label)}
            help="Deal origination channel for pipeline attribution — broker network, lender relationship, franchisor direct, operator, capital partner, or direct." />
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

function Step2({ data, update, defaultProfile, onSetDefault }: StepProps & {
  defaultProfile: string | null;
  onSetDefault: (id: string | null) => void;
}) {
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
          Return profile sets target LIRR thresholds and the default capital structure.
          <span className="block mt-1.5"><b>Core</b> 8–12% · <b>Value-Add</b> 12–18% · <b>Opportunistic</b> 18%+.</span>
          You can calibrate leverage and exit cap on the Returns tab.
        </>}
        side="top"
        learnMoreHref="/methodology#engines"
      >
      <div className="grid grid-cols-3 gap-4">
        {returnProfiles.map(p => {
          const Icon = iconForReturn[p.id] ?? Target;
          const selected = data.returnProfile === p.id;
          const isDefault = defaultProfile === p.id;
          return (
            <div key={p.id} className="flex flex-col gap-1.5">
            <button onClick={() => update({ returnProfile: p.id })}
              aria-pressed={selected}
              className={cn(
                'p-5 rounded-lg border-2 text-left transition-colors flex-1',
                selected ? 'border-brand-500 bg-brand-50' : 'border-border bg-white hover:border-ink-300'
              )}>
              <div className="flex items-start justify-between mb-3">
                <div className={cn('w-10 h-10 rounded-lg flex items-center justify-center', selected ? 'bg-brand-500 text-white' : 'bg-ink-300/30 text-ink-700')}>
                  <Icon size={18} />
                </div>
                {selected && <Check size={18} className="text-brand-500" />}
              </div>
              <div className="text-[14px] font-semibold text-ink-900">{p.label}</div>
              <div className="text-[12px] text-brand-700 font-medium mt-1">Target LIRR: {p.target}</div>
              <p className="text-[11.5px] text-ink-500 mt-2 leading-relaxed">{p.desc}</p>
              {example[p.id] && (
                <p className="text-[11px] text-ink-700 mt-2 leading-relaxed">
                  <span className="font-medium text-ink-900">Example: </span>{example[p.id]}
                </p>
              )}
            </button>
            {/* R-015 — remembered per analyst in this browser and preselected on new deals. */}
            <div className="flex items-center gap-2 px-1 text-[11.5px]" data-testid={`profile-default-${p.id}`}>
              {isDefault ? (
                <>
                  <Badge tone="blue">Default</Badge>
                  <button type="button" onClick={() => onSetDefault(null)}
                    className="text-ink-500 hover:text-ink-900 font-medium">
                    Clear default
                  </button>
                </>
              ) : (
                <button type="button" onClick={() => onSetDefault(p.id)}
                  aria-label={`Set ${p.label} as default`}
                  className="text-brand-700 hover:text-brand-500 font-medium">
                  Set as default
                </button>
              )}
            </div>
            </div>
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
  showGateWarning,
}: {
  files: WizardFile[];
  onChange: (files: WizardFile[]) => void;
  onCanContinueChange: (ok: boolean) => void;
  showGateWarning: boolean;
}) {
  const { toast } = useToast();
  // Layout: the DocumentsStep owns its own internal sidebar + content
  // column. The right-rail completeness ring shows only at xl (≥1280px);
  // below that the main panel takes the full width so the sidebar +
  // content panel never get squeezed.
  return (
    <div className="grid grid-cols-12 gap-6">
      <div className="col-span-12 xl:col-span-9">
        <DocumentsStep
          files={files}
          onChange={onChange}
          onCanContinueChange={onCanContinueChange}
          showGateWarning={showGateWarning}
          onUnsupportedFile={(filename) =>
            toast(
              unsupportedFileMessage(filename),
              { type: 'error' },
            )
          }
        />
      </div>
      <aside className="hidden xl:block xl:col-span-3">
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
  // R-019 — a search must surface the SPECIFIC brand, never just its
  // collapsed parent chain. A family-name hit keeps every brand in that
  // family; a brand-name hit keeps the matches; and any family with
  // results stays expanded while a query is active, so "Kimpton" shows
  // the Kimpton card (· IHG) instead of a closed "IHG Hotels & Resorts"
  // row. The submitted value is unchanged: always the specific brand.
  // R-020 — aliases count too, so "Starwood" finds the legacy Starwood
  // brands that now sit under Marriott.
  const filtered = searchBrandFamilies(q);
  const selectedChain = isAgnostic ? null : brandChain(data.brand);

  // Re-clicking the active brand drops back to the agnostic default.
  // Keeps the wizard recoverable without a separate "Clear" affordance.
  // R-024 — choosing a brand pre-fills its default positioning (the brand's
  // chain scale); the analyst can still change it below for this property.
  const onBrandClick = (name: string) => {
    if (data.brand === name) {
      update({ brand: 'agnostic' });
      return;
    }
    const prefill = brandDefaultPositioning(name);
    update(prefill ? { brand: name, positioning: prefill } : { brand: name });
  };

  return (
    <div>
      <h2 className="text-[18px] font-semibold text-ink-900 mb-1">Brand and Positioning</h2>
      <p className="text-[12.5px] text-ink-500 mb-3">Choose a hotel brand or select brand agnostic for independent analysis.</p>
      <div className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-6">
        Hotel brands work like franchises — each one has different fees, standards, and
        customer expectations (think Marriott vs. Holiday Inn vs. an indie boutique). Pick the
        brand that matches the deal so we can pull in the right benchmarks, or choose
        <span className="font-medium"> Brand Agnostic</span> for an independent hotel.
      </div>

      <div className="mb-6">
        <Field
          label="Existing brand"
          value={data.existingBrand}
          onChange={v => update({ existingBrand: v })}
          placeholder="e.g. Kimpton"
          help="Leave blank to source from the Offering Memorandum"
        />
      </div>

      {/* R-025 — the intended operating model, linked to the business plan.
          Optional; re-clicking the active choice clears it. */}
      <div className="mb-6" role="radiogroup" aria-label="Operating model" data-testid="operating-model">
        <div className="text-[12px] font-medium text-ink-700 mb-1.5">Operating model</div>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
          {OPERATING_MODEL_OPTIONS.map(o => {
            const active = data.operatingModel === o.id;
            return (
              <button
                key={o.id}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => update({ operatingModel: active ? '' : o.id })}
                className={cn(
                  'p-3 rounded-lg border-2 text-left transition-colors',
                  active ? 'border-brand-500 bg-brand-50' : 'border-border bg-white hover:border-ink-300',
                )}
              >
                <div className="text-[12.5px] font-semibold text-ink-900">{o.label}</div>
                <div className="text-[11.5px] text-ink-500 mt-0.5 leading-snug">{o.desc}</div>
              </button>
            );
          })}
        </div>
        <div className="text-[11px] text-ink-500 mt-1.5">Optional — how the hotel will be run under the business plan.</div>
      </div>

      <div className="text-[12px] font-medium text-ink-700 mb-1.5">Proposed brand</div>
      {/* R-018 — contextual note on what the proposed brand will drive. */}
      <div
        role="note"
        data-testid="brand-wip-note"
        className="rounded-md bg-brand-50 border border-brand-100 p-3 text-[12px] text-ink-700 leading-relaxed mb-3 flex gap-2"
      >
        <Info size={14} className="text-brand-500 flex-shrink-0 mt-0.5" aria-hidden="true" />
        <span>
          This section is a work in progress. The intent is to use the selected brand&apos;s preliminary
          programming requirements when assessing the property improvement plan and required CapEx.
        </span>
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
            {selectedChain && <span className="text-ink-500"> · {selectedChain}</span>}
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
          const expanded = q.length > 0 || data.expandedFamilies.includes(fam.family);
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
                        <div className="text-[10px] text-ink-500 mt-0.5">{b.tier} · {brandFamilyShort(fam)}</div>
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-8">
        <PositioningPicker data={data} update={update} />
      </div>
    </div>
  );
}

function PositioningPicker({ data, update }: StepProps) {
  const prefill = data.brand === 'agnostic' ? null : brandDefaultPositioning(data.brand);
  const prefillLabel = prefill ? positioningTiers.find(p => p.id === prefill)?.label : null;
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
      <h3 className="text-[15px] font-semibold text-ink-900 mb-1">Positioning</h3>
      <p className="text-[12.5px] text-ink-500 mb-3">
        {prefillLabel
          ? <>Pre-filled from {data.brand}: <span className="font-medium text-ink-900">{prefillLabel}</span> (the brand&apos;s chain scale). Change it if this property positions differently.</>
          : 'Select the market segment for your analysis.'}
      </p>
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
    f => f.category === 'financials',
  ).length;
  const strCount = data.docs.filter(f => f.category === 'str').length;
  // Everything else (insurance / taxes / room mix / capex / property
  // info / leases / surveys) rolls up under "Other supporting docs"
  // for the summary headline.
  const otherCount = data.docs.length - omCount - financialCount - strCount;
  const financialYears = Array.from(
    new Set(
      data.docs
        .filter(f => f.category === 'financials')
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
    { label: 'Deal Type', value: dealTypeLabel(data.dealType), step: 1 },
    { label: 'Hotel Name', value: data.hotelName || 'Not specified', step: 1 },
    { label: 'Location', value: data.city || 'Not specified', step: 1 },
    { label: 'Keys / Indicative Price', value: `${data.keys || '—'} keys / ${data.price || '—'}`, step: 1 },
    { label: 'Deal Stage', value: data.stage, step: 1 },
    { label: 'Return Requirements', value: profile ? `${profile.label} (${profile.target})` : '—', step: 2 },
    { label: 'Documents', value: docsSummary, step: 3 },
    {
      label: 'Existing Brand',
      value: data.existingBrand.trim() || 'From the Offering Memorandum',
      step: 4,
    },
    {
      label: 'Proposed Brand',
      value: data.brand === 'agnostic'
        ? 'Brand Agnostic'
        : <>{data.brand}{brandChain(data.brand) && <span className="text-ink-500 font-normal"> · {brandChain(data.brand)}</span>}</>,
      step: 4,
    },
    {
      label: 'Operating Model',
      value: operatingModelLabel(data.operatingModel) ?? 'Not specified',
      step: 4,
    },
    { label: 'Positioning', value: positioning?.label || '—', step: 4 },
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

function Field({ label, value, onChange, onBlur, placeholder, type = 'text', help, listId }: {
  label: string; value: string; onChange: (v: string) => void; onBlur?: (v: string) => void;
  placeholder?: string; type?: string; help?: string; listId?: string;
}) {
  return (
    <div>
      <label className="block text-[12px] font-medium text-ink-700 mb-1.5">{label}</label>
      <input type={type} value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder}
        onBlur={onBlur ? e => onBlur(e.target.value) : undefined}
        list={listId} autoComplete={listId ? 'off' : undefined}
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
