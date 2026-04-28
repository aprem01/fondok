export default function PageHeader({
  title,
  subtitle,
  eyebrow,
  action,
}: {
  title: string;
  subtitle?: string;
  /** Optional uppercase eyebrow label rendered above the title. */
  eyebrow?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between mb-6 lg:mb-8">
      <div>
        {eyebrow && <div className="eyebrow mb-2">{eyebrow}</div>}
        <h1 className="font-display text-[28px] font-semibold tracking-[-0.018em] text-ink-900 leading-[1.15]">
          {title}
        </h1>
        {subtitle && (
          <p className="text-body text-ink-500 mt-1.5 max-w-2xl">{subtitle}</p>
        )}
      </div>
      {action && <div className="flex items-center gap-2 flex-shrink-0">{action}</div>}
    </div>
  );
}
