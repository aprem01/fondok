/**
 * openDocument — open a deal document in a new browser tab (FON-41 / R-040).
 *
 * Why this exists: a new tab is a top-level navigation and never carries the
 * Clerk session JWT, so pointing it at the worker's authenticated
 * ``/download`` route 401s (the worker refuses header-only tenant requests
 * since 2026-10-06) and the tester saw a raw "document not found on deal".
 *
 * The flow:
 *   1. Open a blank tab synchronously, inside the click's user-activation
 *      window — a tab opened after an ``await`` is what pop-up blockers block.
 *   2. Ask the worker (authenticated) for a short-lived link
 *      (``api.documents.downloadUrlSigned``): a presigned S3 GET in prod, an
 *      HMAC-signed worker path on the local store.
 *   3. Send the tab there. For PDFs opened from a citation, append
 *      ``#page=N`` so the viewer lands on the cited page.
 *   4. On failure close the blank tab and toast a short, human reason —
 *      never the raw backend body.
 *
 * E-029 / R-044: ``page`` is the 1-based PDF page INDEX the extractor
 * reports (what ``#page=N`` means to a PDF viewer), not the number printed on
 * the page. Labels say "PDF p.N" for that reason.
 */

import { api, workerUrl, type DocumentDownloadUrl } from '@/lib/api';

export type ToastFn = (
  message: string,
  opts?: { type?: 'success' | 'error' | 'info' },
) => unknown;

/** The slice of ``window`` the opener needs — injectable for tests. */
export interface TabOpener {
  open: (url?: string, target?: string, features?: string) => OpenedTab | null;
}

/** The slice of the opened ``Window`` we touch. */
export interface OpenedTab {
  location: { href: string };
  close: () => void;
  opener: unknown;
}

export interface OpenDocumentOptions {
  dealId: string;
  docId: string;
  /** Shown in the failure toast; falls back to the worker's filename. */
  filename?: string | null;
  /** 1-based PDF page index (extractor ``source_page``). Only applied to PDFs. */
  page?: number | null;
  toast: ToastFn;
  /** Defaults to ``window``. */
  win?: TabOpener;
}

/**
 * Resolve the worker's link into the URL the tab should navigate to.
 *
 * ``signed_path`` links are root-relative (the worker behind its proxy
 * doesn't reliably know its public host), so they resolve against the worker
 * base this client already talks to. Presigned S3 links are absolute and pass
 * through. A ``#page=N`` fragment is appended for PDFs only — the fragment is
 * never sent to the server, so it cannot disturb either signature.
 */
export function resolveDocumentUrl(
  link: DocumentDownloadUrl,
  opts: { filename?: string | null; page?: number | null } = {},
): string {
  const base = workerUrl() || undefined;
  const absolute = new URL(link.url, base).toString();
  const name = (opts.filename ?? link.filename ?? '').trim();
  const isPdf =
    (link.content_type ?? '').toLowerCase() === 'application/pdf' ||
    /\.pdf$/i.test(name);
  const page = opts.page ?? null;
  if (isPdf && page != null && Number.isFinite(page) && page > 0) {
    return `${absolute}#page=${Math.floor(page)}`;
  }
  return absolute;
}

/**
 * A short, user-facing reason for a failed worker call. Deliberately maps on
 * status/name only — the backend body (``{"detail": "document … not found on
 * deal …"}``) is never surfaced.
 */
export function shortReason(err: unknown): string {
  const e = (err ?? {}) as { name?: string; status?: unknown };
  if (e.name === 'TimeoutError') return 'the worker took too long to respond';
  if (e.name === 'AbortError') return 'the request was cancelled';
  if (e.name === 'WorkerError' || typeof e.status === 'number') {
    const status = typeof e.status === 'number' ? e.status : -1;
    switch (status) {
      case 0:
        return 'worker not connected';
      case 401:
        return 'your session has expired — sign in again';
      case 403:
        return 'you do not have access to this document';
      case 404:
        return 'the document is no longer on this deal';
      case 409:
        return 'no active organization is selected';
      case 410:
        return 'the stored file is gone — re-upload it';
      case 503:
        return 'document links are not configured on the worker';
      default:
        return status >= 500
          ? `the worker returned an error (${status})`
          : `the request failed (${status})`;
    }
  }
  return 'unexpected error';
}

/**
 * Open ``docId`` in a new tab. Resolves ``true`` when the tab was sent to the
 * document, ``false`` when it could not be (a toast has already been shown).
 */
export async function openDocumentInNewTab(
  opts: OpenDocumentOptions,
): Promise<boolean> {
  const { dealId, docId, page, toast } = opts;
  const win: TabOpener | null =
    opts.win ?? (typeof window !== 'undefined' ? (window as unknown as TabOpener) : null);
  let name = opts.filename?.trim() || 'document';

  // 1. Open the tab NOW, while we still hold the click's user activation.
  //    ``noopener`` cannot be passed here — it makes ``window.open`` return
  //    ``null`` by spec, and we need the handle to navigate it — so the
  //    opener link is severed by hand before the tab goes anywhere.
  const tab = win ? win.open('', '_blank') : null;
  if (tab) tab.opener = null;

  // 2. Mint the link (authenticated).
  let url: string;
  try {
    const link = await api.documents.downloadUrlSigned(dealId, docId);
    if (!opts.filename?.trim() && link.filename) name = link.filename;
    url = resolveDocumentUrl(link, { filename: name, page });
  } catch (err) {
    tab?.close();
    toast(`Couldn't open ${name}: ${shortReason(err)}`, { type: 'error' });
    return false;
  }

  // 3. Send the pre-opened tab there.
  if (tab) {
    tab.location.href = url;
    return true;
  }

  // The synchronous open was blocked (or there is no window). One late try;
  // if the blocker refuses again, say so instead of failing silently.
  const late = win ? win.open(url, '_blank') : null;
  if (late) {
    late.opener = null;
    return true;
  }
  toast(`Couldn't open ${name}: the browser blocked the pop-up`, { type: 'error' });
  return false;
}
