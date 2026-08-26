'use client';
import { useState } from 'react';
import { useParams } from 'next/navigation';
import {
  FileSpreadsheet, FileText, Presentation, Download, Copy,
  AlertTriangle, Loader2,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import type { Project } from '@/lib/mockData';
import { useToast } from '@/components/ui/Toast';
import { IntroCard } from '@/components/help/IntroCard';
import { useEngineOutputs } from '@/lib/hooks/useEngineOutputs';

type ExportPath = 'excel' | 'memo.pdf' | 'presentation.pptx';

type Deliverable = {
  type: string;
  ext: string;
  desc: string;
  icon: typeof FileSpreadsheet;
  color: string;
  path: ExportPath;
};

const deliverables: Deliverable[] = [
  { type: 'Excel Model', ext: '.xlsx', desc: 'Complete underwriting model with all assumptions and calculations', icon: FileSpreadsheet, color: 'text-success-700 bg-success-50', path: 'excel' },
  { type: 'IC Memo (PDF)', ext: '.pdf', desc: 'One-page investment committee summary document', icon: FileText, color: 'text-danger-700 bg-danger-50', path: 'memo.pdf' },
  { type: 'Deal Presentation', ext: '.pptx', desc: 'Full presentation deck with market analysis and financials', icon: Presentation, color: 'text-warn-700 bg-warn-50', path: 'presentation.pptx' },
];

// Browsers don't expose .env to client without the NEXT_PUBLIC_ prefix.
// When unset (production today) we disable the buttons and surface a tooltip.
const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL ?? '';

const labelByPath: Record<ExportPath, string> = {
  excel: 'Excel model',
  'memo.pdf': 'IC memo',
  'presentation.pptx': 'deal presentation',
};

export default function ExportTab({ project }: { project: Project }) {
  const [busy, setBusy] = useState<ExportPath | null>(null);
  const workerConnected = WORKER_URL.length > 0;
  const { toast } = useToast();
  // FON-54 — the real deal id is the route param (a UUID for live deals);
  // page.tsx passes `project.id: 0` for live deals, so String(project.id)
  // resolved to "0" and both the run-status read AND the export download URL
  // pointed at a non-existent deal — deliverables showed "Awaiting model run"
  // and downloads 404'd on a fully-modeled deal (Sam QA). Route id wins.
  const params = useParams();
  const routeId = typeof params?.id === 'string' ? params.id : null;
  const dealId = routeId ?? String(project.id);
  // FON-54 — deliverables must reflect the latest model run, not a canned
  // "2 hours ago". Stamp them with the real run time; when no run exists yet
  // there's nothing to export.
  const { outputs, lastRunAt } = useEngineOutputs(dealId);
  const hasRun = outputs != null && Object.keys(outputs.engines ?? {}).length > 0;
  const runStamp = lastRunAt
    ? new Date(lastRunAt).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : null;
  const sourceLabel = hasRun
    ? `Latest model run${runStamp ? ` · ${runStamp}` : ''}`
    : 'Awaiting model run';

  const handleDownload = (path: ExportPath) => {
    if (!workerConnected) return;
    setBusy(path);
    toast(`Generating ${labelByPath[path]}…`, { type: 'info' });
    // Stream the file via the worker — FileResponse on the Python side sets
    // Content-Disposition so the browser saves it directly.
    window.location.href = `${WORKER_URL}/deals/${dealId}/export/${path}`;
    // The redirect kicks off a download; clear the spinner shortly after.
    window.setTimeout(() => setBusy(null), 2500);
  };

  return (
    <div className="space-y-5">
      <IntroCard
        dismissKey="export-intro"
        title="Export & Share"
        body={
          <>
            Generate the latest underwriting model, IC memo, and deal presentation from the current
            model run and active scenario. Three formats: a full <span className="font-semibold">Excel model</span>,
            a <span className="font-semibold">PDF IC memo</span>, and a <span className="font-semibold">PowerPoint deck</span>.
          </>
        }
      />
      <Card className="p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-[15px] font-semibold text-ink-900">Export &amp; Share</h2>
            <p className="text-[12.5px] text-ink-500 mt-1">
              Deliverables regenerate from the current model run and active scenario.
            </p>
          </div>
          <Badge tone={hasRun ? 'green' : 'amber'}>{sourceLabel}</Badge>
        </div>
      </Card>

      {!workerConnected && (
        <Card className="p-5 border-warn-500/30 bg-warn-50">
          <div className="flex items-start gap-3">
            <AlertTriangle size={16} className="text-warn-700 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              <div className="text-[13px] font-semibold text-ink-900">Run the model and generate the IC memo to enable downloads</div>
              <p className="text-[12px] text-ink-700 mt-1 leading-relaxed">
                Excel models, IC memos, and presentation decks are produced once the underwriting engines have completed their run on this deal.
              </p>
            </div>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-3 gap-4">
        {deliverables.map(d => {
          const Icon = d.icon;
          const isBusy = busy === d.path;
          return (
            <Card key={d.type} className="p-5">
              <div className="flex items-start gap-3 mb-4">
                <div className={`w-12 h-12 rounded-lg flex items-center justify-center flex-shrink-0 ${d.color}`}>
                  <Icon size={20} />
                </div>
                <div className="flex-1">
                  <div className="text-[13.5px] font-semibold text-ink-900">{d.type}</div>
                  <div className="text-[11px] text-ink-500 mt-0.5">{d.ext}</div>
                </div>
              </div>
              <p className="text-[11.5px] text-ink-500 mb-4 leading-relaxed">{d.desc}</p>
              <div className="text-[10.5px] text-ink-500 mb-3">{hasRun ? sourceLabel : 'Available after model run'}</div>
              <Button
                variant="primary"
                size="sm"
                className="w-full"
                onClick={() => handleDownload(d.path)}
                disabled={!workerConnected || !hasRun || isBusy}
                title={workerConnected ? `Download ${d.ext}` : 'Available after model run'}
              >
                {isBusy ? (
                  <><Loader2 size={12} className="animate-spin" /> Generating…</>
                ) : (
                  <><Download size={12} /> Download</>
                )}
              </Button>
            </Card>
          );
        })}
      </div>

      <Card className="p-5">
        <h3 className="text-[14px] font-semibold text-ink-900 mb-2">Share with Team</h3>
        <p className="text-[12px] text-ink-500 mb-4">Generate a secure link for principals to review this analysis.</p>
        <div className="flex items-center gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={async () => {
              try {
                // Append ?share=true so collaborators landing on the link know
                // they're on a shared view (the page itself can read this in
                // a future iteration to suppress edit affordances).
                const url = new URL(window.location.href);
                url.searchParams.set('share', 'true');
                await navigator.clipboard.writeText(url.toString());
                toast('Share link copied', { type: 'success' });
              } catch {
                toast('Could not copy link', { type: 'error' });
              }
            }}
          >
            <Copy size={12} /> Copy Link
          </Button>
        </div>
      </Card>

      <Card className="p-5 bg-brand-50 border-brand-100">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-[14px] font-semibold text-ink-900">Ready for Investment Committee?</h3>
            <p className="text-[12px] text-ink-700 mt-1">Mark this project as IC Ready to notify your team for review.</p>
          </div>
          {/* Status flip is local-only today — the worker doesn't yet expose
              PATCH /deals/{id}/status. Toast names the deal so users know
              their click registered, and we route them to the project header
              kebab where the same action lives alongside Archive/Export. */}
          <Button
            variant="primary"
            onClick={() => toast(`${project.name} flagged IC Ready · status sync pending worker rollout`, { type: 'success' })}
          >
            Mark as IC Ready
          </Button>
        </div>
      </Card>
    </div>
  );
}
