/**
 * FON-41 / R-036 — document subcategories (``documents.doc_subtype``).
 *
 * A subtype refines a doc_type without adding a DocType value: the worker's
 * Router, extractor and engines keep reading plain ``CAPEX``, while the
 * wizard and Data Room tell Historic CapEx (``historic``) from Future CapEx
 * (``future`` — PIP budgets / forward capital plans) apart. Unstated CAPEX
 * (legacy uploads, the Data Room bulk drop, a worker without the column) is
 * Historic.
 *
 * No runtime imports, so components and tests can use it even
 * where ``@/lib/api`` is mocked.
 */
import type { WizardCategory, WizardFile } from './api';

/** The subcategories a doc_type accepts. Not DocType
 *  values: the worker's Router / extractor / engines keep reading ``CAPEX``. */
export type DocSubtype = 'future' | 'historic';

export const DOC_SUBTYPES_BY_DOC_TYPE: Record<string, readonly DocSubtype[]> = {
  CAPEX: ['historic', 'future'],
};

/** The subtype a doc_type falls back to when none was stated: an untagged
 *  CAPEX file is Historic CapEx (pre-FON-41 uploads, the Data Room bulk
 *  drop, a worker without the column). */
const DEFAULT_DOC_SUBTYPE: Record<string, DocSubtype> = {
  CAPEX: 'historic',
};

/** The subtype a document is grouped under — its stored ``doc_subtype``
 *  when valid for the doc_type, else the doc_type's default (``null`` for
 *  doc_types that take none). */
export function effectiveDocSubtype(
  docType: string | null | undefined,
  docSubtype: string | null | undefined,
): DocSubtype | null {
  const t = (docType ?? '').toUpperCase().trim();
  const allowed = DOC_SUBTYPES_BY_DOC_TYPE[t];
  if (!allowed) return null;
  const s = (docSubtype ?? '').toLowerCase().trim() as DocSubtype;
  return allowed.includes(s) ? s : DEFAULT_DOC_SUBTYPE[t] ?? null;
}

/** Wizard slots that pin a subtype on upload: the Future CapEx slot sends
 *  ``future``, the Historic CapEx slot ``historic``. Every other slot sends
 *  nothing. */
export const WIZARD_CATEGORY_DOC_SUBTYPE: Partial<Record<WizardCategory, DocSubtype>> = {
  future_capex: 'future',
  capex: 'historic',
};

/** The ``user_doc_subtypes[i]`` value for one wizard file — '' when the
 *  slot pins none or the file was re-tagged off a doc_type that takes one. */
export function wizardDocSubtype(w: WizardFile): DocSubtype | '' {
  const subtype = w.doc_subtype ?? WIZARD_CATEGORY_DOC_SUBTYPE[w.category];
  if (!subtype) return '';
  const allowed = DOC_SUBTYPES_BY_DOC_TYPE[(w.user_doc_type ?? '').toUpperCase()];
  return allowed && allowed.includes(subtype) ? subtype : '';
}
