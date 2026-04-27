// Lightweight typed fetch wrapper around the Fondok worker (FastAPI).
// All endpoints documented in apps/worker/app/api/*.py.
//
// When NEXT_PUBLIC_WORKER_URL is unset, `isWorkerConnected()` returns false
// and consumers should fall back to `lib/mockData.ts`.

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
  status: string;
  deal_stage: string | null;
  risk: string | null;
  ai_confidence: number | null;
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
  status: string; // UPLOADED | CLASSIFYING | EXTRACTING | EXTRACTED | FAILED
  uploaded_at: string;
  content_hash: string | null;
  storage_key: string | null;
  size_bytes: number | null;
  page_count: number | null;
  parser: string | null;
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
}

export interface ExtractionStartResponse {
  document_id: string;
  job_id: string;
  status: string;
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
  const init: RequestInit = { method, headers: {}, signal: opts?.signal };
  if (opts?.formData) {
    init.body = opts.formData;
  } else if (body !== undefined) {
    init.headers = { 'Content-Type': 'application/json' };
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
  },
};
