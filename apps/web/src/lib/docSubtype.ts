/**
 * FON-41 / R-036 (+ the PIP lane) — document subcategories
 * (``documents.doc_subtype``).
 *
 * A subtype refines a doc_type without adding a DocType value: the worker's
 * Router, extractor and engines keep reading plain ``CAPEX``, while the
 * wizard and Data Room lane CAPEX files three ways:
 *
 *   ``pip``      PIP / Renovation Budget — the day-one PIP, owner budget or
 *                approved capital plan. The ONLY tagged lane that FEEDS the
 *                model (``capex_plan.pip.total_usd`` → renovation budget).
 *   ``historic`` Historic CapEx — spend already incurred. Reference only.
 *   ``future``   Future CapEx — forward hold-period capital plan. Reference
 *                only.
 *
 * Unstated CAPEX (legacy uploads, the Data Room bulk drop, a worker without
 * the column) is classifier-assigned: the worker's loader treats it exactly
 * like ``pip`` (it MAY ground the model), so the Data Room files it under
 * PIP / Renovation Budget — the lane that says "feeds the model" — rather
 * than under a reference-only lane it does not behave like. Re-tag it to
 * Historic or Future to take it out of the model.
 *
 * Mirrors ``DOC_SUBTYPES_BY_DOC_TYPE`` in apps/worker/app/api/documents.py.
 *
 * No runtime imports, so components and tests can use it even
 * where ``@/lib/api`` is mocked.
 */
import type { WizardCategory, WizardFile } from './api';

/** The subcategories a doc_type accepts. Not DocType
 *  values: the worker's Router / extractor / engines keep reading ``CAPEX``. */
export type DocSubtype = 'pip' | 'historic' | 'future';

export const DOC_SUBTYPES_BY_DOC_TYPE: Record<string, readonly DocSubtype[]> = {
  CAPEX: ['pip', 'historic', 'future'],
};

/** Short option label for a lane select. */
export const DOC_SUBTYPE_LABEL: Record<DocSubtype, string> = {
  pip: 'PIP / Renovation budget',
  historic: 'Historic',
  future: 'Future',
};

/** What each lane does to the model — the option tooltip / help text. */
export const DOC_SUBTYPE_HELP: Record<DocSubtype, string> = {
  pip: 'Day-one PIP, owner budget or approved capital plan — feeds the model’s renovation budget.',
  historic: 'Spend already incurred — reference only, never read into the model.',
  future: 'Forward hold-period capital plan — reference only, never read into the model.',
};

/** The subtype a doc_type falls back to when none was stated. An untagged
 *  CAPEX file is classifier-assigned and may ground the model, so it files
 *  under the PIP / Renovation Budget lane (see the module comment). */
const DEFAULT_DOC_SUBTYPE: Record<string, DocSubtype> = {
  CAPEX: 'pip',
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

/** Wizard slots that pin a subtype on upload: the PIP / Renovation Budget
 *  slot sends ``pip``, the Future CapEx slot ``future``, the Historic CapEx
 *  slot ``historic``. Every other slot sends nothing. */
export const WIZARD_CATEGORY_DOC_SUBTYPE: Partial<Record<WizardCategory, DocSubtype>> = {
  pip_capex: 'pip',
  future_capex: 'future',
  capex: 'historic',
};

/** The ``user_doc_subtypes[i]`` value for one wizard file — the file's own
 *  lane pick when the analyst changed it on the row, else the slot's pin;
 *  '' when neither applies or the file was re-tagged off a doc_type that
 *  takes one. */
export function wizardDocSubtype(w: WizardFile): DocSubtype | '' {
  const subtype = w.doc_subtype ?? WIZARD_CATEGORY_DOC_SUBTYPE[w.category];
  if (!subtype) return '';
  const allowed = DOC_SUBTYPES_BY_DOC_TYPE[(w.user_doc_type ?? '').toUpperCase()];
  return allowed && allowed.includes(subtype) ? subtype : '';
}
