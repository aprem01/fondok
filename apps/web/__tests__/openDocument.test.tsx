/**
 * "Open document in new tab" — FON-41 / R-040 (+ E-029 / R-044 citations).
 *
 * A new tab is a top-level navigation and never carries the Clerk JWT, so the
 * old ``window.open(downloadUrl)`` 401'd against the worker's authenticated
 * ``/download`` route and the tester saw a raw "document not found on deal".
 *
 * Contracts locked here:
 *  1. The tab is opened SYNCHRONOUSLY (before any await — pop-up blockers),
 *     the signed-link endpoint is called, and the pre-opened tab is navigated.
 *  2. A rejected mint closes the blank tab and toasts "Couldn't open <file>:
 *     <short reason>" — never the raw backend body.
 *  3. A PDF opened from a cited field gets ``#page=<source_page>``; non-PDFs
 *     never get the fragment; ``signed_path`` links resolve against the
 *     worker base.
 *  4. Citations are labelled "PDF p.N" (the extractor's PDF page index), and
 *     the SourceDocPane's "See PDF p.N" control uses the same flow.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, act, waitFor } from '@testing-library/react';
import React from 'react';

const downloadUrlSigned = vi.fn();
vi.mock('@/lib/api', () => ({
  workerUrl: () => 'http://worker.test',
  isWorkerConnected: () => true,
  api: {
    documents: {
      downloadUrlSigned: (...args: unknown[]) => downloadUrlSigned(...args),
    },
  },
}));

const toastSpy = vi.fn();
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: toastSpy }) }));
vi.mock('next/navigation', () => ({ useParams: () => ({ id: 'deal-uuid-1' }) }));
vi.mock('@/lib/hooks/useDocuments', () => ({
  useDocuments: () => ({
    documents: [{ id: 'doc-1', filename: 'Miami Beach OM.pdf' }],
    extractions: {},
  }),
}));

import {
  openDocumentInNewTab,
  resolveDocumentUrl,
  shortReason,
  type OpenedTab,
} from '@/lib/openDocument';
import type { DocumentDownloadUrl } from '@/lib/api';
import { Citation } from '@/components/citations/Citation';
import SourceDocPane from '@/components/citations/SourceDocPane';

// ── fixtures ─────────────────────────────────────────────────────────────

const PRESIGNED: DocumentDownloadUrl = {
  url: 'https://fondok-raw-prod.s3.amazonaws.com/fondok/raw/t/d/abc-OM.pdf?X-Amz-Signature=deadbeef',
  expires_in: 300,
  kind: 's3_presigned',
  filename: 'Miami Beach OM.pdf',
  content_type: 'application/pdf',
};

const SIGNED_PATH: DocumentDownloadUrl = {
  url: '/deals/deal-uuid-1/documents/doc-1/download/signed?token=abc123&exp=1760000000',
  expires_in: 300,
  kind: 'signed_path',
  filename: 'Miami Beach OM.pdf',
  content_type: 'application/pdf',
};

const XLSX: DocumentDownloadUrl = {
  ...PRESIGNED,
  filename: 'November 2024 Financials.xlsx',
  content_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
};

// Mirrors ``WorkerError`` from lib/api (name + status + raw body).
class FakeWorkerError extends Error {
  status: number;
  body: string;
  constructor(message: string, status: number, body: string) {
    super(message);
    this.name = 'WorkerError';
    this.status = status;
    this.body = body;
  }
}

const RAW_BODY = '{"detail":"document doc-1 not found on deal deal-uuid-1"}';

function fakeTab(): OpenedTab & { close: ReturnType<typeof vi.fn> } {
  return { location: { href: '' }, close: vi.fn(), opener: {} };
}

function fakeWin(tab: OpenedTab | null) {
  return { open: vi.fn().mockReturnValue(tab) };
}

beforeEach(() => {
  downloadUrlSigned.mockReset();
  toastSpy.mockReset();
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── the handler ──────────────────────────────────────────────────────────

describe('openDocumentInNewTab', () => {
  it('opens a blank tab synchronously, mints the signed link, then navigates the tab', async () => {
    downloadUrlSigned.mockResolvedValue(PRESIGNED);
    const tab = fakeTab();
    const win = fakeWin(tab);

    const ok = await openDocumentInNewTab({
      dealId: 'deal-uuid-1',
      docId: 'doc-1',
      filename: 'Miami Beach OM.pdf',
      toast: toastSpy,
      win,
    });

    expect(ok).toBe(true);
    // The tab is opened BEFORE the network call (user-activation window).
    expect(win.open).toHaveBeenCalledWith('', '_blank');
    expect(win.open.mock.invocationCallOrder[0]).toBeLessThan(
      downloadUrlSigned.mock.invocationCallOrder[0],
    );
    expect(downloadUrlSigned).toHaveBeenCalledWith('deal-uuid-1', 'doc-1');
    // Opener severed by hand (``noopener`` would make window.open return null).
    expect(tab.opener).toBeNull();
    expect(tab.location.href).toBe(PRESIGNED.url);
    expect(tab.close).not.toHaveBeenCalled();
    expect(toastSpy).not.toHaveBeenCalled();
  });

  it('closes the blank tab and toasts a short reason on failure — never the raw body', async () => {
    downloadUrlSigned.mockRejectedValue(
      new FakeWorkerError('GET /deals/deal-uuid-1/documents/doc-1/download-url → 404', 404, RAW_BODY),
    );
    const tab = fakeTab();
    const win = fakeWin(tab);

    const ok = await openDocumentInNewTab({
      dealId: 'deal-uuid-1',
      docId: 'doc-1',
      filename: 'Miami Beach OM.pdf',
      toast: toastSpy,
      win,
    });

    expect(ok).toBe(false);
    expect(tab.close).toHaveBeenCalledTimes(1);
    expect(tab.location.href).toBe('');
    expect(toastSpy).toHaveBeenCalledTimes(1);
    const [message, opts] = toastSpy.mock.calls[0];
    expect(message).toBe("Couldn't open Miami Beach OM.pdf: the document is no longer on this deal");
    expect(opts).toEqual({ type: 'error' });
    expect(message).not.toContain('not found on deal');
    expect(message).not.toContain('detail');
  });

  it('appends #page=N for a PDF opened from a cited field, never for a non-PDF', async () => {
    downloadUrlSigned.mockResolvedValueOnce(PRESIGNED);
    const pdfTab = fakeTab();
    await openDocumentInNewTab({
      dealId: 'deal-uuid-1',
      docId: 'doc-1',
      filename: 'Miami Beach OM.pdf',
      page: 3,
      toast: toastSpy,
      win: fakeWin(pdfTab),
    });
    expect(pdfTab.location.href).toBe(`${PRESIGNED.url}#page=3`);

    downloadUrlSigned.mockResolvedValueOnce(XLSX);
    const xlsxTab = fakeTab();
    await openDocumentInNewTab({
      dealId: 'deal-uuid-1',
      docId: 'doc-2',
      filename: 'November 2024 Financials.xlsx',
      page: 3,
      toast: toastSpy,
      win: fakeWin(xlsxTab),
    });
    expect(xlsxTab.location.href).toBe(XLSX.url);
    expect(xlsxTab.location.href).not.toContain('#page');
  });

  it('falls back to the worker filename for the toast when the caller has none', async () => {
    downloadUrlSigned.mockResolvedValue(PRESIGNED);
    const tab = fakeTab();
    // Mint succeeds but a later step can still fail — use a blocked pop-up to
    // force a toast after the filename has been learned from the worker.
    const win = { open: vi.fn().mockReturnValue(null) };
    const ok = await openDocumentInNewTab({
      dealId: 'deal-uuid-1',
      docId: 'doc-1',
      toast: toastSpy,
      win,
    });
    expect(ok).toBe(false);
    expect(tab.location.href).toBe('');
    expect(toastSpy).toHaveBeenCalledWith(
      "Couldn't open Miami Beach OM.pdf: the browser blocked the pop-up",
      { type: 'error' },
    );
    // Second attempt (after the await) is made with the real URL.
    expect(win.open).toHaveBeenLastCalledWith(PRESIGNED.url, '_blank');
  });
});

// ── URL + reason helpers ─────────────────────────────────────────────────

describe('resolveDocumentUrl', () => {
  it('resolves a signed_path link against the worker base and keeps the query intact', () => {
    expect(resolveDocumentUrl(SIGNED_PATH)).toBe(
      'http://worker.test/deals/deal-uuid-1/documents/doc-1/download/signed?token=abc123&exp=1760000000',
    );
    expect(resolveDocumentUrl(SIGNED_PATH, { page: 2 })).toBe(
      'http://worker.test/deals/deal-uuid-1/documents/doc-1/download/signed?token=abc123&exp=1760000000#page=2',
    );
  });

  it('passes a presigned URL through unchanged', () => {
    expect(resolveDocumentUrl(PRESIGNED)).toBe(PRESIGNED.url);
  });

  it('decides PDF-ness from the content type when the filename is unknown', () => {
    expect(resolveDocumentUrl({ ...PRESIGNED, filename: '' }, { page: 4 })).toBe(
      `${PRESIGNED.url}#page=4`,
    );
    expect(resolveDocumentUrl(XLSX, { page: 4 })).toBe(XLSX.url);
    expect(resolveDocumentUrl(PRESIGNED, { page: 0 })).toBe(PRESIGNED.url);
  });
});

describe('shortReason', () => {
  it('maps worker statuses to plain language and never echoes the body', () => {
    expect(shortReason(new FakeWorkerError('x', 401, RAW_BODY))).toBe(
      'your session has expired — sign in again',
    );
    expect(shortReason(new FakeWorkerError('x', 404, RAW_BODY))).toBe(
      'the document is no longer on this deal',
    );
    expect(shortReason(new FakeWorkerError('x', 0, 'worker not connected'))).toBe(
      'worker not connected',
    );
    expect(shortReason(new FakeWorkerError('x', 500, 'Internal Server Error'))).toBe(
      'the worker returned an error (500)',
    );
    const timeout = new Error('slow');
    timeout.name = 'TimeoutError';
    expect(shortReason(timeout)).toBe('the worker took too long to respond');
    expect(shortReason(new Error('boom'))).toBe('unexpected error');
    for (const err of [new FakeWorkerError('x', 404, RAW_BODY), new FakeWorkerError('x', 500, RAW_BODY)]) {
      expect(shortReason(err)).not.toContain('doc-1');
    }
  });
});

// ── E-029 / R-044: citations ─────────────────────────────────────────────

describe('Citation labels (R-044)', () => {
  it('says "PDF p.N" so the reviewer knows it is the PDF page index', () => {
    render(<Citation data={{ documentId: 'doc-1', page: 3 }} />);
    expect(screen.getByTitle('Source PDF p.3')).toBeInTheDocument();
    cleanup();
    render(
      <Citation data={{ documentId: 'doc-1', page: 3, excerpt: 'NOI $1.2M' }}>
        NOI
      </Citation>,
    );
    expect(screen.getByTitle('NOI $1.2M — PDF p.3')).toBeInTheDocument();
  });
});

describe('SourceDocPane "See PDF p.N" (E-029)', () => {
  it('opens the cited document in a new tab at the cited PDF page via the signed link', async () => {
    downloadUrlSigned.mockResolvedValue(PRESIGNED);
    const tab = fakeTab();
    const openSpy = vi
      .spyOn(window, 'open')
      .mockReturnValue(tab as unknown as Window);

    render(<SourceDocPane />);
    act(() => {
      window.dispatchEvent(
        new CustomEvent('fondok:citation-focus', {
          detail: { documentId: 'doc-1', page: 3, field: 'property_overview.year_built' },
        }),
      );
    });

    // Header labels the PDF page index ("PDF p.3"), not "page 3".
    expect(screen.getByText('PDF p.3 · property_overview.year_built')).toBeInTheDocument();
    expect(screen.queryByText(/^page 3/)).not.toBeInTheDocument();
    const open = screen.getByRole('button', { name: /See PDF p\.3/ });
    fireEvent.click(open);

    expect(openSpy).toHaveBeenCalledWith('', '_blank');
    await waitFor(() => expect(tab.location.href).toBe(`${PRESIGNED.url}#page=3`));
    expect(downloadUrlSigned).toHaveBeenCalledWith('deal-uuid-1', 'doc-1');
    expect(toastSpy).not.toHaveBeenCalled();
  });
});
