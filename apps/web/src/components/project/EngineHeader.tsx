'use client';
import { useState } from 'react';
import { Play, Download } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { useToast } from '@/components/ui/Toast';

// Browsers don't expose .env to client without the NEXT_PUBLIC_ prefix.
// Same gating ExportTab uses — when unset we surface a toast instead of
// hitting a non-existent worker.
const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL ?? '';

export default function EngineHeader({
  name, desc, outputs, dependsOn, complete = false, dealId, onRun, onExport,
}: {
  name: string; desc: string; outputs: string[]; dependsOn: string | null; complete?: boolean;
  /** Required when no `onExport` is provided so the default handler can build the worker URL. */
  dealId?: string;
  onRun?: () => void;
  onExport?: () => void;
}) {
  const { toast } = useToast();
  const [running, setRunning] = useState(false);

  const handleRun = () => {
    if (onRun) {
      onRun();
      return;
    }
    // Simulate a 2s spin so users get feedback while the request is queued.
    setRunning(true);
    toast('Engine queued — check back shortly', { type: 'info' });
    window.setTimeout(() => setRunning(false), 2000);
  };

  const handleExport = () => {
    if (onExport) {
      onExport();
      return;
    }
    if (!WORKER_URL) {
      toast('Available after model run', { type: 'info' });
      return;
    }
    if (!dealId) {
      toast('Available after model run', { type: 'info' });
      return;
    }
    // Worker streams the file via Content-Disposition; navigating triggers
    // the browser download without a popup.
    window.location.href = `${WORKER_URL}/deals/${dealId}/export/excel`;
  };

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
          <Button
            variant="secondary"
            size="sm"
            onClick={handleExport}
            type="button"
          >
            <Download size={12} /> Export to Excel
          </Button>
          <Button
            variant={complete ? 'primary' : 'premium'}
            size="sm"
            onClick={handleRun}
            loading={running}
            type="button"
          >
            {!running && <Play size={12} />} {running ? 'Running…' : 'Run Model'}
          </Button>
        </div>
      </div>
    </Card>
  );
}
