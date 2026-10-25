/**
 * Human labels for the audit log — the Activity tab and the Audit explorer.
 *
 * The worker records events as ``entity.verb`` actions
 * (``engine_run.kicked_off``, ``override.set``), entity types
 * (``portfolio_library_entry``), override key paths
 * (``debt_stack.tranches.1.principal_usd``) and Python-rendered values
 * (``None``, ``'2025-09-30'``). None of that is copy a client reads: every
 * token is mapped here, with a humanising fallback so a new event type still
 * reads as words rather than as an identifier.
 */
import { humanizeFieldName } from '@/lib/fieldLabels';

// ─────────────────────────── actions ───────────────────────────

const ACTION_LABEL: Record<string, string> = {
  'deal.created': 'Deal created',
  'deal.updated': 'Deal details updated',
  'deal.archived': 'Deal archived',
  'deal.deleted': 'Deal deleted',
  'deal.duplicated': 'Deal duplicated',
  'deal.metadata_synced_from_extraction': 'Deal details synced from documents',
  'deal.positioning_suggested_from_om': 'Positioning suggested from the Offering Memorandum',
  'deal.status_changed': 'Deal status changed',
  'override.set': 'Assumption changed',
  'override.cleared': 'Assumption override cleared',
  'override.deleted': 'Assumption override cleared',
  'engine_run.kicked_off': 'Model run started',
  'engine_run.completed': 'Model run completed',
  'engine_run.failed': 'Model run failed',
  'engine_run.scenario': 'Scenario run',
  'document.uploaded': 'Document uploaded',
  'document.deleted': 'Document removed',
  'document.reclassified': 'Document type changed',
  'document.classification_accepted': 'Document type confirmed',
  'document.year_accepted': 'Document year confirmed',
  'document.extraction_timed_out': 'Extraction timed out',
  'document.re_extract_requested': 'Re-extraction requested',
  'document.field_reviewed': 'Extracted field reviewed',
  'scenario.created': 'Scenario created',
  'scenario.updated': 'Scenario updated',
  'scenario.deleted': 'Scenario deleted',
  'export.excel': 'Excel model downloaded',
  'export.downloaded': 'Export downloaded',
  'memo.generated': 'IC memo drafted',
  'memo.recommendation_confirmed': 'IC recommendation confirmed',
  'memo.diligence_status_changed': 'Diligence status changed',
  'comp_transaction.created': 'Comparable sale added',
  'comp_transaction.updated': 'Comparable sale updated',
  'comp_transaction.deleted': 'Comparable sale removed',
  'portfolio_library_entry.created': 'Portfolio library entry added',
  'portfolio_library_entry.updated': 'Portfolio library entry updated',
  'portfolio_library_entry.deleted': 'Portfolio library entry removed',
};

const ENTITY_LABEL: Record<string, string> = {
  deal: 'Deal',
  scenario: 'Scenario',
  override: 'Assumption',
  document: 'Document',
  engine_run: 'Model run',
  export: 'Export',
  comp_transaction: 'Comparable sale',
  portfolio_library_entry: 'Portfolio library entry',
  memo: 'IC memo',
  extraction: 'Extraction',
  tenant: 'Workspace',
  user: 'User',
};

const VERB_LABEL: Record<string, string> = {
  kicked_off: 'started',
  set: 'changed',
  created: 'created',
  updated: 'updated',
  deleted: 'removed',
  archived: 'archived',
  completed: 'completed',
  failed: 'failed',
  uploaded: 'uploaded',
  requested: 'requested',
  cleared: 'cleared',
  accepted: 'confirmed',
};

function words(token: string): string {
  return token.replace(/[_-]+/g, ' ').trim();
}

