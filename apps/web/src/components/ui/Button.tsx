import { ButtonHTMLAttributes, forwardRef } from 'react';
import { cn } from '@/lib/format';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md';

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant; size?: Size;
}

const variants: Record<Variant, string> = {
  primary: 'bg-brand-500 hover:bg-brand-600 text-white border border-brand-500',
  secondary: 'bg-white hover:bg-ink-300/15 text-ink-900 border border-border',
  ghost: 'bg-transparent hover:bg-ink-300/15 text-ink-700 border border-transparent',
  danger: 'bg-danger-500 hover:bg-danger-700 text-white border border-danger-500',
};

const sizes: Record<Size, string> = {
  sm: 'px-2.5 py-1.5 text-[12px] rounded-md gap-1.5',
  md: 'px-3.5 py-2 text-[13px] rounded-md gap-2',
};

export const Button = forwardRef<HTMLButtonElement, Props>(
  ({ variant = 'secondary', size = 'md', className, ...props }, ref) => (
    <button ref={ref}
      className={cn(
        'inline-flex items-center justify-center font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
        variants[variant], sizes[size], className
      )}
      {...props} />
  )
);
Button.displayName = 'Button';
