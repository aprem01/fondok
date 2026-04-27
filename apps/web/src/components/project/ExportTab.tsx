'use client';
import { useState } from 'react';
import {
  FileSpreadsheet, FileText, Presentation, Download, Copy, ExternalLink,
  CheckCircle2, AlertTriangle, Sparkles, Loader2,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import type { Project } from '@/lib/mockData';

type ExportPath = 'excel' | 'memo.pdf' | 'presentation.pptx';

type Deliverable = {
  type: string;
  ext: string;
  desc: string;
  icon: typeof FileSpreadsheet;
  color: string;
  age: string;
  path: ExportPath;
};

const deliverables: Deliverable[] = [
  { type: 'Excel Model', ext: '.xlsx', desc: 'Complete underwriting model with all assumptions and calculations', icon: FileSpreadsheet, color: 'text-success-700 bg-success-50', age: '2 hours ago', path: 'excel' },
  { type: 'IC Memo (PDF)', ext: '.pdf', desc: 'One-page investment committee summary document', icon: FileText, color: 'text-danger-700 bg-danger-50', age: '2 hours ago', path: 'memo.pdf' },
  { type: 'Deal Presentation', ext: '.pptx', desc: 'Full presentation deck with market analysis and financials', icon: Presentation, color: 'text-warn-700 bg-warn-50', age: '2 hours ago', path: 'presentation.pptx' },
];

const highlights = [
  'Prime South Beach location with strong fundamentals',
  'Kimpton brand affiliation drives 14% ADR premium',
  'Attractive basis at $276K/key (22% discount to comps)',
  '24.5% levered IRR over 5-year hold',
];

const risks = [
  'Q1/Q3 RevPAR seasonal swing of 80%',
  'Pending PIP requirement of $5.3M',
  'Market supply pipeline of 414 rooms (2.2%)',
];

// Browsers don't expose .env to client without the NEXT_PUBLIC_ prefix.
// When unset (production today) we disable the buttons and surface a tooltip.
const WORKER_URL = process.env.NEXT_PUBLIC_WORKER_URL ?? '';

export default function ExportTab({ project }: { project: Project }) {
  const [busy, setBusy] = useState<ExportPath | null>(null);
  const workerConnected = WORKER_URL.length > 0;

  const handleDownload = (path: ExportPath) => {
    if (!workerConnected) return;
    setBusy(path);
    // Stream the file via the worker — FileResponse on the Python side sets
    // Content-Disposition so the browser saves it directly.
    window.location.href = `${WORKER_URL}/deals/${project.id}/export/${path}`;
    // The redirect kicks off a download; clear the spinner shortly after.
    window.setTimeout(() => setBusy(null), 2500);
  };

  return (
    <div className="space-y-5">
      <Card className="p-5">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-[15px] font-semibold text-ink-900">Export</h2>
            <p className="text-[12.5px] text-ink-500 mt-1">Generate IC memos, deal presentations, and Excel models for distribution.</p>
          </div>
          <Badge tone={workerConnected ? 'green' : 'amber'}>
            {workerConnected ? '3 exports ready' : 'Worker not connected'}
          </Badge>
        </div>
      </Card>

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
              <div className="text-[10.5px] text-ink-500 mb-3">Generated {d.age}</div>
              <Button
                variant="primary"
                size="sm"
                className="w-full"
                onClick={() => handleDownload(d.path)}
                disabled={!workerConnected || isBusy}
                title={workerConnected ? `Download ${d.ext}` : 'Connect worker to enable downloads'}
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
          <Button variant="secondary" size="sm"><Copy size={12} /> Copy Link</Button>
          <Button variant="secondary" size="sm"><ExternalLink size={12} /> Open Preview</Button>
        </div>
      </Card>

      <Card className="overflow-hidden">
        <div className="px-5 py-4 border-b border-border flex items-center justify-between">
          <h3 className="text-[14px] font-semibold text-ink-900">IC Memo Preview</h3>
          <button className="text-[12px] text-brand-500 hover:text-brand-700 font-medium">Full Preview ↗</button>
        </div>
        <div className="p-6 bg-gradient-to-br from-white to-ink-300/5">
          <div className="flex items-start justify-between mb-4">
            <div>
              <Badge tone="blue" className="mb-2"><Sparkles size={11} /> AI-Generated IC Memo</Badge>
              <h2 className="text-[20px] font-semibold text-ink-900">{project.name} Hotel</h2>
              <div className="text-[12.5px] text-ink-500 mt-1">{project.city} · {project.keys} Keys · {project.service}</div>
            </div>
            <div className="text-right">
              <div className="text-[11px] text-ink-500 uppercase tracking-wide">Purchase Price</div>
              <div className="text-[20px] font-semibold text-ink-900 tabular-nums">$36,400,000</div>
              <div className="text-[11.5px] text-ink-500">$275,758/key</div>
            </div>
          </div>

          <div className="grid grid-cols-4 gap-3 mb-5">
            {[['RevPAR', '$218'], ['NOI', '$4.2M'], ['Cap Rate', '7.25%'], ['Levered IRR', '24.5%']].map(([k, v]) => (
              <div key={k} className="bg-white border border-border rounded-md p-3">
                <div className="text-[10px] text-ink-500 uppercase tracking-wide">{k}</div>
                <div className="text-[18px] font-semibold tabular-nums mt-0.5 text-brand-700">{v}</div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-5">
            <Card className="p-4 bg-success-50 border-success-500/20">
              <div className="flex items-center gap-2 mb-2">
                <CheckCircle2 size={14} className="text-success-700" />
                <h4 className="text-[12.5px] font-semibold text-ink-900">Investment Highlights</h4>
              </div>
              <ul className="space-y-1.5 text-[11.5px] text-ink-700">
                {highlights.map(h => (
                  <li key={h} className="flex gap-2"><span className="text-success-700">•</span>{h}</li>
                ))}
              </ul>
            </Card>
            <Card className="p-4 bg-warn-50 border-warn-500/30">
              <div className="flex items-center gap-2 mb-2">
                <AlertTriangle size={14} className="text-warn-700" />
                <h4 className="text-[12.5px] font-semibold text-ink-900">Key Risks</h4>
              </div>
              <ul className="space-y-1.5 text-[11.5px] text-ink-700">
                {risks.map(r => (
                  <li key={r} className="flex gap-2"><span className="text-warn-700">•</span>{r}</li>
                ))}
              </ul>
            </Card>
          </div>

          <div className="text-[10.5px] text-ink-500 mt-5 text-center pt-3 border-t border-border">
            Generated by Fondok AI · 94% Confidence — Last updated: 2 hours ago
          </div>
        </div>
      </Card>

      <Card className="p-5 bg-brand-50 border-brand-100">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-[14px] font-semibold text-ink-900">Ready for Investment Committee?</h3>
            <p className="text-[12px] text-ink-700 mt-1">Mark this project as IC Ready to notify your team for review.</p>
          </div>
          <Button variant="primary">Mark as IC Ready</Button>
        </div>
      </Card>
    </div>
  );
}
