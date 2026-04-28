import { Play, Download } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';

export default function EngineHeader({
  name, desc, outputs, dependsOn, complete = false,
}: {
  name: string; desc: string; outputs: string[]; dependsOn: string | null; complete?: boolean;
}) {
  return (
    <Card tone={complete ? 'default' : 'luxe'} className="p-5 mb-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="eyebrow mb-1.5">
            Engine · {complete ? 'Complete' : 'Ready to run'}
          </div>
          <h2 className="font-display text-[18px] font-semibold text-ink-900 tracking-[-0.014em] leading-tight">
            {name}
          </h2>
          <p className="text-body-sm text-ink-500 mt-1.5 max-w-2xl">{desc}</p>

          <div className="flex items-center gap-2 mt-4 flex-wrap">
            <span className="eyebrow">Outputs</span>
            {outputs.map(o => (
              <Badge key={o} tone="blue" dot>{o}</Badge>
            ))}
          </div>

          {dependsOn && (
            <div className="text-[11px] text-ink-500 mt-2.5">
              Depends on: <span className="text-brand-700 font-semibold">{dependsOn}</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {complete && <Badge tone="green" dot uppercase>Model complete</Badge>}
          <Button variant="secondary" size="sm">
            <Download size={12} /> Export to Excel
          </Button>
          <Button variant={complete ? 'primary' : 'premium'} size="sm">
            <Play size={12} /> Run Model
          </Button>
        </div>
      </div>
    </Card>
  );
}
