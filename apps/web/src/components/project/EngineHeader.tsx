import { Play, Download, ChevronDown } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';

export default function EngineHeader({
  name, desc, outputs, dependsOn, complete = false,
}: {
  name: string; desc: string; outputs: string[]; dependsOn: string | null; complete?: boolean;
}) {
  return (
    <Card className="p-5 mb-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <h2 className="text-[15px] font-semibold text-ink-900">{name}</h2>
          <p className="text-[12.5px] text-ink-500 mt-1">{desc}</p>
          <div className="flex items-center gap-2 mt-3 flex-wrap">
            <span className="text-[11px] text-ink-500">Outputs:</span>
            {outputs.map(o => <Badge key={o} tone="blue">{o}</Badge>)}
          </div>
          {dependsOn && (
            <div className="text-[11px] text-ink-500 mt-2">Depends on: <span className="text-brand-700 font-medium">{dependsOn}</span></div>
          )}
        </div>
        <div className="flex items-center gap-2">
          {complete && <Badge tone="green">✓ Model complete</Badge>}
          <Button variant="secondary" size="sm"><Download size={12} /> Export to Excel</Button>
          <Button variant="primary" size="sm"><Play size={12} /> Run Model</Button>
        </div>
      </div>
    </Card>
  );
}
