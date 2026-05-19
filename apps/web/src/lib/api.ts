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
    upload: (dealId: string, files: File[]) => {
      const fd = new FormData();
      files.forEach((f) => fd.append('files', f, f.name));
      return request<WorkerDocument[]>(
        'POST',
        `/deals/${dealId}/documents/upload`,
        undefined,
        { formData: fd },
      );
    },
    extract: (dealId: string, docId: string) =>
      request<ExtractionStartResponse>(
        'POST',
        `/deals/${dealId}/documents/${docId}/extract`,
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
