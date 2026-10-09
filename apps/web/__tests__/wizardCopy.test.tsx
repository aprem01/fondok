/**
 * Create-deal wizard copy — external tester workbook (Linear FON-41).
 *
 * Pins the wording fixes exactly as the workbook asked for them:
 *
 *   R-013  one "* Required field" legend on the only step that stars labels;
 *          unstarred labels carry no "(optional)" suffix.
 *   R-014  hint / help copy says "direct", never "proprietary".
 *   R-016  the Return Profile step reads "LIRR" and never a bare "IRR".
 *   R-017  the Return Profile hint reads the exact calibrate sentence.
 *   R-028  the Offering Memorandum slot says "seller pitch deck" / "seller
 *          pro forma", not "broker …".
 *   R-029  the upload prompt capitalises the document name.
 *   R-019  searching a brand under a collapsed chain shows the SPECIFIC brand
 *          with the chain as secondary text, and the specific brand is what
 *          is submitted (Kimpton → IHG chain; Curio → Hilton chain).
 *   R-048  (FON-59, Sam's decision 4) the picker is the PROPOSED brand and is
 *          submitted as `proposed_brand`; the optional "Existing brand" text
 *          input is submitted as `brand` (blank → null, left to the OM).
 *
 * The real page, real DocumentsStep and real brand catalog are rendered;
 * only navigation, the worker api, toasts and the CoachMark popover are
 * stubbed (the stub renders the hint body inline so its copy is testable).
 */
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest';
import { render, screen, cleanup, fireEvent, waitFor, within } from '@testing-library/react';
import React from 'react';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn(), back: vi.fn() }),
}));
vi.mock('next/link', () => ({
  default: ({ href, children, ...rest }: { href: string; children: React.ReactNode }) => (
    <a href={href} {...rest}>{children}</a>
  ),
}));

