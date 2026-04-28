import { ButtonHTMLAttributes, forwardRef } from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '@/lib/format';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'premium';
type Size = 'sm' | 'md' | 'lg';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  /** Inline spinner replaces leading icon and disables the button. */
  loading?: boolean;
}

// All variants share `tracking-[-0.005em]` for a hair of letter-spacing
// compression — makes labels feel set rather than typed.
const variants: Record<Variant, string> = {
  // Primary — institutional navy gradient, deeper shadow, inner highlight.
  primary:
    'bg-brand-gradient text-white border border-brand-700 shadow-card ' +
    'hover:shadow-card-hover hover:brightness-110 ' +
    'active:brightness-95 ' +
    'relative before:content-[""] before:absolute before:inset-x-0 before:top-0 before:h-px ' +
    'before:bg-white/20 before:rounded-t-md',
  // Secondary — clean white with hairline border.
  secondary:
    'bg-white text-ink-900 border border-border ' +
    'hover:bg-ink-100 hover:border-ink-300 active:bg-ink-200',
  // Ghost — no chrome, generous hover surface.
  ghost:
    'bg-transparent text-ink-700 border border-transparent ' +
    'hover:bg-ink-100 hover:text-ink-900 active:bg-ink-200',
  danger:
    'bg-danger-500 hover:bg-danger-600 active:bg-danger-700 text-white border border-danger-600 shadow-card',
  // Premium — champagne gradient with a subtle gold glow.
  premium:
    'bg-gold-gradient text-white border border-gold-500 shadow-premium-glow ' +
    'hover:shadow-premium-glow hover:brightness-105 active:brightness-95 ' +
    'relative before:content-[""] before:absolute before:inset-x-0 before:top-0 before:h-px ' +
    'before:bg-white/25 before:rounded-t-md',
};

const sizes: Record<Size, string> = {
  sm: 'h-7  px-2.5 text-[12px]   rounded-md gap-1.5',
  md: 'h-8  px-3.5 text-[12.5px] rounded-md gap-2',
  lg: 'h-10 px-4   text-[13.5px] rounded-md gap-2',
};

export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ variant = 'secondary', size = 'md', loading = false, className, children, disabled, ...props }, ref) => (
    <button
      ref={ref}
      disabled={disabled || loading}
      className={cn(
        'inline-flex items-center justify-center font-medium tracking-[-0.005em]',
        'whitespace-nowrap select-none',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-1 focus-visible:ring-offset-bg',
        variants[variant],
        sizes[size],
        className,
      )}
      {...props}
    >
      {loading && <Loader2 size={size === 'sm' ? 12 : 14} className="animate-spin" aria-hidden="true" />}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';
