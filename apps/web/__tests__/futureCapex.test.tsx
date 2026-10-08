/**
 * Future CapEx as a real document category — Linear FON-41, finding R-036.
 *
 * Historic and Future CapEx both upload as doc_type ``CAPEX``; the worker's
 * ``documents.doc_subtype`` (``historic`` | ``future`` | null) tells them
 * apart. Pinned here:
 *
 *  1. The wizard upload sends ``user_doc_subtypes[]`` index-aligned with
 *     ``files[]``: the Future CapEx slot sends ``future``, Historic CapEx
 *     ``historic``, every other slot ''.
 *  2. The Data Room groups CAPEX files by ``doc_subtype`` — ``future`` under
 *     Future CapEx, ``historic`` AND null (legacy / bulk drop) under Historic.
 *  3. Future CapEx is a real coverage row (counts toward "of N types").
 *  4. A CapEx file's Historic / Future select and a drop onto either row
 *     reclassify through ``doc_subtype``.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import React from 'react';

process.env.NEXT_PUBLIC_WORKER_URL = 'http://test-worker.local';

vi.mock('@/lib/auth', () => ({
  getCurrentOrgId: () => null,
  getClerkSessionToken: async () => null,
  waitForClerkTokenFn: async () => {},
}));

import { api, type WizardFile } from '@/lib/api';
import { effectiveDocSubtype, wizardDocSubtype } from '@/lib/docSubtype';
import {
  DocumentCoverage,
  categoryForFile,
  type CoverageFile,
} from '@/components/project/DocumentCoverage';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const capex = (id: string, name: string, docSubtype?: string | null): CoverageFile => ({
  id,
  name,
  docType: 'CAPEX',
  docSubtype,
  fields: 4,
  confidence: 92,
  toReview: 0,
  fiscalYear: null,
  status: 'EXTRACTED',
});

describe('wizard upload sends the CapEx subtype', () => {
  it('appends user_doc_subtypes[] index-aligned with files[]', async () => {
    const fetchSpy = vi
      .spyOn(global, 'fetch')
      .mockResolvedValue(
        new Response('[]', { status: 201, headers: { 'Content-Type': 'application/json' } }),
      );
    const f = (name: string) => new File(['x'], name, { type: 'application/pdf' });
    const staged: WizardFile[] = [
      { file: f('pip.pdf'), category: 'future_capex', user_doc_type: 'CAPEX' },
      { file: f('t12.pdf'), category: 'financials', user_doc_type: 'T12', fiscal_year: 2024 },
      { file: f('capex.pdf'), category: 'capex', user_doc_type: 'CAPEX' },
      { file: f('om.pdf'), category: 'om', user_doc_type: 'OM' },
    ];

    await api.documents.upload('deal-1', staged);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain('/deals/deal-1/documents/upload');
    const fd = init!.body as FormData;
    expect(fd.getAll('files').map((v) => (v as File).name)).toEqual([
      'pip.pdf',
      't12.pdf',
      'capex.pdf',
      'om.pdf',
    ]);
    expect(fd.getAll('user_doc_types')).toEqual(['CAPEX', 'T12', 'CAPEX', 'OM']);
    expect(fd.getAll('fiscal_years')).toEqual(['', '2024', '', '']);
    expect(fd.getAll('user_doc_subtypes')).toEqual(['future', '', 'historic', '']);
  });

  it('drops the slot subtype when the file is re-tagged off CAPEX', () => {
    const file = new File(['x'], 'a.pdf');
    expect(wizardDocSubtype({ file, category: 'future_capex', user_doc_type: 'CAPEX' })).toBe('future');
    expect(wizardDocSubtype({ file, category: 'capex', user_doc_type: 'CAPEX' })).toBe('historic');
    expect(wizardDocSubtype({ file, category: 'future_capex', user_doc_type: 'OM' })).toBe('');
    expect(wizardDocSubtype({ file, category: 'insurance', user_doc_type: 'INSURANCE' })).toBe('');
  });

  it('legacy File[] uploads send no subtype field', async () => {
    const fetchSpy = vi
      .spyOn(global, 'fetch')
      .mockResolvedValue(new Response('[]', { status: 201 }));
    await api.documents.upload('deal-1', [new File(['x'], 'drop.pdf')]);
    const fd = fetchSpy.mock.calls[0][1]!.body as FormData;
    expect(fd.getAll('user_doc_subtypes')).toEqual([]);
  });
});

describe('effective subtype', () => {
  it('null / unknown CAPEX → historic; other doc types → null', () => {
    expect(effectiveDocSubtype('CAPEX', 'future')).toBe('future');
    expect(effectiveDocSubtype('capex', 'Future')).toBe('future');
    expect(effectiveDocSubtype('CAPEX', 'historic')).toBe('historic');
    expect(effectiveDocSubtype('CAPEX', null)).toBe('historic');
    expect(effectiveDocSubtype('CAPEX', 'someday')).toBe('historic');
    expect(effectiveDocSubtype('OM', 'future')).toBeNull();
  });
});

describe('Data Room groups CAPEX by doc_subtype', () => {
  const files = [
    capex('h1', 'CapEx 2022-2024.xlsx', 'historic'),
    capex('n1', 'Legacy CapEx.pdf', null),
    capex('u1', 'Untagged CapEx.pdf'),
    capex('f1', 'PIP Budget.xlsx', 'future'),
  ];

  it('lists future under Future CapEx and historic + null under Historic CapEx', () => {
    expect(categoryForFile(files[0])?.id).toBe('capex');
    expect(categoryForFile(files[1])?.id).toBe('capex');
    expect(categoryForFile(files[2])?.id).toBe('capex');
    expect(categoryForFile(files[3])?.id).toBe('future_capex');

    const { container } = render(
      <DocumentCoverage files={files} onReclassify={vi.fn()} onOpenDoc={vi.fn()} />,
    );
    const historic = container.querySelector('li[data-category="capex"]')!.textContent!;
    const future = container.querySelector('li[data-category="future_capex"]')!.textContent!;
    expect(historic).toContain('CapEx 2022-2024.xlsx');
    expect(historic).toContain('Legacy CapEx.pdf');
    expect(historic).toContain('Untagged CapEx.pdf');
    expect(historic).not.toContain('PIP Budget.xlsx');
    expect(future).toContain('PIP Budget.xlsx');
    expect(future).toMatch(/^Future CapEx1 file(?!s)/);
    expect(container.textContent).not.toMatch(/Filed as CapEx/);
  });

  it('a null-subtype CAPEX file leaves Future CapEx empty and counts only Historic', () => {
    const { container } = render(
      <DocumentCoverage
        files={[capex('n1', 'Legacy CapEx.pdf', null)]}
        onReclassify={vi.fn()}
        onOpenDoc={vi.fn()}
      />,
    );
    expect(container.querySelector('li[data-category="future_capex"]')!.textContent).toMatch(
      /Not uploaded/,
    );
    expect(
      screen.getByText((_c, el) => el?.tagName === 'P' && /1 of 12 types/.test(el.textContent || '')),
    ).toBeInTheDocument();
  });

  it('Future CapEx counts toward coverage', () => {
    render(
      <DocumentCoverage
        files={[capex('h1', 'h.xlsx', 'historic'), capex('f1', 'f.xlsx', 'future')]}
        onReclassify={vi.fn()}
        onOpenDoc={vi.fn()}
      />,
    );
    expect(
      screen.getByText((_c, el) => el?.tagName === 'P' && /2 of 12 types/.test(el.textContent || '')),
    ).toBeInTheDocument();
  });
});

describe('reclassify offers Historic / Future for CAPEX docs', () => {
  it('shows the select on CapEx rows with the effective subtype and reclassifies by subtype', () => {
    const onReclassify = vi.fn();
    render(
      <DocumentCoverage
        files={[
          capex('n1', 'Legacy CapEx.pdf', null),
          { ...capex('o1', 'OM.pdf'), docType: 'OM', docSubtype: null },
        ]}
        onReclassify={onReclassify}
        onOpenDoc={vi.fn()}
      />,
    );
    const select = screen.getByLabelText('CapEx timing for Legacy CapEx.pdf') as HTMLSelectElement;
    expect(select.value).toBe('historic');
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Historic', 'Future']);
    fireEvent.change(select, { target: { value: 'future' } });
    expect(onReclassify).toHaveBeenCalledWith('n1', { doc_subtype: 'future' });
    // Non-CapEx rows get no timing select.
    expect(screen.queryByLabelText('CapEx timing for OM.pdf')).toBeNull();
  });

  it('dropping a file on Future CapEx reclassifies to CAPEX + future', () => {
    const onReclassify = vi.fn();
    const { container } = render(
      <DocumentCoverage
        files={[capex('h1', 'CapEx.xlsx', 'historic')]}
        onReclassify={onReclassify}
        onOpenDoc={vi.fn()}
      />,
    );
    const row = container.querySelector('li[draggable]')!;
    fireEvent.dragStart(row);
    fireEvent.drop(container.querySelector('li[data-category="future_capex"]')!);
    expect(onReclassify).toHaveBeenCalledWith('h1', { doc_type: 'CAPEX', doc_subtype: 'future' });
  });
});
