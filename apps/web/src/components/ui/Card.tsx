import { HTMLAttributes, forwardRef } from 'react';
import { cn } from '@/lib/format';

type Tone = 'default' | 'luxe' | 'elevated' | 'inset';

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: Tone;
  /** Adds hover-lift translate + card-hover shadow. Use for clickable cards. */
  interactive?: boolean;
}

const toneClasses: Record<Tone, string> = {
  // Flat default — institutional, restrained.
  default:
    'bg-white border border-border rounded-lg shadow-card',
  // Premium anchor card — subtle gradient + champagne left rule.
  luxe:
    'bg-card-luxe border border-border rounded-lg shadow-premium relative ' +
    'before:content-[""] before:absolute before:left-0 before:top-3 before:bottom-3 ' +
    'before:w-[2px] before:rounded-full before:bg-gold-gradient before:opacity-80',
  // Default card with the elevated shadow baked in.
  elevated:
    'bg-white border border-border rounded-lg shadow-card-hover',
  // Nested / inset card — inset hairline rather than full border.
  inset:
    'bg-surface rounded-lg shadow-inset-line',
};

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, tone = 'default', interactive = false, ...props }, ref) => (
    <div
      ref={ref}
      className={cn(
        toneClasses[tone],
        interactive && 'card-interactive cursor-pointer hover:shadow-card-hover hover:border-ink-300',
        className,
      )}
      {...props}
    />
  ),
);
Card.displayName = 'Card';

export const CardHeader = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('px-5 py-4 border-b hairline', className)} {...props} />
);

export const CardBody = ({ className, ...props }: HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('p-5', className)} {...props} />
);
