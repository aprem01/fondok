/**
 * FON-79 — the New Deal wizard's Documents step shows two kinds of rows once
 * the deal exists as a server-side draft: files still staged in the browser
 * (``WizardFile``) and documents already uploaded to the worker
 * (``WorkerDocument``). This module places a server document in the wizard
 * slot it belongs to and answers the Step-3 gate over both kinds.
 *
 * The slot is read from the analyst's own tag first (``user_provided_doc_type``
 * — what the wizard sent on upload), then from the worker's classification
 * (``doc_type``). A document with neither yet (dropped in "Drop everything
 * here", not classified) lists under the auto zone. CapEx lanes follow the
 * same subtype rule the Data Room uses (``effectiveDocSubtype``).
 *
 * No runtime import of ``@/lib/api`` so tests that mock it keep the real
 * mapping.
 */
import type { WizardCategory, WizardFile, WorkerDocument } from './api';
import { effectiveDocSubtype } from './docSubtype';

/** Document types the Step-3 gate accepts as "one financial statement". */
const FINANCIAL_DOC_TYPES: ReadonlySet<string> = new Set(['T12', 'PNL', 'PNL_MONTHLY', 'PNL_YTD']);

const SLOT_BY_DOC_TYPE: Record<string, WizardCategory> = {
  OM: 'om',
  T12: 'financials',
  PNL: 'financials',
  PNL_MONTHLY: 'financials',
  PNL_YTD: 'financials',
  PORTFOLIO_PNL: 'financials',
  STR: 'str',
  STR_TREND: 'str',
  STR_SEGMENTATION: 'str',
  PNL_BENCHMARK: 'comp_set',
  CBRE_HORIZONS: 'comp_set',
  MARKET_STUDY: 'comp_set',
  INSURANCE: 'insurance',
  PROPERTY_TAX: 'property_tax',
  ROOM_MIX: 'room_mix',
  PROPERTY_INFO: 'property_info',
  LEASES: 'leases',
  RENT_ROLL: 'leases',
  CONTRACT: 'leases',
  SURVEYS: 'surveys',
};

const CAPEX_SLOT_BY_SUBTYPE: Record<'pip' | 'historic' | 'future', WizardCategory> = {
  pip: 'pip_capex',
  historic: 'capex',
  future: 'future_capex',
};

/** The document type the wizard treats as the row's own: the analyst's tag,
 *  else the worker's classification, else null. */
export function wizardDocType(doc: Pick<WorkerDocument, 'user_provided_doc_type' | 'doc_type'>): string | null {
  const tag = (doc.user_provided_doc_type ?? '').toUpperCase().trim();
  if (tag) return tag;
  const cls = (doc.doc_type ?? '').toUpperCase().trim();
  return cls || null;
}

/** The wizard slot a server document lists under. */
export function wizardCategoryForDocument(
  doc: Pick<WorkerDocument, 'user_provided_doc_type' | 'doc_type' | 'doc_subtype'>,
): WizardCategory {
  const type = wizardDocType(doc);
  if (!type) return 'auto';
  if (type === 'CAPEX') {
    const lane = effectiveDocSubtype('CAPEX', doc.doc_subtype) ?? 'pip';
    return CAPEX_SLOT_BY_SUBTYPE[lane];
  }
  return SLOT_BY_DOC_TYPE[type] ?? 'auto';
}

/** True when the server document counts toward the Step-3 financials gate. */
export function isFinancialDocument(
  doc: Pick<WorkerDocument, 'user_provided_doc_type' | 'doc_type'>,
): boolean {
  const type = wizardDocType(doc);
  return type != null && FINANCIAL_DOC_TYPES.has(type);
}

/** The Step-3 gate over both kinds of row: at least one financial statement,
 *  staged in the Financial Statements slot or already uploaded as one. Files
 *  in the auto zone count only once the worker has classified them. */
export function hasFinancialStatement(
  staged: readonly WizardFile[],
  serverDocs: readonly WorkerDocument[],
): boolean {
  return staged.some((f) => f.category === 'financials') || serverDocs.some(isFinancialDocument);
}
