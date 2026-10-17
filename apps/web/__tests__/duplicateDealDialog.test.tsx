/**
 * DuplicateDealDialog — the per-copy choice Sam asked for.
 *
 *   1. The checkbox "Also copy P&L cell comments" is OFF by default, and
 *      resets to off every time the dialog opens.
 *   2. The summary lists what the copy gets / does not get for the current
 *      choice: comments sit under "Not copied" until the box is ticked, then
 *      move to "The copy gets" (marked as copied from this deal).
 *   3. Confirm hands the choice to the caller; Cancel closes without it.
 *   4. While the POST is in flight the buttons are disabled and the label
 *      says "Duplicating…".
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';
import React from 'react';
import DuplicateDealDialog from '@/components/project/DuplicateDealDialog';

afterEach(cleanup);

function items(testId: string): string[] {
  return within(screen.getByTestId(testId))
    .getAllByRole('listitem')
    .map((li) => li.textContent ?? '');
}

describe('DuplicateDealDialog', () => {
  it('renders nothing when closed', () => {
    render(
      <DuplicateDealDialog open={false} dealName="Kimpton" onClose={() => {}} onConfirm={() => {}} />,
    );
    expect(screen.queryByTestId('duplicate-deal-dialog')).toBeNull();
  });

  it('defaults the comment checkbox to off and lists comments under "Not copied"', () => {
    render(<DuplicateDealDialog open dealName="Kimpton Miami Beach" onClose={() => {}} onConfirm={() => {}} />);
    expect(screen.getByText(/Kimpton Miami Beach/)).toBeTruthy();
    const box = screen.getByRole('checkbox', { name: /Also copy P&L cell comments/ }) as HTMLInputElement;
    expect(box.checked).toBe(false);

    const copied = items('duplicate-deal-copied');
    expect(copied).toEqual([
      'the deal details',
      'your assumptions and their notes',
      'scenarios',
      'every document with its latest extraction (including the fields you reviewed)',
    ]);
    expect(items('duplicate-deal-skipped')).toEqual([
      'model runs',
      'the activity history',
      'P&L cell comments',
    ]);
  });

  it('moves comments into "The copy gets" when ticked and passes the choice on confirm', () => {
    const onConfirm = vi.fn();
    render(<DuplicateDealDialog open dealName="Kimpton" onClose={() => {}} onConfirm={onConfirm} />);
    const box = screen.getByRole('checkbox', { name: /Also copy P&L cell comments/ });
    fireEvent.click(box);

    expect(items('duplicate-deal-copied')).toContain(
      'P&L cell comments (each marked as copied from this deal)',
    );
    expect(items('duplicate-deal-skipped')).toEqual(['model runs', 'the activity history']);

    fireEvent.click(screen.getByRole('button', { name: 'Duplicate' }));
    expect(onConfirm).toHaveBeenCalledWith({ includeCellComments: true });
  });

  it('confirms with comments OFF when untouched, and Cancel only closes', () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();
    render(<DuplicateDealDialog open dealName="Kimpton" onClose={onClose} onConfirm={onConfirm} />);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Duplicate' }));
    expect(onConfirm).toHaveBeenCalledWith({ includeCellComments: false });
  });

  it('resets the choice to off on every open', () => {
    const { rerender } = render(
      <DuplicateDealDialog open dealName="Kimpton" onClose={() => {}} onConfirm={() => {}} />,
    );
    fireEvent.click(screen.getByRole('checkbox', { name: /Also copy P&L cell comments/ }));
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(true);

    rerender(<DuplicateDealDialog open={false} dealName="Kimpton" onClose={() => {}} onConfirm={() => {}} />);
    rerender(<DuplicateDealDialog open dealName="Kimpton" onClose={() => {}} onConfirm={() => {}} />);
    expect((screen.getByRole('checkbox') as HTMLInputElement).checked).toBe(false);
  });

  it('disables the buttons and says Duplicating… while busy', () => {
    const onClose = vi.fn();
    render(<DuplicateDealDialog open busy dealName="Kimpton" onClose={onClose} onConfirm={() => {}} />);
    const go = screen.getByRole('button', { name: /Duplicating…/ }) as HTMLButtonElement;
    expect(go.disabled).toBe(true);
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('checkbox') as HTMLInputElement).disabled).toBe(true);
  });
});
