/**
 * Fondok canonical design-system layer — render + mapping regression suite.
 *
 * Locks the state→color/label contract extracted from the vendored canonical
 * design (`design/canonical/*.dc.html`, FON-72): every ProvenanceDot origin,
 * the six-state Data Key strip + "How to read this" popover, the KPI tile,
 * SectionCard, SubTabNav, StatementTable dense-color mapping, FieldValue field
 * treatments, and the WhereThisCameFrom popover sections.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';
import type { ValueState } from '@/lib/api';
import {
  ProvenanceDot,
  StateBadge,
  DataKey,
  KpiTile,
  SectionCard,
  SubTabNav,
  StatementTable,
  denseValueColor,
  FieldValue,
  fieldKindFromState,
  WhereThisCameFrom,
  prov,
  field,
  provDot,
} from '@/components/design';

afterEach(cleanup);

const ALL_STATES: ValueState[] = [
  'document_sourced',
  'linked',
  'assumption',
  'calculated',
  'awaiting_data',
  'needs_review',
];

describe('ProvenanceDot — 6-state origin mapping (Fondok Data Key)', () => {
  it('labels every state with its canonical name', () => {
    for (const s of ALL_STATES) {
      const { unmount } = render(<ProvenanceDot state={s} />);
      expect(screen.getByRole('img')).toHaveAccessibleName(provDot[s].label);
      unmount();
    }
  });

  it('fills document_sourced with the canonical green', () => {
    render(<ProvenanceDot state="document_sourced" />);
    expect(screen.getByRole('img')).toHaveStyle({ background: prov.green });
  });

  it('renders linked as a hollow green ring (white fill, 2px green border)', () => {
    render(<ProvenanceDot state="linked" />);
    const dot = screen.getByRole('img');
    expect(dot).toHaveStyle({ border: `2px solid ${prov.green}` });
  });

  it('fills assumption blue and calculated grey', () => {
    const { unmount } = render(<ProvenanceDot state="assumption" />);
    expect(screen.getByRole('img')).toHaveStyle({ background: prov.blue });
    unmount();
    render(<ProvenanceDot state="calculated" />);
    expect(screen.getByRole('img')).toHaveStyle({ background: prov.gray });
  });

  it('renders awaiting_data as a dashed muted hollow dot', () => {
    render(<ProvenanceDot state="awaiting_data" />);
    expect(screen.getByRole('img')).toHaveStyle({ border: `1px dashed ${prov.muted}` });
  });

  it('renders needs_review as a green origin with the amber halo ring', () => {
    render(<ProvenanceDot state="needs_review" />);
    const dot = screen.getByRole('img');
    expect(dot).toHaveStyle({ background: prov.green });
    expect(dot.getAttribute('style')).toContain('box-shadow');
  });

  it('overlays the review halo on any origin via the review prop', () => {
    render(<ProvenanceDot state="assumption" review />);
    const dot = screen.getByRole('img');
    expect(dot).toHaveStyle({ background: prov.blue });
    expect(dot.getAttribute('style')).toContain('box-shadow');
  });

  it('StateBadge shows the dot + canonical label text', () => {
    render(<StateBadge state="calculated" />);
    expect(screen.getByText('Calculated')).toBeInTheDocument();
  });
});

describe('DataKey — canonical strip + How-to-read-this popover', () => {
  it('renders the DATA KEY label, all six origin labels and both sample rows', () => {
    render(<DataKey totalSample="1,240" editableSample="123" />);
    expect(screen.getByText('DATA KEY')).toBeInTheDocument();
    for (const s of ALL_STATES) {
      expect(screen.getByText(provDot[s].label)).toBeInTheDocument();
    }
    expect(screen.getByText('1,240')).toBeInTheDocument();
    expect(screen.getByText('123')).toBeInTheDocument();
    expect(screen.getByText('Total / summary')).toBeInTheDocument();
    expect(screen.getByText('Editable — click to change')).toBeInTheDocument();
  });

  it('opens the help modal with intro, all eight tokens and the footer', () => {
    render(<DataKey />);
    fireEvent.click(screen.getByText('ⓘ How to read this'));
    const dialog = screen.getByRole('dialog', { name: 'How to read this' });
    const q = within(dialog);
    // 6 origins + Total/summary + Editable modifiers
    expect(q.getByText('Document sourced')).toBeInTheDocument();
    expect(q.getByText('Total / summary')).toBeInTheDocument();
    expect(q.getByText('Editable')).toBeInTheDocument();
    // canonical "means" copy for an origin
    expect(q.getByText(/Extracted by Fondok from a document/)).toBeInTheDocument();
    expect(q.getByText(/Click any value to see where it came from/)).toBeInTheDocument();
  });

  it('is controllable via helpOpen', () => {
    render(<DataKey helpOpen />);
    expect(screen.getByRole('dialog', { name: 'How to read this' })).toBeInTheDocument();
  });
});

describe('KpiTile (Cash Flow / Investment summary tile)', () => {
  it('renders label, value, sub and applies the value color', () => {
    render(<KpiTile label="Equity Multiple" value="2.47x" sub="Base case" valueColor={prov.green} />);
    expect(screen.getByText('Equity Multiple')).toBeInTheDocument();
    expect(screen.getByText('2.47x')).toHaveStyle({ color: prov.green });
    expect(screen.getByText('Base case')).toBeInTheDocument();
  });
});

describe('SectionCard', () => {
  it('eyebrow variant renders an uppercase title + note + body', () => {
    render(
      <SectionCard title="Exit" note="per key">
        <div>body-a</div>
      </SectionCard>,
    );
    expect(screen.getByText('Exit')).toHaveStyle({ textTransform: 'uppercase' });
    expect(screen.getByText('per key')).toBeInTheDocument();
    expect(screen.getByText('body-a')).toBeInTheDocument();
  });

  it('title variant renders a sentence-case title + caption + body', () => {
    render(
      <SectionCard variant="title" title="Cash Flow" note="FY24A–FY28E">
        <div>body-b</div>
      </SectionCard>,
    );
    expect(screen.getByText('Cash Flow')).toBeInTheDocument();
    expect(screen.getByText('FY24A–FY28E')).toBeInTheDocument();
    expect(screen.getByText('body-b')).toBeInTheDocument();
  });
});

describe('SubTabNav (Fondok Sub-Tabs standard)', () => {
  const items = [
    { id: 'hist', label: 'Historicals' },
    { id: 'proj', label: 'Projections' },
    { id: 'idx', label: 'Index Analysis', disabled: true, disabledHint: 'No STR export yet' },
  ];

  it('marks the active tab selected and the rest not', () => {
    render(<SubTabNav items={items} activeId="hist" caption="Actual operating history" />);
    expect(screen.getByRole('tab', { name: 'Historicals' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Projections' })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByText('Actual operating history')).toBeInTheDocument();
  });

  it('calls onSelect for enabled tabs but not disabled ones', () => {
    const onSelect = vi.fn();
    render(<SubTabNav items={items} activeId="hist" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole('tab', { name: 'Projections' }));
    expect(onSelect).toHaveBeenCalledWith('proj');
    const disabled = screen.getByRole('tab', { name: 'Index Analysis' });
    expect(disabled).toBeDisabled();
    fireEvent.click(disabled);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe('StatementTable (navy statement grid)', () => {
  it('maps each state to the canonical dense value color', () => {
    expect(denseValueColor('document_sourced')).toBe(prov.green);
    expect(denseValueColor('linked')).toBe(prov.green);
    expect(denseValueColor('assumption')).toBe(prov.blue);
    expect(denseValueColor('calculated')).toBe(prov.gray);
    expect(denseValueColor('awaiting_data')).toBe(prov.muted);
    expect(denseValueColor('needs_review')).toBe(prov.green);
  });

  it('renders the navy header, row labels, dots and colour-carrying cells', () => {
    render(
      <StatementTable
        columns={['FY24A', 'FY25A']}
        rows={[
          {
            label: 'ADR',
            state: 'document_sourced',
            cells: [
              { text: '$276', state: 'document_sourced' },
              { text: '$286', state: 'assumption' },
            ],
          },
          {
            label: 'Net Operating Income',
            total: true,
            state: 'calculated',
            cells: [
              { text: '$2,438k', state: 'calculated' },
              { text: '$2,551k', state: 'calculated' },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByText('LINE ITEM')).toBeInTheDocument();
    expect(screen.getByText('FY24A')).toBeInTheDocument();
    expect(screen.getByText('ADR')).toBeInTheDocument();
    expect(screen.getByText('$276')).toHaveStyle({ color: prov.green });
    expect(screen.getByText('$286')).toHaveStyle({ color: prov.blue });
    // total row is bold near-black
    expect(screen.getByText('$2,438k')).toHaveStyle({ color: prov.gray });
    // origin dots present in the label cells (2 rows)
    expect(screen.getAllByRole('img').length).toBe(2);
  });
});

describe('FieldValue (Fondok Field System inline treatment)', () => {
  it('derives the field kind from a ValueState', () => {
    expect(fieldKindFromState('document_sourced')).toBe('doc');
    expect(fieldKindFromState('linked')).toBe('link');
    expect(fieldKindFromState('assumption')).toBe('input');
    expect(fieldKindFromState('calculated')).toBe('calc');
    expect(fieldKindFromState('needs_review')).toBe('review');
    expect(fieldKindFromState('awaiting_data')).toBe('awaiting');
    expect(fieldKindFromState('document_sourced', { override: true })).toBe('override');
  });

  it('renders an editable assumption in blue with the dashed underline', () => {
    render(<FieldValue kind="input" value="6.50%" />);
    const el = screen.getByText('6.50%');
    // value span carries the dashed rule; wrapper carries the input blue
    expect(el).toHaveStyle({ borderBottom: field.inputRule });
    expect(el.parentElement).toHaveStyle({ color: field.input });
  });

  it('renders a document-sourced value with the page-corner glyph', () => {
    render(<FieldValue kind="doc" value="132" />);
    expect(screen.getByText(field.glyphDoc)).toBeInTheDocument();
  });

  it('renders a linked value with the outbound-arrow glyph', () => {
    render(<FieldValue kind="link" value="$4,440,848" />);
    expect(screen.getByText(field.glyphLink)).toBeInTheDocument();
  });

  it('renders calculated as plain ink with no glyph or rule', () => {
    render(<FieldValue kind="calc" value="$68,320,731" />);
    expect(screen.queryByText(field.glyphDoc)).not.toBeInTheDocument();
    expect(screen.queryByText(field.glyphLink)).not.toBeInTheDocument();
    expect(screen.getByText('$68,320,731').parentElement).toHaveStyle({ color: field.ink });
  });

  it('shows the framed blue editor in editing mode', () => {
    render(<FieldValue kind="input" value="6.50" editing />);
    expect(screen.getByText('6.50')).toHaveStyle({ color: field.input });
  });

  it('fires onClick (click-to-edit / open popover)', () => {
    const onClick = vi.fn();
    render(<FieldValue kind="input" value="6.50%" onClick={onClick} />);
    fireEvent.click(screen.getByText('6.50%'));
    expect(onClick).toHaveBeenCalled();
  });
});

describe('WhereThisCameFrom (anchored provenance popover)', () => {
  it('renders header, source, override, deps and actions', () => {
    const onClose = vi.fn();
    render(
      <WhereThisCameFrom
        kind="Overridden · was sourced"
        label="Purchase Price"
        where="Investment / Acquisition"
        value="$35,500,000"
        source={{
          doc: 'Kimpton Angler — Offering Memorandum',
          loc: 'Page 14 · Acquisition Summary',
          text: 'Asking price of $36,436,802',
          confidence: '98%',
        }}
        override={{
          orig: '$36,436,802',
          current: '$35,500,000',
          meta: [{ k: 'Changed by', v: 'M. Okafor' }],
        }}
        deps={{ count: '11 fields', items: [{ name: 'Total Uses', where: 'Investment' }] }}
        actions={[
          { label: 'Restore source value', primary: true },
          { label: 'View source' },
        ]}
        onClose={onClose}
      />,
    );
    const dialog = screen.getByRole('dialog', { name: /Purchase Price/ });
    const q = within(dialog);
    expect(q.getByText('Overridden · was sourced')).toBeInTheDocument();
    expect(q.getByText('Purchase Price')).toBeInTheDocument();
    expect(q.getByText('Investment / Acquisition')).toBeInTheDocument();
    expect(q.getByText('Source')).toBeInTheDocument();
    expect(q.getByText('Kimpton Angler — Offering Memorandum')).toBeInTheDocument();
    expect(q.getByText('98% confidence')).toBeInTheDocument();
    expect(q.getByText('Override')).toBeInTheDocument();
    expect(q.getByText('$36,436,802')).toHaveStyle({ textDecoration: 'line-through' });
    expect(q.getByText('Affects downstream')).toBeInTheDocument();
    expect(q.getByText('11 fields')).toBeInTheDocument();

    // primary vs secondary action chrome
    const primary = q.getByRole('button', { name: 'Restore source value' });
    expect(primary).toHaveStyle({ background: '#14213d', color: '#fff' });

    fireEvent.click(q.getByText('✕'));
    expect(onClose).toHaveBeenCalled();
  });

  it('renders the calculation section with the formula and inputs', () => {
    render(
      <WhereThisCameFrom
        kind="Calculated"
        label="Loan Amount"
        where="Debt / Senior loan"
        value="$23,187,000"
        calc={{
          expr: 'min( LTC × Total Uses , NOI ÷ Debt Yield )',
          numbers: 'min( 65.0% × $42,572,678 , … )',
          inputs: [{ name: 'LTC', path: 'Debt / Assumptions', dotColor: field.input }],
        }}
      />,
    );
    expect(screen.getByText('Calculation')).toBeInTheDocument();
    expect(screen.getByText('min( LTC × Total Uses , NOI ÷ Debt Yield )')).toBeInTheDocument();
    expect(screen.getByText('LTC')).toBeInTheDocument();
  });
});
