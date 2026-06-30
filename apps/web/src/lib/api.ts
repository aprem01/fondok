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
  // Wave 3 W3.5 — analyst-declared target levered IRR (fraction). NULL
  // when no opinion is set. Surfaced on the Pipeline view's "meeting
  // target" badge + summary KPI.
  target_irr?: number | null;
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
 *    pnl_benchmark     — extracted from a generic P&L benchmark (HotStats-style)
 *    portfolio_pnl     — analyst's in-house portfolio P&L benchmark
 *                        (Wave 2 P2.7; outranks pnl_benchmark + cbre_horizons
 *                         for op-ratios because the firm's own portfolio is
 *                         the most credible peer set)
 *    om_comps          — median of OM transaction comps (exit_cap_rate)
 *    om_broker         — broker proforma value on the OM
 *    analyst_override  — set via deal.field_overrides
 */
export type AssumptionSource =
  | 'seed' | 'deal_row' | 't12_actual' | 'cbre_horizons'
  | 'pnl_benchmark' | 'portfolio_pnl' | 'om_comps' | 'om_broker' | 'analyst_override'
  | 'str_forecast';

/** Multi-deal pipeline row (Wave 3 W3.5). One per active deal in the
 *  current tenant, enriched with the LATEST engine-output snapshot per
 *  engine. Numbers carry natural units: prices in USD, IRR + cap rate as
 *  fractions (0.18 = 18%), EM as a raw multiple. NULL fields are
 *  meaningful: "no engine run yet" — the UI dashes those cells.
 */
export interface PipelineDealRow {
  deal_id: string;
  name: string;
  state: string; // ONBOARDING / VALIDATING / READY
  status: string; // Draft / Active / …
  city: string | null;
  brand: string | null;
  deal_stage: string | null;
  keys: number | null;
  purchase_price: number | null;
  price_per_key: number | null;
  noi_y1: number | null;
  noi_stabilized: number | null;
  exit_cap_rate: number | null;
  levered_irr: number | null;
  equity_multiple: number | null;
  dscr_y1: number | null;
  document_count: number;
  last_engine_run_at: string | null;
  last_activity_at: string;
  pip_total_usd: number | null;
  target_irr: number | null;
  target_irr_met: boolean | null;
}

export interface PipelineSummary {
  deal_count: number;
  median_irr: number | null;
  p25_irr: number | null;
  p75_irr: number | null;
  median_em: number | null;
  median_per_key: number | null;
  median_cap_rate: number | null;
  deals_meeting_target_irr: number;
  deals_with_target_irr: number;
  deals_by_state: Record<string, number>;
}

export interface PipelineResponse {
  deals: PipelineDealRow[];
  summary: PipelineSummary;
  total_count: number;
  limit: number;
  offset: number;
}

export type PipelineSort =
  | 'irr_desc' | 'irr_asc'
  | 'em_desc' | 'em_asc'
  | 'per_key_asc' | 'per_key_desc'
  | 'cap_rate_asc' | 'cap_rate_desc'
  | 'noi_y1_desc' | 'noi_y1_asc'
  | 'name_asc' | 'name_desc'
  | 'last_activity_desc' | 'last_activity_asc';

export interface PipelineQuery {
  sort?: PipelineSort;
  state?: string;
  deal_stage?: string;
  min_irr?: number;
  max_irr?: number;
  min_per_key?: number;
  max_per_key?: number;
  target_met?: boolean;
  limit?: number;
  offset?: number;
}

// ─── Saved pipeline views + digests (Wave 4 W4.5) ────────────────────
// Mirrors apps/worker/app/api/pipeline_filters.py.

export interface PipelineFilterBody {
  state?: string[] | null;
  min_irr?: number | null;
  max_irr?: number | null;
  min_per_key?: number | null;
  max_per_key?: number | null;
  max_cap_rate?: number | null;
  chain_scales?: string[] | null;
  sort?: PipelineSort | string;
}