const worker = vi.hoisted(() => ({
  create: vi.fn(async (body: { name: string }) => ({ id: 'deal-new-1', name: body.name })),
  upload: vi.fn(async () => []),
}));
vi.mock('@/lib/api', () => ({
  isWorkerConnected: () => true,
  api: {
    deals: { create: worker.create },
    documents: { upload: worker.upload },
  },
}));
vi.mock('@/components/ui/Toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
// Render the hint copy inline (the real popover portals + sequences on
// localStorage) so R-014 / R-017 can be asserted on the body text.
vi.mock('@/components/help/CoachMark', () => ({
  CoachMark: ({ children, title, body }: { children?: React.ReactNode; title: string; body: React.ReactNode }) => (
    <div>
      <div data-testid="coach-hint">
        <div>{title}</div>
        <div>{body}</div>
      </div>
      {children}
    </div>
  ),
}));

import NewProjectPage from '@/app/projects/new/page';

const bodyText = () => document.body.textContent ?? '';
const nextButton = () => screen.getByRole('button', { name: /^next\s*$/i });
const clickNext = () => fireEvent.click(nextButton());

/** Step 1 → Step 2. Fills the one field `onCreate` insists on. */
function fillStep1AndAdvance() {
  fireEvent.change(screen.getByPlaceholderText('Chicago Downtown Acquisition'), {
    target: { value: 'Workbook Deal' },
  });
  clickNext();
  expect(screen.getByText('Return Requirements')).toBeInTheDocument();
}

/** Step 3 is gated on a financial statement — stage one through the real
 *  DocumentsStep (category switch + the hidden file input). */
function stageFinancialAndAdvance() {
  fireEvent.click(screen.getByRole('button', { name: 'Financial Statements (missing)' }));
  const input = screen.getByLabelText('Add Financial Statements files');
  const t12 = new File(['t12'], 'T12-Mar-2026.xlsx', { type: 'application/vnd.ms-excel' });
  fireEvent.change(input, { target: { files: [t12] } });
  expect(screen.getByRole('button', { name: 'Financial Statements (1 file)' })).toBeInTheDocument();
  clickNext();
  expect(screen.getByRole('heading', { name: 'Brand and Positioning' })).toBeInTheDocument();
}

function driveToBrandStep() {
  render(<NewProjectPage />);
  fillStep1AndAdvance();
  clickNext(); // Step 2 → 3 (no gate)
  expect(screen.getByRole('region', { name: 'Offering Memorandum' })).toBeInTheDocument();
  stageFinancialAndAdvance();
}

beforeEach(() => {
  worker.create.mockClear();
  worker.upload.mockClear();
  window.localStorage.clear();
});
afterEach(cleanup);

describe('R-013 — required-field legend and optional labels', () => {
  it('shows "* Required field" exactly once on the starred step, keeps the stars, drops "(optional)"', () => {
    render(<NewProjectPage />);
    expect(screen.getAllByText('* Required field')).toHaveLength(1);
    // The starred labels are untouched.
    expect(screen.getByText('Deal Type *')).toBeInTheDocument();
    expect(screen.getByText('Deal Name *')).toBeInTheDocument();
    expect(screen.getByText('City / Submarket *')).toBeInTheDocument();
    expect(screen.getByText('How far along are you in the acquisition process? *')).toBeInTheDocument();
    expect(screen.getByText('Sourcing channel *')).toBeInTheDocument();
    // Unstarred now means optional — no bracketed suffix anywhere on the step.
    expect(screen.getByText('Keys')).toBeInTheDocument();
    expect(screen.getByText('Hotel Name')).toBeInTheDocument();
    expect(screen.getByText('Indicative Price')).toBeInTheDocument();
    expect(bodyText()).not.toMatch(/\(optional\)/i);
  });

  it('renders no legend on steps without starred labels', () => {
    render(<NewProjectPage />);
    fillStep1AndAdvance();
    expect(screen.queryByText('* Required field')).toBeNull();
    clickNext();
    expect(screen.getByRole('region', { name: 'Offering Memorandum' })).toBeInTheDocument();
    expect(screen.queryByText('* Required field')).toBeNull();
  });
});

describe('R-014 — sourcing hint says "direct", not "proprietary"', () => {
  it('replaces the word in both the coach mark and the field help', () => {
    render(<NewProjectPage />);
    expect(bodyText()).not.toMatch(/proprietary/i);
    expect(screen.getByTestId('coach-hint').textContent).toContain('capital partner, direct)');
    expect(screen.getByText(/capital partner, or direct\./)).toBeInTheDocument();
  });
});

describe('R-016 / R-017 — Return Profile step reads LIRR and the exact calibrate sentence', () => {
  it('contains LIRR and no bare IRR; hint has the exact sentence and no "fine-tune"', () => {
    render(<NewProjectPage />);
    fillStep1AndAdvance();
    const text = bodyText();
    expect(text).toContain('LIRR');
    expect(text.replace(/LIRR/g, '')).not.toContain('IRR');
    expect(screen.getAllByText(/^Target LIRR: /)).toHaveLength(3);
    const hint = screen.getByTestId('coach-hint').textContent ?? '';
    expect(hint).toContain('You can calibrate leverage and exit cap in the Returns section of the Overview.');
    expect(hint).not.toMatch(/fine-tune/i);
  });
});

describe('R-028 / R-029 — Offering Memorandum slot copy', () => {
  it('says seller pitch deck / seller pro forma and capitalises the drop prompt', () => {
    render(<NewProjectPage />);
    fillStep1AndAdvance();
    clickNext();
    const om = screen.getByRole('region', { name: 'Offering Memorandum' });
    expect(om.textContent).toMatch(/Seller pitch deck/);
    expect(om.textContent).toMatch(/seller pro forma/);
    expect(om.textContent).not.toMatch(/broker pitch deck/i);
    expect(om.textContent).not.toMatch(/broker pro ?forma/i);
    expect(screen.getByText('Drop Offering Memorandum here')).toBeInTheDocument();
    expect(screen.getByText('Drop an Offering Memorandum here or skip for now.')).toBeInTheDocument();
    expect(bodyText()).not.toMatch(/Drop offering memorandum/);
  });
});

describe('R-019 — brand search shows the specific brand with its chain; submits the specific brand', () => {
  it('Kimpton (IHG, collapsed by default) surfaces as its own card with "· IHG" and submits the specific brand', async () => {
    driveToBrandStep();
    fireEvent.change(screen.getByPlaceholderText('Search brands...'), { target: { value: 'Kimpton' } });
    // The specific brand is visible without expanding the chain by hand.
    const card = screen.getByRole('button', { name: /Kimpton Hotels & Restaurants/ });
    expect(card.textContent).toContain('Kimpton Hotels & Restaurants');
    expect(card.textContent).toContain('· IHG');
    fireEvent.click(card);
    const selected = screen.getByText('Selected:', { exact: false });
    expect(selected.textContent).toBe('Selected: Kimpton Hotels & Restaurants · IHG');

    clickNext(); // → Review (R-024: brand + positioning are one step)
    expect(screen.getByText('Review & Create Deal')).toBeInTheDocument();
    expect(bodyText()).toContain('Kimpton Hotels & Restaurants · IHG');

    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({
      name: 'Workbook Deal',
      proposed_brand: 'Kimpton Hotels & Restaurants',
      // No existing brand typed → null, left for the OM to fill.
      brand: null,
    });
  });

  it('Curio (Hilton, expanded by default) shows "· Hilton" and submits the specific brand — same shape as an IHG sub-brand', async () => {
    driveToBrandStep();
    fireEvent.change(screen.getByPlaceholderText('Search brands...'), { target: { value: 'Curio' } });
    const card = screen.getByRole('button', { name: /Curio Collection by Hilton/ });
    expect(card.textContent).toContain('· Hilton');
    fireEvent.click(card);
    expect(screen.getByText('Selected:', { exact: false }).textContent).toBe('Selected: Curio Collection by Hilton · Hilton');

    clickNext(); // → Review
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ proposed_brand: 'Curio Collection by Hilton', brand: null });
  });

  it('a chain-name search lists every brand in that chain as specific cards', () => {
    driveToBrandStep();
    fireEvent.change(screen.getByPlaceholderText('Search brands...'), { target: { value: 'IHG' } });
    // Anchored: the positioning cards on the same step mention Holiday Inn Express as an example.
    expect(screen.getByRole('button', { name: /^Holiday Inn Express/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Kimpton Hotels & Restaurants/ })).toBeInTheDocument();
  });
});

