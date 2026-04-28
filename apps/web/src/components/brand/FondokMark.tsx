import { cn } from '@/lib/format';

type Size = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const sizeMap: Record<Size, {
  square: string;
  rounded: string;
  letter: string;
  letterOffset: string;
  wordmark: string;
  gap: string;
}> = {
  xs: { square: 'w-6 h-6',  rounded: 'rounded-[7px]',  letter: 'text-[13px]', letterOffset: '-mt-px',   wordmark: 'text-[12px]',   gap: 'gap-2'   },
  sm: { square: 'w-7 h-7',  rounded: 'rounded-[8px]',  letter: 'text-[15px]', letterOffset: '-mt-px',   wordmark: 'text-[13.5px]', gap: 'gap-2'   },
  md: { square: 'w-9 h-9',  rounded: 'rounded-[10px]', letter: 'text-[18px]', letterOffset: '-mt-0.5',  wordmark: 'text-[15px]',   gap: 'gap-2.5' },
  lg: { square: 'w-11 h-11', rounded: 'rounded-[12px]', letter: 'text-[22px]', letterOffset: '-mt-0.5', wordmark: 'text-[18px]',   gap: 'gap-3'   },
  xl: { square: 'w-14 h-14', rounded: 'rounded-[14px]', letter: 'text-[28px]', letterOffset: '-mt-1',   wordmark: 'text-[22px]',   gap: 'gap-3.5' },
};

export default function FondokMark({
  size = 'md',
  wordmark = true,
  className,
  /**
   * `navy`  — institutional brand gradient (default, sidebar/header use).
   * `gold`  — champagne gradient for premium contexts (memo cover, hero CTA).
   * `mono`  — flat ink-900 for embedded contexts (PDF print).
   */
  variant = 'navy',
}: {
  size?: Size;
  wordmark?: boolean;
  className?: string;
  variant?: 'navy' | 'gold' | 'mono';
}) {
  const s = sizeMap[size];

  const squareBg =
    variant === 'gold' ? 'bg-gold-gradient' :
    variant === 'mono' ? 'bg-ink-900' :
    'bg-brand-gradient';

  const accentDot =
    variant === 'gold' ? 'text-gold-500' :
    variant === 'mono' ? 'text-ink-700' :
    'text-gold-400';

  return (
    <span className={cn('inline-flex items-center', s.gap, className)}>
      <span
        className={cn(
          s.square,
          s.rounded,
          squareBg,
          'flex items-center justify-center shadow-card',
          variant !== 'mono' && 'ring-1 ring-inset ring-white/10',
        )}
        aria-hidden="true"
      >
        <span
          className={cn(
            'font-serif font-semibold text-white leading-none',
            s.letter,
            s.letterOffset,
          )}
        >
          F
        </span>
      </span>
      {wordmark && (
        <span
          className={cn(
            'font-display font-semibold text-ink-900 tracking-[-0.018em] leading-none',
            s.wordmark,
          )}
        >
          Fondok<span className={accentDot}>.</span>
        </span>
      )}
    </span>
  );
}