export interface SavedViewRecord {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  filter: PipelineFilterBody;
  is_owner_default: boolean;
  created_by: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateSavedViewBody {
  name: string;
  description?: string | null;
  filter: PipelineFilterBody;
  is_owner_default?: boolean;
  created_by?: string | null;
}

export interface UpdateSavedViewBody {
  name?: string;
  description?: string | null;
  filter?: PipelineFilterBody;
  is_owner_default?: boolean;
}

export type DigestCadence = 'daily' | 'weekly' | 'monthly';
export type DigestDelivery = 'slack' | 'email' | 'both';

export interface DigestScheduleRecord {
  id: string;
  tenant_id: string;
  name: string;
  saved_view_id: string | null;
  cadence: DigestCadence;
  weekday: number | null;
  hour_utc: number;
  delivery: DigestDelivery;
  slack_webhook_url: string | null;
  email_recipients: string[];
  include_kpi_summary: boolean;
  include_recently_mutated: boolean;
  include_deals_meeting_target: boolean;
  include_full_table: boolean;
  is_active: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateDigestScheduleBody {
  name: string;
  saved_view_id?: string | null;
  cadence?: DigestCadence;
  weekday?: number | null;
  hour_utc?: number;
  delivery?: DigestDelivery;
  slack_webhook_url?: string | null;
  email_recipients?: string[];
  include_kpi_summary?: boolean;
  include_recently_mutated?: boolean;
  include_deals_meeting_target?: boolean;
  include_full_table?: boolean;
  is_active?: boolean;
}

export interface UpdateDigestScheduleBody extends Partial<CreateDigestScheduleBody> {}

export interface RunNowResponse {
  schedule_id: string;
  dispatched_at: string;
  slack_attempted: boolean;
  slack_succeeded: boolean;
  slack_error: string | null;
  email_attempted: boolean;
  email_succeeded: boolean;
  email_error: string | null;
  no_op_reason: string | null;
  deal_count: number;
}

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
   *  - ``ai_proposed_doc_type`` — Sam QA Bug #2 v2 (June 2026): the Router's
   *    proposal at extraction time, kept SEPARATE from ``doc_type`` (which
   *    stays equal to the analyst tag when ``misclassified=true``). The banner
   *    reads this column for ``aiLabel`` — without it both sides resolved from
   *    ``doc_type`` and rendered "T-12 vs T-12". NULL on non-misclassified
   *    rows and on pre-v2 legacy data (a one-time migration clears stale
   *    ``misclassified=true`` flags so the banner stays hidden until a
   *    re-extraction populates the v2 columns).
   *  - ``year_mismatch`` — Wave 1 #4: true when the Extractor's
   *    ``period_ending`` year disagrees with the analyst's ``fiscal_year``.
   *    The Data Room surfaces a sibling YearMismatchBanner with the same
   *    "Use Fondok's year" / "Keep mine" UX.
   *  - ``extracted_period_year`` — Fondok's read of the period_ending year
   *    so the banner can render both values without a second fetch. */
  user_provided_doc_type?: string | null;
  fiscal_year?: number | null;
  misclassified?: boolean;
  ai_proposed_doc_type?: string | null;
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

// ─────────────────────── revenue segmentation (Wave 2 P2.1) ───────────
//
// Mirrors fondok_schemas.underwriting.RevenueSegment / SegmentYear.
// Empty `segment_breakdown` arrays mean the deal is on the legacy
// single-line revenue path and the PLTab hides the segmentation
// sub-section. When populated, the engine ran the 5-segment model
// and `rooms_revenue` is NET of channel cost.

export type RevenueSegmentName =
  | 'transient_bar'
  | 'transient_ota'
  | 'corporate'
  | 'group'
  | 'contract';

export interface RevenueSegmentInput {
  name: RevenueSegmentName;
  mix_pct: number;
  adr: number;
  channel_cost_pct: number;
  adr_growth: number | null;
}

export interface SegmentYearOutput {
  name: RevenueSegmentName;
  mix_pct: number;
  occupied_rooms: number;
  /** Post-growth, post-Y1-displacement effective ADR for this year. */
  adr: number;
  channel_cost_pct: number;
  gross_revenue: number;
  net_revenue: number;
}

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

// ─────────────────────────── scenarios (Wave 3 W3.2) ────────────────
//
// Mirrors apps/worker/app/api/scenarios.py. A scenario is a named
// what-if layer of overrides on top of the deal's persisted
// ``field_overrides`` — engines run with the scenario applied without
// disturbing the base deal.

export interface ScenarioOverride {
  field_path: string;
  value: unknown;
  source?: string;
}

export interface ScenarioRecord {
  id: string;
  deal_id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  is_base: boolean;
  overrides: ScenarioOverride[];
  last_run_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateScenarioBody {
  name: string;
  description?: string | null;
  overrides: ScenarioOverride[];
}

export interface UpdateScenarioBody {
  name?: string;
  description?: string | null;
  overrides?: ScenarioOverride[];
}

export interface ScenarioRunResponse {
  scenario_id: string;
  deal_id: string;
  run_id: string;
  started_at: string;
  engines: Record<string, EngineOutputResponse>;
}

export interface ScenarioCompareCell {
  scenario_id: string;
  scenario_name: string;
  is_base: boolean;
  last_run_id: string | null;
  engines: Record<string, EngineOutputResponse>;
}

export interface ScenarioCompareResponse {
  deal_id: string;
  base_scenario_id: string | null;
  scenarios: ScenarioCompareCell[];
}

// ─────────────────────────── audit feed (Wave 4 W4.3) ───────────────
//
// Mirrors apps/worker/app/api/audit.py. One row = one append-only event
// in the audit log. The per-deal Activity Feed surfaces the deal-scoped
// slice; the Compliance Explorer surfaces the tenant-wide search.

export type AuditSeverity = 'info' | 'warning' | 'critical';

export interface AuditEntry {
  id: string;
  tenant_id: string;
  deal_id: string | null;
  actor_id: string | null;
  actor_email: string | null;
  actor_ip: string | null;
  user_agent: string | null;
  action: string;
  resource_type: string;
  resource_id: string | null;
  severity: AuditSeverity;
  diff_summary: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  tags: string[] | null;
  payload: Record<string, unknown> | null;
  input_hash: string | null;
  output_hash: string | null;
  created_at: string;
}

export interface DealAuditResponse {
  deal_id: string;
  limit: number;
  offset: number;
  total: number;
  entries: AuditEntry[];
}

export interface ExplorerResponse {
  limit: number;
  offset: number;
  total: number;
  entries: AuditEntry[];
}

export interface DealAuditQuery {
  action?: string;
  entity_type?: string;
  severity?: AuditSeverity;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export interface ExplorerQuery {
  q?: string;
  actor?: string;
  entity_type?: string;
  severity?: AuditSeverity;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

// ─── Portfolio Library — Wave 4 W4.1 ────────────────────────────────
// Mirrors apps/worker/app/api/portfolio_library.py PortfolioLibraryEntryRecord.

export interface PortfolioLibraryEntry {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  vintage_year: number;
  asset_count: number;
  total_rooms_modeled: number;
  chain_scales_covered: string[];
  msa_coverage: string[] | null;
  expense_ratios: Record<string, number>;
  revenue_mix: Record<string, number> | null;
  source_document_id: string | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreatePortfolioLibraryEntryBody {
  name: string;
  description?: string | null;
  vintage_year: number;
  asset_count: number;
  total_rooms_modeled: number;
  chain_scales_covered: string[];
  msa_coverage?: string[] | null;
  expense_ratios: Record<string, number>;
  revenue_mix?: Record<string, number> | null;
  source_document_id?: string | null;
}

export interface UpdatePortfolioLibraryEntryBody {
  name?: string;
  description?: string | null;
  vintage_year?: number;
  asset_count?: number;
  total_rooms_modeled?: number;
  chain_scales_covered?: string[];
  msa_coverage?: string[] | null;
  expense_ratios?: Record<string, number>;
  revenue_mix?: Record<string, number> | null;
  is_active?: boolean;
}

export interface PortfolioLibraryListQuery {
  is_active?: boolean;
  chain_scale?: string;
}

// ─────────────────────────── core fetcher ───────────────────────────

interface RequestOpts {
  formData?: FormData;
  signal?: AbortSignal;
  /** Override the per-method default timeout (ms). Pass 0 to disable
   *  the client-side timeout entirely — only do this for explicitly
   *  long-running streams (none today). */
  timeoutMs?: number;
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

/** Thrown when the request exceeds the configured client-side timeout.
 *
 *  Wave 4 reliability fix (Bug #3): the worker can hang under
 *  extraction load (Bug #2 root cause), and an un-timed fetch leaves
 *  the UI stuck on a skeleton forever. ``TimeoutError`` lets hooks /
 *  pages render a "worker is busy" affordance instead of swallowing
 *  the failure.
 *
 *  ``name === 'TimeoutError'`` is the contract — callers should check
 *  the name string and not ``instanceof`` (the constructor isn't
 *  re-exported on every codepath, and Next's RSC boundary can break
 *  prototype identity).
 */
export class TimeoutError extends Error {
  timeoutMs: number;
  method: string;
  path: string;
  constructor(message: string, method: string, path: string, timeoutMs: number) {
    super(message);
    this.name = 'TimeoutError';
    this.method = method;
    this.path = path;
    this.timeoutMs = timeoutMs;
  }
}

// Per-method client-side timeout defaults (ms). Reads small enough
// that an analyst notices a stuck worker before they switch tabs;
// writes/uploads get more headroom because file uploads + extraction
// kickoff legitimately take longer.
const DEFAULT_TIMEOUT_GET_MS = 20_000;
const DEFAULT_TIMEOUT_WRITE_MS = 60_000;
// File uploads need much more headroom than other writes — the body
// transmission itself takes real time on residential/office upload
// speeds. Sam QA 2026-06-30: the 19.7 MB OM uploads in 2.2s on a
// fast pipe but would silently abort at 60s on a 2-Mbps connection
// (~80s transmission). 5 min covers a 50 MB upload on a 1.5 Mbps
// link with headroom, while still failing fast enough that a
// genuinely stuck server doesn't make the analyst wait forever.
const DEFAULT_TIMEOUT_UPLOAD_MS = 300_000;

function _defaultTimeoutFor(method: string, hasFormData: boolean): number {
  if (hasFormData) return DEFAULT_TIMEOUT_UPLOAD_MS;
  return method === 'GET' ? DEFAULT_TIMEOUT_GET_MS : DEFAULT_TIMEOUT_WRITE_MS;
}

/**
 * Combine an optional caller signal with the timeout signal so either
 * abort path triggers ``fetch``'s cancellation. Prefers the native
 * ``AbortSignal.any`` (Node 20+ / modern browsers) and falls back to a
 * manual fan-in for older runtimes.
 */
function _combineSignals(
  caller: AbortSignal | undefined,
  timeoutSignal: AbortSignal,
): AbortSignal {
  if (!caller) return timeoutSignal;
  // Modern path — keeps reason propagation correct on either side.
  const anyFn = (
    AbortSignal as unknown as { any?: (sigs: AbortSignal[]) => AbortSignal }
  ).any;
  if (typeof anyFn === 'function') {
    return anyFn.call(AbortSignal, [caller, timeoutSignal]);
  }
  const ctrl = new AbortController();
  const onAbort = (reason: unknown) => {
    if (!ctrl.signal.aborted) ctrl.abort(reason);
  };
  if (caller.aborted) onAbort(caller.reason);
  else caller.addEventListener('abort', () => onAbort(caller.reason), { once: true });
  if (timeoutSignal.aborted) onAbort(timeoutSignal.reason);
  else
    timeoutSignal.addEventListener(
      'abort',
      () => onAbort(timeoutSignal.reason),
      { once: true },
    );
  return ctrl.signal;
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

  // ─── Client-side timeout (Wave 4 reliability fix — Bug #3) ───────
  // Every fetch is wrapped in an AbortController that fires after the
  // per-method default (overridable via ``opts.timeoutMs``). If the
  // caller also passed a signal (e.g. ``useDeal`` unmount cleanup),
  // we combine both so either abort path cancels the fetch.
  const hasFormData = !!opts?.formData;
  const timeoutMs =
    opts?.timeoutMs ?? _defaultTimeoutFor(method, hasFormData);
  const timeoutCtrl = new AbortController();
  let timeoutFired = false;
  const timeoutHandle =
    timeoutMs > 0
      ? setTimeout(() => {
          timeoutFired = true;
          timeoutCtrl.abort(
            new TimeoutError(
              `Request timed out after ${timeoutMs}ms: ${method} ${path}`,
              method,
              path,
              timeoutMs,
            ),
          );
        }, timeoutMs)
      : null;
  const signal = _combineSignals(opts?.signal, timeoutCtrl.signal);

  const init: RequestInit = { method, headers, signal };
  if (opts?.formData) {
    init.body = opts.formData;
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  try {
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
  } catch (err) {
    // Distinguish our timeout from caller-driven aborts. ``fetch``
    // raises a ``DOMException`` of name ``AbortError`` on either, so
    // we use the captured flag to decide which one to surface.
    if (timeoutFired) {
      throw new TimeoutError(
        `Request timed out after ${timeoutMs}ms: ${method} ${path}`,
        method,
        path,
        timeoutMs,
      );
    }
    throw err;
  } finally {
    if (timeoutHandle !== null) clearTimeout(timeoutHandle);
  }
}

// ─────────────────────────── public api ───────────────────────────

export const api = {
  health: () => request<{ status: string; version: string; db: string }>('GET', '/health'),
  deals: {
    list: (signal?: AbortSignal) =>
      request<WorkerDeal[]>('GET', '/deals', undefined, { signal }),
    /** Multi-deal Pipeline view (Wave 3 W3.5) — sortable / filterable
     *  table of every active deal in the tenant, enriched with the
     *  latest engine-output snapshot per deal + portfolio-level KPIs
     *  (median IRR, p25/p75, deals meeting target). Backed by a 60s
     *  per-tenant cache so analyst click-storms don't hammer the DB. */
    pipeline: (q: PipelineQuery = {}, signal?: AbortSignal) => {
      const params = new URLSearchParams();
      if (q.sort) params.set('sort', q.sort);
      if (q.state) params.set('state', q.state);
      if (q.deal_stage) params.set('deal_stage', q.deal_stage);
      if (q.min_irr != null) params.set('min_irr', String(q.min_irr));
      if (q.max_irr != null) params.set('max_irr', String(q.max_irr));
      if (q.min_per_key != null) params.set('min_per_key', String(q.min_per_key));
      if (q.max_per_key != null) params.set('max_per_key', String(q.max_per_key));
      if (q.target_met != null) params.set('target_met', String(q.target_met));
      if (q.limit != null) params.set('limit', String(q.limit));
      if (q.offset != null) params.set('offset', String(q.offset));
      const qs = params.toString();
      return request<PipelineResponse>(
        'GET',
        `/deals/pipeline${qs ? `?${qs}` : ''}`,
        undefined,
        { signal },
      );
    },
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
    /** Wave 3 W3.1 — full Comparable Sales set for the deal:
     *  the raw extracted comp rows + the derived median/weighted cap
     *  rate + analyst-readable weighting notes. The Investment tab's
     *  "Comps" sub-panel renders this. */
    compSales: (id: string, signal?: AbortSignal) =>
      request<CompSalesSetResponse>(
        'GET',
        `/deals/${id}/comp-sales`,
        undefined,
        { signal },
      ),
    /** Wave 3 W3.1 — mark a specific comp row as excluded from the
     *  derived cap rate. Idempotent — re-submitting the same id is a
     *  no-op. Returns the refreshed CompSalesSet so the UI can
     *  re-render the derivation without a second round-trip. */
    excludeComp: (id: string, transactionId: string) =>
      request<CompSalesSetResponse>(
        'POST',
        `/deals/${id}/comp-sales/exclude`,
        { transaction_id: transactionId },
      ),
    /** Soft-archive a deal (DELETE /deals/{id}). Flips status to
     *  ``Archived`` — recoverable via direct SQL. The Pipeline view
     *  + Projects list both hide archived deals; the deal page still
     *  loads if you have its UUID. */
    archive: (id: string) =>
      request<WorkerDeal>('DELETE', `/deals/${id}`),
    /** HARD-delete a deal (DELETE /deals/{id}/hard). Cascades:
     *  documents, extraction_results, broker_questions, broker_qa_pairs,
     *  scenarios, engine_outputs. Object-store blobs are NOT deleted
     *  (orphan-tolerant). Audit-logged at severity=warning. Irreversible. */
    delete: (id: string) =>
      request<void>('DELETE', `/deals/${id}/hard`),
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
    /** Hard-delete one document. Cascades extraction_results + object-
     *  store blob (best-effort on storage). Audit-logged. Irreversible. */
    delete: (dealId: string, docId: string) =>
      request<void>(
        'DELETE',
        `/deals/${dealId}/documents/${docId}`,
      ),
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
    /** Wave 2 P2.8 — Pricing sensitivity / max-price / LOI draft.
     *
     * All three are read-only with respect to the deal: they walk the
     * engine chain in memory, flex parameters, return data. None persist
     * to ``engine_outputs`` or mutate the deal row.
     */
    pricing: {
      sensitivity: (
        dealId: string,
        body: PricingSensitivityBody,
        signal?: AbortSignal,
      ) =>
        request<PricingSensitivityResponse>(
          'POST',
          `/analysis/${dealId}/pricing/sensitivity`,
          body,
          { signal },
        ),
      maxPrice: (
        dealId: string,
        body: PricingMaxPriceBody,
        signal?: AbortSignal,
      ) =>
        request<PricingMaxPriceResponse>(
          'POST',
          `/analysis/${dealId}/pricing/max-price`,
          body,
          { signal },
        ),
      loi: (
        dealId: string,
        body: PricingLOIBody,
        signal?: AbortSignal,
      ) =>
        request<PricingLOIResponse>(
          'POST',
          `/analysis/${dealId}/pricing/loi`,
          body,
          { signal },
        ),
    },
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
    /** Wave 1 #5 — Seller Q&A re-ingestion loop.
     *
     * Analyst pastes the broker's emailed reply → ``submit`` runs the
     * QA Resolver agent and persists the round-trip row. The agent's
     * proposed overrides do NOT auto-apply: the analyst confirms a
     * subset via ``apply``. ``list`` returns every QA pair on the deal
     * for the history panel.
     */
    brokerQA: {
      /** POST a broker reply + run the QA Resolver agent.
       *  Returns 402 when the per-deal budget is exhausted. */
      submit: (
        dealId: string,
        body: SubmitBrokerResponseBody,
      ) =>
        request<BrokerQAPair>(
          'POST',
          `/analysis/${dealId}/broker_responses`,
          body,
        ),
      /** List Q&A pairs for a deal, newest first. ``state`` filters on
       *  the resolver verdict (resolved / partially_resolved /
       *  still_concerning). */
      list: (
        dealId: string,
        state?: BrokerQAVerdict,
        signal?: AbortSignal,
      ) => {
        const qs = state ? `?state=${state}` : '';
        return request<BrokerQAPair[]>(
          'GET',
          `/analysis/${dealId}/qa_history${qs}`,
          undefined,
          { signal },
        );
      },
      /** Analyst confirms the subset of proposed overrides to apply.
       *  Empty list ``[]`` is the explicit "skip all" choice (different
       *  from never having called this endpoint). */
      apply: (
        dealId: string,
        qaPairId: string,
        body: ApplyOverridesBody,
      ) =>
        request<BrokerQAPair>(
          'PATCH',
          `/analysis/${dealId}/broker_responses/${qaPairId}/apply`,
          body,
        ),
    },
    compSetDrift: (dealId: string, signal?: AbortSignal) =>
      request<CompSetDriftResponse>(
        'GET',
        `/deals/${dealId}/comp_set_drift`,
        undefined,
        { signal },
      ),
    /** Wave 2 P2.6 — 3-5 year historical baseline (Sam's ask).
     *
     *  Returns the per-year P&L roll-up + the YoY walk (sorted by
     *  abs(yoy_pct) DESC with a 0.5% noise floor). The UI hides the
     *  panel when ``coverage_pct === 0`` (no historical docs uploaded).
     */
    historicalBaseline: (dealId: string, signal?: AbortSignal) =>
      request<HistoricalBaselineResponse>(
        'GET',
        `/deals/${dealId}/historical-baseline`,
        undefined,
        { signal },
      ),
    /** Wave 3 W3.3 — STR forward forecast (24 months × 3 scenarios).
     *  GET pulls the engine result computed from the deal's STR_TREND
     *  monthly extractions. POST overrides one or more scenarios'
     *  knobs and returns the recomputed forecast.
     */
    strForecast: (dealId: string, signal?: AbortSignal) =>
      request<STRForecastResponse>(
        'GET',
        `/deals/${dealId}/str-forecast`,
        undefined,
        { signal },
      ),
    updateStrForecastScenarios: (
      dealId: string,
      body: STRForecastScenariosRequest,
    ) =>
      request<STRForecastResponse>(
        'POST',
        `/deals/${dealId}/str-forecast/scenarios`,
        body,
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
  /** Wave 4 W4.5 — saved pipeline filters. Named filter + sort
   *  presets the analyst recalls from the pipeline page.
   *  ``is_owner_default`` pins as the actor's landing filter. */
  pipelineViews: {
    list: (signal?: AbortSignal) =>
      request<SavedViewRecord[]>('GET', '/pipeline-views', undefined, { signal }),
    create: (body: CreateSavedViewBody) =>
      request<SavedViewRecord>('POST', '/pipeline-views', body),
    get: (id: string, signal?: AbortSignal) =>
      request<SavedViewRecord>('GET', `/pipeline-views/${id}`, undefined, { signal }),
    update: (id: string, patch: UpdateSavedViewBody) =>
      request<SavedViewRecord>('PATCH', `/pipeline-views/${id}`, patch),
    delete: (id: string) =>
      request<void>('DELETE', `/pipeline-views/${id}`),
    setDefault: (id: string) =>
      request<SavedViewRecord>('POST', `/pipeline-views/${id}/set-default`),
  },
  /** Wave 4 W4.5 — recurring Slack/email pipeline digests. */
  pipelineDigests: {
    list: (signal?: AbortSignal) =>
      request<DigestScheduleRecord[]>('GET', '/pipeline-digests', undefined, { signal }),
    create: (body: CreateDigestScheduleBody) =>
      request<DigestScheduleRecord>('POST', '/pipeline-digests', body),
    update: (id: string, patch: UpdateDigestScheduleBody) =>
      request<DigestScheduleRecord>('PATCH', `/pipeline-digests/${id}`, patch),
    delete: (id: string) =>
      request<void>('DELETE', `/pipeline-digests/${id}`),
    runNow: (id: string) =>
      request<RunNowResponse>('POST', `/pipeline-digests/${id}/run-now`),
  },
  /** Wave 3 W3.2 — named what-if scenarios per deal. */
  scenarios: {
    list: (dealId: string, signal?: AbortSignal) =>
      request<ScenarioRecord[]>(
        'GET',
        `/deals/${dealId}/scenarios`,
        undefined,
        { signal },
      ),
    create: (dealId: string, body: CreateScenarioBody) =>
      request<ScenarioRecord>(
        'POST',
        `/deals/${dealId}/scenarios`,
        body,
      ),
    get: (dealId: string, scenarioId: string, signal?: AbortSignal) =>
      request<ScenarioRecord>(
        'GET',
        `/deals/${dealId}/scenarios/${scenarioId}`,
        undefined,
        { signal },
      ),
    update: (dealId: string, scenarioId: string, patch: UpdateScenarioBody) =>
      request<ScenarioRecord>(
        'PATCH',
        `/deals/${dealId}/scenarios/${scenarioId}`,
        patch,
      ),
    delete: (dealId: string, scenarioId: string) =>
      request<ScenarioRecord>(
        'DELETE',
        `/deals/${dealId}/scenarios/${scenarioId}`,
      ),
    run: (dealId: string, scenarioId: string) =>
      request<ScenarioRunResponse>(
        'POST',
        `/deals/${dealId}/scenarios/${scenarioId}/run`,
      ),
    compare: (dealId: string, scenarioIds: string[]) =>
      request<ScenarioCompareResponse>(
        'POST',
        `/deals/${dealId}/scenarios/compare`,
        { scenario_ids: scenarioIds },
      ),
  },
  /** Wave 4 W4.3 — Activity Feed (per-deal) + Compliance Explorer
   *  (tenant-wide). Surfaces the existing append-only audit log to the
   *  analyst UI so "who changed what when" is one click away. */
  audit: {
    deal: (dealId: string, q: DealAuditQuery = {}, signal?: AbortSignal) => {
      const params = new URLSearchParams();
      if (q.action) params.set('action', q.action);
      if (q.entity_type) params.set('entity_type', q.entity_type);
      if (q.severity) params.set('severity', q.severity);
      if (q.since) params.set('since', q.since);
      if (q.until) params.set('until', q.until);
      if (q.limit != null) params.set('limit', String(q.limit));
      if (q.offset != null) params.set('offset', String(q.offset));
      const qs = params.toString();
      return request<DealAuditResponse>(
        'GET',
        `/deals/${dealId}/audit${qs ? `?${qs}` : ''}`,
        undefined,
        { signal },
      );
    },
    explorer: (q: ExplorerQuery = {}, signal?: AbortSignal) => {
      const params = new URLSearchParams();
      if (q.q) params.set('q', q.q);
      if (q.actor) params.set('actor', q.actor);
      if (q.entity_type) params.set('entity_type', q.entity_type);
      if (q.severity) params.set('severity', q.severity);
      if (q.since) params.set('since', q.since);
      if (q.until) params.set('until', q.until);
      if (q.limit != null) params.set('limit', String(q.limit));
      if (q.offset != null) params.set('offset', String(q.offset));
      const qs = params.toString();
      return request<ExplorerResponse>(
        'GET',
        `/audit/explorer${qs ? `?${qs}` : ''}`,
        undefined,
        { signal },
      );
    },
  },
  /** Wave 4 W4.1 — firm-level Portfolio P&L Library. Tenant-scoped via
   *  the X-Tenant-Id header (mirrors the active Clerk org). The engine
   *  pulls every active entry whose ``chain_scales_covered`` overlaps
   *  the subject deal's chain scale within the 3-year vintage look-back
   *  and feeds the per-ratio median as the portfolio_pnl candidate. */
  portfolioLibrary: {
    list: (q: PortfolioLibraryListQuery = {}, signal?: AbortSignal) => {
      const params = new URLSearchParams();
      if (q.is_active !== undefined) params.set('is_active', String(q.is_active));
      if (q.chain_scale) params.set('chain_scale', q.chain_scale);
      const qs = params.toString();
      const path = qs ? `/portfolio-library?${qs}` : '/portfolio-library';
      return request<PortfolioLibraryEntry[]>('GET', path, undefined, { signal });
    },
    create: (body: CreatePortfolioLibraryEntryBody) =>
      request<PortfolioLibraryEntry>('POST', '/portfolio-library', body),
    get: (id: string, signal?: AbortSignal) =>
      request<PortfolioLibraryEntry>(
        'GET',
        `/portfolio-library/${id}`,
        undefined,
        { signal },
      ),
    update: (id: string, patch: UpdatePortfolioLibraryEntryBody) =>
      request<PortfolioLibraryEntry>(
        'PATCH',
        `/portfolio-library/${id}`,
        patch,
      ),
    deactivate: (id: string) =>
      request<PortfolioLibraryEntry>(
        'POST',
        `/portfolio-library/${id}/deactivate`,
      ),
    activate: (id: string) =>
      request<PortfolioLibraryEntry>(
        'POST',
        `/portfolio-library/${id}/activate`,
      ),
    delete: (id: string) =>
      request<PortfolioLibraryEntry>('DELETE', `/portfolio-library/${id}`),
    upload: (form: FormData) =>
      request<PortfolioLibraryEntry>(
        'POST',
        '/portfolio-library/upload',
        undefined,
        { formData: form },
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

// ─── Pricing — Wave 2 P2.8 ──────────────────────────────────────────
// Mirrors apps/worker/app/api/analysis.py pricing endpoints.

export interface PricingSensitivityBody {
  target_irr?: number;
  target_em?: number;
  cap_axis?: number[];
  noi_axis?: number[];
}

export interface PricingSensitivityCell {
  exit_cap_pct: number;
  noi_multiplier: number;
  levered_irr: number;
  equity_multiple: number;
  going_in_cap_rate: number;
  dscr_y1: number;
  breaches_dscr_floor: boolean;
}

export interface PricingSensitivityResponse {
  deal_id: string;
  base_exit_cap_pct: number;
  base_stabilized_noi: number;
  cells: PricingSensitivityCell[];
  breakeven_exit_cap_pct: number | null;
  breakeven_noi_multiplier: number | null;
}

export interface PricingMaxPriceBody {
  target_irr?: number;
  target_em?: number;
}

export interface PricingMaxPriceResponse {
  deal_id: string;
  target_irr: number;
  target_em: number;
  max_price_for_irr: number;
  max_price_for_em: number;
  binding_constraint: 'irr' | 'em' | 'both';
  final_price_per_key: number;
  iters: number;
}

export interface PricingLOIBody {
  target_irr?: number;
  target_em?: number;
  buyer?: string;
  seller?: string;
  earnest_money_pct?: number;
  due_diligence_days?: number;
  closing_days_from_pa?: number;
  financing_contingency?: string;
  exclusivity_days?: number;
  representation?: string;
  valid_until?: string;
  contingencies?: string[];
  proposed_price_override?: number;
}

export interface PricingLOIResponse {
  deal_id: string;
  buyer: string;
  seller: string;
  asset_name: string;
  asset_address: string;
  rooms: number;
  proposed_price: number;
  proposed_price_per_key: number;
  earnest_money_pct: number;
  deposit_at_pa: number;
  due_diligence_days: number;
  closing_days_from_pa: number;
  financing_contingency: string;
  exclusivity_days: number;
  representation: string;
  valid_until: string;
  contingencies: string[];
  rendered_markdown: string;
}

// ─── Comparable Sales (Wave 3 W3.1) ─────────────────────────────────
// Mirrors apps/worker/app/api/deals.py CompSalesSetOut.

export interface CompTransactionRow {
  property_name: string | null;
  city: string | null;
  state: string | null;
  sale_date: string | null;
  keys: number | null;
  sale_price_usd: number | null;
  sale_price_per_key_usd: number | null;
  noi_usd: number | null;
  cap_rate_pct: number | null;
  chain_scale: string | null;
  brand_family: string | null;
  flag: string | null;
  source_document_id: string;
  source_page_number: number | null;
  note: string | null;
  transaction_id: string | null;
}

export interface CompSalesSetResponse {
  deal_id: string;
  transactions: CompTransactionRow[];
  total_count: number;
  derived_cap_rate_median: number | null;
  derived_cap_rate_weighted: number | null;
  derived_cap_rate_method: 'median' | 'weighted' | 'none';
  weighting_notes: string[];
  coverage_quality: 'high' | 'medium' | 'low';
  subject_market: string | null;
  subject_chain_scale: string | null;
  lookback_years: number;
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

// ─── Broker Q&A re-ingestion (Wave 1 #5) ──────────────────────────────
//
// Mirrors:
//   - apps/worker/app/api/analysis.BrokerQAPairOut
//   - apps/worker/app/api/analysis.ProposedOverrideOut
//   - apps/worker/app/api/analysis.SubmitBrokerResponseBody
//   - apps/worker/app/api/analysis.ApplyOverridesBody
//
// Wire-shape only — UI helpers (verdict tone, badge label, etc.) live
// next to the QAResolutionInline component, not here.

export type BrokerQAVerdict =
  | 'resolved'
  | 'partially_resolved'
  | 'still_concerning';

export type ProposedOverrideConfidence = 'high' | 'medium' | 'low';

export interface ProposedOverride {
  field_path: string;
  value: number | string;
  rationale: string;
  confidence: ProposedOverrideConfidence;
}

export interface BrokerQAPair {
  id: string;
  deal_id: string;
  tenant_id: string;
  broker_question_id: string;
  analyst_question: string;
  broker_response: string;
  resolver_verdict: BrokerQAVerdict | null;
  resolver_summary: string | null;
  proposed_overrides: ProposedOverride[];
  /** ``null`` = pending decision; ``[]`` = analyst reviewed + skipped all;
   *  non-empty list = chosen subset that landed in
   *  ``deals.field_overrides``. */
  applied_overrides: ProposedOverride[] | null;
  audit_note: string | null;
  created_at: string;
  updated_at: string;
}

export interface SubmitBrokerResponseBody {
  broker_question_id: string;
  broker_response: string;
}

export interface ApplyOverridesBody {
  override_indexes_to_apply: number[];
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

// ─── Historical Baseline (Wave 2 P2.6) ──────────────────────────────
// Mirrors apps/worker/app/api/documents.py HistoricalBaselineResponse +
// HistoricalYearOut + YoYDeltaOut. Sam's June 2026 ask: "Institutional
// IC analysts will not approve a deal without seeing the multi-year
// trend." Every numeric is ``number | null`` — null means the
// extractor didn't ship that line (UI renders an em-dash).
export interface HistoricalYear {
  fiscal_year: number;
  occupancy: number | null;
  adr: number | null;
  revpar: number | null;
  rooms_revenue: number | null;
  fnb_revenue: number | null;
  other_revenue: number | null;
  total_revenue: number | null;
  rooms_dept_expense: number | null;
  fnb_dept_expense: number | null;
  other_dept_expense: number | null;
  /** A&G + sales/mkt + utilities + prop_ops + info/telecom. */
  undistributed: number | null;
  gop: number | null;
  /** property_tax + insurance + mgmt_fee (institutional fixed-block). */
  fixed_expenses: number | null;
  noi: number | null;
  source_document_ids: string[];
}

export interface YoYDelta {
  line: string;
  year: number;
  value: number;
  yoy_abs: number | null;
  /** Signed decimal — ``-0.05`` = down 5%. ``null`` for the first
   *  year of the series (no prior to compare). The walk is sorted by
   *  ``abs(yoy_pct) DESC`` with ``null`` entries pushed to the end. */
  yoy_pct: number | null;
}

export interface HistoricalBaselineResponse {
  deal_id: string;
  years: HistoricalYear[];
  /** Missing fiscal years between min and max (inclusive). */
  gaps: number[];
  look_back_years: number;
  /** ``years.length / look_back_years`` — UI hides the panel at 0. */
  coverage_pct: number;
  /** YoY walk projection; biggest abs(yoy_pct) first. */
  walk: YoYDelta[];
}

// ─── STR Forward Forecast (Wave 3 W3.3) ─────────────────────────────
// Mirrors apps/worker/app/api/documents.py _STRForecastResultOut.

export type STRForecastScenarioName = 'downside' | 'base' | 'upside';

export interface STRMonth {
  /** YYYY-MM. */
  period: string;
  /** 0..1 (NOT a percent). */
  occupancy: number;
  adr: number;
  revpar: number;
  comp_set_revpar: number;
  /** subject revpar / comp_set_revpar. */
  revpar_index: number;
  /** True for ingested STR Trend rows, False for forecast rows. */
  is_historical: boolean;
}

export interface STRForecastScenario {
  name: STRForecastScenarioName;
  revpar_cagr_pct: number;
  revpar_index_target: number;
  occupancy_floor: number;
  /** Multiplier on trailing-12 ADR. */
  adr_floor: number;
  notes: string[];
}

export interface STRForecastResponse {
  deal_id: string;
  historical_months: STRMonth[];
  /** Keyed by scenario name → 24 forward months. */
  forecast_months: Record<string, STRMonth[]>;
  scenario_settings: STRForecastScenario[];
  coverage_quality: 'high' | 'medium' | 'low';
}

export interface STRForecastScenarioOverride {
  name: STRForecastScenarioName;
  revpar_cagr_pct?: number;
  revpar_index_target?: number;
  occupancy_floor?: number;
  adr_floor?: number;
  notes?: string[];
}

export interface STRForecastScenariosRequest {
  scenarios: STRForecastScenarioOverride[];
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
