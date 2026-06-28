// Lightweight typed fetch wrapper around the Fondok worker (FastAPI).
// All endpoints documented in apps/worker/app/api/*.py.
//
// When NEXT_PUBLIC_WORKER_URL is unset, `isWorkerConnected()` returns false
// and consumers should fall back to `lib/mockData.ts`.

import { getCurrentOrgId } from './auth';

const BASE = (process.env.NEXT_PUBLIC_WORKER_URL ?? '').replace(/\/+$/, '');

export const isWorkerConnected = (): boolean => BASE.length > 0;
export const workerUrl = (): string => BASE;

// ─────────────────────────── Worker types ───────────────────────────
// These mirror the Pydantic response models in apps/worker/app/api.
// Kept narrow on purpose — only fields the web app reads.

export interface WorkerDeal {
  id: string;
  tenant_id: string;
  name: string;
  city: string | null;
  keys: number | null;
  service: string | null;
  brand: string | null;
  status: string;
  deal_stage: string | null;
  risk: string | null;
  ai_confidence: number | null;
  // Per-field analyst overrides keyed by extractor field path
  // (e.g. `property_overview.year_built`) → primitive value. May be
  // omitted on older worker builds — always default to {} on read.
  field_overrides?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WorkerDealStatus {
  id: string;
  status: string;
  deal_stage: string | null;
  last_event: string | null;
}

/** Per-assumption provenance map. Sources:
 *    seed              — Kimpton fixture default
 *    deal_row          — set on the deals table (keys, purchase_price)
 *    t12_actual        — extracted from an uploaded T-12
 *    cbre_horizons     — extracted from a CBRE Horizons forecast
 *    pnl_benchmark     — extracted from a P&L benchmark (HotStats-style)
 *    om_comps          — median of OM transaction comps (exit_cap_rate)
 *    om_broker         — broker proforma value on the OM
 *    analyst_override  — set via deal.field_overrides
 */
export type AssumptionSource =
  | 'seed' | 'deal_row' | 't12_actual' | 'cbre_horizons'
  | 'pnl_benchmark' | 'om_comps' | 'om_broker' | 'analyst_override';

export interface AssumptionSourcesResponse {
  id: string;
  sources: Record<string, AssumptionSource | string>;
  values: Record<string, number | string | boolean | null>;
  /** Per-assumption document-id provenance (Sam P3). Maps the
   *  canonical key to the document_id that most likely contributed
   *  the value. Only populated for source labels backed by an
   *  uploaded doc; seed / deal_row / analyst_override are omitted. */
  source_documents?: Record<string, string>;
}

export interface NewDealBody {
  name: string;
  city?: string | null;
  keys?: number | null;
  service?: string | null;
}

export interface WorkerDocument {
  id: string;
  deal_id: string;
  tenant_id: string;
  filename: string;
  doc_type: string | null;
  status: string; // UPLOADED | CLASSIFYING | EXTRACTING | EXTRACTED | FAILED | PARSE_FAILED
  uploaded_at: string;
  content_hash: string | null;
  storage_key: string | null;
  size_bytes: number | null;
  page_count: number | null;
  parser: string | null;
  /** Typed failure kind for FAILED rows (billing | auth | rate_limit | parse | empty_envelope | other). */
  error_kind: string | null;
  /** Friendly explanation the UI surfaces for FAILED docs. */
  error_message: string | null;
  /** USALI compliance score (0-100). NULL → inconclusive (< 5 applicable
   *  rules) or "no P&L data on this doc" — UI surfaces them differently. */
  usali_score?: number | null;
  /** Either the full ``USALIScore`` shape (``{inconclusive, applicable_count,
   *  passed_count, deviations}``) or just the flat deviation list. The badge
   *  + accordion normalize both. */
  usali_deviations?: WorkerUsaliPayload | WorkerUsaliDeviation[] | null;
  /** Guided-onboarding wizard signals (ROADMAP #1).
   *  - ``user_provided_doc_type`` — analyst's tag at upload time (T12, PNL_MONTHLY, …).
   *  - ``fiscal_year`` — optional year the file represents (2025, 2024, …).
   *  - ``misclassified`` — true when the Router disagrees with the user tag. The
   *    DataRoomTab surfaces a MisclassificationBanner with "Use Fondok's
   *    classification" / "Keep mine" choices.
   *  - ``year_mismatch`` — Wave 1 #4: true when the Extractor's
   *    ``period_ending`` year disagrees with the analyst's ``fiscal_year``.
   *    The Data Room surfaces a sibling YearMismatchBanner with the same
   *    "Use Fondok's year" / "Keep mine" UX.
   *  - ``extracted_period_year`` — Fondok's read of the period_ending year
   *    so the banner can render both values without a second fetch. */
  user_provided_doc_type?: string | null;
  fiscal_year?: number | null;
  misclassified?: boolean;
  year_mismatch?: boolean;
  extracted_period_year?: number | null;
}

/** Wizard-step file payload — what the new-project guided onboarding hands to
 *  ``api.documents.upload``. The category drives the right-rail checklist; the
 *  worker only reads ``user_doc_type`` + ``fiscal_year``. */
/**
 * Wave 1 expansion (June 2026) — 11 wizard sub-stages, one per IC-grade
 * doc category. The wizard collapses the legacy four-bucket pattern
 * (`om | financials | str | other`) into the 11 below; the worker reads
 * ``user_doc_type`` directly, so the category id is purely a UI grouping
 * and the worker treats every entry the same. ``surveys`` is the only
 * category marked "recommended" rather than "required for IC" — it
 * shows up gray instead of red in the right-rail until covered.
 */
export type WizardCategory =
  | 'om'
  | 't12'
  | 'historical_pnl'
  | 'str'
  | 'insurance'
  | 'property_tax'
  | 'room_mix'
  | 'capex'
  | 'property_info'
  | 'leases'
  | 'surveys';

export type WizardUserDocType =
  | 'OM'
  | 'T12'
  | 'PNL'
  | 'PNL_MONTHLY'
  | 'PNL_YTD'
  | 'PNL_BENCHMARK'
  | 'STR_TREND'
  | 'STR'
  | 'INSURANCE'
  | 'PROPERTY_TAX'
  | 'ROOM_MIX'
  | 'CAPEX'
  | 'PROPERTY_INFO'
  | 'LEASES'
  | 'SURVEYS';

export interface WizardFile {
  file: File;
  category: WizardCategory;
  /** Analyst's pre-categorization. ``null`` when the analyst picked
   *  "Not sure" — the worker falls back to filename + Router. */
  user_doc_type?: WizardUserDocType | null;
  /** Fiscal year for financials. Optional even for the financials category
   *  (year prompt is OPTIONAL per locked Wave 1 product decision). */
  fiscal_year?: number | null;
}

// ─── USALI compliance ─────────────────────────────────────────────────
// Mirrors ``services.usali_scorer.USALIDeviation`` + ``USALIScore`` JSON.

export type UsaliSeverity = 'CRITICAL' | 'WARN' | 'INFO';

export interface WorkerUsaliDeviation {
  rule_id: string;
  rule_name: string;
  severity: UsaliSeverity;
  message: string;
  actual_value: number | null;
  threshold_min: number | null;
  threshold_max: number | null;
  requires_market_context?: boolean;
}

export interface WorkerUsaliPayload {
  inconclusive?: boolean;
  applicable_count?: number;
  passed_count?: number;
  deviations?: WorkerUsaliDeviation[];
}

export interface ExtractionField {
  field_name: string;
  value: unknown | null;
  unit: string | null;
  source_page: number | null;
  confidence: number | null;
  raw_text: string | null;
}

export interface ExtractionConfidenceReport {
  overall: number;
  by_field: Record<string, number>;
  low_confidence_fields: string[];
  requires_human_review: boolean;
}

export interface ExtractionResult {
  document_id: string;
  status: string;
  fields: ExtractionField[];
  confidence_report: ExtractionConfidenceReport | null;
  agent_version: string | null;
  created_at: string | null;
  /** Per-page text from the parser cache, keyed by page number string. */
  parsed_pages?: Record<string, string>;
  page_count?: number | null;
}

export interface ExtractionStartResponse {
  document_id: string;
  job_id: string;
  status: string;
}

// ─── Engines ────────────────────────────────────────────────────────
// Mirrors apps/worker/app/api/model.py engines_router responses.

export type EngineName =
  | 'revenue'
  | 'fb'
  | 'expense'
  | 'capital'
  | 'debt'
  | 'returns'
  | 'sensitivity'
  | 'partnership';

export type EngineStatus = 'queued' | 'running' | 'complete' | 'failed';

export interface EngineOutputResponse {
  deal_id: string;
  engine: EngineName;
  status: EngineStatus;
  /** One-line headline (e.g. "IRR 23.0% · Multiple 2.37x"). */
  summary: string;
  outputs: Record<string, unknown> | null;
  inputs: Record<string, unknown> | null;
  error: string | null;
  runtime_ms: number | null;
  started_at: string | null;
  completed_at: string | null;
  run_id: string | null;
}

export interface EngineOutputsResponse {
  deal_id: string;
  engines: Record<EngineName, EngineOutputResponse>;
}

export interface EngineRunKickoffResponse {
  deal_id: string;
  run_id: string;
  started_at: string;
  engines: { name: EngineName; status: EngineStatus }[];
}

export interface EngineRunStatusResponse {
  deal_id: string;
  run_id: string;
  engines: EngineOutputResponse[];
}

// ─────────────────────────── core fetcher ───────────────────────────

interface RequestOpts {
  formData?: FormData;
  signal?: AbortSignal;
}

export class WorkerError extends Error {
  status: number;
  body: string;
  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = 'WorkerError';
    this.status = status;
    this.body = body;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts?: RequestOpts,
): Promise<T> {
  if (!BASE) {
    throw new WorkerError(
      'NEXT_PUBLIC_WORKER_URL is not configured',
      0,
      'worker not connected',
    );
  }
  const url = `${BASE}${path}`;
  const headers: Record<string, string> = {};
  // Multi-tenant header: when an active Clerk org is set, scope every
  // request to it. Worker falls back to DEFAULT_TENANT_ID when absent.
  const orgId = getCurrentOrgId();
  if (orgId) headers['X-Tenant-Id'] = orgId;
  const init: RequestInit = { method, headers, signal: opts?.signal };
  if (opts?.formData) {
    init.body = opts.formData;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const res = await fetch(url, init);
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new WorkerError(
      `${method} ${path} → ${res.status}`,
      res.status,
      text,
    );
  }
  // Some 204s have no body
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ─────────────────────────── public api ───────────────────────────

export const api = {
  health: () => request<{ status: string; version: string; db: string }>('GET', '/health'),
  deals: {
    list: (signal?: AbortSignal) =>
      request<WorkerDeal[]>('GET', '/deals', undefined, { signal }),
    create: (deal: NewDealBody) => request<WorkerDeal>('POST', '/deals', deal),
    get: (id: string, signal?: AbortSignal) =>
      request<WorkerDeal>('GET', `/deals/${id}`, undefined, { signal }),
    status: (id: string, signal?: AbortSignal) =>
      request<WorkerDealStatus>('GET', `/deals/${id}/status`, undefined, { signal }),
    /** Per-category coverage score for the workspace CompletenessCard
     *  + wizard right-rail (Wave 1 #1). Returns the 11 canonical
     *  categories with covered/doc_count/required_for_ic. The percent
     *  is over 10 required-for-IC items (SURVEYS excluded). */
    completeness: (id: string, signal?: AbortSignal) =>
      request<CompletenessResponse>(
        'GET',
        `/deals/${id}/completeness`,
        undefined,
        { signal },
      ),
    /** Per-assumption provenance map — which numbers came from
     *  Kimpton seed vs T-12 actual vs CBRE vs analyst override.
     *  Sam v2 #11. */
    assumptionSources: (id: string, signal?: AbortSignal) =>
      request<AssumptionSourcesResponse>(
        'GET',
        `/deals/${id}/assumption_sources`,
        undefined,
        { signal },
      ),
    /** Patch one-or-more deal fields (keys override, brand fix, etc.). */
    update: (
      id: string,
      patch: Partial<Pick<WorkerDeal, 'name' | 'city' | 'keys' | 'service' | 'brand'>> & {
        field_overrides?: Record<string, unknown>;
      },
    ) =>
      request<WorkerDeal>('PATCH', `/deals/${id}`, patch),
  },
  documents: {
    list: (dealId: string, signal?: AbortSignal) =>
      request<WorkerDocument[]>('GET', `/deals/${dealId}/documents`, undefined, { signal }),
    /** Upload one-or-more files. Accepts the legacy ``File[]`` payload
     *  (Data Room bulk drop) and the wizard ``WizardFile[]`` payload
     *  (guided onboarding). Wizard items carry an analyst-picked
     *  ``user_doc_type`` ("Annual / T-12", "Monthly", …) and an optional
     *  ``fiscal_year``; both are sent as positionally-aligned form
     *  arrays so the worker can map ``files[i] → user_doc_types[i] →
     *  fiscal_years[i]``. */
    upload: (dealId: string, files: File[] | WizardFile[]) => {
      const fd = new FormData();
      const isWizard = files.length > 0 && 'file' in (files[0] as object);
      if (isWizard) {
        for (const w of files as WizardFile[]) {
          fd.append('files', w.file, w.file.name);
          // Send empty strings for nullables so the index alignment
          // holds — the worker treats "" the same as missing. Sending
          // ``undefined`` here would drop the slot and shift every
          // subsequent file's metadata one to the left.
          fd.append('user_doc_types', w.user_doc_type ?? '');
          fd.append(
            'fiscal_years',
            w.fiscal_year != null ? String(w.fiscal_year) : '',
          );
        }
      } else {
        (files as File[]).forEach((f) => fd.append('files', f, f.name));
      }
      return request<WorkerDocument[]>(
        'POST',
        `/deals/${dealId}/documents/upload`,
        undefined,
        { formData: fd },
      );
    },
    /** Resolve a misclassification banner (Wave 1 #1).
     *  ``use_ai`` true accepts Fondok's classification; false keeps the
     *  analyst's wizard tag. Either way, ``misclassified`` is cleared. */
    acceptClassification: (
      dealId: string,
      docId: string,
      useAi: boolean,
    ) =>
      request<WorkerDocument>(
        'POST',
        `/deals/${dealId}/documents/${docId}/accept_classification`,
        { use_ai_classification: useAi },
      ),
    /** Resolve a year-mismatch banner (Wave 1 #4).
     *  ``useAi=true`` overwrites ``fiscal_year`` with Fondok's
     *  ``extracted_period_year``; ``false`` keeps the analyst's tag.
     *  Either way, ``year_mismatch`` is cleared. */
    acceptYear: (dealId: string, docId: string, useAi: boolean) =>
      request<WorkerDocument>(
        'POST',
        `/deals/${dealId}/documents/${docId}/accept_year`,
        { use_ai_year: useAi },
      ),
    extract: (dealId: string, docId: string) =>
      request<ExtractionStartResponse>(
        'POST',
        `/deals/${dealId}/documents/${docId}/extract`,
      ),
    /** Re-run parse + extract for a FAILED or PARSE_FAILED document.
     *  Pulls bytes back from storage on the worker; user doesn't re-upload. */
    reprocess: (dealId: string, docId: string) =>
      request<ExtractionStartResponse>(
        'POST',
        `/deals/${dealId}/documents/${docId}/reprocess`,
      ),
    extraction: (dealId: string, docId: string, signal?: AbortSignal) =>
      request<ExtractionResult>(
        'GET',
        `/deals/${dealId}/documents/${docId}/extraction`,
        undefined,
        { signal },
      ),
    /** Direct URL to the raw uploaded file — citation deep-links use ``#page=N``. */
    downloadUrl: (dealId: string, docId: string, page?: number): string => {
      const base = workerUrl();
      if (!base) return '';
      const url = `${base}/deals/${dealId}/documents/${docId}/download`;
      return page && page > 0 ? `${url}#page=${page}` : url;
    },
  },
  engines: {
    /** Run all 8 engines in dependency order (background task on the worker). */
    runAll: (dealId: string, assumptions?: Record<string, unknown>) =>
      request<EngineRunKickoffResponse>(
        'POST',
        `/deals/${dealId}/engines/run`,
        assumptions ? { assumptions } : undefined,
      ),
    /** Run a single engine synchronously; returns the persisted row. */
    runOne: (
      dealId: string,
      name: EngineName,
      assumptions?: Record<string, unknown>,
    ) =>
      request<EngineOutputResponse>(
        'POST',
        `/deals/${dealId}/engines/${name}/run`,
        assumptions ? { assumptions } : undefined,
      ),
    /** Latest persisted output per engine for a deal. */
    getAll: (dealId: string, signal?: AbortSignal) =>
      request<EngineOutputsResponse>(
        'GET',
        `/deals/${dealId}/engines`,
        undefined,
        { signal },
      ),
    /** Latest persisted output for a single engine. */
    getOne: (dealId: string, name: EngineName, signal?: AbortSignal) =>
      request<EngineOutputResponse>(
        'GET',
        `/deals/${dealId}/engines/${name}`,
        undefined,
        { signal },
      ),
    /** All engine rows for a specific run_id (kickoff polling). */
    getRun: (dealId: string, runId: string, signal?: AbortSignal) =>
      request<EngineRunStatusResponse>(
        'GET',
        `/deals/${dealId}/engines/run/${runId}`,
        undefined,
        { signal },
      ),
  },
  analysis: {
    /** Deterministic broker-vs-T12 variance flags for a deal. */
    variance: (dealId: string, signal?: AbortSignal) =>
      request<VarianceReportResult>(
        'GET',
        `/analysis/${dealId}/variance`,
        undefined,
        { signal },
      ),
  },
  /** Wave 1 Validation surface — gap chips, USALI compliance, broker
   *  questions, comp-set drift. The four endpoints below back the
   *  ValidationTab + DataRoomTab badges. */
  validation: {
    /** Document coverage gap audit (ROADMAP #7).
     *  ``lookback_years`` defaults to 5 server-side. */
    coverage: (dealId: string, lookbackYears?: number, signal?: AbortSignal) => {
      const qs =
        lookbackYears && lookbackYears > 0
          ? `?lookback_years=${lookbackYears}`
          : '';
      return request<CoverageResponse>(
        'GET',
        `/deals/${dealId}/document_coverage${qs}`,
        undefined,
        { signal },
      );
    },
    brokerQuestions: {
      /** ``state`` filters to one of pending/sent/answered/dismissed. */
      list: (
        dealId: string,
        state?: BrokerQuestionState,
        signal?: AbortSignal,
      ) => {
        const qs = state ? `?state=${state}` : '';
        return request<BrokerQuestion[]>(
          'GET',
          `/analysis/${dealId}/broker_questions${qs}`,
          undefined,
          { signal },
        );
      },
      patch: (
        dealId: string,
        questionId: string,
        body: BrokerQuestionPatchBody,
      ) =>
        request<BrokerQuestion>(
          'PATCH',
          `/analysis/${dealId}/broker_questions/${questionId}`,
          body,
        ),
      /** Re-runs the deterministic detector against current P&L
       *  extractions and returns the up-to-date list. */
      refresh: (dealId: string) =>
        request<BrokerQuestion[]>(
          'POST',
          `/analysis/${dealId}/broker_questions/refresh`,
          {},
        ),
    },
    compSetDrift: (dealId: string, signal?: AbortSignal) =>
      request<CompSetDriftResponse>(
        'GET',
        `/deals/${dealId}/comp_set_drift`,
        undefined,
        { signal },
      ),
  },
  /** Market intelligence — submarket overview, comp set, transaction comps. */
  market: {
    transactionComps: (dealId: string, signal?: AbortSignal) =>
      request<TransactionCompsResult>(
        'GET',
        `/market/${dealId}/transaction-comps`,
        undefined,
        { signal },
      ),
  },
  /** AI-generated broker due-diligence question packet. */
  dueDiligence: {
    list: (dealId: string, signal?: AbortSignal) =>
      request<DueDiligencePacket>(
        'GET',
        `/deals/${dealId}/due-diligence`,
        undefined,
        { signal },
      ),
    generate: (dealId: string) =>
      request<{ deal_id: string; generated: number; error: string | null }>(
        'POST',
        `/deals/${dealId}/due-diligence/generate`,
      ),
    updateStatus: (
      dealId: string,
      questionId: string,
      status: 'pending' | 'sent' | 'answered',
    ) =>
      request<DueDiligenceQuestion>(
        'PATCH',
        `/deals/${dealId}/due-diligence/${questionId}`,
        { status },
      ),
  },
  /** Context Data Product surface — deal dossier + grounded Q&A. */
  dossier: {
    get: (dealId: string, signal?: AbortSignal) =>
      request<unknown>('GET', `/deals/${dealId}/dossier`, undefined, { signal }),
    ask: (dealId: string, question: string, signal?: AbortSignal) =>
      request<AskAnswerResult>(
        'POST',
        `/deals/${dealId}/ask`,
        { question },
        { signal },
      ),
  },
};

// ─── Ask / Researcher ───────────────────────────────────────────────
// Mirrors AskResponse in apps/worker/app/api/dossier.py.

export interface AskCitationResult {
  document_id: string | null;
  page: number | null;
  field: string | null;
  excerpt: string | null;
}

export interface AskAnswerResult {
  deal_id: string;
  question: string;
  answer: string;
  citations: AskCitationResult[];
  confidence: number;
  note: string | null;
}

// ─── Analysis ───────────────────────────────────────────────────────
// Mirrors apps/worker/app/api/analysis.py VarianceReportResponse shape.

export interface VarianceFlagResult {
  field: string;
  rule_id: string | null;
  /** Title-cased severity per fondok_schemas Severity enum. */
  severity: 'Critical' | 'Warn' | 'Info' | string;
  actual: number | null;
  broker: number | null;
  delta: number | null;
  delta_pct: number | null;
  source_page: number | null;
  note: string | null;
}

export interface VarianceReportResult {
  deal_id: string;
  flags: VarianceFlagResult[];
  critical_count: number;
  warn_count: number;
  info_count: number;
  /** Set when no flags can be computed yet (e.g. only one of broker/T-12 is extracted). */
  note: string | null;
}

// ─── Transaction Comps ──────────────────────────────────────────────
// Mirrors apps/worker/app/api/market.py TransactionCompsResponse.

export interface TransactionCompEntry {
  name: string;
  market: string | null;
  sale_date: string | null;
  keys: number | null;
  sale_price_usd: number | null;
  price_per_key_usd: number | null;
  cap_rate_pct: number | null;
  buyer_name: string | null;
  buyer_type: string | null;
  source_document_id: string | null;
  source_page: number | null;
}

export interface TransactionCompsResult {
  deal_id: string;
  comps: TransactionCompEntry[];
  median_price_per_key: number | null;
  median_cap_rate_pct: number | null;
  note: string | null;
}

// ─── Document Coverage (ROADMAP #7) ─────────────────────────────────
// Mirrors apps/worker/app/api/documents.py DocumentCoverageResponse.

export type CoverageGapType =
  | 'year_missing'
  | 'month_partial'
  | 'annual_no_detail'
  | 'summary_only';

/** Worker emits lowercase ``error|warn|info``. */
export type CoverageGapSeverity = 'error' | 'warn' | 'info';

export interface CoverageGap {
  gap_type: CoverageGapType | string;
  year: number;
  message: string;
  severity: CoverageGapSeverity | string;
  months_missing: number[] | null;
  dismissible: boolean;
}

export interface CoverageDoc {
  doc_id: string | null;
  doc_type: string | null;
  period_type: string | null;
  period_ending: string | null;
}

export interface CoverageResponse {
  deal_id: string;
  /** year → contributing docs (object keys are stringified ints on the wire). */
  year_coverage: Record<string, CoverageDoc[]>;
  gaps: CoverageGap[];
  lookback_years: number;
}

// ─── Broker Questions (ROADMAP #4) ──────────────────────────────────
// Mirrors apps/worker/app/api/analysis.py BrokerQuestionOut + UpdateStateBody.

export type BrokerQuestionState =
  | 'pending'
  | 'dismissed'
  | 'sent'
  | 'answered';

export type BrokerQuestionSeverity = 'CRITICAL' | 'WARN' | 'INFO';

export interface BrokerQuestion {
  id: string;
  deal_id: string;
  line_item: string;
  period_key: string;
  variance_pct: number;
  actual_prior: number | null;
  actual_current: number | null;
  threshold_pct: number;
  severity: BrokerQuestionSeverity;
  question_text: string;
  state: BrokerQuestionState;
  dismissal_reason: string | null;
  broker_response: string | null;
  created_at: string;
  updated_at: string;
}

export interface BrokerQuestionPatchBody {
  next_state: BrokerQuestionState;
  dismissal_reason?: string;
  broker_response?: string;
}

// ─── Comp Set Drift (ROADMAP #8) ────────────────────────────────────
// Mirrors apps/worker/app/services/comp_set_drift.CompSetDriftReportOut.

export interface CompSetEntry {
  name: string;
  keys: number | null;
}

export interface CompSetUncertainMatch {
  from_name: string;
  to_name: string;
  /** 0–1, ≥ 0.80 by construction. */
  similarity: number;
}

export interface CompSetDrift {
  year_from: number;
  year_to: number;
  added: CompSetEntry[];
  removed: CompSetEntry[];
  unchanged: CompSetEntry[];
  uncertain_matches: CompSetUncertainMatch[];
}

export interface CompSetDriftResponse {
  deal_id: string;
  drifts: CompSetDrift[];
}

// ─── Due Diligence ──────────────────────────────────────────────────
// Mirrors apps/worker/app/api/due_diligence.py shapes.

export type DueDiligencePriority = 'high' | 'medium' | 'low';
export type DueDiligenceCategory =
  | 'revenue'
  | 'expenses'
  | 'operations'
  | 'market'
  | 'capex';
export type DueDiligenceStatus = 'pending' | 'sent' | 'answered';

export interface DueDiligenceQuestion {
  id: string;
  deal_id: string;
  question: string;
  narrative: string;
  priority: DueDiligencePriority;
  category: DueDiligenceCategory;
  source: string;
  supporting_metric_key: string | null;
  supporting_metric_value: string | null;
  status: DueDiligenceStatus;
  created_at: string;
  sent_at: string | null;
}

export interface DueDiligencePacket {
  deal_id: string;
  questions: DueDiligenceQuestion[];
  total: number;
  high_priority: number;
  pending: number;
  answered: number;
  note: string | null;
}

// ─── Deal completeness (Wave 1 #1) ──────────────────────────────────
// Mirrors apps/worker/app/api/documents.py CompletenessResponse.

export interface CompletenessCategory {
  /** Stable id — matches WizardCategory above. */
  id: WizardCategory;
  /** Display label ("Offering Memorandum", "Property Taxes", …). */
  label: string;
  /** At least one document of any matching doc_type has been uploaded. */
  covered: boolean;
  /** Total document count contributing to this category. */
  doc_count: number;
  /** Categories with ``required_for_ic=false`` count as Recommended
   *  rather than Missing — currently only Surveys & Reviews. */
  required_for_ic: boolean;
}

export interface CompletenessResponse {
  deal_id: string;
  /** 0-100 percent over the 10 required-for-IC categories (Surveys
   *  excluded). Rounded server-side. */
  completeness_pct: number;
  categories: CompletenessCategory[];
}
