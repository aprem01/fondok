import { cn } from '@/lib/format';

type Tone = 'gray' | 'blue' | 'green' | 'amber' | 'red' | 'slate' | 'gold';

const tones: Record<Tone, { wrap: string; dot: string }> = {
  gray:  { wrap: 'bg-ink-100 text-ink-700 border-ink-200',                dot: 'bg-ink-400' },
  blue:  { wrap: 'bg-brand-50 text-brand-700 border-brand-100',           dot: 'bg-brand-500' },
  green: { wrap: 'bg-success-50 text-success-700 border-success-500/25', dot: 'bg-success-500' },
  amber: { wrap: 'bg-warn-50 text-warn-700 border-warn-500/30',           dot: 'bg-warn-500' },
  red:   { wrap: 'bg-danger-50 text-danger-700 border-danger-500/25',     dot: 'bg-danger-500' },
  slate: { wrap: 'bg-ink-900/[0.04] text-ink-700 border-ink-900/10',      dot: 'bg-ink-700' },
  gold:  { wrap: 'bg-gold-50 text-gold-700 border-gold-200',              dot: 'bg-gold-400' },
};

export function Badge({
  tone = 'gray',
  children,
  className,
  dot = false,
  uppercase = false,
}: {
  tone?: Tone;
  children: React.ReactNode;
  className?: string;
  /** Prepends a small filled status dot. */
  dot?: boolean;
  /** Uses eyebrow-style uppercase letterforms. */
  uppercase?: boolean;
}) {
  const t = tones[tone];
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border',
        'font-medium',
        uppercase
          ? 'text-[10px] tracking-[0.08em] uppercase font-semibold'
          : 'text-[11px]',
        t.wrap,
        className,
      )}
    >
      {dot && <span className={cn('w-1.5 h-1.5 rounded-full', t.dot)} aria-hidden="true" />}
      {children}
    </span>
  );
}

const statusToneMap: Record<string, Tone> = {
  'Draft': 'blue', 'In Review': 'amber', 'IC Ready': 'gold', 'Archived': 'gray',
  'Low': 'green', 'Medium': 'amber', 'High': 'red',
  'Low Risk': 'green', 'Medium Risk': 'amber', 'High Risk': 'red',
  'Extracted': 'green', 'Processing': 'blue', 'Pending': 'gray',
  'Teaser': 'gray', 'Under NDA': 'blue', 'LOI': 'amber', 'PSA': 'green',
  'Pro Plan': 'blue', 'Coming Soon': 'gray',
};

export function StatusBadge({
  value,
  className,
  dot = true,
}: {
  value: string;
  className?: string;
  dot?: boolean;
}) {
  return (
    <Badge tone={statusToneMap[value] || 'gray'} className={className} dot={dot} uppercase>
      {value}
    </Badge>
  );
}