describe('R-048 — Existing brand and Proposed brand are two separate fields', () => {
  it('sends a typed Existing brand as `brand` and the picked brand as `proposed_brand` (Kimpton existing / Thompson proposed)', async () => {
    driveToBrandStep();
    expect(screen.getByText('Proposed brand')).toBeInTheDocument();
    expect(screen.getByText('Existing brand')).toBeInTheDocument();
    expect(screen.getByText('Leave blank to source from the Offering Memorandum')).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText('e.g. Kimpton'), { target: { value: '  Kimpton  ' } });
    fireEvent.change(screen.getByPlaceholderText('Search brands...'), { target: { value: 'Thompson' } });
    fireEvent.click(screen.getByRole('button', { name: /Thompson Hotels/ }));

    clickNext(); // → Review (R-024: brand + positioning are one step)
    // The Review step shows both, each on its own row.
    expect(screen.getByText('Existing Brand')).toBeInTheDocument();
    expect(screen.getByText('Proposed Brand')).toBeInTheDocument();
    expect(screen.getByText('Existing Brand').nextElementSibling?.textContent).toBe('Kimpton');
    expect(screen.getByText('Proposed Brand').nextElementSibling?.textContent).toMatch(/^Thompson Hotels/);

    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    const body = worker.create.mock.calls[0][0] as Record<string, unknown>;
    expect(body.brand).toBe('Kimpton');
    expect(body.proposed_brand).toBe('Thompson Hotels');
  });

  it('Brand Agnostic sends proposed_brand null; a blank Existing brand sends brand null', async () => {
    driveToBrandStep();
    clickNext(); // → Review
    expect(screen.getByText('Existing Brand').nextElementSibling?.textContent).toBe('From the Offering Memorandum');
    expect(screen.getByText('Proposed Brand').nextElementSibling?.textContent).toBe('Brand Agnostic');
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ brand: null, proposed_brand: null });
  });
});

describe('R-024 — Brand and Positioning: choosing a brand pre-fills positioning, still editable', () => {
  it('Kimpton pre-fills Upper Upscale (STR chain scale); the analyst override is what gets submitted', async () => {
    driveToBrandStep();
    expect(screen.getByText('Brand and Positioning', { selector: 'div' })).toBeInTheDocument(); // stepper label
    fireEvent.change(screen.getByPlaceholderText('Search brands...'), { target: { value: 'Kimpton' } });
    fireEvent.click(screen.getByRole('button', { name: /Kimpton Hotels & Restaurants/ }));
    expect(bodyText()).toContain('Pre-filled from Kimpton Hotels & Restaurants');

    // Analyst changes it for this property.
    fireEvent.click(screen.getByRole('button', { name: /^Luxury/ }));
    clickNext(); // → Review
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ proposed_brand: 'Kimpton Hotels & Restaurants', positioning: 'luxury' });
  });

  it('without an override the pre-filled positioning is submitted', async () => {
    driveToBrandStep();
    fireEvent.change(screen.getByPlaceholderText('Search brands...'), { target: { value: 'Motel One' } });
    fireEvent.click(screen.getByRole('button', { name: /^Motel One\s*Upper Midscale/ }));
    clickNext();
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ proposed_brand: 'Motel One', positioning: 'upper-midscale' });
  });
});

describe('R-025 — Operating model is captured on the Brand step and sent as `operating_model`', () => {
  it('a picked operating model shows on Review and is submitted', async () => {
    driveToBrandStep();
    const group = screen.getByRole('radiogroup', { name: 'Operating model' });
    fireEvent.click(within(group).getByRole('radio', { name: /Third-party operator/ }));
    expect(within(group).getByRole('radio', { name: /Third-party operator/ })).toHaveAttribute('aria-checked', 'true');

    clickNext(); // → Review
    expect(screen.getByText('Operating Model').nextElementSibling?.textContent).toBe('Third-party operator');

    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ operating_model: 'third_party' });
  });

  it('is optional — nothing picked sends operating_model null', async () => {
    driveToBrandStep();
    clickNext();
    expect(screen.getByText('Operating Model').nextElementSibling?.textContent).toBe('Not specified');
    fireEvent.click(screen.getByRole('button', { name: /create deal/i }));
    await waitFor(() => expect(worker.create).toHaveBeenCalledTimes(1));
    expect(worker.create.mock.calls[0][0]).toMatchObject({ operating_model: null });
  });
});
