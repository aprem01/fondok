/**
 * CapEx lanes as real document categories — Linear FON-41, finding R-036,
 * plus the PIP lane.
 *
 * Historic CapEx, Future CapEx and PIP / Renovation Budget all upload as
 * doc_type ``CAPEX``; the worker's ``documents.doc_subtype``
 * (``pip`` | ``historic`` | ``future`` | null) tells them apart. Only the
 * PIP lane feeds the model; Historic and Future are reference only. Pinned
 * here:
 *
 *  1. The wizard upload sends ``user_doc_subtypes[]`` index-aligned with
 *     ``files[]``: the PIP slot sends ``pip``, Future CapEx ``future``,
 *     Historic CapEx ``historic``, every other slot ''; a row-level lane pick
 *     overrides the slot's pin.
 *  2. The Data Room groups CAPEX files by ``doc_subtype`` — ``future`` under
 *     Future CapEx, ``historic`` under Historic, ``pip`` AND null (legacy /
 *     bulk drop — classifier-assigned, so the loader treats it like ``pip``)
 *     under PIP / Renovation Budget.
 *  3. All three are real coverage rows (count toward "of N types").
 *  4. A CapEx file's lane select and a drop onto any of the three rows
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
import {
  DOC_SUBTYPES_BY_DOC_TYPE,
  effectiveDocSubtype,
  wizardDocSubtype,
} from '@/lib/docSubtype';
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
      { file: f('plan.pdf'), category: 'future_capex', user_doc_type: 'CAPEX' },
      { file: f('t12.pdf'), category: 'financials', user_doc_type: 'T12', fiscal_year: 2024 },
      { file: f('capex.pdf'), category: 'capex', user_doc_type: 'CAPEX' },
      { file: f('om.pdf'), category: 'om', user_doc_type: 'OM' },
      { file: f('pip.pdf'), category: 'pip_capex', user_doc_type: 'CAPEX' },
      // Dropped in Future CapEx, re-laned on the row to PIP — the row wins.
      { file: f('brand-pip.pdf'), category: 'future_capex', user_doc_type: 'CAPEX', doc_subtype: 'pip' },
    ];

    await api.documents.upload('deal-1', staged);

    expect(fetchSpy).toHaveBeenCalledTimes(1);
    const [url, init] = fetchSpy.mock.calls[0];
    expect(String(url)).toContain('/deals/deal-1/documents/upload');
    const fd = init!.body as FormData;
    expect(fd.getAll('files').map((v) => (v as File).name)).toEqual([
      'plan.pdf',
      't12.pdf',
      'capex.pdf',
      'om.pdf',
      'pip.pdf',
      'brand-pip.pdf',
    ]);
    expect(fd.getAll('user_doc_types')).toEqual(['CAPEX', 'T12', 'CAPEX', 'OM', 'CAPEX', 'CAPEX']);
    expect(fd.getAll('fiscal_years')).toEqual(['', '2024', '', '', '', '']);
    expect(fd.getAll('user_doc_subtypes')).toEqual(['future', '', 'historic', '', 'pip', 'pip']);
  });

  it('drops the slot subtype when the file is re-tagged off CAPEX', () => {
    const file = new File(['x'], 'a.pdf');
    expect(wizardDocSubtype({ file, category: 'pip_capex', user_doc_type: 'CAPEX' })).toBe('pip');
    expect(wizardDocSubtype({ file, category: 'future_capex', user_doc_type: 'CAPEX' })).toBe('future');
    expect(wizardDocSubtype({ file, category: 'capex', user_doc_type: 'CAPEX' })).toBe('historic');
    expect(wizardDocSubtype({ file, category: 'capex', user_doc_type: 'CAPEX', doc_subtype: 'pip' })).toBe('pip');
    expect(wizardDocSubtype({ file, category: 'pip_capex', user_doc_type: 'OM' })).toBe('');
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
  it('mirrors the worker contract: pip / historic / future', () => {
    expect(DOC_SUBTYPES_BY_DOC_TYPE.CAPEX).toEqual(['pip', 'historic', 'future']);
  });

  it('null / unknown CAPEX → pip (classifier-assigned, may feed the model); other doc types → null', () => {
    expect(effectiveDocSubtype('CAPEX', 'pip')).toBe('pip');
    expect(effectiveDocSubtype('CAPEX', 'future')).toBe('future');
    expect(effectiveDocSubtype('capex', 'Future')).toBe('future');
    expect(effectiveDocSubtype('CAPEX', 'historic')).toBe('historic');
    expect(effectiveDocSubtype('CAPEX', null)).toBe('pip');
    expect(effectiveDocSubtype('CAPEX', 'someday')).toBe('pip');
    expect(effectiveDocSubtype('OM', 'future')).toBeNull();
  });
});

describe('Data Room groups CAPEX by doc_subtype', () => {
  const files = [
    capex('h1', 'CapEx 2022-2024.xlsx', 'historic'),
    capex('n1', 'Legacy CapEx.pdf', null),
    capex('u1', 'Untagged CapEx.pdf'),
    capex('f1', '5yr plan.xlsx', 'future'),
    capex('p1', 'Brand PIP.pdf', 'pip'),
  ];

  it('lists historic under Historic CapEx, future under Future CapEx, pip + null under PIP / Renovation Budget', () => {
    expect(categoryForFile(files[0])?.id).toBe('capex');
    expect(categoryForFile(files[1])?.id).toBe('pip_capex');
    expect(categoryForFile(files[2])?.id).toBe('pip_capex');
    expect(categoryForFile(files[3])?.id).toBe('future_capex');
    expect(categoryForFile(files[4])?.id).toBe('pip_capex');

    const { container } = render(
      <DocumentCoverage files={files} onReclassify={vi.fn()} onOpenDoc={vi.fn()} />,
    );
    const historic = container.querySelector('li[data-category="capex"]')!.textContent!;
    const future = container.querySelector('li[data-category="future_capex"]')!.textContent!;
    const pip = container.querySelector('li[data-category="pip_capex"]')!.textContent!;
    expect(historic).toContain('CapEx 2022-2024.xlsx');
    expect(historic).not.toContain('Legacy CapEx.pdf');
    expect(historic).not.toContain('Brand PIP.pdf');
    expect(future).toContain('5yr plan.xlsx');
    expect(future).toMatch(/^Future CapEx1 file(?!s)/);
    expect(pip).toContain('Brand PIP.pdf');
    expect(pip).toContain('Legacy CapEx.pdf');
    expect(pip).toContain('Untagged CapEx.pdf');
    expect(pip).toMatch(/^PIP \/ Renovation Budget3 files/);
    expect(container.textContent).not.toMatch(/Filed as CapEx/);
  });

  it('a null-subtype CAPEX file leaves Historic and Future empty and counts only the PIP lane', () => {
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
    expect(container.querySelector('li[data-category="capex"]')!.textContent).toMatch(
      /Not uploaded/,
    );
    expect(container.querySelector('li[data-category="pip_capex"]')!.textContent).toContain(
      'Legacy CapEx.pdf',
    );
    expect(
      screen.getByText((_c, el) => el?.tagName === 'P' && /1 of 13 types/.test(el.textContent || '')),
    ).toBeInTheDocument();
  });

  it('every CapEx lane counts toward coverage', () => {
    render(
      <DocumentCoverage
        files={[
          capex('h1', 'h.xlsx', 'historic'),
          capex('f1', 'f.xlsx', 'future'),
          capex('p1', 'p.pdf', 'pip'),
        ]}
        onReclassify={vi.fn()}
        onOpenDoc={vi.fn()}
      />,
    );
    expect(
      screen.getByText((_c, el) => el?.tagName === 'P' && /3 of 13 types/.test(el.textContent || '')),
    ).toBeInTheDocument();
  });
});

describe('reclassify offers the three CapEx lanes for CAPEX docs', () => {
  it('shows the lane select on CapEx rows with the effective subtype and reclassifies by subtype', () => {
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
    const select = screen.getByLabelText('CapEx lane for Legacy CapEx.pdf') as HTMLSelectElement;
    expect(select.value).toBe('pip');
    expect(Array.from(select.options).map((o) => o.textContent)).toEqual([
      'PIP / Renovation budget',
      'Historic',
      'Future',
    ]);
    // Each option says what the lane does to the model.
    expect(select.options[0].title).toMatch(/feeds the model/);
    expect(select.options[1].title).toMatch(/reference only/);
    expect(select.options[2].title).toMatch(/reference only/);
    fireEvent.change(select, { target: { value: 'historic' } });
    expect(onReclassify).toHaveBeenCalledWith('n1', { doc_subtype: 'historic' });
    // Non-CapEx rows get no lane select.
    expect(screen.queryByLabelText('CapEx lane for OM.pdf')).toBeNull();
  });

  it.each([
    ['future_capex', 'future'],
    ['capex', 'historic'],
    ['pip_capex', 'pip'],
  ])('dropping a file on %s reclassifies to CAPEX + %s', (category, subtype) => {
    const onReclassify = vi.fn();
    const { container } = render(
      <DocumentCoverage
        files={[capex('x1', 'CapEx.xlsx', subtype === 'historic' ? 'future' : 'historic')]}
        onReclassify={onReclassify}
        onOpenDoc={vi.fn()}
      />,
    );
    const row = container.querySelector('li[draggable]')!;
    fireEvent.dragStart(row);
    fireEvent.drop(container.querySelector(`li[data-category="${category}"]`)!);
    expect(onReclassify).toHaveBeenCalledWith('x1', { doc_type: 'CAPEX', doc_subtype: subtype });
  });
});
