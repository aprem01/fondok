import { cn } from '@/lib/format';

type Tone = 'gray' | 'blue' | 'green' | 'amber' | 'red' | 'slate';

const tones: Record<Tone, string> = {
  gray:  'bg-ink-300/30 text-ink-700 border-ink-300/40',
  blue:  'bg-brand-50 text-brand-700 border-brand-100',
  green: 'bg-success-50 text-success-700 border-success-500/20',
  amber: 'bg-warn-50 text-warn-700 border-warn-500/30',
  red:   'bg-danger-50 text-danger-700 border-danger-500/20',
  slate: 'bg-ink-900/5 text-ink-700 border-ink-300/40',
};

export function Badge({ tone = 'gray', children, className }: {
  tone?: Tone; children: React.ReactNode; className?: string;
}) {
  return (
    <span className={cn(
      'inline-flex items-center gap-1 px-2 py-0.5 text-[11px] font-medium rounded-md border',
      tones[tone], className
    )}>{children}</span>
  );
}

const statusToneMap: Record<string, Tone> = {
  'Draft': 'blue', 'In Review': 'amber', 'IC Ready': 'green', 'Archived': 'gray',
  'Low': 'green', 'Medium': 'amber', 'High': 'red',
  'Low Risk': 'green', 'Medium Risk': 'amber', 'High Risk': 'red',
  'Extracted': 'green', 'Processing': 'blue', 'Pending': 'gray',
  'Teaser': 'gray', 'Under NDA': 'blue', 'LOI': 'amber', 'PSA': 'green',
  'Pro Plan': 'blue', 'Coming Soon': 'gray',
};

export function StatusBadge({ value, className }: { value: string; className?: string }) {
  return <Badge tone={statusToneMap[value] || 'gray'} className={className}>{value}</Badge>;
}
