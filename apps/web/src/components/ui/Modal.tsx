'use client';

/**
 * Lightweight modal primitive — no external library.
 *
 * Renders a fixed overlay + centered Card. Click outside dismisses;
 * Escape closes. Children are responsible for the modal's internal
 * layout (header, body, footer). Caller controls open state.
 *
 *   <Modal open={open} onClose={() => setOpen(false)} title="Add Market">
 *     <div className="p-5">…</div>
 *   </Modal>
 */

import { useEffect } from 'react';
import { X } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { cn } from '@/lib/format';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  /** Tailwind max-width class; defaults to max-w-md. */
  maxWidth?: string;
  children: React.ReactNode;
}

export default function Modal({
  open,
  onClose,
  title,
  maxWidth = 'max-w-md',
  children,
}: ModalProps) {
  // Escape closes; only bind while open so we don't leak listeners.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={title}
      className="fixed inset-0 z-50 flex items-center justify-center bg-ink-900/40 px-4"
      onClick={onClose}
    >
      <Card
        className={cn('w-full', maxWidth)}
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <div className="flex items-center justify-between px-5 py-3.5 border-b border-border">
            <h3 className="text-[14px] font-semibold text-ink-900">{title}</h3>
            <button
              type="button"
              aria-label="Close"
              onClick={onClose}
              className="p-1 -m-1 rounded text-ink-500 hover:text-ink-900 hover:bg-ink-300/20 transition-colors"
            >
              <X size={14} />
            </button>
          </div>
        )}
        {children}
      </Card>
    </div>
  );
}