function sentenceCase(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/** The entity type as a reader sees it ("Model run"), never the token. */
export function entityLabel(entityType: string | null | undefined): string {
  if (!entityType) return 'Record';
  return ENTITY_LABEL[entityType] ?? sentenceCase(words(entityType));
}

/** The action as a reader sees it ("Model run started"), never ``engine_run.kicked_off``. */
export function actionLabel(action: string | null | undefined): string {
  if (!action) return 'Event';
  const direct = ACTION_LABEL[action];
  if (direct) return direct;
  const dot = action.indexOf('.');
  if (dot < 0) return sentenceCase(words(action));
  const entity = action.slice(0, dot);
  const verb = action.slice(dot + 1);
  return `${entityLabel(entity)} ${VERB_LABEL[verb] ?? words(verb)}`;
}

// ─────────────────────────── assumption keys ───────────────────────────

const ASSUMPTION_LABEL: Record<string, string> = {
  hold_years: 'Hold period',
  acquisition_close_date: 'Acquisition close date',
  reopening_date: 'Reopening date',
  purchase_price: 'Purchase price',
  closing_costs_pct: 'Closing costs',
  renovation_budget: 'Renovation budget',
  renovation_budget_usd: 'Renovation budget',
  exit_cap_rate: 'Exit cap rate',
  entry_cap_rate: 'Entry cap rate',
  ltv: 'LTV',
  interest_rate: 'Interest rate',
  amortization_years: 'Amortization',
  term_years: 'Loan term',
  revpar_growth: 'RevPAR growth',
  adr_growth: 'ADR growth',
  occupancy_growth: 'Occupancy growth',
  expense_growth: 'Expense growth',
  other_expense_growth: 'Other expense growth',
  mgmt_fee_pct: 'Management fee',
  incentive_mgmt_fee_pct_of_gop: 'Incentive management fee',
  ffe_reserve_pct: 'FF&E reserve',
  stabilization_year: 'Stabilization year',
  exit_noi_basis: 'Exit NOI basis',
  terminal_noi_override: 'Terminal NOI override',
  noi_override_by_year: 'NOI schedule (pinned)',
  y1_occupancy_displacement_pct: 'Year-1 rooms offline',
  y1_adr_displacement_pct: 'Year-1 rate displacement',
  renovation_displacement_schedule: 'Renovation displacement schedule',
  target_irr: 'Target levered IRR',
  target_moic: 'Target equity multiple',
  target_stabilized_yoc: 'Target stabilized yield on cost',
  index_methodology: 'Index benchmark',
  index_mpi_target: 'MPI target',
  index_ari_target: 'ARI target',
  index_mpi_target_by_year: 'MPI target by year',
  index_ari_target_by_year: 'ARI target by year',
  worksheet_layout: 'Historical P&L layout',
  principal_usd: 'Principal',
  loan_amount: 'Loan amount',
  rate_type: 'Rate basis',
  spread_bps: 'Spread',
  all_in_rate: 'All-in rate',
  io_months: 'Interest-only period',
  maturity_years: 'Maturity',
  gp_split: 'GP split',
  lp_split: 'LP split',
  pref_rate: 'Preferred return',
  hurdle_rate: 'IRR hurdle',
  catch_up_pct: 'Catch-up',
  gp_equity_pct: 'GP ownership',
  lp_equity_pct: 'LP ownership',
  status: 'Status',
  deal_stage: 'Deal stage',
  name: 'Project name',
  city: 'City',
  market: 'Market',
  submarket: 'Submarket',
  keys: 'Keys',
  brand: 'Existing brand',
  proposed_brand: 'Proposed brand',
  positioning: 'Positioning',
  operating_model: 'Operating model',
  service: 'Property type',
  deal_type: 'Deal type',
  return_profile: 'Return profile',
};

const CONTAINER_LABEL: Record<string, string> = {
  debt_stack: 'Debt',
  tranches: 'tranche',
  partnership: 'Partnership',
  waterfall: 'tier',
  tiers: 'tier',
  field_overrides: '',
  periods: 'period',
  years: 'year',
};

/**
 * An override key path as the assumption registry labels it:
 *   ``hold_years`` → "Hold period",
 *   ``debt_stack.tranches.1.principal_usd`` → "Debt tranche 2 · Principal",
 *   ``partnership.waterfall.1.gp_split`` → "Partnership tier 2 · GP split".
 */
export function assumptionKeyLabel(key: string): string {
  const segs = key.split('.').filter(Boolean);
  if (segs.length === 0) return key;
  const leaf = segs[segs.length - 1];
  const leafLabel = ASSUMPTION_LABEL[leaf] ?? humanizeFieldName(leaf);
  if (segs.length === 1) return leafLabel;
  const prefix: string[] = [];
  for (let i = 0; i < segs.length - 1; i++) {
    const s = segs[i];
    if (/^\d+$/.test(s)) {
      // A list index follows its container ("tranche 2").
      const prev = prefix.pop();
      prefix.push(`${prev ?? 'item'} ${Number(s) + 1}`);
      continue;
    }
    const mapped = CONTAINER_LABEL[s];
    if (mapped === '') continue;
    prefix.push(mapped ?? words(s));
  }
  const head = sentenceCase(prefix.join(' ').trim());
  return head ? `${head} · ${leafLabel}` : leafLabel;
}

// ─────────────────────────── values ───────────────────────────

const PCT_KEY = /(_pct|_rate|_growth|ltv|ltc|_split|_share|_margin|_yoc|_irr|_moic)$/;
const USD_KEY = /(_usd|price|budget|amount|_costs?|noi|revenue)$/;
const YEARS_KEY = /(_years|hold_years|amortization|term|maturity|stabilization_year)$/;

/** A Python / JSON rendered value as a reader sees it: "not set", "5 years", "$36.4M", "30%". */
export function fmtAuditValue(key: string, raw: string): string {
  const v = raw.trim();
  if (v === '' || v === 'None' || v === 'null' || v === 'undefined') return 'not set';
  if (v === 'True' || v === 'true') return 'yes';
  if (v === 'False' || v === 'false') return 'no';
  const quoted = v.match(/^['"](.*)['"]$/);
  if (quoted) return quoted[1];
  const n = Number(v);
  if (!Number.isFinite(n)) return v;
  const leaf = key.split('.').pop() ?? key;
  if (YEARS_KEY.test(leaf) && Number.isInteger(n)) return `${n} year${n === 1 ? '' : 's'}`;
  if (PCT_KEY.test(leaf) && Math.abs(n) <= 1) {
    const pts = n * 100;
    return `${Number.isInteger(pts) ? pts : pts.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')}%`;
  }
  if (USD_KEY.test(leaf) && Math.abs(n) >= 1000) {
    return Math.abs(n) >= 1e6
      ? `$${(n / 1e6).toFixed(2).replace(/\.?0+$/, '')}M`
      : `$${Math.round(n).toLocaleString('en-US')}`;
  }
  if (leaf === 'keys' || leaf === 'io_months') return String(n);
  return Number.isInteger(n) ? String(n) : String(Math.round(n * 10000) / 10000);
}

const DIFF_PART = /^\s*([A-Za-z_][\w.[\]-]*)\s*:\s*(.*?)\s*(?:→|->)\s*(.*?)\s*$/;

/**
 * The worker's one-line diff summary as a reader sees it:
 *   ``hold_years: None → 5`` → "Hold period: not set → 5 years".
 * Parts separated by ";" or newlines are handled one at a time; text that is
 * not a diff is returned unchanged.
 */
export function humanizeDiffSummary(summary: string | null | undefined): string | null {
  if (!summary) return null;
  const parts = summary.split(/;\s*|\n/).map((p) => p.trim()).filter(Boolean);
  const out = parts.map((p) => {
    const m = p.match(DIFF_PART);
    if (!m) return p;
    const [, key, before, after] = m;
    return `${assumptionKeyLabel(key)}: ${fmtAuditValue(key, before)} → ${fmtAuditValue(key, after)}`;
  });
  return out.join('; ');
}

/** "Oct 7, 3:37 PM" in the viewer's zone — for run rows and timestamps. */
export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return 'time unknown';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'time unknown';
  return d.toLocaleString('en-US', {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}
