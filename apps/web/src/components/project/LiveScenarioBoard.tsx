'use client';

/**
 * LiveScenarioBoard — big bet #4, wiring step.
 *
 * The scenario infrastructure (ScenarioComparePanel + full CRUD + the compare
 * endpoint) already exists but was never rendered on a live deal — the
 * Analysis → Scenarios sub showed only a placeholder. This fetches the deal's
 * scenarios and renders the side-by-side compare (IRR / DSCR / equity multiple
 * / total cost across up to 4 cases). Scenarios are created from the selector
 * in the deal header; a one-click Downside/Upside generator is the next step.
 */

import { useEffect, useState } from 'react';
import { Layers, Loader2 } from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { api, isWorkerConnected } from '@/lib/api';
import type { ScenarioRecord } from '@/lib/api';
import ScenarioComparePanel from './ScenarioComparePanel';

export function LiveScenarioBoard({ dealId }: { dealId: string }) {
  const [scenarios, setScenarios] = useState<ScenarioRecord[] | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isWorkerConnected() || /^\d+$/.test(dealId)) {
      setLoading(false);
      return;
    }
    const ac = new AbortController();
    setLoading(true);
    api.scenarios
      .list(dealId, ac.signal)
      .then((s) => setScenarios(s))
      .catch(() => setScenarios([]))
      .finally(() => setLoading(false));
    return () => ac.abort();
  }, [dealId]);

  if (loading) {
    return (
      <Card className="p-8 flex items-center justify-center gap-2 text-[12.5px] text-ink-500">
        <Loader2 size={14} className="animate-spin" /> Loading scenarios…
      </Card>
    );
  }

  if (!scenarios || scenarios.length === 0) {
    return (
      <Card className="p-8 text-center">
        <div className="w-12 h-12 mx-auto rounded-lg bg-brand-50 flex items-center justify-center mb-3">
          <Layers size={20} className="text-brand-500" />
        </div>
        <h3 className="text-[14px] font-semibold text-ink-900 mb-1">
          Compare downside / base / upside
        </h3>
        <p className="text-[12.5px] text-ink-500 max-w-md mx-auto leading-relaxed">
          Add a Downside or Upside case from the <strong>scenario selector</strong>{' '}
          in the deal header (top-left of the deal), then compare IRR, DSCR,
          equity multiple, and total cost side-by-side here.
        </p>
      </Card>
    );
  }

  return <ScenarioComparePanel dealId={dealId} scenarios={scenarios} />;
}
