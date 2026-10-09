/**
 * R-030 — the one list of file formats Fondok accepts for document upload,
 * shared by the wizard Documents step, the Data Room, and their toasts.
 *
 * Mirrors the worker's ``_ALLOWED_EXTENSIONS`` (apps/worker/app/api/documents.py)
 * and ``CONVERT_FIRST_HINTS`` (apps/worker/app/extraction/parser.py). Every
 * accepted extension has a registered worker parser; the "convert first"
 * formats are ones a broker plausibly sends that Fondok does not read, each
 * with the one-step conversion the user should do.
 *
 * Google Sheets / Slides / Docs are not files: a native Google file can only
 * reach Fondok as an export (File → Download → Microsoft Excel / PowerPoint /
 * Word / PDF). There is no Google Drive connection — by design.
 */

export const UPLOAD_EXTENSIONS = [
  '.pdf',
  '.xlsx',
  '.xlsm',
  '.xls',
  '.csv',
  '.docx',
  '.pptx',
] as const;

const UPLOAD_EXTENSION_SET: ReadonlySet<string> = new Set(UPLOAD_EXTENSIONS);

/** `<input accept=…>` value. */
export const UPLOAD_ACCEPT = `${UPLOAD_EXTENSIONS.join(',')},application/pdf`;

/** Human list of accepted formats, for hints and toasts. */
export const ACCEPTED_FORMATS_LABEL =
  'PDF, Excel (.xlsx / .xlsm / .xls), CSV, Word (.docx) or PowerPoint (.pptx)';

/** The import path for native Google files (no Drive OAuth — export them). */
export const GOOGLE_EXPORT_GUIDANCE =
  'Google Sheets / Slides: File → Download → Microsoft Excel / PowerPoint, then drop here.';

const GOOGLE_EXPORT_HINT =
  'Google Sheets / Slides / Docs are not files Fondok can read directly — in Google, use File → Download → Microsoft Excel (.xlsx), Microsoft PowerPoint (.pptx), Microsoft Word (.docx) or PDF, then upload that file.';

/** Known formats Fondok does not parse → the conversion to do first. */
export const CONVERT_FIRST_HINTS: Readonly<Record<string, string>> = {
  '.ppt': 'Legacy PowerPoint (.ppt) is not supported — re-save the deck as .pptx (File → Save As → PowerPoint Presentation) or export it to PDF, then re-upload.',
  '.pps': 'PowerPoint shows (.pps) are not supported — re-save the deck as .pptx or export it to PDF, then re-upload.',
  '.ppsx': 'PowerPoint shows (.ppsx) are not supported — re-save the deck as .pptx or export it to PDF, then re-upload.',
  '.key': 'Keynote (.key) is not supported — in Keynote use File → Export To → PowerPoint (.pptx) or PDF, then re-upload.',
  '.numbers': 'Apple Numbers (.numbers) is not supported — in Numbers use File → Export To → Excel (.xlsx) or CSV, then re-upload.',
  '.pages': 'Apple Pages (.pages) is not supported — in Pages use File → Export To → Word (.docx) or PDF, then re-upload.',
  '.doc': 'Legacy Word (.doc) is not supported — re-save the document as .docx (File → Save As → Word Document) or export it to PDF, then re-upload.',
  '.odp': 'OpenDocument presentations (.odp) are not supported — re-save as .pptx or export to PDF, then re-upload.',
  '.ods': 'OpenDocument spreadsheets (.ods) are not supported — re-save as .xlsx (or CSV), then re-upload.',
  '.odt': 'OpenDocument text (.odt) is not supported — re-save as .docx or export to PDF, then re-upload.',
  '.gsheet': GOOGLE_EXPORT_HINT,
  '.gslides': GOOGLE_EXPORT_HINT,
  '.gdoc': GOOGLE_EXPORT_HINT,
};

/** Lower-cased extension including the dot ('' when there is none). */
export function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.');
  return dot >= 0 ? name.slice(dot).toLowerCase() : '';
}

export function isUploadableFile(name: string): boolean {
  return UPLOAD_EXTENSION_SET.has(fileExtension(name));
}

/** Toast copy for a file the client filtered out before upload. Always says
 *  "unsupported file type" (the e2e suite keys on it), then either the
 *  specific conversion or the accepted list. */
export function unsupportedFileMessage(name: string): string {
  const hint = CONVERT_FIRST_HINTS[fileExtension(name)];
  return hint
    ? `${name}: unsupported file type — ${hint}`
    : `${name}: unsupported file type — Fondok accepts ${ACCEPTED_FORMATS_LABEL}.`;
}
