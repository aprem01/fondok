'use client';
import { MoreHorizontal } from 'lucide-react';
import { useEffect, useRef, useState, MouseEvent } from 'react';
import { cn } from '@/lib/format';

export type KebabItem = {
  label: string;
  onSelect?: () => void;
  danger?: boolean;
};

export default function KebabMenu({
  items,
  align = 'right',
  className,
}: {
  items: KebabItem[];
  align?: 'left' | 'right';
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClick(e: globalThis.MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) {
      document.addEventListener('mousedown', onClick);
      return () => document.removeEventListener('mousedown', onClick);
    }
  }, [open]);

  return (
    <div className={cn('relative inline-block', className)} ref={ref}>
      <button
        aria-label="More actions"
        className="p-1 hover:bg-ink-300/20 rounded"
        onClick={(e: MouseEvent<HTMLButtonElement>) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen(o => !o);
        }}
      >
        <MoreHorizontal size={14} className="text-ink-400" />
      </button>
      {open && (
        <div
          className={cn(
            'absolute top-full mt-1 bg-white border border-border rounded-lg shadow-lg py-1 z-50 min-w-[160px]',
            align === 'right' ? 'right-0' : 'left-0',
          )}
        >
          {items.map((it, i) => (
            <button
              key={`${it.label}-${i}`}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setOpen(false);
                it.onSelect?.();
              }}
              className={cn(
                'w-full px-3 py-2 text-[12.5px] hover:bg-ink-300/10 text-left',
                it.danger && 'text-danger-700 hover:bg-danger-50',
              )}
            >
              {it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
